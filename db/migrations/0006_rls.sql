-- 0006_rls.sql
-- Row-level security for every table, explicit grants, and guard triggers for
-- decisions that require specific roles (approval, publication, confirmation).
--
-- Model: the server sets request.jwt.claims {sub, tenant_id} after verifying the session.
-- Every tenant-scoped policy requires tenant_id = app.active_tenant_id() AND a role in that
-- tenant. Helper calls are wrapped in (select ...) so PostgreSQL evaluates them once per query.
-- The ingestion workers connect as the table owner (bypass RLS) and always set tenant_id explicitly.

-- Supabase grants broad default privileges to anon/authenticated; start from zero.
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema app from public;
grant execute on all functions in schema app to authenticated, service_role;
grant usage on all sequences in schema public to authenticated;

do $$
declare t text;
begin
  foreach t in array array[
    'plans', 'tenants', 'app_users', 'memberships', 'tenant_counters', 'tenant_secrets', 'audit_log',
    'rate_limits', 'geo_entities', 'event_types', 'actors', 'source_catalog', 'sources', 'source_health',
    'articles', 'claims', 'claim_provenance', 'jobs', 'incidents', 'incident_articles', 'incident_revisions',
    'incident_candidates', 'analyst_reviews', 'risk_methodologies', 'risk_assessments', 'reports',
    'report_versions', 'report_exports', 'alert_rules', 'alerts', 'alert_deliveries', 'watchlists',
    'chat_sessions', 'chat_messages', 'llm_usage'
  ] loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

-- Shorthand used below:
--   T  = tenant_id = (select app.active_tenant_id())

-- ---------------------------------------------------------------------------
-- Global reference data (read-only)
-- ---------------------------------------------------------------------------
grant select on public.plans, public.geo_entities, public.source_catalog to authenticated;
create policy plans_read on public.plans for select to authenticated using (true);
create policy geo_read on public.geo_entities for select to authenticated using (true);
create policy catalog_read on public.source_catalog for select to authenticated using (true);

-- ---------------------------------------------------------------------------
-- Tenancy
-- ---------------------------------------------------------------------------
grant select on public.tenants to authenticated;
grant update (name, settings, branding) on public.tenants to authenticated;
create policy tenants_read on public.tenants for select to authenticated
  using (id = (select app.active_tenant_id()) and (select app.is_member()));
create policy tenants_admin_update on public.tenants for update to authenticated
  using (id = (select app.active_tenant_id()) and (select app.is_admin()))
  with check (id = (select app.active_tenant_id()));

create or replace function app.shares_active_tenant(p_user uuid) returns boolean
language sql stable security definer set search_path = public, pg_temp
as $$
  select exists (select 1 from public.memberships m
                 where m.user_id = p_user and m.tenant_id = app.active_tenant_id())
$$;
grant execute on function app.shares_active_tenant(uuid) to authenticated;

-- Sensitive columns (password_hash, mfa secret, lockout counters) are never granted.
grant select (id, email, display_name, auth_provider, locale, timezone, is_platform_admin, disabled,
              last_login_at, created_at) on public.app_users to authenticated;
grant update (display_name, locale, timezone) on public.app_users to authenticated;
create policy users_read on public.app_users for select to authenticated
  using (id = (select auth.uid()) or ((select app.is_member()) and app.shares_active_tenant(id)));
create policy users_update_self on public.app_users for update to authenticated
  using (id = (select auth.uid())) with check (id = (select auth.uid()));

grant select, insert, update, delete on public.memberships to authenticated;
create policy memberships_read on public.memberships for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));
create policy memberships_admin_write on public.memberships for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()))
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));

-- tenant_counters / rate_limits: no grants, accessed only through SECURITY DEFINER functions.

grant select (tenant_id, name, updated_by, updated_at) on public.tenant_secrets to authenticated;
grant insert, update, delete on public.tenant_secrets to authenticated;
create policy secrets_admin_read on public.tenant_secrets for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));
create policy secrets_admin_write on public.tenant_secrets for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()))
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));

-- Audit log: append-only. Members may append their own actions; admins read.
grant select, insert on public.audit_log to authenticated;
create policy audit_admin_read on public.audit_log for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));
create policy audit_member_append on public.audit_log for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_member())
              and actor_user_id = (select auth.uid()));

-- ---------------------------------------------------------------------------
-- Taxonomy & actors: platform defaults + tenant overrides
-- ---------------------------------------------------------------------------
grant select, insert, update, delete on public.event_types, public.actors to authenticated;
create policy event_types_read on public.event_types for select to authenticated
  using (tenant_id is null or (tenant_id = (select app.active_tenant_id()) and (select app.is_member())));
create policy event_types_write on public.event_types for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()))
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));
create policy actors_read on public.actors for select to authenticated
  using (tenant_id is null or (tenant_id = (select app.active_tenant_id()) and (select app.is_member())));
create policy actors_write on public.actors for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.can_write()))
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));

