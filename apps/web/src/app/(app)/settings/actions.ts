"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { actionSession, rateLimit } from "@/lib/auth";
import { decryptSecret, encryptSecret, hashPassword, verifyPassword } from "@/lib/crypto";
import { sql, withRls } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { audit } from "@/lib/jobs";
import { generateSecret, verifyTotp } from "@/lib/totp";

type State = { error?: string; ok?: string } | undefined;
const msg = (e: unknown) => (e instanceof z.ZodError ? e.issues.map((i) => i.message).join(" ") : e instanceof Error ? e.message : String(e));
const SETUP_COOKIE = "gi_mfa_setup";

export async function saveProfileAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession();
    const d = z.object({
      display_name: z.string().trim().max(120),
      locale: z.enum(["en", "ar"]),
      timezone: z.string().refine((tz) => { try { new Intl.DateTimeFormat("en", { timeZone: tz }); return true; } catch { return false; } }, "Unknown time zone"),
    }).parse({ display_name: form.get("display_name") ?? "", locale: form.get("locale"), timezone: form.get("timezone") });
    await withRls(s, async (tx) => {
      await tx`update app_users set display_name = ${d.display_name || null}, locale = ${d.locale}, timezone = ${d.timezone} where id = ${s.userId}`;
      await audit(tx, s, "user.profile", "user", s.userId, d);
    });
    (await cookies()).set("gi_locale", d.locale, { sameSite: "lax", path: "/", maxAge: 365 * 86400, secure: getEnv().secureCookies });
    revalidatePath("/", "layout");
    return { ok: "Profile saved." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function changePasswordAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession();
    if (!(await rateLimit(`pwchange:${s.userId}`, 900, 5))) throw new Error("Too many attempts. Try again later.");
    const d = z.object({ current: z.string().min(1), next: z.string().min(12, "New password must be at least 12 characters").max(200), confirm: z.string() })
      .refine((v) => v.next === v.confirm, "The new passwords do not match.")
      .parse({ current: form.get("current"), next: form.get("next"), confirm: form.get("confirm") });
    const [u] = await sql()<{ password_hash: string | null }[]>`select password_hash from app_users where id = ${s.userId} and auth_provider = 'local'`;
    if (!u || !(await verifyPassword(d.current, u.password_hash))) throw new Error("Current password is incorrect.");
    await sql()`update app_users set password_hash = ${await hashPassword(d.next)} where id = ${s.userId}`;
    await withRls(s, (tx) => audit(tx, s, "user.password_changed", "user", s.userId));
    return { ok: "Password changed." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function startMfaAction(): Promise<State> {
  try {
    const s = await actionSession();
    const env = getEnv();
    if (!env.encryptionKey) throw new Error("MFA needs APP_ENCRYPTION_KEY to be configured on the server.");
    const secret = generateSecret();
    (await cookies()).set(SETUP_COOKIE, encryptSecret(secret, `user:${s.userId}:mfa-setup`, env.encryptionKey),
      { httpOnly: true, sameSite: "strict", path: "/settings", maxAge: 600, secure: env.secureCookies });
    revalidatePath("/settings");
    return { ok: "Scan or type the key below into your authenticator app, then enter a code to finish." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function confirmMfaAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession();
    const env = getEnv();
    const jar = await cookies();
    const pending = jar.get(SETUP_COOKIE)?.value;
    if (!pending) throw new Error("Setup expired. Start again.");
    const secret = decryptSecret(pending, `user:${s.userId}:mfa-setup`, env.encryptionKey);
    if (!verifyTotp(secret, String(form.get("code") ?? "").replace(/\s+/g, ""))) throw new Error("That code is not valid. Check the time on your device and try again.");
    await sql()`update app_users set mfa_totp_secret = ${encryptSecret(secret, `user:${s.userId}:mfa`, env.encryptionKey)} where id = ${s.userId}`;
    jar.delete({ name: SETUP_COOKIE, path: "/settings" });
    await withRls(s, (tx) => audit(tx, s, "user.mfa_enabled", "user", s.userId));
    revalidatePath("/settings");
    return { ok: "Two-factor authentication is on. You'll be asked for a code at each sign-in." };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function disableMfaAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession();
    if (!(await rateLimit(`mfa-disable:${s.userId}`, 900, 5))) throw new Error("Too many attempts. Try again later.");
    const env = getEnv();
    const [u] = await sql()<{ mfa_totp_secret: string | null }[]>`select mfa_totp_secret from app_users where id = ${s.userId}`;
    if (!u?.mfa_totp_secret) throw new Error("MFA is not enabled.");
    const secret = decryptSecret(u.mfa_totp_secret, `user:${s.userId}:mfa`, env.encryptionKey);
    if (!verifyTotp(secret, String(form.get("code") ?? "").replace(/\s+/g, ""))) throw new Error("That code is not valid.");
    await sql()`update app_users set mfa_totp_secret = null where id = ${s.userId}`;
    await withRls(s, (tx) => audit(tx, s, "user.mfa_disabled", "user", s.userId));
    revalidatePath("/settings");
    return { ok: "Two-factor authentication is off." };
  } catch (e) {
    return { error: msg(e) };
  }
}

const SECRET_NAMES = ["slack_webhook_url", "telegram_bot_token", "webhook_signing_secret"] as const;

export async function saveSecretAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession(["admin"]);
    const env = getEnv();
    if (!env.encryptionKey) throw new Error("APP_ENCRYPTION_KEY is not configured, so secrets cannot be stored.");
    const d = z.object({ name: z.enum(SECRET_NAMES), value: z.string().trim().max(500) }).parse({ name: form.get("name"), value: form.get("value") ?? "" });
    if (d.name === "slack_webhook_url" && d.value && !d.value.startsWith("https://hooks.slack.com/")) throw new Error("Slack webhook URLs start with https://hooks.slack.com/");
    if (d.name === "telegram_bot_token" && d.value && !/^\d{5,}:[A-Za-z0-9_-]{20,}$/.test(d.value)) throw new Error("That does not look like a Telegram bot token.");
    await withRls(s, async (tx) => {
      if (!d.value) await tx`delete from tenant_secrets where name = ${d.name}`;
      else {
        const ct = encryptSecret(d.value, `${s.tenantId}:${d.name}`, env.encryptionKey);
        await tx`insert into tenant_secrets(tenant_id, name, ciphertext, updated_by) values (${s.tenantId}, ${d.name}, ${ct}, ${s.userId})
                 on conflict (tenant_id, name) do update set ciphertext = excluded.ciphertext, updated_by = excluded.updated_by, updated_at = now()`;
      }
      await audit(tx, s, d.value ? "secret.set" : "secret.delete", "tenant_secret", d.name); // the value is never logged
    });
    revalidatePath("/settings");
    return { ok: d.value ? `${d.name} saved (encrypted).` : `${d.name} removed.` };
  } catch (e) {
    return { error: msg(e) };
  }
}

export async function saveWatchlistAction(_p: State, form: FormData): Promise<State> {
  try {
    const s = await actionSession();
    const words = (v: FormDataEntryValue | null) => String(v ?? "").split(",").map((w) => w.trim()).filter(Boolean).slice(0, 30);
    const d = z.object({
      id: z.string().uuid().optional(), name: z.string().trim().min(2).max(120),
      countries: z.array(z.string().regex(/^[A-Z]{2}$/)).max(12), keywords: z.array(z.string().max(60)), sectors: z.array(z.string().max(60)),
      delete: z.boolean(),
      exposure: z.record(z.string().regex(/^[A-Z]{2}$/), z.number().min(0).max(5)),
    }).parse({ id: form.get("id") || undefined, name: form.get("name"), countries: form.getAll("countries").map(String), keywords: words(form.get("keywords")),
      sectors: words(form.get("sectors")), delete: form.get("delete") === "on",
      // "SA=1.5, AE=1" -> {"SA": 1.5, "AE": 1}: exposure multipliers used by the risk methodology
      exposure: Object.fromEntries(words(form.get("exposure")).map((kv) => kv.split("=").map((x) => x.trim())).map(([k, v]) => [k.toUpperCase(), Number(v)])) });
    await withRls(s, async (tx) => {
      if (d.id && d.delete) await tx`delete from watchlists where id = ${d.id}`;
      else if (d.id) {
        await tx`update watchlists set name = ${d.name}, countries = ${d.countries}::text[], keywords = ${d.keywords}::text[], sectors = ${d.sectors}::text[],
                 exposure = ${tx.json(d.exposure)} where id = ${d.id}`;
      } else {
        // Organisation-wide watchlists (admins only) carry the exposure multipliers the risk methodology uses.
        const owner = form.get("org") === "on" && s.role === "admin" ? null : s.userId;
        await tx`insert into watchlists(tenant_id, user_id, name, countries, keywords, sectors, exposure)
                 values (${s.tenantId}, ${owner}, ${d.name}, ${d.countries}::text[], ${d.keywords}::text[], ${d.sectors}::text[], ${tx.json(d.exposure)})`;
      }
      await audit(tx, s, d.delete ? "watchlist.delete" : "watchlist.save", "watchlist", d.id ?? null, { name: d.name });
    });
    revalidatePath("/settings");
    return { ok: d.delete ? "Watchlist deleted." : "Watchlist saved." };
  } catch (e) {
    return { error: msg(e) };
  }
}
