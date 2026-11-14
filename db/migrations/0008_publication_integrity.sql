-- 0008_publication_integrity.sql
-- A report can only be published when its current version passed the citation
-- integrity check (every cited number resolves to stored evidence of this tenant).

create or replace function app.guard_report_publication() returns trigger
language plpgsql as $$
declare ok boolean;
begin
  if new.status is distinct from old.status and new.status = 'published' then
    if current_user = 'authenticated' and not app.can_review() then
      raise exception 'only reviewers or admins may publish reports' using errcode = '42501';
    end if;
    select coalesce((v.citation_check ->> 'ok')::boolean, false) into ok
    from public.report_versions v where v.report_id = new.id and v.version = new.current_version;
    if not coalesce(ok, false) then
      raise exception 'report % version % failed the citation integrity check and cannot be published',
        new.ref, new.current_version using errcode = '23514';
    end if;
  end if;
  return new;
end $$;
