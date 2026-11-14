"""Pipeline orchestration for one tenant:

  due sources -> fetch (B) -> store/dedupe/syndication (B) -> translate (C) -> extract (D)
  -> match/create incident + evidence links -> verification & severity & review policy (E)
  -> regional context (F) -> alert rules (I) -> delivery

Each article is processed in its own transaction so one bad item cannot poison a run.
Risk (G), briefs (H) and quality audits (J) run on their own schedules (see scheduler.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import psycopg

from .adapters.http import PoliteFetcher
from .agents import alerts as alert_agent
from .agents import context as context_agent
from .agents import incidents as inc_svc
from .agents.extraction import Extraction, llm_enrich, rule_extract
from .agents.ingestion import due_sources, ingest_source
from .agents.translation import translate_article
from .config import Settings, get_settings
from .db import audit, execute, fetch_all, fetch_one, jsonb
from .llm.service import LLMService, build_provider
from .log import get_logger
from .refdata import gazetteer_from_db, taxonomy_from_db
from .seed import DEFAULT_TENANT_SETTINGS
from .text.gazetteer import Gazetteer
from .text.taxonomy import Taxonomy

log = get_logger(__name__)


@dataclass
class RunContext:
    conn: psycopg.Connection
    tenant_id: str
    settings: Settings
    gazetteer: Gazetteer
    taxonomy: Taxonomy
    llm: LLMService
    tenant_settings: dict
    channel_overrides: dict | None = None
    stats: dict = field(default_factory=lambda: {
        "sources": 0, "source_errors": 0, "articles_new": 0, "articles_processed": 0, "irrelevant": 0,
        "incidents_created": 0, "incidents_updated": 0, "discarded": 0, "errors": 0, "alerts_created": 0,
        "alerts_pending_review": 0, "alerts_delivered": 0, "translated": 0})

    @property
    def review_policy(self) -> dict:
        return {**DEFAULT_TENANT_SETTINGS["review_policy"], **self.tenant_settings.get("review_policy", {})}

    @property
    def verification_policy(self) -> dict:
        return {**DEFAULT_TENANT_SETTINGS["verification_policy"], **self.tenant_settings.get("verification_policy", {})}

    @property
    def matching(self) -> dict:
        return {**DEFAULT_TENANT_SETTINGS["matching"], **self.tenant_settings.get("matching", {})}


def make_context(conn: psycopg.Connection, tenant_id: str, settings: Settings | None = None,
                 provider=None, channel_overrides: dict | None = None) -> RunContext:
    settings = settings or get_settings()
    tenant = fetch_one(conn, "select * from tenants where id = %s", (tenant_id,))
    if tenant is None:
        raise ValueError(f"tenant {tenant_id} not found")
    prov = provider if provider is not None else build_provider(settings)
    return RunContext(conn=conn, tenant_id=str(tenant_id), settings=settings, gazetteer=gazetteer_from_db(conn),
                      taxonomy=taxonomy_from_db(conn, tenant_id), llm=LLMService(conn, str(tenant_id), prov, settings),
                      tenant_settings=tenant["settings"] or {}, channel_overrides=channel_overrides)


def correction_title(ex: Extraction) -> str | None:
    """Headline of a location correction without its 'CORRECTION:' marker, so the incident title
    stops repeating the wrong place. The original headline stays in the revision history."""
    import re  # noqa: PLC0415
    t = re.sub(r"^\s*(\[[^\]]*\]\s*)?(correction|corrected|clarification|تصحيح)\s*[:\-]\s*", "", ex.title_en or ex.title,
               flags=re.I).strip()
    return t[:300] if len(t) >= 3 else None


def _store_claims(ctx: RunContext, article: dict, ex: Extraction, incident_id: str | None) -> None:
    for c in ex.claims[:12]:
        execute(ctx.conn, """
            insert into claims(tenant_id, article_id, incident_id, claim_type, text, attributed_to, is_official, stance,
                               numeric_value, numeric_unit, confidence, extracted_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (ctx.tenant_id, article["id"], incident_id, c.claim_type, c.text[:600], c.attributed_to, c.is_official,
                 c.stance, c.numeric_value, c.numeric_unit, c.confidence, ex.extractor))


