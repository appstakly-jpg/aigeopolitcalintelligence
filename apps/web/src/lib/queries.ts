import type { MapPoint } from "@/components/MapView";
import type { Tx } from "./db";
import type { DateRange } from "./range";
import { searchTerms, tsOr } from "./retrieval";
import { first, type SearchParams } from "./utils";

export interface IncidentFilters {
  country?: string;
  category?: string;
  verification?: string[];
  minSeverity?: number;
  review?: string;
  q?: string;
  maritime?: boolean;
  includeRejected?: boolean;
}

const VERIFICATION = ["confirmed", "corroborated", "single_source", "disputed", "unverified"];
// Same set as workers/gccintel/agents/briefing.py MARITIME_ENERGY.
export const MARITIME_ENERGY_CATEGORIES = [
  "maritime_incident", "shipping_disruption", "energy_disruption", "infrastructure_disruption", "sanctions",
];

export function parseIncidentFilters(sp: SearchParams): IncidentFilters {
  const country = first(sp.country)?.toUpperCase();
  const verification = (first(sp.verification) ?? "").split(",").filter((v) => VERIFICATION.includes(v));
  const minSev = Number(first(sp.min_severity));
  const review = first(sp.review);
  return {
    country: country && /^[A-Z]{2}$/.test(country) ? country : undefined,
    category: /^[a-z_]{2,40}$/.test(first(sp.category) ?? "") ? first(sp.category) : undefined,
    verification: verification.length ? verification : undefined,
    minSeverity: Number.isInteger(minSev) && minSev >= 1 && minSev <= 5 ? minSev : undefined,
    review: review && ["pending", "approved", "rejected", "changes_requested"].includes(review) ? review : undefined,
    q: (first(sp.q) ?? "").slice(0, 200) || undefined,
    includeRejected: review === "rejected",
  };
}

export interface IncidentRow {
  id: string; ref: string; title: string; summary: string | null; status: string; verification_status: string; credibility: number | null;
  review_status: string; severity: number; confidence: string; primary_category: string | null; categories: string[];
  country_code: string | null; affected_countries: string[]; location_name: string | null; lat: number | null; lon: number | null;
  geo_precision: string; geo_confidence: number | null; casualties_killed: number | null; casualties_injured: number | null;
  source_count: number; independent_source_count: number; first_reported_at: Date; last_reported_at: Date; occurred_at: Date | null;
  version: number; is_demo: boolean; updated_at: Date;
}

export async function listIncidents(tx: Tx, f: IncidentFilters, r: DateRange, limit = 50, offset = 0): Promise<{ rows: IncidentRow[]; total: number }> {
  const terms = f.q ? searchTerms(f.q) : [];
  const tsq = terms.length ? tsOr(terms) : null;
  const where = tx`
    i.last_reported_at >= ${r.from.toISOString()}::timestamptz and i.first_reported_at <= ${r.to.toISOString()}::timestamptz
    and i.status <> 'merged'
    ${f.includeRejected ? tx`` : tx`and i.review_status <> 'rejected' and i.status <> 'rejected'`}
    ${f.country ? tx`and (i.country_code = ${f.country} or ${f.country} = any(i.affected_countries))` : tx``}
    ${f.category ? tx`and ${f.category} = any(i.categories)` : tx``}
    ${f.verification ? tx`and i.verification_status = any(${f.verification}::text[])` : tx``}
    ${f.minSeverity ? tx`and i.severity >= ${f.minSeverity}` : tx``}
    ${f.review ? tx`and i.review_status = ${f.review}` : tx``}
    ${f.maritime ? tx`and i.categories && ${MARITIME_ENERGY_CATEGORIES}::text[]` : tx``}
    ${tsq ? tx`and i.search @@ to_tsquery('simple', ${tsq})` : tx``}`;
  const rows = await tx<IncidentRow[]>`
    select i.id, i.ref, i.title, i.summary, i.status, i.verification_status, i.credibility, i.review_status, i.severity, i.confidence,
           i.primary_category, i.categories, i.country_code, i.affected_countries, i.location_name, i.lat, i.lon, i.geo_precision,
           i.geo_confidence, i.casualties_killed, i.casualties_injured, i.source_count, i.independent_source_count,
           i.first_reported_at, i.last_reported_at, i.occurred_at, i.version, i.is_demo, i.updated_at
    from incidents i where ${where}
    order by i.last_reported_at desc limit ${limit} offset ${offset}`;
  const [{ n }] = await tx<{ n: number }[]>`select count(*)::int as n from incidents i where ${where}`;
  return { rows, total: n };
}

export function toPoints(rows: IncidentRow[]): { points: MapPoint[]; unlocated: number } {
  const points: MapPoint[] = [];
  let unlocated = 0;
  for (const r of rows) {
    if (r.lat === null || r.lon === null) {
      unlocated++;
      continue;
    }
    points.push({
      id: r.id, ref: r.ref, title: r.title, lat: r.lat, lon: r.lon, severity: r.severity, verification: r.verification_status,
      precision: r.geo_precision, geoConfidence: r.geo_confidence, location: r.location_name, isDemo: r.is_demo,
    });
  }
  return { points, unlocated };
}

export async function taxonomyLabels(tx: Tx): Promise<Record<string, { label: string; label_ar: string | null }>> {
  const rows = await tx<{ key: string; label: string; label_ar: string | null }[]>`
    select distinct on (key) key, label, label_ar from event_types order by key, tenant_id nulls last`;
  return Object.fromEntries(rows.map((r) => [r.key, { label: r.label, label_ar: r.label_ar }]));
}

export async function lastIngestion(tx: Tx): Promise<{ last: Date | null; healthy: number; enabled: number; failing: number }> {
  const [r] = await tx<{ last: Date | null; healthy: number; enabled: number; failing: number }[]>`
    select max(last_success_at) as last,
           count(*) filter (where enabled and consecutive_failures = 0 and last_success_at > now() - interval '3 days')::int as healthy,
           count(*) filter (where enabled)::int as enabled,
           count(*) filter (where enabled and consecutive_failures > 0)::int as failing
    from sources`;
  return r;
}
