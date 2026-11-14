# Sources

## Policy

- **Public, lawful sources only**: RSS/Atom feeds and documented public APIs. No private messages,
  private accounts, closed groups or personal location data.
- **No access-control bypass**: robots.txt is honoured, requests are throttled per host, and
  401/402/403/429 responses are recorded as failures, never worked around. No paywall, login or
  rate-limit circumvention, and no assumption that a platform offers an API.
- **Licences before use**: a source is fetched only after an admin approves it. Each source carries a
  licence type (`public_domain`, `open_license`, `attribution`, `licensed`, `review_required`,
  `prohibited`) and a usage policy that limits what is stored:
  - `metadata_only`: headline, link and dates;
  - `headline_link_excerpt` (default): plus a short excerpt (320 characters by default);
  - `full_text`: only for sources whose licence allows it.
  A `prohibited` source can never be enabled (database constraint).
- **Attribution**: every incident, report and chat answer links to the original article and does not
  reproduce more of its text than the stored excerpt.
- **Social media**: none is ingested in this version. If added, only permitted public endpoints of
  official accounts, under the platform's terms.
- **ACLED**: not used. The platform is not affiliated with ACLED and contains no ACLED data; licensed
  datasets can be added only with an appropriate licence.

## Reliability grades

Each source has a grade from A to F. The catalogue grades are **provisional defaults** based on
source type and ownership, with the rationale stored in the catalogue; they are not a measured track
record. Analysts should review them under Sources. State media and official channels are graded as
attributable sources: their statements are recorded as claims of the stated party (see
[methodology.md](methodology.md#verification)).

## Catalogue

`workers/gccintel/data/sources.json` lists 21 candidate sources. New organisations get them as
**proposed and disabled**; an admin reviews the terms and approves each one.

"Live result" is what happened when the platform fetched each feed on 14 November 2026 from the
development machine, first with `gccintel sources-verify` (robots.txt, reachability, parsing, dates) and
then on the normal schedule. 18 of the 21 feeds worked and stored 568 articles in the first hours. Run
the check again from your deployment network, whose outbound address may be treated differently.

| Source | Language | Type | Grade | Licence | Live result (14 Nov 2026) |
| --- | --- | --- | --- | --- | --- |
| Al Jazeera English (all news) | en | news media | B | review required | works |
| Al Jazeera Arabic (الجزيرة نت) | ar | news media | B | review required | works |
| Arab News | en | news media | C | review required | **HTTP 403** from the publisher's bot protection; left proposed |
| Asharq Al-Awsat (الشرق الأوسط) | ar | news media | C | review required | works |
| The National (UAE) | en | news media | C | review required | works |
| Middle East Eye | en | news media | C | review required | works |
| Qatar News Agency, Qatar | en | state media | C | review required | works (times read as Doha time, see notes) |
| Qatar News Agency, General | en | state media | C | review required | works (times read as Doha time) |
| Anadolu Agency, Middle East | en | wire agency | C | review required | works |
| IAEA Top News | en | international organisation | B | review required | works (dates without an offset read as Vienna time) |
| BBC News, Middle East | en | news media | B | review required | works |
| UN News, Middle East | en | international organisation | B | review required | **robots.txt disallows** automated fetching; left proposed |
| US State Department travel advisories | en | government | B | public domain | works (travel advisories) |
| UK FCDO travel advice: Saudi Arabia, UAE, Qatar, Kuwait, Bahrain, Oman, Yemen (7 feeds) | en | government | B | Open Government Licence v3.0 | all 7 work (travel advisories) |
| GDELT DOC 2.0 (GCC article list) | en | aggregator | F | attribution (GDELT); articles keep their own rights | **HTTP 429** (rate limit on the shared outbound address); approved, retried on schedule |

Notes:
- **Limited use until licences are reviewed.** Twelve catalogue sources are `review_required`. The ten
  of them that work (news, agency and IAEA feeds) were approved in the live test for limited use only:
  headline, link and an excerpt of at most 320 characters. A source whose licence has not been reviewed cannot be set to
  full text (admin action and database constraint), and briefs say how many of their sources are in
  this state. Review each publisher's terms before commercial use: BBC feeds are for personal,
  non-commercial use unless licensed, and Anadolu's wire content normally needs a licence.
- **Arab News** answers automated clients with HTTP 403 from its bot protection (an external fetch on
  13 November worked). This is an access control and is not worked around: enable the source only
  with the publisher's permission or a licensed feed.
- **UN News**: its robots.txt disallows generic crawlers on the feed path, so the source stays
  disabled. For UN humanitarian updates, the ReliefWeb API is on the [roadmap](roadmap.md).
- **GDELT** allows one request per 5 seconds per address and returned HTTP 429 to the development
  machine's shared outbound address. The adapter paces its requests, honours `Retry-After` and backs
  off; check it again from the deployment network. GDELT is graded F because it is an index of other
  publishers' articles: each linked article is assessed through its own publisher.
- **Feed times.** QNA's feeds label Doha local time as UTC, so their stated offset is ignored and times
  are read as `Asia/Qatar` (`ignore_feed_offset` in the catalogue entry). IAEA dates without an offset
  are read as `Europe/Vienna`. Publication dates in the future are rejected.
- **Travel advisories.** The US State Department and UK FCDO feeds have the content kind
  `travel_advisory`: they are stored but not turned into incidents. The latest advisory from each
  source is shown on the country page under "Official travel advisories".

### References consulted

- Al Jazeera RSS feed addresses: the feeder.co knowledge base,
  <https://feeder.co/knowledge-base/rss-content/al-jazeera-rss-feeds/>.
- Qatar News Agency feeds: QNA's RSS page, <https://qna.org.qa/en/rss-feeds/>.

These pages were consulted to find the feed addresses; the pages themselves are not ingested.

## Adding a source

1. **Sources → Add from catalogue**, or **Add custom source** with its name, publisher, RSS/Atom URL,
   adapter, language, source type and terms URL. A custom source starts as proposed, disabled,
   graded F, `review_required` and `headline_link_excerpt`, with a review task and an automatic
   technical check.
2. **Assess it**: reliability grade with a rationale, usage policy and excerpt length, fetch interval
   (5–1440 minutes), licence type and notes (licence changes are admin-only), and the **origin group**.
   Outlets of the same network or newsroom must share an origin group so they count once for
   corroboration; a source without one is its own origin.
3. **Technical check** (button on the source, or `gccintel sources-verify --tenant <slug>`): robots.txt,
   reachability, parsing and dated items. Source Scout records the result; it never approves or
   enables a source.
4. **Approve and enable** (admins) once the licence and terms are reviewed. Every step is audited.
5. Watch the health log: consecutive failures back off the schedule and show on the Sources page and in
   the quality audit.

Country focus, priority and a feed time zone come from the catalogue entry; for custom sources they
are not yet editable in the app ([roadmap](roadmap.md)).
