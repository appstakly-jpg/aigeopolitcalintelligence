import { IncidentTable } from "@/components/IncidentTable";
import { Button, Card, Input, LinkButton, PageHeader, Select } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { countryName, CONTEXT_COUNTRIES, GCC, t } from "@/lib/i18n";
import { listIncidents, parseIncidentFilters, taxonomyLabels } from "@/lib/queries";
import { parseRange, rangeParams } from "@/lib/range";
import { first, qs, type SearchParams } from "@/lib/utils";

export const metadata = { title: "Incidents" };
const PAGE = 50;

export default async function IncidentsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const range = parseRange(sp);
  const f = parseIncidentFilters(sp);
  const page = Math.max(1, Number(first(sp.page)) || 1);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const { rows, total, labels } = await withRls(s, async (tx) => {
    const labels = await taxonomyLabels(tx);
    const r = await listIncidents(tx, f, range, PAGE, (page - 1) * PAGE);
    return { ...r, labels };
  });
  const base = { ...rangeParams(range), country: f.country, category: f.category, verification: f.verification?.join(","),
    min_severity: f.minSeverity, review: f.review, q: f.q };
  const exportHref = `/api/v1/incidents${qs({ ...base, format: "csv" })}`;
  return (
    <>
      <PageHeader title={t(s.locale, "nav.incidents")}
        description="Incidents are clusters of reports about one event. Verification counts independent origins only; syndicated copies and same-network outlets do not corroborate each other."
        meta={`${total} incident(s) match`}
        actions={<>
          {can.staff(s.role) ? <LinkButton href="/reviews">Review queue</LinkButton> : null}
          <LinkButton href={exportHref} prefetch={false}>Export CSV</LinkButton>
        </>} />
      <form className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-7" method="get">
        {Object.entries(rangeParams(range)).map(([k, v]) => <input key={k} type="hidden" name={k} value={v} />)}
        <Input name="q" defaultValue={f.q ?? ""} placeholder="Search incidents" className="lg:col-span-2" />
        <Select name="country" defaultValue={f.country ?? ""}>
          <option value="">All countries</option>
          {[...GCC, ...CONTEXT_COUNTRIES].map((c) => <option key={c} value={c}>{countryName(c, s.locale)}</option>)}
        </Select>
        <Select name="category" defaultValue={f.category ?? ""}>
          <option value="">All categories</option>
          {Object.entries(labels).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </Select>
        <Select name="verification" defaultValue={f.verification?.join(",") ?? ""}>
          <option value="">Any verification</option>
          <option value="confirmed">Confirmed</option>
          <option value="corroborated">Corroborated</option>
          <option value="confirmed,corroborated">Confirmed or corroborated</option>
          <option value="single_source">Single source</option>
          <option value="disputed">Disputed</option>
          <option value="unverified">Unverified</option>
          <option value="disputed,single_source,unverified">Disputed, single-source or unverified</option>
        </Select>
        <Select name="min_severity" defaultValue={f.minSeverity ? String(f.minSeverity) : ""}>
          <option value="">Any severity</option>
          {[2, 3, 4, 5].map((n) => <option key={n} value={n}>Severity ≥ {n}</option>)}
        </Select>
        {can.staff(s.role) ? (
          <Select name="review" defaultValue={f.review ?? ""}>
            <option value="">Any review state</option>
            <option value="pending">Pending review</option>
            <option value="approved">Approved</option>
            <option value="changes_requested">Changes requested</option>
            <option value="rejected">Rejected</option>
          </Select>
        ) : null}
        <Button type="submit" variant="outline">Filter</Button>
      </form>
      <Card>
        <IncidentTable rows={rows} labels={labels} tz={tz} locale={s.locale} showReview={can.staff(s.role)} />
      </Card>
      <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
        <span>Page {page} of {Math.max(1, Math.ceil(total / PAGE))}</span>
        <div className="flex gap-2">
          {page > 1 ? <LinkButton href={`/incidents${qs({ ...base, page: page - 1 })}`}>Previous</LinkButton> : null}
          {page * PAGE < total ? <LinkButton href={`/incidents${qs({ ...base, page: page + 1 })}`}>Next</LinkButton> : null}
        </div>
      </div>
    </>
  );
}
