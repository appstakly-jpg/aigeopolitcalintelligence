"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { actionSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { audit, enqueueJob } from "@/lib/jobs";

type State = { error?: string; ok?: string } | undefined;
const msg = (e: unknown) => (e instanceof z.ZodError ? e.issues.map((i) => i.message).join(" ") : e instanceof Error ? e.message : String(e));

export async function adjustRiskAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst", "reviewer"]);
    const d = z.object({
      id: z.string().uuid(),
      band: z.enum(["", "low", "moderate", "elevated", "high", "severe"]),
      rationale: z.string().trim().max(1000),
    }).parse({ id: form.get("id"), band: form.get("band") ?? "", rationale: form.get("rationale") ?? "" });
    if (d.band && d.rationale.length < 10) throw new Error("Explain the adjustment (at least 10 characters).");
    await withRls(s, async (tx) => {
      const r = await tx`
        update risk_assessments set analyst_band = ${d.band || null}, analyst_rationale = ${d.band ? d.rationale : null},
               analyst_id = ${d.band ? s.userId : null}, status = ${d.band ? "analyst_adjusted" : "computed"}
        where id = ${d.id} returning id`;
      if (!r.length) throw new Error("Assessment not found");
      await audit(tx, s, d.band ? "risk.adjust" : "risk.adjust_cleared", "risk_assessment", d.id, { band: d.band, rationale: d.rationale });
    });
    revalidatePath("/risk");
    return { ok: d.band ? "Analyst band recorded alongside the computed score." : "Analyst adjustment cleared." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function computeRiskAction(): Promise<State> {
  try {
    const s = await actionSession(["admin", "analyst", "reviewer"]);
    const id = await withRls(s, async (tx) => {
      const j = await enqueueJob(tx, s, "compute_risk", {}, { dedupeKey: `compute_risk:${s.tenantId}`, priority: 3 });
      await audit(tx, s, "risk.compute_requested", "job", j ? String(j) : null);
      return j;
    });
    return { ok: id ? `Risk computation queued (job #${id}).` : "A risk computation is already queued." };
  } catch (e) {
    return { error: msg(e) };
  }
}
