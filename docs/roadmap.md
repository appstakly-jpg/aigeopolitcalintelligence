# Roadmap

Prioritised by what blocks real use first. The MVP scope in the specification deferred advanced
forecasting, complex maritime integrations, billing and enterprise customisation; they are here.

## P0: before relying on it in production

| Item | Why |
| --- | --- |
| Licence and terms review of every catalogue source, then `gccintel sources-verify` from the deployment network | 12 of 21 sources are `review_required`; live fetching could not be tested from the build environment; UN News and GDELT URLs need re-checking |
| Run with a model API key and measure: extraction, translation, brief narrative and AI analyst quality, refusals and cost | Model paths are implemented and validated but were never exercised against live APIs |
| Analyst-labelled evaluation set from real, manually reviewed incidents; track the metrics per release | The current set is small, synthetic and written by the developers ([eval/README.md](../eval/README.md)) |
| Fix the known evaluation failure (missile attack on a ship classified as maritime only) | Category recall 0.947 on the synthetic set |
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
| Languages and places | Persian, Hebrew and Turkish extraction through translation; more gazetteer entries (provinces, ports, facilities, Horn of Africa) |
| Source management | Edit country focus, priority and feed time zone for custom sources; reliability track record from corrections and retractions |
| Client integration | Scoped API keys, outbound webhooks per client, scheduled report e-mail, logo upload for branded reports |
| Security | Nonce-based CSP, `APP_ENCRYPTION_KEY` rotation command |

## P2: later

| Item | Notes |
| --- | --- |
| Billing | Payment provider integration in test mode first, then invoices; plans and prices stay configuration |
| Enterprise | SSO (SAML/OIDC), SCIM provisioning, per-client taxonomies, white-label domains, data residency |
| Maritime | Licensed AIS and shipping datasets for vessel and port context (licensed data only) |
| Social media | Official accounts through permitted public endpoints only, under each platform's terms |
