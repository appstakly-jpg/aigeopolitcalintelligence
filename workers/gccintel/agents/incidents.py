"""Incident service: event matching, creation, evidence linking, deterministic severity,
verification, review policy and versioned revisions."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import psycopg

from ..db import execute, fetch_all, fetch_one, jsonb
from ..log import get_logger
from ..refdata import COUNTRY_NAMES
from ..text.gazetteer import Gazetteer
from ..text.simhash import jaccard
from ..text.taxonomy import Taxonomy
from . import verification
from .extraction import Extraction

log = get_logger(__name__)

AGENT = "incident-service"
CHOKEPOINTS = {"maritime:strait-of-hormuz", "maritime:bab-el-mandeb", "corridor:suez-canal"}
INFRA_CATEGORIES = {"drone_missile", "energy_disruption", "maritime_incident", "armed_clash", "terrorism",
                    "cross_border_attack", "cyber_incident"}
SNAPSHOT_FIELDS = ["title", "summary", "status", "verification_status", "credibility", "review_status", "severity",
                   "confidence", "primary_category", "categories", "occurred_at", "occurred_at_precision",
                   "country_code", "affected_countries", "geo_entity_id", "location_name", "lat", "lon",
                   "geo_precision", "geo_confidence", "actors", "sectors", "casualties_killed", "casualties_injured",
                   "casualty_note", "source_count", "independent_source_count", "official_statement_count",
                   "uncertainty_notes"]


# ---------------------------------------------------------------------------
# Severity (deterministic, documented)
# ---------------------------------------------------------------------------
def compute_severity(categories: list[str], taxonomy: Taxonomy, killed: int | None, injured: int | None,
                     geo_entity_id: str | None, geo_kind: str | None) -> tuple[int, str]:
    reasons: list[str] = []
    base = 1
    base_cat = None
    for c in categories:
        et = taxonomy.event_types.get(c)
        if et and et.default_severity > base:
            base, base_cat = et.default_severity, et.label
    sev = base
    reasons.append(f"base {base}" + (f" ({base_cat})" if base_cat else ""))
    if killed:
        floor = 5 if killed >= 50 else 4 if killed >= 10 else 3
        if floor > sev:
            reasons.append(f"raised to {floor}: {killed} reported killed")
            sev = floor
    if injured and injured >= 10 and sev < 3:
        reasons.append(f"raised to 3: {injured} reported injured")
        sev = 3
    cats = set(categories)
    if geo_kind in ("facility", "port") and cats & INFRA_CATEGORIES and sev < 5:
        sev += 1
        reasons.append(f"+1 critical infrastructure location ({geo_kind})")
    if geo_entity_id in CHOKEPOINTS and cats & {"maritime_incident", "shipping_disruption", "drone_missile"} and sev < 5:
        sev += 1
        reasons.append("+1 strategic maritime chokepoint")
    if killed == 0 and (injured or 0) == 0:
        reasons.append("no casualties reported")
    return max(1, min(5, sev)), "; ".join(reasons)


def review_decision(severity: int, status: str, categories: list[str], taxonomy: Taxonomy, policy: dict,
                    extra_reasons: list[str]) -> list[str]:
    reasons = list(extra_reasons)
    if severity >= int(policy.get("incident_min_severity", 4)):
        reasons.append(f"severity {severity} requires analyst review")
    if status in ("single_source", "unverified", "disputed") and severity >= int(policy.get("incident_unverified_min_severity", 3)):
        reasons.append(f"{status.replace('_', ' ')} report at severity {severity}")
    for c in categories:
        et = taxonomy.event_types.get(c)
        if et and et.review_required:
            reasons.append(f"category '{et.label}' always requires review")
    return sorted(set(reasons))


# ---------------------------------------------------------------------------
# Matching candidate -> existing incident
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _ancestor_ids(gazetteer: Gazetteer, entity_id: str | None) -> set[str]:
    if not entity_id or entity_id not in gazetteer.entities:
        return set()
    e = gazetteer.entities[entity_id]
    return {entity_id, *(a.id for a in gazetteer.ancestors(e))}


def match_score(ex: Extraction, inc: dict, gazetteer: Gazetteer, ex_time: datetime | None) -> tuple[float, list[str]]:
    reasons: list[str] = []
    a, b = set(ex.categories), set(inc["categories"] or [])
    cat = 0.0
    if a and b:
        if ex.primary_category == inc["primary_category"]:
            cat = 0.35
        else:
            cat = 0.35 * len(a & b) / len(a | b)
    if cat:
        reasons.append(f"category {cat:.2f}")
    loc = 0.0
    ex_ent, inc_ent = ex.location.get("entity_id"), inc["geo_entity_id"]
    if ex_ent and inc_ent:
        if ex_ent == inc_ent:
            loc = 0.30
        elif ex_ent in _ancestor_ids(gazetteer, inc_ent) or inc_ent in _ancestor_ids(gazetteer, ex_ent):
            loc = 0.25 if not (ex_ent.startswith("country:") or inc_ent.startswith("country:")) else 0.15
    if not loc and ex.location.get("lat") is not None and inc["lat"] is not None:
        # Proximity of resolved points (gazetteer or source-reported), allowing for their precision.
        d = haversine_km(ex.location["lat"], ex.location["lon"], inc["lat"], inc["lon"])
        if d <= 75:
            loc = 0.25
        elif d <= 250:
            loc = 0.15
    if not loc and ex.location.get("country_code") and ex.location.get("country_code") == inc["country_code"]:
        loc = 0.12
    if loc:
        reasons.append(f"location {loc:.2f}")
    t_ex = ex.title_en or ex.title
    sim = jaccard(t_ex, inc["title"]) if t_ex and inc["title"] else 0.0
    title_score = 0.20 * min(1.0, sim * 2.5)
    if title_score:
        reasons.append(f"headline {title_score:.2f}")
    actor = 0.15 if set(ex.actors) & set(inc["actors"] or []) else 0.0
    if actor:
        reasons.append("actor overlap 0.15")
    bonus = 0.0
    ref_time = inc["last_reported_at"] or inc["created_at"]
    if ex_time and ref_time and abs(ex_time - ref_time) <= timedelta(hours=24):
        bonus = 0.05
    if ex.stance in ("denial", "correction", "retraction") and loc >= 0.12 and cat > 0:
        bonus += 0.1  # denials/corrections reuse fewer of the original keywords
        reasons.append("denial/correction bonus 0.10")
    return round(cat + loc + title_score + actor + bonus, 3), reasons


def find_match(conn: psycopg.Connection, tenant_id: str, ex: Extraction, ex_time: datetime | None,
               gazetteer: Gazetteer, window_hours: int, threshold: float) -> tuple[dict | None, float, list[str]]:
    ts = ex_time or datetime.now().astimezone()
    rows = fetch_all(conn, """
        select * from incidents
        where tenant_id = %s and status in ('active', 'closed')
          and coalesce(last_reported_at, created_at) >= %s and coalesce(first_reported_at, created_at) <= %s
          and (categories && %s::text[] or country_code = %s or geo_entity_id = %s)
        order by last_reported_at desc nulls last limit 200""",
                     (tenant_id, ts - timedelta(hours=window_hours), ts + timedelta(hours=window_hours),
                      ex.categories or ["__none__"], ex.location.get("country_code"), ex.location.get("entity_id")))
    best, best_score, best_reasons = None, 0.0, []
    for inc in rows:
        score, reasons = match_score(ex, inc, gazetteer, ex_time)
        if score > best_score:
            best, best_score, best_reasons = inc, score, reasons
    if best is not None and best_score >= threshold:
        return best, best_score, best_reasons
    return None, best_score, best_reasons


# ---------------------------------------------------------------------------
# Create / attach / recompute
# ---------------------------------------------------------------------------
def _snapshot(inc: dict) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in inc.items() if k in SNAPSHOT_FIELDS}


def write_revision(conn: psycopg.Connection, inc: dict, change_type: str, note: str | None,
                   changed_fields: list[str], agent: str = AGENT, user_id: str | None = None) -> None:
    execute(conn, """
        insert into incident_revisions(tenant_id, incident_id, version, change_type, change_note, changed_fields,
                                       snapshot, changed_by, changed_by_agent)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (inc["tenant_id"], inc["id"], inc["version"], change_type, note, changed_fields, jsonb(_snapshot(inc)),
             user_id, None if user_id else agent))


