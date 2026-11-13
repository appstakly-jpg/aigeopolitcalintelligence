import Link from "next/link";
import { SeverityBadge, VerificationBadge } from "@/components/badges";
import { MapView } from "@/components/MapView";
import { Button, Card, CardBody, CardHeader, PageHeader, Select } from "@/components/ui/primitives";
import { requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { t } from "@/lib/i18n";
import { listIncidents, parseIncidentFilters, taxonomyLabels, toPoints } from "@/lib/queries";
import { parseRange, rangeParams } from "@/lib/range";
import { type SearchParams } from "@/lib/utils";

export const metadata = { title: "Map" };

export default async function MapPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const range = parseRange(sp);
  const f = parseIncidentFilters(sp);
  const { rows, labels } = await withRls(s, async (tx) => ({ rows: (await listIncidents(tx, f, range, 500)).rows, labels: await taxonomyLabels(tx) }));
  const { points, unlocated } = toPoints(rows);
  const unplaced = rows.filter((r) => r.lat === null || r.lon === null);
  return (
    <>
      <PageHeader title={t(s.locale, "nav.map")}
        description="Markers use gazetteer coordinates with their stated precision; nothing is placed more precisely than the reporting allows. Incidents without a resolvable location are listed beside the map instead of being guessed."
        meta={`${points.length} mapped · ${unlocated} not mapped`} />
      <form className="mb-3 flex flex-wrap gap-2" method="get">
        {Object.entries(rangeParams(range)).map(([k, v]) => <input key={k} type="hidden" name={k} value={v} />)}
        <Select name="category" defaultValue={f.category ?? ""} className="w-auto">
          <option value="">All categories</option>
          {Object.entries(labels).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </Select>
        <Select name="min_severity" defaultValue={f.minSeverity ? String(f.minSeverity) : ""} className="w-auto">
          <option value="">Any severity</option>
          {[2, 3, 4, 5].map((n) => <option key={n} value={n}>Severity ≥ {n}</option>)}
        </Select>
        <Select name="verification" defaultValue={f.verification?.join(",") ?? ""} className="w-auto">
          <option value="">Any verification</option>
          <option value="confirmed,corroborated">Confirmed or corroborated</option>
          <option value="single_source">Single source</option>
          <option value="disputed">Disputed</option>
          <option value="unverified">Unverified</option>
        </Select>
        <Button type="submit" variant="outline">Apply</Button>
      </form>
      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <MapView points={points} unlocated={unlocated} height={620} zoom={4} center={[48, 22]} />
        <Card>
          <CardHeader title="Not mapped" description="No location could be resolved from the reporting." />
          <CardBody className="grid max-h-[560px] gap-2 overflow-y-auto">
            {unplaced.length === 0 ? <span className="text-xs text-muted-foreground">All incidents in range have a location.</span> : unplaced.map((r) => (
              <Link key={r.id} href={`/incidents/${r.id}`} className="rounded-md border p-2 text-xs hover:border-primary">
                <div className="font-medium">{r.title}</div>
                <div className="mt-1 flex gap-1"><VerificationBadge status={r.verification_status} /><SeverityBadge value={r.severity} /></div>
              </Link>
            ))}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
