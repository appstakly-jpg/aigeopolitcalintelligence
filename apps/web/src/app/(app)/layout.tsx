import type { ReactNode } from "react";
import { Suspense } from "react";
import { Sidebar, type NavItem } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { t } from "@/lib/i18n";

export default async function AppLayout({ children }: { children: ReactNode }) {
  const s = await requireSession();
  const L = s.locale;
  const openReviews = can.staff(s.role)
    ? await withRls(s, async (tx) => {
      const [r] = await tx<{ n: number }[]>`select count(*)::int as n from analyst_reviews where status = 'open'`;
      return r.n;
    })
    : 0;
  const items: NavItem[] = [
    { key: "overview", href: "/", label: t(L, "nav.overview") },
    { key: "feed", href: "/feed", label: t(L, "nav.feed") },
    { key: "countries", href: "/countries", label: t(L, "nav.countries") },
    { key: "map", href: "/map", label: t(L, "nav.map") },
    { key: "incidents", href: "/incidents", label: t(L, "nav.incidents") },
    { key: "risk", href: "/risk", label: t(L, "nav.risk") },
    { key: "maritime", href: "/maritime", label: t(L, "nav.maritime") },
    { key: "reports", href: "/reports", label: t(L, "nav.reports") },
    { key: "alerts", href: "/alerts", label: t(L, "nav.alerts") },
    { key: "sources", href: "/sources", label: t(L, "nav.sources") },
    { key: "analyst", href: "/analyst", label: t(L, "nav.analyst") },
    ...(can.staff(s.role) ? [{ key: "reviews" as const, href: "/reviews", label: t(L, "nav.reviews"), badge: openReviews }] : []),
    ...(can.admin(s.role) ? [{ key: "admin" as const, href: "/admin", label: t(L, "nav.admin") }] : []),
    { key: "settings", href: "/settings", label: t(L, "nav.settings") },
  ];
  const demo = s.tenantIsDemo || getEnv().demoMode;
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 overflow-y-auto border-e bg-card md:block">
        <Suspense>
          <Sidebar items={items} tenantName={s.tenantName} />
        </Suspense>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {demo ? (
          <div role="note" className="bg-violet-700 px-4 py-1.5 text-center text-xs font-medium text-white">{t(L, "demo.banner")}</div>
        ) : null}
        <Suspense>
          <TopBar locale={L}
            labels={{ search: t(L, "top.search"), range: t(L, "top.range"), signout: t(L, "top.signout"), language: t(L, "top.language"), theme: t(L, "top.theme") }}
            user={{ email: s.email, role: s.role }} activeTenantId={s.tenantId}
            tenants={s.memberships.map((m) => ({ id: m.tenantId, name: m.tenantName }))} />
        </Suspense>
        <nav className="flex gap-1 overflow-x-auto border-b px-3 py-1.5 text-xs md:hidden" aria-label="Sections">
          {items.map((i) => <a key={i.key} href={i.href} className="rounded px-2 py-1 whitespace-nowrap hover:bg-muted">{i.label}</a>)}
        </nav>
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4 md:p-6">{children}</main>
        <footer className="border-t px-6 py-3 text-[11px] text-muted-foreground">
          Unclassified public-source intelligence. Reporting is attributed, not endorsed; official statements are claims. Coverage depends on
          the enabled sources and is not complete.
        </footer>
      </div>
    </div>
  );
}
