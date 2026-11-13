-- 0004_incidents.sql
-- Canonical incidents (versioned), evidence links, extraction candidates and
-- analyst review queue.

create table public.incidents (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  ref text not null,
  title text not null check (length(title) between 3 and 300),
  summary text check (summary is null or length(summary) <= 4000),
  status text not null default 'active' check (status in ('active', 'closed', 'rejected', 'merged')),
  verification_status text not null default 'unverified'
    check (verification_status in ('confirmed', 'corroborated', 'single_source', 'disputed', 'unverified')),
  -- Admiralty information credibility 1 (confirmed) .. 6 (cannot be judged), derived from verification
  credibility smallint check (credibility between 1 and 6),
  review_status text not null default 'not_required'
    check (review_status in ('not_required', 'pending', 'approved', 'rejected', 'changes_requested')),
  review_reason text,
  severity smallint not null default 1 check (severity between 1 and 5),
  severity_rationale text,
  confidence text not null default 'low' check (confidence in ('low', 'moderate', 'high')),
  primary_category text not null,
  categories text[] not null default '{}',
  occurred_at timestamptz,
  occurred_at_precision text not null default 'reported'
    check (occurred_at_precision in ('exact', 'hour', 'day', 'reported', 'unknown')),
  first_reported_at timestamptz,
  last_reported_at timestamptz,
  country_code char(2),
  affected_countries text[] not null default '{}',
  geo_entity_id text references public.geo_entities(id),
  location_name text,
  lat double precision check (lat between -90 and 90),
  lon double precision check (lon between -180 and 180),
  geo_precision text not null default 'unknown'
    check (geo_precision in ('point', 'city', 'facility', 'port', 'admin1', 'country', 'maritime_zone', 'corridor', 'unknown')),
  geo_confidence real check (geo_confidence is null or geo_confidence between 0 and 1),
  geo_source text,
  actors text[] not null default '{}',
  sectors text[] not null default '{}',
  casualties_killed integer check (casualties_killed is null or casualties_killed >= 0),
  casualties_injured integer check (casualties_injured is null or casualties_injured >= 0),
  casualty_note text,
  source_count integer not null default 0,
  independent_source_count integer not null default 0,
  official_statement_count integer not null default 0,
  uncertainty_notes text,
  verification_detail jsonb not null default '{}'::jsonb,
  context jsonb not null default '{}'::jsonb,           -- regional context agent output
  gcc_relevance real check (gcc_relevance is null or gcc_relevance between 0 and 1),
  version integer not null default 1,
  merged_into uuid references public.incidents(id),
  is_demo boolean not null default false,
  created_by_agent text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  search tsvector generated always as (
    to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(location_name, ''))
  ) stored,
  unique (tenant_id, ref),
  constraint coordinates_need_provenance check ((lat is null and lon is null) or geo_source is not null)
);
create index incidents_tenant_time_idx on public.incidents(tenant_id, last_reported_at desc);
create index incidents_tenant_country_idx on public.incidents(tenant_id, country_code);
create index incidents_categories_idx on public.incidents using gin(categories);
create index incidents_search_idx on public.incidents using gin(search);
create index incidents_title_trgm_idx on public.incidents using gin(title gin_trgm_ops);
create index incidents_review_idx on public.incidents(tenant_id, review_status) where review_status = 'pending';
create trigger incidents_touch before update on public.incidents
for each row execute function app.touch_updated_at();

alter table public.claims
  add constraint claims_incident_fk foreign key (incident_id) references public.incidents(id) on delete set null;

-- Evidence links: which articles support / contradict / correct an incident
create table public.incident_articles (
  incident_id uuid not null references public.incidents(id) on delete cascade,
  article_id uuid not null references public.articles(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  role text not null check (role in ('original', 'syndicated', 'follow_up', 'official_statement',
                                     'contradicting', 'correction', 'retraction')),
  origin_key text not null,                -- independence key used by the verification agent
  match_score real,
  added_by text not null,
  added_at timestamptz not null default now(),
  primary key (incident_id, article_id)
);
create index incident_articles_article_idx on public.incident_articles(article_id);

-- Full snapshots of every incident version (corrections, retractions, analyst edits)
create table public.incident_revisions (
  id bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  incident_id uuid not null references public.incidents(id) on delete cascade,
  version integer not null,
  change_type text not null check (change_type in ('created', 'evidence_added', 'verification_changed',
                                                   'analyst_edit', 'correction', 'retraction', 'merged',
                                                   'reviewed', 'status_changed')),
  change_note text,
  changed_fields text[] not null default '{}',
  snapshot jsonb not null,
  changed_by uuid,
  changed_by_agent text,
  created_at timestamptz not null default now(),
  unique (incident_id, version)
);

-- Extraction output per article (audit trail + evaluation), before/after matching
create table public.incident_candidates (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  article_id uuid not null references public.articles(id) on delete cascade,
  extractor text not null,
  extraction jsonb not null,
  matched_incident_id uuid references public.incidents(id) on delete set null,
  match_score real,
  decision text not null check (decision in ('created', 'attached', 'discarded', 'error')),
  decision_reason text,
  created_at timestamptz not null default now()
);
create index incident_candidates_article_idx on public.incident_candidates(article_id);

-- Human review queue (incidents, alerts, reports, sources, risk assessments, quality findings)
create table public.analyst_reviews (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  item_type text not null check (item_type in ('incident', 'alert', 'report', 'source', 'risk_assessment', 'quality')),
  item_id uuid not null,
  reason text not null,
  priority smallint not null default 3 check (priority between 1 and 5),
  status text not null default 'open' check (status in ('open', 'approved', 'rejected', 'changes_requested', 'dismissed')),
  assigned_to uuid references public.app_users(id) on delete set null,
  decided_by uuid references public.app_users(id) on delete set null,
  decided_at timestamptz,
  decision_note text,
  created_by_agent text,
  created_at timestamptz not null default now()
);
create unique index analyst_reviews_open_item_idx on public.analyst_reviews(item_type, item_id, reason) where status = 'open';
create index analyst_reviews_tenant_status_idx on public.analyst_reviews(tenant_id, status, priority);
