import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

// Liveness/readiness for load balancers. Reports only coarse state, never tenant data.
export async function GET() {
  const started = Date.now();
  try {
    const [r] = await sql()<{ migrations: number; queued: number; stale: number }[]>`
      select (select count(*)::int from schema_migrations) as migrations,
             (select count(*)::int from jobs where status = 'queued') as queued,
             (select count(*)::int from jobs where status = 'running' and locked_at < now() - interval '30 minutes') as stale`;
    return NextResponse.json({ status: "ok", db: "ok", migrations: r.migrations, jobs_queued: r.queued, jobs_stale: r.stale, latency_ms: Date.now() - started });
  } catch {
    return NextResponse.json({ status: "degraded", db: "unreachable" }, { status: 503 });
  }
}
