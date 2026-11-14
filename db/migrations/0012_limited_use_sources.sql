-- 0012_limited_use_sources.sql
-- A source whose licence has not been reviewed yet ('review_required') may be approved for limited
-- use only: headlines, links and short excerpts (or metadata only). Full text needs a recorded
-- licence. The web app asks the admin to acknowledge this when approving; this constraint makes the
-- rule hold for every write path (web app, workers, SQL).

alter table public.sources
  add constraint review_required_limited_use
  check (not (enabled and license_type = 'review_required' and usage_policy = 'full_text'));
