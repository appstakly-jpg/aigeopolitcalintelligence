# Evaluation

`gccintel eval` runs the real pipeline (ingestion, extraction, matching, verification, alerting and a
daily brief) on a labelled dataset in a throwaway organisation, then compares the results with the
labels. Every mismatch is listed under `failures`; nothing is filtered out.

```bash
cd workers && gccintel eval --out ../var/eval.json     # needs DATABASE_URL; removes its organisation afterwards
```

## Dataset

`workers/gccintel/data/eval_dataset.json`: **24 synthetic items describing 14 events**, from six
synthetic sources of different types and grades (wire, newspaper, Arabic outlet, maritime
organisation, government, social media). Items are in English and Arabic and include several reports
of the same event from different sources, an official statement, an official denial of a social-media
claim, off-topic stories and a prompt-injection attempt. Each item carries its expected relevance and,
where they apply, category, place, country, casualties, stance and actors; each event says whether the
evaluation alert rule (GCC countries, severity ≥ 3) should fire.

It was **written and labelled by the developers**, not drawn from real reporting or reviewed by
independent analysts, and the extraction rules were developed alongside it, so the scores are optimistic.
They show that the pipeline behaves as designed, not how it performs on real news. Replace or extend the
set with manually reviewed real incidents before relying on any number ([roadmap](../docs/roadmap.md)).

## Metrics

| Metric | Definition |
| --- | --- |
| Relevance precision and recall | Items kept vs items labelled relevant |
| Category recall | Expected category among the extracted categories |
| Location, country, casualty, stance accuracy | Exact match with the label, over items that have one |
| Clustering precision and recall | Pairwise: two items share an incident iff they share an event label |
| Alert precision, recall, false alert rate | Per event: alert created vs labelled `alert` |
| Citation correctness | The brief's integrity check passes, and each brief item cites only evidence linked to its own incident |
| Latency | Pipeline wall time and milliseconds per article |
| Cost | Model calls and USD from `llm_usage` |

## Latest results

14 November 2026, rules only (no model configured), PostgreSQL 16:

| Metric | Result |
| --- | --- |
| Relevance | precision 1.0, recall 1.0 (19 TP, 0 FP, 0 FN, 5 TN) |
| Category recall | 1.0 |
| Location / country / casualties / stance accuracy | 1.0 / 1.0 / 1.0 / 1.0 |
| Clustering | pairwise precision 1.0, recall 1.0 |
| Alerts | precision 1.0, recall 1.0, false alert rate 0.0 (3 TP, 0 FP, 0 FN, 11 TN) |
| Citations | integrity check passed; every brief item cites evidence of its own incident (19 citations) |
| Latency | 0.79 s for the run, 32 ms per article |
| Cost | 0 model calls, $0.00 |

No failures. The earlier failure (e15: "Houthi forces claim missile attack on ship in Red Sea" classified
as a maritime incident only) disappeared when attack phrases such as "missile attack" and "drone strike"
were added to the drone/missile type while working on live headlines. The rules keep being changed with
this set in view, so a perfect score here shows that the pipeline behaves as designed, not that it is
accurate on real news.

Not measured here: model-assisted extraction and translation, the AI analyst's answers, and
performance on real reporting. Those need an API key and an analyst-labelled real dataset.

## Live check on real feeds

On 14 November 2026 the pipeline ran on the 18 working public feeds ([docs/sources.md](../docs/sources.md))
without a model key. Of 570 articles stored by 10:20 UTC:

| Outcome | Articles |
| --- | --- |
| Kept as reports of security events in the coverage area | 35, grouped into 24 incidents (3 with several independent outlets) |
| No event category (sport, business, politics elsewhere ...) | 389 |
| Official travel advisories (shown on country pages, not incidents) | 107 |
| Event category but no place in the coverage area named (Microsoft outage, US flights, France, Ukraine ...) | 13 |
| Violence keywords but no new event: no specific event 8, running tally 4, reaction or demand 3, commentary or interview 3, background mention 3 | 21 |
| Political or social news in the region without a GCC link | 4 |
| Happened outside the coverage area | 1 |

Every kept item was checked by hand against its source article: each reports a real event that the
article describes, placed where the article says it happened (at country level when the gazetteer does
not know the place, such as Saada or Kordofan). Discarded items were spot-checked, not
reviewed one by one, so missed events are not measured; one known miss is a GCC statement welcoming a
UN Security Council resolution on the Houthis, dropped because it names no place. Each class of error
found during the check (datelines read as places, reactions and tallies turned into incidents, topics
read as places, events abroad placed in the region, separate attacks merged into one incident) is now a
regression test in `workers/tests/test_real_headlines.py`.

This was a check by the developer, not an analyst-labelled dataset. Building one from reviewed live
incidents is the first evaluation task on the [roadmap](../docs/roadmap.md).
