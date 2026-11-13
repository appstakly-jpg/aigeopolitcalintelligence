import Link from "next/link";
import { ActionForm } from "@/components/ActionForm";
import { StatusBadge } from "@/components/badges";
import { Badge, Card, CardBody, CardHeader, Empty, Input, Label, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtDateTime, humanize } from "@/lib/format";
import { CONTEXT_COUNTRIES, countryName, GCC, t } from "@/lib/i18n";
import { taxonomyLabels } from "@/lib/queries";
import { first, isUuid, type SearchParams } from "@/lib/utils";
import { reviewAlertAction, saveRuleAction, testRuleAction, toggleRuleAction } from "./actions";

export const metadata = { title: "Alerts" };

interface Rule { id: string; name: string; enabled: boolean; countries: string[]; event_types: string[]; min_severity: number; verification_statuses: string[];
  keywords: string[]; channels: string[]; destination: { email?: string | null; telegram_chat_id?: string | null; webhook_url?: string | null };
  throttle_minutes: number; notify_updates: boolean; email: string; user_id: string }

export default async function AlertsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const status = first(sp.status);
  const incident = first(sp.incident);
  const edit = first(sp.edit);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const d = await withRls(s, async (tx) => {
    const alerts = await tx<{ id: string; kind: string; impact: string; status: string; status_reason: string | null; title: string; body: string; created_at: Date;
      sent_at: Date | null; incident_id: string; ref: string; rule_name: string; reviewer: string | null; reviewed_at: Date | null;
      deliveries: { channel: string; destination_masked: string | null; status: string; attempts: number; last_error: string | null; sent_at: string | null }[] | null }[]>`
      select a.id, a.kind, a.impact, a.status, a.status_reason, a.title, a.body, a.created_at, a.sent_at, a.incident_id, i.ref, r.name as rule_name,
             u.email as reviewer, a.reviewed_at,
             (select json_agg(json_build_object('channel', d.channel, 'destination_masked', d.destination_masked, 'status', d.status, 'attempts', d.attempts,
                     'last_error', d.last_error, 'sent_at', d.sent_at) order by d.created_at) from alert_deliveries d where d.alert_id = a.id) as deliveries
      from alerts a join incidents i on i.id = a.incident_id join alert_rules r on r.id = a.rule_id left join app_users u on u.id = a.reviewed_by
      where (${status ?? null}::text is null or a.status = ${status ?? null})
        and (${isUuid(incident) ? incident : null}::uuid is null or a.incident_id = ${isUuid(incident) ? incident : null}::uuid)
      order by (a.status = 'pending_review') desc, a.created_at desc limit 100`;
    const rules = await tx<Rule[]>`
      select r.*, u.email from alert_rules r join app_users u on u.id = r.user_id order by r.created_at`;
    return { alerts, rules, labels: await taxonomyLabels(tx) };
  });
  const editing = d.rules.find((r) => r.id === edit);
  const countries = [...GCC, ...CONTEXT_COUNTRIES];
  return (
    <>
      <PageHeader title={t(s.locale, "nav.alerts")}
        description="Alerts fire when an incident matches a rule. High-impact alerts wait for a reviewer before anything is sent. Updates, corrections and retractions follow earlier alerts; repeats are de-duplicated and throttled."
        actions={<form method="get" className="flex gap-1.5">
          <Select name="status" defaultValue={status ?? ""} className="w-auto">
            <option value="">All statuses</option>
            {["pending_review", "approved", "sent", "partially_sent", "failed", "suppressed", "rejected"].map((x) => <option key={x} value={x}>{humanize(x)}</option>)}
          </Select>
          <button type="submit" className="rounded-md border px-3 text-sm">Filter</button>
        </form>} />

      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader title={`Alerts (${d.alerts.length})`} description={can.staff(s.role) ? "All alerts in your organisation." : "Alerts from your own rules."} />
          <CardBody className="grid gap-2">
            {d.alerts.length === 0 ? <Empty>No alerts.</Empty> : d.alerts.map((a) => (
              <details key={a.id} className="rounded-md border" open={a.status === "pending_review"}>
                <summary className="flex cursor-pointer flex-wrap items-center gap-1.5 px-3 py-2 text-sm">
                  <StatusBadge status={a.status} />
                  {a.impact === "high" ? <Badge className="border-red-300 text-red-700 dark:text-red-300">High impact</Badge> : null}
                  <Badge>{humanize(a.kind)}</Badge>
                  <span className="font-medium">{a.title}</span>
                  <span className="ms-auto text-xs text-muted-foreground">{fmtDateTime(a.created_at, tz, s.locale)}</span>
                </summary>
                <div className="grid gap-2 border-t px-3 py-2 text-xs">
                  <div className="text-muted-foreground">Rule: {a.rule_name} · Incident <Link href={`/incidents/${a.incident_id}`} className="text-primary hover:underline">{a.ref}</Link>
                    {a.status_reason ? ` · ${a.status_reason}` : ""}{a.reviewer ? ` · reviewed by ${a.reviewer} ${fmtDateTime(a.reviewed_at, tz, s.locale)}` : ""}</div>
                  <pre className="rounded-md bg-muted p-2 font-sans whitespace-pre-wrap">{a.body}</pre>
                  {a.deliveries?.length ? (
                    <Table>
                      <thead><tr><Th>Channel</Th><Th>Destination</Th><Th>Status</Th><Th>Attempts</Th><Th>Detail</Th></tr></thead>
                      <tbody>
                        {a.deliveries.map((x, i) => (
                          <tr key={i}><Td>{x.channel}</Td><Td>{x.destination_masked ?? "—"}</Td><Td><StatusBadge status={x.status} /></Td><Td>{x.attempts}</Td>
                            <Td className="max-w-xs text-muted-foreground">{x.last_error ?? (x.sent_at ? `sent ${fmtDateTime(x.sent_at, tz, s.locale)}` : "")}</Td></tr>
                        ))}
                      </tbody>
                    </Table>
                  ) : <span className="text-muted-foreground">No delivery attempts yet.</span>}
                  {a.status === "pending_review" && can.review(s.role) ? (
                    <div className="flex flex-wrap gap-2">
                      <ActionForm action={reviewAlertAction} submit="Approve and send" className="flex items-center gap-2">
                        <input type="hidden" name="id" value={a.id} /><input type="hidden" name="decision" value="approved" />
                      </ActionForm>
                      <ActionForm action={reviewAlertAction} submit="Reject" variant="outline" className="flex items-center gap-2">
                        <input type="hidden" name="id" value={a.id} /><input type="hidden" name="decision" value="rejected" />
                        <Input name="note" placeholder="Reason" className="h-8 w-48" maxLength={500} />
                      </ActionForm>
                    </div>
                  ) : null}
                </div>
              </details>
            ))}
          </CardBody>
        </Card>

        <div className="grid content-start gap-4">
          <Card>
            <CardHeader title="Alert rules" description={can.admin(s.role) ? "All rules in the organisation." : "Your rules."} />
            <CardBody className="grid gap-2 text-xs">
              {d.rules.length === 0 ? <span className="text-muted-foreground">No rules yet.</span> : d.rules.map((r) => (
                <div key={r.id} className="rounded-md border p-2">
                  <div className="flex items-center gap-1.5"><span className="font-medium">{r.name}</span>{r.enabled ? null : <Badge>Disabled</Badge>}</div>
                  <div className="mt-1 text-muted-foreground">
                    {r.countries.length ? r.countries.join(", ") : "All countries"} · {r.event_types.length ? r.event_types.map((e) => d.labels[e]?.label ?? e).join(", ") : "all categories"} ·
                    severity ≥ {r.min_severity} · {r.channels.join(", ")} · throttle {r.throttle_minutes} min{r.notify_updates ? " · updates on" : ""}
                    {r.user_id !== s.userId ? ` · owner ${r.email}` : ""}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <Link href={`/alerts?edit=${r.id}`} className="rounded border px-2 py-1 hover:bg-muted">Edit</Link>
                    <ActionForm action={toggleRuleAction} submit={r.enabled ? "Disable" : "Enable"} variant="outline" className="flex">
                      <input type="hidden" name="id" value={r.id} /><input type="hidden" name="op" value={r.enabled ? "disable" : "enable"} />
                    </ActionForm>
                    {can.staff(s.role) ? (
                      <ActionForm action={testRuleAction} submit="Send test" variant="outline" className="flex"><input type="hidden" name="id" value={r.id} /></ActionForm>
                    ) : null}
                    <ActionForm action={toggleRuleAction} submit="Delete" variant="outline" className="flex" confirm="Delete this rule?">
                      <input type="hidden" name="id" value={r.id} /><input type="hidden" name="op" value="delete" />
                    </ActionForm>
                  </div>
                </div>
              ))}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title={editing ? `Edit rule: ${editing.name}` : "New rule"}
              description="Email works with SMTP settings; Slack needs a webhook saved under Settings; Telegram needs a bot token. Missing credentials are reported as 'skipped', never as sent." />
            <CardBody>
              <ActionForm action={saveRuleAction} submit={editing ? "Save rule" : "Create rule"}>
                {editing ? <input type="hidden" name="id" value={editing.id} /> : null}
                <Label>Name<Input name="name" required minLength={2} maxLength={120} defaultValue={editing?.name} /></Label>
                <Label hint="None selected = all">Countries
                  <select name="countries" multiple defaultValue={editing?.countries} className="h-24 rounded-md border border-input bg-card p-1 text-sm">
                    {countries.map((c) => <option key={c} value={c}>{countryName(c, s.locale)}</option>)}
                  </select>
                </Label>
                <Label hint="None selected = all">Categories
                  <select name="event_types" multiple defaultValue={editing?.event_types} className="h-24 rounded-md border border-input bg-card p-1 text-sm">
                    {Object.entries(d.labels).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                  </select>
                </Label>
                <div className="grid grid-cols-2 gap-2">
                  <Label>Minimum severity
                    <Select name="min_severity" defaultValue={String(editing?.min_severity ?? 3)}>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</Select>
                  </Label>
                  <Label>Throttle (minutes)<Input name="throttle_minutes" type="number" min={0} max={10080} defaultValue={editing?.throttle_minutes ?? 60} /></Label>
                </div>
                <Label hint="None selected = any">Verification
                  <select name="verification_statuses" multiple defaultValue={editing?.verification_statuses} className="h-20 rounded-md border border-input bg-card p-1 text-sm">
                    {["confirmed", "corroborated", "single_source", "disputed", "unverified"].map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
                  </select>
                </Label>
                <Label hint="Comma-separated">Keywords<Input name="keywords" defaultValue={editing?.keywords.join(", ")} /></Label>
                <fieldset className="grid gap-1 text-xs">
                  <legend className="mb-1 font-medium">Channels</legend>
                  {["email", "slack", "telegram", "webhook"].map((c) => (
                    <label key={c} className="flex items-center gap-2"><input type="checkbox" name="channels" value={c} defaultChecked={editing ? editing.channels.includes(c) : c === "email"} /> {humanize(c)}</label>
                  ))}
                </fieldset>
                <Label>Email<Input name="email" type="email" defaultValue={editing?.destination.email ?? s.email} /></Label>
                <Label>Telegram chat id<Input name="telegram_chat_id" defaultValue={editing?.destination.telegram_chat_id ?? ""} /></Label>
                <Label>Webhook URL (https)<Input name="webhook_url" type="url" defaultValue={editing?.destination.webhook_url ?? ""} /></Label>
                <label className="flex items-center gap-2 text-xs"><input type="checkbox" name="notify_updates" defaultChecked={editing?.notify_updates ?? true} /> Send updates, corrections and retractions</label>
              </ActionForm>
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