def create_incident(conn: psycopg.Connection, tenant_id: str, ex: Extraction, article: dict, is_demo: bool) -> dict:
    n = fetch_one(conn, "select app.next_ref(%s, 'INC') as n", (tenant_id,))["n"]
    loc = ex.location
    inc = fetch_one(conn, """
        insert into incidents(tenant_id, ref, title, primary_category, categories, occurred_at, occurred_at_precision,
          first_reported_at, last_reported_at, country_code, affected_countries, geo_entity_id, location_name, lat, lon,
          geo_precision, geo_confidence, geo_source, actors, sectors, gcc_relevance, is_demo, created_by_agent, version)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
        returning *""",
                     (tenant_id, f"INC-{n:06d}", (ex.title_en or ex.title)[:300], ex.primary_category, ex.categories,
                      ex.occurred_at, ex.occurred_precision, article["published_at"] or article["retrieved_at"],
                      article["published_at"] or article["retrieved_at"], loc.get("country_code"),
                      loc.get("affected_countries") or [], loc.get("entity_id"), loc.get("name"), loc.get("lat"),
                      loc.get("lon"), loc.get("precision") or "unknown", loc.get("confidence"),
                      loc.get("source") if loc.get("lat") is not None else None, ex.actors, ex.sectors, ex.relevance,
                      is_demo, AGENT))
    return inc


