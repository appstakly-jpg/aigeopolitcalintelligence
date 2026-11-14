# Deployment and operations

## Topology

`docker-compose.yml` runs a complete single-host deployment:

| Service | Image | Role |
| --- | --- | --- |
| `db` | `pgvector/pgvector:pg16` | PostgreSQL 16 with pgvector; data in the `db-data` volume; not published to the host |
| `migrate` | workers image | one-shot: `gccintel migrate && gccintel seed` (idempotent); everything else waits for it |
| `web` | `apps/web/Dockerfile` | Next.js standalone server on port 3000, non-root, health-checked |
| `worker` | `workers/Dockerfile` | processes the job queue; scale with `docker compose up -d --scale worker=3` |
| `scheduler` | workers image | enqueues recurring jobs; run exactly one |
| `demo-feeds`, `demo-setup` | workers image | profile `demo` only: SYNTHETIC feed server and demo organisation |
| `mailpit` | `axllent/mailpit` | profiles `demo` and `mail`: catches alert email (UI on 127.0.0.1:8025) |

```bash
./scripts/init-env.sh                 # .env with generated secrets (refuses to overwrite)
docker compose up -d --build
docker compose ps                     # web should become "healthy"
docker compose logs -f worker scheduler
```

Create the first organisation and its admin (prompts for a password of at least 12 characters, or
reads `ADMIN_PASSWORD`):

```bash
docker compose run --rm migrate gccintel tenant-create --slug acme --name "Acme" --plan team \
  --admin-email you@example.com --admin-name "Your Name"
```

Plans are `trial`, `team` and `enterprise` (`workers/gccintel/data/platform.json`); limits and feature
flags are data. Prices are deliberately empty: set them in the `plans` table when you have validated
pricing. `BILLING_MODE=test` lets admins switch plans without payment.

## Production checklist

1. **TLS.** Put the web service behind a TLS-terminating reverse proxy (Caddy, nginx, a cloud load
   balancer). Keep `SECURE_COOKIES=true`, set `APP_BASE_URL=https://your-host`, and forward
   `Host`/`X-Forwarded-Host`. The web container binds to `127.0.0.1` by default (`WEB_BIND`).
2. **Secrets.** Generate `SESSION_SECRET` (32+ random characters) and `APP_ENCRYPTION_KEY`
   (32 random bytes, base64) once and keep them in a secret manager. Rotating `SESSION_SECRET` signs
   everyone out; rotating `APP_ENCRYPTION_KEY` makes stored tenant secrets and MFA seeds unreadable
   (users re-enrol MFA; admins re-enter Slack/Telegram credentials).
3. **Database.** Prefer a managed PostgreSQL 16 (or Supabase) with `vector` and `citext` available.
   Point `DATABASE_URL` at it (Compose: replace the `db` service and the `x-db-url` anchor). The
   connecting role must own the schema and be able to create the `anon`, `authenticated` and
   `service_role` roles on first migration (Supabase already has them). Request-time queries run as
   `authenticated` under RLS; the owner connection is used only for the documented privileged paths.
4. **Backups.** See below. Test a restore before go-live.
5. **Demo off.** `DEMO_MODE=false` and `ALLOW_PRIVATE_NETWORK_FETCH=false` (the SSRF guard).
6. **Mail.** Real `SMTP_*` settings with STARTTLS; a sender domain with SPF/DKIM.
7. **Sources.** Review each catalogue source's licence and terms before approving it (Sources page).
   Set `HTTP_USER_AGENT` to identify your deployment with a contact URL.
8. **Models (optional).** `ANTHROPIC_API_KEY` (or OpenAI/Gemini), a realistic daily cap per
   organisation (`LLM_DAILY_BUDGET_USD`), and each organisation's monthly budget (Administration →
   Policies). `LLM_ANTHROPIC_BASE_URL` pins the endpoint; an ambient `ANTHROPIC_BASE_URL` is
   deliberately ignored.
9. **Review policy.** Keep "High-impact alerts require human review" on (Administration). Turning it off
   is audit-logged with a warning.
10. **Monitoring.** Probe `GET /api/health` (503 when the database is unreachable; watch
    `jobs_stale` and `jobs_queued`). Ship container logs (JSON from the workers) to your log store.

## Backups and restore

Logical backups with `pg_dump` are enough for this data volume:

```bash
# nightly, keep 14 days off-host
docker compose exec -T db pg_dump -U gccintel -d gccintel -Fc > backups/gccintel-$(date +%F).dump

# restore into an empty database
docker compose exec -T db createdb -U gccintel gccintel_restore
docker compose exec -T db pg_restore -U gccintel -d gccintel_restore --no-owner < backups/gccintel-2026-11-13.dump
```

Managed Postgres and Supabase provide point-in-time recovery; enable it. Back up `.env` (or your secret
store) separately: without `APP_ENCRYPTION_KEY`, encrypted tenant secrets in a backup are unusable,
which is intended.

## Upgrades

```bash
git pull
docker compose build
docker compose up -d          # migrate runs first; web, worker and scheduler restart after it succeeds
```

