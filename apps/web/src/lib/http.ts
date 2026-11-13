import { getEnv } from "./env";

/**
 * Same-origin check for state-changing API routes (defence in depth: session cookies are also
 * SameSite=Lax). Compares the browser's Origin with the host the request was sent to, as Next.js
 * does for server actions, so it holds behind port mappings and TLS-terminating reverse proxies.
 */
export function isSameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return true; // not a browser cross-site request
  let host: string;
  try {
    host = new URL(origin).host;
  } catch {
    return false;
  }
  const allowed = [
    req.headers.get("x-forwarded-host")?.split(",")[0]?.trim(),
    req.headers.get("host"),
    new URL(getEnv().appBaseUrl).host,
  ];
  return allowed.some((h) => h && h.toLowerCase() === host.toLowerCase());
}

/** Absolute link to a page of this app, using the configured public URL (never an internal address). */
export function appUrl(path: string): string {
  return `${getEnv().appBaseUrl.replace(/\/$/, "")}${path}`;
}
