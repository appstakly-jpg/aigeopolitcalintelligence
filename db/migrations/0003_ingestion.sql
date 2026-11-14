-- 0003_ingestion.sql
-- Tenant source registry, source health, retrieved articles (evidence), claims and
-- the claim/syndication provenance graph, plus the Postgres-backed job queue.

create table public.sources (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  catalog_key text references public.source_catalog(key) on delete set null,
  name text not null check (length(name) between 2 and 160),
  publisher text not null,
  adapter text not null check (adapter in ('rss', 'gdelt', 'html_list', 'json_api', 'manual')),
  url text not null check (url ~* '^https?://'),
  homepage_url text,
  language text not null default 'en' check (language ~ '^[a-z]{2}$'),
  country_focus text[] not null default '{}',
  source_type text not null check (source_type in ('news_media', 'wire_agency', 'state_media', 'government',
                                                   'intl_org', 'think_tank', 'ngo', 'social_media',
                                                   'aggregator', 'user_feed', 'synthetic')),
  ownership text,
  -- Admiralty-style source reliability: A completely reliable .. E unreliable, F cannot be judged
  reliability char(1) not null default 'F' check (reliability in ('A', 'B', 'C', 'D', 'E', 'F')),
  reliability_rationale text,
  -- Sources sharing editorial origin (same network/owner) are NOT independent corroboration
  origin_group text,
  license_type text not null default 'review_required'
    check (license_type in ('public_domain', 'open_license', 'attribution', 'licensed',
                            'review_required', 'prohibited', 'synthetic')),
  license_notes text,
  terms_url text,
  usage_policy text not null default 'headline_link_excerpt'
    check (usage_policy in ('metadata_only', 'headline_link_excerpt', 'full_text')),
  max_excerpt_chars integer not null default 320 check (max_excerpt_chars between 0 and 2000),
  fetch_interval_minutes integer not null default 30 check (fetch_interval_minutes between 5 and 1440),
  priority smallint not null default 3 check (priority between 1 and 5),
  status text not null default 'proposed' check (status in ('proposed', 'approved', 'rejected', 'retired')),
  enabled boolean not null default false,
  is_demo boolean not null default false,
  verified_at timestamptz,
  verification_note text,
  config jsonb not null default '{}'::jsonb,         -- adapter options (query, selectors ...); never secrets
  next_fetch_at timestamptz,
  etag text,
  last_modified text,
  last_success_at timestamptz,
  last_error_at timestamptz,
  last_error text,
  consecutive_failures integer not null default 0,
  proposed_by_agent text,
  created_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, url),
  constraint enabled_requires_approval check (not enabled or status = 'approved'),
  constraint prohibited_never_enabled check (not (enabled and license_type = 'prohibited'))
);
create index sources_due_idx on public.sources(next_fetch_at) where enabled;
create trigger sources_touch before update on public.sources
for each row execute function app.touch_updated_at();

create table public.source_health (
  id bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  source_id uuid not null references public.sources(id) on delete cascade,
  checked_at timestamptz not null default now(),
  ok boolean not null,
  http_status integer,
  robots_allowed boolean,
  items_found integer,
  items_new integer,
  latency_ms integer,
  error text
);
create index source_health_source_time_idx on public.source_health(source_id, checked_at desc);

