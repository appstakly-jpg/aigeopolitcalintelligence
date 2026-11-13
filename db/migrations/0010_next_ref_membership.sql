-- 0010_next_ref_membership.sql
-- app.next_ref: a caller with request claims must be a member of the tenant it numbers.
-- 0009 compared the requested tenant with the *claimed* tenant only, and the claim alone is not
-- proof of membership. Workers connect without request claims and are unaffected.

create or replace function app.next_ref(p_tenant uuid, p_name text) returns bigint
language plpgsql security definer set search_path = public, pg_temp
as $$
declare v bigint;
begin
  if auth.uid() is not null or app.active_tenant_id() is not null then
    if app.active_tenant_id() is distinct from p_tenant or not app.is_member() then
      raise exception 'cannot allocate references for this tenant' using errcode = '42501';
    end if;
  end if;
  insert into public.tenant_counters(tenant_id, name, value) values (p_tenant, p_name, 1)
  on conflict (tenant_id, name) do update set value = public.tenant_counters.value + 1
  returning value into v;
  return v;
end $$;
