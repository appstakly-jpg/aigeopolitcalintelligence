import { notFound } from "next/navigation";
import { IncidentTable } from "@/components/IncidentTable";
import { MapView } from "@/components/MapView";
import { RiskTable } from "@/components/RiskTable";
import { Card, CardBody, CardHeader, LinkButton, PageHeader } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtDateTime } from "@/lib/format";
import { COUNTRY_NAMES, countryName, GCC } from "@/lib/i18n";
import { listIncidents, taxonomyLabels, toPoints } from "@/lib/queries";
import { parseRange, rangeParams } from "@/lib/range";
import { latestAssessments } from "@/lib/risk";
import { qs, type SearchParams } from "@/lib/utils";

export async function generateMetadata({ params }: { params: Promise<{ code: string }> }) {
  return { title: countryName((await params).code.toUpperCase()) };
}

export default async function CountryPage({ params, searchParams }: { params: Promise<{ code: string }>; searchParams: Promise<SearchParams> }) {
  const code = (await params).code.toUpperCase();
  if (!COUNTRY_NAMES[code]) notFound();
  const s = await requireSession();
  const range = parseRange(await searchParams);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const isGcc = (GCC as readonly string[]).includes(code);
  const d = await withRls(s, async (tx) => {
    const labels = await taxonomyLabels(tx);
    const { rows, total } = await listIncidents(tx, { country: code }, range, 100);
    const risk = await latestAssessments(tx, [code]);
    const [center] = await tx<{ lat: number; lon: number }[]>`select lat, lon from geo_entities where kind = 'country' and country_code = ${code} limit 1`;
    const sources = await tx<{ n: number }[]>`
      select count(*)::int as n from sources where enabled and (country_focus = '{}' or ${code} = any(country_focus))`;
    return { labels, rows, total, risk, center, sources: sources[0].n };
  });
  const { points, unlocated } = toPoints(d.rows);
  const latest = d.risk.reduce<Date | null>((m, r) => (!m || r.computed_at > m ? r.computed_at : m), null);
  return (
    <>
      <PageHeader title={`${countryName(code, s.locale)} · ${countryName(code, s.locale === "ar" ? "en" : "ar")}`}
        description={isGcc ? "GCC member state." : "Context country: incidents are tracked where they affect GCC security, energy, shipping, diplomacy, investment or business continuity."}
        meta={`${d.total} incident(s) in range · ${d.sources} enabled source(s) cover this country · risk last computed ${fmtDateTime(latest, tz, s.locale)}`}
        actions={<LinkButton href={`/incidents${qs({ ...rangeParams(range), country: code })}`}>All incidents</LinkButton>} />
      <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
        <Card id="risk">
          <CardHeader title="Risk by dimension" description="Severity, exposure, vulnerability and uncertainty are kept separate. Expand a row for its factor contributions." />
          <CardBody>
            {isGcc ? <RiskTable rows={d.risk} tz={tz} canAdjust={can.staff(s.role)} />
              : <p className="text-sm text-muted-foreground">Risk indices are computed for GCC states. Incidents here feed GCC indices through the spill-over weights in the methodology.</p>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Map" />
          <CardBody>
            <MapView points={points} unlocated={unlocated} height={340} zoom={4.5} center={d.center ? [d.center.lon, d.center.lat] : [50.5, 23.5]} />
          </CardBody>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader title="Incidents" />
        <IncidentTable rows={d.rows} labels={d.labels} tz={tz} locale={s.locale} showReview={can.staff(s.role)} />
      </Card>
    </>
  );
}
