-- 0005_analysis.sql
-- Risk methodology + assessments, reports (versioned), alerting, watchlists,
-- analyst chat, and LLM usage/cost accounting.

-- ---------------------------------------------------------------------------
-- Risk methodology: versioned, documented parameters. tenant_id NULL = platform default.
-- Assessments always reference the exact methodology version used.
-- ---------------------------------------------------------------------------
create table public.risk_methodologies (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references public.tenants(id) on delete cascade,
  version text not null check (version ~ '^[0-9]+\.[0-9]+(\.[0-9]+)?$'),
  name text not null,
  description_md text not null,
  params jsonb not null,
  is_active boolean not null default false,
  created_by uuid,
  created_at timestamptz not null default now(),
  unique nulls not distinct (tenant_id, version)
);

create table public.risk_assessments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  methodology_id uuid not null references public.risk_methodologies(id),
  country_code char(2) not null,
  dimension text not null,
  window_start timestamptz not null,
  window_end timestamptz not null,
  computed_at timestamptz not null default now(),
  as_of_date date not null,
  score numeric(4, 2) check (score is null or score between 0 and 5),
  band text not null check (band in ('insufficient_data', 'low', 'moderate', 'elevated', 'high', 'severe')),
  confidence text not null check (confidence in ('low', 'moderate', 'high')),
  forecast_uncertainty text not null default 'high' check (forecast_uncertainty in ('low', 'moderate', 'high')),
  components jsonb not null,             -- observed severity, exposure, vulnerability, k, coverage ...
  contributions jsonb not null default '[]'::jsonb,  -- per-incident contributions (evidence links)
  evidence_incident_ids uuid[] not null default '{}',
  incident_count integer not null default 0,
  analyst_band text check (analyst_band is null or analyst_band in ('low', 'moderate', 'elevated', 'high', 'severe')),
  analyst_rationale text,
  analyst_id uuid references public.app_users(id) on delete set null,
  status text not null default 'computed' check (status in ('computed', 'analyst_adjusted', 'reviewed')),
  unique (tenant_id, methodology_id, country_code, dimension, as_of_date)
);
create index risk_assessments_lookup_idx on public.risk_assessments(tenant_id, country_code, dimension, as_of_date desc);

-- ---------------------------------------------------------------------------
-- Reports
-- ---------------------------------------------------------------------------
create table public.reports (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  ref text not null,
  report_type text not null check (report_type in ('breaking', 'daily_gcc', 'country_daily', 'weekly_outlook',
                                                   'monthly_assessment', 'maritime_energy', 'custom')),
  title text not null,
  country_code char(2),
  period_start timestamptz not null,
  period_end timestamptz not null,
  status text not null default 'draft' check (status in ('draft', 'in_review', 'published', 'archived')),
  current_version integer not null default 0,
  is_demo boolean not null default false,
  created_by uuid,
  created_by_agent text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  published_at timestamptz,
  published_by uuid,
  unique (tenant_id, ref),
  check (period_end > period_start)
);
create index reports_tenant_time_idx on public.reports(tenant_id, created_at desc);
create trigger reports_touch before update on public.reports
for each row execute function app.touch_updated_at();

create table public.report_versions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  report_id uuid not null references public.reports(id) on delete cascade,
  version integer not null,
  content jsonb not null,                -- structured sections with citation numbers
  html text not null,
  citations jsonb not null,              -- [{n, article_id, url, publisher, title, published_at, retrieved_at}]
  citation_check jsonb not null,         -- integrity verification result
  coverage jsonb not null default '{}'::jsonb,
  generator text not null,
  change_note text,
  created_by uuid,
  created_at timestamptz not null default now(),
  unique (report_id, version)
);

create table public.report_exports (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  report_version_id uuid not null references public.report_versions(id) on delete cascade,
  format text not null check (format in ('html', 'pdf', 'docx')),
  content_type text not null,
  bytes bytea not null,
  created_at timestamptz not null default now(),
  unique (report_version_id, format)
);

