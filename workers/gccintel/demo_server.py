"""Local feed server for SYNTHETIC demo data (DEMO_MODE only).

Serves RSS 2.0 feeds at /feeds/<slug>.xml and an article page per item, all bannered as
synthetic. Items are released in stages so the pipeline can be shown evolving: stage 1
(initial reports) then stage 2 (denial, correction, follow-ups). Advance with
POST /admin/stage?set=2 (loopback only) or --auto-advance SECONDS.
"""

from __future__ import annotations

import html
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .refdata import load_json
from .seed import DEMO_SOURCES


class DemoFeedState:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.data = load_json("demo_feeds.json")
        self.released: dict[str, datetime] = {"1": datetime.now(UTC)}
        self.lock = threading.Lock()

    @property
    def stage(self) -> int:
        return max(int(s) for s in self.released)

    def set_stage(self, stage: int) -> None:
        with self.lock:
            for s in range(1, stage + 1):
                self.released.setdefault(str(s), datetime.now(UTC))

    def items(self, feed: str | None = None) -> list[dict]:
        out = []
        for stage, at in sorted(self.released.items()):
            for it in self.data["stages"].get(stage, []):
                if feed and it["feed"] != feed:
                    continue
                out.append({**it, "published": at - timedelta(minutes=it["minutes_ago"]),
                            "link": f"{self.base_url}/articles/{it['feed']}/{it['id']}.html"})
        return sorted(out, key=lambda i: i["published"], reverse=True)

    def rss(self, slug: str) -> str:
        src = next((s for s in DEMO_SOURCES if s["slug"] == slug), None)
        if src is None:
            raise KeyError(slug)
        parts = [f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
                 f"<title>{html.escape(src['name'])}</title><link>{self.base_url}/</link>"
                 f"<description>SYNTHETIC demo feed - not real news</description><language>{src['language']}</language>"]
        for it in self.items(slug):
            parts.append(
                f"<item><title>{html.escape(it['title'])}</title><link>{it['link']}</link>"
                f"<guid isPermaLink=\"false\">synthetic-{it['id']}</guid>"
                f"<pubDate>{format_datetime(it['published'])}</pubDate>"
                f"<category>synthetic</category><description>{html.escape(it['body'])}</description></item>")
        parts.append("</channel></rss>")
        return "".join(parts)


def make_handler(state: DemoFeedState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, code: int, body: str, ctype: str) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_HEAD(self):  # noqa: N802
            self.do_GET(head=True)

        def do_GET(self, head: bool = False):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/robots.txt":
                return self._send(200, "User-agent: *\nAllow: /\n", "text/plain")
            if path == "/admin/stage":
                return self._send(200, json.dumps({"stage": state.stage}), "application/json")
            if path.startswith("/feeds/") and path.endswith(".xml"):
                try:
                    return self._send(200, state.rss(path[len("/feeds/"):-4]), "application/rss+xml; charset=utf-8")
                except KeyError:
                    return self._send(404, "unknown feed", "text/plain")
            if path.startswith("/articles/"):
                slug = path.rsplit("/", 1)[-1].removesuffix(".html")
                it = next((i for i in state.items() if i["id"] == slug), None)
                if it is None:
                    return self._send(404, "not found", "text/plain")
                page = (f"<!doctype html><meta charset=utf-8><title>{html.escape(it['title'])}</title>"
                        f"<p style='background:#fde68a;padding:8px'><b>SYNTHETIC DEMO ARTICLE - not a real event.</b></p>"
                        f"<h1>{html.escape(it['title'])}</h1><p>{html.escape(it['body'])}</p>")
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "/":
                return self._send(200, "GCC Intelligence synthetic demo feeds", "text/plain")
            return self._send(404, "not found", "text/plain")

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path != "/admin/stage" or self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._send(403, "forbidden", "text/plain")
            stage = int(parse_qs(u.query).get("set", ["2"])[0])
            state.set_stage(stage)
            return self._send(200, json.dumps({"stage": state.stage}), "application/json")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8088, base_url: str | None = None, stage: int = 1,
          auto_advance: int | None = None, background: bool = False):
    state = DemoFeedState(base_url or f"http://{host}:{port}")
    state.set_stage(stage)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    if auto_advance:
        def _advance():
            time.sleep(auto_advance)
            state.set_stage(2)
        threading.Thread(target=_advance, daemon=True).start()
    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, state
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return server, state
