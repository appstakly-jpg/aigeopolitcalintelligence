"""Risk Analyst Agent (G): transparent, versioned country x dimension risk indices.

Method (documented in the methodology record and docs/methodology.md):
  observed severity  S = sum over incidents i in the window of
                         severity_weight(sev_i) x verification_weight(status_i) x 0.5^(age_i / half_life)
                         x country_weight (1.0 primary country, affected_country_weight if only affected)
  exposure           E = tenant watchlist exposure multiplier for the country (default 1.0)
  vulnerability      V = methodology vulnerability multiplier (default 1.0)
  score              = 5 x (1 - exp(-S x E x V / k_dimension))      (0..5, saturating)
  band               = from methodology thresholds
  confidence         = from healthy source coverage and the corroborated share of S
Severity (what happened), exposure (what the client has at stake), vulnerability (how
susceptible) and uncertainty (confidence / coverage) are stored separately in `components`.
Without a healthy source covering the country the result is `insufficient_data`, never 0.
Scores describe recent observed reporting; they are not forecasts.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import psycopg

from ..db import fetch_all, fetch_one, jsonb
from ..refdata import GCC_COUNTRIES, load_json
from ..text.taxonomy import Taxonomy


def active_methodology(conn: psycopg.Connection, tenant_id: str) -> dict:
    row = fetch_one(conn, """
        select * from risk_methodologies where is_active and (tenant_id = %s or tenant_id is null)
        order by tenant_id nulls last, created_at desc limit 1""", (tenant_id,))
    if not row:
        raise RuntimeError("no active risk methodology; run `gccintel seed`")
    return row


def band_for(score: float, bands: list) -> str:
    for upper, name in bands:
        if score < float(upper):
            return name
    return bands[-1][1]


def healthy_coverage(conn: psycopg.Connection, tenant_id: str, country: str, max_failures: int) -> list[dict]:
    return fetch_all(conn, """
        select id, name, reliability from sources
        where tenant_id = %s and enabled and status = 'approved' and consecutive_failures < %s
          and last_success_at > now() - interval '3 days'
          and (country_focus = '{}' or %s = any(country_focus))""", (tenant_id, max_failures, country))


def exposure_for(conn: psycopg.Connection, tenant_id: str, default: float) -> dict[str, float]:
    rows = fetch_all(conn, "select exposure from watchlists where tenant_id = %s and user_id is null", (tenant_id,))
    out: dict[str, float] = {}
    for r in rows:
        for k, v in (r["exposure"] or {}).items():
            try:
                out[k] = max(out.get(k, 0.0), float(v))
            except (TypeError, ValueError):
                continue
    return {c: out.get(c, default) for c in GCC_COUNTRIES}


def compute_assessments(conn: psycopg.Connection, tenant_id: str, taxonomy: Taxonomy,
                        as_of: datetime | None = None, countries: list[str] | None = None) -> list[dict]:
    as_of = as_of or datetime.now(UTC)
    meth = active_methodology(conn, tenant_id)
    p = meth["params"]
    window_start = as_of - timedelta(days=int(p["window_days"]))
    half_life = float(p["half_life_days"])
    sev_w = {int(k): float(v) for k, v in p["severity_weights"].items()}
    ver_w = {k: float(v) for k, v in p["verification_weights"].items()}
    aff_w = float(p["affected_country_weight"])
    ks = p["saturation_k"]
    conf_p = p["confidence"]
    vuln = p.get("vulnerability", {})
    exposure = exposure_for(conn, tenant_id, float(p.get("exposure_default", 1.0)))
    dims = [d["key"] for d in load_json("taxonomy.json")["risk_dimensions"]]

    incidents = fetch_all(conn, """
        select id, ref, title, severity, verification_status, categories, country_code, affected_countries,
               coalesce(occurred_at, first_reported_at, created_at) as t, is_demo
        from incidents
        where tenant_id = %s and status in ('active', 'closed') and review_status <> 'rejected'
          and coalesce(occurred_at, first_reported_at, created_at) between %s and %s""",
                          (tenant_id, window_start, as_of))
    out = []
    for country in countries or GCC_COUNTRIES:
        coverage = healthy_coverage(conn, tenant_id, country, int(p["source_healthy_max_failures"]))
        for dim in dims:
            contributions = []
            s_total = 0.0
            s_corroborated = 0.0
            for inc in incidents:
                if dim not in taxonomy.dimensions_for(inc["categories"] or []):
                    continue
                if inc["country_code"] == country:
                    cw = 1.0
                elif country in (inc["affected_countries"] or []):
                    cw = aff_w
                else:
                    continue
                age_days = max(0.0, (as_of - inc["t"]).total_seconds() / 86400)
                decay = 0.5 ** (age_days / half_life)
                c = sev_w.get(int(inc["severity"]), 1.0) * ver_w.get(inc["verification_status"], 0.2) * decay * cw
                s_total += c
                if inc["verification_status"] in ("confirmed", "corroborated"):
                    s_corroborated += c
                contributions.append({
                    "incident_id": str(inc["id"]), "ref": inc["ref"], "title": inc["title"],
                    "severity": inc["severity"], "verification_status": inc["verification_status"],
                    "decay": round(decay, 3), "country_weight": cw, "contribution": round(c, 3),
                    "is_demo": inc["is_demo"],
                })
            k = float(ks.get(dim, ks["default"]))
            e = exposure.get(country, 1.0)
            v = float(vuln.get(country, p.get("vulnerability_default", 1.0)))
            share = (s_corroborated / s_total) if s_total else 0.0
            if not coverage:
                score, band, confidence = None, "insufficient_data", "low"
            else:
                score = round(5 * (1 - math.exp(-s_total * e * v / k)), 2)
                band = band_for(score, p["bands"])
                if len(coverage) >= int(conf_p["high_sources"]) and (share >= float(conf_p["high_corroborated_share"]) or not contributions):
                    confidence = "high" if contributions else "moderate"
                elif len(coverage) >= int(conf_p["moderate_sources"]) and (share >= float(conf_p["moderate_corroborated_share"]) or not contributions):
                    confidence = "moderate"
                else:
                    confidence = "low"
            contributions.sort(key=lambda c: c["contribution"], reverse=True)
            components = {
                "observed_severity": round(s_total, 3), "corroborated_share": round(share, 3), "exposure": e,
                "vulnerability": v, "k": k, "window_days": p["window_days"], "half_life_days": half_life,
                "healthy_sources": len(coverage), "coverage_sources": [c["name"] for c in coverage][:10],
                "note": "Observed-reporting index, not a forecast. Absence of reporting is not evidence of absence.",
            }
            row = fetch_one(conn, """
                insert into risk_assessments(tenant_id, methodology_id, country_code, dimension, window_start, window_end,
                    as_of_date, score, band, confidence, forecast_uncertainty, components, contributions,
                    evidence_incident_ids, incident_count, computed_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'high', %s, %s, %s, %s, now())
                on conflict (tenant_id, methodology_id, country_code, dimension, as_of_date) do update set
                    window_start = excluded.window_start, window_end = excluded.window_end, score = excluded.score,
                    band = excluded.band, confidence = excluded.confidence, components = excluded.components,
                    contributions = excluded.contributions, evidence_incident_ids = excluded.evidence_incident_ids,
                    incident_count = excluded.incident_count, computed_at = now()
                returning id, country_code, dimension, score, band, confidence""",
                            (tenant_id, meth["id"], country, dim, window_start, as_of, as_of.date(), score, band,
                             confidence, jsonb(components), jsonb(contributions[:25]),
                             [c["incident_id"] for c in contributions[:50]], len(contributions)))
            out.append(row)
    return out
