"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { actionSession } from "@/lib/auth";
import { withRls, type Tx } from "@/lib/db";
import { audit, enqueueJob } from "@/lib/jobs";
import { removeAnalystOverrides, setAnalystOverrides, type Overrides } from "@/lib/overrides";

export type ActionState = { error?: string; ok?: string } | undefined;

const id = z.string().uuid();
const note = z.string().trim().min(3, "Add a short note explaining the decision.").max(1000);

async function revision(tx: Tx, incidentId: string, changeType: string, changeNote: string, fields: string[], userId: string) {
  await tx`
    insert into incident_revisions(tenant_id, incident_id, version, change_type, change_note, changed_fields, snapshot, changed_by)
    select i.tenant_id, i.id, i.version, ${changeType}, ${changeNote}, ${fields}::text[],
           to_jsonb(i) - 'search' - 'verification_detail' - 'context', ${userId}
    from incidents i where i.id = ${incidentId}`;
}

function fail(e: unknown): ActionState {
  const msg = e instanceof z.ZodError ? e.issues.map((i) => i.message).join(" ") : e instanceof Error ? e.message : String(e);
  return { error: msg.includes("42501") || /only reviewers|permission denied|row-level security/i.test(msg) ? `Not permitted: ${msg}` : msg };
}

export async function reviewIncidentAction(_p: ActionState, form: FormData): Promise<ActionState> {
  try {
    const s = await actionSession(["admin", "reviewer"]);
    const data = z.object({ id, decision: z.enum(["approved", "rejected", "changes_requested"]), note })
      .parse({ id: form.get("id"), decision: form.get("decision"), note: form.get("note") });
    await withRls(s, async (tx) => {
      const r = await tx`update incidents set review_status = ${data.decision}, version = version + 1 where id = ${data.id} returning id`;
      if (!r.length) throw new Error("Incident not found");
      await revision(tx, data.id, "reviewed", `${data.decision}: ${data.note}`, ["review_status"], s.userId);
      await tx`update analyst_reviews set status = ${data.decision}, decided_by = ${s.userId}, decided_at = now(), decision_note = ${data.note}
               where item_type = 'incident' and item_id = ${data.id} and status = 'open'`;
      await audit(tx, s, `incident.review.${data.decision}`, "incident", data.id, { note: data.note });
    });
    revalidatePath(`/incidents/${data.id}`);
    return { ok: `Review recorded: ${data.decision.replace("_", " ")}.` };
  } catch (e) {
    return fail(e);
  }
}

export async function confirmIncidentAction(_p: ActionState, form: FormData): Promise<ActionState> {
  try {
    const s = await actionSession(["admin", "reviewer"]);
    const data = z.object({ id, note }).parse({ id: form.get("id"), note: form.get("note") });
    await withRls(s, async (tx) => {
      const r = await tx`
        update incidents set verification_status = 'confirmed', version = version + 1,
               verification_detail = jsonb_set(coalesce(verification_detail, '{}'), '{analyst_confirmed}', 'true'::jsonb)
                                     || jsonb_build_object('analyst_confirmation', jsonb_build_object('by', ${s.userId}::text, 'note', ${data.note}::text, 'at', now()))
        where id = ${data.id} and independent_source_count >= 1 returning id`;
      if (!r.length) throw new Error("Incident not found or has no evidence to confirm against.");
      await revision(tx, data.id, "verification_changed", `Analyst confirmed: ${data.note}`, ["verification_status"], s.userId);
      await audit(tx, s, "incident.confirm", "incident", data.id, { note: data.note });
    });
    revalidatePath(`/incidents/${data.id}`);
    return { ok: "Marked as confirmed by an analyst." };
  } catch (e) {
    return fail(e);
  }
}

const optInt = z.preprocess((v) => (v === "" || v === null ? undefined : Number(v)), z.number().int().min(0).max(100000).optional());

