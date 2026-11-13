"""Offline evaluation on the synthetic labelled dataset (data/eval_dataset.json).

Runs the real pipeline against an in-memory feed transport in a throwaway tenant, then reports
item-level extraction metrics (relevance precision/recall, category, location, casualties,
stance), event clustering (pairwise precision/recall), alerting (precision, recall, false alert
rate), citation correctness of the generated brief, latency and LLM cost. Every mismatch is
listed under `failures` - nothing is hidden.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from itertools import combinations
from xml.sax.saxutils import escape

import httpx

from .adapters.http import PoliteFetcher
from .agents import briefing
from .config import Settings
from .db import connect, execute, fetch_all, fetch_one, jsonb
from .notify.channels import MockChannel
from .pipeline import run_pipeline
from .refdata import GCC_COUNTRIES, load_json, taxonomy_from_db
from .seed import add_member, create_tenant, create_user

BASE = "https://eval.feeds.test"


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def _feeds(data: dict) -> dict[str, bytes]:
    now = datetime.now(UTC)
    by_src: dict[str, list[dict]] = defaultdict(list)
    for it in data["items"]:
        by_src[it["source"]].append(it)
    out = {}
    for key, items in by_src.items():
        parts = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>eval</title>']
        for it in items:
            parts.append(f"<item><title>{escape(it['title'])}</title><link>{BASE}/a/{it['id']}</link>"
                         f"<guid>{it['id']}</guid><pubDate>{format_datetime(now - timedelta(minutes=it['minutes_ago']))}</pubDate>"
                         f"<description>{escape(it['body'])}</description></item>")
        parts.append("</channel></rss>")
        out[f"{BASE}/{key}.xml"] = "".join(parts).encode()
    return out


def run_evaluation(settings: Settings, keep: bool = False) -> dict:
    data = load_json("eval_dataset.json")
    feeds = _feeds(data)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url in feeds:
            return httpx.Response(200, content=feeds[url], headers={"content-type": "application/rss+xml"})
        return httpx.Response(200 if "/a/" in url else 404, text="ok")

    settings = dataclasses.replace(settings, per_host_min_interval_seconds=0.0)
    fetcher = PoliteFetcher(settings, transport=httpx.MockTransport(handler))
    failures: list[str] = []
    conn = connect(settings.database_url, autocommit=True)
    tenant = create_tenant(conn, f"eval-{uuid.uuid4().hex[:8]}", "Evaluation (synthetic)", "team")
    user = create_user(conn, f"eval-{uuid.uuid4().hex[:8]}@example.test", None, "Eval")
    try:
        add_member(conn, tenant, user, "analyst")
        url_to_item = {}
        for key, meta in data["sources"].items():
            execute(conn, """
                insert into sources(tenant_id, name, publisher, adapter, url, language, source_type, reliability,
                                    origin_group, license_type, status, enabled, next_fetch_at, is_demo)
                values (%s, %s, %s, 'rss', %s, %s, %s, %s, %s, 'synthetic', 'approved', true, now(), true)""",
                    (tenant, meta["name"], meta["name"], f"{BASE}/{key}.xml", meta.get("language", "en"), meta["type"],
                     meta["reliability"], f"eval-{key}"))
        for it in data["items"]:
            url_to_item[f"{BASE}/a/{it['id']}"] = it
        execute(conn, """insert into alert_rules(tenant_id, user_id, name, countries, min_severity, channels, destination,
                                                 throttle_minutes) values (%s, %s, 'Eval GCC sev3+', %s, 3, '{email}', %s, 0)""",
                (tenant, user, GCC_COUNTRIES, jsonb({"email": "eval@example.test"})))
        t0 = time.monotonic()
        out = run_pipeline(conn, tenant, settings=settings, force=True, fetcher=fetcher,
                           channel_overrides={"email": MockChannel()})
        elapsed = time.monotonic() - t0
        n_articles = max(1, out["stats"]["articles_processed"])

        rows = fetch_all(conn, """
            select a.url, a.relevance, a.stance, a.injection_flags, c.extraction, c.decision, c.matched_incident_id
            from articles a left join incident_candidates c on c.article_id = a.id where a.tenant_id = %s""", (tenant,))
        pred: dict[str, dict] = {}
        for r in rows:
            it = url_to_item.get(r["url"])
            if it:
                pred[it["id"]] = r
        tp = fp = fn = tn = 0
        cat_ok = cat_n = loc_ok = loc_n = cty_ok = cty_n = cas_ok = cas_n = st_ok = st_n = 0
        for it in data["items"]:
            exp = it["expect"]
            p = pred.get(it["id"])
            if p is None:
                failures.append(f"{it['id']}: not ingested")
                fn += 1 if exp["relevant"] else 0
                continue
            ex = p["extraction"] or {}
            is_rel = p["decision"] in ("created", "attached") or (ex.get("is_relevant") and p["decision"] != "discarded")
            if exp["relevant"] and is_rel:
                tp += 1
            elif exp["relevant"]:
                fn += 1
                failures.append(f"{it['id']}: missed relevant item ({ex.get('relevance_reason')})")
            elif is_rel:
                fp += 1
                failures.append(f"{it['id']}: irrelevant item kept (categories {ex.get('categories')})")
            else:
                tn += 1
            if exp.get("injection") and not p["injection_flags"]:
                failures.append(f"{it['id']}: injection not flagged")
            if not exp["relevant"]:
                continue
            if "category" in exp:
                cat_n += 1
                if exp["category"] in (ex.get("categories") or []):
                    cat_ok += 1
                else:
                    failures.append(f"{it['id']}: category {exp['category']} not in {ex.get('categories')}")
            loc = ex.get("location") or {}
            if exp.get("entity"):
                loc_n += 1
                if loc.get("entity_id") == exp["entity"]:
                    loc_ok += 1
                else:
                    failures.append(f"{it['id']}: location {loc.get('entity_id')} != {exp['entity']}")
            if "country" in exp:
                cty_n += 1
                if loc.get("country_code") == exp["country"]:
                    cty_ok += 1
                else:
                    failures.append(f"{it['id']}: country {loc.get('country_code')} != {exp['country']}")
            for key, field in (("killed", "casualties_killed"), ("injured", "casualties_injured")):
                if key in exp:
                    cas_n += 1
                    if ex.get(field) == exp[key]:
                        cas_ok += 1
                    else:
                        failures.append(f"{it['id']}: {key} {ex.get(field)} != {exp[key]}")
            if "stance" in exp:
                st_n += 1
                if p["stance"] == exp["stance"] or (exp["stance"] == "official_statement" and p["stance"] == "report"
                                                     and ex.get("claims") and ex["claims"][0].get("is_official")):
                    st_ok += 1
                else:
                    failures.append(f"{it['id']}: stance {p['stance']} != {exp['stance']}")

        # Clustering: pairwise over items that became evidence
        inc_of = {iid: str(p["matched_incident_id"]) for iid, p in pred.items() if p["matched_incident_id"]}
        labelled = [it for it in data["items"] if it["event"] and it["id"] in inc_of]
        ptp = pfp = pfn = 0
        for a, b in combinations(labelled, 2):
            same_true = a["event"] == b["event"]
            same_pred = inc_of[a["id"]] == inc_of[b["id"]]
            if same_true and same_pred:
                ptp += 1
            elif same_pred:
                pfp += 1
                failures.append(f"cluster: {a['id']} and {b['id']} merged but are different events")
            elif same_true:
                pfn += 1
                failures.append(f"cluster: {a['id']} and {b['id']} split but are the same event")

        # Alerting per labelled event
        alerted = {str(r["incident_id"]) for r in fetch_all(conn, "select incident_id from alerts where tenant_id = %s", (tenant,))}
        atp = afp = afn = atn = 0
        for ev, meta in data["events"].items():
            incs = {inc_of[it["id"]] for it in data["items"] if it["event"] == ev and it["id"] in inc_of}
            fired = bool(incs & alerted)
            if meta["alert"] and fired:
                atp += 1
            elif meta["alert"]:
                afn += 1
                failures.append(f"alert: event {ev} should have alerted")
            elif fired:
                afp += 1
                failures.append(f"alert: event {ev} alerted but should not have")
            else:
                atn += 1

        rep = briefing.generate_report(conn, tenant, "daily_gcc", taxonomy_from_db(conn, tenant))
        v = fetch_one(conn, "select content, citations, citation_check from report_versions where report_id = %s", (rep["id"],))
        art_inc = defaultdict(set)
        for r in fetch_all(conn, "select article_id, incident_id from incident_articles where tenant_id = %s", (tenant,)):
            art_inc[str(r["article_id"])].add(str(r["incident_id"]))
        cites = {c["n"]: c for c in v["citations"]}
        items_ok = items_n = 0
        import re  # noqa: PLC0415
        for s in v["content"]["sections"]:
            for it in s.get("items", []):
                items_n += 1
                nums = [int(n) for n in re.findall(r"\[(\d+)\]", it["text"])]
                if nums and all(n in cites and it["incident_id"] in art_inc[cites[n]["article_id"]] for n in nums):
                    items_ok += 1
                else:
                    failures.append(f"citation: {s['key']} item {it['incident_ref']} cites evidence of another incident")
        if not v["citation_check"]["ok"]:
            failures.extend(f"citation check: {e}" for e in v["citation_check"]["errors"])
        cost = fetch_one(conn, "select coalesce(sum(cost_usd), 0) as usd, count(*) as calls from llm_usage where tenant_id = %s",
                         (tenant,))
        summary = {
            "dataset": f"synthetic, {len(data['items'])} items / {len(data['events'])} events (developer-labelled; indicative only)",
            "extractor": "rules + LLM" if settings.llm_enabled else "rules only (no LLM configured)",
            "relevance": {"precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn), "tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "category_recall": _ratio(cat_ok, cat_n), "location_accuracy": _ratio(loc_ok, loc_n),
            "country_accuracy": _ratio(cty_ok, cty_n), "casualty_accuracy": _ratio(cas_ok, cas_n),
            "stance_accuracy": _ratio(st_ok, st_n),
            "clustering": {"pairwise_precision": _ratio(ptp, ptp + pfp), "pairwise_recall": _ratio(ptp, ptp + pfn)},
            "alerts": {"precision": _ratio(atp, atp + afp), "recall": _ratio(atp, atp + afn),
                       "false_alert_rate": _ratio(afp, afp + atn), "tp": atp, "fp": afp, "fn": afn, "tn": atn},
            "citations": {"integrity_check_ok": v["citation_check"]["ok"], "items_correctly_cited": _ratio(items_ok, items_n),
                          "citations": len(v["citations"])},
            "latency": {"pipeline_seconds": round(elapsed, 2), "ms_per_article": int(elapsed * 1000 / n_articles)},
            "cost": {"llm_calls": cost["calls"], "llm_usd": float(cost["usd"])},
            "failure_count": len(failures),
        }
        return {"summary": summary, "failures": failures}
    finally:
        fetcher.close()
        if not keep:
            execute(conn, "delete from tenants where id = %s", (tenant,))
            execute(conn, "delete from app_users where id = %s", (user,))
        conn.close()
