"""Seeding: platform reference data, tenants/users, catalog sources and the demo tenant.

Demo data is SYNTHETIC: the demo tenant is flagged is_demo, its sources point at the local
demo feed server, and every demo headline carries a [SYNTHETIC] marker.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

from .db import audit, execute, fetch_all, fetch_one, jsonb
from .log import get_logger
from .refdata import GCC_COUNTRIES, load_json
from .security import hash_password

log = get_logger(__name__)

DEFAULT_TENANT_SETTINGS: dict[str, Any] = {
    "timezone": "Asia/Riyadh",
    "review_policy": {
        "incident_min_severity": 4,            # severity >= 4 always needs human review
        "incident_unverified_min_severity": 3,  # single-source/unverified/disputed at >= 3 need review
        "alert_high_impact_min_severity": 4,    # alerts on incidents >= 4 are high impact
        "high_impact_requires_review": True,    # never auto-send high-impact alerts
        "auto_send_standard_alerts": True,
    },
    "verification_policy": {"corroboration_min_independent": 2, "confirmed_requires_analyst": True},
    "matching": {"window_hours": 72, "threshold": 0.6},
    "retention": {"article_excerpt_days": 365, "chat_days": 365, "audit_days": 730},
    "llm": {"monthly_budget_usd": 25},
}


def seed_reference(conn: psycopg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    geo = load_json("geo.json")
    coord_source = geo["coord_source"]
    for e in geo["entities"]:
        execute(conn, """
            insert into geo_entities(id, kind, name, name_ar, aliases, country_code, parent_id, lat, lon,
                                     precision_km, coord_source, is_gcc, region)
            values (%(id)s, %(kind)s, %(name)s, %(name_ar)s, %(aliases)s, %(country_code)s, null, %(lat)s, %(lon)s,
                    %(precision_km)s, %(coord_source)s, %(is_gcc)s, %(region)s)
            on conflict (id) do update set kind = excluded.kind, name = excluded.name, name_ar = excluded.name_ar,
              aliases = excluded.aliases, country_code = excluded.country_code, lat = excluded.lat, lon = excluded.lon,
              precision_km = excluded.precision_km, coord_source = excluded.coord_source, is_gcc = excluded.is_gcc,
              region = excluded.region""",
                {**e, "aliases": e.get("aliases", []), "coord_source": coord_source,
                 "name_ar": e.get("name_ar"), "country_code": e.get("country_code")})
    for e in geo["entities"]:  # parents after all rows exist
        if e.get("parent_id"):
            execute(conn, "update geo_entities set parent_id = %s where id = %s", (e["parent_id"], e["id"]))
    counts["geo_entities"] = len(geo["entities"])

    tax = load_json("taxonomy.json")
    for et in tax["event_types"]:
        execute(conn, """
            insert into event_types(tenant_id, key, label, label_ar, default_severity, risk_dimensions,
                                    keywords_en, keywords_ar, review_required)
            values (null, %(key)s, %(label)s, %(label_ar)s, %(default_severity)s, %(risk_dimensions)s,
                    %(keywords_en)s, %(keywords_ar)s, %(review_required)s)
            on conflict (tenant_id, key) do update set label = excluded.label, label_ar = excluded.label_ar,
              default_severity = excluded.default_severity, risk_dimensions = excluded.risk_dimensions,
              keywords_en = excluded.keywords_en, keywords_ar = excluded.keywords_ar,
              review_required = excluded.review_required""", et)
    counts["event_types"] = len(tax["event_types"])

    for a in load_json("actors.json")["actors"]:
        execute(conn, """
            insert into actors(id, tenant_id, name, name_ar, aliases, actor_type, country_code)
            values (%(id)s, null, %(name)s, %(name_ar)s, %(aliases)s, %(actor_type)s, %(country_code)s)
            on conflict (id) do update set name = excluded.name, name_ar = excluded.name_ar, aliases = excluded.aliases,
              actor_type = excluded.actor_type, country_code = excluded.country_code""", a)
    counts["actors"] = len(load_json("actors.json")["actors"])

    for s in load_json("sources.json")["sources"]:
        execute(conn, """
            insert into source_catalog(key, name, publisher, adapter, url, homepage_url, language, country_focus,
              source_type, ownership, reliability, reliability_rationale, origin_group, license_type, license_notes,
              terms_url, fetch_interval_minutes, priority, verified_at, verification_note)
            values (%(key)s, %(name)s, %(publisher)s, %(adapter)s, %(url)s, %(homepage_url)s, %(language)s,
              %(country_focus)s, %(source_type)s, %(ownership)s, %(reliability)s, %(reliability_rationale)s,
              %(origin_group)s, %(license_type)s, %(license_notes)s, %(terms_url)s, %(fetch_interval_minutes)s,
              %(priority)s, %(verified_at)s, %(verification_note)s)
            on conflict (key) do update set name = excluded.name, url = excluded.url, verified_at = excluded.verified_at,
              verification_note = excluded.verification_note, reliability = excluded.reliability,
              reliability_rationale = excluded.reliability_rationale, license_type = excluded.license_type,
              license_notes = excluded.license_notes""", s)
    counts["source_catalog"] = len(load_json("sources.json")["sources"])

    platform = load_json("platform.json")
    for p in platform["plans"]:
        execute(conn, """
            insert into plans(id, name, description, price_monthly_minor, currency, limits, features)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (id) do update set name = excluded.name, description = excluded.description,
              limits = excluded.limits, features = excluded.features""",
                (p["id"], p["name"], p["description"], p["price_monthly_minor"], p["currency"],
                 jsonb(p["limits"]), jsonb(p["features"])))
    m = platform["risk_methodology"]
    execute(conn, """
        insert into risk_methodologies(tenant_id, version, name, description_md, params, is_active)
        values (null, %s, %s, %s, %s, true)
        on conflict (tenant_id, version) do nothing""",
            (m["version"], m["name"], m["description_md"], jsonb(m["params"])))
    counts["plans"] = len(platform["plans"])
    return counts


def create_tenant(conn: psycopg.Connection, slug: str, name: str, plan_id: str = "trial",
                  is_demo: bool = False, settings: dict | None = None) -> str:
    row = fetch_one(conn, "select id from tenants where slug = %s", (slug,))
    if row:
        return str(row["id"])
    row = fetch_one(conn, """insert into tenants(slug, name, plan_id, is_demo, settings, branding)
                             values (%s, %s, %s, %s, %s, %s) returning id""",
                    (slug, name, plan_id, is_demo, jsonb(settings or DEFAULT_TENANT_SETTINGS),
                     jsonb({"report_header": name, "accent": "#0f766e"})))
    tenant_id = str(row["id"])
    audit(conn, tenant_id=tenant_id, action="tenant.created", entity_type="tenant", entity_id=tenant_id,
          agent="seed", details={"slug": slug, "is_demo": is_demo})
    return tenant_id


def create_user(conn: psycopg.Connection, email: str, password: str | None, display_name: str,
                auth_provider: str = "local", user_id: str | None = None) -> str:
    row = fetch_one(conn, "select id from app_users where email = %s", (email,))
    if row:
        return str(row["id"])
    row = fetch_one(conn, """insert into app_users(id, email, display_name, password_hash, auth_provider)
                             values (%s, %s, %s, %s, %s) returning id""",
                    (user_id or str(uuid.uuid4()), email, display_name,
                     hash_password(password) if password else None, auth_provider))
    return str(row["id"])


def add_member(conn: psycopg.Connection, tenant_id: str, user_id: str, role: str) -> None:
    execute(conn, """insert into memberships(tenant_id, user_id, role) values (%s, %s, %s)
                     on conflict (tenant_id, user_id) do update set role = excluded.role""",
            (tenant_id, user_id, role))


def add_catalog_sources(conn: psycopg.Connection, tenant_id: str, keys: list[str] | None = None,
                        approve: bool = False) -> int:
    """Copy catalog entries into a tenant registry. They start as 'proposed' and disabled:
    an admin must review licence/robots and approve before anything is fetched."""
    catalog = fetch_all(conn, "select * from source_catalog" + (" where key = any(%s)" if keys else ""),
                        (keys,) if keys else None)
    meta = {s["key"]: s for s in load_json("sources.json")["sources"]}
    n = 0
    for c in catalog:
        execute(conn, """
            insert into sources(tenant_id, catalog_key, name, publisher, adapter, url, homepage_url, language,
              country_focus, source_type, ownership, reliability, reliability_rationale, origin_group, license_type,
              license_notes, terms_url, fetch_interval_minutes, priority, status, enabled, verified_at,
              verification_note, config, next_fetch_at)
            values (%(tenant_id)s, %(key)s, %(name)s, %(publisher)s, %(adapter)s, %(url)s, %(homepage_url)s,
              %(language)s, %(country_focus)s, %(source_type)s, %(ownership)s, %(reliability)s,
              %(reliability_rationale)s, %(origin_group)s, %(license_type)s, %(license_notes)s, %(terms_url)s,
              %(fetch_interval_minutes)s, %(priority)s, %(status)s, %(enabled)s, %(verified_at)s,
              %(verification_note)s, %(config)s, now())
            on conflict (tenant_id, url) do nothing""",
                {**c, "tenant_id": tenant_id, "status": "approved" if approve else "proposed",
                 "enabled": approve and c["license_type"] != "prohibited",
                 "config": jsonb(meta.get(c["key"], {}).get("config", {}))})
        n += 1
    return n


DEMO_SOURCES = [
    {"slug": "demo-wire", "name": "Demo Wire Service [SYNTHETIC]", "publisher": "Demo Wire Service", "language": "en",
     "source_type": "wire_agency", "reliability": "B", "origin_group": "demo-wire", "country_focus": []},
    {"slug": "gulf-daily", "name": "Synthetic Gulf Daily [SYNTHETIC]", "publisher": "Synthetic Gulf Daily",
     "language": "en", "source_type": "news_media", "reliability": "C", "origin_group": "demo-gulf-daily",
     "country_focus": ["AE", "SA"]},
    {"slug": "arabic-daily", "name": "الصحيفة التجريبية [SYNTHETIC]", "publisher": "Demo Arabic Daily",
     "language": "ar", "source_type": "news_media", "reliability": "B", "origin_group": "demo-arabic-daily",
     "country_focus": ["SA", "KW", "BH"]},
    {"slug": "official-gazette", "name": "Demo Official Gazette [SYNTHETIC]", "publisher": "Demo Official Gazette",
     "language": "en", "source_type": "government", "reliability": "B", "origin_group": "demo-official",
     "country_focus": ["AE", "QA", "BH"]},
    {"slug": "maritime-bulletin", "name": "Demo Maritime Security Bulletin [SYNTHETIC]",
     "publisher": "Demo Maritime Security Bulletin", "language": "en", "source_type": "intl_org",
     "reliability": "B", "origin_group": "demo-maritime", "country_focus": ["OM", "YE"]},
    {"slug": "social-aggregator", "name": "Demo Social Aggregator [SYNTHETIC]", "publisher": "Demo Social Aggregator",
     "language": "en", "source_type": "social_media", "reliability": "E", "origin_group": "demo-social",
     "country_focus": []},
]

DEMO_USERS = [
    ("admin@demo.example", "Demo Admin", "admin"),
    ("analyst@demo.example", "Demo Analyst", "analyst"),
    ("reviewer@demo.example", "Demo Reviewer", "reviewer"),
    ("client@demo.example", "Demo Client", "client"),
]


def seed_demo(conn: psycopg.Connection, feed_base_url: str, password: str) -> str:
    tenant_id = create_tenant(conn, "demo", "Demo Organisation (synthetic data)", "team", is_demo=True)
    users = {}
    for email, name, role in DEMO_USERS:
        uid = create_user(conn, email, password, name)
        add_member(conn, tenant_id, uid, role)
        users[role] = uid
    base = feed_base_url.rstrip("/")
    for s in DEMO_SOURCES:
        execute(conn, """
            insert into sources(tenant_id, name, publisher, adapter, url, homepage_url, language, country_focus,
              source_type, ownership, reliability, reliability_rationale, origin_group, license_type, license_notes,
              usage_policy, fetch_interval_minutes, priority, status, enabled, is_demo, next_fetch_at)
            values (%s, %s, %s, 'rss', %s, %s, %s, %s, %s, 'Synthetic test source', %s,
                    'Synthetic source used for demonstration and tests only.', %s, 'synthetic',
                    'Synthetic content generated for testing. Not real news.', 'headline_link_excerpt', 5, 3,
                    'approved', true, true, now())
            on conflict (tenant_id, url) do nothing""",
                (tenant_id, s["name"], s["publisher"], f"{base}/feeds/{s['slug']}.xml", f"{base}/",
                 s["language"], s["country_focus"], s["source_type"], s["reliability"], s["origin_group"]))
    # Demo alert rules (one standard, one broad high-severity rule for the client)
    existing = fetch_one(conn, "select count(*) as n from alert_rules where tenant_id = %s", (tenant_id,))
    if existing and existing["n"] == 0:
        execute(conn, """
            insert into alert_rules(tenant_id, user_id, name, countries, event_types, min_severity, channels,
                                    destination, throttle_minutes)
            values (%s, %s, 'Saudi air & maritime threats', %s, %s, 2, '{email}', %s, 60)""",
                (tenant_id, users["analyst"], ["SA"], ["drone_missile", "maritime_incident", "shipping_disruption"],
                 jsonb({"email": "analyst@demo.example"})))
        execute(conn, """
            insert into alert_rules(tenant_id, user_id, name, countries, min_severity, channels, destination,
                                    throttle_minutes)
            values (%s, %s, 'All GCC - severity 3+', %s, 3, '{email}', %s, 120)""",
                (tenant_id, users["client"], GCC_COUNTRIES, jsonb({"email": "client@demo.example"})))
        execute(conn, """
            insert into watchlists(tenant_id, user_id, name, countries, sectors, exposure)
            values (%s, null, 'Default exposure (neutral)', %s, '{energy,shipping}', %s)""",
                (tenant_id, GCC_COUNTRIES, jsonb({c: 1.0 for c in GCC_COUNTRIES})))
    audit(conn, tenant_id=tenant_id, action="demo.seeded", entity_type="tenant", entity_id=tenant_id, agent="seed",
          details={"feed_base_url": base})
    return tenant_id