def origin_key_for(article: dict, source: dict) -> str:
    """Independence key: wire origin > syndication parent origin > source origin group > source id.
    Normally computed at ingestion (articles.meta.origin_key); this is the fallback."""
    if (article.get("meta") or {}).get("origin_key"):
        return article["meta"]["origin_key"]
    if source.get("adapter") == "gdelt" and (article.get("meta") or {}).get("publisher_domain"):
        return "domain:" + article["meta"]["publisher_domain"]
    if article.get("attributed_origin"):
        return "origin:" + article["attributed_origin"]
    return "origin:" + (source.get("origin_group") or str(source["id"]))


def attach_article(conn: psycopg.Connection, inc: dict, article: dict, origin_key: str, role: str,
                   score: float | None) -> bool:
    return bool(fetch_one(conn, """
        insert into incident_articles(incident_id, article_id, tenant_id, role, origin_key, match_score, added_by)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (incident_id, article_id) do nothing returning incident_id""",
                          (inc["id"], article["id"], inc["tenant_id"], role, origin_key, score, AGENT)))


def role_for(ex: Extraction, article: dict, source: dict, is_new_incident: bool) -> str:
    if ex.stance == "denial":
        return "contradicting"
    if ex.stance == "correction":
        return "correction"
    if ex.stance == "retraction":
        return "retraction"
    if article.get("syndicated_from_article_id"):
        return "syndicated"
    if source.get("source_type") == "government" or ex.stance == "official_statement":
        return "official_statement"
    return "original" if is_new_incident else "follow_up"


def casualty_phrase(killed: int | None, injured: int | None) -> str | None:
    """Only figures that were actually reported; an unstated number is never shown as zero."""
    if killed is None and injured is None:
        return None
    if (killed or 0) == 0 and (injured or 0) == 0:
        return "No casualties reported."
    bits = []
    if killed is not None:
        bits.append(f"{killed} killed")
    if injured is not None:
        bits.append(f"{injured} injured")
    return "Reported casualties: " + ", ".join(bits) + " (as reported; see attribution)."


def compose_summary(inc: dict, taxonomy: Taxonomy, ver: verification.VerificationResult) -> str:
    label = ", ".join(taxonomy.event_types[c].label for c in (inc["categories"] or []) if c in taxonomy.event_types) \
        or (inc["primary_category"] or "Event")
    where = inc["location_name"] or COUNTRY_NAMES.get(inc["country_code"] or "", "an unresolved location")
    country = COUNTRY_NAMES.get(inc["country_code"] or "")
    loc = f"{where}{', ' + country if country and country not in where else ''}"
    parts = [f"{label} reported at/near {loc}."]
    cas = casualty_phrase(inc["casualties_killed"], inc["casualties_injured"])
    if cas:
        parts.append(cas)
    parts.append(f"Evidence: {ver.source_count} report(s) from {ver.independent_count} independent origin(s); "
                 f"status {ver.status.replace('_', ' ')}.")
    return " ".join(parts)


