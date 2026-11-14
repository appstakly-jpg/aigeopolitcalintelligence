-- 0009_analyst_jobs.sql
-- 1. Analysts may queue `recompute_incident` (re-derive an incident after an analyst correction).
-- 2. app.next_ref refuses to advance another tenant's counters when called with request claims.

drop policy if exists jobs_request on public.jobs;
create policy jobs_request on public.jobs for insert to authenticated
  with check (
    tenant_id = (select app.active_tenant_id()) and (select app.is_staff())
    and requested_by = (select auth.uid())
    and kind in ('run_pipeline', 'fetch_source', 'generate_report', 'export_report', 'compute_risk',
                 'quality_audit', 'source_scout', 'test_notification', 'verify_source', 'recompute_incident')
  );

create or replace function app.next_ref(p_tenant uuid, p_name text) returns bigint
language plpgsql security definer set search_path = public, pg_temp
as $$
declare v bigint;
begin
  -- Workers connect without request claims; signed-in users may only number their active tenant.
  if app.active_tenant_id() is not null and app.active_tenant_id() <> p_tenant then
    raise exception 'cannot allocate references for another tenant' using errcode = '42501';
  end if;
  insert into public.tenant_counters(tenant_id, name, value) values (p_tenant, p_name, 1)
  on conflict (tenant_id, name) do update set value = public.tenant_counters.value + 1
  returning value into v;
  return v;
end $$;
