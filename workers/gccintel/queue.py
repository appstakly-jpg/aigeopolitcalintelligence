"""Postgres-backed job queue (FOR UPDATE SKIP LOCKED) with retries, backoff and dead-lettering.
Chosen over Redis/BullMQ to keep one fewer stateful service; the interface is small enough to
swap later."""

from __future__ import annotations

from datetime import datetime

import psycopg

from .db import execute, fetch_one, jsonb


def enqueue(conn: psycopg.Connection, kind: str, payload: dict | None = None, *, tenant_id: str | None = None,
            run_after: datetime | None = None, dedupe_key: str | None = None, priority: int = 5,
            max_attempts: int = 5, requested_by: str | None = None) -> int | None:
    row = fetch_one(conn, """
        insert into jobs(tenant_id, kind, payload, run_after, dedupe_key, priority, max_attempts, requested_by)
        values (%s, %s, %s, coalesce(%s, now()), %s, %s, %s, %s)
        on conflict (dedupe_key) where status in ('queued', 'running') and dedupe_key is not null do nothing
        returning id""", (tenant_id, kind, jsonb(payload or {}), run_after, dedupe_key, priority, max_attempts, requested_by))
    return row["id"] if row else None


def claim(conn: psycopg.Connection, worker_id: str, kinds: list[str] | None = None) -> dict | None:
    return fetch_one(conn, """
        update jobs set status = 'running', locked_by = %s, locked_at = now(), started_at = now(), attempts = attempts + 1
        where id = (select id from jobs where status = 'queued' and run_after <= now()
                      and (%s::text[] is null or kind = any(%s::text[]))
                    order by priority, run_after for update skip locked limit 1)
        returning *""", (worker_id, kinds, kinds))


def complete(conn: psycopg.Connection, job_id: int, result: dict | None) -> None:
    execute(conn, "update jobs set status = 'succeeded', finished_at = now(), result = %s, last_error = null where id = %s",
            (jsonb(result or {}), job_id))


def fail(conn: psycopg.Connection, job: dict, error: str, retryable: bool = True) -> str:
    if retryable and job["attempts"] < job["max_attempts"]:
        delay = min(3600, 30 * 2 ** (job["attempts"] - 1))
        execute(conn, """update jobs set status = 'queued', locked_by = null, locked_at = null, last_error = %s,
                         run_after = now() + make_interval(secs => %s) where id = %s""", (error[:2000], delay, job["id"]))
        return "queued"
    execute(conn, "update jobs set status = 'dead', finished_at = now(), last_error = %s where id = %s",
            (error[:2000], job["id"]))
    return "dead"


def recover_stale(conn: psycopg.Connection, older_than_minutes: int = 30) -> int:
    """Re-queue jobs whose worker died mid-run."""
    return execute(conn, """update jobs set status = 'queued', locked_by = null, locked_at = null,
                            last_error = coalesce(last_error, '') || ' [recovered after worker timeout]'
                            where status = 'running' and locked_at < now() - make_interval(mins => %s)""",
                   (older_than_minutes,))
