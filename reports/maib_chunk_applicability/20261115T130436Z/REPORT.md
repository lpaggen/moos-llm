# GPT audit-state/chunk applicability test

Model: `gpt-6.1-sol`

Validated judgments: **7**  
Meaningfully applicable: **0**  
Rejected as not meaningful: **7**  
Failed API or local validation: **3**

## Passage-type judgments

| Passage type | Count |
|---|---:|
| background_or_method | 6 |
| mixed | 1 |

## Pair-level results

| Audit pair | Meaningful | Usefulness | Confidence | Extracted lesson |
|---|---|---|---:|---|
| case_01-cycle_44-maib-2891026537f2 | False | none | 0.99 | — |
| case_02-cycle_47-maib-2891026537f2 | False | none | 0.99 | — |
| case_13-cycle_55-maib-2891026537f2 | False | none | 0.99 | — |
| case_13-cycle_55-maib-edc53abc1ec8 | False | none | 0.99 | — |
| case_16-cycle_81-maib-2891026537f2 | False | none | 0.99 | — |
| case_16-cycle_81-maib-d272bb7dbf43 | False | none | 0.99 | — |
| case_20-cycle_74-maib-2891026537f2 | False | none | 0.99 | — |

The prior captain evidence verdict is retained in `results.jsonl` for comparison but was not shown to GPT.
Every accepted card passed exact page and quotation checks against the supplied chunk.
