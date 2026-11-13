import { NextResponse, type NextRequest } from "next/server";
import { getSession, rateLimit } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { listIncidents, parseIncidentFilters } from "@/lib/queries";
import { parseRange } from "@/lib/range";

// GET /api/v1/incidents — the signed-in user's view of incidents (RLS applies), as JSON or CSV.
// Query: range|from&to, country, category, verification, min_severity, review, q, limit (<=500), offset, format=json|csv
function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? "" : Array.isArray(v) ? v.join(";") : v instanceof Date ? v.toISOString() : String(v);
  const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s; // neutralise spreadsheet formula injection
  return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export async function GET(req: NextRequest) {
  const s = await getSession();
  if (!s) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  if (!(await rateLimit(`api:${s.userId}`, 60, 120))) return NextResponse.json({ error: "rate limit exceeded" }, { status: 429 });
  const sp = Object.fromEntries(req.nextUrl.searchParams.entries());
  const range = parseRange(sp);
  const f = parseIncidentFilters(sp);
  const limit = Math.min(500, Math.max(1, Number(sp.limit) || 100));
  const offset = Math.max(0, Number(sp.offset) || 0);
  const { rows, total } = await withRls(s, (tx) => listIncidents(tx, f, range, limit, offset));
  const data = rows.map((r) => ({
    id: r.id, ref: r.ref, title: r.title, summary: r.summary, status: r.status, verification_status: r.verification_status, review_status: r.review_status, credibility: r.credibility,
    severity: r.severity, confidence: r.confidence, primary_category: r.primary_category, categories: r.categories, country_code: r.country_code,
    affected_countries: r.affected_countries, location_name: r.location_name, lat: r.lat, lon: r.lon, geo_precision: r.geo_precision,
    geo_confidence: r.geo_confidence, casualties_killed: r.casualties_killed, casualties_injured: r.casualties_injured, source_count: r.source_count,
    independent_source_count: r.independent_source_count, first_reported_at: r.first_reported_at, last_reported_at: r.last_reported_at,
    version: r.version, is_synthetic: r.is_demo, url: `${req.nextUrl.origin}/incidents/${r.id}`,
  }));
  if (sp.format === "csv") {
    const cols = Object.keys(data[0] ?? { id: "" });
    const body = [cols.join(","), ...data.map((row) => cols.map((c) => csvCell((row as Record<string, unknown>)[c])).join(","))].join("\n");
    return new NextResponse(body, { headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="incidents.csv"', "Cache-Control": "private, no-store" } });
  }
  return NextResponse.json({ data, total, limit, offset, range: { from: range.from, to: range.to } }, { headers: { "Cache-Control": "private, no-store" } });
}
