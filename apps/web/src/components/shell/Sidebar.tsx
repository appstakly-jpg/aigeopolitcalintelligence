"use client";

import {
  Bell, Bot, ClipboardCheck, Database, FileText, Gauge, Globe, LayoutDashboard, Map, Rss, Settings, Shield, Ship, TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";

const ICONS = {
  overview: LayoutDashboard, feed: Rss, countries: Globe, map: Map, incidents: TriangleAlert, risk: Gauge, maritime: Ship,
  reports: FileText, alerts: Bell, sources: Database, analyst: Bot, admin: Shield, settings: Settings, reviews: ClipboardCheck,
} as const;

export interface NavItem { key: keyof typeof ICONS; href: string; label: string; badge?: number }

export function Sidebar({ items, tenantName }: { items: NavItem[]; tenantName: string }) {
  const pathname = usePathname();
  const sp = useSearchParams();
  // Keep the selected date range while moving between sections.
  const carry = new URLSearchParams();
  for (const k of ["range", "from", "to"]) {
    const v = sp.get(k);
    if (v) carry.set(k, v);
  }
  const suffix = carry.toString() ? `?${carry}` : "";
  return (
    <nav className="flex h-full flex-col gap-0.5 p-3 text-sm" aria-label="Main">
      <div className="mb-3 px-2">
        <div className="text-[11px] font-semibold tracking-widest text-primary uppercase">GCC Security Intel</div>
        <div className="truncate text-xs text-muted-foreground" title={tenantName}>{tenantName}</div>
      </div>
      {items.map((it) => {
        const Icon = ICONS[it.key];
        const active = it.href === "/" ? pathname === "/" : pathname === it.href || pathname.startsWith(`${it.href}/`);
        return (
          <Link key={it.key} href={`${it.href}${suffix}`} aria-current={active ? "page" : undefined}
            className={cn("flex items-center gap-2.5 rounded-md px-2 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
              active && "bg-accent font-medium text-accent-foreground")}>
            <Icon className="size-4 shrink-0" aria-hidden />
            <span className="truncate">{it.label}</span>
            {it.badge ? (
              <span className="ms-auto rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold text-white tabular-nums">{it.badge}</span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}