def process_article(ctx: RunContext, article_id: str) -> dict:
    conn = ctx.conn
    art = fetch_one(conn, """
        select a.*, s.source_type, s.reliability, s.origin_group, s.adapter, s.config as source_config,
               s.max_excerpt_chars, s.name as source_name
        from articles a join sources s on s.id = a.source_id where a.id = %s and a.tenant_id = %s""",
                    (article_id, ctx.tenant_id))
    if art is None or art["status"] != "new":
        return {"article_id": article_id, "decision": "skipped"}
    if (art["source_config"] or {}).get("content_kind") == "travel_advisory":
        # Government travel advice is context for a country, not an incident: it stays with its source
        # and is shown on country pages.
        execute(conn, "update articles set status = 'processed', relevance = null where id = %s", (art["id"],))
        execute(conn, """insert into incident_candidates(tenant_id, article_id, extractor, extraction, decision, decision_reason)
                         values (%s, %s, 'rules', '{}'::jsonb, 'discarded', %s)""",
                (ctx.tenant_id, art["id"], "official travel advisory: shown on country pages, not an incident"))
        return {"article_id": str(art["id"]), "decision": "advisory"}
    tz = (art["source_config"] or {}).get("timezone") or "Asia/Riyadh"
    if translate_article(conn, art, ctx.llm, int(art["max_excerpt_chars"])):
        ctx.stats["translated"] += 1
    ex = rule_extract(title=art["title"], excerpt=art["excerpt"] or "", published_at=art["published_at"] or art["retrieved_at"],
                      language=art["language"], source_type=art["source_type"], gazetteer=ctx.gazetteer,
                      taxonomy=ctx.taxonomy, tz_name=tz)
    if ctx.llm.available:
        ex = llm_enrich(ex, title=art["title"], excerpt=art["excerpt"] or "", language=art["language"], llm=ctx.llm,
                        gazetteer=ctx.gazetteer, taxonomy=ctx.taxonomy)
    if art.get("title_en") and not ex.title_en:
        ex.title_en = art["title_en"]
    execute(conn, """update articles set stance = %s, relevance = %s, title_en = coalesce(title_en, %s),
                     attributed_origin = coalesce(attributed_origin, %s),
                     injection_flags = (select array(select distinct unnest(injection_flags || %s::text[])))
                     where id = %s""",
            (ex.stance if ex.stance in ("report", "denial", "correction", "retraction", "official_statement") else "report",
             ex.relevance, ex.title_en, ex.attributed_origin, ex.injection_flags, art["id"]))

    if not ex.is_relevant:
        execute(conn, "update articles set status = 'irrelevant' where id = %s", (art["id"],))
        execute(conn, """insert into incident_candidates(tenant_id, article_id, extractor, extraction, decision, decision_reason)
                         values (%s, %s, %s, %s, 'discarded', %s)""",
                (ctx.tenant_id, art["id"], ex.extractor, jsonb(ex.to_json()),
                 f"not relevant ({ex.relevance:.2f}): {ex.relevance_reason}" if ex.categories else "no event type detected"))
        ctx.stats["irrelevant"] += 1
        return {"article_id": str(art["id"]), "decision": "irrelevant"}

    t_ex = art["published_at"] or art["retrieved_at"]
    m = ctx.matching
    match, score, reasons = inc_svc.find_match(conn, ctx.tenant_id, ex, t_ex, ctx.gazetteer, int(m["window_hours"]),
                                               float(m["threshold"]))
    if match is None and ex.stance in ("denial", "correction", "retraction"):
        execute(conn, "update articles set status = 'processed' where id = %s", (art["id"],))
        execute(conn, """insert into incident_candidates(tenant_id, article_id, extractor, extraction, match_score, decision, decision_reason)
                         values (%s, %s, %s, %s, %s, 'discarded', %s)""",
                (ctx.tenant_id, art["id"], ex.extractor, jsonb(ex.to_json()), score,
                 f"{ex.stance} with no matching incident (best score {score:.2f})"))
        _store_claims(ctx, art, ex, None)
        ctx.stats["discarded"] += 1
        return {"article_id": str(art["id"]), "decision": "discarded"}

    source = {"id": art["source_id"], "source_type": art["source_type"], "origin_group": art["origin_group"],
              "adapter": art["adapter"]}
    okey = inc_svc.origin_key_for(art, source)
    if match is None:
        inc = inc_svc.create_incident(conn, ctx.tenant_id, ex, art, bool(art["is_demo"]))
        role, change_type, decision = inc_svc.role_for(ex, art, source, True), "created", "created"
        prev_version = 0
    else:
        inc = match
        role = inc_svc.role_for(ex, art, source, False)
        change_type = {"correction": "correction", "retraction": "retraction"}.get(ex.stance, "evidence_added")
        decision = "attached"
        prev_version = inc["version"]
    inc_svc.attach_article(conn, inc, art, okey, role, score if match else None)
    execute(conn, """insert into incident_candidates(tenant_id, article_id, extractor, extraction, matched_incident_id,
                                                     match_score, decision, decision_reason)
                     values (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (ctx.tenant_id, art["id"], ex.extractor, jsonb(ex.to_json()), inc["id"], score if match else None, decision,
             ("match: " + ", ".join(reasons)) if match else "no existing incident above threshold"))
    _store_claims(ctx, art, ex, str(inc["id"]))
    if role in ("contradicting", "correction", "retraction") and match:
        original = fetch_one(conn, """select article_id from incident_articles where incident_id = %s and role = 'original'
                                      order by added_at limit 1""", (inc["id"],))
        if original:
            rel = {"contradicting": "contradicts", "correction": "corrects", "retraction": "retracts"}[role]
            execute(conn, """insert into claim_provenance(tenant_id, article_id, relation, target_article_id)
                             values (%s, %s, %s, %s)""", (ctx.tenant_id, art["id"], rel, original["article_id"]))

    loc_override = ex.location if (change_type == "correction" and ex.location.get("entity_id")
                                   and ex.location.get("entity_id") != inc["geo_entity_id"]) else None
    cas_override = None
    if change_type == "correction" and (ex.casualties_killed is not None or ex.casualties_injured is not None):
        cas_override = (ex.casualties_killed, ex.casualties_injured)
    force = []
    if ex.injection_flags:
        force.append("Evidence contains instruction-like text (possible prompt injection).")
    if change_type in ("correction", "retraction"):
        force.append(f"Publisher {ex.stance} received.")
    updated = inc_svc.recompute(conn, inc["id"], taxonomy=ctx.taxonomy, gazetteer=ctx.gazetteer,
                                policy=ctx.review_policy, verification_policy=ctx.verification_policy,
                                change_type=change_type, change_note=f"{ex.stance} from {art['source_name']}",
                                location_override=loc_override, casualty_override=cas_override,
                                force_review_reasons=force,
                                title_override=correction_title(ex) if loc_override else None)
    execute(conn, "update articles set status = 'processed' where id = %s", (art["id"],))
    updated["context"] = context_agent.apply_context(conn, updated, ctx.gazetteer)
    changed = []
    if updated["version"] != prev_version:
        rev = fetch_one(conn, "select changed_fields from incident_revisions where incident_id = %s and version = %s",
                        (updated["id"], updated["version"]))
        changed = rev["changed_fields"] if rev else []
    if decision == "created":
        ctx.stats["incidents_created"] += 1
    elif updated["version"] != prev_version:
        ctx.stats["incidents_updated"] += 1
    created_alerts = []
    if decision == "created" or updated["version"] != prev_version:
        created_alerts = alert_agent.evaluate_incident(conn, updated, change_type, changed, ctx.review_policy,
                                                       ctx.gazetteer, ctx.settings)
        ctx.stats["alerts_created"] += len(created_alerts)
        ctx.stats["alerts_pending_review"] += sum(1 for a in created_alerts if a["status"] == "pending_review")
    return {"article_id": str(art["id"]), "decision": decision, "incident_id": str(updated["id"]), "ref": updated["ref"],
            "role": role, "change_type": change_type, "version": updated["version"],
            "verification": updated["verification_status"], "severity": updated["severity"],
            "alerts": [str(a["id"]) for a in created_alerts]}


def apply_analyst_edit(ctx: RunContext, incident_id: str, note: str | None, user_id: str | None) -> dict:
    """Re-derive an incident after an analyst stored overrides (verification_detail.overrides).

    Uses the same recompute path as publisher corrections, so severity, review policy, context and
    correction alerts stay consistent whichever way a correction arrives.
    """
    conn = ctx.conn
    inc = fetch_one(conn, "select id, version from incidents where id = %s and tenant_id = %s", (incident_id, ctx.tenant_id))
    if inc is None:
        raise ValueError("incident not found for this tenant")
    prev_version = inc["version"]
    updated = inc_svc.recompute(conn, incident_id, taxonomy=ctx.taxonomy, gazetteer=ctx.gazetteer,
                                policy=ctx.review_policy, verification_policy=ctx.verification_policy,
                                change_type="correction", change_note=f"Analyst correction: {note or 'no note'}"[:500],
                                force_review_reasons=["Analyst correction applied; confirm before external use."],
                                changed_by=user_id)
    updated["context"] = context_agent.apply_context(conn, updated, ctx.gazetteer)
    alerts: list[dict] = []
    changed: list[str] = []
    if updated["version"] != prev_version:
        rev = fetch_one(conn, "select changed_fields from incident_revisions where incident_id = %s and version = %s",
                        (updated["id"], updated["version"]))
        changed = rev["changed_fields"] if rev else []
        alerts = alert_agent.evaluate_incident(conn, updated, "correction", changed, ctx.review_policy,
                                               ctx.gazetteer, ctx.settings)
    return {"incident_id": str(updated["id"]), "ref": updated["ref"], "version": updated["version"],
            "changed_fields": changed, "alerts": [str(a["id"]) for a in alerts]}


def process_new_articles(ctx: RunContext, article_ids: list[str] | None = None, limit: int = 500) -> list[dict]:
    if article_ids is None:
        rows = fetch_all(ctx.conn, """select id from articles where tenant_id = %s and status = 'new'
                                      order by coalesce(published_at, retrieved_at) limit %s""", (ctx.tenant_id, limit))
        article_ids = [str(r["id"]) for r in rows]
    else:
        rows = fetch_all(ctx.conn, """select id from articles where id = any(%s::uuid[])
                                      order by coalesce(published_at, retrieved_at)""", (article_ids,))
        article_ids = [str(r["id"]) for r in rows]
    results = []
    for aid in article_ids:
        try:
            with ctx.conn.transaction():
                results.append(process_article(ctx, aid))
            ctx.stats["articles_processed"] += 1
        except Exception as exc:
            log.exception("article processing failed", extra={"article_id": aid})
            ctx.stats["errors"] += 1
            with ctx.conn.transaction():
                execute(ctx.conn, "update articles set status = 'error', processing_error = %s where id = %s",
                        (f"{type(exc).__name__}: {exc}"[:500], aid))
            results.append({"article_id": aid, "decision": "error", "error": str(exc)[:200]})
    return results


def run_pipeline(conn: psycopg.Connection, tenant_id: str, *, settings: Settings | None = None, provider=None,
                 force: bool = False, source_ids: list[str] | None = None, deliver: bool = True,
                 fetcher: PoliteFetcher | None = None, channel_overrides: dict | None = None) -> dict:
    t0 = time.monotonic()
    ctx = make_context(conn, tenant_id, settings, provider, channel_overrides)
    own_fetcher = fetcher is None
    fetcher = fetcher or PoliteFetcher(ctx.settings)
    new_ids: list[str] = []
    fetch_results = []
    try:
        for src in due_sources(conn, ctx.tenant_id, force=force, source_ids=source_ids):
            with conn.transaction():
                r = ingest_source(conn, src, fetcher)
            fetch_results.append({k: v for k, v in r.items() if k != "article_ids"})
            ctx.stats["sources"] += 1
            ctx.stats["source_errors"] += 1 if r["error"] else 0
            new_ids += r["article_ids"]
    finally:
        if own_fetcher:
            fetcher.close()
    ctx.stats["articles_new"] = len(new_ids)
    results = process_new_articles(ctx)
    delivered = []
    if deliver:
        with conn.transaction():
            delivered = alert_agent.deliver_pending(conn, ctx.tenant_id, ctx.settings, ctx.channel_overrides)
        ctx.stats["alerts_delivered"] = sum(1 for d in delivered if d.get("status") in ("sent", "partially_sent"))
    ctx.stats["llm_calls"] = ctx.llm.calls
    ctx.stats["llm_cost_usd"] = round(ctx.llm.cost, 4)
    ctx.stats["seconds"] = round(time.monotonic() - t0, 2)
    with conn.transaction():
        audit(conn, tenant_id=ctx.tenant_id, action="pipeline.run", entity_type="tenant", entity_id=ctx.tenant_id,
              agent="pipeline", details=ctx.stats)
    log.info("pipeline run complete", extra={"tenant_id": ctx.tenant_id, **ctx.stats})
    return {"stats": ctx.stats, "fetch": fetch_results, "articles": results, "deliveries": delivered}
