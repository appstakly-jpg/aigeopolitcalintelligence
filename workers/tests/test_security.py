"""Tenant isolation and role enforcement via Postgres row-level security, report citation
integrity, publication guard, broken-link auditing and secret encryption."""

import psycopg
import pytest
from conftest import add_source, as_user, make_user, rss

from gccintel.agents import briefing, quality
from gccintel.db import fetch_all, fetch_one
from gccintel.notify.channels import MockChannel
from gccintel.pipeline import run_pipeline
from gccintel.refdata import taxonomy_from_db
from gccintel.security import decrypt_secret, encrypt_secret, hash_password, verify_password


def _seed_incident(conn, tenant, settings, web, fetcher, url_suffix="a"):
    src = add_source(conn, tenant, url=f"https://feeds.test/{tenant}-{url_suffix}.xml", name=f"Src {url_suffix}")
    web.set(src["url"], rss([{"title": "Power outage hits Kuwait City districts",
                              "body": "A power outage affected districts of Kuwait City, the ministry said.",
                              "link": f"https://w.test/{tenant}/{url_suffix}", "minutes_ago": 5}]))
    run_pipeline(conn, tenant, settings=settings, force=True, fetcher=fetcher, channel_overrides={"email": MockChannel()})
    return fetch_one(conn, "select * from incidents where tenant_id = %s", (tenant,))


@pytest.fixture()
def two_tenants(conn, settings, web, fetcher):
    import uuid

    from gccintel.seed import create_tenant
    a = create_tenant(conn, f"a-{uuid.uuid4().hex[:8]}", "Tenant A", "team")
    b = create_tenant(conn, f"b-{uuid.uuid4().hex[:8]}", "Tenant B", "team")
    inc_a = _seed_incident(conn, a, settings, web, fetcher, "a")
    inc_b = _seed_incident(conn, b, settings, web, fetcher, "b")
    return a, b, inc_a, inc_b


def test_tenant_isolation(conn, two_tenants):
    a, b, inc_a, inc_b = two_tenants
    analyst_a = make_user(conn, a, "analyst")
    with as_user(conn, analyst_a, a):
        ids = {r["id"] for r in fetch_all(conn, "select id from incidents")}
        assert inc_a["id"] in ids and inc_b["id"] not in ids
        assert fetch_all(conn, "select id from articles where tenant_id = %s", (b,)) == []
        assert conn.execute("update incidents set title = 'hacked' where id = %s", (inc_b["id"],)).rowcount == 0
    # Claiming another tenant in the JWT without membership yields nothing
    with as_user(conn, analyst_a, b):
        assert fetch_all(conn, "select id from incidents") == []


def test_client_role_restrictions(conn, two_tenants):
    a, _, inc_a, _ = two_tenants
    client = make_user(conn, a, "client")
    with as_user(conn, client, a):
        assert [r["id"] for r in fetch_all(conn, "select id from incidents")] == [inc_a["id"]]
        assert conn.execute("update incidents set title = 'edited by client'").rowcount == 0   # read-only role
    conn.execute("update incidents set review_status = 'rejected' where id = %s", (inc_a["id"],))
    with as_user(conn, client, a):
        assert fetch_all(conn, "select id from incidents") == []                # rejected hidden from clients
        assert fetch_all(conn, "select id from jobs") == []                     # staff-only operational data
        assert fetch_all(conn, "select id from llm_usage") == []                # admin-only cost data
        assert fetch_all(conn, "select id from analyst_reviews") == []          # review queue is staff-only


def test_secret_columns_not_exposed(conn, two_tenants):
    a, *_ = two_tenants
    admin = make_user(conn, a, "admin")
    with as_user(conn, admin, a):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select password_hash from app_users")


