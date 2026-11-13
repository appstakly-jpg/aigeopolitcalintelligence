import { ActionForm } from "@/components/ActionForm";
import { StatusBadge } from "@/components/badges";
import { Badge, Card, CardBody, CardHeader, Input, Label, Notice, PageHeader, Select, Table, Td, Th } from "@/components/ui/primitives";
import { requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { fmtAgo, fmtDateTime, fmtNumber, fmtUsd, humanize } from "@/lib/format";
import { t } from "@/lib/i18n";
import { addMemberAction, memberRoleAction, saveEventTypeAction, savePoliciesAction, testPlanChangeAction } from "./actions";

export const metadata = { title: "Administration" };

interface TenantSettings {
  timezone?: string; reports?: { daily_brief_hour?: number }; llm?: { monthly_budget_usd?: number };
  review_policy?: Record<string, number | boolean>; verification_policy?: Record<string, number | boolean>;
}

interface Plan { id: string; name: string; description: string | null; price_monthly_minor: number | null; currency: string | null;
  limits: Record<string, number>; features: Record<string, boolean>; is_active: boolean }

export default async function AdminPage() {
  const s = await requireSession(["admin"]);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const env = getEnv();
  const d = await withRls(s, async (tx) => {
    const [tenant] = await tx<{ name: string; slug: string; plan_id: string; settings: TenantSettings; branding: Record<string, unknown>; is_demo: boolean }[]>`
      select name, slug, plan_id, settings, branding, is_demo from tenants where id = ${s.tenantId}`;
    const plans = await tx<Plan[]>`select * from plans order by price_monthly_minor nulls first, id`;
    const members = await tx<{ user_id: string; role: string; email: string; display_name: string | null; last_login_at: Date | null; disabled: boolean }[]>`
      select m.user_id, m.role, u.email, u.display_name, u.last_login_at, u.disabled from memberships m join app_users u on u.id = m.user_id order by m.role, u.email`;
    const usage = (await tx<{ sources: number; rules: number; reports_today: number; members: number }[]>`
      select (select count(*)::int from sources where status <> 'retired') as sources, (select count(*)::int from alert_rules) as rules,
             (select count(*)::int from reports where created_at >= date_trunc('day', now())) as reports_today, (select count(*)::int from memberships) as members`)[0];
    const llm = await tx<{ agent: string; model: string; calls: number; failures: number; input_tokens: number; output_tokens: number; cost: string; avg_ms: number }[]>`
      select agent, model, count(*)::int as calls, count(*) filter (where not success)::int as failures, sum(input_tokens)::int as input_tokens,
             sum(output_tokens)::int as output_tokens, sum(cost_usd)::text as cost, avg(latency_ms)::int as avg_ms
      from llm_usage where created_at > now() - interval '30 days' group by agent, model order by sum(cost_usd) desc`;
    const llmMonth = (await tx<{ cost: string }[]>`select coalesce(sum(cost_usd), 0)::text as cost from llm_usage where created_at >= date_trunc('month', now())`)[0].cost;
    const jobs = await tx<{ id: number; kind: string; status: string; attempts: number; last_error: string | null; result: unknown; created_at: Date; finished_at: Date | null; email: string | null }[]>`
      select j.id, j.kind, j.status, j.attempts, j.last_error, j.result, j.created_at, j.finished_at, u.email
      from jobs j left join app_users u on u.id = j.requested_by order by j.id desc limit 40`;
    const auditRows = await tx<{ id: number; action: string; entity_type: string | null; entity_id: string | null; details: unknown; created_at: Date; email: string | null; actor_agent: string | null; ip: string | null }[]>`
      select a.id, a.action, a.entity_type, a.entity_id, a.details, a.created_at, u.email, a.actor_agent, a.ip
      from audit_log a left join app_users u on u.id = a.actor_user_id order by a.id desc limit 60`;
    const types = await tx<{ key: string; label: string; label_ar: string | null; parent_key: string | null; default_severity: number; keywords_en: string[]; keywords_ar: string[];
      review_required: boolean; enabled: boolean; tenant_id: string | null }[]>`
      select distinct on (key) key, label, label_ar, parent_key, default_severity, keywords_en, keywords_ar, review_required, enabled, tenant_id
      from event_types order by key, tenant_id nulls last`;
    return { tenant, plans, members, usage, llm, llmMonth, jobs, auditRows, types };
  });
  const plan = d.plans.find((p) => p.id === d.tenant.plan_id);
  const rp = d.tenant.settings.review_policy ?? {};
  const vp = d.tenant.settings.verification_policy ?? {};
  const billingMode = process.env.BILLING_MODE ?? "test";
  return (
    <>
      <PageHeader title={t(s.locale, "nav.admin")} description={`${d.tenant.name} (${d.tenant.slug})${d.tenant.is_demo ? " · demo organisation" : ""}`} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Plan, limits and billing" description={`Billing mode: ${billingMode}. Prices are configuration, not validated market pricing.`} />
          <CardBody className="grid gap-3 text-sm">
            <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{plan?.name ?? d.tenant.plan_id}</span>
              <Badge>{plan?.price_monthly_minor != null ? `${(plan.price_monthly_minor / 100).toFixed(2)} ${plan.currency ?? ""}/month` : "Price not configured"}</Badge></div>
            <Table>
              <thead><tr><Th>Limit</Th><Th>Used</Th><Th>Plan</Th></tr></thead>
              <tbody>
                <tr><Td>Seats</Td><Td>{d.usage.members}</Td><Td>{plan?.limits.seats ?? "—"}</Td></tr>
                <tr><Td>Sources</Td><Td>{d.usage.sources}</Td><Td>{plan?.limits.sources ?? "—"}</Td></tr>
                <tr><Td>Alert rules</Td><Td>{d.usage.rules}</Td><Td>{plan?.limits.alert_rules ?? "—"}</Td></tr>
                <tr><Td>Reports today</Td><Td>{d.usage.reports_today}</Td><Td>{plan?.limits.reports_per_day ?? "—"}</Td></tr>
                <tr><Td>LLM spend this month</Td><Td>{fmtUsd(d.llmMonth)}</Td><Td>{plan?.limits.llm_monthly_usd != null ? fmtUsd(plan.limits.llm_monthly_usd) : "—"} (daily cap {fmtUsd(env.llmDailyBudgetUsd)})</Td></tr>
              </tbody>
            </Table>
            <div className="flex flex-wrap gap-1">{Object.entries(plan?.features ?? {}).map(([k, v]) => <Badge key={k} className={v ? "border-emerald-300 text-emerald-700" : "opacity-60"}>{humanize(k)}: {v ? "on" : "off"}</Badge>)}</div>
            {billingMode === "test" ? (
              <ActionForm action={testPlanChangeAction} submit="Switch plan (test mode, no charge)" variant="outline" className="flex flex-wrap items-center gap-2">
                <Select name="plan_id" defaultValue={d.tenant.plan_id} className="w-auto">{d.plans.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
              </ActionForm>
            ) : <Notice tone="info">Plan changes are handled by the billing provider.</Notice>}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Members and roles" description="Admin: everything. Analyst: edit incidents, sources, reports. Reviewer: approve incidents, alerts and publication. Client: read published output." />
          <CardBody className="grid gap-3">
            <Table>
              <thead><tr><Th>User</Th><Th>Role</Th><Th>Last sign-in</Th></tr></thead>
              <tbody>
                {d.members.map((m) => (
                  <tr key={m.user_id}>
                    <Td className="text-xs">{m.email}{m.display_name ? <div className="text-muted-foreground">{m.display_name}</div> : null}{m.disabled ? <Badge>Disabled</Badge> : null}</Td>
                    <Td>
                      <ActionForm action={memberRoleAction} submit="Apply" variant="outline" className="flex items-center gap-1.5">
                        <input type="hidden" name="user_id" value={m.user_id} />
                        <Select name="role" defaultValue={m.role} className="h-8 w-28 text-xs">
                          {["admin", "analyst", "reviewer", "client"].map((r) => <option key={r} value={r}>{r}</option>)}<option value="remove">remove</option>
                        </Select>
                      </ActionForm>
                    </Td>
                    <Td className="text-xs text-muted-foreground">{fmtAgo(m.last_login_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <ActionForm action={addMemberAction} submit="Add member">
              <div className="grid grid-cols-2 gap-2">
                <Label>Email<Input name="email" type="email" required /></Label>
                <Label>Name<Input name="display_name" /></Label>
                <Label>Role<Select name="role" defaultValue="analyst">{["admin", "analyst", "reviewer", "client"].map((r) => <option key={r}>{r}</option>)}</Select></Label>
                <Label>Temporary password<Input name="password" type="password" minLength={12} required autoComplete="new-password" /></Label>
              </div>
            </ActionForm>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Review and verification policy" description="Controls which incidents and alerts need a human decision. Lowering these thresholds is recorded in the audit log." />
          <CardBody>
            <ActionForm action={savePoliciesAction} submit="Save policies">
              <div className="grid grid-cols-2 gap-2">
                <Label>Time zone<Input name="timezone" defaultValue={d.tenant.settings.timezone ?? "Asia/Riyadh"} /></Label>
                <Label>Daily brief hour (local)<Input name="daily_brief_hour" type="number" min={0} max={23} defaultValue={d.tenant.settings.reports?.daily_brief_hour ?? 6} /></Label>
                <Label hint="Any incident at or above this severity needs review">Review incidents from severity<Input name="incident_min_severity" type="number" min={1} max={5} defaultValue={Number(rp.incident_min_severity ?? 4)} /></Label>
                <Label hint="Single-source, unverified or disputed incidents">…weakly sourced from severity<Input name="incident_unverified_min_severity" type="number" min={1} max={5} defaultValue={Number(rp.incident_unverified_min_severity ?? 3)} /></Label>
                <Label>High-impact alerts from severity<Input name="alert_high_impact_min_severity" type="number" min={1} max={5} defaultValue={Number(rp.alert_high_impact_min_severity ?? 4)} /></Label>
                <Label>Independent origins to corroborate<Input name="corroboration_min_independent" type="number" min={2} max={5} defaultValue={Number(vp.corroboration_min_independent ?? 2)} /></Label>
                <Label>LLM monthly budget (USD)<Input name="monthly_budget_usd" type="number" min={0} step="1" defaultValue={Number(d.tenant.settings.llm?.monthly_budget_usd ?? 25)} /></Label>
              </div>
              <label className="flex items-center gap-2 text-xs"><input type="checkbox" name="high_impact_requires_review" defaultChecked={rp.high_impact_requires_review !== false} /> High-impact alerts require human review before sending (recommended)</label>
              <label className="flex items-center gap-2 text-xs"><input type="checkbox" name="auto_send_standard_alerts" defaultChecked={rp.auto_send_standard_alerts !== false} /> Send standard-impact alerts automatically</label>
            </ActionForm>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Event taxonomy" description="Platform defaults with your organisation's overrides. An incident can carry several categories. Changes apply to newly processed articles." />
          <CardBody className="grid gap-3">
            <div className="max-h-72 overflow-y-auto">
              <Table>
                <thead><tr><Th>Key</Th><Th>Label</Th><Th>Sev.</Th><Th>Review</Th><Th>Scope</Th></tr></thead>
                <tbody>
                  {d.types.map((x) => (
                    <tr key={x.key} className={x.enabled ? "" : "opacity-50"}>
                      <Td className="font-mono text-[11px]">{x.key}</Td>
                      <Td className="text-xs">{x.label}{x.label_ar ? <span className="ms-1 text-muted-foreground">· {x.label_ar}</span> : null}</Td>
                      <Td className="text-xs">{x.default_severity}</Td>
                      <Td className="text-xs">{x.review_required ? "yes" : "—"}</Td>
                      <Td className="text-xs">{x.tenant_id ? "custom" : "platform"}{x.enabled ? "" : " (off)"}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
            <ActionForm action={saveEventTypeAction} submit="Save category">
              <div className="grid grid-cols-2 gap-2">
                <Label hint="Existing key = override">Key<Input name="key" required pattern="[a-z][a-z_]{1,39}" list="type-keys" /></Label>
                <Label>Label<Input name="label" required /></Label>
                <Label>Arabic label<Input name="label_ar" dir="rtl" /></Label>
                <Label>Default severity<Select name="default_severity" defaultValue="2">{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</Select></Label>
              </div>
              <datalist id="type-keys">{d.types.map((x) => <option key={x.key} value={x.key} />)}</datalist>
              <Label hint="Comma-separated">English keywords<Input name="keywords_en" /></Label>
              <Label hint="Comma-separated">Arabic keywords<Input name="keywords_ar" dir="rtl" /></Label>
              <div className="flex gap-4 text-xs">
                <label className="flex items-center gap-2"><input type="checkbox" name="review_required" /> Always needs review</label>
                <label className="flex items-center gap-2"><input type="checkbox" name="enabled" defaultChecked /> Enabled</label>
              </div>
            </ActionForm>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="AI agents: usage and cost (30 days)" description="Every model call is metered per agent, with retries, call caps and a daily budget enforced before sending." />
          <CardBody>
            {d.llm.length === 0 ? <p className="text-xs text-muted-foreground">No model calls recorded{env.anthropicApiKey ? "" : " (no LLM key configured: agents run in rules-only mode)"}.</p> : (
              <Table>
                <thead><tr><Th>Agent</Th><Th>Model</Th><Th>Calls</Th><Th>Failed</Th><Th>Tokens in/out</Th><Th>Cost</Th><Th>Avg ms</Th></tr></thead>
                <tbody>{d.llm.map((x) => (
                  <tr key={`${x.agent}-${x.model}`}><Td className="text-xs">{x.agent}</Td><Td className="text-xs">{x.model}</Td><Td>{x.calls}</Td><Td>{x.failures}</Td>
                    <Td className="text-xs">{fmtNumber(x.input_tokens)} / {fmtNumber(x.output_tokens)}</Td><Td>{fmtUsd(x.cost)}</Td><Td>{x.avg_ms}</Td></tr>
                ))}</tbody>
              </Table>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Recent jobs" description="Ingestion, reports, exports, risk, audits and notification tests run on the worker queue with retries and backoff." />
          <CardBody className="max-h-96 overflow-y-auto">
            <Table>
              <thead><tr><Th>#</Th><Th>Kind</Th><Th>Status</Th><Th>Result</Th><Th>When</Th></tr></thead>
              <tbody>{d.jobs.map((j) => (
                <tr key={j.id}>
                  <Td className="text-xs">{j.id}</Td><Td className="text-xs">{j.kind}{j.email ? <div className="text-muted-foreground">{j.email}</div> : null}</Td>
                  <Td><StatusBadge status={j.status} />{j.attempts > 1 ? <div className="text-[10px] text-muted-foreground">{j.attempts} attempts</div> : null}</Td>
                  <Td className="max-w-xs text-[11px] break-words">{j.last_error ? <span className="text-red-600">{j.last_error.slice(0, 200)}</span> : j.result ? JSON.stringify(j.result).slice(0, 200) : ""}</Td>
                  <Td className="text-xs whitespace-nowrap text-muted-foreground">{fmtDateTime(j.created_at, tz, s.locale)}</Td>
                </tr>
              ))}</tbody>
            </Table>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Audit log" description="Append-only record of sign-ins, decisions, edits, exports and configuration changes." />
        <CardBody className="max-h-[480px] overflow-y-auto">
          <Table>
            <thead><tr><Th>When</Th><Th>Actor</Th><Th>Action</Th><Th>Object</Th><Th>Details</Th></tr></thead>
            <tbody>{d.auditRows.map((a) => (
              <tr key={a.id}>
                <Td className="text-xs whitespace-nowrap">{fmtDateTime(a.created_at, tz, s.locale)}</Td>
                <Td className="text-xs">{a.email ?? a.actor_agent ?? "system"}{a.ip ? <div className="text-muted-foreground">{a.ip}</div> : null}</Td>
                <Td className="font-mono text-[11px]">{a.action}</Td>
                <Td className="text-xs">{a.entity_type ?? ""} {a.entity_id ? <span className="font-mono text-[10px]">{a.entity_id.slice(0, 12)}</span> : null}</Td>
                <Td className="max-w-md text-[11px] break-words text-muted-foreground">{a.details ? JSON.stringify(a.details).slice(0, 240) : ""}</Td>
              </tr>
            ))}</tbody>
          </Table>
        </CardBody>
      </Card>
    </>
  );
}
