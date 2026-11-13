import { ActionForm } from "@/components/ActionForm";
import { DemoTag, ReliabilityBadge, StatusBadge } from "@/components/badges";
import { Badge, Card, CardBody, CardHeader, Input, Label, Notice, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtAgo, fmtDateTime, humanize } from "@/lib/format";
import { t } from "@/lib/i18n";
import { addSourceAction, sourceDecisionAction, sourceJobAction, updateSourceAction } from "./actions";

export const metadata = { title: "Sources" };

interface Src {
  id: string; name: string; publisher: string | null; adapter: string; url: string; homepage_url: string | null; language: string; country_focus: string[];
  source_type: string; ownership: string | null; reliability: string; reliability_rationale: string | null; origin_group: string | null; license_type: string;
  license_notes: string | null; terms_url: string | null; usage_policy: string; max_excerpt_chars: number; fetch_interval_minutes: number; status: string;
  enabled: boolean; is_demo: boolean; verified_at: Date | null; verification_note: string | null; next_fetch_at: Date | null; last_success_at: Date | null;
  last_error: string | null; consecutive_failures: number; articles_7d: number;
  health: { checked_at: string; ok: boolean; http_status: number | null; items_found: number | null; items_new: number | null; latency_ms: number | null; error: string | null; robots_allowed: boolean | null }[] | null;
}

