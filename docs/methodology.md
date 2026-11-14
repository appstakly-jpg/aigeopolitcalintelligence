# Methodology

How the platform turns public reporting into incidents, verification statuses, severities, risk
indices, alerts and briefs. Every rule below is deterministic code; the module is named so you can
check the implementation. Organisation-level settings are under Administration → Policies.

## Principles

- **Evidence first.** Every incident, score, alert and brief paragraph links back to stored articles.
  Nothing is shown that cannot be traced to evidence.
- **Counting sources is not corroboration.** Reports are grouped by origin; copies, wire pickups and
  same-network outlets count once.
- **Official statements are attributable claims.** They are labelled as such and do not verify an
  event on their own.
- **Contradictions stay visible.** Denials and conflicting figures are kept as evidence and shown.
- **Nothing is invented.** No coordinates, casualty figures, citations or scores are filled in when the
  evidence does not state them. `null` means "not stated", never zero.
- **Models never decide.** Verification status, severity, review requirements and alert impact come
  from the rules below, never from model output, so a prompt injection in an article cannot escalate
  an incident.

## Sources and reliability

Each source has a reliability grade from **A** (consistently reliable) to **F** (reliability cannot be
judged), in the style of the Admiralty grading. The catalogue grades are **provisional defaults** set
from source type and ownership, not a measured track record; analysts should review them
(see [sources.md](sources.md)). Grades A–C count as "reliable" for the rules below.

## Ingestion and de-duplication

| Step | Rule | Code |
| --- | --- | --- |
| Canonical URL | Lower-case scheme and host, remove fragments and known tracking parameters (`utm_*`, `fbclid`, ...) only | `text/urls.py` |
| Exact duplicates | One article per canonical URL per organisation; identical text under another URL is linked to the earliest copy | `agents/ingestion.py` |
| Syndicated copies | SimHash (Hamming distance) plus shingle Jaccard on the text; a copy points to its earliest parent and shares its origin | `text/simhash.py`, `agents/ingestion.py` |
| Origin key | The source's origin group (newsroom or network); wire copies and near-duplicates inherit the parent's origin | `agents/ingestion.py`, `agents/incidents.py` |
| Time | Feed dates are parsed with the source's configured time zone when the feed omits an offset; everything is stored in UTC and shown in the user's time zone | `adapters/rss.py` |
| Language | Arabic and English are extracted directly; Persian, Hebrew, Turkish and French are detected and need translation (agent C) before extraction | `text/lang.py` |
| Untrusted text | Instruction-like sentences are flagged and removed before extraction; the article keeps its flags | `text/injection.py` |

## Event extraction

Rules (agent D, `agents/extraction.py`) extract, from the headline and the permitted excerpt:

- **Categories** from the editable event taxonomy (17 types, English and Arabic keywords, several per
  incident).
- **Place** through the gazetteer (129 entries: countries, cities, ports, facilities, maritime zones,
  one corridor) including Arabic names and aliases.
- **Casualties** only when a number is stated next to a casualty term; "no casualties" is recorded as 0.
- **Stance**: report, official statement, denial, correction, retraction or update.
- **Actors** (22 organisational actors) and **affected sectors**.

If a model is configured it may add a summary and suggest categories, but its output is validated:
categories must exist in the taxonomy, places must resolve through the gazetteer, and every number
or attribution must appear in the source text. Anything else is dropped.

### Does the item report a new event?

Event-type keywords say what an item is about, not whether it reports something that happened. On the
first day of live feeds (14 November 2026) many items with violence keywords reported no new incident,
so the rules (`text/events.py`, `agents/extraction.py: rule_extract`) check:

| Rule | Example from live feeds | Effect |
| --- | --- | --- |
| News-agency dateline | "Riyadh, November 09 (QNA) - The Coalition ..." | The filing city is not used as the location |
| Analysis, opinion, interview, explainer or question headline | "QNA Interview: Lebanon Min. Praises Qatar's Mediation Efforts" | No event categories |
| Reaction headline (condemns, welcomes, urges, calls for ...) | "Kuwaiti Cabinet Condemns Continued Houthi Attacks on Saudi Arabia" | Violence categories dropped unless the words before the reaction verb report an event; categories named only as the demand ("urge UK government to impose sanctions") dropped |
| Running tally | "UN says 25 seafarers killed in, around Hormuz since US-Israel war on Iran"; "Gaza Death Toll ... Rises to 74,645" | Violence categories dropped (an updated toll below 1,000 for one incident is kept) |
| No event word in the headline or the first two sentences | "Iraq expects first purchases of air defence systems to arrive soon" | Violence categories dropped |
| Headline without an event word and a single weak violence keyword in the text | "Newborn twins fight for survival as conflict fuels Yemen's hunger crisis" | Violence categories dropped (background) |

