# Security and safety

This is an unclassified, lawful, public-source monitoring product. It is not a surveillance or
targeting system: it does not ingest private messages, private accounts or personal location data,
and it declines requests to locate, profile or track individuals.

## Controls

### Identity and sessions

- Email and password sign-in. Passwords are hashed with scrypt (N=16384, r=8, p=1, random salt); at
  least 12 characters.
- Sign-in is rate limited (50 attempts per IP and 10 per email address per 15 minutes) and an account
  locks for 15 minutes after 5 consecutive failures. Unknown addresses take the same hashing time.
- **MFA**: TOTP (RFC 6238) that any user can enable under Settings. The seed is stored encrypted;
  verification is rate limited and failures are audited. Turning MFA off needs a current code.
- Sessions are signed (HS256) HTTP-only cookies, `SameSite=Lax`, `Secure` when `SECURE_COOKIES=true`,
  valid for 12 hours. Every request re-reads the user and membership, so disabling a user, suspending an
  organisation or changing a role takes effect on the next request.
- Optional Supabase Auth (`AUTH_PROVIDER=supabase`) verifies Supabase access tokens instead (see
  [deployment.md](deployment.md#supabase)).

### Authorisation and tenant isolation

- Four roles: **admin**, **analyst**, **reviewer**, **client** ([architecture.md](architecture.md#multi-tenancy-and-access-control)).
  Server actions check the role before doing anything.
- **Row-level security** on every tenant table is the boundary: the web app runs each request as the
  Postgres `authenticated` role with the user's claims, so a bug in a page cannot read another
  organisation's rows. Policies check membership, not just the tenant claim.
- Database triggers enforce the decisions that matter: only reviewers and admins can approve, reject or
  confirm incidents and publish reports, and a report whose citation check failed cannot be published.
- Workers run as trusted system processes and check that every object in a job belongs to the job's
  organisation.
- Tests: `workers/tests/test_security.py` covers cross-tenant reads and writes, client restrictions,
  hidden secret columns, publication rights, citation tampering, reference numbering, alert-rule
  privacy, plan-gated branding, redirect SSRF and LLM budgets.

### Secrets

- All configuration secrets are environment variables (`.env.example` documents each). `.env` is
  created with mode 600 and is ignored by git and Docker builds.
- Organisation secrets (Slack and Telegram tokens, webhook URLs) and MFA seeds are encrypted with
  AES-256-GCM under `APP_ENCRYPTION_KEY`, with the organisation or user bound in as associated data,
  so a ciphertext copied to another row does not decrypt. They are never sent back to the browser;
  delivery logs store masked destinations.
- Logs never include secrets or passwords.

### Web application

- Input validation with zod on every server action and API route; all SQL is parameterised.
- State-changing API routes refuse cross-site requests (Origin check), on top of SameSite cookies and
  the framework's server-action origin check.
- Headers: a Content-Security-Policy that limits scripts, connections, forms and frames to the app's
  own origin and forbids plugins and framing by other sites, plus `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy` and `Permissions-Policy`.
- Rate limits: incidents API 120 requests per minute per user, AI analyst 60 questions per hour per
  user, password and MFA changes 5 per 15 minutes.
- Report downloads are served as attachments with a sandboxing CSP. CSV exports neutralise cells that a
  spreadsheet would read as formulas.
- Report HTML is rendered with autoescaping; article text is only ever shown as text.

### Retrieval

- Only sources an admin approved are fetched, and robots.txt is honoured (RFC 9309), with per-host
  throttling and Crawl-delay. 401, 402, 403 and 429 responses are recorded, not worked around: no
  paywall, login or rate-limit bypass, and no assumption that a platform offers an API.
- SSRF guard: every request, including each redirect hop and robots.txt, is refused when the host
  resolves to a private, loopback, link-local or reserved address (`ALLOW_PRIVATE_NETWORK_FETCH` exists
  only for the local demo). Responses are size-capped and time-limited.
- Stored text is limited to what each source's usage policy allows (headline, link and a short excerpt
  for news media until a licence is confirmed).

### Untrusted content and models

- Retrieved text is data. Instruction-like sentences (English and Arabic) are flagged, removed before
  extraction and route the incident to review. Text sent to a model is fenced as untrusted data that
  cannot close its own fence.
- Severity, verification, review routing and alert impact are computed by rules, so a compromised or
  manipulated model cannot escalate an incident or send a high-impact alert (a test uses a deliberately
  "compromised" mock model to prove this).
- Model output that cites non-existent evidence or adds figures is rejected.
- Model calls are capped per run and per day, and by the organisation's monthly budget; every call is
  metered in `llm_usage`. Server-side fallbacks are enabled for Claude; a final refusal is recorded
  and the user gets the extractive answer with the reason.

### Audit and retention

- `audit_log` records sign-ins, failed MFA, reviews, edits, publications, alert decisions, policy and
  plan changes, branding and source approvals. The application role can append and admins can read; it
  cannot update or delete entries. Entries are kept 730 days by default.
- Retention rules clear stored excerpts and chat history after 365 days (configurable).
- Backups: see [deployment.md](deployment.md#backups-and-restore).

### Containers

- Images run as non-root users; the web container has a health check; the database is not published to
  the host; the web port binds to 127.0.0.1 unless `WEB_BIND` says otherwise.

## Known gaps

| Gap | Mitigation now | Plan |
| --- | --- | --- |
| MFA is optional; it cannot yet be required per organisation or role | Ask admins and reviewers to enable it | Enforce per role ([roadmap](roadmap.md)) |
| Sessions are stateless; a stolen cookie works until it expires (12 hours) | Disable the user, or rotate `SESSION_SECRET` to sign everyone out | Server-side session list with revoke |
| No `Strict-Transport-Security` header from the app | Set HSTS at the TLS proxy | Add when TLS is terminated in-app |
| The CSP allows inline scripts (the framework needs them without nonces) | React escapes all output and no stored HTML is rendered | Nonce-based CSP |
| The SSRF check resolves DNS before connecting (DNS rebinding window) | Egress firewall: deny private ranges and cloud metadata from the worker network | Pin the resolved address in the transport |
| `APP_ENCRYPTION_KEY` cannot be rotated in place | Keep it in a secret manager | Re-encryption command |
| Supabase Auth mode is untested against a live project | Use built-in auth | End-to-end test |
| No long-lived API keys for server-to-server use | Use a dedicated user session | Scoped API keys |
| Source licences are not reviewed | News sources are limited to headline, link and short excerpt | Licence review before production ([sources.md](sources.md)) |

## Reporting a vulnerability

Please report security issues privately to the repository owner rather than in a public issue.
