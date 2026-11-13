"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { actionSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { audit, enqueueJob } from "@/lib/jobs";
import { changedSinceGenerated, describeChanges, type ChangedIncident } from "@/lib/reports";

type State = { error?: string; ok?: string } | undefined;
const msg = (e: unknown) => {
  const m = e instanceof z.ZodError ? e.issues.map((i) => i.message).join(" ") : e instanceof Error ? e.message : String(e);
  return /citation integrity/i.test(m) ? "Publication blocked: the current version failed the citation integrity check. Regenerate or fix the evidence first." : m;
};

const TYPES = ["breaking", "daily_gcc", "country_daily", "weekly_outlook", "monthly_assessment", "maritime_energy", "custom"] as const;

export async function generateReportAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst"]);
    const d = z.object({
      report_type: z.enum(TYPES),
      country_code: z.string().regex(/^[A-Z]{2}$/).optional(),
      period_start: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
      period_end: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
    }).parse({
      report_type: form.get("report_type"), country_code: form.get("country_code") || undefined,
      period_start: form.get("period_start") || undefined, period_end: form.get("period_end") || undefined,
    });
    if (d.report_type === "country_daily" && !d.country_code) throw new Error("Choose a country for a country brief.");
    if (d.report_type === "custom" && (!d.period_start || !d.period_end || d.period_start >= d.period_end)) {
      throw new Error("A custom report needs a start date before its end date.");
    }
    const payload: Record<string, unknown> = { report_type: d.report_type };
    if (d.country_code) payload.country_code = d.country_code;
    if (d.report_type === "custom") Object.assign(payload, { period_start: `${d.period_start}T00:00:00+00:00`, period_end: `${d.period_end}T23:59:59+00:00` });
    const id = await withRls(s, async (tx) => {
      const [plan] = await tx<{ limits: { reports_per_day?: number } }[]>`select p.limits from plans p join tenants t on t.plan_id = p.id where t.id = ${s.tenantId}`;
      const [{ n }] = await tx<{ n: number }[]>`select count(*)::int as n from reports where created_at >= date_trunc('day', now())`;
      if (plan?.limits?.reports_per_day !== undefined && n >= plan.limits.reports_per_day) {
        throw new Error(`Your plan allows ${plan.limits.reports_per_day} reports per day; the limit has been reached.`);
      }
      const j = await enqueueJob(tx, s, "generate_report", payload, { priority: 3 });
      await audit(tx, s, "report.generate_requested", "job", j ? String(j) : null, payload);
      return j;
    });
    return { ok: `Report generation queued (job #${id}). It appears below as soon as the worker finishes; it is built only from stored, verified records.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function regenerateReportAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst"]);
    const d = z.object({ id: z.string().uuid(), note: z.string().trim().min(3).max(500) }).parse({ id: form.get("id"), note: form.get("note") });
    const j = await withRls(s, async (tx) => {
      const [r] = await tx<{ report_type: string; country_code: string | null; status: string }[]>`select report_type, country_code, status from reports where id = ${d.id}`;
      if (!r) throw new Error("Report not found");
      if (r.status === "published" || r.status === "archived") throw new Error("Published or archived reports are not regenerated; generate a new report instead.");
      const job = await enqueueJob(tx, s, "generate_report", { report_type: r.report_type, country_code: r.country_code, report_id: d.id, change_note: d.note },
        { dedupeKey: `regen:${d.id}`, priority: 3 });
      await audit(tx, s, "report.regenerate_requested", "report", d.id, { note: d.note });
      return job;
    });
    return { ok: j ? `New version queued (job #${j}).` : "A regeneration is already queued." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function setReportStatusAction(_p: State, form: FormData): Promise<State> {
  try {
    const d = z.object({ id: z.string().uuid(), to: z.enum(["draft", "in_review", "published", "archived"]), note: z.string().trim().max(500).optional(),
      acknowledge: z.enum(["on"]).optional() })
      .parse({ id: form.get("id"), to: form.get("to"), note: form.get("note") || undefined, acknowledge: form.get("acknowledge_changes") || undefined });
    const s = await actionSession(d.to === "published" || d.to === "archived" ? ["admin", "reviewer"] : ["admin", "analyst", "reviewer"]);
    await withRls(s, async (tx) => {
      const [r] = await tx<{ status: string; current_version: number }[]>`select status, current_version from reports where id = ${d.id} for update`;
      if (!r) throw new Error("Report not found");
      const allowed: Record<string, string[]> = { draft: ["in_review", "archived"], in_review: ["draft", "published", "archived"], published: ["archived"], archived: [] };
      if (!allowed[r.status]?.includes(d.to)) throw new Error(`Cannot move a ${r.status.replace("_", " ")} report to ${d.to.replace("_", " ")}.`);
      let changed: ChangedIncident[] = [];
      if (d.to === "published") {
        // Evidence may have moved on since this version was written: make the reviewer decide knowingly.
        changed = await changedSinceGenerated(tx, d.id, r.current_version);
        if (changed.length && !d.acknowledge) {
          throw new Error(`Cited incidents changed after this version was generated: ${describeChanges(changed)}. Regenerate the report, or confirm you have checked the changes.`);
        }
        await tx`update reports set status = 'published', published_at = now(), published_by = ${s.userId} where id = ${d.id}`;
        await tx`update analyst_reviews set status = 'approved', decided_by = ${s.userId}, decided_at = now(), decision_note = ${d.note ?? "published"}
                 where item_type = 'report' and item_id = ${d.id} and status = 'open'`;
      } else {
        await tx`update reports set status = ${d.to} where id = ${d.id}`;
      }
      await audit(tx, s, `report.${d.to}`, "report", d.id, { from: r.status, note: d.note ?? null,
        ...(changed.length ? { acknowledged_changes: changed.map((c) => ({ ref: c.ref, cited_version: c.cited_version, version: c.version })) } : {}) });
    });
    revalidatePath(`/reports/${d.id}`);
    return { ok: `Report moved to ${d.to.replace("_", " ")}.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function exportReportAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst", "reviewer"]);
    const d = z.object({ id: z.string().uuid(), format: z.enum(["html", "docx", "pdf"]), version: z.coerce.number().int().min(1) })
      .parse({ id: form.get("id"), format: form.get("format"), version: form.get("version") });
    const j = await withRls(s, async (tx) => {
      if (d.format === "pdf") {
        const [p] = await tx<{ features: { pdf_export?: boolean } }[]>`select p.features from plans p join tenants t on t.plan_id = p.id where t.id = ${s.tenantId}`;
        if (!p?.features?.pdf_export) throw new Error("PDF export is not included in your plan.");
      }
      const job = await enqueueJob(tx, s, "export_report", { report_id: d.id, format: d.format, version: d.version },
        { dedupeKey: `export:${d.id}:${d.version}:${d.format}`, priority: 4 });
      await audit(tx, s, "report.export_requested", "report", d.id, { format: d.format, version: d.version });
      return job;
    });
    revalidatePath(`/reports/${d.id}`);
    return { ok: j ? `${d.format.toUpperCase()} export queued (job #${j}); refresh in a moment to download it.` : "That export is already queued." };
  } catch (e) {
    return { error: msg(e) };
  }
}