Violence categories are armed clash, drone/missile, political violence, terrorism, cross-border attack,
maritime incident and energy disruption. Event words are verbs and nouns such as attack, strike,
intercepted, explosion, killed, seized, clashes, caught fire or outage, with their Arabic stems.
A ceasefire violation ("Israeli attacks kill 6 ... in Gaza despite ceasefire") is an armed clash; only an
agreement, talks, an extension or a collapse of a ceasefire is a geopolitical development. Dropped items
stay stored, searchable and available to the AI analyst, and the reason is kept with the candidate.

### Where did it happen?

The gazetteer (`text/gazetteer.py: Gazetteer.resolve`) finds every place mentioned, then chooses the
event location:

1. A place introduced by a locative word ("in", "at", "near", "off", "over", "outside", "targeting",
   Arabic "في", "قرب", "داخل", "نحو" ...) beats a place that is only mentioned, and a place in the
   headline beats one in the text. A more precise place inside the chosen one is preferred ("Explosion
   reported in Saudi Arabia ... at the port in Jeddah" is placed in Jeddah).
2. "on" and "على" place an event only after an attack word ("strike on Gaza hospital"), not when they
   introduce a topic ("positions on Gaza", "sanctions on Israel"). "over" does not after words such as
   "row", "talks" or "sanctions". A place followed by a topic word ("Gaza genocide", "Yemen talks") or
   naming an agreement ("Makkah pact", "اتفاق الرياض") is not a location.
3. Country adjectives ("Israeli bombardment of Gaza Strip") and origins ("drone from Ethiopia") are used
   only when nothing else is named. A possessive is read as the place ("UAE's ADNOC").
4. **Outside the coverage area.** When the headline's first locative place (or, if the headline names
   no place, the first sentence's) is outside the coverage area, for example "drone activity over UK air
   base", the item is not placed on the map. 31 such places are listed in English and Arabic: the UK,
   the US, Russia, China, Türkiye, Pakistan, North African, European and Asian countries, and Europe.

### Relevance

| Where | Relevance |
| --- | --- |
| In a GCC country | 1.0 |
| Elsewhere in the coverage area, with a GCC country affected or named | 0.8 |
| A strategic waterway (Gulf waters, Red Sea, Arabian Sea) | 0.7 |
| Outside the coverage area, with a GCC country involved | 0.6 |
| Security event elsewhere in the coverage area (Iraq, Iran, Yemen, Israel and Palestine, Jordan, Lebanon, Syria, Egypt, the Horn of Africa) | 0.5 |
| Political or social news there without a GCC link | 0.3 |
| Outside the coverage area | 0.15 |
| No place resolved | 0.1 |

An item becomes an incident candidate at relevance 0.45 or more with at least one event category.

## Event matching

A new report joins an existing incident when the best match score in the matching window (default
72 hours) reaches the threshold (default 0.6). The score (`agents/incidents.py: match_score`) adds:

| Signal | Points |
| --- | --- |
| Same primary category 0.35; one report's primary category among the other's categories 0.30; otherwise the overlap share of categories | up to 0.35 |
| Same place 0.30; parent/child place 0.25 (0.15 via a country); within 75 km 0.25, 250 km 0.15; same country only 0.12 | up to 0.30 |
| Headline word overlap | up to 0.20 |
| Shared actor | 0.15 |
| Reported within 24 hours of the incident's latest report | 0.05 |
| Denial or correction near the same place and category | 0.10 |

**One physical event, one place, one time.** When either side has a violence category, the score is 0
if the report cannot describe the same physical event (`_physical_event_veto`):

- the places conflict: different countries, or two precise places (cities, ports, facilities, places with
  an uncertainty of 150 km or less) more than 100 km apart plus their uncertainty;
- the event times are more than 24 hours apart (the stated event time, otherwise the report time);
- both reports state a day, and the days differ;
- either place is broad (a country or a sea) and the headlines share less than 35% of their content
  words, unless both name the same sea or strait, with matching event types, within 6 hours.

Recurring attacks, such as interceptions over Riyadh and over Makkah, or strikes in Gaza on consecutive
days, therefore stay separate incidents; reports in English and Arabic of one tanker incident in the
Strait of Hormuz are grouped. The reason for every decision is stored with the candidate.

## Verification

`agents/verification.py: assess` evaluates the linked evidence in this order:

1. Every supporting origin retracted, none left: **unverified**.
2. A denial from any origin: **disputed**. Both versions stay in the evidence list.
3. Confirmed by an analyst: **confirmed**.
4. At least *N* independent live origins (default 2) and at least one of them reliable (A–C):
   **corroborated**. If the organisation allows automatic confirmation (off by default), at least
   3 origins with 2 reliable become **confirmed**.
5. At least one live origin and a reliable one: **single_source**.
6. Otherwise (only low-reliability or unknown sources): **unverified**.

Each incident also gets a credibility number derived mechanically from the status: 1 confirmed,
2 corroborated, 3 single source graded A/B, 4 single source graded C, 5 disputed, 6 unverified. It
is a summary of the status, not a separate analyst judgement.

## Severity

`agents/incidents.py: compute_severity`, on a 1–5 scale:

1. Start from the highest default severity among the incident's categories (taxonomy, editable).
2. Reported fatalities raise it to at least 3 (1–9), 4 (10–49) or 5 (50 or more). Ten or more
   injured raise it to at least 3.
3. +1 when the place is a facility or port and the category affects infrastructure.
4. +1 at a strategic chokepoint (Strait of Hormuz, Bab el-Mandeb, Suez Canal) for maritime, shipping
   or drone/missile categories.

The rationale is stored with the incident. An analyst can override the severity with a reason; the
computed value stays in the rationale.

## Human review

An incident goes to the review queue when any of these hold (thresholds editable per organisation):

- severity ≥ 4;
- single-source, unverified or disputed at severity ≥ 3;
- a category marked "always review" (terrorism and cross-border attacks by default);
- contradictory reporting, a publisher correction or retraction, or instruction-like text in the
  evidence.

Clients see incidents that are still awaiting review, marked "Awaiting analyst review"; rejected
incidents are hidden from them. Reviews, approvals and every change are written to the audit log.

## Corrections and versions

Each material change creates a new incident version with a snapshot, the changed fields, and who or
which agent made it (`incident_revisions`). Corrections come from two places:

- **Publishers.** A correction or retraction article updates the incident and sends it back to review.
  Retractions remove that origin from the corroboration count.
- **Analysts.** Overrides for title, summary, location, casualties, severity or categories, each saved
  with a note (a severity override also needs a reason).
  A later publisher correction to the same field replaces the analyst's value. Removing analyst
  overrides restores the publisher's corrections, or the values derived when the incident was created.

Subscribers who were alerted about an incident get a correction or retraction alert even if the
corrected incident no longer matches their rule.

## Geolocation

Incidents are placed only on gazetteer reference points or on coordinates the source itself states.

| Precision | Base confidence | Meaning |
| --- | --- | --- |
| `point` | 0.9 (source-reported) / 0.8 (border crossing) | Coordinates stated in the source, or a crossing |
| `facility`, `port` | 0.85 | A named facility or port |
| `city` | 0.8 | Gazetteer city point |
| `admin1` | 0.65 | Province or emirate centroid |
| `maritime_zone`, `corridor` | 0.6 | Sea area or corridor reference point |
| `country` | 0.5 | Country centroid |
| `unknown` | — | Not placed on the map |

Confidence drops by 0.2 when several equally specific places in different countries are mentioned and
by 0.15 when the place name is ambiguous, within 0.1–0.95. Coarse placements are drawn as areas, not
pins, and the map legend says so. Gazetteer points come from public reference gazetteers with an
uncertainty radius (`precision_km`).

## Risk indices (methodology v1.0)

Risk is computed per GCC country and dimension (political stability, armed conflict, civil unrest,
terrorism, maritime security, energy infrastructure, supply chain, regulatory policy, cybersecurity)
from stored incidents only, never from article volume or sentiment (`agents/risk.py`). The active
methodology is stored as a versioned record and shown in the app.

| Component | v1.0 |
| --- | --- |
| Window | 30 days, half-life 14 days |
| Observed severity S | Σ severity weight × verification weight × 0.5^(age / half-life) × country weight |
| Severity weights | 1, 2, 4, 8, 16 for severity 1–5 |
| Verification weights | confirmed 1.0, corroborated 0.85, single source 0.5, disputed 0.35, unverified 0.2 |
| Country weight | 1.0 where it happened; 0.5 for an affected country |
| Exposure E | Organisation watchlist multiplier (default 1.0); a client input, not data |
| Vulnerability V | Per-country multiplier in the methodology (1.0 everywhere in v1.0) |
| Score | 5 × (1 − e^(−S·E·V / k)), k = 24 (16 civil unrest, 12 regulatory policy and cybersecurity) |
| Bands | < 1 low, < 2 moderate, < 3 elevated, < 4 high, otherwise severe |
| Coverage rule | No approved, enabled source covering the country with a successful fetch in the last 3 days (and fewer than 3 consecutive failures): `insufficient_data`, no score |
| Confidence | High: ≥ 3 healthy sources and ≥ 60% of S from corroborated or confirmed incidents. Moderate: ≥ 2 and ≥ 30%. Otherwise low. At most moderate without incidents |

Severity (what happened), exposure, vulnerability and confidence are stored separately with the
per-incident contributions, so each score can be explained. Analysts can record an adjusted band with
a rationale; the computed score is kept next to it. **The indices describe recent reporting; they are
not forecasts.** Forecasting and trend modelling are on the [roadmap](roadmap.md).

## Alerts

`agents/alerts.py`:

- Rules match on countries (including affected countries and documented exposure pathways such as a
  Red Sea attack on Saudi trade), places, event types, sectors, keywords, minimum severity and
  verification statuses.
- **Impact** is high at severity ≥ 4 (editable), otherwise standard.
- **High-impact alerts wait for a reviewer** unless an admin turns that policy off (audited, with a
  warning). Standard alerts are sent automatically unless that is turned off.
- **De-duplication**: one alert per rule, incident, kind (new, update, correction, retraction) and
  incident version.
- **Throttling**: a rule's `throttle_minutes` allows one new standard alert per window; the rest are
  recorded as suppressed.
- **Backlog**: a new-incident alert is created for sending only when the incident was first reported
  within the last 24 hours (Administration → Policies, "Alert on incidents first reported within").
  Older incidents, for example from a new source's first fetch or a reprocessing run, are recorded once
  per rule as suppressed alerts and never sent as breaking news.
- **Follow-ups**: subscribers get an update when severity, verification status, fatalities or place
  changes, and always get corrections and retractions. An alert still waiting for review is refreshed
  to each new incident version (title, text and impact) instead of stacking a second one, so the
  reviewer approves what is current.
- **Delivery** per channel (email, Slack, Telegram, webhook) with up to 5 attempts, retried after 1,
  5, 15 and 60 minutes. Every attempt is logged with a masked destination.

## Briefs and citation integrity

Reports are written from stored incident records (agent H, `agents/briefing.py`), never from free web
text. Every paragraph cites numbered evidence. Before a report is saved, the integrity check verifies
that each citation points at an incident or article in the report's evidence set and that every figure
in the text appears in that evidence. A database trigger refuses publication when the check failed.
Reports record which incident versions they cite; publishing after a cited incident changed requires
the reviewer to acknowledge the changes, which is audited. If a model rewrites the executive summary,
the rewrite is accepted only if it passes the same check.

Citation numbers follow the order in which evidence first appears in the brief. A country section lists
the incidents in that country and, for incidents outside the GCC, those that name the country as
affected (an incident placed in Yemen that names Saudi Arabia as affected also appears under Saudi
Arabia). The
methodology note states how many monitored sources are limited to headlines, links and short excerpts
because their licences have not been reviewed.

## AI analyst

The analyst chat retrieves stored incidents and articles for the question and period (full-text
search in English and Arabic, under the user's row-level security), passes them to the model as
numbered, fenced untrusted data, and checks the answer like a report. If no model is configured, the
budget is used up, the model declines, or the answer fails the check, it lists the retrieved evidence
instead and says why. Requests to locate, profile or track a person are declined.

## Known limitations

- Extraction rules cover English and Arabic; other languages need a configured model for translation.
  Without a model there are no translations or summaries, so reports of one event in English and Arabic
  are grouped only when both name a precise place; country-level reports in two languages stay apart.
- The gazetteer is deliberately small (129 places). Places it does not know fall back to the country
  (Saada and Kordofan on 14 November) or leave an incident unplaced.
- **Corroboration counts outlets, not claimants.** Five outlets relaying one party's statement count as
  five origins: on 14 November the Strait of Hormuz tanker incident was "corroborated" mostly by reports
  of the same IRGC claim. Treat corroboration of a single claimant's statement with care until
  claimant-aware corroboration is built ([roadmap](roadmap.md)). Actor attribution by rules is noisy.
- Reliability grades are provisional. Coverage is biased towards English and Arabic outlets with
  feeds, and absence of reporting is not evidence of absence.
- The evaluation set is small and synthetic ([eval/README.md](../eval/README.md)); the live check on
  14 November was a manual review by the developer, not an analyst-labelled dataset.
