"""Quality Auditor Agent (J): checks the system's own output and opens review items.

Checks: incidents without evidence, low-confidence map placements, possible duplicate
incidents, reports failing citation integrity, failing sources, stale review queues, LLM
spend vs budget, prompt-injection flags, and (politely, sampled) broken evidence links.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from ..adapters.http import FetchBlocked, PoliteFetcher
from ..config import Settings, get_settings
from ..db import audit, execute, fetch_all, fetch_one
from .incidents import ensure_review

AGENT = "quality-auditor"


def audit_tenant(conn: psycopg.Connection, tenant_id: str, settings: Settings | None = None,
                 fetcher: PoliteFetcher | None = None, link_sample: int = 20) -> dict:
    settings = settings or get_settings()
    findings: list[dict] = []

    def add(kind: str, item_type: str, item_id, reason: str, priority: int = 3) -> None:
        findings.append({"kind": kind, "item_type": item_type, "item_id": str(item_id), "reason": reason})
        ensure_review(conn, tenant_id, "quality", item_id, f"[{kind}] {reason}", priority, AGENT)

    for r in fetch_all(conn, """select i.id, i.ref from incidents i where i.tenant_id = %s and i.status = 'active'
                                and not exists (select 1 from incident_articles ia where ia.incident_id = i.id)""", (tenant_id,)):
        add("no_evidence", "incident", r["id"], f"{r['ref']} has no linked evidence", 1)
    for r in fetch_all(conn, """select id, ref, geo_confidence from incidents where tenant_id = %s and status = 'active'
                                and lat is not null and geo_confidence < 0.3""", (tenant_id,)):
        add("low_geo_confidence", "incident", r["id"], f"{r['ref']} is mapped with geolocation confidence {r['geo_confidence']:.2f}")
    for r in fetch_all(conn, """
            select a.id, a.ref, b.ref as other_ref from incidents a join incidents b
              on a.tenant_id = b.tenant_id and a.id < b.id and a.primary_category = b.primary_category
             and a.geo_entity_id = b.geo_entity_id
             and abs(extract(epoch from (coalesce(a.first_reported_at, a.created_at) - coalesce(b.first_reported_at, b.created_at)))) < 172800
            where a.tenant_id = %s and a.status = 'active' and b.status = 'active' limit 50""", (tenant_id,)):
        add("possible_duplicate", "incident", r["id"], f"{r['ref']} may duplicate {r['other_ref']} (same type and place within 48h)")
    for r in fetch_all(conn, """
            select r.id, r.ref from reports r join report_versions v on v.report_id = r.id and v.version = r.current_version
            where r.tenant_id = %s and r.status <> 'archived' and not coalesce((v.citation_check ->> 'ok')::boolean, false)""",
                       (tenant_id,)):
        add("citation_integrity", "report", r["id"], f"{r['ref']} failed the citation integrity check", 1)
    for r in fetch_all(conn, """select id, name, consecutive_failures, last_error from sources
                                where tenant_id = %s and enabled and consecutive_failures >= 3""", (tenant_id,)):
        add("source_failing", "source", r["id"], f"{r['name']} failed {r['consecutive_failures']} times: {(r['last_error'] or '')[:120]}", 2)
    stale = fetch_one(conn, """select count(*) as n from analyst_reviews where tenant_id = %s and status = 'open'
                               and item_type in ('incident', 'alert') and created_at < now() - interval '2 hours'""", (tenant_id,))
    if stale["n"]:
        findings.append({"kind": "stale_reviews", "count": stale["n"],
                         "reason": f"{stale['n']} incident/alert review item(s) open for more than 2 hours"})
    spend = fetch_one(conn, """select coalesce(sum(cost_usd), 0) as usd from llm_usage
                               where tenant_id = %s and created_at >= date_trunc('day', now())""", (tenant_id,))
    if float(spend["usd"]) >= 0.8 * settings.llm_daily_budget_usd:
        findings.append({"kind": "llm_budget", "reason": f"LLM spend today ${float(spend['usd']):.2f} is >= 80% of the daily budget"})
    inj = fetch_one(conn, """select count(*) as n from articles where tenant_id = %s and injection_flags <> '{}'
                             and retrieved_at > now() - interval '1 day'""", (tenant_id,))
    if inj["n"]:
        findings.append({"kind": "prompt_injection", "count": inj["n"],
                         "reason": f"{inj['n']} article(s) in the last day contained instruction-like text (neutralised)"})

    checked = broken = 0
    if fetcher is not None and link_sample:
        rows = fetch_all(conn, """
            select distinct a.id, a.url from articles a join incident_articles ia on ia.article_id = a.id
            where a.tenant_id = %s and (a.link_checked_at is null or a.link_checked_at < now() - interval '7 days')
            order by a.id limit %s""", (tenant_id, link_sample))
        for r in rows:
            try:
                status, final_url = fetcher.head_status(r["url"])
            except FetchBlocked:
                execute(conn, "update articles set link_status = 'skipped', link_checked_at = now() where id = %s", (r["id"],))
                continue
            if status is None and final_url in ("robots", "private"):
                execute(conn, "update articles set link_status = 'skipped', link_checked_at = now() where id = %s", (r["id"],))
                continue
            checked += 1
            if status is None or status >= 400:
                broken += 1
                ls = "broken"
            elif final_url and final_url.rstrip("/") != r["url"].rstrip("/"):
                ls = "redirected"
            else:
                ls = "ok"
            execute(conn, "update articles set link_status = %s, link_checked_at = now() where id = %s", (ls, r["id"]))
        if broken:
            findings.append({"kind": "broken_links", "count": broken, "reason": f"{broken} of {checked} evidence links are broken"})
    summary = {"findings": findings, "links_checked": checked, "links_broken": broken,
               "audited_at": datetime.now(UTC).isoformat()}
    audit(conn, tenant_id=tenant_id, action="quality.audit", entity_type="tenant", entity_id=tenant_id, agent=AGENT,
          details={"findings": len(findings), "links_checked": checked, "links_broken": broken})
    return summary
