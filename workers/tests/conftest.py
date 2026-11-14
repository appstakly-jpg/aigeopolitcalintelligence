"""Test fixtures: a throwaway Postgres database (migrations + reference data), per-test tenants,
an in-memory HTTP transport that serves RSS feeds, and helpers for row-level-security tests.

Requires a reachable Postgres (TEST_DATABASE_ADMIN_URL, default postgres@127.0.0.1)."""

from __future__ import annotations

import dataclasses
import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from xml.sax.saxutils import escape

import httpx
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from gccintel.config import get_settings
from gccintel.db import connect, execute, fetch_one, jsonb
from gccintel.migrate import ensure_database, migrate
from gccintel.seed import add_member, create_tenant, create_user, seed_reference

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")
TEST_DB = os.environ.get("TEST_DATABASE_NAME", "gccintel_test")


@pytest.fixture(scope="session")
def dsn() -> str:
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'drop database if exists "{TEST_DB}" with (force)')
    ensure_database(ADMIN_URL, TEST_DB)
    url = make_conninfo(ADMIN_URL, dbname=TEST_DB)
    migrate(url)
    with connect(url) as conn:
        seed_reference(conn)
        conn.commit()
    return url


@pytest.fixture()
def settings(dsn, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER", "SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", dsn)
    get_settings.cache_clear()
    s = get_settings()
    return dataclasses.replace(s, per_host_min_interval_seconds=0.0, demo_mode=True, smtp_host=None,
                               app_base_url="https://app.test")


@pytest.fixture()
def conn(dsn):
    c = connect(dsn, autocommit=True)
    yield c
    c.close()


@pytest.fixture()
def tenant(conn) -> str:
    return create_tenant(conn, f"t-{uuid.uuid4().hex[:10]}", "Test Org", "team")


def add_source(conn, tenant_id: str, *, url: str, name: str, publisher: str | None = None, source_type: str = "news_media",
               reliability: str = "B", origin_group: str | None = None, language: str = "en",
               timezone: str | None = None, country_focus: list[str] | None = None) -> dict:
    return fetch_one(conn, """
        insert into sources(tenant_id, name, publisher, adapter, url, language, country_focus, source_type, reliability,
                            origin_group, license_type, usage_policy, status, enabled, config, next_fetch_at, is_demo)
        values (%s, %s, %s, 'rss', %s, %s, %s, %s, %s, %s, 'synthetic', 'headline_link_excerpt', 'approved', true, %s,
                now(), true)
        returning *""", (tenant_id, name, publisher or name, url, language, country_focus or [], source_type, reliability,
                         origin_group or name.lower().replace(" ", "-"), jsonb({"timezone": timezone} if timezone else {})))


def rss(items: list[dict], title: str = "Test feed") -> bytes:
    """items: {title, body, link, minutes_ago | pub (raw string)}"""
    now = datetime.now(UTC)
    parts = [f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(title)}</title>']
    for it in items:
        pub = it.get("pub") or format_datetime(now - timedelta(minutes=it.get("minutes_ago", 10)))
        parts.append(f"<item><title>{escape(it['title'])}</title><link>{escape(it['link'])}</link>"
                     f"<guid>{escape(it.get('guid', it['link']))}</guid><pubDate>{pub}</pubDate>"
                     f"<description>{escape(it.get('body', ''))}</description></item>")
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


class FeedWeb:
    """In-memory web: URL -> (status, body). robots.txt allows everything unless overridden."""

    def __init__(self):
        self.pages: dict[str, tuple[int, bytes, str]] = {}
        self.requests: list[str] = []

    def set(self, url: str, body: bytes | str, status: int = 200, ctype: str = "application/rss+xml") -> None:
        self.pages[url] = (status, body.encode() if isinstance(body, str) else body, ctype)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(f"{request.method} {url}")
        if url.endswith("/robots.txt") and url not in self.pages:
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url in self.pages:
            status, body, ctype = self.pages[url]
            return httpx.Response(status, content=body if request.method != "HEAD" else b"", headers={"content-type": ctype})
        return httpx.Response(404, text="not found")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture()
def web() -> FeedWeb:
    return FeedWeb()


@pytest.fixture()
def fetcher(settings, web):
    from gccintel.adapters.http import PoliteFetcher
    f = PoliteFetcher(settings, transport=web.transport())
    yield f
    f.close()


@contextmanager
def as_user(conn, user_id: str, tenant_id: str):
    """Run statements as the Postgres `authenticated` role with request JWT claims, exactly like the web app."""
    with conn.transaction():
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)",
                     (json.dumps({"sub": str(user_id), "tenant_id": str(tenant_id), "role": "authenticated"}),))
        yield conn


def make_user(conn, tenant_id: str, role: str) -> str:
    uid = create_user(conn, f"{role}-{uuid.uuid4().hex[:8]}@example.test", "correct-horse-battery", role.title())
    add_member(conn, tenant_id, uid, role)
    return uid


def add_rule(conn, tenant_id: str, user_id: str, **kw) -> dict:
    cols = {"name": "Test rule", "countries": [], "event_types": [], "min_severity": 1, "channels": ["email"],
            "destination": jsonb({"email": "ops@example.test"}), "throttle_minutes": 0, **kw}
    keys = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    return fetch_one(conn, f"insert into alert_rules(tenant_id, user_id, {keys}) values (%s, %s, {ph}) returning *",
                     (tenant_id, user_id, *cols.values()))


__all__ = ["add_source", "rss", "as_user", "make_user", "add_rule", "execute"]
