"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { actionSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { audit, enqueueJob } from "@/lib/jobs";

type State = { error?: string; ok?: string } | undefined;
const msg = (e: unknown) => (e instanceof z.ZodError ? e.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join(" ") : e instanceof Error ? e.message : String(e));

export async function reviewAlertAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "reviewer"]);
    const d = z.object({ id: z.string().uuid(), decision: z.enum(["approved", "rejected"]), note: z.string().trim().max(500).optional() })
      .parse({ id: form.get("id"), decision: form.get("decision"), note: form.get("note") || undefined });
    await withRls(s, async (tx) => {
      const r = await tx`
        update alerts set status = ${d.decision}, reviewed_by = ${s.userId}, reviewed_at = now(),
               status_reason = ${d.decision === "approved" ? "approved by reviewer" : `rejected by reviewer${d.note ? `: ${d.note}` : ""}`}
        where id = ${d.id} and status = 'pending_review' returning id`;
      if (!r.length) throw new Error("Alert is no longer awaiting review.");
      await tx`update analyst_reviews set status = ${d.decision}, decided_by = ${s.userId}, decided_at = now(), decision_note = ${d.note ?? null}
               where item_type = 'alert' and item_id = ${d.id} and status = 'open'`;
      await audit(tx, s, `alert.${d.decision}`, "alert", d.id, { note: d.note ?? null });
    });
    revalidatePath("/alerts");
    return { ok: d.decision === "approved" ? "Approved. The scheduler delivers approved alerts on its next tick (about a minute)." : "Alert rejected; it will not be sent." };
  } catch (e) {
    return { error: msg(e) };
  }
}

const list = (v: FormDataEntryValue[]) => v.map(String).filter(Boolean);

export async function saveRuleAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession();
    const d = z.object({
      id: z.string().uuid().optional(),
      name: z.string().trim().min(2).max(120),
      countries: z.array(z.string().regex(/^[A-Z]{2}$/)).max(12),
      event_types: z.array(z.string().regex(/^[a-z_]{2,40}$/)).max(30),
      min_severity: z.coerce.number().int().min(1).max(5),
      verification_statuses: z.array(z.enum(["confirmed", "corroborated", "single_source", "disputed", "unverified"])),
      keywords: z.array(z.string().trim().min(2).max(60)).max(20),
      channels: z.array(z.enum(["email", "slack", "telegram", "webhook"])).min(1, "Choose at least one channel"),
      email: z.string().email().max(254).optional(),
      telegram_chat_id: z.string().regex(/^-?\d{3,20}$|^@[A-Za-z0-9_]{5,32}$/).optional(),
      webhook_url: z.string().url().startsWith("https://").max(500).optional(),
      throttle_minutes: z.coerce.number().int().min(0).max(10080),
      notify_updates: z.boolean(),
    }).parse({
      id: form.get("id") || undefined, name: form.get("name"), countries: list(form.getAll("countries")), event_types: list(form.getAll("event_types")),
      min_severity: form.get("min_severity"), verification_statuses: list(form.getAll("verification_statuses")),
      keywords: String(form.get("keywords") ?? "").split(",").map((k) => k.trim()).filter(Boolean), channels: list(form.getAll("channels")),
      email: form.get("email") || undefined, telegram_chat_id: form.get("telegram_chat_id") || undefined, webhook_url: form.get("webhook_url") || undefined,
      throttle_minutes: form.get("throttle_minutes") ?? 60, notify_updates: form.get("notify_updates") === "on",
    });
    if (d.channels.includes("email") && !d.email) throw new Error("Email channel needs an address.");
    if (d.channels.includes("telegram") && !d.telegram_chat_id) throw new Error("Telegram channel needs a chat id.");
    if (d.channels.includes("webhook") && !d.webhook_url) throw new Error("Webhook channel needs an https URL.");
    const destination = { email: d.email ?? null, telegram_chat_id: d.telegram_chat_id ?? null, webhook_url: d.webhook_url ?? null };
    await withRls(s, async (tx) => {
      const [plan] = await tx<{ limits: { alert_rules?: number }; features: Record<string, boolean> }[]>`
        select p.limits, p.features from plans p join tenants t on t.plan_id = p.id where t.id = ${s.tenantId}`;
      for (const ch of ["slack", "telegram"] as const) {
        if (d.channels.includes(ch) && plan && plan.features[ch] === false) throw new Error(`${ch} alerts are not included in your plan.`);
      }
      if (d.id) {
        const r = await tx`
          update alert_rules set name = ${d.name}, countries = ${d.countries}::text[], event_types = ${d.event_types}::text[], min_severity = ${d.min_severity},
                 verification_statuses = ${d.verification_statuses}::text[], keywords = ${d.keywords}::text[], channels = ${d.channels}::text[],
                 destination = ${tx.json(destination)}, throttle_minutes = ${d.throttle_minutes}, notify_updates = ${d.notify_updates}, updated_at = now()
          where id = ${d.id} returning id`;
        if (!r.length) throw new Error("Rule not found");
        await audit(tx, s, "alert_rule.update", "alert_rule", d.id, { name: d.name, channels: d.channels });
      } else {
        const [{ n }] = await tx<{ n: number }[]>`select count(*)::int as n from alert_rules`;
        if (plan?.limits?.alert_rules !== undefined && n >= plan.limits.alert_rules) throw new Error(`Your plan allows ${plan.limits.alert_rules} alert rules.`);
        const [r] = await tx<{ id: string }[]>`
          insert into alert_rules(tenant_id, user_id, name, countries, event_types, min_severity, verification_statuses, keywords, channels,
                                  destination, throttle_minutes, notify_updates)
          values (${s.tenantId}, ${s.userId}, ${d.name}, ${d.countries}::text[], ${d.event_types}::text[], ${d.min_severity},
                  ${d.verification_statuses}::text[], ${d.keywords}::text[], ${d.channels}::text[], ${tx.json(destination)}, ${d.throttle_minutes}, ${d.notify_updates})
          returning id`;
        await audit(tx, s, "alert_rule.create", "alert_rule", r.id, { name: d.name, channels: d.channels });
      }
    });
    revalidatePath("/alerts");
    return { ok: "Rule saved." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function toggleRuleAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession();
    const d = z.object({ id: z.string().uuid(), op: z.enum(["enable", "disable", "delete"]) }).parse({ id: form.get("id"), op: form.get("op") });
    await withRls(s, async (tx) => {
      const r = d.op === "delete" ? await tx`delete from alert_rules where id = ${d.id} returning id`
        : await tx`update alert_rules set enabled = ${d.op === "enable"}, updated_at = now() where id = ${d.id} returning id`;
      if (!r.length) throw new Error("Rule not found");
      await audit(tx, s, `alert_rule.${d.op}`, "alert_rule", d.id);
    });
    revalidatePath("/alerts");
    return { ok: `Rule ${d.op}d.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function testRuleAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst", "reviewer"]);
    const id = z.string().uuid().parse(form.get("id"));
    const j = await withRls(s, async (tx) => {
      const job = await enqueueJob(tx, s, "test_notification", { rule_id: id }, { dedupeKey: `test:${id}`, priority: 2 });
      await audit(tx, s, "alert_rule.test", "alert_rule", id);
      return job;
    });
    return { ok: j ? `Test queued (job #${j}). Its per-channel result (sent, failed, or skipped because credentials are missing) appears under Recent jobs in Administration.` : "A test is already queued." };
  } catch (e) {
    return { error: msg(e) };
  }
}
