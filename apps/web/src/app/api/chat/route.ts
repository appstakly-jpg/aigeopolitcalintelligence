import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { getSession, rateLimit } from "@/lib/auth";
import { composeAnswer, gatherEvidence } from "@/lib/chat";
import { withRls } from "@/lib/db";
import { parseRange } from "@/lib/range";

const Body = z.object({
  sessionId: z.string().uuid().optional(),
  message: z.string().trim().min(3).max(2000),
  range: z.enum(["24h", "7d", "30d", "90d"]).optional(),
  from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
});

export async function POST(req: NextRequest) {
  const s = await getSession();
  if (!s) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const origin = req.headers.get("origin");
  if (origin && origin !== req.nextUrl.origin) return NextResponse.json({ error: "cross-origin request refused" }, { status: 403 });
  if (!(await rateLimit(`chat:${s.userId}`, 3600, 60))) return NextResponse.json({ error: "Rate limit reached (60 questions per hour)." }, { status: 429 });

  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Ask a question of 3 to 2000 characters." }, { status: 400 });
  const b = parsed.data;
  const range = parseRange({ range: b.range, from: b.from, to: b.to });

  try {
    // Transaction 1: session bookkeeping and retrieval under RLS (short).
    const prep = await withRls(s, async (tx) => {
      const [plan] = await tx<{ features: { ai_chat?: boolean } }[]>`select p.features from plans p join tenants t on t.plan_id = p.id where t.id = ${s.tenantId}`;
      if (plan && plan.features.ai_chat === false) throw new Error("The AI analyst is not included in your plan.");
      let sessionId = b.sessionId;
      if (sessionId) {
        const own = await tx`select id from chat_sessions where id = ${sessionId}`;
        if (!own.length) throw new Error("Chat session not found.");
      } else {
        const [row] = await tx<{ id: string }[]>`
          insert into chat_sessions(tenant_id, user_id, title) values (${s.tenantId}, ${s.userId}, ${b.message.slice(0, 80)}) returning id`;
        sessionId = row.id;
      }
      await tx`insert into chat_messages(tenant_id, session_id, role, content) values (${s.tenantId}, ${sessionId}, 'user', ${b.message})`;
      return { sessionId: sessionId!, ...(await gatherEvidence(tx, b.message, range.from, range.to)) };
    });
    // The model call happens outside any database transaction.
    const a = prep.early ?? (await composeAnswer({ tenantId: s.tenantId, question: b.message, items: prep.items, from: range.from, to: range.to }));
    // Transaction 2: store the answer with its citations and grounding check.
    const result = await withRls(s, async (tx) => {
      const [msg] = await tx<{ id: string; created_at: Date }[]>`
        insert into chat_messages(tenant_id, session_id, role, content, citations, retrieval, grounding_check, generator)
        values (${s.tenantId}, ${prep.sessionId}, 'assistant', ${a.content}, ${tx.json(a.citations as never)}, ${tx.json(a.retrieval as never)},
                ${tx.json(a.check as never)}, ${a.generator})
        returning id, created_at`;
      return { sessionId: prep.sessionId, message: { id: msg.id, role: "assistant", content: a.content, citations: a.citations, check: a.check, generator: a.generator, created_at: msg.created_at } };
    });
    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "Chat failed" }, { status: 400 });
  }
}
