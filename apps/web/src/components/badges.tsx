import { Badge } from "@/components/ui/primitives";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

const SEVERITY = ["", "Minimal", "Low", "Moderate", "High", "Critical"];
const sevTone = [
  "",
  "border-slate-300 text-slate-600 dark:border-slate-600 dark:text-slate-300",
  "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-200",
  "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200",
  "border-orange-400 bg-orange-50 text-orange-800 dark:border-orange-700 dark:bg-orange-950 dark:text-orange-200",
  "border-red-400 bg-red-50 text-red-800 dark:border-red-700 dark:bg-red-950 dark:text-red-200",
];

export function SeverityBadge({ value, title }: { value: number; title?: string }) {
  const v = Math.min(5, Math.max(1, value));
  return <Badge className={sevTone[v]} title={title ?? `Severity ${v}/5 (${SEVERITY[v]})`}>S{v} · {SEVERITY[v]}</Badge>;
}

const VER: Record<string, { label: string; cls: string; help: string }> = {
  confirmed: { label: "Confirmed", cls: "border-emerald-400 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-200",
    help: "Confirmed by an analyst against independent evidence." },
  corroborated: { label: "Corroborated", cls: "border-teal-300 bg-teal-50 text-teal-800 dark:border-teal-700 dark:bg-teal-950 dark:text-teal-200",
    help: "Reported by at least two independent origins (syndicated copies are not counted)." },
  single_source: { label: "Single source", cls: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200",
    help: "Only one independent origin has reported this." },
  disputed: { label: "Disputed", cls: "border-fuchsia-300 bg-fuchsia-50 text-fuchsia-800 dark:border-fuchsia-700 dark:bg-fuchsia-950 dark:text-fuchsia-200",
    help: "Contradictory reporting or an official denial exists; both remain visible." },
  unverified: { label: "Unverified", cls: "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300",
    help: "Low-reliability or social-media claim with no independent support yet." },
};

export function VerificationBadge({ status }: { status: string }) {
  const v = VER[status] ?? { label: humanize(status), cls: "", help: "" };
  return <Badge className={v.cls} title={v.help}>{v.label}</Badge>;
}

const REL: Record<string, string> = {
  A: "Completely reliable", B: "Usually reliable", C: "Fairly reliable", D: "Not usually reliable", E: "Unreliable", F: "Reliability cannot be judged",
};
export function ReliabilityBadge({ grade, credibility }: { grade: string | null | undefined; credibility?: number | null }) {
  if (!grade) return null;
  const tone = grade <= "B" ? "border-emerald-300 text-emerald-700 dark:border-emerald-700 dark:text-emerald-300"
    : grade === "C" ? "border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-300"
      : "border-red-300 text-red-700 dark:border-red-700 dark:text-red-300";
  return (
    <Badge className={tone} title={`Admiralty source reliability ${grade}: ${REL[grade] ?? ""}${credibility ? `; information credibility ${credibility}` : ""}`}>
      {grade}{credibility ? credibility : ""}
    </Badge>
  );
}

export function ReviewBadge({ status }: { status: string }) {
  if (status === "not_required") return null;
  const cls = status === "pending" || status === "changes_requested"
    ? "border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-300"
    : status === "approved" ? "border-emerald-300 text-emerald-700 dark:border-emerald-700 dark:text-emerald-300"
      : "border-red-300 text-red-700 dark:border-red-700 dark:text-red-300";
  return <Badge className={cls}>Review: {humanize(status)}</Badge>;
}

const BAND: Record<string, string> = {
  insufficient_data: "border-dashed border-slate-300 text-slate-500 dark:border-slate-600 dark:text-slate-400",
  low: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  moderate: "border-yellow-300 bg-yellow-50 text-yellow-800 dark:border-yellow-800 dark:bg-yellow-950 dark:text-yellow-200",
  elevated: "border-amber-400 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200",
  high: "border-orange-400 bg-orange-50 text-orange-800 dark:border-orange-700 dark:bg-orange-950 dark:text-orange-200",
  severe: "border-red-500 bg-red-50 text-red-800 dark:border-red-700 dark:bg-red-950 dark:text-red-200",
};
export function BandBadge({ band, className }: { band: string; className?: string }) {
  return <Badge className={cn(BAND[band] ?? "", className)}>{band === "insufficient_data" ? "Insufficient data" : humanize(band)}</Badge>;
}

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const good = ["sent", "succeeded", "approved", "published", "ok", "processed", "active"];
  const bad = ["failed", "dead", "rejected", "broken", "error", "suppressed"];
  const warn = ["pending_review", "pending", "queued", "retrying", "in_review", "draft", "proposed", "partially_sent", "skipped", "open"];
  const cls = good.includes(status) ? "border-emerald-300 text-emerald-700 dark:border-emerald-700 dark:text-emerald-300"
    : bad.includes(status) ? "border-red-300 text-red-700 dark:border-red-700 dark:text-red-300"
      : warn.includes(status) ? "border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-300" : "";
  return <Badge className={cn(cls, className)}>{humanize(status)}</Badge>;
}

export function DemoTag({ show }: { show: boolean }) {
  if (!show) return null;
  return <Badge className="border-violet-300 bg-violet-50 text-violet-800 dark:border-violet-700 dark:bg-violet-950 dark:text-violet-200"
    title="Synthetic demo record, not real reporting">SYNTHETIC</Badge>;
}

export function GeoPrecision({ precision, confidence }: { precision: string | null; confidence: number | null }) {
  if (!precision || precision === "unknown") return <Badge title="No location could be resolved from the reporting">Location unknown</Badge>;
  return (
    <Badge title="Geographic precision of the stored location and the confidence that it is right. Coordinates are gazetteer centroids, never invented points.">
      {humanize(precision)} · {confidence !== null ? `${Math.round(confidence * 100)}% geo-confidence` : "confidence n/a"}
    </Badge>
  );
}
