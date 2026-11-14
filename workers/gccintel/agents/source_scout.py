"""Source Scout Agent (A).

Proposes candidate sources from the curated catalog and verifies them technically (robots.txt
permission, reachable feed, parseable items, timezone sanity). It never crawls the open web for
new sources, never assumes a platform offers an API, and never approves or enables a source:
licence review and approval stay with an admin.
"""

from __future__ import annotations

import psycopg

from ..adapters.http import PoliteFetcher
from ..db import audit, execute, fetch_one
from ..seed import add_catalog_sources
from .ingestion import adapter_for

AGENT = "source-scout"


def propose_catalog(conn: psycopg.Connection, tenant_id: str) -> int:
    n = add_catalog_sources(conn, tenant_id, keys=None, approve=False)
    audit(conn, tenant_id=tenant_id, action="sources.proposed", entity_type="tenant", entity_id=tenant_id, agent=AGENT,
          details={"proposed": n})
    return n


def verify_source(conn: psycopg.Connection, source_id: str, fetcher: PoliteFetcher) -> dict:
    src = fetch_one(conn, "select * from sources where id = %s", (source_id,))
    if src is None:
        raise ValueError("source not found")
    adapter = adapter_for(src, fetcher)
    if adapter is None:
        note, ok = f"adapter '{src['adapter']}' not implemented", False
        items = 0
    else:
        res = adapter.fetch({**src, "etag": None, "last_modified": None})
        items = len(res.items)
        ok = res.error is None and items > 0
        if res.robots_allowed is False:
            note = "robots.txt disallows fetching this URL; source must not be enabled."
        elif res.error:
            note = f"fetch failed: {res.error}"
        elif not items:
            note = "reachable but no items parsed"
        else:
            dated = sum(1 for i in res.items if i.published_at)
            note = f"ok: {items} items parsed, {dated} with dates; HTTP {res.http_status}"
    execute(conn, """update sources set verified_at = case when %s then now() else verified_at end,
                     verification_note = %s where id = %s""", (ok, note[:500], source_id))
    audit(conn, tenant_id=str(src["tenant_id"]), action="source.verified", entity_type="source", entity_id=str(source_id),
          agent=AGENT, details={"ok": ok, "note": note, "items": items})
    return {"source_id": str(source_id), "ok": ok, "note": note, "items": items}
