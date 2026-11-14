"""Source adapter interface and shared date handling."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from zoneinfo import ZoneInfo


@dataclass
class RawItem:
    url: str
    title: str
    summary: str = ""
    published_at: datetime | None = None
    published_raw: str | None = None
    published_precision: str = "exact"   # exact | date | unknown
    author: str | None = None
    guid: str | None = None
    language_hint: str | None = None
    publisher_domain: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class AdapterResult:
    items: list[RawItem]
    http_status: int | None = None
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    robots_allowed: bool | None = None
    latency_ms: int | None = None
    error: str | None = None
    retry_after_seconds: int | None = None  # from a 429/503 Retry-After header


class SourceAdapter(Protocol):
    name: str

    def fetch(self, source: dict) -> AdapterResult: ...


_YY_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})(?:\s+|T)(\d{2}):(\d{2})$")
# A feed date further ahead than this is treated as unknown: clocks drift by seconds, not hours,
# and a "future" date usually means a wrong time zone in the feed.
MAX_FUTURE_SKEW = timedelta(minutes=15)
_DMY_RE = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?:[ T](\d{1,2}):(\d{2}))?$")
_RFC_DATE_ONLY_RE = re.compile(r"^(?:[A-Za-z]{3},\s*)?(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$")  # "Fri, 23 Oct 2026"
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_feed_date(raw: str | None, source_tz: str = "UTC", now: datetime | None = None,
                    ignore_offset: bool = False) -> tuple[datetime | None, str]:
    """Parse a feed date string into an aware UTC datetime.

    Dates WITHOUT an explicit offset are interpreted in the source's configured timezone
    (e.g. Asia/Riyadh, UTC+3), not silently as UTC. `ignore_offset` does the same for feeds known to
    stamp local time with a wrong offset (e.g. "Z"). Returns (datetime_utc | None, precision).
    Dates more than 15 minutes in the future are rejected as unknown (clock or time-zone errors).
    """
    if not raw or not raw.strip():
        return None, "unknown"
    raw = raw.strip()
    tz = ZoneInfo(source_tz) if source_tz else UTC
    dt: datetime | None = None
    precision = "exact"
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if len(raw) <= 10:
                precision = "date"
        except ValueError:
            dt = None
    if dt is None:
        m = _YY_RE.match(raw)
        if m:
            yy, mm, dd, hh, mi = (int(x) for x in m.groups())
            dt = datetime(2000 + yy, mm, dd, hh, mi)
    if dt is None:
        m = _DMY_RE.match(raw)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh, mi = int(m.group(4) or 0), int(m.group(5) or 0)
            dt = datetime(y, mo, d, hh, mi)
            precision = "exact" if m.group(4) else "date"
    if dt is None:
        m = _RFC_DATE_ONLY_RE.match(raw)
        if m and m.group(2).lower() in _MONTHS:
            dt = datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
            precision = "date"
    if dt is None:
        return None, "unknown"
    if ignore_offset:
        dt = dt.replace(tzinfo=None)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    dt = dt.astimezone(UTC)
    now = now or datetime.now(UTC)
    if dt > now + MAX_FUTURE_SKEW:
        return None, "unknown"
    return dt, precision
