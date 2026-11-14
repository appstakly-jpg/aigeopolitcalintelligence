-- 0011_alert_rule_summary.sql
-- Staff read every alert in their organisation (policy alerts_read), but alert rules stay private to
-- their owner and admins because they hold delivery destinations (email, Telegram chat, webhook URL).
-- Joining alerts to alert_rules under RLS therefore hid other users' alerts from reviewers, who must
-- see them to approve high-impact alerts. This function exposes only what a reviewer needs to judge
-- an alert: the rule's name and its owner's name and role, never destinations or filters.

create or replace function app.alert_rule_summary(p_rule uuid)
returns table (name text, owner_name text, owner_role public.member_role)
language sql stable security definer set search_path = public, pg_temp
as $$
  select r.name, coalesce(u.display_name, 'member'), m.role
  from public.alert_rules r
  join public.app_users u on u.id = r.user_id
  left join public.memberships m on m.tenant_id = r.tenant_id and m.user_id = r.user_id
  where r.id = p_rule
    and r.tenant_id = (select app.active_tenant_id())
    and ((select app.is_staff()) or r.user_id = (select auth.uid()))
$$;

revoke all on function app.alert_rule_summary(uuid) from public;
grant execute on function app.alert_rule_summary(uuid) to authenticated, service_role;
