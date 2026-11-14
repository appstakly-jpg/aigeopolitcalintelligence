"""Scheduler: enqueues recurring jobs per tenant (idempotent via dedupe keys).

  every tick       run_pipeline when a source is due; deliver_alerts when approved alerts wait
  hourly           compute_risk
  daily (local)    daily_gcc brief at `reports.daily_brief_hour` (default 06:00 tenant time),
                   quality_audit at 03:00, retention at 02:00 UTC (global)
"""

from __future__ import annotations

import signal
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import psycopg

from . import queue
from .config import Settings, get_settings
from .db import connect, fetch_all, fetch_one
from .log import get_logger

log = get_logger(__name__)


def tick(conn: psycopg.Connection, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    enq: list[str] = []
    for t in fetch_all(conn, "select id, slug, settings from tenants where status = 'active'"):
        tid = str(t["id"])
        tz = ZoneInfo((t["settings"] or {}).get("timezone") or "Asia/Riyadh")
        local = now.astimezone(tz)
        due = fetch_one(conn, """select count(*) as n from sources where tenant_id = %s and enabled
                                 and (next_fetch_at is null or next_fetch_at <= %s)""", (tid, now))["n"]
        if due and queue.enqueue(conn, "run_pipeline", {}, tenant_id=tid, dedupe_key=f"pipeline:{tid}", priority=3):
            enq.append(f"{t['slug']}:run_pipeline")
        waiting = fetch_one(conn, """select count(*) as n from alerts a where a.tenant_id = %s and a.status = 'approved'""",
                            (tid,))["n"]
        if waiting and queue.enqueue(conn, "deliver_alerts", {}, tenant_id=tid, dedupe_key=f"deliver:{tid}", priority=2):
            enq.append(f"{t['slug']}:deliver_alerts")
        ran_risk = fetch_one(conn, """select 1 from jobs where tenant_id = %s and kind = 'compute_risk'
                                      and created_at >= date_trunc('hour', %s::timestamptz)""", (tid, now))
        if not ran_risk and queue.enqueue(conn, "compute_risk", {}, tenant_id=tid, dedupe_key=f"risk:{tid}", priority=6):
            enq.append(f"{t['slug']}:compute_risk")
        brief_hour = int(((t["settings"] or {}).get("reports") or {}).get("daily_brief_hour", 6))
        if local.hour >= brief_hour:
            exists = fetch_one(conn, """select 1 from reports where tenant_id = %s and report_type = 'daily_gcc'
                                        and created_by_agent = 'briefing-agent' and (created_at at time zone %s)::date = %s""",
                               (tid, str(tz), local.date()))
            if not exists and queue.enqueue(conn, "generate_report", {"report_type": "daily_gcc"}, tenant_id=tid,
                                            dedupe_key=f"daily:{tid}:{local:%Y%m%d}", priority=4):
                enq.append(f"{t['slug']}:daily_brief")
        if local.hour >= 3:
            ran = fetch_one(conn, """select 1 from jobs where tenant_id = %s and kind = 'quality_audit'
                                     and created_at >= %s""", (tid, local.replace(hour=0, minute=0, second=0, microsecond=0)))
            if not ran and queue.enqueue(conn, "quality_audit", {}, tenant_id=tid,
                                         dedupe_key=f"quality:{tid}:{local:%Y%m%d}", priority=7):
                enq.append(f"{t['slug']}:quality_audit")
    if now.hour >= 2:
        ran = fetch_one(conn, "select 1 from jobs where kind = 'apply_retention' and created_at >= %s",
                        (now.replace(hour=0, minute=0, second=0, microsecond=0),))
        if not ran and queue.enqueue(conn, "apply_retention", {}, dedupe_key=f"retention:{now:%Y%m%d}", priority=9):
            enq.append("retention")
    return enq


def run_scheduler(settings: Settings | None = None, *, interval: int = 60, once: bool = False) -> None:
    settings = settings or get_settings()
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    conn = connect(settings.database_url, autocommit=True)
    try:
        while not stop["flag"]:
            try:
                enq = tick(conn)
                if enq:
                    log.info("scheduled jobs", extra={"jobs": enq})
            except psycopg.Error:
                log.exception("scheduler tick failed")
            if once:
                break
            for _ in range(interval):
                if stop["flag"]:
                    break
                time.sleep(1)
    finally:
        conn.close()
