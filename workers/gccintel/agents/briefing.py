"""Briefing Writer Agent (H).

Builds cited intelligence reports exclusively from stored, evidence-linked incidents:
executive summary, key developments, country sections, maritime & energy, assessment,
uncertainty, watch indicators and a methodology note. Every factual sentence carries citation
numbers that resolve to stored articles; the citation integrity check runs before a version is
saved and publication is blocked in the database when it fails.

The deterministic template writer always works. When an LLM is configured it may rewrite the
executive summary and assessment from the same numbered evidence; its output is accepted only
if every paragraph cites valid evidence numbers and introduces no numbers absent from the
evidence, otherwise the template text is kept and the rejection is recorded.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from ..db import audit, fetch_all, fetch_one, jsonb
from ..llm.base import LLMError, LLMUnavailable
from ..llm.service import LLMService
from ..log import get_logger
from ..refdata import COUNTRY_NAMES, GCC_COUNTRIES, load_json
from ..text import injection
from ..text.taxonomy import Taxonomy
from .incidents import casualty_phrase

log = get_logger(__name__)

GENERATOR = "briefing-template-v1"
REPORT_TYPES: dict[str, tuple[str, timedelta]] = {
    "breaking": ("Breaking Brief", timedelta(hours=6)),
    "daily_gcc": ("Daily GCC Security Brief", timedelta(hours=24)),
    "country_daily": ("Country Daily Brief", timedelta(hours=24)),
    "weekly_outlook": ("Weekly Outlook", timedelta(days=7)),
    "monthly_assessment": ("Monthly Assessment", timedelta(days=30)),
    "maritime_energy": ("Maritime & Energy Brief", timedelta(days=7)),
    "custom": ("Custom Report", timedelta(days=7)),
}
MARITIME_ENERGY = ["maritime_incident", "shipping_disruption", "energy_disruption", "infrastructure_disruption",
                   "sanctions"]
VER_LABEL = {"confirmed": "confirmed", "corroborated": "corroborated", "single_source": "single-source",
             "disputed": "disputed", "unverified": "unverified"}
VER_RANK = {"confirmed": 0, "corroborated": 1, "single_source": 2, "disputed": 3, "unverified": 4}
ROLE_RANK = {"original": 0, "official_statement": 1, "follow_up": 2, "correction": 3, "contradicting": 4,
             "retraction": 5, "syndicated": 6}
CITE_RE = re.compile(r"\[(\d{1,3})\]")
NUM_RE = re.compile(r"(?<![\w\[])(\d+(?:[.,]\d+)?)(?![\w\]])")


class CitationBook:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self._by_article: dict[str, int] = {}

    def cite(self, ev: dict, incident: dict) -> int:
        aid = str(ev["article_id"])
        if aid in self._by_article:
            n = self._by_article[aid]
            if incident["ref"] not in self.items[n - 1]["incident_refs"]:
                self.items[n - 1]["incident_refs"].append(incident["ref"])
            return n
        n = len(self.items) + 1
        self._by_article[aid] = n
        self.items.append({
            "n": n, "article_id": aid, "url": ev["url"], "title": ev["title"], "title_en": ev["title_en"],
            "publisher": ev["publisher"], "reliability": ev["reliability"], "source_type": ev["source_type"],
            "role": ev["role"], "language": ev["language"],
            "published_at": ev["published_at"].isoformat() if ev["published_at"] else None,
            "retrieved_at": ev["retrieved_at"].isoformat() if ev["retrieved_at"] else None,
            "link_status": ev["link_status"], "incident_refs": [incident["ref"]],
            "incident_ids": [str(incident["id"])], "is_demo": ev["is_demo"],
        })
        return n


def select_incidents(conn: psycopg.Connection, tenant_id: str, start: datetime, end: datetime, *,
                     country: str | None = None, categories: list[str] | None = None) -> list[dict]:
    rows = fetch_all(conn, """
        select * from incidents
        where tenant_id = %s and status in ('active', 'closed') and review_status <> 'rejected'
          and coalesce(last_reported_at, created_at) >= %s and coalesce(first_reported_at, created_at) <= %s
          and verification_status <> 'unverified' and coalesce(gcc_relevance, 0) >= 0.45
          and (%s::text is null or country_code = %s or %s = any(affected_countries))
          and (%s::text[] is null or categories && %s::text[])""",
                     (tenant_id, start, end, country, country, country, categories, categories))
    rows.sort(key=lambda r: (-r["severity"], VER_RANK.get(r["verification_status"], 9),
                             -(r["last_reported_at"] or r["created_at"]).timestamp()))
    return rows


def evidence_for(conn: psycopg.Connection, incident_ids: list[str]) -> dict[str, list[dict]]:
    if not incident_ids:
        return {}
    rows = fetch_all(conn, """
        select ia.incident_id, ia.role, ia.origin_key, a.id as article_id, a.url, a.title, a.title_en, a.published_at,
               a.retrieved_at, a.language, a.link_status, a.is_demo, s.publisher, s.reliability, s.source_type
        from incident_articles ia join articles a on a.id = ia.article_id join sources s on s.id = a.source_id
        where ia.incident_id = any(%s::uuid[])""", (incident_ids,))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r["incident_id"]), []).append(r)
    return out


def pick_evidence(evs: list[dict], max_n: int = 4) -> list[dict]:
    """One article per independent origin (prefer originals), always keeping contradicting evidence."""
    evs = sorted(evs, key=lambda e: (ROLE_RANK.get(e["role"], 9), e["published_at"] or e["retrieved_at"]))
    chosen, seen = [], set()
    for e in evs:
        if e["role"] in ("contradicting", "retraction", "correction") or e["origin_key"] not in seen:
            chosen.append(e)
            seen.add(e["origin_key"])
    contra = [e for e in chosen if e["role"] in ("contradicting", "retraction", "correction")]
    rest = [e for e in chosen if e not in contra]
    return (rest[: max(1, max_n - len(contra))] + contra)[: max_n + len(contra)]


def _loc(inc: dict) -> str:
    country = COUNTRY_NAMES.get(inc["country_code"] or "")
    name = inc["location_name"]
    if name and country and country not in name:
        return f"{name}, {country}"
    return name or country or "location unresolved"


def incident_text(inc: dict, taxonomy: Taxonomy) -> str:
    et = taxonomy.event_types.get(inc["primary_category"])
    parts = [f"{inc['title']}."]
    parts.append(f"{et.label if et else inc['primary_category']} | {_loc(inc)} | severity {inc['severity']}/5 | "
                 f"{VER_LABEL[inc['verification_status']]}, {inc['independent_source_count']} independent origin(s).")
    k, i = inc["casualties_killed"], inc["casualties_injured"]
    if (k or 0) or (i or 0):
        parts.append(casualty_phrase(k, i))
    if inc["verification_status"] == "disputed":
        parts.append("Reporting conflicts: at least one source denies or contradicts this account; both are cited.")
    if inc["official_statement_count"]:
        parts.append("Official statements are cited as attributable claims.")
    if inc["review_status"] == "pending":
        parts.append("Pending analyst review.")
    return " ".join(parts)


def _cite_tag(nums: list[int]) -> str:
    return "".join(f"[{n}]" for n in sorted(set(nums)))


def build_content(conn: psycopg.Connection, tenant_id: str, report_type: str, start: datetime, end: datetime,
                  taxonomy: Taxonomy, country: str | None = None) -> tuple[dict, list[dict], dict]:
    title_base, _ = REPORT_TYPES[report_type]
    cats = MARITIME_ENERGY if report_type == "maritime_energy" else None
    incidents = select_incidents(conn, tenant_id, start, end, country=country, categories=cats)
    ev = evidence_for(conn, [str(i["id"]) for i in incidents])
    book = CitationBook()
    cites: dict[str, list[int]] = {}
    for inc in incidents:
        cites[str(inc["id"])] = [book.cite(e, inc) for e in pick_evidence(ev.get(str(inc["id"]), []))]

    def item(inc: dict) -> dict:
        return {"incident_id": str(inc["id"]), "incident_ref": inc["ref"], "severity": inc["severity"],
                "verification": inc["verification_status"], "review_status": inc["review_status"],
                "country_code": inc["country_code"], "is_demo": inc["is_demo"],
                "text": incident_text(inc, taxonomy) + " " + _cite_tag(cites[str(inc["id"])])}

    sections: list[dict] = []
    high = [i for i in incidents if i["severity"] >= 4]
    span = f"{start:%d %b %Y %H:%M} to {end:%d %b %Y %H:%M} UTC"
    if incidents:
        summary = [{"text": f"{len(incidents)} incident(s) met the inclusion criteria between {span}; {len(high)} at "
                            f"severity 4-5, {sum(1 for i in incidents if i['verification_status'] == 'disputed')} disputed, "
                            f"{sum(1 for i in incidents if i['verification_status'] == 'single_source')} single-source.",
                    "computed": True}]
        for inc in incidents[:3]:
            summary.append({"text": f"{inc['title']} ({_loc(inc)}; {VER_LABEL[inc['verification_status']]}, "
                                    f"severity {inc['severity']}/5). {_cite_tag(cites[str(inc['id'])])}"})
    else:
        summary = [{"text": f"No incidents meeting the inclusion criteria were recorded from monitored sources between "
                            f"{span}. This reflects monitored-source coverage only, not the absence of events.",
                    "computed": True}]
    sections.append({"key": "executive_summary", "heading": "Executive summary", "paragraphs": summary})
    sections.append({"key": "key_developments", "heading": "Key developments",
                     "items": [item(i) for i in incidents[:12]],
                     "empty": "No qualifying developments in this period." if not incidents else None})

    if report_type in ("daily_gcc", "weekly_outlook", "monthly_assessment", "custom", "breaking"):
        for c in GCC_COUNTRIES:
            c_inc = [i for i in incidents if i["country_code"] == c or c in (i["affected_countries"] or [])]
            sections.append({"key": f"country:{c}", "heading": COUNTRY_NAMES[c], "items": [item(i) for i in c_inc[:6]],
                             "empty": "No qualifying incidents recorded from monitored sources." if not c_inc else None})
        regional = [i for i in incidents if i["country_code"] not in GCC_COUNTRIES]
        sections.append({"key": "regional", "heading": "Regional developments affecting the GCC",
                         "items": [item(i) for i in regional[:8]],
                         "empty": "No qualifying regional developments." if not regional else None})
    if report_type != "maritime_energy":
        me = [i for i in incidents if set(i["categories"] or []) & set(MARITIME_ENERGY)]
        sections.append({"key": "maritime_energy", "heading": "Maritime & energy", "items": [item(i) for i in me[:8]],
                         "empty": "No qualifying maritime or energy incidents." if not me else None})

    # Assessment: per risk dimension, which countries the evidence concentrates in (deterministic)
    dims = {d["key"]: d["label"] for d in load_json("taxonomy.json")["risk_dimensions"]}
    by_dim: dict[str, list[dict]] = {}
    for inc in incidents:
        for d in taxonomy.dimensions_for(inc["categories"] or []):
            by_dim.setdefault(d, []).append(inc)
    assessment = []
    for d, incs in sorted(by_dim.items(), key=lambda kv: -sum(i["severity"] for i in kv[1]))[:4]:
        countries = Counter(COUNTRY_NAMES.get(i["country_code"] or "", "regional waters") for i in incs)
        nums = [n for i in incs[:4] for n in cites[str(i["id"])][:2]]
        assessment.append({"text": f"{dims.get(d, d)}: {len(incs)} incident(s), concentrated in "
                                   f"{', '.join(f'{k} ({v})' for k, v in countries.most_common(3))}; highest severity "
                                   f"{max(i['severity'] for i in incs)}/5. {_cite_tag(nums)}"})
    risk_rows = fetch_all(conn, """
        select distinct on (country_code, dimension) country_code, dimension, score, band, confidence
        from risk_assessments where tenant_id = %s and as_of_date <= %s::date and band in ('elevated', 'high', 'severe')
        order by country_code, dimension, as_of_date desc""", (tenant_id, end))
    for r in risk_rows[:5]:
        assessment.append({"text": f"Observed-reporting risk index: {COUNTRY_NAMES.get(r['country_code'])} - "
                                   f"{dims.get(r['dimension'], r['dimension'])} is {r['band']} "
                                   f"({float(r['score']):.2f}/5, {r['confidence']} confidence). See Risk Analysis for "
                                   f"factor contributions.", "computed": True})
    if not assessment:
        assessment.append({"text": "Insufficient qualifying evidence in this period to support an assessment.",
                           "computed": True})
    sections.append({"key": "assessment", "heading": "Assessment", "paragraphs": assessment})

    # Uncertainty: computed gaps, never hidden
    unc = []
    ss = [i for i in incidents if i["verification_status"] == "single_source"]
    disputed = [i for i in incidents if i["verification_status"] == "disputed"]
    pending = [i for i in incidents if i["review_status"] == "pending"]
    if ss:
        unc.append(f"{len(ss)} incident(s) rest on a single independent origin and may change.")
    if disputed:
        unc.append(f"{len(disputed)} incident(s) have contradictory reporting; both accounts are cited.")
    if pending:
        unc.append(f"{len(pending)} incident(s) are awaiting analyst review.")
    unhealthy = fetch_all(conn, """select name from sources where tenant_id = %s and enabled and consecutive_failures > 0
                                   order by consecutive_failures desc limit 5""", (tenant_id,))
    if unhealthy:
        unc.append("Sources with recent fetch failures (coverage gap): " + ", ".join(r["name"] for r in unhealthy) + ".")
    untranslated = sum(1 for c in book.items if c["language"] != "en" and not c["title_en"])
    if untranslated:
        unc.append(f"{untranslated} cited non-English item(s) have no machine translation (no LLM configured); "
                   "original-language headlines are shown.")
    unc.append("Casualty and damage figures are as reported by the cited sources and may be revised.")
    sections.append({"key": "uncertainty", "heading": "Uncertainty and gaps",
                     "paragraphs": [{"text": t, "computed": True} for t in unc]})

    watch: list[str] = []
    for inc in incidents[:8]:
        for w in (inc["context"] or {}).get("watch_indicators", []):
            if w not in watch:
                watch.append(w)
    sections.append({"key": "watch_indicators", "heading": "Watch indicators", "bullets": watch[:8] or
                     ["No specific indicators; maintain routine monitoring."]})
    n_sources = fetch_one(conn, "select count(*) as n from sources where tenant_id = %s and enabled", (tenant_id,))["n"]
    sections.append({"key": "methodology", "heading": "Methodology note", "paragraphs": [{"computed": True, "text": (
        f"Compiled from {n_sources} monitored, licence-reviewed sources. Incidents are included only when linked to "
        "stored evidence; unverified incidents are excluded. Verification counts independent origins, not articles: "
        "syndicated copies and same-network reports do not corroborate each other. Official statements are reported "
        "as attributable claims. Severity is computed by a documented rule set and checked by analysts for high-impact "
        "items. Excerpts are limited by each source's licence; follow the citation links for full reporting.")}]})

    is_demo = any(i["is_demo"] for i in incidents)
    title = f"{title_base}{' - ' + COUNTRY_NAMES.get(country, country) if country else ''} - {end:%d %b %Y}"
    content = {"title": title, "report_type": report_type, "country_code": country,
               "period": {"start": start.isoformat(), "end": end.isoformat()}, "is_demo": is_demo,
               "sections": sections,
               "incidents": [{"id": str(i["id"]), "ref": i["ref"], "title": i["title"], "severity": i["severity"],
                              "verification": i["verification_status"]} for i in incidents]}
    coverage = {"incident_count": len(incidents), "citation_count": len(book.items),
                "sources_enabled": n_sources, "countries_without_incidents":
                    [c for c in GCC_COUNTRIES if not any(i["country_code"] == c or c in (i["affected_countries"] or [])
                                                         for i in incidents)]}
    return content, book.items, coverage


def iter_texts(content: dict):
    for s in content["sections"]:
        for p in s.get("paragraphs", []):
            yield s["key"], p.get("text", ""), p.get("computed", False)
        for it in s.get("items", []):
            yield s["key"], it.get("text", ""), False


def check_citations(conn: psycopg.Connection, tenant_id: str, content: dict, citations: list[dict]) -> dict:
    """Citation integrity: every [n] resolves, every cited article exists in this tenant and is linked
    to the incident it is cited for, every non-computed statement carries a citation."""
    errors: list[str] = []
    warnings: list[str] = []
    valid = {c["n"]: c for c in citations}
    used: set[int] = set()
    for key, text, computed in iter_texts(content):
        nums = [int(n) for n in CITE_RE.findall(text)]
        used.update(nums)
        for n in nums:
            if n not in valid:
                errors.append(f"{key}: citation [{n}] does not exist")
        if not nums and not computed:
            errors.append(f"{key}: statement without citation: {text[:80]!r}")
    unused = sorted(set(valid) - used)
    if unused:
        warnings.append(f"citations never referenced in text: {unused}")
    if citations:
        ids = [c["article_id"] for c in citations]
        rows = fetch_all(conn, """
            select a.id, array_agg(ia.incident_id) filter (where ia.incident_id is not null) as incidents
            from articles a left join incident_articles ia on ia.article_id = a.id
            where a.tenant_id = %s and a.id = any(%s::uuid[]) group by a.id""", (tenant_id, ids))
        found = {str(r["id"]): {str(x) for x in (r["incidents"] or [])} for r in rows}
        for c in citations:
            if c["article_id"] not in found:
                errors.append(f"citation [{c['n']}] points to an article that is not stored for this tenant")
            elif not set(c["incident_ids"]) & found[c["article_id"]]:
                errors.append(f"citation [{c['n']}] is not evidence for the incident it is cited for")
            if c.get("link_status") == "broken":
                warnings.append(f"citation [{c['n']}] link was last checked as broken")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "checked_citations": len(citations),
            "checked_at": datetime.now(UTC).isoformat()}


NARRATIVE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["executive_summary", "assessment"],
    "properties": {
        "executive_summary": {"type": "array", "items": {"type": "string"}},
        "assessment": {"type": "array", "items": {"type": "string"}},
    },
}
NARRATIVE_SYSTEM = (
    "You are a careful regional security analyst writing for corporate risk managers. Write ONLY from the numbered "
    "evidence provided. Every paragraph must cite evidence numbers in square brackets, e.g. [2][5]. Do not add facts, "
    "numbers, names, places or dates that are not in the evidence. Keep verification status explicit (single-source, "
    "disputed). Official statements are claims, not facts. Neutral tone; no speculation beyond 'watch' language.\n\n"
    + injection.UNTRUSTED_DATA_POLICY)


def llm_narrative(content: dict, citations: list[dict], llm: LLMService) -> tuple[bool, str]:
    """Replace executive summary / assessment with validated LLM prose. Returns (applied, note)."""
    if not llm.available or not content["incidents"]:
        return False, "template"
    lines = []
    for s in content["sections"]:
        if s["key"] == "key_developments":
            lines += [it["text"] for it in s["items"]]
    evidence_text = "\n".join(f"- {t}" for t in lines)
    try:
        result = llm.json(agent="briefing", task="briefing", system=NARRATIVE_SYSTEM,
                          user=injection.fence("evidence", evidence_text), schema=NARRATIVE_SCHEMA,
                          schema_name="brief_narrative", max_tokens=3000, effort="medium")
    except (LLMUnavailable, LLMError) as exc:
        return False, f"llm unavailable: {type(exc).__name__}"
    data = result.data or {}
    valid_nums = {c["n"] for c in citations}
    allowed_numbers = set(NUM_RE.findall(evidence_text)) | {str(n) for n in valid_nums}
    problems = []
    for key in ("executive_summary", "assessment"):
        paras = data.get(key) or []
        if not paras:
            problems.append(f"{key}: empty")
        for p in paras:
            nums = {int(n) for n in CITE_RE.findall(p)}
            if not nums:
                problems.append(f"{key}: paragraph without citation")
            if nums - valid_nums:
                problems.append(f"{key}: invalid citation {sorted(nums - valid_nums)}")
            stray = {n for n in NUM_RE.findall(CITE_RE.sub("", p))} - allowed_numbers
            if stray:
                problems.append(f"{key}: numbers not in evidence {sorted(stray)[:5]}")
    if problems:
        return False, "llm output rejected: " + "; ".join(problems[:5])
    for s in content["sections"]:
        if s["key"] == "executive_summary":
            s["paragraphs"] = [s["paragraphs"][0]] + [{"text": p, "generated_by": result.model} for p in data["executive_summary"]]
        if s["key"] == "assessment":
            s["paragraphs"] = [{"text": p, "generated_by": result.model} for p in data["assessment"]] + \
                [p for p in s["paragraphs"] if p.get("computed")]
    return True, f"llm:{result.model}"


def generate_report(conn: psycopg.Connection, tenant_id: str, report_type: str, taxonomy: Taxonomy, *,
                    period_end: datetime | None = None, period_start: datetime | None = None,
                    country: str | None = None, llm: LLMService | None = None, created_by: str | None = None,
                    report_id: str | None = None, change_note: str | None = None) -> dict:
    from ..reports.render import render_html  # noqa: PLC0415

    if report_type not in REPORT_TYPES:
        raise ValueError(f"unknown report type {report_type}")
    end = period_end or datetime.now(UTC)
    start = period_start or (end - REPORT_TYPES[report_type][1])
    content, citations, coverage = build_content(conn, tenant_id, report_type, start, end, taxonomy, country)
    generator = GENERATOR
    if llm is not None:
        applied, note = llm_narrative(content, citations, llm)
        coverage["narrative"] = note
        if applied:
            generator = f"{GENERATOR}+{note}"
    check = check_citations(conn, tenant_id, content, citations)
    if report_id:
        rep = fetch_one(conn, "select * from reports where id = %s and tenant_id = %s for update", (report_id, tenant_id))
    else:
        n = fetch_one(conn, "select app.next_ref(%s, 'RPT') as n", (tenant_id,))["n"]
        rep = fetch_one(conn, """
            insert into reports(tenant_id, ref, report_type, title, country_code, period_start, period_end, status,
                                is_demo, created_by, created_by_agent)
            values (%s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, 'briefing-agent') returning *""",
                        (tenant_id, f"RPT-{n:06d}", report_type, content["title"], country, start, end,
                         content["is_demo"], created_by))
    version = rep["current_version"] + 1
    meta = {"ref": rep["ref"], "version": version, "status": "draft", "generated_at": datetime.now(UTC).isoformat()}
    html = render_html(content, citations, meta, check)
    execute_sql = """
        insert into report_versions(tenant_id, report_id, version, content, html, citations, citation_check, coverage,
                                    generator, change_note, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    conn.execute(execute_sql, (tenant_id, rep["id"], version, jsonb(content), html, jsonb(citations), jsonb(check),
                               jsonb(coverage), generator, change_note, created_by))
    status = "in_review" if check["ok"] else "draft"
    rep = fetch_one(conn, """update reports set current_version = %s, title = %s, status = %s, is_demo = %s
                             where id = %s returning *""",
                    (version, content["title"], status, content["is_demo"], rep["id"]))
    from .incidents import ensure_review  # noqa: PLC0415
    ensure_review(conn, tenant_id, "report", rep["id"],
                  "Report ready for review" if check["ok"] else "Citation integrity check failed: " + "; ".join(check["errors"][:3]),
                  2, "briefing-agent")
    audit(conn, tenant_id=tenant_id, action="report.generated", entity_type="report", entity_id=str(rep["id"]),
          agent="briefing-agent", user_id=created_by,
          details={"version": version, "citations": len(citations), "check_ok": check["ok"], "generator": generator})
    return {**rep, "version": version, "citation_check": check, "citations": len(citations),
            "incidents": coverage["incident_count"], "generator": generator}


def as_text(content: dict) -> Any:
    return "\n".join(t for _, t, _ in iter_texts(content))
