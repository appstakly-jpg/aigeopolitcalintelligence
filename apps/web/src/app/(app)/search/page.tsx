import Link from "next/link";
import { DemoTag, ReliabilityBadge, SeverityBadge, StatusBadge, VerificationBadge } from "@/components/badges";
import { Card, CardBody, CardHeader, Empty, PageHeader } from "@/components/ui/primitives";
import { requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtDateTime, humanize } from "@/lib/format";
import { searchTerms, tsOr } from "@/lib/retrieval";
import { first, type SearchParams } from "@/lib/utils";

export const metadata = { title: "Search" };

export default async function SearchPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const q = (first((await searchParams).q) ?? "").trim().slice(0, 200);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const terms = searchTerms(q);
  const tsq = terms.length ? tsOr(terms) : null;
  const like = `%${q.replace(/[%_\\]/g, (c) => `\\${c}`)}%`;
  const d = q.length < 2 ? null : await withRls(s, async (tx) => {
    const incidents = await tx<{ id: string; ref: string; title: string; verification_status: string; severity: number; last_reported_at: Date; is_demo: boolean }[]>`
      select id, ref, title, verification_status, severity, last_reported_at, is_demo from incidents
      where status <> 'merged' and ((${tsq}::text is not null and search @@ to_tsquery('simple', coalesce(${tsq}::text, ''))) or ref ilike ${like} or title ilike ${like})
      order by last_reported_at desc limit 25`;
    const articles = tsq ? await tx<{ id: string; title: string; title_en: string | null; url: string; language: string; published_at: Date | null; source: string; reliability: string; is_demo: boolean; incident_id: string | null }[]>`
      select a.id, a.title, a.title_en, a.url, a.language, a.published_at, s.name as source, s.reliability, a.is_demo,
             (select ia.incident_id from incident_articles ia where ia.article_id = a.id limit 1) as incident_id
      from articles a join sources s on s.id = a.source_id
      where a.search @@ to_tsquery('english', ${tsq}) or a.search @@ to_tsquery('arabic', ${tsq})
      order by a.published_at desc nulls last limit 25` : [];
    const reports = await tx<{ id: string; ref: string; title: string; status: string; created_at: Date }[]>`
      select id, ref, title, status, created_at from reports where title ilike ${like} or ref ilike ${like} order by created_at desc limit 10`;
    const sources = await tx<{ id: string; name: string; publisher: string | null; reliability: string; status: string }[]>`
      select id, name, publisher, reliability, status from sources where name ilike ${like} or publisher ilike ${like} or url ilike ${like} limit 10`;
    return { incidents, articles, reports, sources };
  });
  const total = d ? d.incidents.length + d.articles.length + d.reports.length + d.sources.length : 0;
  return (
    <>
      <PageHeader title={q ? `Search: “${q}”` : "Search"} description="Searches incidents, collected articles (English and Arabic, including translations), reports and sources you can access."
        meta={d ? `${total} result(s)` : "Type at least two characters in the search box."} />
      {d && total === 0 ? <Empty>No matches.</Empty> : null}
      {d ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {d.incidents.length ? (
            <Card><CardHeader title={`Incidents (${d.incidents.length})`} />
              <CardBody className="grid gap-2">{d.incidents.map((i) => (
                <Link key={i.id} href={`/incidents/${i.id}`} className="rounded-md border p-2 hover:border-primary">
                  <div className="text-sm font-medium">{i.title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">{i.ref} <VerificationBadge status={i.verification_status} /> <SeverityBadge value={i.severity} /> {fmtDateTime(i.last_reported_at, tz, s.locale)} <DemoTag show={i.is_demo} /></div>
                </Link>))}
              </CardBody></Card>
          ) : null}
          {d.articles.length ? (
            <Card><CardHeader title={`Articles (${d.articles.length})`} />
              <CardBody className="grid gap-2">{d.articles.map((a) => (
                <div key={a.id} className="rounded-md border p-2">
                  <a href={a.url} target="_blank" rel="noopener noreferrer nofollow" className="text-sm font-medium hover:underline" dir={a.language === "ar" ? "rtl" : "ltr"}>{a.title}</a>
                  {a.language !== "en" && a.title_en ? <div className="text-xs text-muted-foreground">EN: {a.title_en}</div> : null}
                  <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">{a.source} <ReliabilityBadge grade={a.reliability} /> {fmtDateTime(a.published_at, tz, s.locale)} <DemoTag show={a.is_demo} />
                    {a.incident_id ? <Link href={`/incidents/${a.incident_id}`} className="text-primary hover:underline">incident →</Link> : null}</div>
                </div>))}
              </CardBody></Card>
          ) : null}
          {d.reports.length ? (
            <Card><CardHeader title={`Reports (${d.reports.length})`} />
              <CardBody className="grid gap-2">{d.reports.map((r) => (
                <Link key={r.id} href={`/reports/${r.id}`} className="flex items-center justify-between rounded-md border p-2 text-sm hover:border-primary">
                  <span>{r.title} <span className="text-xs text-muted-foreground">{r.ref}</span></span><StatusBadge status={r.status} /></Link>))}
              </CardBody></Card>
          ) : null}
          {d.sources.length ? (
            <Card><CardHeader title={`Sources (${d.sources.length})`} />
              <CardBody className="grid gap-2">{d.sources.map((x) => (
                <Link key={x.id} href="/sources" className="flex items-center justify-between rounded-md border p-2 text-sm hover:border-primary">
                  <span>{x.name} <span className="text-xs text-muted-foreground">{x.publisher}</span></span>
                  <span className="flex gap-1"><ReliabilityBadge grade={x.reliability} /><StatusBadge status={x.status} /></span></Link>))}
              </CardBody></Card>
          ) : null}
        </div>
      ) : null}
      {d && terms.length ? <p className="mt-4 text-[11px] text-muted-foreground">Matched terms: {terms.map(humanize).join(", ")}</p> : null}
    </>
  );
}
