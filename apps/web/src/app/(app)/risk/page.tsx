import Link from "next/link";
import { ActionForm } from "@/components/ActionForm";
import { BandBadge } from "@/components/badges";
import { RiskTable } from "@/components/RiskTable";
import { Card, CardBody, CardHeader, PageHeader, Table, Td, Th } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtDateTime, fmtNumber, humanize } from "@/lib/format";
import { countryName, GCC, t } from "@/lib/i18n";
import { latestAssessments } from "@/lib/risk";
import { first, type SearchParams } from "@/lib/utils";
import { computeRiskAction } from "./actions";

export const metadata = { title: "Risk Analysis" };

export default async function RiskPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const focus = first(sp.country)?.toUpperCase();
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const d = await withRls(s, async (tx) => {
    const rows = await latestAssessments(tx, [...GCC]);
    const methods = await tx<{ id: string; version: string; name: string; description_md: string; params: Record<string, unknown>;
      is_active: boolean; created_at: Date }[]>`
      select id, version, name, description_md, params, is_active, created_at from risk_methodologies order by created_at desc`;
    return { rows, methods };
  });
  const dims = [...new Set(d.rows.map((r) => r.dimension))].sort();
  const active = d.methods.find((m) => m.is_active) ?? d.methods[0];
  return (
    <>
      <PageHeader title={t(s.locale, "nav.risk")}
        description="Evidence-weighted indices of recent observed reporting per country and dimension. They are not forecasts, and a country with no healthy source coverage shows 'insufficient data' rather than a low score."
        actions={can.staff(s.role) ? <ActionForm action={computeRiskAction} submit="Recompute now" variant="outline" className="flex items-center gap-2" /> : null} />
      <Card>
        <CardHeader title="Country × dimension" description="Click a cell for its components and contributing incidents." />
        <Table>
          <thead>
            <tr><Th>Country</Th>{dims.map((x) => <Th key={x}>{humanize(x)}</Th>)}</tr>
          </thead>
          <tbody>
            {GCC.map((c) => (
              <tr key={c}>
                <Td className="font-medium whitespace-nowrap"><Link href={`/risk?country=${c}#detail`} className="hover:underline">{countryName(c, s.locale)}</Link></Td>
                {dims.map((x) => {
                  const a = d.rows.find((r) => r.country_code === c && r.dimension === x);
                  return (
                    <Td key={x}>
                      {a ? (
                        <Link href={`/risk?country=${c}#detail`} className="inline-flex flex-col gap-0.5">
                          <BandBadge band={a.analyst_band ?? a.band} />
                          <span className="text-[10px] text-muted-foreground tabular-nums">{a.score !== null ? fmtNumber(a.score, 2) : "n/a"} · {a.incident_count} inc.</span>
                        </Link>
                      ) : <span className="text-xs text-muted-foreground">—</span>}
                    </Td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>

      {focus && (GCC as readonly string[]).includes(focus) ? (
        <Card className="mt-4" id="detail">
          <CardHeader title={`${countryName(focus, s.locale)}: detail`} />
          <CardBody><RiskTable rows={d.rows.filter((r) => r.country_code === focus)} tz={tz} canAdjust={can.staff(s.role)} /></CardBody>
        </Card>
      ) : null}

      {active ? (
        <Card className="mt-4">
          <CardHeader title={`Methodology v${active.version}: ${active.name}`} description={`Active since ${fmtDateTime(active.created_at, tz, s.locale)}. Versions are immutable; changing parameters creates a new version.`} />
          <CardBody className="grid gap-4 lg:grid-cols-2">
            <div className="text-sm leading-relaxed whitespace-pre-wrap">{active.description_md}</div>
            <div>
              <div className="mb-1 text-xs font-medium">Parameters</div>
              <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-[11px]">{JSON.stringify(active.params, null, 2)}</pre>
              {d.methods.length > 1 ? (
                <div className="mt-2 text-xs text-muted-foreground">Earlier versions: {d.methods.filter((m) => m.id !== active.id).map((m) => `v${m.version}`).join(", ")}</div>
              ) : null}
            </div>
          </CardBody>
        </Card>
      ) : null}
    </>
  );
}
