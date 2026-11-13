"""Database access for workers.

Workers connect as the schema owner (RLS bypass) because they process every tenant.
Every query that touches tenant data MUST filter by tenant_id explicitly; helpers here
take tenant_id as a required argument to make that hard to forget.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import get_settings


def connect(dsn: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn or get_settings().database_url, row_factory=dict_row, autocommit=autocommit)


@contextmanager
def transaction(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    with conn.transaction():
        yield conn


def fetch_one(conn: psycopg.Connection, sql: str, params: Any = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def execute(conn: psycopg.Connection, sql: str, params: Any = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value, dumps=lambda v: json.dumps(v, ensure_ascii=False, default=str))


def audit(
    conn: psycopg.Connection,
    *,
    tenant_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    agent: str | None = None,
    user_id: str | None = None,
    details: dict | None = None,
) -> None:
    execute(
        conn,
        """insert into audit_log(tenant_id, actor_user_id, actor_agent, action, entity_type, entity_id, details)
           values (%s, %s, %s, %s, %s, %s, %s)""",
        (tenant_id, user_id, agent, action, entity_type, str(entity_id) if entity_id else None, jsonb(details or {})),
    )
