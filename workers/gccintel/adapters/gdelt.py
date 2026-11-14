"""GDELT DOC 2.0 API adapter (article list mode).

GDELT is a discovery index: each returned article keeps its own publisher (domain), which
is used as its independence key during verification. Only URL, title, domain, language
and the GDELT "seen" timestamp are stored.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlencode

from .base import AdapterResult, RawItem
from .http import FetchBlocked, PoliteFetcher, retry_after_seconds

_LANG = {"english": "en", "arabic": "ar", "persian": "fa", "farsi": "fa", "hebrew": "he", "turkish": "tr", "french": "fr"}


class GdeltAdapter:
    name = "gdelt"

    def __init__(self, fetcher: PoliteFetcher):
        self.fetcher = fetcher

    def build_url(self, source: dict) -> str:
        cfg = source.get("config") or {}
        params = {
            "query": cfg.get("query", "Gulf"),
            "mode": "artlist",
            "format": "json",
            "maxrecords": min(int(cfg.get("maxrecords", 75)), 250),
            "timespan": cfg.get("timespan", "1d"),
            "sort": "datedesc",
        }
        return source["url"].split("?")[0] + "?" + urlencode(params)

    def fetch(self, source: dict) -> AdapterResult:
        url = self.build_url(source)
        cfg = source.get("config") or {}
        try:
            # GDELT asks for at most one request every 5 seconds; the catalogue sets min_interval_seconds.
            resp = self.fetcher.get(url, min_interval=cfg.get("min_interval_seconds", 6))
        except FetchBlocked as exc:
            return AdapterResult([], error=f"blocked: {exc}")
        except Exception as exc:  # noqa: BLE001
            return AdapterResult([], error=f"{type(exc).__name__}: {exc}"[:500])
        result = AdapterResult([], http_status=resp.status, robots_allowed=resp.robots_allowed, latency_ms=resp.elapsed_ms)
        if resp.status >= 400:
            result.error = f"HTTP {resp.status}"
            result.retry_after_seconds = retry_after_seconds(resp.headers) if resp.status in (429, 503) else None
            return result
        try:
            data = json.loads(resp.content.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError as exc:
            result.error = f"invalid JSON from GDELT: {exc}"
            return result
        for art in data.get("articles", []):
            seen = art.get("seendate")
            published = None
            if seen:
                try:
                    published = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                except ValueError:
                    published = None
            result.items.append(RawItem(
                url=art.get("url", ""), title=art.get("title", ""), summary="",
                published_at=published, published_raw=seen, published_precision="exact" if published else "unknown",
                language_hint=_LANG.get((art.get("language") or "").lower()),
                publisher_domain=art.get("domain"),
                extra={"sourcecountry": art.get("sourcecountry"), "gdelt_seen": seen},
            ))
        return result
