# Chapter 4 reconciled numbers

This is the numerical source of truth for `Chapter_4_revised.md`. “Estimated” denotes a locally prepared token/cost estimate; “measured” denotes API usage fields or persisted evaluator output. USD figures use the rates stored with the experiment, not an invoice. The read-only reproduction command is:

```bash
.venv/bin/python tools/audit_chapter4_artifacts.py --include-pdf-pages
```

| Category | Stage / measure | Reconciled value | Status | Authoritative source |
|---|---|---:|---|---|
| Corpus | PDFs evaluated | 107 | Reproduced | `accidents/*.pdf`; `tools/audit_chapter4_artifacts.py` |
| Corpus | Physical PDF pages | 1,761 | Reproduced with current `pypdf` | `tools/run_maib_extraction_pilot.py:inventory_pdfs`; audit script `--include-pdf-pages` |
| Corpus | Pages with at least 80 extracted characters | 1,724 | Reproduced | Same as above |
| Corpus | Pages with indexed chunks | 1,622 | Reproduced | `accidents/index.jsonl` |
| Corpus | Character-eligible pages without a chunk | 102 | Reproduced; all contain 16–76 words | PDF inventory joined to `accidents/index.jsonl` |
| Corpus | Indexed chunks / unique chunk IDs | 2,373 / 2,373 | Reproduced | `accidents/index.jsonl` |
| Corpus | Mean / median chunk length | 281.188 / 307 words | Reproduced | `accidents/index.jsonl` |
| Corpus | Minimum / maximum chunk length | 80 / 400 words | Reproduced | `accidents/index.jsonl`; `tools/ingest_maib.py:chunks` |
| Corpus | Median / range of chunks per report | 20 / 1–145 | Reproduced | `accidents/index.jsonl` |
| Corpus | Pages with multiple chunks / maximum chunks on one page | 750 / 3 | Reproduced | `accidents/index.jsonl` |
| Corpus acquisition | Search-result PDFs stated in draft | 147 | **Not verifiable** | No dated search log or exclusion register found; omit from thesis claim |
| Pilot selector | Analysis-marker / navigation-marker / collision weights | 5 / 3 / 1 capped at 3 | Verified fixed heuristic | `tools/build_maib_lesson_cards.py:passage_score` |
| Pilot selector | Passage cutoff / zero-score fallback / card cap | 8 / first 2 / 3 | Verified fixed heuristic | `select_passages`; `LessonCardResponse` |
| Pilot extraction | Report requests | 107 | Reproduced | `accidents/cards/batch_input.jsonl` |
| Pilot extraction | Candidates / accepted / rejected | 259 / 255 / 4 | Reproduced | `accidents/cards/cards.machine_validated.jsonl`; `cards.rejected.jsonl` |
| Pilot extraction | Reports represented / verified quotations | 103 / 647 | Reproduced | `accidents/cards/cards.machine_validated.jsonl` |
| Pilot extraction | Prompt / completion tokens | 433,356 / 71,469 | Measured | `accidents/cards/batch_output.jsonl`, response usage |
| Pilot extraction | Implied Batch cost | USD 0.790701 | Calculated at 1/5 USD per million | Same output; `accidents/cards/cost_estimate.json` rates |
| Development appraisal | Encounters / pairs / unique passages | 5 / 10 / 4 | Verified | `tools/test_maib_chunk_applicability.py:DEFAULT_SAMPLES`; final `summary.json` and `results.jsonl` |
| Development appraisal | Pairs labelled useful / not useful | 0 / 10 | Model rubric output | `reports/maib_chunk_applicability/20261115T131345Z/summary.json` |
| Development extraction | Example states / unique passages | 5 / 4 | Verified | `tools/test_maib_decision_style_extraction.py`; `summary.json` |
| Development extraction | Passages producing cards / producing none / cards | 2 / 2 / 7 | Model output plus deterministic schema/quote validation | `reports/maib_decision_style_extraction/20261115T131914Z/summary.json` |
| Per-passage plan | Prepared requests | 2,373 | Prepared, not submitted | `accidents/cards_decision_style_all_chunks/batch_input.jsonl` |
| Per-passage plan | Estimated input / maximum output tokens | 24,388,883 / 9,492,000 | Estimated | `accidents/cards_decision_style_all_chunks/cost_estimate.json` |
| Per-passage plan | Maximum estimated Batch cost | USD 71.848883 | Estimated, not spent | Same as above |
| Screening plan | Prepared report requests / estimated input tokens | 107 / 1,177,489 | Prepared, not used in final pipeline | `accidents/cards_gpt_screened/screening/cost_estimate.json` |
| Screening plan | Maximum estimated Batch cost | USD 7.597489 | Estimated, not spent as final stage | Same as above |
| Final preparation | Report requests / chunk references / unique IDs | 107 / 2,373 / 2,373 | Reproduced | `accidents/cards_decision_style_report_level/{batch_input,manifest}.jsonl` |
| Final preparation | Exact reconstructed request payload matches | 107/107 | Reproduced | Frozen request and manifest; construction in `generation_payload` |
| Final preparation | Estimated input tokens | 1,242,217 | Pre-run estimate | `accidents/cards_decision_style_report_level/cost_estimate.json` |
| Final preparation | Maximum estimated Batch cost | USD 7.662217 | Pre-run maximum, not actual | Same as above |
| Initial extraction | HTTP / Batch responses | 107 / 107 | Verified | `validation_summary.json`; `batch_output.jsonl` |
| Initial extraction | Parseable report responses / response failures | 106 / 1 | Verified | `validation_summary.json` |
| Initial extraction | Length-limited report | 1 (`accidents/pdf127.pdf`) | Verified | `cards.rejected.jsonl`; response `finish_reason="length"` |
| Initial extraction | Reports with accepted cards / empty arrays | 102 / 4 | Verified | `validation_summary.json` |
| Initial extraction | Candidates / source-valid / rejected candidates | 1,015 / 998 / 17 | Reproduced | Validated/rejected JSONL and audit script |
| Initial extraction | Rejections with one / two unmatched quotes | 13 / 4 | Reproduced | `cards.rejected.jsonl` |
| Initial extraction | Verified quotations | 2,067 | Reproduced | `cards.machine_validated.jsonl` |
| Initial extraction | Prompt / completion / total tokens | 1,015,668 / 295,521 / 1,311,189 | Measured | `batch_output.jsonl`; `validation_summary.json` |
| Initial extraction | Implied cost | USD 2.493273 | Calculated at stored 1/5 rates | `validation_summary.json` |
| Failed `pdf127` response | Prompt / completion tokens | 7,522 / 12,000 | Measured; completion all reasoning tokens | Initial `batch_output.jsonl` |
| Retry | Indexed chunks / completion limit | 19 / 20,000 | Verified | Retry manifest and `batch_input.jsonl` |
| Retry | Candidates / source-valid / rejected | 3 / 2 / 1 | Reproduced | Retry validated/rejected JSONL |
| Retry | Verified quotations | 5 | Reproduced | Retry `cards.machine_validated.jsonl` |
| Retry | Prompt / completion / total tokens | 7,522 / 1,165 / 8,687 | Measured | Retry Batch output; `validation_summary.json` |
| Retry | Implied cost | USD 0.013347 | Calculated at stored 1/5 rates | Retry `validation_summary.json` |
| Combined extraction | Candidates / source-valid / candidate rejections | 1,018 / 1,000 / 18 | Reconciled | Initial plus retry artifacts |
| Combined extraction | Verified quotations | 2,072 | Reconciled | 2,067 + 5 |
| Page chronology | Pre-correction cards with unquoted declared pages | 127 | Reproduced from frozen curation input | `accidents/cards_decision_style_curated/manifest.jsonl` |
| Page chronology | Pre-correction unquoted page claims | 128 | Reproduced | Same manifest |
| Page chronology | Unquoted declared pages in current validated/retained files | 0 | Reproduced after correction | Initial validated file; retained file |
| Curation preparation | Report groups / source cards | 102 / 998 | Verified | Curation manifest and cost estimate |
| Curation preparation | Model / effort / completion limit | `gpt-6.1-sol` / low / 8,000 | Verified | `accidents/cards_decision_style_curated/batch_input.jsonl` |
| Curation | Decisions / unique partition IDs | 998 / 998 | Reproduced | Decisions and three disposition outputs |
| Curation | Retained / review / excluded | 503 / 462 / 33 | Reproduced | Curation outputs and `audit.json` |
| Curation | `direct` / `adjacent` / `out_of_scope` | 657 / 315 / 26 | Model rubric labels | `summary.json` |
| Curation | `strong` / `partial` / `weak` | 750 / 241 / 7 | Model rubric labels | `summary.json` |
| Curation | `safe` / `review` / `reject` | 985 / 13 / 0 | Model rubric labels | `summary.json` |
| Curation | Non-null duplicate labels | 0 | Model procedure result, not proof of absence | `curation_decisions.jsonl`; `summary.json` |
| Curation | Prompt / completion / total tokens | 295,812 / 87,455 / 383,267 | Measured | Curation responses in combined Batch output |
| Curation | Implied cost | USD 0.733087 | Calculated at stored 1/5 rates | Combined Batch output plus configured rates |
| Strict corpus | Cards / represented reports | 503 / 101 | Reproduced | `cards.retained.jsonl` |
| Strict corpus | Verified quotations / report-page pairs | 1,008 / 386 | Reproduced | `cards.retained.jsonl` |
| Final submitted stages | Prompt / completion tokens | 1,319,002 / 384,141 | Reconciled | Initial + retry + curation measured usage |
| Final submitted stages | Implied total cost | USD 3.239707 | Reconciled | 2.493273 + 0.013347 + 0.733087 |
| Intended retrieval | Query unit / candidate unit | Narrative bridge state / lesson card | Verified design and runtime path | `agents/agent_captain.py:retrieve_evidence`; `rag/maib.py` |
| Intended retrieval | Pilot runtime corpus | 255 cards / 103 reports | Verified | `accidents/cards/index.jsonl` |
| Intended retrieval | Operational output cutoff | Top 2 cards, at most one per report | Fixed | `experiments/maib_lesson_cards_pilot.ini`; `MaibRetriever.retrieve` |
| Intended retrieval | Fixed diagnostic state descriptions | 4 | Unlabelled; no effectiveness metrics | `tools/benchmark_maib.py:QUERIES` |
| Intended retrieval | Labelled state–card relevance judgements | 0 preserved | **Not evaluated** | No benchmark artifact found |
| Intended retrieval | Comparative BM25/BGE/RRF/reranker result | None | **Not evaluated** | Cannot be inferred from source-page recovery |
| Secondary source-page diagnostic, pilot | Queries / report clusters | 255 / 103 | Not the intended retrieval task | Legacy `retrieval_metrics.json` and details |
| Secondary source-page diagnostic, pilot | BM25 metrics (H1/H3/H5/R5/MRR/nDCG5) | .318/.698/.863/.698/.536/.518 | Reproduced diagnostic | `reports/maib_extraction_legacy_benchmark/retrieval_metrics.json` |
| Secondary source-page diagnostic, pilot | BGE / RRF / cross-encoder H5 | .745 / .812 / .835 | Reproduced diagnostic | Same as above |
| Secondary source-page diagnostic, strict | Card rows / report clusters | 503 / 101 | Not the intended retrieval task | Final retrieval metrics/details |
| Secondary source-page diagnostic, strict | BM25 metrics (H1/H3/H5/R5/MRR/nDCG5) | .139/.423/.652/.561/.349/.355 | Reproduced diagnostic | `reports/maib_extraction_pilot/retrieval_metrics.json` |
| Secondary source-page diagnostic, strict | BGE / RRF / cross-encoder H5 | .565 / .592 / .604 | Reproduced diagnostic | Same as above |
| Secondary source-page diagnostic, strict | Reranker-minus-RRF deltas (H5/MRR/nDCG5) | +.01193 / -.01250 / -.01383 | Reproduced diagnostic | Same as above |
| Simulation, warp 5 | Raw-passage condition composite / verdicts | .705090; 12 pass, 9 clearance violations, 1 collision | One rollout; non-equivalent architecture | Frozen raw score artifact |
| Simulation, warp 5 | Raw axes (S/C/Cit/T/Sp/M/E) | .682268/.718182/.004142/.819529/.829844/1.000000/.986207 | Verified | `reports/imazu_frozen_v1/maib_rag_independent_model_only_warp5/scores.json` |
| Simulation, warp 5 | 255-card condition composite / verdicts | .717124; 13 pass, 7 clearance violations, 2 collisions | One rollout; non-equivalent architecture | Frozen lesson-card score artifact |
| Simulation, warp 5 | Card axes (S/C/Cit/T/Sp/M/E) | .747168/.696364/.000947/.850196/.866083/.956450/.988699 | Verified | `reports/imazu_frozen_v1/maib_lesson_cards_captain_review_prompt_fixed_full_warp5/scores.json` |
| Simulation, lock-step | Base composite / verdicts | .828235; 17 pass, 5 clearance violations | 22 cases, lockstep-v2 | Base `scores_v2/scores.json` |
| Simulation, lock-step | Base axes (S/C/Cit/T/Sp/M/E) | .928393/.677273/.020115/.863263/.994977/1.000000/.984897 | Verified | Same as above |
| Simulation, lock-step | Raw-MAIB critic composite / verdicts | .825105; 22 pass | 22 cases, lockstep-v2 | MAIB critic `scores_v2/scores.json` |
| Simulation, lock-step | Raw-MAIB critic axes | 1.000000/.563636/.001166/.884405/.976157/.831160/.996353 | Verified | Same as above |
| Critic no-evidence ablation | Complete / cycle-limit / missing-result cases | 17 / 3 / 2 | Audit snapshot 2026-11-15 21:50 CET; no 22-case aggregate | `logs/evaluations/imazu/maib_critic_no_evidence_lockstep_30s_warp20/case_*/result.json` |
| Simulation coverage | Closed-loop run with final 503 cards | 0 | Not performed | No runtime index or manifest matches final retained-card artifact |

## Cost boundary

The USD 3.239707 total covers only the initial final extraction, the `pdf127` retry, and curation at configured Batch rates. It excludes development calls, the USD 0.790701 lexical pilot, unsubmitted per-passage and screening workloads, local PDF/embedding/retrieval compute, simulations, storage, tax, and any difference between the configured rates and provider billing.

## Simulation nomenclature

The score artifacts use `PASS_WITH_CPA_VIOLATION`, but the scorer derives this verdict from minimum realised separation below the study-specific 500 m threshold, not from a predicted closest point of approach. The tables above use “clearance violation” to avoid overstating the field name.
