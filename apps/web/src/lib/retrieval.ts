import type { Tx } from "./db";
import { COUNTRY_NAMES } from "./i18n";

// Retrieval over stored, tenant-scoped evidence (runs inside withRls, so tenant isolation and role
// visibility are enforced by Postgres). Lexical full-text search; see docs/roadmap.md for pgvector.

export const VERIFICATION_STATUSES = ["confirmed", "corroborated", "single_source", "disputed", "unverified"] as const;
export const RELIABILITY_GRADES = ["A", "B", "C", "D", "E", "F"] as const;

/** Optional evidence filters chosen by the user. Empty means no filter. */
export interface RetrievalFilters {
  /** Incident records: only these verification statuses. */
  verification?: (typeof VERIFICATION_STATUSES)[number][];
  /** Source articles: only sources graded this letter or better (A is best). */
  minReliability?: (typeof RELIABILITY_GRADES)[number];
}

export function describeFilters(f: RetrievalFilters | undefined): string | null {
  const parts: string[] = [];
  if (f?.verification?.length) parts.push(`incidents: ${f.verification.map((v) => v.replace("_", "-")).join(", ")}`);
  if (f?.minReliability && f.minReliability !== "F") parts.push(`sources graded ${f.minReliability === "A" ? "A" : `A–${f.minReliability}`}`);
  return parts.length ? parts.join("; ") : null;
}

export interface Evidence {
  n: number;
  kind: "incident" | "article";
  id: string;
  ref: string | null;
  title: string;
  text: string;
  url: string | null;
  publisher: string | null;
  reliability: string | null;
  verification: string | null;
  severity: number | null;
  when: string | null;
  countryCode: string | null;
  incidentId: string | null;
  isDemo: boolean;
}

const STOP = new Set(("the a an and or of in on at to for from by with about what which who whom whose when where why how is are was were be been " +
  "this that these those there their it its as any all some latest recent recently happened happening happen news update updates tell me please " +
  "give summary summarise summarize week weeks day days today yesterday month current currently situation report reports show list does did do " +
  "has have had will would could should can than then into over under near after before during between ما هو هي في من على عن إلى ماذا").split(/\s+/));

export function searchTerms(q: string, max = 12): string[] {
  const words = (q.toLowerCase().normalize("NFKC").match(/[\p{L}\p{N}]{3,}/gu) ?? []).filter((w) => !STOP.has(w));
  return [...new Set(words)].slice(0, max);
}

/** Safe to_tsquery input: terms contain only letters/digits, OR-combined with prefix matching. */
export function tsOr(terms: string[]): string {
  return terms.map((t) => `${t}:*`).join(" | ");
}

export function detectCountries(q: string): string[] {
  const lower = q.toLowerCase();
  const out: string[] = [];
  for (const [code, n] of Object.entries(COUNTRY_NAMES)) {
    const aliases = [n.en.toLowerCase(), n.ar, code === "AE" ? "uae" : "", code === "SA" ? "saudi" : "", code === "AE" ? "emirates" : ""].filter(Boolean);
    if (aliases.some((a) => (a.length <= 3 ? new RegExp(`\\b${a}\\b`, "i").test(lower) : lower.includes(a)))) out.push(code);
  }
  return out;
}

interface IncidentRow {
  id: string; ref: string; title: string; summary: string | null; verification_status: string; severity: number;
  location_name: string | null; country_code: string | null; last_reported_at: Date; is_demo: boolean; uncertainty_notes: string | null;
}
interface ArticleRow {
  id: string; url: string; title: string; title_en: string | null; excerpt: string | null; excerpt_en: string | null; language: string;
  publisher: string | null; reliability: string | null; published_at: Date | null; is_demo: boolean; incident_id: string | null; stance: string;
}

