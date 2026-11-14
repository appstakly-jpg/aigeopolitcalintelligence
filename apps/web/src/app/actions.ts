"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { z } from "zod";
import { actionSession, clearSession, completeMfa, getSession, passwordLogin, switchTenant, auditEvent } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { getEnv } from "@/lib/env";

export type FormState = { error?: string; ok?: string } | undefined;

const cookieOpts = () => ({ httpOnly: false, sameSite: "lax" as const, secure: getEnv().secureCookies, path: "/", maxAge: 365 * 86400 });

export async function loginAction(_prev: FormState, form: FormData): Promise<FormState> {
  const parsed = z.object({ email: z.string().email().max(254), password: z.string().min(1).max(200) })
    .safeParse({ email: form.get("email"), password: form.get("password") });
  if (!parsed.success) return { error: "Enter a valid email and password." };
  const r = await passwordLogin(parsed.data.email, parsed.data.password);
  if (!r.ok) return { error: r.error };
  redirect(r.mfa ? "/login?step=mfa" : "/");
}

export async function mfaAction(_prev: FormState, form: FormData): Promise<FormState> {
  const code = String(form.get("code") ?? "").replace(/\s+/g, "");
  if (!/^\d{6}$/.test(code)) return { error: "Enter the 6-digit code from your authenticator app." };
  const r = await completeMfa(code);
  if (!r.ok) return { error: r.error };
  redirect("/");
}

export async function signOutAction(): Promise<void> {
  const s = await getSession();
  if (s) await auditEvent({ tenantId: s.tenantId, userId: s.userId, action: "auth.logout" });
  await clearSession();
  redirect("/login");
}

export async function setThemeAction(form: FormData): Promise<void> {
  const theme = z.enum(["light", "dark", "system"]).catch("system").parse(form.get("theme"));
  (await cookies()).set("gi_theme", theme, cookieOpts());
}

export async function setLocaleAction(form: FormData): Promise<void> {
  const locale = z.enum(["en", "ar"]).catch("en").parse(form.get("locale"));
  (await cookies()).set("gi_locale", locale, cookieOpts());
  const s = await getSession();
  if (s) await withRls(s, (tx) => tx`update app_users set locale = ${locale} where id = ${s.userId}`);
}

export async function switchTenantAction(form: FormData): Promise<void> {
  const tenantId = z.string().uuid().parse(form.get("tenant_id"));
  await switchTenant(tenantId);
  const s = await actionSession();
  await auditEvent({ tenantId, userId: s.userId, action: "auth.switch_tenant" });
  redirect("/");
}
