import Link from "next/link";
import { Suspense } from "react";
import { ChatPanel, type ChatMessage } from "@/components/ChatPanel";
import { Card, CardBody, CardHeader, LinkButton, Notice, PageHeader } from "@/components/ui/primitives";
import { requireSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { fmtDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { first, isUuid, type SearchParams } from "@/lib/utils";

export const metadata = { title: "AI Analyst" };

export default async function AnalystPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const s = await requireSession();
  const sp = await searchParams;
  const sid = first(sp.s);
  const tz = (s.tenantSettings.timezone as string | undefined) ?? s.timezone;
  const env = getEnv();
  const d = await withRls(s, async (tx) => {
    const sessions = await tx<{ id: string; title: string | null; created_at: Date }[]>`
      select id, title, created_at from chat_sessions order by created_at desc limit 30`;
    const messages = isUuid(sid) ? await tx<ChatMessage[]>`
      select id, role, content, citations, generator, grounding_check as "check" from chat_messages where session_id = ${sid} order by created_at` : [];
    return { sessions, messages };
  });
  const current = isUuid(sid) && d.sessions.some((x) => x.id === sid) ? sid : null;
  return (
    <>
      <PageHeader title={t(s.locale, "nav.analyst")}
        description="Questions are answered only from your organisation's stored, tenant-scoped evidence. Every sentence must cite a retrieved record; answers that cite anything else, or state figures the evidence does not contain, are discarded."
        actions={<LinkButton href="/analyst">New conversation</LinkButton>} />
      {!env.anthropicApiKey ? (
        <Notice tone="warn" className="mb-4">No language model is configured (ANTHROPIC_API_KEY is not set). The analyst still searches the stored evidence and returns cited records, but writes no narrative.</Notice>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card className="h-fit">
          <CardHeader title="Your conversations" description="Private to you." />
          <CardBody className="grid gap-1 text-sm">
            {d.sessions.length === 0 ? <span className="text-xs text-muted-foreground">None yet.</span> : d.sessions.map((x) => (
              <Link key={x.id} href={`/analyst?s=${x.id}`} className={`rounded-md px-2 py-1.5 hover:bg-muted ${x.id === current ? "bg-accent" : ""}`}>
                <div className="truncate">{x.title ?? "Untitled"}</div>
                <div className="text-[11px] text-muted-foreground">{fmtDateTime(x.created_at, tz, s.locale)}</div>
              </Link>
            ))}
          </CardBody>
        </Card>
        <Suspense>
          <ChatPanel key={current ?? "new"} sessionId={current} initial={current ? d.messages : []} />
        </Suspense>
      </div>
    </>
  );
}
