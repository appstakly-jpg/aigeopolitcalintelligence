import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm } from "@/components/ActionForm";
import { DemoTag, ReliabilityBadge, StatusBadge } from "@/components/badges";
import { Card, CardBody, CardHeader, Input, LinkButton, Notice, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtDateTime, humanize } from "@/lib/format";
import { countryName } from "@/lib/i18n";
import { changedSinceGenerated } from "@/lib/reports";
import { first, isUuid, type SearchParams } from "@/lib/utils";
import { exportReportAction, regenerateReportAction, setReportStatusAction } from "../actions";

interface Citation { n: number; url: string; title: string; title_en?: string; publisher: string; reliability: string; published_at: string | null;
  incident_refs?: string[]; incident_ids?: string[]; link_status?: string; is_demo?: boolean; language?: string }

export const metadata = { title: "Report" };

export default async function ReportPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<SearchParams> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const s = await requireSession();
  const sp = await searchParams;
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const d = await withRls(s, async (tx) => {
    const [r] = await tx<{ id: string; ref: string; title: string; report_type: string; country_code: string | null; period_start: Date; period_end: Date;
      status: string; current_version: number; created_at: Date; published_at: Date | null; is_demo: boolean; publisher: string | null }[]>`
      select r.*, u.email as publisher from reports r left join app_users u on u.id = r.published_by where r.id = ${id}`;
    if (!r) return null;
    const versions = await tx<{ id: string; version: number; generator: string | null; change_note: string | null; created_at: Date;
      ok: boolean | null; email: string | null }[]>`
      select v.id, v.version, v.generator, v.change_note, v.created_at, (v.citation_check->>'ok')::boolean as ok, u.email
      from report_versions v left join app_users u on u.id = v.created_by where v.report_id = ${id} order by v.version desc`;
    const wanted = Number(first(sp.v)) || r.current_version;
    const [v] = await tx<{ id: string; version: number; html: string; citations: Citation[]; coverage: Record<string, unknown>;
      citation_check: { ok: boolean; errors: string[]; warnings: string[]; checked_at: string; checked_citations: number } }[]>`
      select id, version, html, citations, coverage, citation_check from report_versions where report_id = ${id} and version = ${wanted}`;
    const exports = v ? await tx<{ id: string; format: string; size: number; created_at: Date }[]>`
      select id, format, octet_length(bytes) as size, created_at from report_exports where report_version_id = ${v.id} order by created_at desc` : [];
    const changed = v && r.status !== "archived" ? await changedSinceGenerated(tx, r.id, v.version) : [];
    return { r, versions, v, exports, changed };
  });
  if (!d) notFound();
  const { r, v } = d;
  const isCurrent = v?.version === r.current_version;
  const srcDoc = v ? v.html.replace(/<head>/i, '<head><base target="_blank">') : "";
  return (
    <>
      <PageHeader title={r.title}
        meta={<span className="flex flex-wrap items-center gap-1.5">{r.ref} · {humanize(r.report_type)}{r.country_code ? ` · ${countryName(r.country_code, s.locale)}` : ""} ·
          period {fmtDateTime(r.period_start, tz, s.locale)} – {fmtDateTime(r.period_end, tz, s.locale)} <StatusBadge status={r.status} /> <DemoTag show={r.is_demo} />
          {r.published_at ? <span>· published {fmtDateTime(r.published_at, tz, s.locale)}{r.publisher ? ` by ${r.publisher}` : ""}</span> : null}</span>}
        actions={<>
          {v ? <LinkButton href={`/reports/${r.id}/download?format=html&v=${v.version}`} prefetch={false}>Download HTML</LinkButton> : null}
          {d.exports.map((e) => <LinkButton key={e.id} href={`/reports/${r.id}/download?export=${e.id}`} prefetch={false}>{e.format.toUpperCase()} ({Math.ceil(e.size / 1024)} KB)</LinkButton>)}
        </>} />

      {v ? (
        v.citation_check?.ok ? (
          <Notice tone="ok" className="mb-4">Citation check passed for version {v.version}: {v.citation_check.checked_citations} citations resolve to stored articles linked to the cited incidents ({fmtDateTime(v.citation_check.checked_at, tz, s.locale)}).
            {v.citation_check.warnings?.length ? ` Warnings: ${v.citation_check.warnings.join(" ")}` : ""}</Notice>
        ) : (
          <Notice tone="error" className="mb-4">Citation check FAILED for version {v.version}; this version cannot be published. {v.citation_check?.errors?.join(" ")}</Notice>
        )
      ) : <Notice tone="warn" className="mb-4">This version was not found.</Notice>}
      {d.changed.length ? (
        <Notice tone="warn" className="mb-4">
          {d.changed.length} cited incident{d.changed.length === 1 ? "" : "s"} changed after version {v?.version} was generated:{" "}
          {d.changed.map((c, i) => <span key={c.id}>{i ? ", " : ""}<Link className="underline" href={`/incidents/${c.id}`}>{c.ref}</Link>{c.cited_version ? ` (v${c.cited_version} → v${c.version})` : ""}</span>)}.
          {r.status === "published" ? " This published version does not reflect those changes." : " Regenerate the report, or check the changes before publishing."}
        </Notice>
      ) : null}
      {!isCurrent && v ? <Notice tone="info" className="mb-4">You are viewing version {v.version}; the current version is {r.current_version}. <Link className="underline" href={`/reports/${r.id}`}>View current</Link></Notice> : null}

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <div className="grid min-w-0 gap-4">
          <Card className="overflow-hidden">
            {v ? <iframe title="Report" sandbox="allow-popups allow-popups-to-escape-sandbox" srcDoc={srcDoc} className="h-[75vh] w-full bg-white" /> : null}
          </Card>
          {v ? (
            <Card>
              <CardHeader title={`Sources cited (${v.citations.length})`} description="Each citation is a stored article; follow it to the publisher, or to the incident it supports." />
              <Table>
                <thead><tr><Th>#</Th><Th>Article</Th><Th>Publisher</Th><Th>Published</Th><Th>Supports</Th></tr></thead>
                <tbody>
                  {v.citations.map((c) => (
                    <tr key={c.n}>
                      <Td className="text-xs">[{c.n}]</Td>
                      <Td className="max-w-md text-xs"><a href={c.url} target="_blank" rel="noopener noreferrer nofollow" className="hover:underline">{c.title_en || c.title}</a>
                        {c.link_status === "broken" ? <span className="ms-1 text-red-600">(link broken)</span> : null} <DemoTag show={!!c.is_demo} /></Td>
                      <Td className="text-xs"><span className="inline-flex items-center gap-1">{c.publisher} <ReliabilityBadge grade={c.reliability} /></span></Td>
                      <Td className="text-xs whitespace-nowrap">{fmtDateTime(c.published_at, tz, s.locale)}</Td>
                      <Td className="text-xs">{(c.incident_ids ?? []).map((iid, i) => <Link key={iid} href={`/incidents/${iid}`} className="me-1 text-primary hover:underline">{c.incident_refs?.[i] ?? "incident"}</Link>)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          ) : null}
        </div>

        <div className="grid content-start gap-4">
          {can.staff(s.role) ? (
            <Card>
              <CardHeader title="Workflow" description="Draft → in review → published. Only reviewers or admins publish, and only a version that passed the citation check." />
              <CardBody className="grid gap-3">
                {r.status === "draft" && can.write(s.role) ? (
                  <ActionForm action={setReportStatusAction} submit="Submit for review">
                    <input type="hidden" name="id" value={r.id} /><input type="hidden" name="to" value="in_review" />
                  </ActionForm>
                ) : null}
                {r.status === "in_review" && can.review(s.role) ? (
                  <>
                    <ActionForm action={setReportStatusAction} submit="Publish">
                      <input type="hidden" name="id" value={r.id} /><input type="hidden" name="to" value="published" />
                      <Input name="note" placeholder="Review note (optional)" maxLength={500} />
                      {d.changed.length && isCurrent ? (
                        <label className="flex items-start gap-2 text-xs"><input type="checkbox" name="acknowledge_changes" required className="mt-0.5" />
                          I have checked the {d.changed.length} changed incident{d.changed.length === 1 ? "" : "s"} and this version is still accurate.</label>
                      ) : null}
                    </ActionForm>
                    <ActionForm action={setReportStatusAction} submit="Return to draft" variant="outline">
                      <input type="hidden" name="id" value={r.id} /><input type="hidden" name="to" value="draft" />
                      <Input name="note" placeholder="What needs changing" maxLength={500} />
                    </ActionForm>
                  </>
                ) : null}
                {r.status !== "archived" && can.review(s.role) ? (
                  <ActionForm action={setReportStatusAction} submit="Archive" variant="outline">
                    <input type="hidden" name="id" value={r.id} /><input type="hidden" name="to" value="archived" />
                  </ActionForm>
                ) : null}
                {(r.status === "draft" || r.status === "in_review") && can.write(s.role) ? (
                  <ActionForm action={regenerateReportAction} submit="Regenerate as new version" variant="outline">
                    <input type="hidden" name="id" value={r.id} />
                    <Input name="note" placeholder="Reason for the new version" required minLength={3} maxLength={500} />
                  </ActionForm>
                ) : null}
              </CardBody>
            </Card>
          ) : null}
          {can.staff(s.role) && v ? (
            <Card>
              <CardHeader title="Export" description="Exports are rendered by the worker from the stored version, with citations intact." />
              <CardBody>
                <ActionForm action={exportReportAction} submit="Request export" variant="outline" className="flex flex-wrap items-center gap-2">
                  <input type="hidden" name="id" value={r.id} /><input type="hidden" name="version" value={v.version} />
                  <Select name="format" defaultValue="docx" className="w-28"><option value="docx">DOCX</option><option value="pdf">PDF</option><option value="html">HTML</option></Select>
                </ActionForm>
              </CardBody>
            </Card>
          ) : null}
          <Card>
            <CardHeader title="Version history" />
            <CardBody className="grid gap-2 text-xs">
              {d.versions.map((x) => (
                <Link key={x.id} href={`/reports/${r.id}?v=${x.version}`} className={`rounded-md border p-2 hover:border-primary ${x.version === v?.version ? "border-primary" : ""}`}>
                  <div className="flex items-center gap-1.5"><span className="font-medium">v{x.version}</span>
                    {x.ok ? <span className="text-emerald-700 dark:text-emerald-400">citations ok</span> : <span className="text-red-600">citations failed</span>}</div>
                  <div className="text-muted-foreground">{fmtDateTime(x.created_at, tz, s.locale)} · {x.generator ?? "—"}{x.email ? ` · ${x.email}` : ""}</div>
                  {x.change_note ? <div>{x.change_note}</div> : null}
                </Link>
              ))}
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
