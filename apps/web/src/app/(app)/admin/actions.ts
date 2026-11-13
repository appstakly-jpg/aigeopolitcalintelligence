"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { actionSession } from "@/lib/auth";
import { hashPassword } from "@/lib/crypto";
import { sql, withRls } from "@/lib/db";
import { audit } from "@/lib/jobs";

type State = { error?: string; ok?: string } | undefined;
const msg = (e: unknown) => (e instanceof z.ZodError ? e.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join(" ") : e instanceof Error ? e.message : String(e));
const ROLES = ["admin", "analyst", "reviewer", "client"] as const;

export async function addMemberAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin"]);
    const d = z.object({
      email: z.string().email().max(254).transform((v) => v.toLowerCase()),
      display_name: z.string().trim().max(120).optional(),
      role: z.enum(ROLES),
      password: z.string().min(12, "Temporary password must be at least 12 characters").max(200),
    }).parse({ email: form.get("email"), display_name: form.get("display_name") || undefined, role: form.get("role"), password: form.get("password") });
    await withRls(s, async (tx) => {
      const [plan] = await tx<{ limits: { seats?: number } }[]>`select p.limits from plans p join tenants t on t.plan_id = p.id where t.id = ${s.tenantId}`;
      const [{ n }] = await tx<{ n: number }[]>`select count(*)::int as n from memberships`;
      if (plan?.limits?.seats !== undefined && n >= plan.limits.seats) throw new Error(`All ${plan.limits.seats} seats on your plan are in use.`);
    });
    // Creating a login is a privileged operation (app_users is not writable under RLS); the admin check above gates it.
    const hash = await hashPassword(d.password);
    await sql().begin(async (tx) => {
      const [u] = await tx<{ id: string; created: boolean }[]>`
        insert into app_users(email, display_name, password_hash, auth_provider) values (${d.email}, ${d.display_name ?? null}, ${hash}, 'local')
        on conflict (email) do update set email = excluded.email
        returning id, (xmax = 0) as created`;
      const existing = await tx`select 1 from memberships where tenant_id = ${s.tenantId} and user_id = ${u.id}`;
      if (existing.length) throw new Error("That user is already a member.");
      await tx`insert into memberships(tenant_id, user_id, role) values (${s.tenantId}, ${u.id}, ${d.role})`;
      await tx`insert into audit_log(tenant_id, actor_user_id, action, entity_type, entity_id, details)
               values (${s.tenantId}, ${s.userId}, 'member.add', 'user', ${u.id}, ${tx.json({ email: d.email, role: d.role, new_login: u.created })})`;
    });
    revalidatePath("/admin");
    return { ok: `Added ${d.email} as ${d.role}. Share the temporary password through a separate channel; they can change it and enable MFA in Settings.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function memberRoleAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin"]);
    const d = z.object({ user_id: z.string().uuid(), role: z.enum([...ROLES, "remove"]) }).parse({ user_id: form.get("user_id"), role: form.get("role") });
    await withRls(s, async (tx) => {
      const [{ admins }] = await tx<{ admins: number }[]>`select count(*)::int as admins from memberships where role = 'admin' and user_id <> ${d.user_id}`;
      if (admins === 0 && d.role !== "admin") throw new Error("An organisation needs at least one admin.");
      const r = d.role === "remove" ? await tx`delete from memberships where user_id = ${d.user_id} returning user_id`
        : await tx`update memberships set role = ${d.role} where user_id = ${d.user_id} returning user_id`;
      if (!r.length) throw new Error("Member not found");
      await audit(tx, s, d.role === "remove" ? "member.remove" : "member.role", "user", d.user_id, { role: d.role });
    });
    revalidatePath("/admin");
    return { ok: d.role === "remove" ? "Member removed." : `Role changed to ${d.role}.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

const Policy = z.object({
  timezone: z.string().refine((tz) => { try { new Intl.DateTimeFormat("en", { timeZone: tz }); return true; } catch { return false; } }, "Unknown time zone"),
  daily_brief_hour: z.coerce.number().int().min(0).max(23),
  incident_min_severity: z.coerce.number().int().min(1).max(5),
  incident_unverified_min_severity: z.coerce.number().int().min(1).max(5),
  alert_high_impact_min_severity: z.coerce.number().int().min(1).max(5),
  high_impact_requires_review: z.boolean(),
  auto_send_standard_alerts: z.boolean(),
  corroboration_min_independent: z.coerce.number().int().min(2).max(5),
  monthly_budget_usd: z.coerce.number().min(0).max(100000),
});

export async function savePoliciesAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin"]);
    const d = Policy.parse({
      ...Object.fromEntries(["timezone", "daily_brief_hour", "incident_min_severity", "incident_unverified_min_severity", "alert_high_impact_min_severity",
        "corroboration_min_independent", "monthly_budget_usd"].map((k) => [k, form.get(k)])),
      high_impact_requires_review: form.get("high_impact_requires_review") === "on",
      auto_send_standard_alerts: form.get("auto_send_standard_alerts") === "on",
    });
    await withRls(s, async (tx) => {
      const patch = {
        timezone: d.timezone,
        reports: { daily_brief_hour: d.daily_brief_hour },
        review_policy: { incident_min_severity: d.incident_min_severity, incident_unverified_min_severity: d.incident_unverified_min_severity,
          alert_high_impact_min_severity: d.alert_high_impact_min_severity, high_impact_requires_review: d.high_impact_requires_review,
          auto_send_standard_alerts: d.auto_send_standard_alerts },
        verification_policy: { corroboration_min_independent: d.corroboration_min_independent, confirmed_requires_analyst: true },
        llm: { monthly_budget_usd: d.monthly_budget_usd },
      };
      await tx`update tenants set settings = settings || ${tx.json(patch)}, updated_at = now() where id = ${s.tenantId}`;
      await audit(tx, s, "tenant.policies", "tenant", s.tenantId, { ...patch, warning: d.high_impact_requires_review ? null : "high-impact alerts no longer require review" });
    });
    revalidatePath("/admin");
    return { ok: d.high_impact_requires_review ? "Policies saved." : "Policies saved. Warning: high-impact alerts will now be sent without human review." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function saveEventTypeAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin"]);
    const words = (v: FormDataEntryValue | null) => String(v ?? "").split(",").map((w) => w.trim()).filter(Boolean).slice(0, 60);
    const d = z.object({
      key: z.string().regex(/^[a-z][a-z_]{1,39}$/, "Key: lowercase letters and underscores"),
      label: z.string().trim().min(2).max(80),
      label_ar: z.string().trim().max(80).optional(),
      parent_key: z.string().regex(/^[a-z_]{2,40}$/).optional(),
      default_severity: z.coerce.number().int().min(1).max(5),
      keywords_en: z.array(z.string().max(60)), keywords_ar: z.array(z.string().max(60)),
      review_required: z.boolean(), enabled: z.boolean(),
    }).parse({
      key: form.get("key"), label: form.get("label"), label_ar: form.get("label_ar") || undefined, parent_key: form.get("parent_key") || undefined,
      default_severity: form.get("default_severity"), keywords_en: words(form.get("keywords_en")), keywords_ar: words(form.get("keywords_ar")),
      review_required: form.get("review_required") === "on", enabled: form.get("enabled") === "on",
    });
    await withRls(s, async (tx) => {
      const [base] = await tx<{ risk_dimensions: string[]; description: string | null }[]>`
        select risk_dimensions, description from event_types where key = ${d.key} and tenant_id is null`;
      await tx`
        insert into event_types(tenant_id, key, label, label_ar, parent_key, description, default_severity, risk_dimensions, keywords_en, keywords_ar, review_required, enabled)
        values (${s.tenantId}, ${d.key}, ${d.label}, ${d.label_ar ?? null}, ${d.parent_key ?? null}, ${base?.description ?? null}, ${d.default_severity},
                ${base?.risk_dimensions ?? []}::text[], ${d.keywords_en}::text[], ${d.keywords_ar}::text[], ${d.review_required}, ${d.enabled})
        on conflict (tenant_id, key) do update set label = excluded.label, label_ar = excluded.label_ar, parent_key = excluded.parent_key,
          default_severity = excluded.default_severity, keywords_en = excluded.keywords_en, keywords_ar = excluded.keywords_ar,
          review_required = excluded.review_required, enabled = excluded.enabled`;
      await audit(tx, s, "taxonomy.save", "event_type", d.key, { ...d, overrides_platform_default: !!base });
    });
    revalidatePath("/admin");
    return { ok: `Category '${d.key}' saved for your organisation. New articles are classified with it from the next pipeline run.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function testPlanChangeAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin"]);
    if ((process.env.BILLING_MODE ?? "test") !== "test") throw new Error("Plan changes go through the billing provider when BILLING_MODE is not 'test'.");
    const plan = z.string().regex(/^[a-z][a-z0-9_-]{1,31}$/).parse(form.get("plan_id"));
    // tenants.plan_id is not writable under RLS (it is a billing decision); in test mode the server applies it directly.
    await sql().begin(async (tx) => {
      const [p] = await tx`select id from plans where id = ${plan} and is_active`;
      if (!p) throw new Error("Unknown plan");
      await tx`update tenants set plan_id = ${plan}, updated_at = now() where id = ${s.tenantId}`;
      await tx`insert into audit_log(tenant_id, actor_user_id, action, entity_type, entity_id, details)
               values (${s.tenantId}, ${s.userId}, 'billing.test_plan_change', 'tenant', ${s.tenantId}, ${tx.json({ plan, charged: false, mode: "test" })})`;
    });
    revalidatePath("/admin");
    return { ok: `Plan switched to '${plan}' in billing test mode. No payment was taken.` };
  } catch (e) {
    return { error: msg(e) };
  }
}
