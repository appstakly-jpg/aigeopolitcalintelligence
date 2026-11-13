# GCC Geopolitical Intelligence

A multi-tenant platform that automates the routine work of a regional security analyst for the six
GCC states (Saudi Arabia, UAE, Qatar, Kuwait, Bahrain, Oman), with Iran, Iraq, Yemen, Israel, the Red
Sea and the Horn of Africa covered where they affect GCC security, energy, shipping, diplomacy or
business continuity.

It collects permitted public sources, turns reports into evidence-linked and versioned incidents,
separates source count from independent corroboration, scores risk with a documented methodology,
writes cited briefs, and alerts subscribers, with human review wherever a decision is consequential.

This is an unclassified, lawful, public-source intelligence product. It is not a surveillance or
targeting system: it does not profile, locate or track individuals, and the AI analyst refuses
requests to do so.

## Status at a glance

| Area | State |
| --- | --- |
| Pipeline: scheduling, polite fetching, canonicalisation, dedup, near-duplicate and syndication detection, language detection, extraction, corroboration, versioned incidents, corrections and retractions | Working, tested (55 worker tests), demonstrated end to end on synthetic feeds |
| Web app: 13 sections plus review queue, global search, AI analyst, incident API, map, EN/AR (RTL), dark/light | Working, tested (33 unit tests, browser smoke test for 4 roles, browser workflow test) |
| Reports: 7 types, citation integrity enforced in the database, draft/review/publish, versions, HTML/DOCX export, branding | Working. PDF export needs Chromium in the worker image (optional build) |
| Alerts: rules, dedup, throttling, human review for high impact, updates/corrections/retractions, delivery logs and retries | Working with email (SMTP). Slack, Telegram and webhook adapters are implemented and tested with mocks only |
| AI models (Claude, OpenAI, Gemini) for extraction, translation, brief narrative and the AI analyst | Implemented behind budgets and validation, **not exercised against live APIs here** (no keys). Without a key everything runs deterministically and says so |
| Live sources | 21 catalogued public sources; **12 need licence review** before use; live fetching was not possible from the build sandbox (news hosts blocked), so only synthetic feeds were run |
| SaaS: organisations, seats, plans, feature flags, usage limits, watchlists, test-mode billing | Working; prices are deliberately unset (configuration, not validated pricing) |
| Docker Compose deployment with a demo profile | Working: images build, stack starts healthy, browser tests pass against it |

