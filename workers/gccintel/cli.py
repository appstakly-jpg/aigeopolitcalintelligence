"""Command-line entry point: `gccintel <command>` (see `gccintel --help`)."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import get_settings
from .db import connect, fetch_all, fetch_one
from .log import get_logger, setup_logging

log = get_logger("gccintel.cli")


def _tenant_id(conn, slug: str) -> str:
    row = fetch_one(conn, "select id from tenants where slug = %s", (slug,))
    if row is None:
        sys.exit(f"tenant '{slug}' not found")
    return str(row["id"])


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def cmd_migrate(args, settings):
    import re  # noqa: PLC0415

    from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: PLC0415

    from .migrate import ensure_database, migrate  # noqa: PLC0415
    if args.create_db:
        dbname = conninfo_to_dict(settings.database_url).get("dbname") or ""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", dbname):
            sys.exit(f"refusing to create database with unexpected name {dbname!r}")
        ensure_database(make_conninfo(settings.database_url, dbname="postgres"), dbname)
    _print({"applied": migrate(settings.database_url)})


def cmd_seed(args, settings):
    from .seed import seed_reference  # noqa: PLC0415
    with connect(settings.database_url) as conn:
        counts = seed_reference(conn)
        conn.commit()
    _print(counts)


def cmd_tenant_create(args, settings):
    from .seed import add_catalog_sources, add_member, create_tenant, create_user  # noqa: PLC0415
    with connect(settings.database_url) as conn:
        tid = create_tenant(conn, args.slug, args.name, args.plan)
        if args.admin_email:
            pw = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password (min 12 chars): ")
            uid = create_user(conn, args.admin_email, pw, args.admin_name or args.admin_email)
            add_member(conn, tid, uid, "admin")
        n = add_catalog_sources(conn, tid, approve=False)
        conn.commit()
    _print({"tenant_id": tid, "catalog_sources_proposed": n,
            "note": "Catalog sources are proposed and disabled until an admin reviews licence terms and approves them."})


def cmd_user_create(args, settings):
    from .seed import add_member, create_user  # noqa: PLC0415
    with connect(settings.database_url) as conn:
        tid = _tenant_id(conn, args.tenant)
        pw = os.environ.get("USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
        uid = create_user(conn, args.email, pw, args.name or args.email)
        add_member(conn, tid, uid, args.role)
        conn.commit()
    _print({"user_id": uid, "role": args.role})


def cmd_demo_setup(args, settings):
    if not settings.demo_mode:
        sys.exit("DEMO_MODE must be true to create synthetic demo data")
    from .seed import seed_demo, seed_reference  # noqa: PLC0415
    # No built-in default password: a predictable credential would survive into any deployment that
    # ran the demo. Without DEMO_PASSWORD a random one is generated and printed once.
    password = os.environ.get("DEMO_PASSWORD") or ""
    generated = not password
    if generated:
        password = secrets.token_urlsafe(12)
    emails = ["admin@demo.example", "analyst@demo.example", "reviewer@demo.example", "client@demo.example"]
    with connect(settings.database_url) as conn:
        existing = fetch_one(conn, "select count(*) as n from app_users where email = any(%s)", (emails,))["n"]
        seed_reference(conn)
        tid = seed_demo(conn, args.feed_base_url, password)
        conn.commit()
    if existing == len(emails):  # seeding is idempotent and never resets passwords
        shown = "(unchanged: the demo users already existed)"
    else:
        shown = password if generated else "(value of DEMO_PASSWORD)"
    _print({"tenant_id": tid, "users": emails, "password": shown, "feeds": args.feed_base_url})


def cmd_demo_feeds(args, settings):
    if not settings.demo_mode:
        sys.exit("DEMO_MODE must be true to serve synthetic feeds")
    from .demo_server import serve  # noqa: PLC0415
    print(f"Serving SYNTHETIC demo feeds on http://{args.host}:{args.port} (stage {args.stage})", flush=True)
    serve(args.host, args.port, args.base_url, args.stage, args.auto_advance)


def cmd_demo_run(args, settings):
    """Self-contained demonstration: feeds -> incidents -> corroboration -> brief -> alerts."""
    if not settings.demo_mode:
        sys.exit("DEMO_MODE must be true")
    if not settings.allow_private_network_fetch:
        sys.exit("ALLOW_PRIVATE_NETWORK_FETCH=true is required to fetch the local demo feed server")
    from .agents import briefing, risk  # noqa: PLC0415
    from .demo_server import serve  # noqa: PLC0415
    from .pipeline import run_pipeline  # noqa: PLC0415
    from .refdata import taxonomy_from_db  # noqa: PLC0415
    server, state = serve(args.host, args.port, background=True)
    out = {}
    try:
        with connect(settings.database_url, autocommit=True) as conn:
            tid = _tenant_id(conn, "demo")
            r1 = run_pipeline(conn, tid, settings=settings, force=True)
            out["stage1"] = r1["stats"]
            state.set_stage(2)
            r2 = run_pipeline(conn, tid, settings=settings, force=True)
            out["stage2"] = r2["stats"]
            tax = taxonomy_from_db(conn, tid)
            with conn.transaction():
                risk_rows = risk.compute_assessments(conn, tid, tax)
            out["risk"] = {"assessments": len(risk_rows),
                           "non_low": [f"{r['country_code']}/{r['dimension']}: {r['band']} {r['score']}"
                                       for r in risk_rows if r["band"] not in ("low",)]}
            with conn.transaction():
                rep = briefing.generate_report(conn, tid, "daily_gcc", tax)
            out["brief"] = {"ref": rep["ref"], "status": rep["status"], "citations": rep["citations"],
                            "incidents": rep["incidents"], "citation_check": rep["citation_check"]}
            out["incidents"] = fetch_all(conn, """
                select ref, title, verification_status, severity, review_status, independent_source_count as independent,
                       source_count, location_name, geo_precision, version
                from incidents where tenant_id = %s order by ref""", (tid,))
            out["alerts"] = fetch_all(conn, """select a.kind, a.impact, a.status, a.status_reason, r.name as rule, a.title
                                              from alerts a join alert_rules r on r.id = a.rule_id
                                              where a.tenant_id = %s order by a.created_at""", (tid,))
            out["deliveries"] = fetch_all(conn, """select d.channel, d.status, d.destination_masked, d.last_error, d.attempts
                                                  from alert_deliveries d where d.tenant_id = %s""", (tid,))
    finally:
        server.shutdown()
    _print(out)


def cmd_demo_advance(args, settings):
    """Release the next stage of the SYNTHETIC demo feeds (denials, corrections, follow-ups)."""
    import httpx  # noqa: PLC0415
    # The feed server only accepts this from loopback: run it where the feed server runs
    # (`docker compose exec demo-feeds gccintel demo-advance`).
    r = httpx.post(f"http://127.0.0.1:{args.port}/admin/stage", params={"set": args.stage}, timeout=10)
    r.raise_for_status()
    _print(r.json())


def cmd_run_once(args, settings):
    from .pipeline import run_pipeline  # noqa: PLC0415
    with connect(settings.database_url, autocommit=True) as conn:
        out = run_pipeline(conn, _tenant_id(conn, args.tenant), settings=settings, force=args.force)
    _print({"stats": out["stats"], "fetch": out["fetch"]})


def cmd_worker(args, settings):
    from .worker import run_worker  # noqa: PLC0415
    n = run_worker(settings, once=args.once)
    _print({"processed": n})


def cmd_scheduler(args, settings):
    from .scheduler import run_scheduler  # noqa: PLC0415
    run_scheduler(settings, interval=args.interval, once=args.once)


def cmd_report(args, settings):
    from .agents import briefing  # noqa: PLC0415
    from .llm.service import LLMService, build_provider  # noqa: PLC0415
    from .refdata import taxonomy_from_db  # noqa: PLC0415
    from .reports.render import render_docx, render_pdf  # noqa: PLC0415
    with connect(settings.database_url, autocommit=True) as conn:
        tid = _tenant_id(conn, args.tenant)
        llm = LLMService(conn, tid, build_provider(settings), settings)
        with conn.transaction():
            rep = briefing.generate_report(conn, tid, args.type, taxonomy_from_db(conn, tid), country=args.country, llm=llm)
        v = fetch_one(conn, "select * from report_versions where report_id = %s and version = %s", (rep["id"], rep["version"]))
    result = {"ref": rep["ref"], "status": rep["status"], "citations": rep["citations"], "incidents": rep["incidents"],
              "generator": rep["generator"], "citation_check": rep["citation_check"], "files": []}
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        meta = {"ref": rep["ref"], "version": rep["version"], "status": rep["status"],
                "generated_at": v["created_at"].isoformat()}
        for fmt in args.formats.split(","):
            path = out / f"{rep['ref']}-v{rep['version']}.{fmt}"
            if fmt == "html":
                path.write_text(v["html"], encoding="utf-8")
            elif fmt == "docx":
                path.write_bytes(render_docx(v["content"], v["citations"], meta))
            elif fmt == "pdf":
                path.write_bytes(render_pdf(v["html"]))
            result["files"].append(str(path))
    _print(result)


def cmd_risk(args, settings):
    from .agents import risk  # noqa: PLC0415
    from .refdata import taxonomy_from_db  # noqa: PLC0415
    with connect(settings.database_url, autocommit=True) as conn:
        tid = _tenant_id(conn, args.tenant)
        with conn.transaction():
            rows = risk.compute_assessments(conn, tid, taxonomy_from_db(conn, tid))
    _print(rows)


def cmd_quality(args, settings):
    from .adapters.http import PoliteFetcher  # noqa: PLC0415
    from .agents import quality  # noqa: PLC0415
    fetcher = PoliteFetcher(settings) if args.check_links else None
    with connect(settings.database_url, autocommit=True) as conn:
        with conn.transaction():
            out = quality.audit_tenant(conn, _tenant_id(conn, args.tenant), settings, fetcher)
    if fetcher:
        fetcher.close()
    _print(out)


def cmd_sources_verify(args, settings):
    from .adapters.http import PoliteFetcher  # noqa: PLC0415
    from .agents.source_scout import verify_source  # noqa: PLC0415
    fetcher = PoliteFetcher(settings)
    results = []
    with connect(settings.database_url, autocommit=True) as conn:
        tid = _tenant_id(conn, args.tenant)
        rows = fetch_all(conn, "select id from sources where tenant_id = %s and (%s::uuid is null or id = %s::uuid)",
                         (tid, args.source, args.source))
        for r in rows:
            results.append(verify_source(conn, str(r["id"]), fetcher))
    fetcher.close()
    _print(results)


def cmd_retention(args, settings):
    with connect(settings.database_url, autocommit=True) as conn:
        _print(fetch_one(conn, "select app.apply_retention() as r")["r"])


def cmd_smtp_sink(args, settings):
    """Development SMTP server that stores every message as an .eml file (no relay)."""
    from aiosmtpd.controller import Controller  # noqa: PLC0415
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)

    class Sink:
        async def handle_DATA(self, server, session, envelope):  # noqa: N802
            name = out / f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}.eml"
            name.write_bytes(envelope.original_content or envelope.content)
            print(f"stored {name} for {envelope.rcpt_tos}", flush=True)
            return "250 Message accepted for delivery"

    ctl = Controller(Sink(), hostname=args.host, port=args.port)
    ctl.start()
    print(f"SMTP sink on {args.host}:{args.port} writing to {out}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        ctl.stop()


def cmd_eval(args, settings):
    from .evaluation import run_evaluation  # noqa: PLC0415
    report = run_evaluation(settings)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _print(report["summary"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gccintel", description="GCC regional security intelligence workers")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("migrate", help="apply SQL migrations")
    s.add_argument("--create-db", action="store_true")
    s.set_defaults(fn=cmd_migrate)
    sub.add_parser("seed", help="load reference data (gazetteer, taxonomy, actors, catalog, plans, methodology)").set_defaults(fn=cmd_seed)
    t = sub.add_parser("tenant-create", help="create an organisation")
    t.add_argument("--slug", required=True)
    t.add_argument("--name", required=True)
    t.add_argument("--plan", default="trial")
    t.add_argument("--admin-email")
    t.add_argument("--admin-name")
    t.set_defaults(fn=cmd_tenant_create)
    u = sub.add_parser("user-create", help="create a user and add them to a tenant")
    u.add_argument("--tenant", required=True)
    u.add_argument("--email", required=True)
    u.add_argument("--name")
    u.add_argument("--role", choices=["admin", "analyst", "reviewer", "client"], required=True)
    u.set_defaults(fn=cmd_user_create)
    d = sub.add_parser("demo-setup", help="create the SYNTHETIC demo tenant (DEMO_MODE only)")
    d.add_argument("--feed-base-url", default="http://127.0.0.1:8088")
    d.set_defaults(fn=cmd_demo_setup)
    f = sub.add_parser("demo-feeds", help="serve SYNTHETIC demo RSS feeds")
    f.add_argument("--host", default="127.0.0.1")
    f.add_argument("--port", type=int, default=8088)
    f.add_argument("--base-url")
    f.add_argument("--stage", type=int, default=1)
    f.add_argument("--auto-advance", type=int, help="release stage 2 after N seconds")
    f.set_defaults(fn=cmd_demo_feeds)
    da = sub.add_parser("demo-advance", help="release the next stage of the SYNTHETIC demo feeds")
    da.add_argument("--port", type=int, default=8088)
    da.add_argument("--stage", type=int, default=2)
    da.set_defaults(fn=cmd_demo_advance)
    r = sub.add_parser("demo-run", help="run the full synthetic demo end to end")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=8088)
    r.set_defaults(fn=cmd_demo_run)
    o = sub.add_parser("run-once", help="run the ingestion pipeline once for a tenant")
    o.add_argument("--tenant", required=True)
    o.add_argument("--force", action="store_true", help="fetch all enabled sources even if not due")
    o.set_defaults(fn=cmd_run_once)
    w = sub.add_parser("worker", help="process queued jobs")
    w.add_argument("--once", action="store_true")
    w.set_defaults(fn=cmd_worker)
    sc = sub.add_parser("scheduler", help="enqueue recurring jobs")
    sc.add_argument("--interval", type=int, default=60)
    sc.add_argument("--once", action="store_true")
    sc.set_defaults(fn=cmd_scheduler)
    rp = sub.add_parser("report", help="generate a report")
    rp.add_argument("--tenant", required=True)
    rp.add_argument("--type", default="daily_gcc")
    rp.add_argument("--country")
    rp.add_argument("--out")
    rp.add_argument("--formats", default="html,docx")
    rp.set_defaults(fn=cmd_report)
    rk = sub.add_parser("risk", help="compute risk assessments")
    rk.add_argument("--tenant", required=True)
    rk.set_defaults(fn=cmd_risk)
    q = sub.add_parser("quality", help="run the quality audit")
    q.add_argument("--tenant", required=True)
    q.add_argument("--check-links", action="store_true")
    q.set_defaults(fn=cmd_quality)
    sv = sub.add_parser("sources-verify", help="technically verify sources (robots, reachability, parsing)")
    sv.add_argument("--tenant", required=True)
    sv.add_argument("--source")
    sv.set_defaults(fn=cmd_sources_verify)
    sub.add_parser("retention", help="apply data retention policies").set_defaults(fn=cmd_retention)
    sk = sub.add_parser("smtp-sink", help="development SMTP server writing .eml files")
    sk.add_argument("--host", default="127.0.0.1")
    sk.add_argument("--port", type=int, default=1025)
    sk.add_argument("--dir", default="var/mail")
    sk.set_defaults(fn=cmd_smtp_sink)
    e = sub.add_parser("eval", help="run the offline evaluation dataset")
    e.add_argument("--out")
    e.set_defaults(fn=cmd_eval)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)
    args.fn(args, settings)


if __name__ == "__main__":
    main()
