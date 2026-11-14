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
    // Official travel advisories are stored like articles but are not incidents: show the latest one per
    // source (country feeds such as FCDO, or the US State Department's all-country feed by title).
    const advisories = await tx<{ id: string; title: string; excerpt: string | null; url: string; published_at: Date | null; source_name: string; publisher: string | null }[]>`
      select distinct on (s.id) a.id, a.title, a.excerpt, a.url, a.published_at, s.name as source_name, s.publisher
      from articles a join sources s on s.id = a.source_id
      where s.config->>'content_kind' = 'travel_advisory' and s.status = 'approved'
        and (${code} = any(s.country_focus) or a.title ilike ${COUNTRY_NAMES[code].en + " - %"})
      order by s.id, a.published_at desc nulls last`;
    return { labels, rows, total, risk, center, sources: sources[0].n, advisories };
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
      {d.advisories.length > 0 && (
        <Card className="mt-4" id="advisories">
          <CardHeader title="Official travel advisories"
            description="Latest published guidance from each government source, linked to the original. Advisories are attributable government positions: they are not incidents and do not feed the risk indices." />
          <CardBody>
            <ul className="space-y-3">
              {d.advisories.map((a) => (
                <li key={a.id} className="text-sm">
                  <a href={a.url} target="_blank" rel="noopener noreferrer nofollow" className="font-medium text-primary hover:underline">{a.title}</a>
                  <span className="text-muted-foreground"> · {a.publisher ?? a.source_name} · updated {fmtDateTime(a.published_at, tz, s.locale)}</span>
                  {a.excerpt && <p className="mt-0.5 text-muted-foreground">{a.excerpt}</p>}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}
      <Card className="mt-4">
        <CardHeader title="Incidents" />
        <IncidentTable rows={d.rows} labels={d.labels} tz={tz} locale={s.locale} showReview={can.staff(s.role)} />
      </Card>
    </>
  );
}
