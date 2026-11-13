"""Alert Agent (I).

Matches changed incidents against subscribers' alert rules, decides impact deterministically,
de-duplicates (one alert per rule x incident x kind x version), throttles standard alerts per
rule, routes high-impact alerts to human review (tenant policy - never auto-sent by default),
sends update/correction/retraction follow-ups, and delivers through channels with retries and
per-channel delivery logs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from ..config import Settings, get_settings
from ..db import audit, execute, fetch_all, fetch_one
from ..log import get_logger
from ..notify.base import DeliveryResult, Message
from ..notify.channels import EmailChannel, MockChannel, SlackChannel, TelegramChannel, WebhookChannel
from ..refdata import COUNTRY_NAMES
from ..security import decrypt_secret, mask
from ..text.gazetteer import Gazetteer
from .incidents import ensure_review

log = get_logger(__name__)
AGENT = "alert-agent"
RETRY_MINUTES = [1, 5, 15, 60]
MAX_ATTEMPTS = 5
VER_ORDER = ["unverified", "disputed", "single_source", "corroborated", "confirmed"]


def rule_matches(rule: dict, inc: dict, gazetteer: Gazetteer) -> bool:
    if inc["status"] not in ("active", "closed") or inc["review_status"] == "rejected":
        return False
    if inc["severity"] < rule["min_severity"]:
        return False
    if rule["countries"]:
        places = {inc["country_code"]} | set(inc["affected_countries"] or [])
        # Rules that name specific event types also follow documented exposure pathways
        # (e.g. a Red Sea tanker attack exposes Saudi trade routes) from the context agent.
        if rule["event_types"] and set(inc["categories"] or []) & set(rule["event_types"]):
            places |= {e["country_code"] for e in (inc.get("context") or {}).get("gcc_exposure", [])}
        if not places & set(rule["countries"]):
            return False
    if rule["event_types"] and not set(inc["categories"] or []) & set(rule["event_types"]):
        return False
    if rule["verification_statuses"] and inc["verification_status"] not in rule["verification_statuses"]:
        return False
    if rule["sectors"] and not set(inc["sectors"] or []) & set(rule["sectors"]):
        return False
    if rule["geo_entity_ids"]:
        ent = gazetteer.entities.get(inc["geo_entity_id"] or "")
        ids = {inc["geo_entity_id"]} | ({a.id for a in gazetteer.ancestors(ent)} if ent else set())
        if not ids & set(rule["geo_entity_ids"]):
            return False
    if rule["keywords"]:
        hay = f"{inc['title']} {inc['summary'] or ''} {inc['location_name'] or ''}".lower()
        if not any(k.lower() in hay for k in rule["keywords"]):
            return False
    return True


def compose(inc: dict, kind: str, settings: Settings, changed: list[str] | None, evidence: list[dict]) -> tuple[str, str]:
    prefix = {"new": "", "update": "UPDATE: ", "correction": "CORRECTION: ", "retraction": "RETRACTION: "}[kind]
    demo = "[SYNTHETIC DEMO] " if inc["is_demo"] else ""
    loc = inc["location_name"] or COUNTRY_NAMES.get(inc["country_code"] or "", "unresolved location")
    title = f"{demo}{prefix}{inc['title']}"[:300]
    lines = [
        f"{inc['ref']} | severity {inc['severity']}/5 | {inc['verification_status'].replace('_', ' ')} "
        f"({inc['independent_source_count']} independent origin(s), {inc['source_count']} report(s))",
        f"Location: {loc} (precision: {inc['geo_precision']}"
        + (f", confidence {inc['geo_confidence']:.0%}" if inc["geo_confidence"] is not None else "") + ")",
    ]
    if inc["summary"]:
        lines.append(inc["summary"])
    if kind != "new" and changed:
        lines.append("Changed: " + ", ".join(changed))
    if inc["verification_status"] in ("single_source", "unverified", "disputed"):
        lines.append("Caution: not independently corroborated." if inc["verification_status"] != "disputed"
                     else "Caution: reporting is contradictory.")
    if evidence:
        lines.append("Evidence:")
        lines += [f"- {e['publisher']}: {e['url']}" for e in evidence[:3]]
    lines.append(f"Details: {settings.app_base_url.rstrip('/')}/incidents/{inc['id']}")
    if inc["is_demo"]:
        lines.append("This alert was generated from SYNTHETIC demo data. It does not describe a real event.")
    return title, "\n".join(lines)


def evaluate_incident(conn: psycopg.Connection, inc: dict, change_type: str, changed_fields: list[str],
                      policy: dict, gazetteer: Gazetteer, settings: Settings | None = None,
                      now: datetime | None = None) -> list[dict]:
    """Create alert rows for every matching rule. Returns created alerts."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    rules = fetch_all(conn, "select * from alert_rules where tenant_id = %s and enabled", (inc["tenant_id"],))
    created = []
    evidence = fetch_all(conn, """
        select s.publisher, a.url from incident_articles ia join articles a on a.id = ia.article_id
        join sources s on s.id = a.source_id where ia.incident_id = %s
        order by case ia.role when 'original' then 0 when 'official_statement' then 1 else 2 end, a.published_at""",
                         (inc["id"],))
    for rule in rules:
        # Only alerts the subscriber received (or will receive after review) count as prior notice.
        prior = fetch_one(conn, """select * from alerts where rule_id = %s and incident_id = %s
                                   and status in ('pending_review', 'approved', 'sent', 'partially_sent')
                                   order by created_at desc limit 1""", (rule["id"], inc["id"]))
        if not rule_matches(rule, inc, gazetteer):
            # A retraction must still reach anyone who was alerted about the incident.
            if not (prior and change_type == "retraction"):
                continue
        if prior is None:
            kind = "new"
        elif change_type in ("correction", "retraction"):
            kind = change_type
        else:
            if not rule["notify_updates"]:
                continue
            material = ("severity" in changed_fields or "verification_status" in changed_fields
                        or "casualties_killed" in changed_fields or "geo_entity_id" in changed_fields)
            if not material or inc["version"] <= prior["incident_version"]:
                continue
            kind = "update"
        dedupe = f"{rule['id']}:{inc['id']}:{kind}:{inc['version']}"
        impact = "high" if inc["severity"] >= int(policy.get("alert_high_impact_min_severity", 4)) else "standard"
        if prior is not None and prior["status"] == "pending_review" and kind != "new":
            # Not sent yet: refresh the queued alert instead of stacking an update behind it.
            title, body = compose(inc, prior["kind"], settings, changed_fields, evidence)
            execute(conn, """update alerts set title = %s, body = %s, incident_version = %s,
                             impact = case when %s = 'high' then 'high' else impact end,
                             status_reason = %s where id = %s""",
                    (title, body, inc["version"], impact,
                     f"content refreshed to incident version {inc['version']} ({kind}) while awaiting review", prior["id"]))
            continue
        if impact == "high" and policy.get("high_impact_requires_review", True):
            status, reason = "pending_review", "high-impact alert requires human review before sending"
        elif impact == "standard" and not policy.get("auto_send_standard_alerts", True):
            status, reason = "pending_review", "tenant policy requires review of all alerts"
        else:
            status, reason = "approved", "auto-approved standard alert"
        if kind == "new" and impact == "standard" and rule["throttle_minutes"]:
            recent = fetch_one(conn, """
                select count(*) as n from alerts where rule_id = %s and kind = 'new' and impact = 'standard'
                  and status in ('approved', 'sent', 'partially_sent')
                  and created_at > %s""", (rule["id"], now - timedelta(minutes=rule["throttle_minutes"])))
            if recent["n"] >= 1:
                status, reason = "suppressed", f"throttled: rule allows one new standard alert per {rule['throttle_minutes']} min"
        title, body = compose(inc, kind, settings, changed_fields, evidence)
        row = fetch_one(conn, """
            insert into alerts(tenant_id, rule_id, incident_id, incident_version, kind, impact, status, status_reason,
                               dedupe_key, title, body)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, dedupe_key) do nothing returning *""",
                        (inc["tenant_id"], rule["id"], inc["id"], inc["version"], kind, impact, status, reason, dedupe,
                         title, body))
        if not row:
            continue  # duplicate
        if status == "pending_review":
            ensure_review(conn, inc["tenant_id"], "alert", row["id"], f"{impact}-impact {kind} alert: {inc['ref']}",
                          1 if impact == "high" else 3, AGENT)
        created.append(row)
    return created


