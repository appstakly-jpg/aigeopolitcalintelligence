-- 0000_supabase_compat.sql
-- Supabase compatibility shim.
--
-- On a Supabase project these roles, the `auth` schema and its helper functions already
-- exist, and every statement below is a guarded no-op. On plain PostgreSQL (local dev, CI,
-- self-hosting) it creates the minimal subset our row-level-security policies rely on:
--   * roles anon / authenticated / service_role
--   * auth.jwt(), auth.uid(), auth.role() reading the `request.jwt.claims` setting
--
-- The application server verifies the user's session, then for every user-scoped query runs
--   SET LOCAL ROLE authenticated;
--   SELECT set_config('request.jwt.claims', '<verified claims json>', true);
-- inside a transaction - the same mechanism PostgREST/Supabase use - so RLS is enforced by
-- PostgreSQL itself rather than by application code.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end $$;

create schema if not exists auth;

do $$
declare
  created_any boolean := false;
begin
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'jwt'
  ) then
    execute $f$
      create function auth.jwt() returns jsonb
      language sql stable
      as $b$
        select coalesce(
          nullif(current_setting('request.jwt.claim', true), ''),
          nullif(current_setting('request.jwt.claims', true), ''),
          '{}'
        )::jsonb
      $b$
    $f$;
    created_any := true;
  end if;

  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'uid'
  ) then
    execute $f$
      create function auth.uid() returns uuid
      language sql stable
      as $b$
        select nullif(
          coalesce(
            nullif(current_setting('request.jwt.claim.sub', true), ''),
            auth.jwt() ->> 'sub'
          ), ''
        )::uuid
      $b$
    $f$;
    created_any := true;
  end if;

  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'role'
  ) then
    execute $f$
      create function auth.role() returns text
      language sql stable
      as $b$
        select coalesce(
          nullif(current_setting('request.jwt.claim.role', true), ''),
          auth.jwt() ->> 'role'
        )
      $b$
    $f$;
    created_any := true;
  end if;

  if created_any then
    execute 'grant usage on schema auth to anon, authenticated, service_role';
    execute 'grant execute on all functions in schema auth to anon, authenticated, service_role';
  end if;
end $$;
