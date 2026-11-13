import Link from "next/link";
import { ActionForm } from "@/components/ActionForm";
import { DemoTag, StatusBadge } from "@/components/badges";
import { Card, CardBody, CardHeader, Empty, Input, Label, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtDateTime, humanize } from "@/lib/format";
import { countryName, CONTEXT_COUNTRIES, GCC, t } from "@/lib/i18n";
import { first, type SearchParams } from "@/lib/utils";
import { generateReportAction } from "./actions";

export const metadata = { title: "Intelligence Reports" };

export default async function ReportsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const type = first(sp.type);
  const status = first(sp.status);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const { rows, jobs } = await withRls(s, async (tx) => {
    const rows = await tx<{ id: string; ref: string; title: string; report_type: string; country_code: string | null; period_start: Date; period_end: Date;
      status: string; current_version: number; created_at: Date; published_at: Date | null; is_demo: boolean; ok: boolean | null; citations: number | null }[]>`
      select r.id, r.ref, r.title, r.report_type, r.country_code, r.period_start, r.period_end, r.status, r.current_version, r.created_at,
             r.published_at, r.is_demo, (v.citation_check->>'ok')::boolean as ok, jsonb_array_length(coalesce(v.citations, '[]')) as citations
      from reports r left join report_versions v on v.report_id = r.id and v.version = r.current_version
      where (${type ?? null}::text is null or r.report_type = ${type ?? null})
        and (${status ?? null}::text is null or r.status = ${status ?? null})
      order by r.created_at desc limit 100`;
    const jobs = can.staff(s.role) ? await tx<{ id: number; status: string; payload: Record<string, unknown>; last_error: string | null; created_at: Date }[]>`
      select id, status, payload, last_error, created_at from jobs where kind = 'generate_report' and created_at > now() - interval '1 day'
      order by id desc limit 5` : [];
    return { rows, jobs };
  });
  return (
    <>
      <PageHeader title={t(s.locale, "nav.reports")}
        description="Briefs are assembled only from stored, non-rejected records. Each statement cites source articles; a citation check runs on every version and publication is blocked when it fails." />
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Card>
          <CardHeader title="Reports" action={
            <form className="flex gap-1.5" method="get">
              <Select name="type" defaultValue={type ?? ""} className="h-8 w-auto text-xs">
                <option value="">All types</option>
                {["breaking", "daily_gcc", "country_daily", "weekly_outlook", "monthly_assessment", "maritime_energy", "custom"].map((x) => <option key={x} value={x}>{humanize(x)}</option>)}
              </Select>
              <Select name="status" defaultValue={status ?? ""} className="h-8 w-auto text-xs">
                <option value="">Any status</option>
                {["draft", "in_review", "published", "archived"].map((x) => <option key={x} value={x}>{humanize(x)}</option>)}
              </Select>
              <button className="rounded-md border px-2 text-xs" type="submit">Filter</button>
            </form>} />
          {rows.length === 0 ? <CardBody><Empty>No reports yet.</Empty></CardBody> : (
            <Table>
              <thead><tr><Th>Report</Th><Th>Type</Th><Th>Period</Th><Th>Status</Th><Th>Citations</Th><Th>Created</Th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <Td><Link href={`/reports/${r.id}`} className="font-medium hover:underline">{r.title}</Link>
                      <div className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">{r.ref} · v{r.current_version} <DemoTag show={r.is_demo} /></div></Td>
                    <Td className="text-xs">{humanize(r.report_type)}{r.country_code ? ` · ${countryName(r.country_code, s.locale)}` : ""}</Td>
                    <Td className="text-xs whitespace-nowrap">{fmtDateTime(r.period_start, tz, s.locale)}<br />{fmtDateTime(r.period_end, tz, s.locale)}</Td>
                    <Td><StatusBadge status={r.status} /></Td>
                    <Td className="text-xs">{r.citations ?? 0} · {r.ok === null ? "unchecked" : r.ok ? <span className="text-emerald-700 dark:text-emerald-400">check passed</span> : <span className="text-red-600">check failed</span>}</Td>
                    <Td className="text-xs whitespace-nowrap text-muted-foreground">{fmtDateTime(r.created_at, tz, s.locale)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        {can.write(s.role) ? (
          <Card className="h-fit">
            <CardHeader title="Generate a report" description="Runs on the worker. Without an LLM key the narrative is assembled deterministically from records; with one, model text is accepted only if every citation and figure checks out." />
            <CardBody className="grid gap-4">
              <ActionForm action={generateReportAction} submit="Generate">
                <Label>Type
                  <Select name="report_type" defaultValue="daily_gcc">
                    <option value="daily_gcc">Daily GCC brief</option>
                    <option value="breaking">Breaking brief (6 h)</option>
                    <option value="country_daily">Country daily brief</option>
                    <option value="weekly_outlook">Weekly outlook</option>
                    <option value="monthly_assessment">Monthly assessment</option>
                    <option value="maritime_energy">Maritime &amp; energy brief</option>
                    <option value="custom">Custom period</option>
                  </Select>
                </Label>
                <Label hint="Required for a country brief">Country
                  <Select name="country_code" defaultValue="">
                    <option value="">—</option>
                    {[...GCC, ...CONTEXT_COUNTRIES].map((c) => <option key={c} value={c}>{countryName(c, s.locale)}</option>)}
                  </Select>
                </Label>
                <div className="grid grid-cols-2 gap-2">
                  <Label hint="Custom only">From<Input type="date" name="period_start" /></Label>
                  <Label hint="Custom only">To<Input type="date" name="period_end" /></Label>
                </div>
              </ActionForm>
              {jobs.length ? (
                <div className="grid gap-1 text-xs">
                  <div className="font-medium">Recent generation jobs</div>
                  {jobs.map((j) => (
                    <div key={j.id} className="flex flex-wrap items-center gap-1">#{j.id} <StatusBadge status={j.status} /> {humanize(String(j.payload.report_type ?? ""))}
                      {j.last_error ? <span className="text-red-600">· {j.last_error.slice(0, 120)}</span> : null}</div>
                  ))}
                </div>
              ) : null}
            </CardBody>
          </Card>
        ) : null}
      </div>
    </>
  );
}
