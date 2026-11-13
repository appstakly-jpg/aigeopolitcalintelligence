"""Minimal, ordered SQL migration runner.

Applies db/migrations/*.sql in lexical order, each inside its own transaction, and
records the filename + checksum in public.schema_migrations. A changed checksum for an
already-applied migration is an error (migrations are immutable once applied).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from .log import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def migrate(dsn: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    applied_now: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """create table if not exists public.schema_migrations (
                 filename text primary key,
                 checksum text not null,
                 applied_at timestamptz not null default now())"""
        )
        conn.execute("alter table public.schema_migrations enable row level security")
        rows = conn.execute("select filename, checksum from public.schema_migrations").fetchall()
        done = {r[0]: r[1] for r in rows}
        files = sorted(p for p in migrations_dir.glob("*.sql"))
        if not files:
            raise RuntimeError(f"No migrations found in {migrations_dir}")
        for path in files:
            sql = path.read_text(encoding="utf-8")
            checksum = _checksum(sql)
            if path.name in done:
                if done[path.name] != checksum:
                    raise RuntimeError(
                        f"Migration {path.name} changed after being applied (checksum mismatch). "
                        "Create a new migration instead of editing an applied one."
                    )
                continue
            log.info("applying migration", extra={"migration": path.name})
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "insert into public.schema_migrations(filename, checksum) values (%s, %s)", (path.name, checksum)
                )
            applied_now.append(path.name)
    return applied_now


def ensure_database(admin_dsn: str, dbname: str) -> bool:
    """Create the database if missing (local/dev convenience). Returns True if created."""
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone()
        if exists:
            return False
        conn.execute(f'create database "{dbname}"')  # dbname validated by caller
        return True