-- ---------------------------------------------------------------------------
-- Articles: one row per canonical URL per tenant. Stores provenance metadata and
-- only the excerpt the source's usage policy permits (no full-text copies).
-- ---------------------------------------------------------------------------
create table public.articles (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  source_id uuid not null references public.sources(id) on delete cascade,
  url text not null,
  canonical_url text not null,
  url_hash text not null,
  title text not null,
  title_en text,
  excerpt text,
  excerpt_en text,
  language text not null default 'und',
  language_confidence real,
  translated_by text,
  published_at timestamptz,
  published_at_source text,                 -- raw value as given by the feed, for audit
  retrieved_at timestamptz not null default now(),
  author text,
  content_hash text not null,
  simhash bigint,
  syndicated_from_article_id uuid references public.articles(id) on delete set null,
  syndication_similarity real,
  attributed_origin text,                   -- e.g. 'reuters', 'afp', 'spa' when the copy names a wire
  is_original boolean not null default true,
  stance text not null default 'report'
    check (stance in ('report', 'denial', 'correction', 'retraction', 'update', 'official_statement')),
  license_type text not null,
  usage_policy text not null,
  status text not null default 'new'
    check (status in ('new', 'processed', 'duplicate', 'irrelevant', 'error', 'quarantined')),
  relevance real,
  processing_error text,
  injection_flags text[] not null default '{}',
  link_status text not null default 'unchecked' check (link_status in ('unchecked', 'ok', 'broken', 'redirected', 'skipped')),
  link_checked_at timestamptz,
  is_demo boolean not null default false,
  meta jsonb not null default '{}'::jsonb,
  search tsvector generated always as (
    to_tsvector(case when language = 'ar' then 'arabic'::regconfig else 'english'::regconfig end,
                coalesce(title, '') || ' ' || coalesce(excerpt, ''))
    || to_tsvector('english'::regconfig, coalesce(title_en, '') || ' ' || coalesce(excerpt_en, ''))
  ) stored,
  unique (tenant_id, url_hash)
);
create index articles_tenant_published_idx on public.articles(tenant_id, published_at desc);
create index articles_tenant_retrieved_idx on public.articles(tenant_id, retrieved_at desc);
create index articles_content_hash_idx on public.articles(tenant_id, content_hash);
create index articles_status_idx on public.articles(tenant_id, status);
create index articles_search_idx on public.articles using gin(search);
create index articles_source_idx on public.articles(source_id);

-- ---------------------------------------------------------------------------
-- Claims: atomic, attributable statements extracted from an article.
-- An official statement is an attributable claim, not objective truth.
-- ---------------------------------------------------------------------------
create table public.claims (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  article_id uuid not null references public.articles(id) on delete cascade,
  incident_id uuid,                          -- FK added in 0004
  claim_type text not null check (claim_type in ('event_occurrence', 'casualties', 'damage', 'attribution',
                                                 'denial', 'official_statement', 'impact', 'correction', 'other')),
  text text not null check (length(text) <= 600),
  text_en text,
  attributed_to text,
  attributed_actor_id text references public.actors(id) on delete set null,
  is_official boolean not null default false,
  stance text not null default 'asserts' check (stance in ('asserts', 'denies', 'disputes', 'corrects', 'retracts')),
  numeric_value numeric,
  numeric_unit text check (numeric_unit is null or numeric_unit in ('killed', 'injured', 'missing', 'detained', 'vessels', 'other')),
  confidence real check (confidence is null or confidence between 0 and 1),
  extracted_by text not null,
  created_at timestamptz not null default now()
);
create index claims_article_idx on public.claims(article_id);
create index claims_incident_idx on public.claims(incident_id);

-- Provenance graph edges between claims / articles / external origins
create table public.claim_provenance (
  id bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  claim_id uuid references public.claims(id) on delete cascade,
  article_id uuid references public.articles(id) on delete cascade,
  relation text not null check (relation in ('reported_by', 'copied_from', 'attributed_to', 'cites',
                                             'contradicts', 'corrects', 'retracts')),
  target_article_id uuid references public.articles(id) on delete cascade,
  target_claim_id uuid references public.claims(id) on delete cascade,
  target_label text,
  weight real,
  created_at timestamptz not null default now(),
  check (claim_id is not null or article_id is not null)
);
create index claim_provenance_claim_idx on public.claim_provenance(claim_id);
create index claim_provenance_article_idx on public.claim_provenance(article_id);

-- ---------------------------------------------------------------------------
-- Job queue (FOR UPDATE SKIP LOCKED). Retries with backoff; dead-letter after max_attempts.
-- ---------------------------------------------------------------------------
create table public.jobs (
  id bigint generated always as identity primary key,
  tenant_id uuid references public.tenants(id) on delete cascade,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{2,40}$'),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed', 'dead', 'cancelled')),
  priority smallint not null default 5 check (priority between 1 and 9),
  run_after timestamptz not null default now(),
  attempts integer not null default 0,
  max_attempts integer not null default 5 check (max_attempts between 1 and 20),
  dedupe_key text,
  locked_by text,
  locked_at timestamptz,
  last_error text,
  result jsonb,
  requested_by uuid,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
create index jobs_claim_idx on public.jobs(priority, run_after) where status = 'queued';
create unique index jobs_dedupe_active_idx on public.jobs(dedupe_key) where status in ('queued', 'running') and dedupe_key is not null;
create index jobs_tenant_idx on public.jobs(tenant_id, created_at desc);
