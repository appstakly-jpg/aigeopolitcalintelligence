"""Job worker: claims queued jobs and runs the matching agent with retries and observability.
Every handler validates that the objects in its payload belong to the job's tenant, because
web users can enqueue (a fixed set of) job kinds for their own tenant."""

from __future__ import annotations

import signal
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg

from . import queue
from .adapters.http import PoliteFetcher
from .agents import alerts as alert_agent
from .agents import briefing, quality, risk, source_scout
from .config import Settings, get_settings
from .db import connect, execute, fetch_one
from .llm.service import LLMService, build_provider
from .log import get_logger
from .notify.base import Message
from .pipeline import apply_analyst_edit, make_context, run_pipeline
from .refdata import taxonomy_from_db
from .reports.render import render_docx, render_pdf

log = get_logger(__name__)
STALE_RECOVERY_INTERVAL_SECONDS = 300
Handler = Callable[[psycopg.Connection, dict, Settings], dict]
HANDLERS: dict[str, Handler] = {}


class PermanentJobError(Exception):
    """Do not retry (bad payload, wrong tenant, missing configuration)."""


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


def _owned(conn: psycopg.Connection, table: str, obj_id: str | None, tenant_id: str) -> dict:
    if not obj_id:
        raise PermanentJobError(f"missing {table} id in payload")
    row = fetch_one(conn, f"select * from {table} where id = %s and tenant_id = %s", (obj_id, tenant_id))  # noqa: S608
    if row is None:
        raise PermanentJobError(f"{table} {obj_id} not found for this tenant")
    return row


