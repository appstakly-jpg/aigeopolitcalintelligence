import { jwtVerify, SignJWT } from "jose";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import { verifyPassword, decryptSecret } from "./crypto";
import { sql } from "./db";
import { getEnv } from "./env";
import { verifyTotp } from "./totp";

export type Role = "admin" | "analyst" | "reviewer" | "client";
export const SESSION_COOKIE = "gi_session";
const MFA_COOKIE = "gi_mfa";
const TENANT_COOKIE = "gi_tenant";
const SESSION_HOURS = 12;

export interface Membership {
  tenantId: string;
  tenantSlug: string;
  tenantName: string;
  role: Role;
}

export interface Session {
  userId: string;
  email: string;
  displayName: string;
  locale: "en" | "ar";
  timezone: string;
  mfaEnabled: boolean;
  tenantId: string;
  tenantSlug: string;
  tenantName: string;
  tenantIsDemo: boolean;
  tenantSettings: Record<string, unknown>;
  planId: string;
  role: Role;
  memberships: Membership[];
}

function key() {
  return new TextEncoder().encode(getEnv().sessionSecret);
}

export async function clientIp(): Promise<string> {
  const h = await headers();
  return (h.get("x-forwarded-for")?.split(",")[0] || h.get("x-real-ip") || "unknown").trim().slice(0, 64);
}

async function setCookie(name: string, value: string, maxAgeSeconds: number) {
  (await cookies()).set(name, value, {
    httpOnly: true, sameSite: "lax", secure: getEnv().secureCookies, path: "/", maxAge: maxAgeSeconds,
  });
}

export async function issueSession(userId: string, tenantId: string | null): Promise<void> {
  const token = await new SignJWT({ tid: tenantId ?? undefined, typ: "session" })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(userId)
    .setIssuedAt()
    .setExpirationTime(`${SESSION_HOURS}h`)
    .sign(key());
  await setCookie(SESSION_COOKIE, token, SESSION_HOURS * 3600);
}

export async function clearSession(): Promise<void> {
  const c = await cookies();
  c.delete(SESSION_COOKIE);
  c.delete(MFA_COOKIE);
}

/** Verified identity from the request: local session JWT, or a Supabase access token. */
async function identity(): Promise<{ sub: string; tid?: string } | null> {
  const env = getEnv();
  const c = await cookies();
  if (env.authProvider === "supabase") {
    const token = c.get("sb-access-token")?.value;
    if (!token || !env.supabaseJwtSecret) return null;
    try {
      const { payload } = await jwtVerify(token, new TextEncoder().encode(env.supabaseJwtSecret), { audience: "authenticated" });
      return payload.sub ? { sub: payload.sub, tid: c.get(TENANT_COOKIE)?.value } : null;
    } catch {
      return null;
    }
  }
  const token = c.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, key(), { algorithms: ["HS256"] });
    if (payload.typ !== "session" || !payload.sub) return null;
    return { sub: payload.sub, tid: (payload.tid as string | undefined) ?? undefined };
  } catch {
    return null;
  }
}

interface MembershipRow {
  id: string; email: string; display_name: string | null; locale: "en" | "ar"; timezone: string; mfa_enabled: boolean;
  tenant_id: string; role: Role; slug: string; name: string; is_demo: boolean; settings: Record<string, unknown>; plan_id: string;
}

export const getSession = cache(async (): Promise<Session | null> => {
  const who = await identity();
  if (!who) return null;
  // Privileged lookup (before a tenant is known); RLS applies to everything after this.
  const rows = await sql()<MembershipRow[]>`
    select u.id, u.email, u.display_name, u.locale, u.timezone, (u.mfa_totp_secret is not null) as mfa_enabled,
           m.tenant_id, m.role, t.slug, t.name, t.is_demo, t.settings, t.plan_id
    from app_users u
    join memberships m on m.user_id = u.id
    join tenants t on t.id = m.tenant_id
    where u.id = ${who.sub}::uuid and not u.disabled and t.status = 'active'
    order by t.name`;
  if (!rows.length) return null;
  const active = rows.find((r) => r.tenant_id === who.tid) ?? rows[0];
  return {
    userId: active.id, email: active.email, displayName: active.display_name || active.email,
    locale: active.locale, timezone: active.timezone, mfaEnabled: active.mfa_enabled,
    tenantId: active.tenant_id, tenantSlug: active.slug, tenantName: active.name, tenantIsDemo: active.is_demo,
    tenantSettings: active.settings || {}, planId: active.plan_id, role: active.role,
    memberships: rows.map((r) => ({ tenantId: r.tenant_id, tenantSlug: r.slug, tenantName: r.name, role: r.role })),
  };
});

export async function requireSession(roles?: Role[]): Promise<Session> {
  const s = await getSession();
  if (!s) redirect("/login");
  if (roles && !roles.includes(s.role)) redirect("/?denied=1");
  return s;
}

/** For server actions: returns the session or throws (never redirects silently). */
export async function actionSession(roles?: Role[]): Promise<Session> {
  const s = await getSession();
  if (!s) throw new Error("Not signed in");
  if (roles && !roles.includes(s.role)) throw new Error("Your role does not permit this action");
  return s;
}

