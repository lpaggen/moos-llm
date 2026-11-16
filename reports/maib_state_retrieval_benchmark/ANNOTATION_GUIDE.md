# Human relevance annotation guide

This sheet contains 137 unique state–card pairs pooled from four retrieval methods. The rows are deduplicated and deterministically shuffled with seed 20261116. The sheet does not identify the retrieval method, rank, MAIB report, scenario number, or card identifier. Do not open `private_pool_key.jsonl` until annotation is complete.

Enter exactly one value in `relevance_label` for every row:

- **2 — Relevant:** the investigator finding addresses a mechanism present in the navigation state, and the lesson's applicability conditions are satisfied.
- **1 — Partially relevant:** the finding or lesson addresses a related mechanism, but an important circumstance differs or is unknown.
- **0 — Irrelevant:** the card does not provide applicable reasoning for this encounter.

Judge transfer to the observed state, not shared words such as “collision”, “crossing”, or “overtaking”. Treat unobserved circumstances as unknown; do not invent them. Use `reviewer_notes` for difficult or borderline decisions. Do not alter the other columns or reorder/delete rows.

After labelling, run:

```bash
.venv/bin/python tools/evaluate_maib_state_retrieval.py score \
  --output-dir reports/maib_state_retrieval_benchmark \
  --labels reports/maib_state_retrieval_benchmark/relevance_labels.csv
```
