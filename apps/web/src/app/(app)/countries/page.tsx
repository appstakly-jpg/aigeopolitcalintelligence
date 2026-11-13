import Link from "next/link";
import { BandBadge } from "@/components/badges";
import { Card, PageHeader } from "@/components/ui/primitives";
import { requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { humanize } from "@/lib/format";
import { CONTEXT_COUNTRIES, countryName, GCC, t } from "@/lib/i18n";
import { parseRange, rangeParams } from "@/lib/range";
import { latestAssessments } from "@/lib/risk";
import { qs, type SearchParams } from "@/lib/utils";

export const metadata = { title: "Countries" };
const RANK = ["insufficient_data", "low", "moderate", "elevated", "high", "severe"];

export default async function CountriesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const range = parseRange(await searchParams);
  const all = [...GCC, ...CONTEXT_COUNTRIES];
  const { counts, risk } = await withRls(s, async (tx) => {
    const counts = await tx<{ code: string; n: number; high: number }[]>`
      select c.code, count(i.id)::int as n, count(i.id) filter (where i.severity >= 4)::int as high
      from unnest(${all}::text[]) as c(code)
      left join incidents i on (i.country_code = c.code or c.code = any(i.affected_countries))
        and i.last_reported_at >= ${range.from.toISOString()}::timestamptz and i.first_reported_at <= ${range.to.toISOString()}::timestamptz
        and i.status in ('active', 'closed') and i.review_status <> 'rejected'
      group by c.code`;
    return { counts, risk: await latestAssessments(tx, [...GCC]) };
  });
  const card = (code: string, context: boolean) => {
    const c = counts.find((x) => x.code === code);
    const dims = risk.filter((r) => r.country_code === code).sort((a, b) =>
      RANK.indexOf(b.analyst_band ?? b.band) - RANK.indexOf(a.analyst_band ?? a.band));
    return (
      <Link key={code} href={`/countries/${code}${qs(rangeParams(range))}`}>
        <Card className="h-full p-4 transition-colors hover:border-primary">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="font-medium">{countryName(code, s.locale)}</div>
              <div className="text-xs text-muted-foreground">{countryName(code, s.locale === "ar" ? "en" : "ar")}</div>
            </div>
            {context ? <span className="text-[10px] text-muted-foreground uppercase">Context</span> : null}
          </div>
          <div className="mt-3 text-sm">{c?.n ?? 0} incident(s) in range{c?.high ? <span className="text-red-600 dark:text-red-400"> · {c.high} at severity 4–5</span> : null}</div>
          {!context ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {dims.slice(0, 3).map((d) => (
                <span key={d.dimension} className="inline-flex items-center gap-1 text-[11px]"><BandBadge band={d.analyst_band ?? d.band} /> {humanize(d.dimension)}</span>
              ))}
              {dims.length === 0 ? <BandBadge band="insufficient_data" /> : null}
            </div>
          ) : <div className="mt-2 text-[11px] text-muted-foreground">Monitored where it affects GCC security, energy, shipping or business.</div>}
        </Card>
      </Link>
    );
  };
  return (
    <>
      <PageHeader title={t(s.locale, "nav.countries")} description="The six GCC states, plus neighbouring countries tracked for spill-over into the Gulf." />
      <h2 className="mb-2 text-sm font-semibold">GCC states</h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{GCC.map((c) => card(c, false))}</div>
      <h2 className="mt-6 mb-2 text-sm font-semibold">Context countries</h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{CONTEXT_COUNTRIES.map((c) => card(c, true))}</div>
    </>
  );
}