export default async function SourcesPage() {
  const s = await requireSession();
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const d = await withRls(s, async (tx) => {
    const sources = await tx<Src[]>`
      select s.*, (select count(*)::int from articles a where a.source_id = s.id and a.retrieved_at > now() - interval '7 days') as articles_7d,
             (select json_agg(h order by h.checked_at desc) from (select checked_at, ok, http_status, items_found, items_new, latency_ms, error, robots_allowed
                from source_health h where h.source_id = s.id order by checked_at desc limit 8) h) as health
      from sources s order by s.enabled desc, s.status, s.name`;
    const catalog = can.write(s.role) ? await tx<{ key: string; name: string; language: string; license_type: string; verified_at: Date | null }[]>`
      select key, name, language, license_type, verified_at from source_catalog c
      where not exists (select 1 from sources x where x.catalog_key = c.key) order by name` : [];
    return { sources, catalog };
  });
  return (
    <>
      <PageHeader title={t(s.locale, "nav.sources")}
        description="Only approved, licence-reviewed public feeds are collected. The collector honours robots.txt, rate limits and each source's usage policy, and never logs in, bypasses paywalls or reads private accounts. Reliability is graded A–F (Admiralty scale) with a written rationale."
        actions={can.staff(s.role) ? (
          <>
            <ActionForm action={sourceJobAction} submit="Run ingestion now" variant="outline" className="flex"><input type="hidden" name="op" value="run_pipeline" /></ActionForm>
            {can.write(s.role) ? <ActionForm action={sourceJobAction} submit="Propose catalog sources" variant="outline" className="flex"><input type="hidden" name="op" value="source_scout" /></ActionForm> : null}
          </>
        ) : null} />
      {d.sources.length === 0 ? <Notice tone="warn">No sources yet. Add one from the vetted catalog below.</Notice> : null}
      <div className="grid gap-2">
        {d.sources.map((x) => (
          <details key={x.id} className="rounded-lg border bg-card">
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm">
              <span className={`size-2 rounded-full ${!x.enabled ? "bg-slate-400" : x.consecutive_failures ? "bg-red-500" : "bg-emerald-500"}`} aria-hidden />
              <span className="font-medium">{x.name}</span>
              <ReliabilityBadge grade={x.reliability} />
              <StatusBadge status={x.status} />
              {x.enabled ? <Badge className="border-emerald-300 text-emerald-700 dark:text-emerald-300">Enabled</Badge> : <Badge>Disabled</Badge>}
              <DemoTag show={x.is_demo} />
              <span className="text-xs text-muted-foreground">{humanize(x.source_type)} · {x.language.toUpperCase()} · {humanize(x.license_type)}</span>
              <span className="ms-auto text-xs text-muted-foreground">
                {x.articles_7d} items/7d · last success {fmtAgo(x.last_success_at)}{x.consecutive_failures ? ` · ${x.consecutive_failures} failure(s)` : ""}
              </span>
            </summary>
            <div className="grid gap-4 border-t p-3 text-xs lg:grid-cols-3">
              <div className="grid content-start gap-1">
                <div><span className="text-muted-foreground">Feed:</span> <a href={x.url} target="_blank" rel="noopener noreferrer nofollow" className="break-all text-primary hover:underline">{x.url}</a></div>
                <div><span className="text-muted-foreground">Publisher / ownership:</span> {x.publisher ?? "—"} / {x.ownership ?? "—"}</div>
                <div><span className="text-muted-foreground">Origin group (independence):</span> {x.origin_group ?? "own"}</div>
                <div><span className="text-muted-foreground">Reliability rationale:</span> {x.reliability_rationale ?? "—"}</div>
                <div><span className="text-muted-foreground">Licence:</span> {humanize(x.license_type)}{x.license_notes ? ` · ${x.license_notes}` : ""} {x.terms_url ? <a className="text-primary hover:underline" href={x.terms_url} target="_blank" rel="noopener noreferrer nofollow">terms</a> : null}</div>
                <div><span className="text-muted-foreground">Usage:</span> {humanize(x.usage_policy)} · excerpts ≤ {x.max_excerpt_chars} chars · every {x.fetch_interval_minutes} min</div>
                <div><span className="text-muted-foreground">Verified:</span> {x.verified_at ? `${fmtDateTime(x.verified_at, tz, s.locale)}${x.verification_note ? ` · ${x.verification_note}` : ""}` : "not yet"}</div>
                <div><span className="text-muted-foreground">Next fetch:</span> {fmtDateTime(x.next_fetch_at, tz, s.locale)}</div>
                {x.last_error ? <div className="text-red-600">Last error: {x.last_error}</div> : null}
              </div>
              <div>
                <div className="mb-1 font-medium">Recent checks</div>
                {x.health?.length ? (
                  <Table>
                    <thead><tr><Th>When</Th><Th>Result</Th><Th>Items</Th><Th>ms</Th></tr></thead>
                    <tbody>
                      {x.health.map((h, i) => (
                        <tr key={i}><Td>{fmtDateTime(h.checked_at, tz, s.locale)}</Td>
                          <Td>{h.ok ? "OK" : <span className="text-red-600">{h.robots_allowed === false ? "robots.txt disallows" : h.error ?? `HTTP ${h.http_status}`}</span>}</Td>
                          <Td>{h.items_found ?? "—"}/{h.items_new ?? "—"} new</Td><Td>{h.latency_ms ?? "—"}</Td></tr>
                      ))}
                    </tbody>
                  </Table>
                ) : <span className="text-muted-foreground">No checks yet.</span>}
              </div>
              <div className="grid content-start gap-2">
                {can.staff(s.role) ? (
                  <div className="flex flex-wrap gap-1.5">
                    {x.enabled ? <ActionForm action={sourceJobAction} submit="Fetch now" variant="outline" className="flex"><input type="hidden" name="op" value="fetch_source" /><input type="hidden" name="id" value={x.id} /></ActionForm> : null}
                    <ActionForm action={sourceJobAction} submit="Verify (robots, reachability)" variant="outline" className="flex"><input type="hidden" name="op" value="verify_source" /><input type="hidden" name="id" value={x.id} /></ActionForm>
                  </div>
                ) : null}
                {can.admin(s.role) ? (
                  <div className="flex flex-wrap gap-1.5">
                    {x.status === "proposed" ? <ActionForm action={sourceDecisionAction} submit="Approve" className="flex"><input type="hidden" name="id" value={x.id} /><input type="hidden" name="op" value="approve" /></ActionForm> : null}
                    {x.status === "approved" && !x.enabled ? <ActionForm action={sourceDecisionAction} submit="Enable" className="flex"><input type="hidden" name="id" value={x.id} /><input type="hidden" name="op" value="enable" /></ActionForm> : null}
                    {x.enabled ? <ActionForm action={sourceDecisionAction} submit="Disable" variant="outline" className="flex"><input type="hidden" name="id" value={x.id} /><input type="hidden" name="op" value="disable" /></ActionForm> : null}
                    {x.status === "proposed" ? <ActionForm action={sourceDecisionAction} submit="Reject" variant="outline" className="flex"><input type="hidden" name="id" value={x.id} /><input type="hidden" name="op" value="reject" /></ActionForm> : null}
                    {x.status === "approved" ? <ActionForm action={sourceDecisionAction} submit="Retire" variant="outline" className="flex"><input type="hidden" name="id" value={x.id} /><input type="hidden" name="op" value="retire" /></ActionForm> : null}
                  </div>
                ) : null}
                {can.write(s.role) ? (
                  <ActionForm action={updateSourceAction} submit="Save assessment" variant="outline">
                    <input type="hidden" name="id" value={x.id} />
                    <div className="grid grid-cols-2 gap-1.5">
                      <Label>Reliability<Select name="reliability" defaultValue={x.reliability}>{["A", "B", "C", "D", "E", "F"].map((g) => <option key={g}>{g}</option>)}</Select></Label>
                      <Label>Interval (min)<Input name="fetch_interval_minutes" type="number" min={5} max={1440} defaultValue={x.fetch_interval_minutes} /></Label>
                    </div>
                    <Label>Rationale<Input name="reliability_rationale" defaultValue={x.reliability_rationale ?? ""} required minLength={5} /></Label>
                    <div className="grid grid-cols-2 gap-1.5">
                      <Label>Usage<Select name="usage_policy" defaultValue={x.usage_policy}>{["metadata_only", "headline_link_excerpt", "full_text"].map((u) => <option key={u} value={u}>{humanize(u)}</option>)}</Select></Label>
                      <Label>Excerpt chars<Input name="max_excerpt_chars" type="number" min={0} max={2000} defaultValue={x.max_excerpt_chars} /></Label>
                    </div>
                    <Label hint={can.admin(s.role) ? undefined : "Admins only"}>Licence
                      <Select name="license_type" defaultValue={x.license_type}>{["public_domain", "open_license", "attribution", "licensed", "review_required", "prohibited", "synthetic"].map((u) => <option key={u} value={u}>{humanize(u)}</option>)}</Select>
                    </Label>
                    <Label>Licence notes<Input name="license_notes" defaultValue={x.license_notes ?? ""} maxLength={1000} /></Label>
                  </ActionForm>
                ) : null}
              </div>
            </div>
          </details>
        ))}
      </div>

      {can.write(s.role) ? (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Add from the vetted catalog" description="Catalog entries record the publisher's terms, licence notes and a reliability rationale. Added sources start as 'proposed'." />
            <CardBody>
              {d.catalog.length === 0 ? <span className="text-xs text-muted-foreground">Every catalog source has been added.</span> : (
                <ActionForm action={addSourceAction} submit="Add source" className="flex flex-wrap items-center gap-2">
                  <Select name="catalog_key" className="w-auto max-w-full">
                    {d.catalog.map((c) => <option key={c.key} value={c.key}>{c.name} ({c.language}, {humanize(c.license_type)}{c.verified_at ? ", feed verified" : ", not verified"})</option>)}
                  </Select>
                </ActionForm>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Propose a custom feed" description="RSS or JSON feeds the publisher offers publicly. The worker checks robots.txt and reachability; an admin records the licence and approves." />
            <CardBody>
              <ActionForm action={addSourceAction} submit="Propose">
                <div className="grid grid-cols-2 gap-2">
                  <Label>Name<Input name="name" required minLength={2} /></Label>
                  <Label>Publisher<Input name="publisher" required minLength={2} /></Label>
                </div>
                <Label>Feed URL (https)<Input name="url" type="url" required /></Label>
                <div className="grid grid-cols-3 gap-2">
                  <Label>Format<Select name="adapter"><option value="rss">RSS/Atom</option><option value="json_api">JSON API</option></Select></Label>
                  <Label>Language<Select name="language"><option value="en">English</option><option value="ar">Arabic</option></Select></Label>
                  <Label>Type<Select name="source_type">{["news_media", "wire_agency", "state_media", "government", "intl_org", "think_tank", "ngo"].map((v) => <option key={v} value={v}>{humanize(v)}</option>)}</Select></Label>
                </div>
                <Label>Terms of use URL<Input name="terms_url" type="url" /></Label>
              </ActionForm>
            </CardBody>
          </Card>
        </div>
      ) : null}
    </>
  );
}
