import Link from "next/link";
import { BandBadge, DemoTag, StatusBadge } from "@/components/badges";
import { IncidentTable } from "@/components/IncidentTable";
import { MapView } from "@/components/MapView";
import { Card, CardBody, CardHeader, LinkButton, Notice, PageHeader, Stat } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtAgo, fmtDateTime, humanize } from "@/lib/format";
import { countryName, GCC, t } from "@/lib/i18n";
import { lastIngestion, listIncidents, taxonomyLabels, toPoints } from "@/lib/queries";
import { parseRange, rangeParams } from "@/lib/range";
import { qs, type SearchParams } from "@/lib/utils";

export const metadata = { title: "Overview" };

export default async function OverviewPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const range = parseRange(sp);
  const rp = rangeParams(range);
  const L = s.locale;
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;

  const data = await withRls(s, async (tx) => {
    const labels = await taxonomyLabels(tx);
    const { rows, total } = await listIncidents(tx, {}, range, 200);
    const health = await lastIngestion(tx);
    const alertsPending = can.staff(s.role)
      ? (await tx<{ n: number }[]>`select count(*)::int as n from alerts where status = 'pending_review'`)[0].n : 0;
    const reviewsOpen = can.staff(s.role)
      ? (await tx<{ n: number }[]>`select count(*)::int as n from analyst_reviews where status = 'open'`)[0].n : 0;
    const risk = await tx<{ country_code: string; dimension: string; band: string; score: string | null; as_of_date: Date; computed_at: Date }[]>`
      select distinct on (country_code, dimension) country_code, dimension, coalesce(analyst_band, band) as band, score, as_of_date, computed_at
      from risk_assessments where country_code = any(${[...GCC]}::text[])
      order by country_code, dimension, as_of_date desc, computed_at desc`;
    const brief = await tx<{ id: string; ref: string; title: string; status: string; created_at: Date; is_demo: boolean }[]>`
      select id, ref, title, status, created_at, is_demo from reports where report_type = 'daily_gcc' order by created_at desc limit 1`;
    return { labels, rows, total, health, alertsPending, reviewsOpen, risk, brief: brief[0] ?? null };
  });

  const high = data.rows.filter((r) => r.severity >= 4).length;
  const weak = data.rows.filter((r) => ["disputed", "single_source", "unverified"].includes(r.verification_status)).length;
  const { points, unlocated } = toPoints(data.rows);
  const bandRank = ["insufficient_data", "low", "moderate", "elevated", "high", "severe"];

  return (
    <>
      <PageHeader title={t(L, "overview.title")}
        description="Public-source reporting on the six GCC states, plus spill-over from Iran, Iraq, Yemen, Israel, the Red Sea and the Horn of Africa where it affects GCC security, energy, shipping or business continuity."
        meta={<>{t(L, "common.lastUpdated")}: last successful ingestion {fmtAgo(data.health.last)} ({fmtDateTime(data.health.last, tz, L)}) · {data.health.healthy}/{data.health.enabled} sources healthy{data.health.failing ? ` · ${data.health.failing} failing` : ""}</>} />

      {data.health.enabled === 0 ? (
        <Notice tone="warn" className="mb-4">No sources are enabled, so nothing is being collected. An admin can approve sources under <Link className="underline" href="/sources">Sources</Link>.</Notice>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label={t(L, "overview.incidents")} value={data.total} href={`/incidents${qs(rp)}`} />
        <Stat label={t(L, "overview.highSeverity")} value={high} tone={high ? "danger" : "default"} href={`/incidents${qs({ ...rp, min_severity: 4 })}`} />
        <Stat label={t(L, "overview.disputed")} value={weak} tone={weak ? "warn" : "default"}
          href={`/incidents${qs({ ...rp, verification: "disputed,single_source,unverified" })}`} />
        {can.staff(s.role) ? (
          <>
            <Stat label={t(L, "overview.pendingReview")} value={data.reviewsOpen} tone={data.reviewsOpen ? "warn" : "default"} href="/reviews" />
            <Stat label={t(L, "overview.alerts")} value={data.alertsPending} tone={data.alertsPending ? "warn" : "default"} href="/alerts?status=pending_review" />
          </>
        ) : null}
        <Stat label={t(L, "overview.sources")} value={`${data.health.healthy}/${data.health.enabled}`} href="/sources" hint="Source health →" />
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader title="Incident map" description="Click a marker for its evidence. Coordinates are gazetteer centroids with stated precision." />
          <CardBody><MapView points={points} unlocated={unlocated} height={360} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="GCC states" description="Incidents in range and the highest current risk band (observed-reporting index, not a forecast)." />
          <CardBody className="grid gap-2">
            {GCC.map((c) => {
              const inc = data.rows.filter((r) => r.country_code === c || r.affected_countries.includes(c));
              const bands = data.risk.filter((r) => r.country_code === c);
              const top = bands.sort((a, b) => bandRank.indexOf(b.band) - bandRank.indexOf(a.band))[0];
              return (
                <Link key={c} href={`/countries/${c}${qs(rp)}`} className="flex items-center justify-between rounded-md border px-3 py-2 hover:border-primary">
                  <div>
                    <div className="text-sm font-medium">{countryName(c, L)}</div>
                    <div className="text-[11px] text-muted-foreground">{inc.length} incident(s){inc.some((i) => i.severity >= 4) ? " · includes severity 4–5" : ""}</div>
                  </div>
                  <div className="text-end">
                    {top ? <BandBadge band={top.band} /> : <BandBadge band="insufficient_data" />}
                    {top && top.band !== "insufficient_data" ? <div className="mt-0.5 text-[10px] text-muted-foreground">{humanize(top.dimension)}</div> : null}
                  </div>
                </Link>
              );
            })}
          </CardBody>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader title="Latest incidents" action={<LinkButton href={`/incidents${qs(rp)}`}>{t(L, "common.viewAll")}</LinkButton>} />
          <IncidentTable rows={data.rows.slice(0, 10)} labels={data.labels} tz={tz} locale={L} showReview={can.staff(s.role)} />
        </Card>
        <Card>
          <CardHeader title="Latest daily GCC brief" action={<LinkButton href="/reports">All reports</LinkButton>} />
          <CardBody className="text-sm">
            {data.brief ? (
              <div className="grid gap-2">
                <Link href={`/reports/${data.brief.id}`} className="font-medium hover:underline">{data.brief.title}</Link>
                <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  {data.brief.ref} · {fmtDateTime(data.brief.created_at, tz, L)} <StatusBadge status={data.brief.status} /> <DemoTag show={data.brief.is_demo} />
                </div>
                <p className="text-xs text-muted-foreground">Every statement in a brief cites stored source articles; publication is blocked if the citation check fails.</p>
              </div>
            ) : (
              <p className="text-muted-foreground">No daily brief has been generated yet. Staff can generate one under Intelligence Reports; the scheduler creates one each morning.</p>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
