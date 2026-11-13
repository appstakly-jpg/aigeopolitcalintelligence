import Link from "next/link";
import { IncidentTable } from "@/components/IncidentTable";
import { MapView } from "@/components/MapView";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { t } from "@/lib/i18n";
import { listIncidents, MARITIME_ENERGY_CATEGORIES, taxonomyLabels, toPoints } from "@/lib/queries";
import { parseRange, rangeParams } from "@/lib/range";
import { qs, type SearchParams } from "@/lib/utils";

export const metadata = { title: "Maritime & Energy" };

const CHOKEPOINTS = [
  { id: "hormuz", name: "Strait of Hormuz", match: /hormuz|gulf of oman|arabian gulf|persian gulf/i },
  { id: "bab", name: "Bab el-Mandeb & Gulf of Aden", match: /bab el-mandeb|bab al-mandab|gulf of aden|aden/i },
  { id: "red", name: "Red Sea & Suez", match: /red sea|suez|jeddah|yanbu|jazan/i },
];

export default async function MaritimePage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const range = parseRange(await searchParams);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const { rows, labels } = await withRls(s, async (tx) => ({
    rows: (await listIncidents(tx, { maritime: true }, range, 200)).rows,
    labels: await taxonomyLabels(tx),
  }));
  const { points, unlocated } = toPoints(rows);
  return (
    <>
      <PageHeader title={t(s.locale, "nav.maritime")}
        description={`Shipping, ports, energy infrastructure and sanctions: incidents tagged ${MARITIME_ENERGY_CATEGORIES.map((c) => labels[c]?.label ?? c).join(", ")}.`}
        meta={`${rows.length} incident(s) in range`} />
      <div className="grid gap-3 md:grid-cols-3">
        {CHOKEPOINTS.map((c) => {
          const hits = rows.filter((r) => c.match.test(`${r.location_name ?? ""} ${r.title}`));
          return (
            <Card key={c.id} className="p-4">
              <div className="text-sm font-medium">{c.name}</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{hits.length}</div>
              <div className="text-[11px] text-muted-foreground">incident(s) in range{hits.some((h) => h.severity >= 4) ? " · includes severity 4–5" : ""}</div>
              <div className="mt-2 grid gap-1 text-xs">
                {hits.slice(0, 3).map((h) => <Link key={h.id} href={`/incidents/${h.id}`} className="truncate text-primary hover:underline">{h.ref}: {h.title}</Link>)}
              </div>
            </Card>
          );
        })}
      </div>
      <Card className="mt-4">
        <CardHeader title="Sea lanes and energy sites" />
        <CardBody><MapView points={points} unlocated={unlocated} height={460} zoom={4} center={[47, 20]} /></CardBody>
      </Card>
      <Card className="mt-4">
        <CardHeader title="Incidents" description={<Link className="underline" href={`/reports${qs(rangeParams(range))}`}>Maritime &amp; energy reports →</Link>} />
        <IncidentTable rows={rows} labels={labels} tz={tz} locale={s.locale} showReview={can.staff(s.role)} />
      </Card>
    </>
  );
}
