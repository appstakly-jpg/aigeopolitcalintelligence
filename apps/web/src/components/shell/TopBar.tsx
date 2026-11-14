"use client";

import { Languages, LogOut, Monitor, Moon, Search, Sun } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { setLocaleAction, setThemeAction, signOutAction, switchTenantAction } from "@/app/actions";
import { buttonClass, Input, Select } from "@/components/ui/primitives";

interface Props {
  locale: "en" | "ar";
  labels: { search: string; range: string; signout: string; language: string; theme: string };
  user: { email: string; role: string };
  tenants: { id: string; name: string }[];
  activeTenantId: string;
}

const PRESETS = [["24h", "24 h"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"], ["custom", "Custom…"]] as const;

export function TopBar({ locale, labels, user, tenants, activeTenantId }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const current = sp.get("from") && sp.get("to") ? "custom" : sp.get("range") ?? "7d";
  const [showCustom, setShowCustom] = useState(current === "custom");

  function applyRange(next: Record<string, string | null>) {
    const u = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(next)) {
      if (v === null) u.delete(k);
      else u.set(k, v);
    }
    u.delete("page");
    router.push(`${pathname}${u.toString() ? `?${u}` : ""}`);
  }

  return (
    <header className="sticky top-0 z-20 flex flex-wrap items-center gap-2 border-b bg-background/95 px-4 py-2 backdrop-blur">
      <form action="/search" className="relative min-w-48 flex-1" role="search">
        <Search className="pointer-events-none absolute start-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
        <Input name="q" placeholder={labels.search} aria-label={labels.search} className="ps-8" defaultValue={pathname === "/search" ? sp.get("q") ?? "" : ""} />
      </form>
      <div className="flex items-center gap-1.5">
        <Select aria-label={labels.range} className="w-auto" value={current}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "custom") setShowCustom(true);
            else {
              setShowCustom(false);
              applyRange({ range: v === "7d" ? null : v, from: null, to: null });
            }
          }}>
          {PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        {showCustom ? (
          <form className="flex items-center gap-1" onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            applyRange({ range: null, from: String(f.get("from") || ""), to: String(f.get("to") || "") });
          }}>
            <Input type="date" name="from" defaultValue={sp.get("from") ?? ""} className="h-9 w-36" required aria-label="From" />
            <Input type="date" name="to" defaultValue={sp.get("to") ?? ""} className="h-9 w-36" required aria-label="To" />
            <button className={buttonClass("outline", "sm")} type="submit">Apply</button>
          </form>
        ) : null}
      </div>
      <form action={setLocaleAction}>
        <input type="hidden" name="locale" value={locale === "ar" ? "en" : "ar"} />
        <button className={buttonClass("ghost", "sm")} type="submit" title="Switch language"><Languages className="size-4" />{labels.language}</button>
      </form>
      <form action={setThemeAction} className="flex items-center rounded-md border" aria-label={labels.theme}>
        {([["light", Sun], ["dark", Moon], ["system", Monitor]] as const).map(([v, Icon]) => (
          <button key={v} name="theme" value={v} type="submit" title={`${v} theme`} className={buttonClass("ghost", "sm", "h-8 px-2")}
            onClick={() => {
              const dark = v === "dark" || (v === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
              document.documentElement.classList.toggle("dark", dark);
            }}>
            <Icon className="size-4" aria-hidden />
          </button>
        ))}
      </form>
      {tenants.length > 1 ? (
        <form action={switchTenantAction}>
          <Select name="tenant_id" defaultValue={activeTenantId} className="w-auto" aria-label="Organisation"
            onChange={(e) => e.currentTarget.form?.requestSubmit()}>
            {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </Select>
        </form>
      ) : null}
      <div className="hidden text-end text-[11px] leading-tight text-muted-foreground lg:block">
        <div className="max-w-44 truncate">{user.email}</div>
        <div className="capitalize">{user.role}</div>
      </div>
      <form action={signOutAction}>
        <button className={buttonClass("ghost", "sm")} type="submit" title={labels.signout}><LogOut className="size-4" /></button>
      </form>
    </header>
  );
}
