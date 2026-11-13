"""Fetching, canonicalisation, exact/near-duplicate detection, syndication, time zones,
robots.txt and source health."""

from datetime import UTC, datetime

from conftest import add_source, rss

from gccintel.adapters.base import parse_feed_date
from gccintel.agents.ingestion import due_sources, ingest_source
from gccintel.db import fetch_all, fetch_one
from gccintel.text.urls import canonicalize_url

WIRE = "A commercial tanker reported an explosion close to its hull while transiting the Bab el-Mandeb strait, the operator said. No casualties were reported. (Reuters)"


def test_canonicalisation_strips_tracking_only():
    a = canonicalize_url("https://Example.com/news/1?utm_source=x&id=5&fbclid=abc#top")
    assert a == "https://example.com/news/1?id=5"
    assert canonicalize_url("https://example.com/a?page=2") == "https://example.com/a?page=2"


def test_naive_feed_dates_use_source_timezone():
    dt, _ = parse_feed_date("2026-11-10 09:30", "Asia/Riyadh")
    assert dt == datetime(2026, 11, 10, 6, 30, tzinfo=UTC)
    dt2, _ = parse_feed_date("Tue, 10 Nov 2026 09:30:00 +0400", "Asia/Riyadh")
    assert dt2 == datetime(2026, 11, 10, 5, 30, tzinfo=UTC)
    future, _ = parse_feed_date("Tue, 10 Nov 2099 09:30:00 +0000", "UTC")
    assert future is None


def test_exact_and_tracking_duplicates_are_stored_once(conn, tenant, web, fetcher):
    src = add_source(conn, tenant, url="https://feeds.test/a.xml", name="Outlet A")
    web.set(src["url"], rss([
        {"title": "Tanker reports explosion", "body": WIRE, "link": "https://a.test/1?utm_source=rss"},
        {"title": "Tanker reports explosion", "body": WIRE, "link": "https://a.test/1"},
    ]))
    r = ingest_source(conn, src, fetcher)
    assert r["found"] == 2 and r["new"] == 1


def test_syndicated_copy_shares_origin(conn, tenant, web, fetcher):
    a = add_source(conn, tenant, url="https://feeds.test/a.xml", name="Outlet A")
    b = add_source(conn, tenant, url="https://feeds.test/b.xml", name="Outlet B")
    web.set(a["url"], rss([{"title": "Tanker reports explosion near Bab el-Mandeb", "body": WIRE,
                            "link": "https://a.test/1", "minutes_ago": 60}]))
    web.set(b["url"], rss([{"title": "Tanker reports explosion near Bab el-Mandeb - operator", "body": WIRE,
                            "link": "https://b.test/x", "minutes_ago": 30}]))
    ingest_source(conn, a, fetcher)
    ingest_source(conn, b, fetcher)
    rows = fetch_all(conn, "select source_id, syndicated_from_article_id, is_original, meta from articles where tenant_id = %s",
                     (tenant,))
    copy = next(r for r in rows if r["source_id"] == b["id"])
    assert copy["syndicated_from_article_id"] is not None and not copy["is_original"]
    assert {r["meta"]["origin_key"] for r in rows} == {"origin:reuters"}


def test_robots_disallow_is_respected_and_logged(conn, tenant, web, fetcher):
    src = add_source(conn, tenant, url="https://blocked.test/feed.xml", name="Blocked")
    web.set("https://blocked.test/robots.txt", "User-agent: *\nDisallow: /\n", ctype="text/plain")
    web.set(src["url"], rss([{"title": "x", "link": "https://blocked.test/1"}]))
    r = ingest_source(conn, src, fetcher)
    assert r["error"] and "robots" in r["error"]
    assert not any(req.startswith("GET https://blocked.test/feed.xml") for req in web.requests)
    h = fetch_one(conn, "select ok, error from source_health where source_id = %s", (src["id"],))
    assert h["ok"] is False


def test_broken_feed_backs_off(conn, tenant, web, fetcher):
    src = add_source(conn, tenant, url="https://down.test/feed.xml", name="Down")
    web.set(src["url"], "oops", status=503)
    r = ingest_source(conn, src, fetcher)
    assert r["error"]
    s = fetch_one(conn, "select consecutive_failures, next_fetch_at from sources where id = %s", (src["id"],))
    assert s["consecutive_failures"] == 1 and s["next_fetch_at"] > datetime.now(UTC)
    assert not due_sources(conn, tenant)


def test_excerpt_respects_usage_policy(conn, tenant, web, fetcher):
    src = add_source(conn, tenant, url="https://feeds.test/long.xml", name="Long")
    conn.execute("update sources set max_excerpt_chars = 80 where id = %s", (src["id"],))
    src = fetch_one(conn, "select * from sources where id = %s", (src["id"],))
    web.set(src["url"], rss([{"title": "Long story", "body": "word " * 400, "link": "https://l.test/1"}]))
    ingest_source(conn, src, fetcher)
    a = fetch_one(conn, "select excerpt from articles where source_id = %s", (src["id"],))
    assert len(a["excerpt"]) <= 80