def _secret(conn: psycopg.Connection, tenant_id: str, name: str, settings: Settings) -> str | None:
    row = fetch_one(conn, "select ciphertext from tenant_secrets where tenant_id = %s and name = %s", (tenant_id, name))
    if not row:
        return None
    try:
        return decrypt_secret(row["ciphertext"], tenant_id=str(tenant_id), name=name, key_b64=settings.app_encryption_key)
    except Exception:  # wrong key / tampering: treat as not configured, never log the value
        log.error("tenant secret could not be decrypted", extra={"tenant_id": str(tenant_id), "secret": name})
        return None


def channel_for(conn: psycopg.Connection, name: str, tenant_id: str, rule: dict, settings: Settings,
                overrides: dict | None = None) -> tuple[object, str]:
    dest = rule["destination"] or {}
    if overrides and name in overrides:
        return overrides[name], dest.get(name) or dest.get("email") or "test"
    if name == "email":
        return EmailChannel(settings), dest.get("email") or ""
    if name == "slack":
        return SlackChannel(), _secret(conn, tenant_id, "slack_webhook_url", settings) or ""
    if name == "telegram":
        token = settings.telegram_bot_token or _secret(conn, tenant_id, "telegram_bot_token", settings)
        return TelegramChannel(token), str(dest.get("telegram_chat_id") or "")
    if name == "webhook":
        return WebhookChannel(_secret(conn, tenant_id, "webhook_signing_secret", settings)), dest.get("webhook_url") or ""
    return MockChannel(), "mock"


