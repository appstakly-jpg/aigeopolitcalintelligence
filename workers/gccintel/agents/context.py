"""Regional Context Agent (F).

Adds deterministic, documented context to an incident: which GCC states are exposed and why
(spill-over rules), related recent incidents, and analytic watch indicators. It states
exposure pathways, not facts about the event, and never invents figures.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg

from ..db import execute, fetch_all, jsonb
from ..refdata import COUNTRY_NAMES, GCC_COUNTRIES
from ..text.gazetteer import Gazetteer

RED_SEA_ZONES = {"red_sea", "horn_of_africa"}
RULES = [
    # (region or entity predicate, categories, exposed GCC states, pathway text)
    ({"maritime:bab-el-mandeb", "maritime:red-sea", "maritime:gulf-of-aden", "corridor:suez-canal"}, None,
     {"maritime_incident", "shipping_disruption", "drone_missile"}, GCC_COUNTRIES,
     "Red Sea / Bab el-Mandeb transit disruption affects GCC trade routes to Europe and Saudi Red Sea ports."),
    ({"maritime:strait-of-hormuz", "maritime:gulf-of-oman", "maritime:arabian-gulf"}, None,
     {"maritime_incident", "shipping_disruption", "drone_missile", "energy_disruption"}, GCC_COUNTRIES,
     "Strait of Hormuz / Gulf waters carry most GCC hydrocarbon exports and imports."),
    (None, "YE", {"drone_missile", "cross_border_attack", "armed_clash"}, ["SA", "AE"],
     "Cross-border drone/missile activity from Yemen has historically targeted Saudi and Emirati territory."),
    (None, "IQ", {"drone_missile", "cross_border_attack"}, ["KW", "SA"],
     "Iraq-based drone/missile activity can affect Kuwaiti and Saudi border areas and airspace."),
    (None, "IR", {"sanctions", "diplomatic_crisis", "maritime_incident", "drone_missile", "geopolitical_development"},
     GCC_COUNTRIES, "Iran-related escalation or sanctions affect Gulf shipping, energy markets and regional diplomacy."),
    (None, "IL", {"drone_missile", "armed_clash", "cross_border_attack", "border_restrictions"}, ["SA", "AE", "BH", "QA"],
     "Escalation involving Israel can trigger airspace closures and aviation disruption across the Gulf."),
]
WATCH = {
    "drone_missile": ["Official confirmation or denial of interceptions", "Claims of responsibility",
                      "Airspace or airport restrictions (NOTAMs)"],
    "maritime_incident": ["Maritime security advisories (e.g. UKMTO / JMIC)", "Vessel rerouting announcements",
                          "War-risk insurance changes reported by industry press"],
    "shipping_disruption": ["Port congestion and transit-time reports", "Carrier service suspensions"],
    "energy_disruption": ["Operator statements on output or exports", "Force majeure declarations"],
    "infrastructure_disruption": ["Utility restoration statements", "Repeat outages in the same area"],
    "protest": ["Size and spread of gatherings as reported by multiple outlets", "Official restrictions on assembly"],
    "civil_unrest": ["Curfews or security deployments announced officially"],
    "cyber_incident": ["Attribution statements by national CERTs", "Service restoration notices"],
    "sanctions": ["Implementation guidance from the issuing authority", "Counterparty or shipping impacts"],
    "diplomatic_crisis": ["Ambassador recalls or expulsions", "Mediation announcements"],
    "border_restrictions": ["Duration of closures", "Exemptions for cargo or nationals"],
    "policy_change": ["Effective dates and implementing regulations"],
    "terrorism": ["Official threat-level changes", "Claims of responsibility"],
    "cross_border_attack": ["Official statements from both sides", "Retaliation announcements"],
    "armed_clash": ["Ceasefire statements", "Displacement reports from humanitarian agencies"],
}


def build_context(conn: psycopg.Connection, inc: dict, gazetteer: Gazetteer) -> dict:
    cats = set(inc["categories"] or [])
    ent = gazetteer.entities.get(inc["geo_entity_id"] or "")
    entity_ids = {inc["geo_entity_id"]} | ({a.id for a in gazetteer.ancestors(ent)} if ent else set())
    exposure: dict[str, list[str]] = {}
    for c in inc["affected_countries"] or []:
        if c in GCC_COUNTRIES:
            exposure.setdefault(c, []).append("Named or located in the reporting.")
    for ents, country, rule_cats, exposed, text in RULES:
        hit_place = bool(ents and entity_ids & ents)
        hit_country = bool(country and (inc["country_code"] == country or country in (inc["affected_countries"] or [])))
        if (hit_place or hit_country) and cats & rule_cats:
            for c in exposed:
                if text not in exposure.setdefault(c, []):
                    exposure[c].append(text)
    t = inc["last_reported_at"] or inc["created_at"]
    related = fetch_all(conn, """
        select id, ref, title, severity, verification_status, last_reported_at from incidents
        where tenant_id = %s and id <> %s and status in ('active', 'closed') and review_status <> 'rejected'
          and categories && %s::text[] and (country_code = %s or geo_entity_id = %s)
          and last_reported_at between %s and %s
        order by last_reported_at desc limit 5""",
                        (inc["tenant_id"], inc["id"], list(cats) or ["__none__"], inc["country_code"],
                         inc["geo_entity_id"], t - timedelta(days=14), t + timedelta(days=1)))
    watch = []
    for c in inc["categories"] or []:
        for w in WATCH.get(c, []):
            if w not in watch:
                watch.append(w)
    return {
        "gcc_exposure": [{"country_code": c, "country": COUNTRY_NAMES[c], "pathways": p} for c, p in exposure.items()],
        "related_incidents": [{"id": str(r["id"]), "ref": r["ref"], "title": r["title"], "severity": r["severity"],
                               "verification_status": r["verification_status"]} for r in related],
        "watch_indicators": watch[:6],
        "method": "rules-v1: spill-over pathways are analytic rules, not reported facts.",
    }


def apply_context(conn: psycopg.Connection, inc: dict, gazetteer: Gazetteer) -> dict:
    ctx = build_context(conn, inc, gazetteer)
    execute(conn, "update incidents set context = %s where id = %s", (jsonb(ctx), inc["id"]))
    return ctx
