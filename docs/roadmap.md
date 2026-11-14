# Roadmap

Prioritised by what blocks real use first. The MVP scope in the specification deferred advanced
forecasting, complex maritime integrations, billing and enterprise customisation; they are here.

## P0: before relying on it in production

| Item | Why |
| --- | --- |
| Licence and terms review of every catalogue source, then `gccintel sources-verify` from the deployment network | 12 of 21 sources are `review_required` (10 run in limited mode); Arab News needs the publisher's permission; GDELT must be re-checked from the deployment address ([sources.md](sources.md#catalogue)) |
| Run with a model API key and measure: extraction, translation, brief narrative and AI analyst quality, refusals and cost | Model paths are implemented and validated but were never exercised against live APIs |
| Claimant-aware corroboration: count who makes a claim separately from the outlets relaying it | Five outlets relaying one IRGC statement made the Strait of Hormuz incident "corroborated" on 14 November ([methodology.md](methodology.md#known-limitations)) |
| Analyst-labelled evaluation set from real, manually reviewed incidents, starting with the live incidents of 14 November; measure missed events too; track the metrics per release | The synthetic set scores 1.0 because the rules were written alongside it; the live check was the developer's, and discarded items were only spot-checked ([eval/README.md](../eval/README.md)) |
| Require MFA for admins and reviewers; server-side session list with revoke | See [security.md](security.md#known-gaps) |
| Egress firewall for workers (deny private ranges and cloud metadata), HSTS at the proxy | Defence in depth for the SSRF guard and transport security |
| Automated backups with a tested restore, alerting on `/api/health`, dead jobs and failing sources | Operations |
| Live tests of Slack, Telegram and webhook delivery; Supabase Auth end to end if that mode is used | Implemented, tested with mocks only |

## P1: next

| Item | Notes |
| --- | --- |
| Trend analysis | Rolling 7/30/90-day comparisons, event-frequency change against a baseline, seasonality checks, geospatial clustering |
| Scenario analysis | Labelled as probabilistic scenarios with assumptions, horizon, supporting and contradicting evidence and confidence; analyst-editable; no false precision |
| Risk history charts | Daily assessments with methodology versions are already stored; chart them per country and dimension |
| Methodology management | Publish new methodology versions (weights, vulnerability baselines) from the app; carry analyst band adjustments forward with an expiry |
| Semantic retrieval | Hybrid full-text and pgvector retrieval for the AI analyst and for event matching |
| Languages and places | Persian, Hebrew and Turkish extraction through translation; grouping English and Arabic reports through translation when no precise place is named; more gazetteer entries (provinces, districts such as Saada and Kordofan, ports, facilities, Horn of Africa) |
| Source management | Edit country focus, priority, content kind and feed time zone (including ignoring a wrong feed offset) for custom sources; reliability track record from corrections and retractions |
| More sources | ReliefWeb API for UN humanitarian updates (UN News's feed is disallowed by robots.txt); licensed news APIs |
| Alerts | Alert when an official travel advisory for a watched country changes; per-incident throttling (the standard throttle is per rule) |
| Client integration | Scoped API keys, outbound webhooks per client, scheduled report e-mail, logo upload for branded reports |
| Security | Nonce-based CSP, `APP_ENCRYPTION_KEY` rotation command |

## P2: later

| Item | Notes |
| --- | --- |
| Billing | Payment provider integration in test mode first, then invoices; plans and prices stay configuration |
| Enterprise | SSO (SAML/OIDC), SCIM provisioning, per-client taxonomies, white-label domains, data residency |
| Maritime | Licensed AIS and shipping datasets for vessel and port context (licensed data only) |
| Social media | Official accounts through permitted public endpoints only, under each platform's terms |