def deliver_alert(conn: psycopg.Connection, alert_id: str, settings: Settings | None = None,
                  channel_overrides: dict | None = None) -> dict:
    """Attempt (re)delivery of an approved alert on every channel of its rule."""
    settings = settings or get_settings()
    alert = fetch_one(conn, "select * from alerts where id = %s for update", (alert_id,))
    if alert["status"] not in ("approved", "partially_sent", "failed"):
        return {"alert_id": str(alert_id), "status": alert["status"], "skipped": "not approved for delivery"}
    rule = fetch_one(conn, "select * from alert_rules where id = %s", (alert["rule_id"],))
    results = {}
    for ch_name in rule["channels"]:
        channel, dest = channel_for(conn, ch_name, alert["tenant_id"], rule, settings, channel_overrides)
        d = fetch_one(conn, """
            insert into alert_deliveries(tenant_id, alert_id, channel, destination_masked, status)
            values (%s, %s, %s, %s, 'queued')
            on conflict (alert_id, channel) do update set destination_masked = excluded.destination_masked
            returning *""", (alert["tenant_id"], alert["id"], ch_name, mask(dest, 4) if dest else None))
        if d["status"] in ("sent", "skipped") or d["attempts"] >= MAX_ATTEMPTS:
            results[ch_name] = d["status"]
            continue
        if d["next_attempt_at"] and d["next_attempt_at"] > datetime.now(UTC):
            results[ch_name] = "retrying"
            continue
        msg = Message(subject=alert["title"], text=alert["body"], meta={"alert_id": str(alert["id"]),
                                                                        "incident_id": str(alert["incident_id"]),
                                                                        "kind": alert["kind"]})
        try:
            res: DeliveryResult = channel.send(dest, msg)
        except Exception as exc:  # a channel bug must not lose the alert
            res = DeliveryResult("failed", error=f"{type(exc).__name__}: {exc}"[:300], retryable=True)
        attempts = d["attempts"] + 1
        if res.status == "failed" and res.retryable and attempts < MAX_ATTEMPTS:
            status = "retrying"
            nxt = datetime.now(UTC) + timedelta(minutes=RETRY_MINUTES[min(attempts, len(RETRY_MINUTES)) - 1])
        else:
            status, nxt = res.status, None
        execute(conn, """
            update alert_deliveries set status = %s, attempts = %s, last_error = %s, provider_message_id = %s,
                   next_attempt_at = %s, sent_at = case when %s = 'sent' then now() else sent_at end
            where id = %s""", (status, attempts, res.error, res.provider_message_id, nxt, status, d["id"]))
        results[ch_name] = status
    statuses = set(results.values())
    if statuses == {"sent"}:
        final = "sent"
    elif "sent" in statuses:
        final = "partially_sent"
    elif "retrying" in statuses:
        final = alert["status"]
    else:
        final = "failed"
    reason = None if final == "sent" else "; ".join(f"{k}: {v}" for k, v in results.items())
    execute(conn, """update alerts set status = %s, status_reason = coalesce(%s, status_reason),
                     sent_at = case when %s in ('sent', 'partially_sent') then coalesce(sent_at, now()) else sent_at end
                     where id = %s""", (final, reason, final, alert["id"]))
    audit(conn, tenant_id=str(alert["tenant_id"]), action="alert.delivery", entity_type="alert", entity_id=str(alert["id"]),
          agent=AGENT, details={"results": results, "status": final})
    return {"alert_id": str(alert["id"]), "status": final, "channels": results}


def deliver_pending(conn: psycopg.Connection, tenant_id: str, settings: Settings | None = None,
                    channel_overrides: dict | None = None) -> list[dict]:
    rows = fetch_all(conn, """
        select distinct a.id from alerts a left join alert_deliveries d on d.alert_id = a.id
        where a.tenant_id = %s and (a.status = 'approved'
              or (a.status in ('partially_sent', 'failed') and d.status = 'retrying' and d.next_attempt_at <= now()))""",
                     (tenant_id,))
    return [deliver_alert(conn, r["id"], settings, channel_overrides) for r in rows]
