// Markdown export of an AI analyst conversation: every answer keeps its numbered citations with
// links, how it was produced and whether it passed the citation check.

export interface ExportCitation {
  n: number; kind: string; ref: string | null; title: string; url: string | null; incidentId: string | null; isDemo: boolean;
}

export interface ExportMessage {
  role: "user" | "assistant";
  content: string;
  created_at: Date;
  citations: ExportCitation[] | null;
  generator: string | null;
  grounding_check: { ok?: boolean } | null;
  retrieval: { from?: string; to?: string; filters?: string | null } | null;
}

function utc(d: Date | string): string {
  return new Date(d).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

/** One line of plain text: newlines and Markdown link brackets in titles cannot break the layout. */
function oneLine(s: string): string {
  return s.replace(/\s+/g, " ").replace(/[[\]]/g, "").trim();
}

export function chatToMarkdown(opts: {
  title: string; organisation: string; exportedBy: string; exportedAt: Date; messages: ExportMessage[];
  incidentUrl: (incidentId: string) => string;
}): string {
  const synthetic = opts.messages.some((m) => (m.citations ?? []).some((c) => c.isDemo));
  const out: string[] = [
    `# AI analyst conversation: ${oneLine(opts.title)}`,
    "",
    `Organisation: ${oneLine(opts.organisation)} · exported ${utc(opts.exportedAt)} by ${oneLine(opts.exportedBy)}`,
    "",
    "Answers are generated only from evidence stored in the platform and cite it by number. Reported claims are not verified facts; check the verification status of each incident.",
  ];
  if (synthetic) out.push("", "> **SYNTHETIC DEMO DATA.** Some cited records are fabricated test data, not real events.");
  for (const m of opts.messages) {
    if (m.role === "user") {
      out.push("", `## Question · ${utc(m.created_at)}`, "", m.content.trim());
      continue;
    }
    const check = m.grounding_check?.ok === false ? "failed" : "passed";
    const scope = m.retrieval?.from && m.retrieval?.to ? ` · evidence from ${utc(m.retrieval.from)} to ${utc(m.retrieval.to)}` : "";
    const filters = m.retrieval?.filters ? ` · filters: ${oneLine(m.retrieval.filters)}` : "";
    out.push("", `## Answer · ${utc(m.created_at)}`, "", `_Produced by: ${m.generator ?? "unknown"} · citation check ${check}${scope}${filters}_`, "", m.content.trim());
    const cites = m.citations ?? [];
    if (cites.length) {
      out.push("", "Evidence:");
      for (const c of cites) {
        const raw = c.kind === "incident" && c.incidentId ? opts.incidentUrl(c.incidentId) : c.url;
        const link = raw ? raw.replace(/[<>\s]/g, (ch) => encodeURIComponent(ch)) : null;
        const label = c.ref ? `${c.ref}: ${oneLine(c.title)}` : oneLine(c.title);
        out.push(`- [${c.n}] ${label}${c.isDemo ? " (SYNTHETIC)" : ""}${link ? ` <${link}>` : ""}`);
      }
    }
  }
  return out.join("\n") + "\n";
}
