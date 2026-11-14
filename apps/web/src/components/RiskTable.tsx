import Link from "next/link";
import { adjustRiskAction } from "@/app/(app)/risk/actions";
import { ActionForm } from "@/components/ActionForm";
import { BandBadge, DemoTag } from "@/components/badges";
import { Badge, Empty, Input, Select } from "@/components/ui/primitives";
import { fmtDateTime, fmtNumber, humanize } from "@/lib/format";
import { DIMENSION_HELP, type Assessment } from "@/lib/risk";

/** Risk by dimension, with each factor kept separate and every contribution linked to its incident. */
export function RiskTable({ rows, tz, canAdjust }: { rows: Assessment[]; tz: string; canAdjust: boolean }) {
  if (!rows.length) return <Empty>No risk assessments have been computed yet.</Empty>;
  return (
    <div className="grid gap-2">
      {rows.map((a) => {
        const c = a.components as { observed_severity?: number; exposure?: number; vulnerability?: number; corroborated_share?: number;
          healthy_sources?: number; coverage_sources?: string[]; note?: string };
        return (
          <details key={a.id} className="rounded-md border bg-card">
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm">
              <span className="min-w-40 font-medium">{humanize(a.dimension)}</span>
              <BandBadge band={a.analyst_band ?? a.band} />
              {a.analyst_band ? <Badge title={a.analyst_rationale ?? ""}>Analyst-adjusted (computed: {humanize(a.band)})</Badge> : null}
              <span className="text-xs text-muted-foreground tabular-nums">Index {a.score !== null ? fmtNumber(a.score, 2) : "n/a"} / 5</span>
              <span className="text-xs text-muted-foreground">Confidence {a.confidence} · forecast uncertainty {a.forecast_uncertainty}</span>
              <span className="ms-auto text-xs text-muted-foreground">{a.incident_count} incident(s) · methodology v{a.methodology_version}</span>
            </summary>
            <div className="grid gap-3 border-t px-3 py-3 text-xs md:grid-cols-2">
              <div className="grid gap-1">
                <p className="text-muted-foreground">{DIMENSION_HELP[a.dimension] ?? ""}</p>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  <span className="text-muted-foreground">Observed severity (S)</span><span className="tabular-nums">{fmtNumber(c.observed_severity ?? null, 2)}</span>
                  <span className="text-muted-foreground">Exposure (E)</span><span className="tabular-nums">{fmtNumber(c.exposure ?? null, 2)}</span>
                  <span className="text-muted-foreground">Vulnerability (V)</span><span className="tabular-nums">{fmtNumber(c.vulnerability ?? null, 2)}</span>
                  <span className="text-muted-foreground">Corroborated share</span><span className="tabular-nums">{c.corroborated_share !== undefined ? `${Math.round(c.corroborated_share * 100)}%` : "—"}</span>
                  <span className="text-muted-foreground">Healthy covering sources</span><span className="tabular-nums">{c.healthy_sources ?? "—"}</span>
                  <span className="text-muted-foreground">Window</span><span>{fmtDateTime(a.window_start, tz)} – {fmtDateTime(a.window_end, tz)}</span>
                </div>
                {a.band === "insufficient_data" ? (
                  <p className="text-amber-700 dark:text-amber-300">No healthy enabled source covers this country, so no score is given. Absence of reporting is not evidence of calm.</p>
                ) : null}
                <p className="text-muted-foreground">{c.note ?? "Observed-reporting index, not a forecast."} Computed {fmtDateTime(a.computed_at, tz)}.</p>
                {a.analyst_rationale ? <p><span className="font-medium">Analyst rationale:</span> {a.analyst_rationale}</p> : null}
              </div>
              <div className="grid content-start gap-1">
                <div className="font-medium">Factor contributions</div>
                {a.contributions.length === 0 ? <span className="text-muted-foreground">No incidents contributed.</span> : (
                  <ul className="grid gap-1">
                    {a.contributions.map((x) => (
                      <li key={x.incident_id} className="flex flex-wrap items-center gap-1">
                        <span className="w-12 tabular-nums">{fmtNumber(x.contribution, 2)}</span>
                        <Link href={`/incidents/${x.incident_id}`} className="text-primary hover:underline">{x.ref}</Link>
                        <span className="truncate">{x.title}</span>
                        <span className="text-muted-foreground">(S{x.severity}, {humanize(x.verification_status)}, decay {fmtNumber(x.decay, 2)}{x.country_weight !== 1 ? `, weight ${x.country_weight}` : ""})</span>
                        <DemoTag show={x.is_demo} />
                      </li>
                    ))}
                  </ul>
                )}
                {canAdjust ? (
                  <ActionForm action={adjustRiskAction} submit="Save analyst band" variant="outline" className="mt-2 grid gap-1.5">
                    <input type="hidden" name="id" value={a.id} />
                    <div className="grid grid-cols-[140px_1fr] gap-1.5">
                      <Select name="band" defaultValue={a.analyst_band ?? ""}>
                        <option value="">(use computed)</option>
                        {["low", "moderate", "elevated", "high", "severe"].map((b) => <option key={b} value={b}>{humanize(b)}</option>)}
                      </Select>
                      <Input name="rationale" placeholder="Rationale (required for an adjustment)" defaultValue={a.analyst_rationale ?? ""} maxLength={1000} />
                    </div>
                  </ActionForm>
                ) : null}
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}
