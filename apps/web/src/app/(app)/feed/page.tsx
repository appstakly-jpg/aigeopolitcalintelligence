import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { DemoTag, ReliabilityBadge, StatusBadge } from "@/components/badges";
import { Badge, Button, Card, Empty, Input, LinkButton, PageHeader, Select } from "@/components/ui/primitives";
import { can, requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtDateTime, humanize } from "@/lib/format";
import { t } from "@/lib/i18n";
import { parseRange, rangeParams } from "@/lib/range";
import { searchTerms, tsOr } from "@/lib/retrieval";
import { first, isUuid, qs, type SearchParams } from "@/lib/utils";

export const metadata = { title: "Live Feed" };
const PAGE = 50;

interface Row {
  id: string; url: string; title: string; title_en: string | null; excerpt: string | null; excerpt_en: string | null; language: string;
  translated_by: string | null; published_at: Date | null; retrieved_at: Date; stance: string; status: string; relevance: number | null;
  link_status: string; injection_flags: string[]; is_demo: boolean; syndicated_from_article_id: string | null; attributed_origin: string | null;
  source_id: string; source_name: string; reliability: string; source_type: string; usage_policy: string;
  incidents: { id: string; ref: string }[] | null;
}

export default async function FeedPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const range = parseRange(sp);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const lang = first(sp.lang);
  const source = first(sp.source);
  const stance = first(sp.stance);
  const staff = can.staff(s.role);
  const status = staff ? first(sp.status) ?? "processed" : "processed";
  const q = (first(sp.q) ?? "").slice(0, 200);
  const page = Math.max(1, Number(first(sp.page)) || 1);
  const terms = searchTerms(q);
  const tsq = terms.length ? tsOr(terms) : null;

  const { rows, total, sources } = await withRls(s, async (tx) => {
    const where = tx`
      a.retrieved_at >= ${range.from.toISOString()}::timestamptz and a.retrieved_at <= ${range.to.toISOString()}::timestamptz
      ${status === "all" ? tx`` : tx`and a.status = ${status}`}
      ${lang === "en" || lang === "ar" ? tx`and a.language = ${lang}` : tx``}
      ${isUuid(source) ? tx`and a.source_id = ${source}` : tx``}
      ${stance && /^[a-z_]+$/.test(stance) ? tx`and a.stance = ${stance}` : tx``}
      ${tsq ? tx`and (a.search @@ to_tsquery('english', ${tsq}) or a.search @@ to_tsquery('arabic', ${tsq}))` : tx``}`;
    const rows = await tx<Row[]>`
      select a.id, a.url, a.title, a.title_en, a.excerpt, a.excerpt_en, a.language, a.translated_by, a.published_at, a.retrieved_at,
             a.stance, a.status, a.relevance, a.link_status, a.injection_flags, a.is_demo, a.syndicated_from_article_id, a.attributed_origin,
             s.id as source_id, s.name as source_name, s.reliability, s.source_type, s.usage_policy,
             (select json_agg(json_build_object('id', i.id, 'ref', i.ref)) from incident_articles ia join incidents i on i.id = ia.incident_id
               where ia.article_id = a.id) as incidents
      from articles a join sources s on s.id = a.source_id
      where ${where}
      order by coalesce(a.published_at, a.retrieved_at) desc
      limit ${PAGE} offset ${(page - 1) * PAGE}`;
    const [{ n }] = await tx<{ n: number }[]>`select count(*)::int as n from articles a where ${where}`;
    const sources = await tx<{ id: string; name: string }[]>`select id, name from sources order by name`;
    return { rows, total: n, sources };
  });

  const base = { ...rangeParams(range), lang, source, stance, status: staff ? status : undefined, q };
  return (
    <>
      <PageHeader title={t(s.locale, "nav.feed")}
        description="Every collected item with its source, reliability grade, language and translation. Headlines link to the original publisher; only short excerpts are stored, as each source's licence allows."
        meta={`${total} item(s) in range`} />
      <form className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-6" method="get">
        {Object.entries(rangeParams(range)).map(([k, v]) => <input key={k} type="hidden" name={k} value={v} />)}
        <Input name="q" defaultValue={q} placeholder="Search text (English or Arabic)" className="lg:col-span-2" />
        <Select name="source" defaultValue={source ?? ""}>
          <option value="">All sources</option>
          {sources.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </Select>
        <Select name="lang" defaultValue={lang ?? ""}>
          <option value="">All languages</option>
          <option value="en">English</option>
          <option value="ar">Arabic</option>
        </Select>
        <Select name="stance" defaultValue={stance ?? ""}>
          <option value="">Any stance</option>
          {["report", "official_statement", "denial", "correction", "retraction", "update"].map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
        </Select>
        {staff ? (
          <Select name="status" defaultValue={status}>
            {["processed", "irrelevant", "duplicate", "quarantined", "error", "new", "all"].map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
          </Select>
        ) : null}
        <Button type="submit" variant="outline">Filter</Button>
      </form>

      {rows.length === 0 ? <Empty>{t(s.locale, "common.none")}</Empty> : (
        <div className="grid gap-2">
          {rows.map((r) => (
            <Card key={r.id} className="p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <a href={r.url} target="_blank" rel="noopener noreferrer nofollow" dir={r.language === "ar" ? "rtl" : "ltr"}
                    className="inline-flex items-start gap-1 font-medium hover:underline">
                    {r.title} <ExternalLink className="mt-1 size-3 shrink-0 opacity-60" aria-hidden />
                  </a>
                  {r.language !== "en" && r.title_en ? (
                    <div className="text-sm text-muted-foreground">EN ({r.translated_by ? `machine translation: ${r.translated_by}` : "translation"}): {r.title_en}</div>
                  ) : r.language !== "en" ? <div className="text-xs text-muted-foreground">Not translated (no translation model configured).</div> : null}
                  {r.excerpt ? <p className="mt-1 line-clamp-2 text-sm text-muted-foreground" dir={r.language === "ar" ? "rtl" : "ltr"}>{r.excerpt}</p> : null}
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  <DemoTag show={r.is_demo} />
                  <Badge>{r.language.toUpperCase()}</Badge>
                  {r.stance !== "report" ? <Badge className="border-fuchsia-300 text-fuchsia-700 dark:text-fuchsia-300">{humanize(r.stance)}</Badge> : null}
                  {staff && r.status !== "processed" ? <StatusBadge status={r.status} /> : null}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <Link href={`/feed${qs({ ...base, source: r.source_id, page: undefined })}`} className="hover:underline">{r.source_name}</Link>
                  <ReliabilityBadge grade={r.reliability} /> <span>{humanize(r.source_type)}</span>
                </span>
                <span>Published {fmtDateTime(r.published_at, tz, s.locale)}</span>
                <span>Collected {fmtDateTime(r.retrieved_at, tz, s.locale)}</span>
                {r.syndicated_from_article_id ? <Badge title="Near-duplicate of an earlier item; not counted as independent corroboration">Syndicated copy</Badge> : null}
                {r.attributed_origin ? <span>Attributed to: {r.attributed_origin}</span> : null}
                {r.link_status === "broken" ? <Badge className="border-red-300 text-red-700">Link broken</Badge> : null}
                {staff && r.injection_flags.length ? (
                  <Badge className="border-red-300 text-red-700" title={r.injection_flags.join(", ")}>Prompt-injection text removed</Badge>
                ) : null}
                {r.incidents?.map((i) => <Link key={i.id} href={`/incidents/${i.id}`} className="text-primary hover:underline">{i.ref} →</Link>)}
              </div>
            </Card>
          ))}
        </div>
      )}
      <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
        <span>Page {page} of {Math.max(1, Math.ceil(total / PAGE))}</span>
        <div className="flex gap-2">
          {page > 1 ? <LinkButton href={`/feed${qs({ ...base, page: page - 1 })}`}>Previous</LinkButton> : null}
          {page * PAGE < total ? <LinkButton href={`/feed${qs({ ...base, page: page + 1 })}`}>Next</LinkButton> : null}
        </div>
      </div>
    </>
  );
}
