"""RSS 2.0 / RSS 1.0 (RDF) / Atom adapter (feedparser)."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

import feedparser

from ..text.normalize import clean_text
from .base import AdapterResult, RawItem, parse_feed_date
from .http import FetchBlocked, PoliteFetcher


def parse_feed_bytes(content: bytes, source_tz: str = "UTC", base_url: str | None = None) -> tuple[list[RawItem], str | None]:
    parsed = feedparser.parse(content, response_headers={"content-location": base_url} if base_url else None)
    if parsed.bozo and not parsed.entries:
        return [], f"unparseable feed: {type(parsed.bozo_exception).__name__}"
    feed_lang = (parsed.feed.get("language") or "")[:2].lower() or None
    items: list[RawItem] = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        if not link and entry.get("id", "").startswith("http"):
            link = entry["id"]
        title = clean_text(entry.get("title"))
        if not link or not title:
            continue
        raw_date = entry.get("published") or entry.get("updated") or entry.get("dc_date")
        published, precision = parse_feed_date(raw_date, source_tz)
        if published is None:
            struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if struct:
                published = datetime.fromtimestamp(calendar.timegm(struct), UTC)
                precision = "exact"
        summary = entry.get("summary") or ""
        if not summary and entry.get("content"):
            summary = entry["content"][0].get("value", "")
        items.append(RawItem(
            url=link.strip(), title=title, summary=summary, published_at=published, published_raw=raw_date,
            published_precision=precision if published else "unknown", author=clean_text(entry.get("author")) or None,
            guid=entry.get("id"), language_hint=feed_lang,
        ))
    return items, None


class RssAdapter:
    name = "rss"

    def __init__(self, fetcher: PoliteFetcher):
        self.fetcher = fetcher

    def fetch(self, source: dict) -> AdapterResult:
        try:
            resp = self.fetcher.get(source["url"], etag=source.get("etag"), last_modified=source.get("last_modified"))
        except FetchBlocked as exc:
            return AdapterResult([], error=f"blocked: {exc}", robots_allowed=("robots" not in str(exc)))
        except Exception as exc:  # noqa: BLE001 - network errors are recorded in source health
            return AdapterResult([], error=f"{type(exc).__name__}: {exc}"[:500])
        result = AdapterResult([], http_status=resp.status, robots_allowed=resp.robots_allowed,
                               latency_ms=resp.elapsed_ms, etag=resp.headers.get("etag"),
                               last_modified=resp.headers.get("last-modified"))
        if resp.not_modified:
            result.not_modified = True
            return result
        if resp.status >= 400:
            result.error = f"HTTP {resp.status}"
            return result
        tz = (source.get("config") or {}).get("timezone", "UTC")
        items, err = parse_feed_bytes(resp.content, tz, resp.final_url)
        result.items, result.error = items, err
        return result