-- ---------------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------------
grant select, insert, update, delete on public.sources to authenticated;
create policy sources_read on public.sources for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));
create policy sources_insert on public.sources for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));
create policy sources_update on public.sources for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.can_write()))
  with check (tenant_id = (select app.active_tenant_id()));
create policy sources_delete on public.sources for delete to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));

-- Only admins may approve/enable a source (licence + robots review is an admin decision).
create or replace function app.guard_source_approval() returns trigger
language plpgsql as $$
begin
  if current_user = 'authenticated' then
    if (tg_op = 'INSERT' and (new.status = 'approved' or new.enabled))
       or (tg_op = 'UPDATE' and (new.status is distinct from old.status or new.enabled is distinct from old.enabled
                                 or new.license_type is distinct from old.license_type)) then
      if not app.is_admin() then
        raise exception 'only tenant admins may approve, enable or relicense sources' using errcode = '42501';
      end if;
    end if;
  end if;
  return new;
end $$;
create trigger sources_guard_approval before insert or update on public.sources
for each row execute function app.guard_source_approval();

grant select on public.source_health to authenticated;
create policy source_health_read on public.source_health for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));

-- ---------------------------------------------------------------------------
-- Evidence: articles, claims, provenance
-- ---------------------------------------------------------------------------
grant select, insert, update on public.articles, public.claims to authenticated;
grant select, insert on public.claim_provenance to authenticated;
create policy articles_read on public.articles for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));
create policy articles_write on public.articles for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));
create policy articles_update on public.articles for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.can_write()))
  with check (tenant_id = (select app.active_tenant_id()));
create policy claims_read on public.claims for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));
create policy claims_insert on public.claims for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));
create policy claims_update on public.claims for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.can_write()))
  with check (tenant_id = (select app.active_tenant_id()));
create policy provenance_read on public.claim_provenance for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));
create policy provenance_insert on public.claim_provenance for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));

-- ---------------------------------------------------------------------------
-- Jobs: staff can read their tenant's jobs and request a fixed set of job kinds
-- ---------------------------------------------------------------------------
grant select, insert on public.jobs to authenticated;
create policy jobs_read on public.jobs for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_staff()));
create policy jobs_request on public.jobs for insert to authenticated
  with check (
    tenant_id = (select app.active_tenant_id()) and (select app.is_staff())
    and requested_by = (select auth.uid())
    and kind in ('run_pipeline', 'fetch_source', 'generate_report', 'export_report', 'compute_risk',
                 'quality_audit', 'source_scout', 'test_notification', 'verify_source')
  );

-- ---------------------------------------------------------------------------
-- Incidents. Clients see active/closed incidents that were not rejected in review;
-- staff see everything including candidates under review.
-- ---------------------------------------------------------------------------
grant select, insert, update on public.incidents to authenticated;
create policy incidents_read on public.incidents for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and ((select app.is_staff())
              or ((select app.is_member()) and status in ('active', 'closed') and review_status <> 'rejected')));
create policy incidents_insert on public.incidents for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));
create policy incidents_update on public.incidents for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and ((select app.can_write()) or (select app.can_review())))
  with check (tenant_id = (select app.active_tenant_id()));

-- Human confirmation policy: only reviewers/admins can approve/reject, or mark "confirmed".
create or replace function app.guard_incident_decisions() returns trigger
language plpgsql as $$
begin
  if current_user = 'authenticated' then
    if (new.review_status is distinct from old.review_status and new.review_status in ('approved', 'rejected'))
       or (new.verification_status is distinct from old.verification_status and new.verification_status = 'confirmed') then
      if not app.can_review() then
        raise exception 'only reviewers or admins may approve, reject or confirm incidents' using errcode = '42501';
      end if;
    end if;
  end if;
  return new;
end $$;
create trigger incidents_guard_decisions before update on public.incidents
for each row execute function app.guard_incident_decisions();

grant select, insert, update, delete on public.incident_articles to authenticated;
create policy incident_articles_read on public.incident_articles for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and exists (select 1 from public.incidents i where i.id = incident_id));
create policy incident_articles_write on public.incident_articles for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.can_write()))
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));

grant select, insert on public.incident_revisions to authenticated;
create policy incident_revisions_read on public.incident_revisions for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and exists (select 1 from public.incidents i where i.id = incident_id));
create policy incident_revisions_insert on public.incident_revisions for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id())
              and ((select app.can_write()) or (select app.can_review()))
              and changed_by = (select auth.uid()));

grant select, insert on public.incident_candidates to authenticated;
create policy candidates_read on public.incident_candidates for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_staff()));
create policy candidates_insert on public.incident_candidates for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));

grant select, insert, update on public.analyst_reviews to authenticated;
create policy reviews_read on public.analyst_reviews for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_staff()));
create policy reviews_insert on public.analyst_reviews for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_staff()));
create policy reviews_update on public.analyst_reviews for update to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and ((select app.can_review()) or ((select app.can_write()) and item_type in ('quality', 'source'))))
  with check (tenant_id = (select app.active_tenant_id()));

