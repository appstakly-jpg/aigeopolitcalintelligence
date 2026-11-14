import type { Session } from "./auth";
import type { Tx } from "./db";

// Job kinds a signed-in staff member may request (mirrors the jobs_request RLS policy).
export const USER_JOB_KINDS = [
  "run_pipeline", "fetch_source", "generate_report", "export_report", "compute_risk",
  "quality_audit", "source_scout", "test_notification", "verify_source", "recompute_incident",
] as const;
export type UserJobKind = (typeof USER_JOB_KINDS)[number];

/** Queue work for the Python worker. Returns the job id, or null when an identical job is already queued. */
export async function enqueueJob(tx: Tx, s: Session, kind: UserJobKind, payload: Record<string, unknown>,
  opts: { dedupeKey?: string; priority?: number } = {}): Promise<number | null> {
  const rows = await tx<{ id: number }[]>`
    insert into jobs(tenant_id, kind, payload, priority, dedupe_key, requested_by)
    values (${s.tenantId}, ${kind}, ${tx.json(payload as never)}, ${opts.priority ?? 5}, ${opts.dedupeKey ?? null}, ${s.userId})
    on conflict (dedupe_key) where status in ('queued', 'running') and dedupe_key is not null do nothing
    returning id`;
  return rows[0]?.id ?? null;
}

export async function audit(tx: Tx, s: Session, action: string, entityType: string, entityId: string | null,
  details: Record<string, unknown> = {}): Promise<void> {
  await tx`
    insert into audit_log(tenant_id, actor_user_id, action, entity_type, entity_id, details)
    values (${s.tenantId}, ${s.userId}, ${action}, ${entityType}, ${entityId}, ${tx.json(details as never)})`;
}