Migrations are ordered SQL files applied once each, inside a transaction, and recorded with a checksum.
A changed checksum for an applied migration stops the run: never edit an applied migration, add a new
one. Workers and the scheduler can be restarted at any time: workers re-queue jobs that have been
running for more than 30 minutes (a worker died mid-job), checked at start-up and every 5 minutes.
Failed jobs retry with exponential backoff (30 s doubling, at most an hour) until
`max_attempts`, then stay in the jobs table with status `dead` and their last error.

## Scaling

- **Web**: stateless; run several replicas behind the proxy. `DATABASE_POOL_MAX` per replica (default 10).
- **Worker**: add replicas; jobs are claimed with `FOR UPDATE SKIP LOCKED`. Fetching is polite per host
  inside one worker; with many workers keep `HTTP_PER_HOST_MIN_INTERVAL_SECONDS` conservative.
- **Scheduler**: one instance.
- **Database**: the largest tables are `articles`, `source_health` and `audit_log`; the retention job
  bounds them (see [architecture.md](architecture.md#data-model-main-tables)).

## Supabase

The schema is Supabase-compatible: migration `0000_supabase_compat.sql` creates `auth.uid()`,
`auth.jwt()` and the standard roles only where they do not already exist, and the RLS policies use
them. Two ways to run on Supabase:

- **Supabase as the database, built-in auth** (`AUTH_PROVIDER=local`): set `DATABASE_URL` to the
  Supabase connection string (session pooler or direct connection, as the owner role) and run
  `gccintel migrate`. This is the tested path for everything except the database host.
- **Supabase Auth** (`AUTH_PROVIDER=supabase`, `SUPABASE_JWT_SECRET`): the web app verifies the
  Supabase access token from the `sb-access-token` cookie and maps `sub` to `app_users.id`. You need
  to provide the Supabase sign-in flow that sets that cookie and create matching `app_users` and
  `memberships` rows. This mode is implemented but **has not been tested against a live Supabase
  project**.

## PDF export

HTML and DOCX exports always work. PDF uses headless Chromium in the worker:

```bash
WORKER_EXTRAS=llm,pdf WORKER_WITH_CHROMIUM=true docker compose build migrate
PDF_EXPORT_ENABLED=true docker compose up -d worker
```

The `pdf_export` plan feature also has to be on for the organisation.

## Building behind a TLS-intercepting proxy

If `npm ci` or `pip install` fail with certificate errors during `docker build`, pass the proxy's CA
bundle as a BuildKit secret; it is used for that step only and never stored in an image:

```bash
docker build --secret id=build_ca,src=/path/to/ca.pem -f workers/Dockerfile -t gccintel-workers:local .
docker build --secret id=build_ca,src=/path/to/ca.pem -t gccintel-web:local apps/web
docker compose up -d --no-build
```

## Map tiles

The map ships with an offline country basemap (Natural Earth via `world-atlas`, bundled). To use a
vector tile service you are licensed for, build the web image with
`NEXT_PUBLIC_MAP_STYLE_URL=https://…/style.json`; the Content-Security-Policy then allows HTTPS
connections for tiles.

## Command line (`gccintel`)

| Command | Purpose |
| --- | --- |
| `migrate [--create-db]` | apply migrations (optionally create the database) |
| `seed` | load reference data: taxonomy, gazetteer, actors, source catalogue, plans, risk methodology |
| `tenant-create`, `user-create` | create an organisation (with admin) or a user with a role |
| `worker [--once]`, `scheduler [--interval N] [--once]` | background processing |
| `run-once --tenant SLUG [--force]` | run the ingestion pipeline now for one organisation |
| `report --tenant SLUG --type daily_gcc [--country SA] [--out DIR] [--formats html,docx]` | generate a report from the shell |
| `risk --tenant SLUG`, `quality --tenant SLUG [--check-links]` | recompute risk; run the quality audit |
| `sources-verify --tenant SLUG [--source ID]` | robots, reachability and parsing checks for sources |
| `retention` | apply retention rules now |
| `eval [--out FILE]` | run the evaluation set (see `eval/README.md`) |
| `demo-setup`, `demo-feeds`, `demo-advance`, `demo-run` | synthetic demo (requires `DEMO_MODE=true`) |
| `smtp-sink --port 1025 --dir DIR` | development SMTP server that writes `.eml` files |

## Troubleshooting

- **Sign-in loops back to the login page**: you are on plain HTTP with `SECURE_COOKIES=true`. Use HTTPS
  (or set `SECURE_COOKIES=false` on a private test machine only).
- **AI analyst says "cross-origin request refused"**: the proxy does not forward the original `Host`;
  forward it, or set `APP_BASE_URL` to the public URL.
- **Sources stay "failing"**: check the Sources page health log. `robots_disallowed` and HTTP 401/403/429
  are respected, not retried aggressively; private addresses are refused unless
  `ALLOW_PRIVATE_NETWORK_FETCH=true` (demo only).
- **`migrations directory not found`**: set `MIGRATIONS_DIR` when running the workers outside the
  repository or the Docker image.
