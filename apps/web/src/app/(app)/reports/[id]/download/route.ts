import { NextResponse, type NextRequest } from "next/server";
import { getSession } from "@/lib/auth";
import { withRls } from "@/lib/db";
import { isUuid } from "@/lib/utils";

// Downloads run under the caller's RLS identity: clients only reach published reports.
export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const s = await getSession();
  if (!s) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const { id } = await ctx.params;
  if (!isUuid(id)) return NextResponse.json({ error: "not found" }, { status: 404 });
  const exportId = req.nextUrl.searchParams.get("export");
  const version = Number(req.nextUrl.searchParams.get("v")) || null;

  const file = await withRls(s, async (tx) => {
    const [r] = await tx<{ ref: string; current_version: number }[]>`select ref, current_version from reports where id = ${id}`;
    if (!r) return null;
    if (exportId) {
      if (!isUuid(exportId)) return null;
      const [e] = await tx<{ format: string; content_type: string; bytes: Buffer; version: number }[]>`
        select e.format, e.content_type, e.bytes, v.version from report_exports e join report_versions v on v.id = e.report_version_id
        where e.id = ${exportId} and v.report_id = ${id}`;
      return e ? { name: `${r.ref}-v${e.version}.${e.format}`, type: e.content_type, body: e.bytes } : null;
    }
    const [v] = await tx<{ html: string; version: number }[]>`
      select html, version from report_versions where report_id = ${id} and version = ${version ?? r.current_version}`;
    return v ? { name: `${r.ref}-v${v.version}.html`, type: "text/html; charset=utf-8", body: Buffer.from(v.html, "utf8") } : null;
  });
  if (!file) return NextResponse.json({ error: "not found" }, { status: 404 });
  await withRls(s, (tx) => tx`insert into audit_log(tenant_id, actor_user_id, action, entity_type, entity_id, details)
    values (${s.tenantId}, ${s.userId}, 'report.download', 'report', ${id}, ${tx.json({ file: file.name } as never)})`);
  return new NextResponse(new Uint8Array(file.body), {
    headers: {
      "Content-Type": file.type,
      "Content-Disposition": `attachment; filename="${file.name.replace(/[^A-Za-z0-9._-]/g, "_")}"`,
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