def test_only_reviewers_publish_and_only_with_valid_citations(conn, two_tenants, settings):
    a, b, _, inc_b = two_tenants
    rep = briefing.generate_report(conn, a, "daily_gcc", taxonomy_from_db(conn, a))
    assert rep["citation_check"]["ok"]
    analyst = make_user(conn, a, "analyst")
    reviewer = make_user(conn, a, "reviewer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with as_user(conn, analyst, a):
            conn.execute("update reports set status = 'published' where id = %s", (rep["id"],))
    with as_user(conn, reviewer, a):
        conn.execute("update reports set status = 'published', published_by = %s, published_at = now() where id = %s",
                     (reviewer, rep["id"]))
    assert fetch_one(conn, "select status from reports where id = %s", (rep["id"],))["status"] == "published"


def test_citation_integrity_detects_tampering(conn, two_tenants):
    a, b, inc_a, inc_b = two_tenants
    content, citations, _ = briefing.build_content(conn, a, "daily_gcc", *briefing_period(), taxonomy_from_db(conn, a))
    assert briefing.check_citations(conn, a, content, citations)["ok"]
    # 1) a citation number that does not exist
    content["sections"][0]["paragraphs"].append({"text": "Invented claim. [99]"})
    assert not briefing.check_citations(conn, a, content, citations)["ok"]
    content["sections"][0]["paragraphs"].pop()
    # 2) a statement without any citation
    content["sections"][0]["paragraphs"].append({"text": "Unsourced assertion."})
    assert not briefing.check_citations(conn, a, content, citations)["ok"]
    content["sections"][0]["paragraphs"].pop()
    # 3) a citation pointing to another tenant's article
    other = fetch_one(conn, "select a.id from articles a where a.tenant_id = %s limit 1", (b,))
    forged = [dict(c) for c in citations]
    forged[0]["article_id"] = str(other["id"])
    res = briefing.check_citations(conn, a, content, forged)
    assert not res["ok"] and any("not stored for this tenant" in e for e in res["errors"])


def test_failed_citation_check_blocks_publication(conn, two_tenants):
    a, *_ = two_tenants
    rep = briefing.generate_report(conn, a, "daily_gcc", taxonomy_from_db(conn, a))
    conn.execute("""update report_versions set citation_check = jsonb_set(citation_check, '{ok}', 'false')
                    where report_id = %s""", (rep["id"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("update reports set status = 'published' where id = %s", (rep["id"],))


def test_broken_links_found_by_quality_audit(conn, tenant, settings, web, fetcher):
    inc = _seed_incident(conn, tenant, settings, web, fetcher)
    art = fetch_one(conn, "select a.url from articles a join incident_articles ia on ia.article_id = a.id where ia.incident_id = %s",
                    (inc["id"],))
    web.set(art["url"], "gone", status=404, ctype="text/html")
    out = quality.audit_tenant(conn, tenant, settings, fetcher)
    assert out["links_broken"] == 1
    assert fetch_one(conn, "select link_status from articles where url = %s", (art["url"],))["link_status"] == "broken"


def test_password_and_secret_crypto():
    h = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", h) and not verify_password("wrong-password!", h)
    key = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    tok = encrypt_secret("https://hooks.slack.com/services/T/B/x", tenant_id="t1", name="slack_webhook_url", key_b64=key)
    assert "hooks.slack.com" not in tok
    assert decrypt_secret(tok, tenant_id="t1", name="slack_webhook_url", key_b64=key).endswith("/x")
    with pytest.raises(Exception):
        decrypt_secret(tok, tenant_id="t2", name="slack_webhook_url", key_b64=key)  # bound to tenant + name


def briefing_period():
    from datetime import UTC, datetime, timedelta
    end = datetime.now(UTC)
    return end - timedelta(days=1), end


def test_reference_numbers_only_for_member_tenant(conn, two_tenants):
    """app.next_ref is SECURITY DEFINER: a signed-in user may only number tenants they belong to."""
    a, b, _, _ = two_tenants
    analyst_a = make_user(conn, a, "analyst")
    with as_user(conn, analyst_a, a):
        assert fetch_one(conn, "select app.next_ref(%s, 'TEST') as n", (a,))["n"] >= 1
    for claimed in (a, b):  # another tenant's counter; a claimed tenant without membership
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_user(conn, analyst_a, claimed):
            conn.execute("select app.next_ref(%s, 'TEST')", (b,))


def test_reviewers_see_every_alert_but_not_rule_destinations(conn, tenant, settings, web, fetcher):
    """Reviewers must see other users' alerts to approve them; the rules behind them (which hold
    delivery destinations) stay private to their owner and admins."""
    from conftest import add_rule
    client = make_user(conn, tenant, "client")
    reviewer = make_user(conn, tenant, "reviewer")
    other_client = make_user(conn, tenant, "client")
    rule = add_rule(conn, tenant, client, name="Client KW rule", countries=["KW"], min_severity=1)
    _seed_incident(conn, tenant, settings, web, fetcher, "rv")
    with as_user(conn, reviewer, tenant):
        rows = fetch_all(conn, """select a.id, rs.name, rs.owner_role from alerts a
                                  left join lateral app.alert_rule_summary(a.rule_id) rs on true""")
        assert rows and all(r["name"] == "Client KW rule" and r["owner_role"] == "client" for r in rows)
        assert fetch_all(conn, "select id from alert_rules") == []
    with as_user(conn, other_client, tenant):
        assert fetch_all(conn, "select id from alerts") == []
        assert fetch_one(conn, "select * from app.alert_rule_summary(%s)", (rule["id"],)) is None


def test_branded_reports_follow_the_plan_and_sanitise_colours(conn, two_tenants):
    from gccintel.agents.briefing import report_branding
    from gccintel.reports.render import render_docx, render_html
    a, _, _, _ = two_tenants  # plan "team": no branded reports
    conn.execute("""update tenants set branding = '{"report_header": "Acme Risk Desk", "accent": "#123abc"}' where id = %s""", (a,))
    assert report_branding(conn, a) is None
    conn.execute("update tenants set plan_id = 'enterprise' where id = %s", (a,))
    assert report_branding(conn, a) == {"header": "Acme Risk Desk", "accent": "#123abc"}
    conn.execute("""update tenants set branding = '{"report_header": "X", "accent": "red;}</style><script>"}' where id = %s""", (a,))
    assert report_branding(conn, a)["accent"] is None  # never written into CSS unless it is a plain hex colour
    content = {"title": "T", "is_demo": True, "period": {"start": "2026-11-12T00:00", "end": "2026-11-13T00:00"}, "sections": []}
    meta = {"ref": "RPT-1", "version": 1, "status": "draft", "generated_at": "2026-11-13T00:00",
            "branding": {"header": "Acme <b>Risk</b>", "accent": "#123abc"}}
    html = render_html(content, [], meta)
    assert "--accent:#123abc" in html and "Acme &lt;b&gt;Risk&lt;/b&gt;" in html
    assert render_docx(content, [], meta)[:2] == b"PK"


def test_redirects_into_private_networks_are_refused(settings):
    """A public feed URL must not be able to redirect the fetcher to loopback, link-local (cloud
    metadata) or private addresses; the refused hop is never sent."""
    import dataclasses

    import httpx

    from gccintel.adapters.http import FetchBlocked, PoliteFetcher

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.host == "feeds.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="secret")

    strict = dataclasses.replace(settings, allow_private_network_fetch=False)
    f = PoliteFetcher(strict, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FetchBlocked):
            f.get("https://feeds.example/feed.xml")
        with pytest.raises(FetchBlocked):
            f.get("http://127.0.0.1:8088/feed.xml")
    finally:
        f.close()
    assert "https://feeds.example/feed.xml" in seen
    assert not any("169.254.169.254" in u or "127.0.0.1" in u for u in seen)

    # Demo mode (local synthetic feeds) can opt in explicitly.
    relaxed = dataclasses.replace(settings, allow_private_network_fetch=True)
    f = PoliteFetcher(relaxed, transport=httpx.MockTransport(handler))
    try:
        assert f.get("https://feeds.example/feed.xml").status == 200
    finally:
        f.close()


def test_organisation_monthly_llm_budget_stops_model_calls(conn, tenant, settings):
    """The monthly budget an admin sets (Administration -> Policies) is enforced before any
    provider call; 0 turns model use off for the organisation."""
    import dataclasses

    from gccintel.db import execute
    from gccintel.llm.base import LLMBudgetExceeded
    from gccintel.llm.mock_provider import MockProvider
    from gccintel.llm.service import LLMService

    s = dataclasses.replace(settings, llm_model_default="mock-model", llm_model_extraction="mock-model",
                            llm_daily_budget_usd=1000.0)
    provider = MockProvider(lambda kind, system, payload, schema_name: {"ok": True})
    call = {"agent": "test", "task": "extraction", "system": "s", "user": "u", "schema": {"type": "object"},
            "schema_name": "t"}

    execute(conn, """update tenants set settings = jsonb_set(settings, '{llm,monthly_budget_usd}', '5')
                     where id = %s""", (tenant,))
    LLMService(conn, tenant, provider, s).json(**call)
    assert len(provider.calls) == 1

    execute(conn, """insert into llm_usage(tenant_id, agent, provider, model, cost_usd, success)
                     values (%s, 'test', 'mock', 'mock-model', 5, true)""", (tenant,))
    with pytest.raises(LLMBudgetExceeded, match="monthly"):
        LLMService(conn, tenant, provider, s).json(**call)
    assert len(provider.calls) == 1  # refused before reaching the provider

    execute(conn, """update tenants set settings = jsonb_set(settings, '{llm,monthly_budget_usd}', '0')
                     where id = %s""", (tenant,))
    execute(conn, "delete from llm_usage where tenant_id = %s", (tenant,))
    with pytest.raises(LLMBudgetExceeded, match="monthly"):
        LLMService(conn, tenant, provider, s).json(**call)
    assert len(provider.calls) == 1
