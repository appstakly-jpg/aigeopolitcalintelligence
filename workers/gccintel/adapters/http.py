"""Polite HTTP client for source retrieval.

* Honours robots.txt (RFC 9309: 4xx = allowed, 5xx/unreachable = disallowed) and Crawl-delay
* Per-host minimum interval between requests
* Conditional GET (ETag / Last-Modified)
* Response size cap, redirect cap, timeouts
* SSRF guard: private/loopback hosts are refused unless ALLOW_PRIVATE_NETWORK_FETCH=true
Never used to bypass paywalls, logins or rate limits: 401/402/403/429 are reported as-is.
"""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from ..log import get_logger
from ..text.urls import is_private_host

log = get_logger(__name__)

ROBOTS_TTL_SECONDS = 6 * 3600


class FetchBlocked(Exception):
    """Fetching is not permitted (robots.txt, private network, policy)."""


@dataclass
class FetchResponse:
    url: str
    final_url: str
    status: int
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    not_modified: bool = False
    robots_allowed: bool | None = None


class PoliteFetcher:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.client = httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout_seconds, connect=10.0),
            headers={
                "User-Agent": settings.http_user_agent,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, application/json;q=0.8, text/html;q=0.5, */*;q=0.1",
                "Accept-Language": "en, ar;q=0.9",
            },
            follow_redirects=True,
            max_redirects=5,
            transport=transport,
        )
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser | None, bool, float]] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self.client.close()

    # -- robots.txt -------------------------------------------------------
    def _robots_for(self, url: str) -> tuple[urllib.robotparser.RobotFileParser | None, bool]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached and time.time() - cached[2] < ROBOTS_TTL_SECONDS:
            return cached[0], cached[1]
        parser: urllib.robotparser.RobotFileParser | None = None
        allow_all = False
        try:
            resp = self.client.get(origin + "/robots.txt", timeout=10.0)
            if resp.status_code >= 500:
                allow_all = False  # unreachable -> assume disallow (RFC 9309 2.3.1.4)
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(["User-agent: *", "Disallow: /"])
            elif resp.status_code >= 400:
                allow_all = True   # unavailable -> crawler may access (RFC 9309 2.3.1.3)
            else:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(resp.text.splitlines())
        except httpx.HTTPError:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
        self._robots[origin] = (parser, allow_all, time.time())
        return parser, allow_all

    def robots_allowed(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        parser, allow_all = self._robots_for(url)
        if allow_all or parser is None:
            return True
        return parser.can_fetch(self.settings.http_user_agent, url)

    def _crawl_delay(self, url: str) -> float:
        parser, _ = self._robots_for(url) if self.settings.respect_robots else (None, True)
        delay = None
        if parser is not None:
            try:
                delay = parser.crawl_delay(self.settings.http_user_agent)
            except Exception:  # noqa: BLE001 - malformed robots file
                delay = None
        return max(self.settings.per_host_min_interval_seconds, float(delay or 0))

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        wait = self._crawl_delay(url)
        with self._lock:
            last = self._last_hit.get(host)
            now = time.monotonic()
            sleep_for = (last + wait - now) if last else 0
            self._last_hit[host] = max(now, (last or 0) + wait)
        if sleep_for > 0:
            time.sleep(min(sleep_for, 30))

    # -- fetch ------------------------------------------------------------
    def get(self, url: str, *, etag: str | None = None, last_modified: str | None = None,
            check_robots: bool = True) -> FetchResponse:
        if not url.lower().startswith(("http://", "https://")):
            raise FetchBlocked(f"unsupported URL scheme: {url}")
        if not self.settings.allow_private_network_fetch and is_private_host(url):
            raise FetchBlocked("private/loopback network address refused (set ALLOW_PRIVATE_NETWORK_FETCH for local demo feeds)")
        robots_ok = True
        if check_robots:
            robots_ok = self.robots_allowed(url)
            if not robots_ok:
                raise FetchBlocked("disallowed by robots.txt")
        self._throttle(url)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        start = time.monotonic()
        with self.client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 304:
                return FetchResponse(url, str(resp.url), 304, b"", dict(resp.headers),
                                     int((time.monotonic() - start) * 1000), True, robots_ok)
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > self.settings.http_max_bytes:
                    raise FetchBlocked(f"response exceeds {self.settings.http_max_bytes} bytes")
                chunks.append(chunk)
            return FetchResponse(url, str(resp.url), resp.status_code, b"".join(chunks), dict(resp.headers),
                                 int((time.monotonic() - start) * 1000), False, robots_ok)

    def head_status(self, url: str) -> tuple[int | None, str | None]:
        """Lightweight link check used by the quality auditor (respects robots + throttling)."""
        try:
            if not self.robots_allowed(url):
                return None, "robots"
            if not self.settings.allow_private_network_fetch and is_private_host(url):
                return None, "private"
            self._throttle(url)
            resp = self.client.head(url)
            if resp.status_code in (405, 501):  # HEAD not supported
                resp = self.client.get(url, headers={"Range": "bytes=0-1024"})
            return resp.status_code, str(resp.url)
        except httpx.HTTPError as exc:
            return None, type(exc).__name__