export async function rateLimit(keyName: string, windowSeconds: number, max: number): Promise<boolean> {
  const [row] = await sql()<{ ok: boolean }[]>`select app.rate_limit_hit(${keyName}, ${windowSeconds}, ${max}) as ok`;
  return row.ok;
}

export async function auditEvent(e: {
  tenantId?: string | null; userId?: string | null; action: string; entityType?: string; entityId?: string;
  details?: Record<string, unknown>; ip?: string;
}): Promise<void> {
  await sql()`
    insert into audit_log(tenant_id, actor_user_id, action, entity_type, entity_id, details, ip)
    values (${e.tenantId ?? null}, ${e.userId ?? null}, ${e.action}, ${e.entityType ?? null}, ${e.entityId ?? null},
            ${sql().json((e.details ?? {}) as never)}, ${e.ip ?? null})`;
}

type LoginResult = { ok: true; mfa: boolean } | { ok: false; error: string };

export async function passwordLogin(emailRaw: string, password: string): Promise<LoginResult> {
  const email = emailRaw.trim().toLowerCase();
  const ip = await clientIp();
  const generic = "Invalid email or password.";
  if (!(await rateLimit(`login:ip:${ip}`, 900, 50)) || !(await rateLimit(`login:email:${email}`, 900, 10))) {
    return { ok: false, error: "Too many attempts. Try again in 15 minutes." };
  }
  const [u] = await sql()<{ id: string; password_hash: string | null; disabled: boolean; locked_until: Date | null;
    failed_logins: number; mfa_totp_secret: string | null }[]>`
    select id, password_hash, disabled, locked_until, failed_logins, mfa_totp_secret
    from app_users where email = ${email} and auth_provider = 'local'`;
  if (!u || u.disabled) {
    await verifyPassword(password, "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$" + "A".repeat(88)); // constant-ish time
    await auditEvent({ action: "auth.login_failed", details: { reason: "unknown_or_disabled" }, ip });
    return { ok: false, error: generic };
  }
  if (u.locked_until && u.locked_until > new Date()) {
    return { ok: false, error: "Account temporarily locked after repeated failures. Try again later." };
  }
  if (!(await verifyPassword(password, u.password_hash))) {
    await sql()`update app_users set failed_logins = failed_logins + 1,
                  locked_until = case when failed_logins + 1 >= 5 then now() + interval '15 minutes' else locked_until end
                where id = ${u.id}`;
    await auditEvent({ userId: u.id, action: "auth.login_failed", details: { reason: "bad_password" }, ip });
    return { ok: false, error: generic };
  }
  await sql()`update app_users set failed_logins = 0, locked_until = null, last_login_at = now() where id = ${u.id}`;
  if (u.mfa_totp_secret) {
    const token = await new SignJWT({ typ: "mfa" }).setProtectedHeader({ alg: "HS256" }).setSubject(u.id)
      .setIssuedAt().setExpirationTime("5m").sign(key());
    await setCookie(MFA_COOKIE, token, 300);
    return { ok: true, mfa: true };
  }
  await issueSession(u.id, null);
  await auditEvent({ userId: u.id, action: "auth.login", ip });
  return { ok: true, mfa: false };
}

export async function completeMfa(code: string): Promise<LoginResult> {
  const c = await cookies();
  const token = c.get(MFA_COOKIE)?.value;
  if (!token) return { ok: false, error: "Sign-in expired. Start again." };
  let sub: string;
  try {
    const { payload } = await jwtVerify(token, key(), { algorithms: ["HS256"] });
    if (payload.typ !== "mfa" || !payload.sub) throw new Error("bad token");
    sub = payload.sub;
  } catch {
    return { ok: false, error: "Sign-in expired. Start again." };
  }
  if (!(await rateLimit(`mfa:${sub}`, 900, 10))) return { ok: false, error: "Too many attempts. Try again later." };
  const [u] = await sql()<{ mfa_totp_secret: string | null }[]>`select mfa_totp_secret from app_users where id = ${sub}`;
  let ok = false;
  try {
    ok = !!u?.mfa_totp_secret && verifyTotp(decryptSecret(u.mfa_totp_secret, `user:${sub}:mfa`, getEnv().encryptionKey), code.trim());
  } catch {
    ok = false;
  }
  if (!ok) {
    await auditEvent({ userId: sub, action: "auth.mfa_failed", ip: await clientIp() });
    return { ok: false, error: "Invalid authentication code." };
  }
  c.delete(MFA_COOKIE);
  await issueSession(sub, null);
  await auditEvent({ userId: sub, action: "auth.login", details: { mfa: true }, ip: await clientIp() });
  return { ok: true, mfa: false };
}

export async function switchTenant(tenantId: string): Promise<void> {
  const s = await actionSession();
  if (!s.memberships.some((m) => m.tenantId === tenantId)) throw new Error("Not a member of that organisation");
  if (getEnv().authProvider === "supabase") await setCookie(TENANT_COOKIE, tenantId, SESSION_HOURS * 3600);
  else await issueSession(s.userId, tenantId);
}

export const can = {
  write: (r: Role) => r === "admin" || r === "analyst",
  review: (r: Role) => r === "admin" || r === "reviewer",
  staff: (r: Role) => r !== "client",
  admin: (r: Role) => r === "admin",
};
