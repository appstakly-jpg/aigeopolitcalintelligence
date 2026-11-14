"""Fetching, canonicalisation, exact/near-duplicate detection, syndication, time zones,
robots.txt and source health."""

from datetime import UTC, datetime, timedelta

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


def test_fetcher_paces_robots_and_honours_source_minimum_interval(settings, monkeypatch):
    """The robots.txt request counts against a host's pacing, and a source's own minimum interval
    (GDELT asks for one request every 5 seconds) spaces requests even when the global one is lower."""
    import dataclasses

    import httpx

    from gccintel.adapters import http as http_mod

    clock, sleeps = [1000.0], []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 2))
        clock[0] += seconds

    monkeypatch.setattr(http_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http_mod.time, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"articles": []})

    f = http_mod.PoliteFetcher(dataclasses.replace(settings, per_host_min_interval_seconds=2.0),
                               transport=httpx.MockTransport(handler))
    try:
        f.get("https://api.example/doc?q=1", min_interval=6)   # robots.txt at t=1000, then wait 6 s
        f.get("https://api.example/doc?q=2", min_interval=6)   # 6 s after the previous request
        f.get("https://feeds.example/rss.xml")                 # other host: robots.txt, then the 2 s default
    finally:
        f.close()
    assert sleeps == [6.0, 6.0, 2.0]


def test_retry_after_header_parsing():
    from email.utils import format_datetime

    from gccintel.adapters.http import retry_after_seconds

    assert retry_after_seconds({"retry-after": "120"}) == 120
    assert retry_after_seconds({"retry-after": "999999"}) == 86400
    later = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    assert 590 <= retry_after_seconds({"retry-after": format_datetime(later, usegmt=True)}) <= 600
    assert retry_after_seconds({"retry-after": "soon"}) is None
    assert retry_after_seconds({}) is None


def test_rate_limited_source_waits_for_retry_after(conn, tenant, settings):
    """A 429 with Retry-After is recorded in source health and pushes the next fetch past that time."""
    import httpx

    from gccintel.adapters.http import PoliteFetcher

    src = add_source(conn, tenant, url="https://limited.example/feed.xml", name="Rate limited")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"Retry-After": "7200"}, text="Please slow down")

    f = PoliteFetcher(settings, transport=httpx.MockTransport(handler))
    try:
        r = ingest_source(conn, src, f)
    finally:
        f.close()
    assert r["error"] == "HTTP 429"
    row = fetch_one(conn, "select last_error, consecutive_failures, next_fetch_at - now() as wait from sources where id = %s",
                    (src["id"],))
    assert row["consecutive_failures"] == 1 and row["wait"] >= timedelta(minutes=119)
    health = fetch_one(conn, "select ok, http_status, error from source_health where source_id = %s", (src["id"],))
    assert health["ok"] is False and health["http_status"] == 429


