# API

The web app exposes a small HTTP API next to the UI. All UI mutations (corrections, reviews, alert
decisions, publication, settings) are Next.js server actions that run the same validation, role checks,
RLS and audit logging; they are not a public API.

## Authentication

API routes use the browser session (the `gi_session` cookie, HTTP-only, SameSite=Lax, 12 hours) or,
with `AUTH_PROVIDER=supabase`, the Supabase access token. Every query runs under row-level security as
the signed-in user in their active organisation, so the API returns exactly what that user may see in
the UI. Long-lived API keys for server-to-server use are on the [roadmap](roadmap.md).

Unauthenticated requests get `401 {"error":"unauthorized"}`. State-changing routes also refuse
cross-site requests (`403`): the browser's `Origin` must match the host the request was sent to
(`Host` / `X-Forwarded-Host`) or `APP_BASE_URL`.

## `GET /api/v1/incidents`

Incidents visible to the caller, newest first. Rate limit: 120 requests per minute per user.

| Query parameter | Meaning |
| --- | --- |
| `range` | `24h`, `7d` (default), `30d`, `90d`; or `from` and `to` as `YYYY-MM-DD` (at most 366 days) |
| `country` | ISO 3166-1 alpha-2, matches the primary or an affected country (`SA`, `AE`, `QA`, `KW`, `BH`, `OM`, `IR`, `IQ`, `YE`, `IL`, ...) |
| `category` | event type key, e.g. `maritime_incident`, `drone_missile` (see Administration → Event taxonomy) |
| `verification` | comma-separated: `confirmed`, `corroborated`, `single_source`, `disputed`, `unverified` |
| `min_severity` | 1 to 5 |
| `review` | `pending`, `approved`, `rejected`, `changes_requested` (rejected incidents only appear when asked for) |
| `q` | full-text search, English and Arabic, up to 200 characters |
| `limit`, `offset` | page size (1 to 500, default 100) and offset |
| `format` | `csv` for a CSV download (cells that could be read as spreadsheet formulas are neutralised) |

Response:

```json
{
  "data": [
    {
      "id": "a3f6f7a1-5aaa-44eb-b410-eb73c495ceeb",
      "ref": "INC-000003",
      "title": "Commercial tanker reports explosion near Bab el-Mandeb",
      "summary": "…",
      "status": "active",
      "verification_status": "corroborated",
      "review_status": "pending",
      "credibility": 2,
      "severity": 4,
      "confidence": "moderate",
      "primary_category": "maritime_incident",
      "categories": ["maritime_incident", "shipping_disruption"],
      "country_code": "YE",
      "affected_countries": ["SA"],
      "location_name": "Bab el-Mandeb Strait",
      "lat": 12.58, "lon": 43.33,
      "geo_precision": "maritime_zone",
      "geo_confidence": 0.8,
      "casualties_killed": null,
      "casualties_injured": null,
      "source_count": 5,
      "independent_source_count": 3,
      "first_reported_at": "2026-11-13T18:20:00.000Z",
      "last_reported_at": "2026-11-13T19:05:00.000Z",
      "version": 5,
      "is_synthetic": true,
      "url": "https://intel.example.com/incidents/a3f6f7a1-5aaa-44eb-b410-eb73c495ceeb"
    }
  ],
  "total": 6, "limit": 100, "offset": 0,
  "range": { "from": "2026-11-06T19:00:00.000Z", "to": "2026-11-13T19:00:00.000Z" }
}
```

(Values above are illustrative of the shape; they come from the synthetic demo.)

Notes on fields:
- `casualties_killed` / `casualties_injured` are `null` when no source states a figure. `null` never
  means zero.
- `source_count` counts linked reports; `independent_source_count` counts distinct origins (wire copies
  and syndicated near-duplicates collapse into one). Only the latter supports corroboration.
- `geo_precision` is one of `point`, `facility`, `port`, `city`, `admin1`, `country`, `maritime_zone`,
  `corridor`, `unknown`; `geo_confidence` is 0 to 1. Coordinates of coarse placements are the centroid
  of the area, not the event location.
- `is_synthetic` is `true` only for the demo organisation's fabricated test data.

Example:

```bash
curl -s --cookie "gi_session=$SESSION" \
  "https://intel.example.com/api/v1/incidents?country=SA&min_severity=3&range=30d" | jq '.data[].ref'
```

## `POST /api/chat`

The AI analyst. Answers only from stored evidence retrieved for the question, with numbered citations.
Rate limit: 60 questions per hour per user. Requires the `ai_chat` plan feature.

Request:

```json
{ "message": "What changed in Gulf maritime security in the last seven days?", "range": "7d", "sessionId": "optional uuid" }
```

`range` is `24h`, `7d`, `30d` or `90d`; or pass `from` and `to` (`YYYY-MM-DD`). Optional evidence
filters:

| Field | Meaning |
| --- | --- |
| `verification` | only incidents with these statuses, e.g. `["confirmed", "corroborated"]` |
| `minReliability` | only source articles graded this letter or better, e.g. `"C"` for A–C |

Filters are stored with the answer and the model is told to mention them as a coverage limit.

Response:

```json
{
  "sessionId": "…",
  "message": {
    "id": "…",
    "role": "assistant",
    "content": "A commercial tanker reported an explosion near Bab el-Mandeb [1][2] … Uncertainty: …",
    "citations": [
      { "n": 1, "kind": "incident", "id": "…", "ref": "INC-000003", "title": "…", "url": null, "incidentId": "…", "isDemo": true },
      { "n": 2, "kind": "article", "id": "…", "ref": null, "title": "…", "url": "https://publisher.example/…", "incidentId": "…", "isDemo": true }
    ],
    "check": { "ok": true, "cited": [1, 2], "errors": [], "warnings": [] },
    "generator": "claude-opus-5-5",
    "created_at": "…"
  }
}
```

`generator` says how the answer was produced:
- a model id: a model wrote it and it passed the citation check;
- `extractive (<reason>)`: no model answer was used (`not_configured`, `budget`, `refused`, `error`)
  and the answer lists the retrieved evidence instead; `extractive (rejected <model>)` when a model
  draft failed the citation check;
- `none`: nothing relevant is stored for the question and period (the answer says so);
- `policy`: the request asked to locate, profile or track a person and was declined.

The check rejects citations that point at nothing and figures that do not appear in the evidence.

## `GET /api/chat/export?sessionId={id}`

The caller's own conversation as a Markdown download: each question and answer with its numbered
evidence links, how the answer was produced, whether it passed the citation check, the date range and
filters. Conversations marked as containing synthetic demo data say so. Exports are audit-logged.

## `GET /api/health`

Unauthenticated liveness and readiness probe. Reports only coarse state:

```json
{ "status": "ok", "db": "ok", "migrations": 12, "jobs_queued": 0, "jobs_stale": 0, "latency_ms": 2 }
```

`503 {"status":"degraded","db":"unreachable"}` when the database cannot be reached.

## `GET /reports/{id}/download`

Downloads a report under RLS: `?format=html&v={version}` for the stored HTML of a version, or
`?export={exportId}` for a generated DOCX/PDF/HTML export (exports are requested on the report page
and produced by the worker). Clients can only download published reports. Files are sent as
attachments with a sandboxing Content-Security-Policy.

## Errors

JSON bodies of the form `{"error": "…"}` with `400` (validation), `401`, `403`, `404`, `429` (rate
limit) or `503`.