def _ts(value: str | None) -> datetime | None:
    """Parse an ISO timestamp from a job payload (naive values are taken as UTC)."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PermanentJobError(f"invalid timestamp {value!r}") from exc
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


@handler("run_pipeline")
def _run_pipeline(conn, job, settings):
    out = run_pipeline(conn, job["tenant_id"], settings=settings, force=bool(job["payload"].get("force")))
    return out["stats"]


@handler("recompute_incident")
def _recompute_incident(conn, job, settings):
    inc = _owned(conn, "incidents", job["payload"].get("incident_id"), job["tenant_id"])
    ctx = make_context(conn, str(job["tenant_id"]), settings)
    with conn.transaction():
        return apply_analyst_edit(ctx, str(inc["id"]), job["payload"].get("note"),
                                  str(job["requested_by"]) if job.get("requested_by") else None)


@handler("fetch_source")
def _fetch_source(conn, job, settings):
    src = _owned(conn, "sources", job["payload"].get("source_id"), job["tenant_id"])
    return run_pipeline(conn, job["tenant_id"], settings=settings, force=True, source_ids=[str(src["id"])])["stats"]


@handler("generate_report")
def _generate_report(conn, job, settings):
    p = job["payload"]
    tax = taxonomy_from_db(conn, job["tenant_id"])
    llm = LLMService(conn, str(job["tenant_id"]), build_provider(settings), settings)
    if p.get("report_id"):
        _owned(conn, "reports", p["report_id"], job["tenant_id"])
    with conn.transaction():
        rep = briefing.generate_report(conn, str(job["tenant_id"]), p.get("report_type", "daily_gcc"), tax,
                                       country=p.get("country_code"), llm=llm, created_by=job["requested_by"],
                                       report_id=p.get("report_id"), change_note=p.get("change_note"),
                                       period_start=_ts(p.get("period_start")), period_end=_ts(p.get("period_end")))
    return {"report_id": str(rep["id"]), "ref": rep["ref"], "version": rep["version"],
            "citation_check_ok": rep["citation_check"]["ok"], "citations": rep["citations"]}


@handler("export_report")
def _export_report(conn, job, settings):
    p = job["payload"]
    rep = _owned(conn, "reports", p.get("report_id"), job["tenant_id"])
    v = fetch_one(conn, "select * from report_versions where report_id = %s and version = %s",
                  (rep["id"], p.get("version") or rep["current_version"]))
    if v is None:
        raise PermanentJobError("report version not found")
    fmt = p.get("format", "docx")
    meta = {"ref": rep["ref"], "version": v["version"], "status": rep["status"],
            "generated_at": v["created_at"].isoformat(), "branding": briefing.report_branding(conn, job["tenant_id"])}
    if fmt == "html":
        data, ctype = v["html"].encode("utf-8"), "text/html; charset=utf-8"
    elif fmt == "docx":
        data = render_docx(v["content"], v["citations"], meta)
        ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "pdf":
        if not settings.pdf_export_enabled:
            raise PermanentJobError("PDF export disabled (set PDF_EXPORT_ENABLED=true and install the pdf extra)")
        try:
            data = render_pdf(v["html"])
        except RuntimeError as exc:
            raise PermanentJobError(str(exc)) from exc
        ctype = "application/pdf"
    else:
        raise PermanentJobError(f"unsupported format {fmt}")
    execute(conn, """insert into report_exports(tenant_id, report_version_id, format, content_type, bytes)
                     values (%s, %s, %s, %s, %s)
                     on conflict (report_version_id, format) do update set bytes = excluded.bytes,
                       content_type = excluded.content_type, created_at = now()""",
            (job["tenant_id"], v["id"], fmt, ctype, data))
    return {"report_id": str(rep["id"]), "version": v["version"], "format": fmt, "bytes": len(data)}


@handler("compute_risk")
def _compute_risk(conn, job, settings):
    with conn.transaction():
        rows = risk.compute_assessments(conn, str(job["tenant_id"]), taxonomy_from_db(conn, job["tenant_id"]))
    return {"assessments": len(rows), "insufficient": sum(1 for r in rows if r["band"] == "insufficient_data")}


@handler("quality_audit")
def _quality(conn, job, settings):
    fetcher = PoliteFetcher(settings)
    try:
        with conn.transaction():
            out = quality.audit_tenant(conn, str(job["tenant_id"]), settings, fetcher)
    finally:
        fetcher.close()
    return {"findings": len(out["findings"]), "links_checked": out["links_checked"], "links_broken": out["links_broken"]}


@handler("source_scout")
def _scout(conn, job, settings):
    with conn.transaction():
        return {"proposed": source_scout.propose_catalog(conn, str(job["tenant_id"]))}


@handler("verify_source")
def _verify(conn, job, settings):
    src = _owned(conn, "sources", job["payload"].get("source_id"), job["tenant_id"])
    fetcher = PoliteFetcher(settings)
    try:
        return source_scout.verify_source(conn, str(src["id"]), fetcher)
    finally:
        fetcher.close()


@handler("test_notification")
def _test_notification(conn, job, settings):
    rule = _owned(conn, "alert_rules", job["payload"].get("rule_id"), job["tenant_id"])
    results = {}
    for ch in rule["channels"]:
        channel, dest = alert_agent.channel_for(conn, ch, str(job["tenant_id"]), rule, settings)
        res = channel.send(dest, Message(subject="Test notification from GCC Intelligence",
                                         text=f"This is a test of alert rule '{rule['name']}'. No incident is attached."))
        results[ch] = {"status": res.status, "error": res.error}
    return results


@handler("deliver_alerts")
def _deliver(conn, job, settings):
    with conn.transaction():
        out = alert_agent.deliver_pending(conn, str(job["tenant_id"]), settings)
    return {"attempted": len(out), "sent": sum(1 for o in out if o.get("status") == "sent")}


@handler("apply_retention")
def _retention(conn, job, settings):
    return fetch_one(conn, "select app.apply_retention() as r")["r"]


def run_job(conn: psycopg.Connection, job: dict, settings: Settings) -> str:
    fn = HANDLERS.get(job["kind"])
    t0 = time.monotonic()
    if fn is None:
        return queue.fail(conn, job, f"no handler for job kind {job['kind']}", retryable=False)
    try:
        result = fn(conn, job, settings) or {}
        result = {**result, "duration_ms": int((time.monotonic() - t0) * 1000)}
        queue.complete(conn, job["id"], result)
        log.info("job succeeded", extra={"job_id": job["id"], "kind": job["kind"], "tenant_id": str(job["tenant_id"])})
        return "succeeded"
    except PermanentJobError as exc:
        log.warning("job failed permanently", extra={"job_id": job["id"], "kind": job["kind"], "error": str(exc)})
        return queue.fail(conn, job, str(exc), retryable=False)
    except Exception as exc:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        log.exception("job failed", extra={"job_id": job["id"], "kind": job["kind"]})
        return queue.fail(conn, job, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")


def run_worker(settings: Settings | None = None, *, once: bool = False, kinds: list[str] | None = None) -> int:
    settings = settings or get_settings()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    processed = 0
    conn = connect(settings.database_url, autocommit=True)
    queue.recover_stale(conn)
    last_recovery = time.monotonic()
    log.info("worker started", extra={"worker_id": settings.worker_id})
    try:
        while not stop["flag"]:
            if time.monotonic() - last_recovery > STALE_RECOVERY_INTERVAL_SECONDS:
                # Re-queue jobs left "running" by a worker that died, without waiting for a restart.
                queue.recover_stale(conn)
                last_recovery = time.monotonic()
            job = queue.claim(conn, settings.worker_id, kinds)
            if job is None:
                if once:
                    break
                time.sleep(settings.worker_poll_seconds)
                continue
            run_job(conn, job, settings)
            processed += 1
    finally:
        conn.close()
    return processed