-- ---------------------------------------------------------------------------
-- Alerting
-- ---------------------------------------------------------------------------
create table public.alert_rules (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null references public.app_users(id) on delete cascade,
  name text not null check (length(name) between 2 and 120),
  enabled boolean not null default true,
  countries text[] not null default '{}',
  event_types text[] not null default '{}',
  geo_entity_ids text[] not null default '{}',
  min_severity smallint not null default 1 check (min_severity between 1 and 5),
  verification_statuses text[] not null default '{}',
  keywords text[] not null default '{}',
  sectors text[] not null default '{}',
  channels text[] not null default '{email}' check (channels <@ array['email', 'slack', 'telegram', 'webhook']::text[]),
  destination jsonb not null default '{}'::jsonb,     -- e.g. {"email": "ops@example.com"}; secrets stay in tenant_secrets
  throttle_minutes integer not null default 60 check (throttle_minutes between 0 and 10080),
  notify_updates boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index alert_rules_tenant_idx on public.alert_rules(tenant_id) where enabled;
create trigger alert_rules_touch before update on public.alert_rules
for each row execute function app.touch_updated_at();

create table public.alerts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  rule_id uuid not null references public.alert_rules(id) on delete cascade,
  incident_id uuid not null references public.incidents(id) on delete cascade,
  incident_version integer not null,
  kind text not null check (kind in ('new', 'update', 'correction', 'retraction')),
  impact text not null check (impact in ('standard', 'high')),
  status text not null check (status in ('pending_review', 'approved', 'sent', 'partially_sent', 'failed',
                                         'suppressed', 'rejected')),
  status_reason text,
  dedupe_key text not null,
  title text not null,
  body text not null,
  reviewed_by uuid references public.app_users(id) on delete set null,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  unique (tenant_id, dedupe_key)
);
create index alerts_tenant_time_idx on public.alerts(tenant_id, created_at desc);
create index alerts_rule_incident_idx on public.alerts(rule_id, incident_id, created_at desc);

create table public.alert_deliveries (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  alert_id uuid not null references public.alerts(id) on delete cascade,
  channel text not null check (channel in ('email', 'slack', 'telegram', 'webhook', 'mock')),
  destination_masked text,
  status text not null default 'queued' check (status in ('queued', 'sent', 'failed', 'retrying', 'skipped')),
  attempts integer not null default 0,
  last_error text,
  provider_message_id text,
  next_attempt_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique (alert_id, channel)
);

-- Client-specific watchlists (countries, places, keywords, sectors, exposure weights)
create table public.watchlists (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid references public.app_users(id) on delete cascade,
  name text not null,
  countries text[] not null default '{}',
  geo_entity_ids text[] not null default '{}',
  keywords text[] not null default '{}',
  sectors text[] not null default '{}',
  exposure jsonb not null default '{}'::jsonb,   -- {"SA": 1.2, "AE": 1.0}: client exposure multipliers used by risk
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- AI analyst chat (grounded in stored evidence)
-- ---------------------------------------------------------------------------
create table public.chat_sessions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null references public.app_users(id) on delete cascade,
  title text not null default 'New conversation',
  created_at timestamptz not null default now()
);

create table public.chat_messages (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  citations jsonb not null default '[]'::jsonb,
  retrieval jsonb not null default '{}'::jsonb,
  grounding_check jsonb not null default '{}'::jsonb,
  generator text,
  created_at timestamptz not null default now()
);
create index chat_messages_session_idx on public.chat_messages(session_id, created_at);

-- ---------------------------------------------------------------------------
-- LLM usage / cost accounting (per agent, per tenant)
-- ---------------------------------------------------------------------------
create table public.llm_usage (
  id bigint generated always as identity primary key,
  tenant_id uuid references public.tenants(id) on delete cascade,
  agent text not null,
  provider text not null,
  model text not null,
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  cache_read_tokens integer not null default 0,
  cache_write_tokens integer not null default 0,
  cost_usd numeric(12, 6) not null default 0,
  latency_ms integer,
  success boolean not null,
  error text,
  request_id text,
  created_at timestamptz not null default now()
);
create index llm_usage_tenant_time_idx on public.llm_usage(tenant_id, created_at desc);