export async function editIncidentAction(_p: ActionState, form: FormData): Promise<ActionState> {
  try {
    const s = await actionSession(["admin", "analyst"]);
    const data = z.object({
      id, note,
      title: z.string().trim().max(300).optional(),
      summary: z.string().trim().max(4000).optional(),
      severity: z.preprocess((v) => (v === "" ? undefined : Number(v)), z.number().int().min(1).max(5).optional()),
      severity_reason: z.string().trim().max(500).optional(),
      location: z.string().regex(/^[a-z0-9_:-]{1,80}$/i).optional().or(z.literal("")),
      killed: optInt, injured: optInt,
      categories: z.array(z.string().regex(/^[a-z_]{2,40}$/)).max(6),
      reset: z.enum(["on"]).optional(), // form field "remove_overrides"
    }).parse({
      id: form.get("id"), note: form.get("note"), title: form.get("title") || undefined, summary: form.get("summary") || undefined,
      severity: form.get("severity") ?? "", severity_reason: form.get("severity_reason") || undefined, location: form.get("location") ?? "",
      killed: form.get("killed") ?? "", injured: form.get("injured") ?? "", categories: form.getAll("categories").map(String),
      // Not named "reset": a form control with that name shadows HTMLFormElement.reset(), which React calls.
      reset: form.get("remove_overrides") || undefined,
    });
    if (data.severity && !data.severity_reason) throw new Error("A severity override needs a reason.");
    const result = await withRls(s, async (tx) => {
      const [cur] = await tx<{ verification_detail: Record<string, unknown> | null }[]>`
        select verification_detail from incidents where id = ${data.id} for update`;
      if (!cur) throw new Error("Incident not found");
      // Publisher corrections share the overrides object; lib/overrides.ts keeps the two apart.
      const prev = (cur.verification_detail?.overrides as Overrides | undefined) ?? {};
      const edits: Record<string, Overrides> = {};
      if (!data.reset) {
        if (data.title) edits.title = { title: data.title };
        if (data.summary) edits.summary = { summary: data.summary };
        if (data.severity) edits.severity = { severity: data.severity, severity_reason: data.severity_reason };
        if (data.categories.length) {
          const known = await tx<{ key: string }[]>`select distinct key from event_types where key = any(${data.categories}::text[]) and enabled`;
          if (known.length !== data.categories.length) throw new Error("Unknown or disabled category.");
          edits.categories = { categories: data.categories };
        }
        if (data.location) {
          const [g] = await tx<{ id: string; name: string; kind: string; lat: number | null; lon: number | null; country_code: string | null; precision_km: string | null }[]>`
            select id, name, kind, lat, lon, country_code, precision_km from geo_entities where id = ${data.location}`;
          if (!g) throw new Error("Unknown location.");
          edits.location = { location: { entity_id: g.id, name: g.name, lat: g.lat, lon: g.lon, precision: g.kind, confidence: 0.9,
            source: `analyst:gazetteer:${g.id} (±${Number(g.precision_km ?? 0)} km)`, country_code: g.country_code } };
        }
        if (data.killed !== undefined || data.injured !== undefined) {
          edits.casualties = { casualties: [data.killed ?? null, data.injured ?? null], casualty_note: `Casualty figures set by analyst: ${data.note}` };
        }
        if (!Object.keys(edits).length) throw new Error("Nothing to change: fill in at least one field, or tick the box to remove analyst overrides.");
      }
      const overrides = data.reset ? removeAnalystOverrides(prev) : setAnalystOverrides(prev, edits);
      await tx`update incidents set verification_detail = jsonb_set(coalesce(verification_detail, '{}'), '{overrides}', ${tx.json(overrides as never)})
               where id = ${data.id}`;
      await audit(tx, s, data.reset ? "incident.overrides_reset" : "incident.edit", "incident", data.id, { note: data.note, overrides });
      return enqueueJob(tx, s, "recompute_incident", { incident_id: data.id, note: data.note }, { priority: 2 });
    });
    revalidatePath(`/incidents/${data.id}`);
    return { ok: `Correction saved${result ? ` and queued for re-derivation (job #${result})` : " (a re-derivation is already queued)"}. Severity, review state and correction alerts update when the worker runs it.` };
  } catch (e) {
    return fail(e);
  }
}

export async function setStatusAction(_p: ActionState, form: FormData): Promise<ActionState> {
  try {
    const s = await actionSession(["admin", "analyst"]);
    const data = z.object({ id, status: z.enum(["active", "closed"]), note })
      .parse({ id: form.get("id"), status: form.get("status"), note: form.get("note") });
    await withRls(s, async (tx) => {
      const r = await tx`update incidents set status = ${data.status}, version = version + 1 where id = ${data.id} and status in ('active', 'closed') returning id`;
      if (!r.length) throw new Error("Incident not found or merged");
      await revision(tx, data.id, "status_changed", `${data.status}: ${data.note}`, ["status"], s.userId);
      await audit(tx, s, `incident.${data.status}`, "incident", data.id, { note: data.note });
    });
    revalidatePath(`/incidents/${data.id}`);
    return { ok: `Incident ${data.status === "closed" ? "closed" : "reopened"}.` };
  } catch (e) {
    return fail(e);
  }
}