Details: [what is implemented, stubbed or needs credentials](#implemented-stubbed-needs-credentials).

## Quick start: synthetic demo in Docker

```bash
./scripts/init-env.sh --demo                 # writes .env with generated secrets and prints the demo password
docker compose --profile demo up -d --build
```

Open http://localhost:3000 and sign in as `admin@demo.example`, `analyst@demo.example`,
`reviewer@demo.example` or `client@demo.example`. Alert emails land in Mailpit at http://localhost:8025.

Everything in the demo organisation is **synthetic**, labelled as such in the UI, in reports and in
emails. Within a minute the scheduler ingests the first stage of the demo feeds: ten items in English
and Arabic become six incidents (a sports story is filtered out as irrelevant), including a tanker
explosion near Bab el-Mandeb reported by several outlets, of which only the independent origins count
as corroboration, and an unverified social-media claim of a drone strike on Ras Tanura. About ten
minutes later (or at once with `docker compose exec demo-feeds gccintel demo-advance`) the second stage
arrives: an official denial that turns the drone claim into "disputed" while keeping both sides
visible, a publisher correction that moves the Kuwait outage to Ahmadi and triggers correction alerts,
and follow-ups, one in Arabic, that update earlier incidents. The scheduler also writes a cited daily
brief that waits for a reviewer, and high-impact alerts wait for approval before anything is sent.

## Production-style start

```bash
./scripts/init-env.sh                        # production defaults: demo off, secure cookies
$EDITOR .env                                 # APP_BASE_URL (https), SMTP_*, optional ANTHROPIC_API_KEY
docker compose up -d --build
docker compose run --rm migrate gccintel tenant-create --slug acme --name "Acme" --plan team \
  --admin-email you@example.com              # prompts for the admin password
```

Then, as the admin: **Sources** → add sources from the catalogue, complete the licence review, approve
them; the scheduler fetches them on their intervals. Put the app behind TLS before real use. See
[docs/deployment.md](docs/deployment.md).

## Local development without Docker

Requirements: Node 22, Python 3.12+, PostgreSQL 16 with the `vector` and `citext` extensions.

```bash
# workers
cd workers && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,llm]"
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/gccintel_dev \
       DEMO_MODE=true ALLOW_PRIVATE_NETWORK_FETCH=true DEMO_PASSWORD='choose-a-password' \
       SESSION_SECRET="$(openssl rand -hex 32)" APP_ENCRYPTION_KEY="$(openssl rand -base64 32)" \
       SMTP_HOST=127.0.0.1 SMTP_PORT=1025 SMTP_STARTTLS=false
gccintel migrate --create-db
gccintel demo-setup                          # synthetic demo organisation and users
gccintel demo-run                            # scripted run: feeds → incidents → corroboration → correction → brief → alerts (JSON summary)
gccintel smtp-sink --port 1025 --dir ../var/mail &   # optional: write alert emails to files
gccintel demo-feeds --stage 2 &              # keep the demo feeds available for the scheduler
gccintel worker & gccintel scheduler &

# web app (same environment variables)
cd ../apps/web && npm ci && npm run dev      # http://localhost:3000
```

## Tests and evaluation

| Command | What it covers |
| --- | --- |
| `cd workers && pytest` | 55 tests against a throwaway Postgres database: multilingual parsing, dedup and syndication, independent corroboration, time zones, corrections and retractions, broken links, prompt injection (including a compromised model), tenant isolation under RLS, role restrictions, notification dedup and throttling, delivery retries, report citation integrity, analyst corrections, branding, redirect SSRF, LLM budgets |
| `cd apps/web && npm test && npm run lint && npm run typecheck && npm run build` | 33 unit tests (crypto shared with Python, TOTP vectors, citation checking, retrieval safety, targeting guard, override bookkeeping, same-origin checks, chat export), lint, types, production build |
| `node apps/web/scripts/smoke.mjs` | Browser: every section for all four roles, role refusals, unreviewed incidents marked for clients, grounded chat with evidence filters and export, targeting refusal, no access to other users' conversations, anonymous API refusal |
| `node apps/web/scripts/workflow-e2e.mjs` | Browser: analyst correction → worker re-derivation → high-impact correction alert held → reviewer approval → email → brief publication with change acknowledgement → override removal |
| `gccintel eval --out var/eval.json` | Precision, recall, false alert rate, citation correctness, latency and cost on the evaluation set. See [eval/README.md](eval/README.md), including the failing case |

Both browser scripts need `DEMO_PASSWORD` and a running instance with the demo loaded
(`BASE_URL`, default http://localhost:3000); the workflow script changes data, so run it on a fresh demo.

## Implemented, stubbed, needs credentials

**Implemented and tested**
- Tenancy and security: Postgres row-level security on every tenant table, roles admin / analyst /
  reviewer / client, scrypt passwords, TOTP MFA, lockout and rate limits, AES-256-GCM encrypted tenant
  secrets, append-only audit log, input validation, CSP and security headers. See [docs/security.md](docs/security.md).
- Agents A–J as separate modules with retry limits, cost limits and metering (see
  [docs/architecture.md](docs/architecture.md)). Severity, verification, review requirements and alert
  impact are always computed by deterministic code, never taken from a model.
- Geography: 129-entity gazetteer (countries, cities, ports, facilities, maritime zones and a
  corridor) with precision levels and geolocation confidence. Coordinates come only from the gazetteer
  or from coordinates a source states; nothing is invented, and coarse placements are drawn as areas.
- Risk: 9 dimensions × 6 GCC states, versioned methodology, stored factor contributions, separate
  severity / exposure / vulnerability / uncertainty, `insufficient_data` instead of zero, analyst
  adjustments with rationale. Scores describe recent observed reporting, not forecasts. See
  [docs/methodology.md](docs/methodology.md).
- AI analyst: answers only from retrieved stored evidence, cites every factual sentence, rejects
  citations that point at nothing and figures absent from the evidence, keeps contradictions visible,
  and falls back to an extractive answer when no model is configured, the budget is used up or a draft
  fails the check. Users can restrict the evidence by verification status and source grade, and export
  a conversation with its citations as Markdown.
- Cost limits: per-run model call caps, a daily cap per organisation (`LLM_DAILY_BUDGET_USD`) and a monthly budget per
  organisation (0 turns model use off); every call is metered.

**Stubbed or partial**
- Live LLM calls (Anthropic, OpenAI, Gemini) are implemented but untested against the real services in
  this environment. Claude calls opt in to the API's server-side refusal fallbacks
  (`fallbacks: "default"`); a response that still ends in refusal is treated as no answer and the
  deterministic path is used.
- Slack, Telegram and webhook delivery: implemented, tested with mock channels only.
- Supabase Auth mode (`AUTH_PROVIDER=supabase`): JWT verification and RLS claims implemented, not tested
  against a live Supabase project. The schema is Supabase-compatible (`auth.uid()`, `auth.jwt()`, roles).
- Trend and forecasting module (rolling baselines, seasonality, scenarios), social-media adapters,
  semantic (pgvector) retrieval, API keys and real billing are not built yet. They are on the
  [roadmap](docs/roadmap.md), as the specification's MVP scope deferred them.
- Branded reports apply the header and accent colour; logo upload is not implemented.
- Risk assessments are stored per day with their methodology version, but there is no history chart
  yet. Country focus, priority and feed time zone of custom sources are not editable in the app.
- UI components are hand-written shadcn-style primitives (same tokens and conventions), not generated
  with the shadcn CLI.

**Needs credentials**
- `ANTHROPIC_API_KEY` (or OpenAI / Gemini) for model-assisted extraction, translation, brief narrative
  and AI analyst answers. Without it: rule-based extraction (English and Arabic), untranslated text
  labelled as such, template briefs, extractive answers.
- SMTP settings for email alerts; a Slack webhook or Telegram bot token per organisation (Settings).
- Licensed news APIs, if you add adapters for them.

## Configuration

Every variable is documented in [.env.example](.env.example). The required ones are `POSTGRES_PASSWORD`
(Docker) or `DATABASE_URL`, `SESSION_SECRET` and `APP_ENCRYPTION_KEY`; `scripts/init-env.sh` generates
them. Without optional credentials, integrations report "not configured"; nothing is faked.

## Safety, ethics and source rights

- Public sources only. Robots.txt, rate limits, paywalls and logins are respected; 401/402/403/429
  responses are logged, never worked around. No private messages, private accounts or personal
  location data are ingested. No platform is assumed to offer an API.
- Only the excerpt each source's usage policy permits is stored, with publisher, URL, timestamps,
  language and licence metadata. 12 of the 21 catalogued sources are marked `review_required` until an
  admin records a licence decision. No ACLED data is used and no affiliation is claimed.
- Retrieved text is untrusted data: prompt-injection text is flagged, stripped before extraction and
  fenced in prompts, and model output is validated against the source text.
- Official statements are attributable claims, not facts. Contradictory reporting stays visible.
- High-impact alerts are never sent without human review unless an admin explicitly changes that
  policy (the change is audit-logged with a warning).
- Demo data exists only in a clearly labelled demo organisation and is marked SYNTHETIC everywhere.

## Repository layout

```
apps/web/            Next.js 16 app (App Router, TypeScript, Tailwind 4, MapLibre)
  src/app/(app)/     the 13 sections plus reviews and search; server actions next to each page
  src/app/api/       incident API (JSON/CSV), AI analyst, health
  src/lib/           auth, RLS database access, LLM client, retrieval, citation checks, i18n
  scripts/           smoke and workflow browser tests, basemap and MapLibre worker helpers
workers/             Python package `gccintel` (CLI: gccintel --help)
  gccintel/agents/   agents A–J and the incident service
  gccintel/adapters/ polite HTTP client, RSS/Atom and GDELT adapters
  gccintel/llm/      provider abstraction (Anthropic, OpenAI, Gemini, mock), budgets, pricing
  gccintel/data/     taxonomy, gazetteer, actors, source catalogue, plans, methodology, demo and eval fixtures
  tests/             pytest suite
db/migrations/       ordered SQL migrations (schema, RLS, guards)
docs/                architecture, API, deployment, methodology, security, sources, roadmap
eval/                evaluation method and latest results
scripts/init-env.sh  .env generator
```

## Documentation

- [Architecture and diagram](docs/architecture.md)
- [API](docs/api.md)
- [Deployment and operations](docs/deployment.md)
- [Methodology: verification, severity, risk, alerts, citations](docs/methodology.md)
- [Security and governance](docs/security.md)
- [Sources and licensing](docs/sources.md)
- [Evaluation](eval/README.md)
- [Prioritised roadmap](docs/roadmap.md)