def test_unreviewed_licence_is_limited_to_headlines_links_and_excerpts(conn, tenant):
    """An enabled source whose licence is still under review can never be switched to full text."""
    import psycopg
    import pytest

    from gccintel.db import execute

    src = add_source(conn, tenant, url="https://unreviewed.example/feed.xml", name="Unreviewed")
    execute(conn, "update sources set license_type = 'review_required' where id = %s", (src["id"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        execute(conn, "update sources set usage_policy = 'full_text' where id = %s", (src["id"],))
    execute(conn, "update sources set usage_policy = 'metadata_only' where id = %s", (src["id"],))
    execute(conn, "update sources set enabled = false where id = %s", (src["id"],))
    execute(conn, "update sources set usage_policy = 'full_text' where id = %s", (src["id"],))  # disabled: allowed


def test_templated_items_with_different_headlines_are_not_copies(conn, tenant, web, fetcher):
    """Real FCDO feeds published the same change note for seven countries; each advisory is its own
    item, not a syndicated copy of the first (seen on live data, 2026-11-14)."""
    note = "Updated information about regional tensions ('Warnings and insurance' page)."
    a = add_source(conn, tenant, url="https://gov.test/bahrain.atom", name="Advice Bahrain", origin_group="gov-test")
    b = add_source(conn, tenant, url="https://gov.test/kuwait.atom", name="Advice Kuwait", origin_group="gov-test")
    web.set(a["url"], rss([{"title": "Bahrain travel advice", "body": note, "link": "https://gov.test/bahrain", "minutes_ago": 60}]))
    web.set(b["url"], rss([{"title": "Kuwait travel advice", "body": note, "link": "https://gov.test/kuwait", "minutes_ago": 50}]))
    ingest_source(conn, a, fetcher)
    ingest_source(conn, b, fetcher)
    rows = fetch_all(conn, "select syndicated_from_article_id, is_original from articles where tenant_id = %s", (tenant,))
    assert len(rows) == 2 and all(r["is_original"] and r["syndicated_from_article_id"] is None for r in rows)


def test_same_publisher_items_that_swap_a_name_are_not_copies(conn, tenant, web, fetcher):
    """Templated advisories of one publisher ("Mongolia - Level 1: ..." / "Samoa - Level 1: ...") are
    separate items; the same headline from another outlet is still a copy."""
    def advice(country: str) -> str:
        return (f"Exercise normal precautions in {country}. {country} is generally a safe destination for travelers. "
                "Petty crime is most likely to occur in popular tourist locations. Stay aware of your surroundings, "
                "keep valuables out of sight and follow the advice of local authorities at all times.")
    gov = add_source(conn, tenant, url="https://gov.test/advisories.xml", name="Gov advisories", origin_group="gov-test")
    web.set(gov["url"], rss([
        {"title": "Mongolia - Level 1: Exercise Normal Precautions", "body": advice("Mongolia"), "link": "https://gov.test/mn", "minutes_ago": 90},
        {"title": "Samoa - Level 1: Exercise Normal Precautions", "body": advice("Samoa"), "link": "https://gov.test/ws", "minutes_ago": 80},
    ]))
    ingest_source(conn, gov, fetcher)
    rows = fetch_all(conn, "select syndicated_from_article_id from articles where tenant_id = %s", (tenant,))
    assert len(rows) == 2 and all(r["syndicated_from_article_id"] is None for r in rows)


def test_description_reused_from_another_item_is_not_used(conn, tenant, web, fetcher):
    """A live news-agency feed gave a landmine story the description of an unrelated story. The
    headline and link are kept; the mismatched text is dropped and noted, and no copy is recorded."""
    body = ("Istanbul, November 13 (QNA) - HH the Amir left Istanbul on Friday after chairing the delegation "
            "to the 12th meeting of the Supreme Strategic Committee.")
    src = add_source(conn, tenant, url="https://agency.test/feed.xml", name="Agency", origin_group="agency-test")
    web.set(src["url"], rss([
        {"title": "HH the Amir Leaves Istanbul", "body": body, "link": "https://agency.test/1", "minutes_ago": 60},
        {"title": "One Person Injured by Landmine Explosion in Syria", "body": body, "link": "https://agency.test/2", "minutes_ago": 50},
    ]))
    ingest_source(conn, src, fetcher)
    rows = {r["title"]: r for r in fetch_all(conn, """select title, excerpt, meta, syndicated_from_article_id from articles
                                                     where tenant_id = %s""", (tenant,))}
    landmine = rows["One Person Injured by Landmine Explosion in Syria"]
    assert landmine["excerpt"] is None and "not used" in landmine["meta"]["excerpt_note"]
    assert landmine["syndicated_from_article_id"] is None
    assert rows["HH the Amir Leaves Istanbul"]["excerpt"].startswith("Istanbul, November 13")


def test_real_feed_date_formats_and_wrong_offsets():
    """Formats and errors seen on live feeds on 2026-11-14."""
    now = datetime(2026, 11, 14, 9, 28, tzinfo=UTC)
    # QNA stamps Doha local time with "Z": as stated it lies 3.5 hours in the future, so it is refused ...
    assert parse_feed_date("Sat, 14 Nov 2026 12:01:55 Z", "UTC", now=now) == (None, "unknown")
    # ... and with the source's ignore_feed_offset setting it is read as Doha time.
    assert parse_feed_date("Sat, 14 Nov 2026 12:01:55 Z", "Asia/Qatar", now=now, ignore_offset=True) == (
        datetime(2026, 11, 14, 9, 1, 55, tzinfo=UTC), "exact")
    # IAEA: two-digit year, two spaces, no offset (Vienna time).
    assert parse_feed_date("26-11-13  15:00", "Europe/Vienna", now=now) == (datetime(2026, 11, 13, 14, 0, tzinfo=UTC), "exact")
    # US State Department: a date without a time.
    assert parse_feed_date("Fri, 23 Oct 2026", "UTC", now=now) == (datetime(2026, 10, 23, tzinfo=UTC), "date")
    # Small clock differences are tolerated.
    assert parse_feed_date("Sat, 14 Nov 2026 09:35:00 +0000", "UTC", now=now)[0] is not None
