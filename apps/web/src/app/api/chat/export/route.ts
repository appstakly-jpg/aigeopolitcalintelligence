import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { getSession, rateLimit } from "@/lib/auth";
import { chatToMarkdown, type ExportMessage } from "@/lib/chatExport";
import { withRls } from "@/lib/db";
import { appUrl } from "@/lib/http";
import { audit } from "@/lib/jobs";

/** GET /api/chat/export?sessionId=... : the caller's own conversation as Markdown, with citations. */
export async function GET(req: NextRequest) {
  const s = await getSession();
  if (!s) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  if (!(await rateLimit(`api:${s.userId}`, 60, 120))) return NextResponse.json({ error: "rate limit exceeded" }, { status: 429 });
  const id = z.string().uuid().safeParse(req.nextUrl.searchParams.get("sessionId"));
  if (!id.success) return NextResponse.json({ error: "sessionId must be a UUID" }, { status: 400 });

  const data = await withRls(s, async (tx) => {
    // RLS limits chat sessions to the signed-in user's own, in the active organisation.
    const [session] = await tx<{ id: string; title: string | null }[]>`select id, title from chat_sessions where id = ${id.data}`;
    if (!session) return null;
    const messages = await tx<ExportMessage[]>`
      select role, content, created_at, citations, generator, grounding_check, retrieval
      from chat_messages where session_id = ${session.id} order by created_at, id`;
    await audit(tx, s, "chat.export", "chat_session", session.id, { messages: messages.length });
    return { session, messages };
  });
  if (!data) return NextResponse.json({ error: "not found" }, { status: 404 });

  const now = new Date();
  const body = chatToMarkdown({
    title: data.session.title || "untitled", organisation: s.tenantName, exportedBy: s.email, exportedAt: now,
    messages: data.messages, incidentUrl: (incidentId) => appUrl(`/incidents/${incidentId}`),
  });
  return new NextResponse(body, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="analyst-chat-${now.toISOString().slice(0, 10)}.md"`,
      "Cache-Control": "private, no-store",
    },
  });
}
