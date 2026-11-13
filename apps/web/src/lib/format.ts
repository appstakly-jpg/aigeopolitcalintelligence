import type { Locale } from "./i18n";

function asDate(d: Date | string): Date {
  return typeof d === "string" ? new Date(d) : d;
}

export function fmtDateTime(d: Date | string | null | undefined, tz: string, locale: Locale = "en"): string {
  if (!d) return "—";
  const date = asDate(d);
  if (Number.isNaN(date.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat(locale === "ar" ? "ar-SA-u-nu-latn-ca-gregory" : "en-GB", {
      day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: tz, timeZoneName: "short",
    }).format(date);
  } catch {
    return date.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }
}

export function fmtDate(d: Date | string | null | undefined, tz: string, locale: Locale = "en"): string {
  if (!d) return "—";
  const date = asDate(d);
  if (Number.isNaN(date.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat(locale === "ar" ? "ar-SA-u-nu-latn-ca-gregory" : "en-GB", {
      day: "2-digit", month: "short", year: "numeric", timeZone: tz,
    }).format(date);
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

export function fmtAgo(d: Date | string | null | undefined, now = Date.now()): string {
  if (!d) return "never";
  const s = Math.round((now - asDate(d).getTime()) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function fmtNumber(n: number | string | null | undefined, digits = 0): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = typeof n === "string" ? Number(n) : n;
  return Number.isFinite(v) ? v.toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "—";
}

export function fmtUsd(n: number | string | null | undefined): string {
  const v = Number(n ?? 0);
  return `$${v.toFixed(v > 0 && v < 1 ? 4 : 2)}`;
}

export function humanize(key: string | null | undefined): string {
  if (!key) return "—";
  return key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Never renders an unstated casualty figure as zero. */
export function casualtyText(killed: number | null | undefined, injured: number | null | undefined): string {
  const parts: string[] = [];
  if (killed !== null && killed !== undefined) parts.push(`${killed} killed`);
  if (injured !== null && injured !== undefined) parts.push(`${injured} injured`);
  return parts.length ? parts.join(", ") : "No casualty figures reported";
}
