import type { Tx } from "./db";

export interface ChangedIncident { id: string; ref: string; version: number; cited_version: number | null }

/**
 * Cited incidents that changed after a report version was generated (a correction, new corroboration,
 * a retraction...). Reports record the incident version they were written from; versions generated
 * before that was recorded fall back to comparing timestamps.
 */
export async function changedSinceGenerated(tx: Tx, reportId: string, version: number): Promise<ChangedIncident[]> {
  return tx<ChangedIncident[]>`
    with v as (select content, created_at from report_versions where report_id = ${reportId} and version = ${version}),
    cited as (
      select (e->>'id')::uuid as id, (e->>'version')::int as cited_version
      from v, jsonb_array_elements(coalesce(v.content->'incidents', '[]'::jsonb)) e
    )
    select i.id, i.ref, i.version, c.cited_version
    from cited c join incidents i on i.id = c.id cross join v
    where (c.cited_version is not null and i.version > c.cited_version)
       or (c.cited_version is null and i.updated_at > v.created_at)
    order by i.ref`;
}

export function describeChanges(changed: ChangedIncident[]): string {
  return changed.map((c) => (c.cited_version ? `${c.ref} (v${c.cited_version} → v${c.version})` : c.ref)).join(", ");
}
