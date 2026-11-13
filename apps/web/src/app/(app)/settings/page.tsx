import { cookies } from "next/headers";
import { ActionForm } from "@/components/ActionForm";
import { Badge, Card, CardBody, CardHeader, Input, Label, Notice, PageHeader, Select } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { decryptSecret } from "@/lib/crypto";
import { withRls } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { fmtDateTime } from "@/lib/format";
import { CONTEXT_COUNTRIES, countryName, GCC, t } from "@/lib/i18n";
import { otpauthUri } from "@/lib/totp";
import {
  changePasswordAction, confirmMfaAction, disableMfaAction, saveProfileAction, saveSecretAction, saveWatchlistAction, startMfaAction,
} from "./actions";

export const metadata = { title: "Settings" };

const SECRETS = [
  { name: "slack_webhook_url", label: "Slack incoming webhook URL", help: "Used by rules with the Slack channel." },
  { name: "telegram_bot_token", label: "Telegram bot token", help: "Or set TELEGRAM_BOT_TOKEN on the server." },
  { name: "webhook_signing_secret", label: "Webhook signing secret", help: "Signs outgoing webhook alerts (HMAC-SHA256)." },
];

export default async function SettingsPage() {
  const s = await requireSession();
  const env = getEnv();
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const d = await withRls(s, async (tx) => {
    const secrets = can.admin(s.role) ? await tx<{ name: string; updated_at: Date }[]>`select name, updated_at from tenant_secrets` : [];
    const watchlists = await tx<{ id: string; name: string; countries: string[]; keywords: string[]; sectors: string[]; exposure: Record<string, number>; user_id: string | null }[]>`
      select id, name, countries, keywords, sectors, exposure, user_id from watchlists order by created_at`;
    const [me] = await tx<{ display_name: string | null; auth_provider: string }[]>`select display_name, auth_provider from app_users where id = ${s.userId}`;
    return { secrets, watchlists, me };
  });
  let setupSecret: string | null = null;
  const pending = (await cookies()).get("gi_mfa_setup")?.value;
  if (pending && env.encryptionKey) {
    try { setupSecret = decryptSecret(pending, `user:${s.userId}:mfa-setup`, env.encryptionKey); } catch { setupSecret = null; }
  }
  return (
    <>
      <PageHeader title={t(s.locale, "nav.settings")} description="Your profile, sign-in security, watchlists and (for admins) notification credentials. Theme and language are also in the top bar." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Profile" description={s.email} />
          <CardBody>
            <ActionForm action={saveProfileAction} submit="Save profile">
              <Label>Display name<Input name="display_name" defaultValue={d.me?.display_name ?? ""} maxLength={120} /></Label>
              <div className="grid grid-cols-2 gap-2">
                <Label>Language<Select name="locale" defaultValue={s.locale}><option value="en">English</option><option value="ar">العربية</option></Select></Label>
                <Label hint="Times are shown in the organisation time zone where one is set">Time zone<Input name="timezone" defaultValue={s.timezone} /></Label>
              </div>
            </ActionForm>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Two-factor authentication" description="Time-based codes (RFC 6238) from any authenticator app." />
          <CardBody className="grid gap-3 text-sm">
            {!env.encryptionKey ? <Notice tone="warn">The server has no APP_ENCRYPTION_KEY, so MFA secrets cannot be stored. An operator must set it.</Notice>
              : s.mfaEnabled ? (
                <>
                  <Badge className="w-fit border-emerald-300 text-emerald-700">MFA is on</Badge>
                  <ActionForm action={disableMfaAction} submit="Turn off MFA" variant="outline">
                    <Label>Current code<Input name="code" inputMode="numeric" pattern="[0-9 ]{6,7}" required autoComplete="one-time-code" /></Label>
                  </ActionForm>
                </>
              ) : setupSecret ? (
                <>
                  <p>Add this key to your authenticator app (time-based, 6 digits, 30 seconds):</p>
                  <code className="rounded-md bg-muted p-2 font-mono text-sm break-all select-all">{setupSecret.match(/.{1,4}/g)?.join(" ")}</code>
                  <details className="text-xs"><summary className="cursor-pointer">Setup link (otpauth://)</summary>
                    <code className="break-all select-all">{otpauthUri(setupSecret, s.email)}</code></details>
                  <ActionForm action={confirmMfaAction} submit="Verify and turn on">
                    <Label>Code from the app<Input name="code" inputMode="numeric" pattern="[0-9 ]{6,7}" required autoComplete="one-time-code" /></Label>
                  </ActionForm>
                </>
              ) : (
                <ActionForm action={startMfaAction} submit="Set up MFA"><p className="text-xs text-muted-foreground">Recommended for every account; strongly recommended for admins and reviewers.</p></ActionForm>
              )}
          </CardBody>
        </Card>

        {d.me?.auth_provider === "local" ? (
          <Card>
            <CardHeader title="Password" />
            <CardBody>
              <ActionForm action={changePasswordAction} submit="Change password">
                <Label>Current password<Input name="current" type="password" required autoComplete="current-password" /></Label>
                <div className="grid grid-cols-2 gap-2">
                  <Label>New password<Input name="next" type="password" minLength={12} required autoComplete="new-password" /></Label>
                  <Label>Repeat<Input name="confirm" type="password" minLength={12} required autoComplete="new-password" /></Label>
                </div>
              </ActionForm>
            </CardBody>
          </Card>
        ) : null}

        <Card>
          <CardHeader title="Watchlists" description="Countries, keywords and sectors you care about. Organisation-wide watchlists also set the exposure multipliers used in risk scoring." />
          <CardBody className="grid gap-3">
            {d.watchlists.map((w) => (
              <details key={w.id} className="rounded-md border p-2 text-xs">
                <summary className="cursor-pointer"><span className="font-medium">{w.name}</span> {w.user_id === null ? <Badge>Organisation</Badge> : null}
                  <span className="text-muted-foreground"> · {w.countries.join(", ") || "all countries"}{Object.keys(w.exposure ?? {}).length ? ` · exposure ${Object.entries(w.exposure).map(([k, v]) => `${k}=${v}`).join(", ")}` : ""}</span></summary>
                <ActionForm action={saveWatchlistAction} submit="Save" className="mt-2 grid gap-2">
                  <input type="hidden" name="id" value={w.id} />
                  <Label>Name<Input name="name" defaultValue={w.name} required /></Label>
                  <select name="countries" multiple defaultValue={w.countries} className="h-20 rounded-md border border-input bg-card p-1 text-sm">
                    {[...GCC, ...CONTEXT_COUNTRIES].map((c) => <option key={c} value={c}>{countryName(c, s.locale)}</option>)}
                  </select>
                  <Label>Keywords<Input name="keywords" defaultValue={w.keywords.join(", ")} /></Label>
                  <Label>Sectors<Input name="sectors" defaultValue={w.sectors.join(", ")} /></Label>
                  <Label hint="e.g. SA=1.5, AE=1.2 (0–5; 1 = default)">Exposure<Input name="exposure" defaultValue={Object.entries(w.exposure ?? {}).map(([k, v]) => `${k}=${v}`).join(", ")} /></Label>
                  <label className="flex items-center gap-2"><input type="checkbox" name="delete" /> Delete this watchlist</label>
                </ActionForm>
              </details>
            ))}
            <ActionForm action={saveWatchlistAction} submit="Add watchlist">
              <Label>Name<Input name="name" required minLength={2} /></Label>
              <select name="countries" multiple className="h-20 rounded-md border border-input bg-card p-1 text-sm" aria-label="Countries">
                {[...GCC, ...CONTEXT_COUNTRIES].map((c) => <option key={c} value={c}>{countryName(c, s.locale)}</option>)}
              </select>
              <Label hint="Comma-separated">Keywords<Input name="keywords" /></Label>
              <Label hint="Comma-separated, e.g. energy, shipping">Sectors<Input name="sectors" /></Label>
              <Label hint="e.g. SA=1.5, AE=1.2">Exposure<Input name="exposure" /></Label>
              {can.admin(s.role) ? <label className="flex items-center gap-2 text-xs"><input type="checkbox" name="org" /> Organisation-wide (used for risk exposure)</label> : null}
            </ActionForm>
          </CardBody>
        </Card>

        {can.admin(s.role) ? (
          <Card>
            <CardHeader title="Notification credentials" description="Stored encrypted (AES-256-GCM) with the server's APP_ENCRYPTION_KEY. Values are never shown again or written to logs." />
            <CardBody className="grid gap-3">
              {!env.encryptionKey ? <Notice tone="warn">APP_ENCRYPTION_KEY is not set, so credentials cannot be stored.</Notice> : null}
              {SECRETS.map((x) => {
                const cur = d.secrets.find((y) => y.name === x.name);
                return (
                  <ActionForm key={x.name} action={saveSecretAction} submit={cur ? "Replace" : "Save"} variant="outline">
                    <input type="hidden" name="name" value={x.name} />
                    <Label hint={`${x.help} ${cur ? `Set ${fmtDateTime(cur.updated_at, tz, s.locale)}; submit empty to remove.` : "Not set."}`}>{x.label}
                      <Input name="value" type="password" autoComplete="off" placeholder={cur ? "•••••••• (stored)" : ""} />
                    </Label>
                  </ActionForm>
                );
              })}
            </CardBody>
          </Card>
        ) : null}
      </div>
    </>
  );
}
