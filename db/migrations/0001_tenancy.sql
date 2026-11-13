-- 0001_tenancy.sql
-- Tenants, users, memberships (RBAC), plans, audit log, rate limits, encrypted tenant secrets
-- and the helper functions every RLS policy uses.

create extension if not exists pgcrypto;
create extension if not exists pg_trgm;
create extension if not exists citext;

create schema if not exists app;
grant usage on schema app to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Plans (SaaS tiers). Prices are configuration, NOT validated market pricing:
-- NULL means "not configured". Limits/features drive feature flags and quotas.
-- ---------------------------------------------------------------------------
create table public.plans (
  id text primary key check (id ~ '^[a-z][a-z0-9_-]{1,31}$'),
  name text not null,
  description text,
  price_monthly_minor integer check (price_monthly_minor is null or price_monthly_minor >= 0),
  currency char(3),
  limits jsonb not null default '{}'::jsonb,
  features jsonb not null default '{}'::jsonb,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table public.tenants (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$'),
  name text not null check (length(name) between 2 and 120),
  plan_id text not null references public.plans(id),
  -- settings: verification policy, review policy, retention, risk exposure, default timezone ...
  settings jsonb not null default '{}'::jsonb,
  -- branding: report logo text, colours, footer (branded reports)
  branding jsonb not null default '{}'::jsonb,
  is_demo boolean not null default false,
  status text not null default 'active' check (status in ('active', 'suspended')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.app_users (
  id uuid primary key default gen_random_uuid(),       -- equals auth.users.id when AUTH_PROVIDER=supabase
  email citext not null unique,
  display_name text check (display_name is null or length(display_name) <= 120),
  password_hash text,                                   -- AUTH_PROVIDER=local only (scrypt); never granted to clients
  auth_provider text not null default 'local' check (auth_provider in ('local', 'supabase')),
  locale text not null default 'en' check (locale in ('en', 'ar')),
  timezone text not null default 'Asia/Riyadh',
  mfa_totp_secret text,                                 -- AES-GCM ciphertext (application key); reserved for M2
  is_platform_admin boolean not null default false,
  disabled boolean not null default false,
  failed_logins integer not null default 0,
  locked_until timestamptz,
  last_login_at timestamptz,
  created_at timestamptz not null default now()
);

create type public.member_role as enum ('admin', 'analyst', 'reviewer', 'client');

create table public.memberships (
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null references public.app_users(id) on delete cascade,
  role public.member_role not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, user_id)
);
create index memberships_user_idx on public.memberships(user_id);

-- Per-tenant counters for human-readable references (INC-000123, RPT-000045)
create table public.tenant_counters (
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  name text not null,
  value bigint not null default 0,
  primary key (tenant_id, name)
);

-- ---------------------------------------------------------------------------
-- Encrypted tenant secrets (Slack webhook URLs, Telegram chat config, API keys
-- for licensed feeds). Encrypted by the application with AES-256-GCM using
-- APP_ENCRYPTION_KEY; the database never sees plaintext.
-- ---------------------------------------------------------------------------
create table public.tenant_secrets (
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  name text not null check (name ~ '^[a-z0-9_]{2,64}$'),
  ciphertext text not null,
  updated_by uuid,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, name)
);

-- ---------------------------------------------------------------------------
-- Append-only audit log
-- ---------------------------------------------------------------------------
create table public.audit_log (
  id bigint generated always as identity primary key,
  tenant_id uuid references public.tenants(id) on delete set null,
  actor_user_id uuid,
  actor_agent text,
  action text not null check (length(action) <= 80),
  entity_type text,
  entity_id text,
  details jsonb not null default '{}'::jsonb,
  ip text,
  created_at timestamptz not null default now()
);
create index audit_log_tenant_time_idx on public.audit_log(tenant_id, created_at desc);
create index audit_log_entity_idx on public.audit_log(entity_type, entity_id);

-- Fixed-window rate limiting shared by all app instances
create table public.rate_limits (
  key text not null,
  window_start timestamptz not null,
  count integer not null default 0,
  primary key (key, window_start)
);

-- ---------------------------------------------------------------------------
-- Helper functions used by RLS policies.
-- The server sets request.jwt.claims = {"sub": <user id>, "tenant_id": <active tenant>, "role": "authenticated"}
-- ---------------------------------------------------------------------------
create or replace function app.uid() returns uuid
language sql stable
as $$ select auth.uid() $$;

create or replace function app.active_tenant_id() returns uuid
language sql stable
as $$ select nullif(auth.jwt() ->> 'tenant_id', '')::uuid $$;

-- True when the current user holds one of `roles` in the ACTIVE tenant.
-- SECURITY DEFINER so it can read memberships without granting clients access to them.
create or replace function app.has_role(p_roles public.member_role[]) returns boolean
language sql stable security definer set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.memberships m
    join public.app_users u on u.id = m.user_id
    join public.tenants t on t.id = m.tenant_id
    where m.tenant_id = app.active_tenant_id()
      and m.user_id = auth.uid()
      and m.role = any(p_roles)
      and not u.disabled
      and t.status = 'active'
  )
$$;

create or replace function app.is_member() returns boolean
language sql stable
as $$ select app.has_role(array['admin','analyst','reviewer','client']::public.member_role[]) $$;

create or replace function app.can_write() returns boolean
language sql stable
as $$ select app.has_role(array['admin','analyst']::public.member_role[]) $$;

create or replace function app.can_review() returns boolean
language sql stable
as $$ select app.has_role(array['admin','reviewer']::public.member_role[]) $$;

create or replace function app.is_staff() returns boolean
language sql stable
as $$ select app.has_role(array['admin','analyst','reviewer']::public.member_role[]) $$;

create or replace function app.is_admin() returns boolean
language sql stable
as $$ select app.has_role(array['admin']::public.member_role[]) $$;

-- Next human-readable reference number for a tenant (atomic).
create or replace function app.next_ref(p_tenant uuid, p_name text) returns bigint
language plpgsql security definer set search_path = public, pg_temp
as $$
declare v bigint;
begin
  insert into public.tenant_counters(tenant_id, name, value) values (p_tenant, p_name, 1)
  on conflict (tenant_id, name) do update set value = public.tenant_counters.value + 1
  returning value into v;
  return v;
end $$;

-- Fixed-window rate limiter. Returns true when the call is allowed.
create or replace function app.rate_limit_hit(p_key text, p_window_seconds integer, p_max integer)
returns boolean
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  v_window timestamptz := to_timestamp(floor(extract(epoch from now()) / p_window_seconds) * p_window_seconds);
  v_count integer;
begin
  insert into public.rate_limits(key, window_start, count) values (p_key, v_window, 1)
  on conflict (key, window_start) do update set count = public.rate_limits.count + 1
  returning count into v_count;
  return v_count <= p_max;
end $$;

create or replace function app.touch_updated_at() returns trigger
language plpgsql
as $$ begin new.updated_at := now(); return new; end $$;

create trigger tenants_touch before update on public.tenants
for each row execute function app.touch_updated_at();
