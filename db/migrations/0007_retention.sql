-- 0007_retention.sql
-- Retention rules, applied daily by the scheduler (`retention` job).
-- Per-tenant overrides live in tenants.settings -> 'retention' (days):
--   {"article_excerpt_days": 365, "chat_days": 365, "audit_days": 730}
-- Evidence metadata (URL, publisher, headline, timestamps) is kept so incident
-- citations never break; only stored excerpts are cleared after the window.

create or replace function app.apply_retention() returns jsonb
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  v_result jsonb := '{}'::jsonb;
  v_count bigint;
  r record;
begin
  delete from public.source_health where checked_at < now() - interval '90 days';
  get diagnostics v_count = row_count; v_result := v_result || jsonb_build_object('source_health', v_count);

  delete from public.jobs where status in ('succeeded', 'cancelled') and finished_at < now() - interval '30 days';
  get diagnostics v_count = row_count; v_result := v_result || jsonb_build_object('jobs', v_count);

  delete from public.rate_limits where window_start < now() - interval '2 days';
  get diagnostics v_count = row_count; v_result := v_result || jsonb_build_object('rate_limits', v_count);

  delete from public.report_exports where created_at < now() - interval '90 days';
  get diagnostics v_count = row_count; v_result := v_result || jsonb_build_object('report_exports', v_count);

  delete from public.llm_usage where created_at < now() - interval '400 days';
  get diagnostics v_count = row_count; v_result := v_result || jsonb_build_object('llm_usage', v_count);

  for r in select id,
                  coalesce((settings -> 'retention' ->> 'article_excerpt_days')::int, 365) as excerpt_days,
                  coalesce((settings -> 'retention' ->> 'chat_days')::int, 365) as chat_days,
                  coalesce((settings -> 'retention' ->> 'audit_days')::int, 730) as audit_days
           from public.tenants loop
    update public.articles set excerpt = null, excerpt_en = null
     where tenant_id = r.id and excerpt is not null
       and retrieved_at < now() - make_interval(days => r.excerpt_days);
    delete from public.chat_sessions s
     where s.tenant_id = r.id
       and not exists (select 1 from public.chat_messages m
                        where m.session_id = s.id and m.created_at >= now() - make_interval(days => r.chat_days))
       and s.created_at < now() - make_interval(days => r.chat_days);
    delete from public.audit_log where tenant_id = r.id and created_at < now() - make_interval(days => r.audit_days);
  end loop;

  return v_result;
end $$;

revoke all on function app.apply_retention() from public, authenticated;
