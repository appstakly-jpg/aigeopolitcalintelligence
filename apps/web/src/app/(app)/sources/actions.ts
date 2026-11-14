"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { actionSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { audit, enqueueJob } from "@/lib/jobs";

type State = { error?: string; ok?: string } | undefined;
const msg = (e: unknown) => {
  const m = e instanceof z.ZodError ? e.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join(" ") : e instanceof Error ? e.message : String(e);
  if (/enabled_requires_approval/.test(m)) return "A source must be approved before it can be enabled.";
  if (/prohibited_never_enabled/.test(m)) return "A source whose licence prohibits collection cannot be enabled.";
  return m;
};

export async function sourceDecisionAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin"]);
    const d = z.object({
      id: z.string().uuid(), op: z.enum(["approve", "enable", "disable", "reject", "retire"]), note: z.string().trim().max(500).optional(),
      limited_use: z.literal("yes").optional(),
    }).parse({ id: form.get("id"), op: form.get("op"), note: form.get("note") || undefined, limited_use: form.get("limited_use") || undefined });
    let limitedUse = false;
    await withRls(s, async (tx) => {
      const [src] = await tx<{ status: string; license_type: string; usage_policy: string; max_excerpt_chars: number }[]>`
        select status, license_type, usage_policy, max_excerpt_chars from sources where id = ${d.id}`;
      if (!src) throw new Error("Source not found");
      if ((d.op === "approve" || d.op === "enable") && src.license_type === "review_required") {
        // An unreviewed licence allows limited use only: headlines, links and short excerpts.
        if (src.usage_policy === "full_text") {
          throw new Error("Full text needs a recorded licence. Set usage to headline, link and excerpt (or metadata only), or record the licence first.");
        }
        if (d.op === "approve" && d.limited_use !== "yes") {
          throw new Error("This source's licence has not been reviewed. Confirm limited use (headlines, links and short excerpts) to approve it.");
        }
        limitedUse = true;
      }
      if (d.op === "approve") await tx`update sources set status = 'approved', updated_at = now() where id = ${d.id}`;
      if (d.op === "enable") await tx`update sources set enabled = true, next_fetch_at = now(), updated_at = now() where id = ${d.id}`;
      if (d.op === "disable") await tx`update sources set enabled = false, updated_at = now() where id = ${d.id}`;
      if (d.op === "reject" || d.op === "retire") {
        await tx`update sources set enabled = false, status = ${d.op === "reject" ? "rejected" : "retired"}, updated_at = now() where id = ${d.id}`;
      }
      await tx`update analyst_reviews set status = ${d.op === "reject" ? "rejected" : "approved"}, decided_by = ${s.userId}, decided_at = now(), decision_note = ${d.note ?? d.op}
               where item_type = 'source' and item_id = ${d.id} and status = 'open' and ${d.op !== "disable"}`;
      await audit(tx, s, `source.${d.op}`, "source", d.id, {
        note: d.note ?? null,
        ...(limitedUse ? { limited_use: `licence review pending; ${src.usage_policy}, excerpts up to ${src.max_excerpt_chars} characters` } : {}),
      });
    });
    revalidatePath("/sources");
    return { ok: `Source ${d.op === "approve" ? "approved" : d.op + "d"}.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function updateSourceAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst"]);
    const d = z.object({
      id: z.string().uuid(),
      reliability: z.enum(["A", "B", "C", "D", "E", "F"]),
      reliability_rationale: z.string().trim().min(5, "Explain the reliability grade").max(500),
      fetch_interval_minutes: z.coerce.number().int().min(5).max(1440),
      max_excerpt_chars: z.coerce.number().int().min(0).max(2000),
      usage_policy: z.enum(["metadata_only", "headline_link_excerpt", "full_text"]),
      license_type: z.enum(["public_domain", "open_license", "attribution", "licensed", "review_required", "prohibited", "synthetic"]),
      license_notes: z.string().trim().max(1000).optional(),
      // Outlets of one newsroom or network share an origin group so they count once for corroboration.
      origin_group: z.string().trim().toLowerCase().regex(/^[a-z0-9][a-z0-9_.-]{1,60}$/, "Origin group: lower-case letters, digits, '.', '_' or '-'").optional(),
    }).parse(Object.fromEntries(["id", "reliability", "reliability_rationale", "fetch_interval_minutes", "max_excerpt_chars", "usage_policy", "license_type", "license_notes", "origin_group"]
      .map((k) => [k, form.get(k) || undefined])));
    if (d.license_type === "review_required" && d.usage_policy === "full_text") {
      throw new Error("Full text needs a recorded licence: choose a licence type, or keep usage to headline, link and excerpt.");
    }
    await withRls(s, async (tx) => {
      const r = await tx`
        update sources set reliability = ${d.reliability}, reliability_rationale = ${d.reliability_rationale},
               fetch_interval_minutes = ${d.fetch_interval_minutes}, max_excerpt_chars = ${d.max_excerpt_chars}, usage_policy = ${d.usage_policy},
               license_type = ${d.license_type}, license_notes = ${d.license_notes ?? null}, origin_group = ${d.origin_group ?? null}, updated_at = now()
        where id = ${d.id} returning id`;
      if (!r.length) throw new Error("Source not found");
      await audit(tx, s, "source.update", "source", d.id, { ...d });
    });
    revalidatePath("/sources");
    return { ok: "Source updated." };
  } catch (e) {
    const m = msg(e);
    return { error: /42501|only tenant admins/.test(m) ? "Only admins may change a source's licence." : m };
  }
}

export async function sourceJobAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst", "reviewer"]);
    const d = z.object({ op: z.enum(["fetch_source", "verify_source", "run_pipeline", "source_scout"]), id: z.string().uuid().optional() })
      .parse({ op: form.get("op"), id: form.get("id") || undefined });
    if ((d.op === "fetch_source" || d.op === "verify_source") && !d.id) throw new Error("Missing source");
    const j = await withRls(s, async (tx) => {
      const payload = d.id ? { source_id: d.id } : d.op === "run_pipeline" ? { force: false } : {};
      const job = await enqueueJob(tx, s, d.op, payload, { dedupeKey: `${d.op}:${d.id ?? s.tenantId}`, priority: 3 });
      await audit(tx, s, `source.${d.op}_requested`, "source", d.id ?? null);
      return job;
    });
    return { ok: j ? `Queued (job #${j}).` : "Already queued." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function addSourceAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst"]);
    const catalogKey = String(form.get("catalog_key") || "");
    const id = await withRls(s, async (tx) => {
      const [plan] = await tx<{ limits: { sources?: number }; features: { custom_sources?: boolean } }[]>`
        select p.limits, p.features from plans p join tenants t on t.plan_id = p.id where t.id = ${s.tenantId}`;
      const [{ n }] = await tx<{ n: number }[]>`select count(*)::int as n from sources where status <> 'retired'`;
      if (plan?.limits?.sources !== undefined && n >= plan.limits.sources) throw new Error(`Your plan allows ${plan.limits.sources} sources.`);
      if (catalogKey) {
        const r = await tx<{ id: string }[]>`
          insert into sources(tenant_id, catalog_key, name, publisher, adapter, url, homepage_url, language, country_focus, source_type, ownership,
                              reliability, reliability_rationale, origin_group, license_type, license_notes, terms_url, usage_policy,
                              fetch_interval_minutes, priority, status, enabled, verified_at, verification_note, created_by)
          select ${s.tenantId}, c.key, c.name, c.publisher, c.adapter, c.url, c.homepage_url, c.language, c.country_focus, c.source_type, c.ownership,
                 c.reliability, c.reliability_rationale, c.origin_group, c.license_type, c.license_notes, c.terms_url, c.usage_policy,
                 c.fetch_interval_minutes, c.priority, 'proposed', false, c.verified_at, c.verification_note, ${s.userId}
          from source_catalog c where c.key = ${catalogKey}
            and not exists (select 1 from sources x where x.catalog_key = c.key)
          returning id`;
        if (!r.length) throw new Error("Catalog entry not found or already added.");
        await audit(tx, s, "source.add_from_catalog", "source", r[0].id, { catalog_key: catalogKey });
        return r[0].id;
      }
      if (!plan?.features?.custom_sources) throw new Error("Custom sources are not included in your plan.");
      const d = z.object({
        name: z.string().trim().min(2).max(160), publisher: z.string().trim().min(2).max(160),
        url: z.string().url().regex(/^https:\/\//, "Use an https feed URL"), adapter: z.enum(["rss", "json_api"]),
        language: z.enum(["en", "ar"]), source_type: z.enum(["news_media", "wire_agency", "state_media", "government", "intl_org", "think_tank", "ngo"]),
        terms_url: z.string().url().optional(),
      }).parse(Object.fromEntries(["name", "publisher", "url", "adapter", "language", "source_type", "terms_url"].map((k) => [k, form.get(k) || undefined])));
      const [r] = await tx<{ id: string }[]>`
        insert into sources(tenant_id, name, publisher, adapter, url, language, source_type, reliability, reliability_rationale, license_type,
                            terms_url, usage_policy, status, enabled, created_by)
        values (${s.tenantId}, ${d.name}, ${d.publisher}, ${d.adapter}, ${d.url}, ${d.language}, ${d.source_type}, 'F',
                'Not yet assessed (custom source).', 'review_required', ${d.terms_url ?? null}, 'headline_link_excerpt', 'proposed', false, ${s.userId})
        returning id`;
      await tx`insert into analyst_reviews(tenant_id, item_type, item_id, reason, priority, created_by_agent)
               values (${s.tenantId}, 'source', ${r.id}, 'Custom source proposed: verify robots.txt, terms and licence before approval.', 3, 'web')`;
      await audit(tx, s, "source.add_custom", "source", r.id, { url: d.url });
      await enqueueJob(tx, s, "verify_source", { source_id: r.id }, { dedupeKey: `verify_source:${r.id}` });
      return r.id;
    });
    revalidatePath("/sources");
    return { ok: `Source added as 'proposed' (${id.slice(0, 8)}). An admin must review its licence and approve it before anything is collected.` };
  } catch (e) {
    return { error: msg(e) };
  }
}
