-- 0002_reference.sql
-- Reference data shared by all tenants (read-only to clients):
-- gazetteer (geo_entities), editable event taxonomy, organisational actors, source catalog.

-- ---------------------------------------------------------------------------
-- Gazetteer. Coordinates are representative points taken from public reference
-- gazetteers (see coord_source); precision_km states their uncertainty radius.
-- The pipeline only ever places incidents on these points or on coordinates
-- explicitly reported by a source - it never invents coordinates.
-- ---------------------------------------------------------------------------
create table public.geo_entities (
  id text primary key check (id ~ '^[a-z0-9:_-]{3,80}$'),
  kind text not null check (kind in ('country', 'admin1', 'city', 'port', 'facility',
                                     'maritime_zone', 'corridor', 'border_crossing')),
  name text not null,
  name_ar text,
  aliases text[] not null default '{}',
  country_code char(2),
  parent_id text references public.geo_entities(id),
  lat double precision check (lat between -90 and 90),
  lon double precision check (lon between -180 and 180),
  precision_km numeric check (precision_km is null or precision_km >= 0),
  coord_source text not null,
  is_gcc boolean not null default false,
  region text not null check (region in ('gcc', 'iran', 'iraq', 'yemen', 'levant', 'israel_palestine',
                                         'egypt', 'horn_of_africa', 'red_sea', 'gulf_waters',
                                         'arabian_sea', 'other')),
  created_at timestamptz not null default now()
);
create index geo_entities_country_idx on public.geo_entities(country_code);
create index geo_entities_kind_idx on public.geo_entities(kind);

-- ---------------------------------------------------------------------------
-- Event taxonomy. tenant_id NULL = platform default; a tenant row with the same
-- key overrides the default for that tenant. Multiple categories per incident.
-- ---------------------------------------------------------------------------
create table public.event_types (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references public.tenants(id) on delete cascade,
  key text not null check (key ~ '^[a-z][a-z0-9_]{1,48}$'),
  label text not null,
  label_ar text,
  parent_key text,
  description text,
  default_severity smallint not null default 2 check (default_severity between 1 and 5),
  risk_dimensions text[] not null default '{}',
  keywords_en text[] not null default '{}',
  keywords_ar text[] not null default '{}',
  review_required boolean not null default false,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  unique nulls not distinct (tenant_id, key)
);

-- ---------------------------------------------------------------------------
-- Actors: ORGANISATIONS ONLY (states, government bodies, armed groups, companies,
-- international organisations). There is deliberately no type for private
-- individuals: the platform must not build profiles of people.
-- ---------------------------------------------------------------------------
create table public.actors (
  id text primary key check (id ~ '^[a-z0-9:_-]{3,80}$'),
  tenant_id uuid references public.tenants(id) on delete cascade,
  name text not null,
  name_ar text,
  aliases text[] not null default '{}',
  actor_type text not null check (actor_type in ('state', 'government_body', 'military', 'security_force',
                                                 'non_state_armed_group', 'political_organization', 'company',
                                                 'intl_org', 'media', 'coalition', 'other_organization')),
  country_code char(2),
  notes text,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Source catalog: vetted public feeds a tenant can add to its registry.
-- verified_at records when (and how) the feed URL was last confirmed to serve a
-- valid feed; NULL = never verified.
-- ---------------------------------------------------------------------------
create table public.source_catalog (
  key text primary key check (key ~ '^[a-z0-9][a-z0-9_.-]{1,80}$'),
  name text not null,
  publisher text not null,
  adapter text not null check (adapter in ('rss', 'gdelt', 'html_list', 'json_api')),
  url text not null,
  homepage_url text,
  language text not null,
  country_focus text[] not null default '{}',
  source_type text not null,
  ownership text,
  reliability char(1) not null check (reliability in ('A', 'B', 'C', 'D', 'E', 'F')),
  reliability_rationale text,
  origin_group text,
  license_type text not null,
  license_notes text,
  terms_url text,
  usage_policy text not null default 'headline_link_excerpt',
  fetch_interval_minutes integer not null default 30,
  priority smallint not null default 3,
  verified_at timestamptz,
  verification_note text,
  default_enabled boolean not null default false,
  created_at timestamptz not null default now()
);
