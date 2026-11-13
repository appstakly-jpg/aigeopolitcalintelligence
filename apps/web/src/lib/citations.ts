// Citation integrity for grounded answers: every [n] must point at a retrieved evidence item,
// factual sentences must cite, and figures must appear in the cited evidence.

export interface CheckResult {
  ok: boolean;
  cited: number[];
  errors: string[];
  warnings: string[];
}

const CITE = /\[(\d{1,3})\]/g;
const HAS_CITE = /\[\d{1,3}\]/;

export function citedNumbers(text: string): number[] {
  const out = new Set<number>();
  for (const m of text.matchAll(CITE)) out.add(Number(m[1]));
  return [...out].sort((a, b) => a - b);
}

function sentences(text: string): string[] {
  return text
    .split(/\n+/)
    .flatMap((line) => line.split(/(?<=[.!?؟])\s+(?=[A-Z؀-ۿ"“(])/))
    .map((s) => s.replace(/^[-*•\d.)\s]+/, "").trim())
    .filter((s) => s.length > 0);
}

// Sentences that describe the evidence base or its limits do not need a citation.
const META = /(no (stored|matching|relevant) (evidence|records?)|not (in|covered by) the (stored )?evidence|cannot (confirm|verify)|insufficient evidence|uncertain|i (do not|don't) have|based on the (stored|retrieved) (evidence|records)|لا توجد|غير مؤكد)/i;

export function checkAnswer(text: string, evidenceCount: number, evidenceTexts: string[] = []): CheckResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  const cited = citedNumbers(text);
  for (const n of cited) {
    if (n < 1 || n > evidenceCount) errors.push(`Citation [${n}] does not correspond to any retrieved evidence item.`);
  }
  const uncited = sentences(text).filter((s) => s.length > 30 && !HAS_CITE.test(s) && !META.test(s));
  if (uncited.length) warnings.push(`${uncited.length} sentence(s) make statements without a citation.`);
  if (evidenceTexts.length) {
    const haystack = evidenceTexts.join(" ").replace(/,/g, "");
    const figures = new Set((text.replace(CITE, "").match(/\b\d{2,}(?:\.\d+)?\b/g) ?? []).filter((f) => !/^(19|20)\d{2}$/.test(f)));
    for (const f of figures) if (!haystack.includes(f)) errors.push(`The figure ${f} does not appear in the retrieved evidence.`);
  }
  return { ok: errors.length === 0, cited, errors, warnings };
}
