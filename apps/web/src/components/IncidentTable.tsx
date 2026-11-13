import Link from "next/link";
import { DemoTag, ReviewBadge, SeverityBadge, VerificationBadge } from "@/components/badges";
import { Empty, Table, Td, Th } from "@/components/ui/primitives";
import { casualtyText, fmtDateTime } from "@/lib/format";
import { countryName, type Locale } from "@/lib/i18n";
import type { IncidentRow } from "@/lib/queries";

export function IncidentTable({ rows, labels, tz, locale, showReview = false }:
  { rows: IncidentRow[]; labels: Record<string, { label: string; label_ar: string | null }>; tz: string; locale: Locale; showReview?: boolean }) {
  if (!rows.length) return <Empty>No incidents match this filter and date range.</Empty>;
  return (
    <Table>
      <thead>
        <tr>
          <Th>Incident</Th>
          <Th>Location</Th>
          <Th>Categories</Th>
          <Th>Verification</Th>
          <Th>Severity</Th>
          <Th title="Independent origins / total reports">Sources</Th>
          <Th>Last reported</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className="hover:bg-muted/50">
            <Td className="max-w-md">
              <Link href={`/incidents/${r.id}`} className="font-medium hover:underline">{r.title}</Link>
              <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
                <span>{r.ref} · v{r.version}</span>
                <DemoTag show={r.is_demo} />
                <ReviewBadge status={r.review_status} audience={showReview ? "staff" : "client"} />
                {r.casualties_killed !== null || r.casualties_injured !== null ? <span>· {casualtyText(r.casualties_killed, r.casualties_injured)}</span> : null}
              </div>
            </Td>
            <Td className="text-xs">
              <div>{r.location_name ?? "Unresolved"}</div>
              <div className="text-muted-foreground">{countryName(r.country_code, locale)}</div>
            </Td>
            <Td className="text-xs">
              {r.categories.map((c) => (
                <div key={c} className={c === r.primary_category ? "font-medium" : "text-muted-foreground"}>
                  {(locale === "ar" ? labels[c]?.label_ar : null) ?? labels[c]?.label ?? c}
                </div>
              ))}
            </Td>
            <Td><VerificationBadge status={r.verification_status} /></Td>
            <Td><SeverityBadge value={r.severity} /></Td>
            <Td className="text-xs tabular-nums">
              <Link href={`/incidents/${r.id}#evidence`} className="hover:underline">{r.independent_source_count} / {r.source_count}</Link>
            </Td>
            <Td className="text-xs whitespace-nowrap text-muted-foreground">{fmtDateTime(r.last_reported_at, tz, locale)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
