import { NextResponse, type NextRequest } from "next/server";

// Optimistic gate only: redirects requests without a session cookie. The session itself is verified
// on the server for every page, action and API route (src/lib/auth.ts), and Postgres RLS enforces
// tenant isolation underneath that.
const PUBLIC = [/^\/login(\/|$)/, /^\/api\/health$/];

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC.some((re) => re.test(pathname))) return NextResponse.next();
  const hasSession = req.cookies.has("gi_session") || req.cookies.has("sb-access-token");
  if (hasSession) return NextResponse.next();
  if (pathname.startsWith("/api/")) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|basemap/|maplibre/|robots.txt).*)"],
};
