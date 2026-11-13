import { describe, expect, it } from "vitest";
import { chatToMarkdown, type ExportMessage } from "@/lib/chatExport";
import { describeFilters } from "@/lib/retrieval";

const at = new Date("2026-11-13T18:30:00Z");

describe("AI analyst conversation export", () => {
  const messages: ExportMessage[] = [
    { role: "user", content: "What happened near Bab el-Mandeb?", created_at: at, citations: null, generator: null, grounding_check: null, retrieval: null },
    {
      role: "assistant", content: "A tanker reported an explosion [1][2].", created_at: at, generator: "extractive (not_configured)",
      grounding_check: { ok: true }, retrieval: { from: "2026-11-06T18:30:00Z", to: "2026-11-13T18:30:00Z", filters: "sources graded A–C" },
      citations: [
        { n: 1, kind: "incident", ref: "INC-000003", title: "Tanker explosion\nnear [Bab el-Mandeb]", url: null, incidentId: "i-1", isDemo: true },
        { n: 2, kind: "article", ref: null, title: "Wire report", url: "https://publisher.example/a b>c", incidentId: "i-1", isDemo: true },
      ],
    },
  ];
  const md = chatToMarkdown({ title: "Bab el-Mandeb", organisation: "Demo", exportedBy: "analyst@demo.example", exportedAt: at, messages,
    incidentUrl: (id) => `https://intel.example.com/incidents/${id}` });

  it("keeps every citation with a working link, how the answer was produced and the filters", () => {
    expect(md).toContain("- [1] INC-000003: Tanker explosion near Bab el-Mandeb (SYNTHETIC) <https://intel.example.com/incidents/i-1>");
    expect(md).toContain("- [2] Wire report (SYNTHETIC) <https://publisher.example/a%20b%3Ec>");
    expect(md).toContain("_Produced by: extractive (not_configured) · citation check passed");
    expect(md).toContain("filters: sources graded A–C");
  });

  it("labels synthetic demo data", () => {
    expect(md).toContain("SYNTHETIC DEMO DATA");
    const real = chatToMarkdown({ title: "t", organisation: "o", exportedBy: "e", exportedAt: at, incidentUrl: (id) => id,
      messages: [{ ...messages[1], citations: [{ ...messages[1].citations![1], isDemo: false }] }] });
    expect(real).not.toContain("SYNTHETIC");
  });

  it("reports a failed citation check instead of hiding it", () => {
    const failed = chatToMarkdown({ title: "t", organisation: "o", exportedBy: "e", exportedAt: at, incidentUrl: (id) => id,
      messages: [{ ...messages[1], grounding_check: { ok: false } }] });
    expect(failed).toContain("citation check failed");
  });
});

describe("evidence filter descriptions", () => {
  it("describes only the filters that restrict evidence", () => {
    expect(describeFilters(undefined)).toBeNull();
    expect(describeFilters({ minReliability: "F" })).toBeNull();
    expect(describeFilters({ verification: ["confirmed", "corroborated"], minReliability: "C" }))
      .toBe("incidents: confirmed, corroborated; sources graded A–C");
    expect(describeFilters({ verification: ["single_source"] })).toBe("incidents: single-source");
  });
});