export async function retrieve(tx: Tx, question: string, opts: { from: Date; to: Date; maxIncidents?: number; filters?: RetrievalFilters }): Promise<Evidence[]> {
  const terms = searchTerms(question);
  const countries = detectCountries(question);
  const query = terms.length ? tsOr(terms) : null;
  const maxInc = opts.maxIncidents ?? 6;
  const verification: string[] = opts.filters?.verification ?? [];
  // Grades are single letters A-F, so "A..grade" is a plain text comparison.
  const maxGrade = opts.filters?.minReliability ?? "F";

  const incidents = await tx<IncidentRow[]>`
    select i.id, i.ref, i.title, i.summary, i.verification_status, i.severity, i.location_name, i.country_code,
           i.last_reported_at, i.is_demo, i.uncertainty_notes
    from incidents i
    where i.status in ('active', 'closed') and i.review_status <> 'rejected'
      and i.last_reported_at >= ${opts.from.toISOString()}::timestamptz and i.first_reported_at <= ${opts.to.toISOString()}::timestamptz
      and (${verification.length === 0} or i.verification_status = any(${verification}::text[]))
      and (
        ${query === null && countries.length === 0}
        or (${query}::text is not null and i.search @@ to_tsquery('simple', coalesce(${query}::text, '')))
        or i.country_code = any(${countries}::text[])
        or i.affected_countries && ${countries}::text[]
      )
    order by case when ${query}::text is not null and i.search @@ to_tsquery('simple', coalesce(${query}::text, ''))
                  then ts_rank(i.search, to_tsquery('simple', coalesce(${query}::text, ''))) else 0 end desc,
             i.severity desc, i.last_reported_at desc
    limit ${maxInc}`;

  const incidentIds = incidents.map((i) => i.id);
  const linked = incidentIds.length
    ? await tx<ArticleRow[]>`
        select * from (
          select distinct on (ia.incident_id, ia.origin_key) a.id, a.url, a.title, a.title_en, a.excerpt, a.excerpt_en, a.language,
                 s.publisher, s.reliability, a.published_at, a.is_demo, ia.incident_id, a.stance
          from incident_articles ia join articles a on a.id = ia.article_id join sources s on s.id = a.source_id
          where ia.incident_id = any(${incidentIds}::uuid[]) and s.reliability <= ${maxGrade}
          order by ia.incident_id, ia.origin_key, (ia.role = 'original') desc, a.published_at
        ) x order by x.published_at desc nulls last limit 18`
    : [];

  const direct = query
    ? await tx<ArticleRow[]>`
        select a.id, a.url, a.title, a.title_en, a.excerpt, a.excerpt_en, a.language, s.publisher, s.reliability, a.published_at,
               a.is_demo, null::uuid as incident_id, a.stance
        from articles a join sources s on s.id = a.source_id
        where a.status = 'processed' and a.retrieved_at >= ${opts.from.toISOString()}::timestamptz and s.reliability <= ${maxGrade}
          and (a.search @@ to_tsquery('english', ${query}) or a.search @@ to_tsquery('arabic', ${query}))
          and not (a.id = any(${linked.map((l) => l.id)}::uuid[]))
        order by a.published_at desc nulls last limit 6`
    : [];

  const out: Evidence[] = [];
  for (const i of incidents) {
    out.push({
      n: out.length + 1, kind: "incident", id: i.id, ref: i.ref, title: i.title,
      text: [i.summary, i.uncertainty_notes ? `Uncertainty: ${i.uncertainty_notes}` : null].filter(Boolean).join(" "),
      url: null, publisher: null, reliability: null, verification: i.verification_status, severity: i.severity,
      when: i.last_reported_at.toISOString(), countryCode: i.country_code, incidentId: i.id, isDemo: i.is_demo,
    });
  }
  for (const a of [...linked, ...direct]) {
    out.push({
      n: out.length + 1, kind: "article", id: a.id, ref: null, title: a.title_en || a.title,
      text: (a.excerpt_en || a.excerpt || "").slice(0, 600) + (a.stance && a.stance !== "report" ? ` (stance: ${a.stance})` : ""),
      url: a.url, publisher: a.publisher, reliability: a.reliability, verification: null, severity: null,
      when: a.published_at ? a.published_at.toISOString() : null, countryCode: null, incidentId: a.incident_id, isDemo: a.is_demo,
    });
  }
  return out;
}

export function evidenceBlock(items: Evidence[]): string {
  return items.map((e) => {
    const head = e.kind === "incident"
      ? `[${e.n}] INCIDENT RECORD ${e.ref} | verification: ${e.verification} | severity ${e.severity}/5 | last reported ${e.when}`
      : `[${e.n}] SOURCE ARTICLE | ${e.publisher ?? "unknown publisher"} (reliability ${e.reliability ?? "?"}) | published ${e.when ?? "unknown"}${e.incidentId ? " | linked to an incident above" : ""}`;
    return `${head}\n<untrusted_content>\n${e.title}\n${e.text}\n</untrusted_content>`;
  }).join("\n\n");
}
