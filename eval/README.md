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

13 November 2026, rules only (no model configured), PostgreSQL 16:

| Metric | Result |
| --- | --- |
| Relevance | precision 1.0, recall 1.0 (19 TP, 0 FP, 0 FN, 5 TN) |
| Category recall | **0.947** |
| Location / country / casualties / stance accuracy | 1.0 / 1.0 / 1.0 / 1.0 |
| Clustering | pairwise precision 1.0, recall 1.0 |
| Alerts | precision 1.0, recall 1.0, false alert rate 0.0 (3 TP, 0 FP, 0 FN, 11 TN) |
| Citations | integrity check passed; every brief item cites evidence of its own incident (19 citations) |
| Latency | 0.45 s for the run, 18 ms per article |
| Cost | 0 model calls, $0.00 |

**Failure (1):** `e15: category drone_missile not in ['maritime_incident']`. "Houthi forces claim
missile attack on ship in Red Sea" is classified as a maritime incident but not as a drone/missile
attack. Its only drone/missile cue is the single word "missile" (weight 1), which falls below the bar
for a secondary category next to the stronger maritime cues ("merchant ship", "vessel"; score 3). The
incident is still created, placed in the Red Sea and linked to the right event, but without the
category it is missed by alert rules that list drone/missile but not maritime incidents and does not count towards the
armed-conflict and energy-infrastructure risk dimensions. It is left visible rather than patched to
the test.

Not measured here: model-assisted extraction and translation, the AI analyst's answers, and
performance on real reporting. Those need an API key and an analyst-labelled real dataset.
