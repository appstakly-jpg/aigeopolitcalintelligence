import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm } from "@/components/ActionForm";
import { DemoTag, GeoPrecision, ReliabilityBadge, ReviewBadge, SeverityBadge, StatusBadge, VerificationBadge } from "@/components/badges";
import { MapView } from "@/components/MapView";
import { Badge, Card, CardBody, CardHeader, Empty, Input, Label, Notice, PageHeader, Select, Table, Td, Textarea, Th } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { casualtyText, fmtDateTime, humanize } from "@/lib/format";
import { countryName } from "@/lib/i18n";
import { taxonomyLabels, type IncidentRow } from "@/lib/queries";
import { isUuid } from "@/lib/utils";
import { confirmIncidentAction, editIncidentAction, reviewIncidentAction, setStatusAction } from "./actions";

interface Incident extends IncidentRow {
  severity_rationale: string | null; casualty_note: string | null; uncertainty_notes: string | null; review_reason: string | null;
  verification_detail: Record<string, unknown> | null; context: Record<string, unknown> | null; geo_source: string | null;
  geo_entity_id: string | null; actors: string[]; sectors: string[]; official_statement_count: number; occurred_at_precision: string;
  gcc_relevance: number | null; created_by_agent: string | null; created_at: Date;
}
interface EvidenceRow {
  article_id: string; role: string; origin_key: string; match_score: number | null; added_by: string; added_at: Date; title: string;
  title_en: string | null; url: string; language: string; published_at: Date | null; retrieved_at: Date; stance: string; link_status: string;
  syndicated_from_article_id: string | null; attributed_origin: string | null; source_name: string; reliability: string; source_type: string;
  is_demo: boolean; injection_flags: string[]; excerpt: string | null; excerpt_en: string | null;
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  return { title: `Incident ${(await params).id.slice(0, 8)}` };
}

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const s = await requireSession();
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const staff = can.staff(s.role);

  const d = await withRls(s, async (tx) => {
    const [inc] = await tx<Incident[]>`select * from incidents where id = ${id}`;
    if (!inc) return null;
    const evidence = await tx<EvidenceRow[]>`
      select ia.article_id, ia.role, ia.origin_key, ia.match_score, ia.added_by, ia.added_at, a.title, a.title_en, a.url, a.language,
             a.published_at, a.retrieved_at, a.stance, a.link_status, a.syndicated_from_article_id, a.attributed_origin, a.is_demo,
             a.injection_flags, a.excerpt, a.excerpt_en, s.name as source_name, s.reliability, s.source_type
      from incident_articles ia join articles a on a.id = ia.article_id join sources s on s.id = a.source_id
      where ia.incident_id = ${id} order by a.published_at nulls last`;
    const claims = await tx<{ id: string; article_id: string; claim_type: string; text: string; text_en: string | null; attributed_to: string | null;
      is_official: boolean; stance: string; numeric_value: string | null; numeric_unit: string | null; confidence: number | null; extracted_by: string }[]>`
      select id, article_id, claim_type, text, text_en, attributed_to, is_official, stance, numeric_value, numeric_unit, confidence, extracted_by
      from claims where incident_id = ${id} order by created_at`;
    const edges = await tx<{ relation: string; article_id: string | null; claim_id: string | null; target_article_id: string | null; target_label: string | null }[]>`
      select relation, article_id, claim_id, target_article_id, target_label from claim_provenance
      where article_id = any(${evidence.map((e) => e.article_id)}::uuid[]) or claim_id = any(${claims.map((c) => c.id)}::uuid[])
      order by id`;
    const revisions = await tx<{ version: number; change_type: string; change_note: string | null; changed_fields: string[]; created_at: Date;
      changed_by_agent: string | null; email: string | null }[]>`
      select r.version, r.change_type, r.change_note, r.changed_fields, r.created_at, r.changed_by_agent, u.email
      from incident_revisions r left join app_users u on u.id = r.changed_by where r.incident_id = ${id} order by r.id desc`;
    const alerts = staff ? await tx<{ id: string; kind: string; impact: string; status: string; status_reason: string | null; created_at: Date; incident_version: number }[]>`
      select id, kind, impact, status, status_reason, created_at, incident_version from alerts where incident_id = ${id} order by created_at desc` : [];
    const review = staff ? (await tx<{ id: string; reason: string; priority: number; status: string }[]>`
      select id, reason, priority, status from analyst_reviews where item_type = 'incident' and item_id = ${id} and status = 'open' limit 1`)[0] ?? null : null;
    const places = can.write(s.role) ? await tx<{ id: string; name: string; kind: string; country_code: string | null }[]>`
      select id, name, kind, country_code from geo_entities order by kind, name` : [];
    const labels = await taxonomyLabels(tx);
    return { inc, evidence, claims, edges, revisions, alerts, review, places, labels };
  });
  if (!d) notFound();
  const { inc } = d;
  const vd = (inc.verification_detail ?? {}) as { notes?: string[]; independent_origins?: string[]; denying_origins?: string[];
    official_origins?: string[]; retracted_origins?: string[]; overrides?: Record<string, unknown>; analyst_confirmed?: boolean;
    needs_review_reasons?: string[] };
  const ctx = (inc.context ?? {}) as { gcc_exposure?: { country: string; country_code: string; pathways: string[] }[]; watch_indicators?: string[]; method?: string };
  const byArticle = new Map(d.evidence.map((e, i) => [e.article_id, { ...e, n: i + 1 }]));
  const label = (k: string) => d.labels[k]?.label ?? k;
  const overrides = Object.keys(vd.overrides ?? {});

  return (
    <>
      <PageHeader title={inc.title}
        meta={<span className="flex flex-wrap items-center gap-1.5">
          {inc.ref} · version {inc.version} · first reported {fmtDateTime(inc.first_reported_at, tz, s.locale)} · last updated {fmtDateTime(inc.updated_at, tz, s.locale)}
          <DemoTag show={inc.is_demo} />
        </span>}
        actions={<div className="flex flex-wrap items-center gap-1.5">
          <VerificationBadge status={inc.verification_status} />
          <SeverityBadge value={inc.severity} />
          <Badge title="Admiralty information credibility: 1 confirmed, 2 probably true, 3 possibly true, 4 doubtful, 5 improbable, 6 cannot be judged">
            Credibility {inc.credibility ?? "?"}
          </Badge>
          {staff ? <ReviewBadge status={inc.review_status} /> : null}
          {inc.status !== "active" ? <StatusBadge status={inc.status} /> : null}
          <Badge title="Analytic confidence in the incident record">Confidence: {inc.confidence}</Badge>
        </div>} />

      {inc.verification_status === "disputed" ? (
        <Notice tone="warn" className="mb-4">Contradictory reporting exists for this incident. Both the claims and the denials are listed below; neither is treated as settled.</Notice>
      ) : null}
      {inc.status === "rejected" || inc.review_status === "rejected" ? <Notice tone="error" className="mb-4">An analyst rejected this incident; it is hidden from client users.</Notice> : null}

      <div className="grid gap-4 xl:grid-cols-[1fr_400px]">
        <div className="grid min-w-0 gap-4">
          <Card>
            <CardHeader title="Assessment" description="Generated from the stored evidence; figures appear only when a source states them." />
            <CardBody className="grid gap-3 text-sm">
              <p>{inc.summary ?? "No summary yet."}</p>
              <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                <div><dt className="text-xs text-muted-foreground">Categories</dt><dd>{inc.categories.map((c) => <span key={c} className={c === inc.primary_category ? "font-medium" : ""}>{label(c)}{" "}</span>)}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Casualties</dt><dd>{casualtyText(inc.casualties_killed, inc.casualties_injured)}{inc.casualty_note ? <div className="text-xs text-muted-foreground">{inc.casualty_note}</div> : null}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Severity rationale</dt><dd className="text-xs">{inc.severity_rationale ?? "—"}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Occurred</dt><dd>{inc.occurred_at ? fmtDateTime(inc.occurred_at, tz, s.locale) : "Not stated"} <span className="text-xs text-muted-foreground">({inc.occurred_at_precision})</span></dd></div>
                <div><dt className="text-xs text-muted-foreground">Actors named in reporting</dt><dd>{inc.actors.length ? inc.actors.join(", ") : "—"}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Sectors</dt><dd>{inc.sectors.length ? inc.sectors.map(humanize).join(", ") : "—"}</dd></div>
              </dl>
              {inc.uncertainty_notes ? <div className="rounded-md bg-muted p-2 text-xs"><span className="font-medium">Uncertainty: </span>{inc.uncertainty_notes}</div> : null}
            </CardBody>
          </Card>

          <Card id="evidence">
            <CardHeader title={`Evidence (${d.evidence.length} reports, ${inc.independent_source_count} independent origin${inc.independent_source_count === 1 ? "" : "s"})`}
              description="Each report with its role, independence key and source grade. Copies share an origin key and do not count as corroboration." />
            {d.evidence.length === 0 ? <CardBody><Empty>No evidence attached.</Empty></CardBody> : (
              <Table>
                <thead><tr><Th>#</Th><Th>Report</Th><Th>Source</Th><Th>Role</Th><Th>Origin key</Th><Th>Published</Th></tr></thead>
                <tbody>
                  {d.evidence.map((e, i) => (
                    <tr key={e.article_id} id={`a-${e.article_id}`}>
                      <Td className="text-xs text-muted-foreground">{i + 1}</Td>
                      <Td className="max-w-md">
                        <a href={e.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-start gap-1 hover:underline" dir={e.language === "ar" ? "rtl" : "ltr"}>
                          {e.title} <ExternalLink className="mt-1 size-3 shrink-0 opacity-60" />
                        </a>
                        {e.language !== "en" && e.title_en ? <div className="text-xs text-muted-foreground">EN: {e.title_en}</div> : null}
                        <div className="mt-1 flex flex-wrap gap-1">
                          <DemoTag show={e.is_demo} />
                          {e.stance !== "report" ? <Badge className="border-fuchsia-300 text-fuchsia-700 dark:text-fuchsia-300">{humanize(e.stance)}</Badge> : null}
                          {e.syndicated_from_article_id ? <Badge>Syndicated copy</Badge> : null}
                          {e.link_status === "broken" ? <Badge className="border-red-300 text-red-700">Link broken</Badge> : null}
                          {staff && e.injection_flags.length ? <Badge className="border-red-300 text-red-700" title={e.injection_flags.join(", ")}>Injection text removed</Badge> : null}
                        </div>
                      </Td>
                      <Td className="text-xs"><div>{e.source_name}</div><div className="mt-0.5 flex items-center gap-1"><ReliabilityBadge grade={e.reliability} /> {humanize(e.source_type)}</div></Td>
                      <Td className="text-xs">{humanize(e.role)}{e.match_score ? <div className="text-muted-foreground">match {Number(e.match_score).toFixed(2)}</div> : null}</Td>
                      <Td className="font-mono text-[11px]">{e.origin_key}</Td>
                      <Td className="text-xs whitespace-nowrap">{fmtDateTime(e.published_at ?? e.retrieved_at, tz, s.locale)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <Card>
            <CardHeader title="Claims" description="Attributable statements extracted from each report. Official statements are claims by the stated party, not established fact." />
            {d.claims.length === 0 ? <CardBody><Empty>No claims extracted.</Empty></CardBody> : (
              <Table>
                <thead><tr><Th>Claim</Th><Th>Type</Th><Th>Stance</Th><Th>Attributed to</Th><Th>Report</Th></tr></thead>
                <tbody>
                  {d.claims.map((c) => {
                    const a = byArticle.get(c.article_id);
                    return (
                      <tr key={c.id}>
                        <Td className="max-w-md text-xs">{c.text_en ?? c.text}{c.numeric_value ? <span className="text-muted-foreground"> ({Number(c.numeric_value)} {c.numeric_unit})</span> : null}</Td>
                        <Td className="text-xs">{humanize(c.claim_type)}</Td>
                        <Td className="text-xs">{c.stance === "asserts" ? "Asserts" : <span className="font-medium text-fuchsia-700 dark:text-fuchsia-300">{humanize(c.stance)}</span>}</Td>
                        <Td className="text-xs">{c.attributed_to ?? "Publisher"}{c.is_official ? <Badge className="ms-1">Official</Badge> : null}</Td>
                        <Td className="text-xs">{a ? <a href={`#a-${c.article_id}`} className="text-primary hover:underline">#{a.n} {a.source_name}</a> : "—"}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            )}
          </Card>

          <Card>
            <CardHeader title="Provenance graph" description="Where each report and claim came from: publisher, copied-from relationships and attributions." />
            <CardBody>
              {d.edges.length === 0 ? <Empty>No provenance edges recorded.</Empty> : (
                <ul className="grid gap-1 text-xs">
                  {d.edges.map((e, i) => {
                    const from = e.article_id ? byArticle.get(e.article_id) : undefined;
                    const to = e.target_article_id ? byArticle.get(e.target_article_id) : undefined;
                    return (
                      <li key={i} className="flex flex-wrap items-center gap-1.5">
                        <span className="rounded border px-1.5 py-0.5">{from ? `#${from.n} ${from.source_name}` : e.claim_id ? "claim" : "report"}</span>
                        <span className="text-muted-foreground">— {humanize(e.relation)} →</span>
                        <span className="rounded border px-1.5 py-0.5">{to ? `#${to.n} ${to.source_name}` : e.target_label ?? "external"}</span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Version history" description="Every change is versioned with a full snapshot: new evidence, corrections, retractions and analyst decisions." />
            <CardBody>
              <ol className="grid gap-2 text-xs">
                {d.revisions.map((r) => (
                  <li key={`${r.version}-${r.change_type}-${r.created_at.toISOString()}`} className="rounded-md border p-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium">v{r.version}</span>
                      <StatusBadge status={r.change_type} />
                      <span className="text-muted-foreground">{fmtDateTime(r.created_at, tz, s.locale)} · {r.email ?? r.changed_by_agent ?? "system"}</span>
                    </div>
                    {r.change_note ? <div className="mt-1">{r.change_note}</div> : null}
                    {r.changed_fields.length ? <div className="mt-1 text-muted-foreground">Changed: {r.changed_fields.join(", ")}</div> : null}
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
        </div>

        <div className="grid content-start gap-4">
          <Card>
            <CardHeader title="Location" />
            <CardBody className="grid gap-2 text-sm">
              <div>{inc.location_name ?? "Location not resolved"} <span className="text-muted-foreground">· {countryName(inc.country_code, s.locale)}</span></div>
              <GeoPrecision precision={inc.geo_precision} confidence={inc.geo_confidence} />
              {inc.geo_source ? <div className="text-xs text-muted-foreground">Coordinates: {inc.geo_source}</div> : <div className="text-xs text-muted-foreground">No coordinates stored; the incident is not drawn on the map.</div>}
              {inc.affected_countries.length ? <div className="text-xs">Also affects: {inc.affected_countries.map((c) => countryName(c, s.locale)).join(", ")}</div> : null}
              {inc.lat !== null && inc.lon !== null ? (
                <MapView height={220} zoom={inc.geo_precision === "country" ? 4 : 5.5} center={[inc.lon, inc.lat]}
                  points={[{ id: inc.id, ref: inc.ref, title: inc.title, lat: inc.lat, lon: inc.lon, severity: inc.severity, verification: inc.verification_status,
                    precision: inc.geo_precision, geoConfidence: inc.geo_confidence, location: inc.location_name, isDemo: inc.is_demo }]} />
              ) : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Verification" />
            <CardBody className="grid gap-2 text-xs">
              {(vd.notes ?? []).map((n, i) => <p key={i}>{n}</p>)}
              <div><span className="text-muted-foreground">Independent origins:</span> {(vd.independent_origins ?? []).join(", ") || "—"}</div>
              {vd.denying_origins?.length ? <div><span className="text-muted-foreground">Denials from:</span> {vd.denying_origins.join(", ")}</div> : null}
              {vd.official_origins?.length ? <div><span className="text-muted-foreground">Official statements from:</span> {vd.official_origins.join(", ")}</div> : null}
              {vd.retracted_origins?.length ? <div><span className="text-muted-foreground">Retracted by:</span> {vd.retracted_origins.join(", ")}</div> : null}
              {vd.analyst_confirmed ? <Badge className="w-fit border-emerald-300 text-emerald-700">Confirmed by analyst</Badge> : null}
              {overrides.length ? <div className="text-amber-700 dark:text-amber-300">Analyst overrides in effect: {overrides.join(", ")}</div> : null}
            </CardBody>
          </Card>

          {ctx.gcc_exposure?.length || ctx.watch_indicators?.length ? (
            <Card>
              <CardHeader title="Regional context" description={ctx.method} />
              <CardBody className="grid gap-2 text-xs">
                {ctx.gcc_exposure?.map((g) => (
                  <div key={g.country_code}><span className="font-medium">{g.country}:</span> {g.pathways.join(" ")}</div>
                ))}
                {ctx.watch_indicators?.length ? (
                  <div><div className="font-medium">Watch indicators</div><ul className="list-disc ps-4">{ctx.watch_indicators.map((w) => <li key={w}>{w}</li>)}</ul></div>
                ) : null}
              </CardBody>
            </Card>
          ) : null}

          {staff ? (
            <Card>
              <CardHeader title="Alerts for this incident" action={<Link href={`/alerts?incident=${inc.id}`} className="text-xs text-primary hover:underline">Open</Link>} />
              <CardBody className="grid gap-1 text-xs">
                {d.alerts.length === 0 ? <span className="text-muted-foreground">No alerts generated.</span> : d.alerts.map((a) => (
                  <div key={a.id} className="flex flex-wrap items-center gap-1">
                    <StatusBadge status={a.status} /> {humanize(a.kind)} (v{a.incident_version}, {a.impact} impact) · {fmtDateTime(a.created_at, tz, s.locale)}
                    {a.status_reason ? <span className="text-muted-foreground">· {a.status_reason}</span> : null}
                  </div>
                ))}
              </CardBody>
            </Card>
          ) : null}

          {staff ? (
            <Card>
              <CardHeader title="Analyst review" description={d.review ? `Open: ${d.review.reason}` : inc.review_reason ?? "No open review item."} />
              <CardBody className="grid gap-4">
                {can.review(s.role) ? (
                  <>
                    <ActionForm action={reviewIncidentAction} submit="Record decision">
                      <input type="hidden" name="id" value={inc.id} />
                      <Label>Decision
                        <Select name="decision" defaultValue="approved">
                          <option value="approved">Approve for distribution</option>
                          <option value="changes_requested">Request changes</option>
                          <option value="rejected">Reject (hide from clients)</option>
                        </Select>
                      </Label>
                      <Label>Note<Textarea name="note" required minLength={3} maxLength={1000} placeholder="What you checked and why" /></Label>
                    </ActionForm>
                    {inc.verification_status !== "confirmed" ? (
                      <ActionForm action={confirmIncidentAction} submit="Mark confirmed" variant="outline">
                        <input type="hidden" name="id" value={inc.id} />
                        <Label hint="Only when you have checked the event against independent evidence.">Confirmation note
                          <Textarea name="note" required minLength={3} maxLength={1000} />
                        </Label>
                      </ActionForm>
                    ) : null}
                  </>
                ) : <p className="text-xs text-muted-foreground">Reviewers and admins record review decisions.</p>}
                {can.write(s.role) ? (
                  <ActionForm action={setStatusAction} submit={inc.status === "closed" ? "Reopen incident" : "Close incident"} variant="outline">
                    <input type="hidden" name="id" value={inc.id} />
                    <input type="hidden" name="status" value={inc.status === "closed" ? "active" : "closed"} />
                    <Label>Note<Input name="note" required minLength={3} maxLength={1000} /></Label>
                  </ActionForm>
                ) : null}
              </CardBody>
            </Card>
          ) : null}

          {can.write(s.role) ? (
            <Card>
              <CardHeader title="Correct this incident" description="Corrections are stored as overrides, re-derived by the pipeline, versioned, and sent as correction alerts to subscribers who already received this incident." />
              <CardBody>
                <ActionForm action={editIncidentAction} submit="Save correction">
                  <input type="hidden" name="id" value={inc.id} />
                  <Label>Title<Input name="title" placeholder={inc.title} maxLength={300} /></Label>
                  <Label>Location
                    <Select name="location" defaultValue="">
                      <option value="">(unchanged)</option>
                      {d.places.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.kind}{p.country_code ? ` · ${p.country_code}` : ""}</option>)}
                    </Select>
                  </Label>
                  <div className="grid grid-cols-2 gap-2">
                    <Label hint="Leave blank if not stated">Killed<Input name="killed" type="number" min={0} /></Label>
                    <Label hint="Leave blank if not stated">Injured<Input name="injured" type="number" min={0} /></Label>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Label>Severity override
                      <Select name="severity" defaultValue=""><option value="">(computed)</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</Select>
                    </Label>
                    <Label>Override reason<Input name="severity_reason" maxLength={500} /></Label>
                  </div>
                  <Label hint="Ctrl/Cmd-click for several; leave empty to keep the extracted categories">Categories
                    <select name="categories" multiple className="h-28 rounded-md border border-input bg-card p-1 text-sm">
                      {Object.entries(d.labels).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                    </select>
                  </Label>
                  <Label>Summary<Textarea name="summary" maxLength={4000} placeholder="Leave blank to keep the generated summary" /></Label>
                  <Label>Reason for the correction (required)<Textarea name="note" required minLength={3} maxLength={1000} /></Label>
                  {overrides.length ? <label className="flex items-center gap-2 text-xs"><input type="checkbox" name="reset" /> Remove all analyst overrides instead</label> : null}
                </ActionForm>
              </CardBody>
            </Card>
          ) : null}
        </div>
      </div>
    </>
  );
}
