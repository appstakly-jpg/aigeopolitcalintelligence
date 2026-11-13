import type { Tx } from "./db";

export interface Assessment {
  id: string; country_code: string; dimension: string; score: string | null; band: string; analyst_band: string | null;
  analyst_rationale: string | null; confidence: string; forecast_uncertainty: string; incident_count: number; computed_at: Date;
  as_of_date: Date; window_start: Date; window_end: Date; status: string; methodology_version: string;
  components: Record<string, unknown>; contributions: { incident_id: string; ref: string; title: string; severity: number;
    verification_status: string; contribution: number; decay: number; country_weight: number; is_demo: boolean }[];
}

/** Latest assessment per country and dimension. */
export async function latestAssessments(tx: Tx, countries: string[]): Promise<Assessment[]> {
  return tx<Assessment[]>`
    select distinct on (a.country_code, a.dimension) a.id, a.country_code, a.dimension, a.score, a.band, a.analyst_band, a.analyst_rationale,
           a.confidence, a.forecast_uncertainty, a.incident_count, a.computed_at, a.as_of_date, a.window_start, a.window_end, a.status,
           m.version as methodology_version, a.components, a.contributions
    from risk_assessments a join risk_methodologies m on m.id = a.methodology_id
    where a.country_code = any(${countries}::text[])
    order by a.country_code, a.dimension, a.as_of_date desc, a.computed_at desc`;
}

export const DIMENSION_HELP: Record<string, string> = {
  armed_conflict: "Armed clashes, cross-border attacks and drone or missile activity affecting the country.",
  terrorism: "Attacks or plots attributed to non-state armed groups.",
  civil_unrest: "Protests and unrest.",
  maritime_security: "Incidents affecting vessels, ports and sea lanes.",
  energy_infrastructure: "Disruption to oil, gas, power and related facilities.",
  cybersecurity: "Reported cyber incidents.",
  political_stability: "Political violence, diplomatic crises and leadership disputes.",
  regulatory_policy: "Policy, sanctions and border or visa changes that affect operations.",
  supply_chain: "Shipping and logistics disruption.",
};
