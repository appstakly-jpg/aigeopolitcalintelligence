import Link from "next/link";
import { StatusBadge } from "@/components/badges";
import { Badge, Card, CardHeader, Empty, PageHeader, Table, Td, Th } from "@/components/ui/primitives";
import { requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { fmtAgo, fmtDateTime, humanize } from "@/lib/format";
import { t } from "@/lib/i18n";
import { first, type SearchParams } from "@/lib/utils";

export const metadata = { title: "Review queue" };

interface Item { id: string; item_type: string; item_id: string; reason: string; priority: number; status: string; created_at: Date; decided_at: Date | null;
  decision_note: string | null; created_by_agent: string | null; decider: string | null; label: string | null; sub: string | null }

function href(i: Item): string {
  switch (i.item_type) {
    case "incident": return `/incidents/${i.item_id}`;
    case "report": return `/reports/${i.item_id}`;
    case "alert": return `/alerts?status=pending_review`;
    case "source": return `/sources`;
    case "risk_assessment": return `/risk`;
    default: return `/admin`;
  }
}

export default async function ReviewsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession(["admin", "analyst", "reviewer"]);
  const type = first((await searchParams).type);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const rows = await withRls(s, (tx) => tx<Item[]>`
    select r.id, r.item_type, r.item_id, r.reason, r.priority, r.status, r.created_at, r.decided_at, r.decision_note, r.created_by_agent, u.email as decider,
           coalesce(i.title, a.title, rp.title, so.name) as label,
           coalesce(i.ref || ' · ' || i.verification_status || ' · severity ' || i.severity, a.kind || ' alert · ' || a.impact || ' impact', rp.ref || ' · ' || rp.status, so.status) as sub
    from analyst_reviews r
    left join incidents i on r.item_type = 'incident' and i.id = r.item_id
    left join alerts a on r.item_type = 'alert' and a.id = r.item_id
    left join reports rp on r.item_type = 'report' and rp.id = r.item_id
    left join sources so on r.item_type = 'source' and so.id = r.item_id
    left join app_users u on u.id = r.decided_by
    where (${type ?? null}::text is null or r.item_type = ${type ?? null})
      and (r.status = 'open' or r.decided_at > now() - interval '7 days')
    order by (r.status = 'open') desc, r.priority, r.created_at
    limit 200`);
  const open = rows.filter((r) => r.status === "open");
  const done = rows.filter((r) => r.status !== "open");
  const types = ["incident", "alert", "report", "source", "quality"];
  return (
    <>
      <PageHeader title={t(s.locale, "nav.reviews")}
        description="Items the agents could not settle on their own: high-severity or weakly sourced incidents, high-impact alerts, report publication, new sources and quality findings. Priority 1 is most urgent."
        actions={<div className="flex flex-wrap gap-1">
          <Link href="/reviews" className={`rounded-md border px-2 py-1 text-xs ${!type ? "bg-accent" : ""}`}>All</Link>
          {types.map((x) => <Link key={x} href={`/reviews?type=${x}`} className={`rounded-md border px-2 py-1 text-xs ${type === x ? "bg-accent" : ""}`}>{humanize(x)}</Link>)}
        </div>} />
      <Card>
        <CardHeader title={`Open (${open.length})`} />
        {open.length === 0 ? <div className="p-4"><Empty>Nothing waiting for review.</Empty></div> : (
          <Table>
            <thead><tr><Th>P</Th><Th>Item</Th><Th>Why it needs review</Th><Th>Raised</Th></tr></thead>
            <tbody>{open.map((r) => (
              <tr key={r.id}>
                <Td><Badge className={r.priority <= 1 ? "border-red-300 text-red-700" : r.priority === 2 ? "border-amber-300 text-amber-700" : ""}>P{r.priority}</Badge></Td>
                <Td><Link href={href(r)} className="font-medium hover:underline">{r.label ?? humanize(r.item_type)}</Link>
                  <div className="text-[11px] text-muted-foreground">{humanize(r.item_type)}{r.sub ? ` · ${r.sub}` : ""}</div></Td>
                <Td className="max-w-lg text-xs">{r.reason}</Td>
                <Td className="text-xs whitespace-nowrap text-muted-foreground">{fmtAgo(r.created_at)}<div>{r.created_by_agent ?? ""}</div></Td>
              </tr>
            ))}</tbody>
          </Table>
        )}
      </Card>
      {done.length ? (
        <Card className="mt-4">
          <CardHeader title="Decided in the last 7 days" />
          <Table>
            <thead><tr><Th>Item</Th><Th>Decision</Th><Th>By</Th><Th>Note</Th></tr></thead>
            <tbody>{done.map((r) => (
              <tr key={r.id}>
                <Td><Link href={href(r)} className="hover:underline">{r.label ?? humanize(r.item_type)}</Link><div className="text-[11px] text-muted-foreground">{humanize(r.item_type)}</div></Td>
                <Td><StatusBadge status={r.status} /></Td>
                <Td className="text-xs">{r.decider ?? "—"}<div className="text-muted-foreground">{fmtDateTime(r.decided_at, tz, s.locale)}</div></Td>
                <Td className="max-w-md text-xs">{r.decision_note ?? ""}</Td>
              </tr>
            ))}</tbody>
          </Table>
        </Card>
      ) : null}
    </>
  );
}