def recompute(conn: psycopg.Connection, incident_id: str, *, taxonomy: Taxonomy, gazetteer: Gazetteer,
              policy: dict, verification_policy: dict, change_type: str, change_note: str | None,
              location_override: dict | None = None, casualty_override: tuple[int | None, int | None] | None = None,
              force_review_reasons: list[str] | None = None, title_override: str | None = None,
              changed_by: str | None = None) -> dict:
    """Recompute aggregate fields, verification and review state; write a revision if anything changed."""
    inc = fetch_one(conn, "select * from incidents where id = %s for update", (incident_id,))
    before = _snapshot(inc)
    ev_rows = fetch_all(conn, """
        select ia.article_id, ia.role, ia.origin_key, a.stance, a.injection_flags, a.published_at, a.retrieved_at,
               a.is_original, s.reliability, s.source_type, s.publisher
        from incident_articles ia join articles a on a.id = ia.article_id join sources s on s.id = a.source_id
        where ia.incident_id = %s""", (incident_id,))
    evidence = [verification.Evidence(str(r["article_id"]), r["origin_key"], r["role"], r["stance"], r["reliability"],
                                      r["source_type"], r["publisher"], r["is_original"], tuple(r["injection_flags"] or ()))
                for r in ev_rows]
    detail = dict(inc["verification_detail"] or {})
    analyst_confirmed = bool(detail.get("analyst_confirmed"))
    # Corrections and analyst edits persist as overrides so later recomputes do not undo them.
    overrides = dict(detail.get("overrides") or {})
    if location_override and location_override.get("entity_id"):
        overrides["location"] = location_override
    if title_override:
        overrides["title"] = title_override
    if casualty_override is not None:
        overrides["casualties"] = list(casualty_override)
        overrides["casualty_note"] = "Casualty figures as corrected by the publisher."
    ver = verification.assess(evidence, min_independent=int(verification_policy.get("corroboration_min_independent", 2)),
                              analyst_confirmed=analyst_confirmed,
                              auto_confirm=not verification_policy.get("confirmed_requires_analyst", True))

    cand_rows = fetch_all(conn, """
        select c.extraction from incident_candidates c
        join incident_articles ia on ia.article_id = c.article_id and ia.incident_id = c.matched_incident_id
        where c.matched_incident_id = %s and ia.role in ('original', 'follow_up', 'syndicated', 'official_statement', 'correction')
        order by c.created_at""", (incident_id,))
    cat_scores: dict[str, float] = {}
    killed_vals, injured_vals, actors, sectors, affected, notes = [], [], [], [], [], []
    for row in cand_rows:
        ex = row["extraction"]
        for k, v in (ex.get("category_scores") or {}).items():
            cat_scores[k] = max(cat_scores.get(k, 0), v)
        if ex.get("casualties_killed") is not None:
            killed_vals.append(ex["casualties_killed"])
        if ex.get("casualties_injured") is not None:
            injured_vals.append(ex["casualties_injured"])
        actors += [a for a in ex.get("actors", []) if a not in actors]
        sectors += [s for s in ex.get("sectors", []) if s not in sectors]
        affected += [c for c in (ex.get("location") or {}).get("affected_countries", []) if c not in affected]
        for n in ex.get("uncertainty_notes", []):
            if n not in notes and not n.startswith("Location from gazetteer"):
                notes.append(n)
    categories = [k for k, _ in sorted(cat_scores.items(), key=lambda kv: kv[1], reverse=True)] or inc["categories"]
    killed = max(killed_vals) if killed_vals else inc["casualties_killed"]
    injured = max(injured_vals) if injured_vals else inc["casualties_injured"]
    casualty_note = inc["casualty_note"]
    if killed_vals and len(set(killed_vals)) > 1:
        casualty_note = f"Reports differ on fatalities ({min(killed_vals)}-{max(killed_vals)}); highest reported figure shown."
    if overrides.get("casualties") is not None:
        killed, injured = overrides["casualties"]
        casualty_note = overrides.get("casualty_note") or "Casualty figures overridden."
    if overrides.get("categories"):
        categories = list(overrides["categories"])

    updates: dict[str, Any] = {}
    loc_o = overrides.get("location")
    if loc_o and loc_o.get("entity_id"):
        updates.update({
            "geo_entity_id": loc_o["entity_id"], "location_name": loc_o.get("name"),
            "lat": loc_o.get("lat"), "lon": loc_o.get("lon"),
            "geo_precision": loc_o.get("precision") or "unknown",
            "geo_confidence": loc_o.get("confidence"),
            "geo_source": loc_o.get("source") if loc_o.get("lat") is not None else None,
            "country_code": loc_o.get("country_code") or inc["country_code"],
        })
    geo_entity = updates.get("geo_entity_id", inc["geo_entity_id"])
    geo_kind = gazetteer.entities[geo_entity].kind if geo_entity in gazetteer.entities else None
    severity, rationale = compute_severity(categories, taxonomy, killed, injured, geo_entity, geo_kind)
    if overrides.get("severity"):
        rationale = f"analyst override to {overrides['severity']} ({overrides.get('severity_reason') or 'no reason given'}); computed: {severity} - {rationale}"
        severity = int(overrides["severity"])

    times = [r["published_at"] or r["retrieved_at"] for r in ev_rows]
    review_reasons = review_decision(severity, ver.status, categories, taxonomy, policy,
                                     ver.needs_review_reasons + (force_review_reasons or []))
    # Review state machine: analyst decisions stick unless the incident changes materially.
    review_status = inc["review_status"]
    material = (inc["verification_status"] != ver.status or severity > inc["severity"]
                or change_type in ("correction", "retraction"))
    if review_reasons:
        if review_status == "not_required" or (review_status == "approved" and material):
            review_status = "pending"

    confidence = "low"
    geo_conf = updates.get("geo_confidence", inc["geo_confidence"]) or 0
    if ver.status in ("confirmed", "corroborated") and geo_conf >= 0.5:
        confidence = "high" if ver.independent_count >= 3 or ver.status == "confirmed" else "moderate"
    elif ver.status == "single_source" and ver.credibility == 3:
        confidence = "moderate"

    all_notes = ver.notes + notes
    updates.update({
        "categories": categories, "primary_category": categories[0] if categories else inc["primary_category"],
        "casualties_killed": killed, "casualties_injured": injured, "casualty_note": casualty_note,
        "actors": actors or inc["actors"], "sectors": sectors or inc["sectors"],
        "affected_countries": sorted(set(affected) | set(inc["affected_countries"] or [])),
        "severity": severity, "severity_rationale": rationale, "verification_status": ver.status,
        "credibility": ver.credibility, "source_count": ver.source_count,
        "independent_source_count": ver.independent_count, "official_statement_count": len(ver.official_origins),
        "first_reported_at": min(times) if times else inc["first_reported_at"],
        "last_reported_at": max(times) if times else inc["last_reported_at"],
        "uncertainty_notes": "\n".join(dict.fromkeys(all_notes)) or None,
        "verification_detail": jsonb({**ver.to_json(), "analyst_confirmed": analyst_confirmed, "overrides": overrides,
                                      **{k: v for k, v in detail.items() if k in ("confirmed_by", "confirmed_at")}}),
        "review_status": review_status, "review_reason": "; ".join(review_reasons) or None, "confidence": confidence,
    })
    if overrides.get("title"):
        updates["title"] = overrides["title"][:300]
    tmp = {**inc, **{k: (v.obj if hasattr(v, "obj") else v) for k, v in updates.items()}}
    tmp["summary"] = overrides.get("summary") or compose_summary(tmp, taxonomy, ver)
    updates["summary"] = tmp["summary"]
    after = _snapshot(tmp)
    changed = sorted(k for k in after if before.get(k) != after.get(k))
    if not changed and change_type != "created":
        return inc
    version = inc["version"] + (0 if change_type == "created" else 1)
    updates["version"] = version
    cols = ", ".join(f"{k} = %s" for k in updates)
    inc = fetch_one(conn, f"update incidents set {cols} where id = %s returning *", (*updates.values(), incident_id))
    write_revision(conn, inc, change_type, change_note, changed, user_id=changed_by)
    if review_status == "pending" and review_reasons:
        ensure_review(conn, inc["tenant_id"], "incident", inc["id"], "; ".join(review_reasons),
                      1 if severity >= 4 else 2 if severity == 3 else 3, "verification-agent")
    return inc


def ensure_review(conn: psycopg.Connection, tenant_id: str, item_type: str, item_id: str, reason: str,
                  priority: int, agent: str) -> None:
    """One open review item per object; its reason/priority is refreshed as evidence changes."""
    updated = execute(conn, """
        update analyst_reviews set reason = %s, priority = least(priority, %s)
        where item_type = %s and item_id = %s and status = 'open'""", (reason, priority, item_type, item_id))
    if not updated:
        execute(conn, """
            insert into analyst_reviews(tenant_id, item_type, item_id, reason, priority, created_by_agent)
            values (%s, %s, %s, %s, %s, %s)""", (tenant_id, item_type, item_id, reason, priority, agent))
