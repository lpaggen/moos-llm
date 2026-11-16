# Final MAIB lesson-card experiment

The Chapter 4 experiment has three data-processing stages and one offline benchmark:

1. **report-level extraction:** all 2,373 indexed chunks are regrouped into their 107
   reports and supplied to `gpt-6.1-sol`; no lexical score or fixed passage cap selects
   the input;
2. **source validation:** generated quotations are matched against their claimed pages;
3. **semantic curation:** all 998 initially valid cards are labelled for decision
   relevance, grounding quality, transfer safety, and within-report duplication; and
4. **retrieval evaluation:** the strict 503-card retained set is used to compare BM25,
   BGE dense retrieval, RRF, and cross-encoder reranking.

The overlapping chunks predate the final extraction experiment. Because every chunk
is recombined by report, chunking does not reduce extraction context or select evidence;
it reuses the frozen page-linked retrieval index and introduces duplicated boundary
text.

## Validate the final downloaded file

The downloaded file contains 102 curation responses and the separate `pdf127` retry
response. Each validator selects the response identifiers belonging to its own
manifest:

```bash
.venv/bin/python tools/curate_maib_lesson_cards.py validate \
  --work-dir accidents/cards_decision_style_curated \
  --batch-output reports/maib_extraction_pilot/batch_6af9cb13553081908595ed7f9b27fd6d_output.jsonl \
  --allow-unrelated-output

.venv/bin/python tools/curate_maib_lesson_cards.py audit \
  --work-dir accidents/cards_decision_style_curated

.venv/bin/python tools/build_maib_lesson_cards_gpt_screened.py validate-generate \
  --work-dir accidents/cards_decision_style_report_level_retry_pdf127 \
  --batch-output reports/maib_extraction_pilot/batch_6af9cb13553081908595ed7f9b27fd6d_output.jsonl \
  --allow-unrelated-output
```

The curation audit must report 998 decisions, 503 retained cards, 462 review cards, 33
excluded cards, no failed responses, and `audit_passed: true`.

The retry returned three candidates: two passed quotation validation and one failed.
Because those two cards were not present when the curation Batch was prepared, they are
recorded separately and are not silently inserted into the strict 503-card corpus.

## Rerun the final retrieval benchmark

```bash
.venv/bin/python tools/run_maib_extraction_pilot.py \
  --cards accidents/cards_decision_style_curated/cards.retained.jsonl \
  --output-dir reports/maib_extraction_pilot
```

The authoritative benchmark artifacts are:

- `reports/maib_extraction_pilot/retrieval_metrics.json`;
- `reports/maib_extraction_pilot/retrieval_details.jsonl`;
- `reports/maib_extraction_pilot/reasoning_records.jsonl`;
- `reports/maib_extraction_pilot/provenance.json`; and
- `reports/maib_extraction_pilot/PILOT_RESULTS.generated.md`.

The final corpus contains 503 cards from 101 reports and 1,008 verified quotations.
BM25 is strongest overall (Hit@5 0.652; reference-page recall@5 0.561). The
cross-encoder changes RRF Hit@5 by +0.012, with a 95% report-cluster bootstrap interval
of [-0.055, +0.075], and does not demonstrate a reliable improvement.
