"""Reference data loading: bundled JSON defaults and per-tenant overrides from the database."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import psycopg

from .db import fetch_all
from .text.gazetteer import Gazetteer, GeoEntity
from .text.taxonomy import Actor, EventType, Taxonomy

DATA_DIR = Path(__file__).resolve().parent / "data"


@cache
def load_json(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def default_gazetteer() -> Gazetteer:
    return Gazetteer([GeoEntity.from_row(e) for e in load_json("geo.json")["entities"]])


def default_taxonomy() -> Taxonomy:
    tax = load_json("taxonomy.json")
    return Taxonomy(
        [EventType.from_row(e) for e in tax["event_types"]],
        tax["sectors"],
        [Actor.from_row(a) for a in load_json("actors.json")["actors"]],
    )


def gazetteer_from_db(conn: psycopg.Connection) -> Gazetteer:
    rows = fetch_all(conn, "select * from geo_entities")
    return Gazetteer([GeoEntity.from_row(r) for r in rows]) if rows else default_gazetteer()


def taxonomy_from_db(conn: psycopg.Connection, tenant_id: str) -> Taxonomy:
    rows = fetch_all(
        conn,
        """select distinct on (key) * from event_types
           where tenant_id is null or tenant_id = %s
           order by key, tenant_id nulls last""",
        (tenant_id,),
    )
    actors = fetch_all(conn, "select * from actors where tenant_id is null or tenant_id = %s", (tenant_id,))
    sectors = load_json("taxonomy.json")["sectors"]
    if not rows:
        return default_taxonomy()
    return Taxonomy([EventType.from_row(r) for r in rows], sectors, [Actor.from_row(a) for a in actors])


GCC_COUNTRIES = ["SA", "AE", "QA", "KW", "BH", "OM"]
COUNTRY_NAMES = {
    "SA": "Saudi Arabia", "AE": "United Arab Emirates", "QA": "Qatar", "KW": "Kuwait", "BH": "Bahrain", "OM": "Oman",
    "IR": "Iran", "IQ": "Iraq", "YE": "Yemen", "IL": "Israel", "PS": "Palestinian Territories", "JO": "Jordan",
    "LB": "Lebanon", "SY": "Syria", "EG": "Egypt", "DJ": "Djibouti", "ER": "Eritrea", "ET": "Ethiopia",
    "SO": "Somalia", "SD": "Sudan",
}