-- ---------------------------------------------------------------------------
-- Risk
-- ---------------------------------------------------------------------------
grant select, insert, update on public.risk_methodologies to authenticated;
create policy methodology_read on public.risk_methodologies for select to authenticated
  using (tenant_id is null or (tenant_id = (select app.active_tenant_id()) and (select app.is_member())));
create policy methodology_admin_insert on public.risk_methodologies for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));
create policy methodology_admin_update on public.risk_methodologies for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()))
  with check (tenant_id = (select app.active_tenant_id()));

grant select, update on public.risk_assessments to authenticated;
create policy risk_read on public.risk_assessments for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));
create policy risk_adjust on public.risk_assessments for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and ((select app.can_write()) or (select app.can_review())))
  with check (tenant_id = (select app.active_tenant_id()));

-- ---------------------------------------------------------------------------
-- Reports: clients only see published reports.
-- ---------------------------------------------------------------------------
grant select, insert, update on public.reports to authenticated;
grant select, insert on public.report_versions to authenticated;
grant select on public.report_exports to authenticated;
create policy reports_read on public.reports for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and ((select app.is_staff()) or ((select app.is_member()) and status = 'published')));
create policy reports_insert on public.reports for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));
create policy reports_update on public.reports for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and ((select app.can_write()) or (select app.can_review())))
  with check (tenant_id = (select app.active_tenant_id()));

create or replace function app.guard_report_publication() returns trigger
language plpgsql as $$
begin
  if current_user = 'authenticated' and new.status is distinct from old.status
     and new.status = 'published' and not app.can_review() then
    raise exception 'only reviewers or admins may publish reports' using errcode = '42501';
  end if;
  return new;
end $$;
create trigger reports_guard_publication before update on public.reports
for each row execute function app.guard_report_publication();

create policy report_versions_read on public.report_versions for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and exists (select 1 from public.reports r where r.id = report_id));
create policy report_versions_insert on public.report_versions for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.can_write()));
create policy report_exports_read on public.report_exports for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and exists (select 1 from public.report_versions v where v.id = report_version_id));

-- ---------------------------------------------------------------------------
-- Alerting: users manage their own rules; admins see all; reviewers decide on alerts.
-- ---------------------------------------------------------------------------
grant select, insert, update, delete on public.alert_rules to authenticated;
create policy alert_rules_read on public.alert_rules for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member())
         and (user_id = (select auth.uid()) or (select app.is_admin())));
create policy alert_rules_own_write on public.alert_rules for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member())
         and (user_id = (select auth.uid()) or (select app.is_admin())))
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_member())
              and (user_id = (select auth.uid()) or (select app.is_admin())));

grant select, update on public.alerts to authenticated;
grant select on public.alert_deliveries to authenticated;
create policy alerts_read on public.alerts for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and ((select app.is_staff())
              or exists (select 1 from public.alert_rules r where r.id = rule_id and r.user_id = (select auth.uid()))));
create policy alerts_review on public.alerts for update to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.can_review()))
  with check (tenant_id = (select app.active_tenant_id()));
create policy deliveries_read on public.alert_deliveries for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and exists (select 1 from public.alerts a where a.id = alert_id));

grant select, insert, update, delete on public.watchlists to authenticated;
create policy watchlists_read on public.watchlists for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member())
         and (user_id is null or user_id = (select auth.uid()) or (select app.is_admin())));
create policy watchlists_write on public.watchlists for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_member())
         and (user_id = (select auth.uid()) or (select app.is_admin())))
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_member())
              and (user_id = (select auth.uid()) or (select app.is_admin())));

-- ---------------------------------------------------------------------------
-- Chat: strictly private to the user
-- ---------------------------------------------------------------------------
grant select, insert, update, delete on public.chat_sessions to authenticated;
grant select, insert on public.chat_messages to authenticated;
create policy chat_sessions_own on public.chat_sessions for all to authenticated
  using (tenant_id = (select app.active_tenant_id()) and user_id = (select auth.uid()) and (select app.is_member()))
  with check (tenant_id = (select app.active_tenant_id()) and user_id = (select auth.uid()) and (select app.is_member()));
create policy chat_messages_own_read on public.chat_messages for select to authenticated
  using (tenant_id = (select app.active_tenant_id())
         and exists (select 1 from public.chat_sessions s where s.id = session_id));
create policy chat_messages_own_insert on public.chat_messages for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id())
              and exists (select 1 from public.chat_sessions s where s.id = session_id));

-- ---------------------------------------------------------------------------
-- LLM usage: members append their own interactive usage (chat); admins read.
-- ---------------------------------------------------------------------------
grant select, insert on public.llm_usage to authenticated;
create policy llm_usage_admin_read on public.llm_usage for select to authenticated
  using (tenant_id = (select app.active_tenant_id()) and (select app.is_admin()));
create policy llm_usage_member_insert on public.llm_usage for insert to authenticated
  with check (tenant_id = (select app.active_tenant_id()) and (select app.is_member()));
