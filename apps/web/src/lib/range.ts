// Date-range handling shared by every page: ?range=24h|7d|30d|90d or ?from=YYYY-MM-DD&to=YYYY-MM-DD.
export const RANGE_PRESETS = { "24h": 1, "7d": 7, "30d": 30, "90d": 90 } as const;
export type RangePreset = keyof typeof RANGE_PRESETS;

export interface DateRange {
  key: RangePreset | "custom";
  from: Date;
  to: Date;
  days: number;
}

const DAY = 86_400_000;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function parseRange(sp: Record<string, string | string[] | undefined>, now = new Date()): DateRange {
  const from = typeof sp.from === "string" && ISO_DAY.test(sp.from) ? new Date(`${sp.from}T00:00:00Z`) : null;
  const to = typeof sp.to === "string" && ISO_DAY.test(sp.to) ? new Date(`${sp.to}T23:59:59Z`) : null;
  if (from && to && !Number.isNaN(from.getTime()) && !Number.isNaN(to.getTime()) && from < to) {
    const days = Math.max(1, Math.round((to.getTime() - from.getTime()) / DAY));
    if (days <= 366) return { key: "custom", from, to, days };
  }
  const key = (typeof sp.range === "string" && sp.range in RANGE_PRESETS ? sp.range : "7d") as RangePreset;
  return { key, from: new Date(now.getTime() - RANGE_PRESETS[key] * DAY), to: now, days: RANGE_PRESETS[key] };
}

/** Query-string entries that preserve the active range when building links. */
export function rangeParams(r: DateRange): Record<string, string> {
  if (r.key === "custom") return { from: r.from.toISOString().slice(0, 10), to: r.to.toISOString().slice(0, 10) };
  return r.key === "7d" ? {} : { range: r.key };
}
