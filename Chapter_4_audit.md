# Chapter 4 implementation audit

## Scope and status convention

This audit compares the former Chapter 4 claims with repository code and artifacts. **FIXED** means the revised chapter corrects an inaccurate, incomplete, repetitive, or unsupported statement. **VERIFIED** means the material claim was reproduced without a substantive discrepancy. **UNRESOLVED** means the repository lacks evidence needed to reconstruct the requested fact; the revised chapter makes only the narrower supported claim. Paths are relative to the repository root.

1. **FIXED — Retrieval task framing.** The intended task is global lesson-card retrieval from a narrative bridge state. `agents/agent_captain.py:retrieve_evidence` builds that state-derived query and calls `rag/maib.py:MaibRetriever` over the card index. The generic-query, known-report page task in `tools/run_maib_extraction_pilot.py` is a separate extraction diagnostic and is no longer presented as the retrieval experiment.

2. **FIXED — BM25 recommendation.** `reports/maib_extraction_pilot/retrieval_metrics.json` cannot select a method for narrative-state-to-card retrieval because its queries, candidates, targets, and units are different. The chapter makes no retrieval-method recommendation without state–card relevance labels.

3. **FIXED — Page-validation chronology.** Initial validation in `tools/build_maib_lesson_cards.py:validate_generate` checked that every claimed page existed and every quotation matched its claimed page; it did not require every declared page to have a quotation. The frozen pre-correction input `accidents/cards_decision_style_curated/manifest.jsonl` contains 127 cards with 128 unquoted page claims. The later assignment `record["pages"] = sorted({quote pages})` and curation normalisation establish full declared-page coverage only after correction. The chronology is stated in §4.3.2.

4. **VERIFIED — Extraction, retry, and curation denominators.** Reproduced from `accidents/cards_decision_style_report_level/{batch_output.jsonl,cards.machine_validated.jsonl,cards.rejected.jsonl}`, `accidents/cards_decision_style_report_level_retry_pdf127/{cards.machine_validated.jsonl,cards.rejected.jsonl,validation_summary.json}`, and `accidents/cards_decision_style_curated/{manifest.jsonl,cards.retained.jsonl,cards.review.jsonl,cards.excluded.jsonl}`: 107/106 responses, 1,015/998/17 initial candidates, 3/2/1 retry candidates, 1,000 total source-valid, and curation of the initial 998 into 503/462/33. The revised chapter presents the timeline once and preserves the initial-versus-combined distinction.

5. **FIXED — Meaning of curation labels.** Exact prompt labels are in `tools/curate_maib_lesson_cards.py:CURATION_SYSTEM_PROMPT`. The chapter defines `strong` and `safe` as model-assigned rubric values based on a card and its selected quotations, not independent expert findings or safety certification.

6. **VERIFIED — Curation decision rule.** `tools/curate_maib_lesson_cards.py:disposition` applies: non-null duplicate → exclude; `direct` + `strong` + `safe` → retain; otherwise any `out_of_scope`, `weak`, or `reject` → exclude; all other combinations → review. `validate` requires exact ordered IDs and `audit` checks an exhaustive, disjoint partition. Outputs reproduce 503 retained, 462 review, and 33 excluded.

7. **FIXED — Duplicate claim.** `duplicate_of` was model-generated under `CURATION_SYSTEM_PROMPT`, limited to an earlier card in the same report, and validated by `validate`. All 998 decisions have null `duplicate_of`. The chapter reports zero duplicates *identified by that procedure*, not absence of semantic duplicates.

8. **FIXED — Closed-loop attribution.** `logs/evaluations/imazu/base_narrative_lockstep_30s_warp20/manifest.json` and `.../maib_rag_captain_critic_lockstep_30s_warp20/manifest.json` compare a one-call base with an OOW-plus-Captain evidence pipeline. The chapter treats architecture and evidence as confounded. The no-evidence campaign is incomplete and has no 22-case aggregate; its count is reported as a timestamped audit snapshot rather than the stale 11/22 claim.

9. **FIXED — Navigation-improvement claim.** Frozen score artifacts under `reports/imazu_frozen_v1/maib_rag_independent_model_only_warp5/` and `.../maib_lesson_cards_captain_review_prompt_fixed_full_warp5/` give composite 0.705 versus 0.717, verdicts 12/9/1 versus 13/7/2, and lower compliance in the card condition. The latter used `accidents/cards/index.jsonl` (255 pilot cards), not the final retained corpus. The result is reported as mixed and noncausal.

10. **FIXED — Pilot/final score explanation.** Legacy and final `retrieval_metrics.json` files reproduce BM25 Hit@5 0.863 and 0.652. Because selection, card population, target pages, and curation changed, the chapter labels the studies non-equivalent and presents lexical-selection bias only as a hypothesis.

11. **UNRESOLVED — Corpus acquisition criteria.** The local corpus contains 107 PDFs, but no dated search log, query/filter record, duplicate register, or auditable 147-to-107 exclusion list was found. **[NEEDS EVIDENCE: dated MAIB search export and per-file inclusion/exclusion register.]** The revised chapter uses the 107 local files as its reproducible boundary and does not assert that 147 was reproduced.

12. **VERIFIED — Page counts and loss.** `tools/audit_chapter4_artifacts.py` opens all PDFs and reproduces 1,761 physical pages, 1,724 pages with at least 80 extracted characters, and 1,622 pages represented in `accidents/index.jsonl`. The 102 character-eligible but unindexed pages contain only 16–76 whitespace-delimited words, below the separate 80-word chunk threshold.

13. **UNRESOLVED — PDF extraction provenance.** `tools/ingest_maib.py:main` verifies `pypdf.PdfReader`, default `page.extract_text()`, one-based physical pages, whitespace collapse, no OCR, no table reconstruction, and no explicit Unicode normalisation; the audit found no open failure. The exact build-time library version was not recorded (current environment: 6.18.0). **[NEEDS EVIDENCE: frozen dependency lock or ingestion manifest containing the original `pypdf` version.]**

14. **VERIFIED — Chunk algorithm.** `tools/ingest_maib.py:chunks` uses whitespace-split words, maximum 400, overlap 60, stride 340, and inclusive 80-word minimum; short pages emit nothing and chunks do not cross pages. The digest construction in `tools/ingest_maib.py:main` hashes filename, page, ordinal, and text with SHA-256 and retains 12 hexadecimal characters. These are implementation facts, not optimised settings.

15. **FIXED — Chunking rationale.** `accidents/index.jsonl` was the retrieval index; `tools/build_maib_lesson_cards_gpt_screened.py:group_reports` later regrouped every indexed chunk by report. The revised chapter states this once and does not claim chunking was designed or tuned for full-report extraction.

16. **VERIFIED — Complete indexed-corpus manifest.** The frozen report-level manifest and requests contain 2,373 references to 2,373 unique IDs from `accidents/index.jsonl` across 107 requests; reconstructed user payloads match all frozen requests exactly. `group_reports` sorts `(page, chunk_id)`. No request-construction truncation was found, although the index does not preserve the original within-page ordinal and the model's context handling is not observable.

17. **UNRESOLVED — Full-report request configuration.** Frozen `accidents/cards_decision_style_report_level/batch_input.jsonl` verifies `/v1/chat/completions`, `gpt-6.1-sol`, low reasoning, 12,000 completion tokens, strict embedded JSON Schema, and `--- PAGE … / CHUNK … ---` formatting; retry uses 20,000. Temperature, top-p, seed, explicit schema version, and dated model snapshot are absent. **[NEEDS EVIDENCE: provider-default snapshot/model revision and versioned schema metadata.]**

18. **VERIFIED — Exact extraction criteria.** The frozen messages and `tools/build_maib_lesson_cards_gpt_screened.py:GENERATION_SYSTEM_PROMPT` specify distinct operational findings, 1–3 source quotations, empty output when no qualifying finding exists, no unsupported inference, and warnings/non-transfer conditions. The revised chapter condenses these criteria without inventing prompt priorities.

19. **VERIFIED — Card deduplication.** `tools/build_maib_lesson_cards.py:validate_generate` derives the ID from source path plus normalised `investigator_finding`, checks accepted IDs through one validation-run `seen` set, and rejects a repeat. Same-report cards with identical normalised findings can collapse even when other fields differ; different reports normally do not. No ID rejection occurred, and truncated-digest collision remains theoretically possible.

20. **VERIFIED — Quotation matching.** `normalized` applies NFKC, removes soft hyphens, collapses whitespace, and trims. `quote_supported` performs normalised exact-substring matching against concatenated indexed text on the *claimed page*. One failed quote rejects the whole candidate. Reproduced failures: 17 initial candidates and one retry candidate; this is not literal byte matching.

21. **VERIFIED — Report-level failure.** The initial `pdf127` output has HTTP 200, `finish_reason="length"`, empty content, 7,522 prompt tokens, and 12,000 completion/reasoning tokens. The second request contains the same 19 chunks, uses a 20,000 limit, stops normally after 1,165 completion tokens, and returns three candidates, of which two validate. The chapter distinguishes transport success from parseable structured content.

22. **VERIFIED — Schema definitions.** Exact controlled vocabularies and list constraints come from the Pydantic models in `tools/build_maib_lesson_cards.py`; unknown/uncertain/not-applicable/other are preserved as defined enum values. The chapter separates model-generated assessments from manifest/validator provenance. It does not manufacture a sample quotation.

23. **UNRESOLVED — Configuration tuning rationale.** Code and artifacts fix 400/60/80 chunking, 1–3 quotes, low reasoning, completion limits, report grouping, and pilot caps, but no tuning sweep or held-out selection log exists. **[NEEDS EVIDENCE: pre-registered defaults or tuning experiment with candidate settings and selection criterion.]** `Chapter_4_configuration.md` labels implemented choices Fixed, none Tuned, and missing rationale Not documented.

24. **VERIFIED — Cost and workload.** `accidents/cards_decision_style_all_chunks/cost_estimate.json` reproduces 2,373 requests, 24,388,883 estimated input tokens, 9,492,000 maximum output tokens, and USD 71.848883 maximum configured Batch cost. Measured output usage reconciles to USD 2.493273 initial extraction, USD 0.013347 retry, USD 0.733087 curation, and USD 3.239707 total at the stored rates. Exclusions and estimate assumptions are explicit in the chapter and numbers ledger.

25. **VERIFIED — Pilot selector.** `tools/build_maib_lesson_cards.py:{ANALYSIS_MARKERS,NAVIGATION_MARKERS,passage_score,select_passages}` verifies the complete marker lists, lowercase substring matching without stemming, analysis/navigation weights 5/3 per present marker, `collision` occurrences capped at 3, ordering by score/page/chunk ID, top eight positive passages or two-item fallback, and maximum three cards. The chapter calls these fixed heuristics, not BM25 or learned optima.

26. **FIXED — Evaluation definitions.** The intended unit is a narrative state, candidates are all lesson cards, and relevance means usefulness of a card to that state. No labelled state–card set is preserved. `tools/run_maib_extraction_pilot.py` instead defines a secondary known-report source-page diagnostic; its metrics are excluded from the chapter's retrieval conclusions.

27. **VERIFIED — Runtime BM25.** `rag/maib.py:{tokenize,MaibRetriever._bm25_scores}` verifies lowercasing, `head_on` replacement, `[a-z0-9]+`, unique query terms, global card-corpus document frequencies, positive IDF, `k1=1.5`, `b=0.75`, evidence weighting, and chunk-ID tie-break. No MAIB tuning record exists.

28. **UNRESOLVED — Runtime BGE revision.** `rag/maib.py:MaibRetriever` and `experiments/maib_lesson_cards_pilot.ini` verify `BAAI/bge-small-en-v1.5`, the query prefix, L2-normalised query encoding, cached card vectors, and dot-product ranking. The immutable revision used for `accidents/cards/embeddings.npy` was not recorded. **[NEEDS EVIDENCE: frozen model revision used for the pilot card embeddings.]**

29. **VERIFIED — Runtime reciprocal-rank fusion.** `rag/maib.py:MaibRetriever.retrieve` forms the union of the first 50 BM25 and dense candidates and sums available one-based terms `1/(60+rank)`. The constant is fixed and not MAIB-optimised.

30. **UNRESOLVED — Runtime cross-encoder comparison.** `rag/maib.py:MaibRetriever.retrieve` optionally reranks the first 20 fused candidates and adds a reciprocal-rank term, but the pilot card configuration leaves `reranker_model` empty. No labelled state–card experiment compares this option. **[NEEDS EVIDENCE: fixed model revision plus a labelled state–card evaluation.]**

31. **UNRESOLVED — Intended retrieval metrics.** No artifact defines relevance judgements between narrative states and lesson cards, so precision/success@k, recall@k, MRR, and nDCG cannot be calculated for the intended task. **[NEEDS EVIDENCE: a labelled narrative-state–lesson-card benchmark.]** Existing page metrics apply only to the secondary source-page diagnostic.

32. **FIXED — Benchmark dependence.** A valid state-to-card benchmark must cluster repeated narrative states by encounter or simulation case. The existing 503-row page diagnostic is clustered by source report, which is another indication that it measures a different task.

33. **UNRESOLVED — State-retrieval uncertainty.** No paired state-to-card method comparison exists, so no applicable bootstrap interval exists. **[NEEDS EVIDENCE: paired rankings on labelled states, resampled by encounter/case.]** The report-cluster intervals in the page diagnostic are not transferred to the intended task.

34. **FIXED — Fusion narrative.** The chapter makes no claim that BM25, BGE, fusion, or reranking is superior for state-to-card retrieval. The available page-diagnostic ordering is irrelevant to that comparison.

35. **VERIFIED — Runtime ranking implementation.** `rag/maib.py` ranks the global card index, applies state-derived metadata preferences before score for lesson cards, returns at most one card per report, and stops at `top_k`; the pilot configuration uses `top_k=2` and no cross-encoder.

36. **FIXED — Runtime timing.** `tools/benchmark_maib.py` prints latency for four unlabelled state-like diagnostic queries, but no persisted, hardware-qualified method comparison supports an academic latency result. Page-diagnostic timings are excluded from the state-to-card result.

37. **FIXED — Ten-pair appraisal.** `tools/test_maib_chunk_applicability.py:DEFAULT_SAMPLES` selects five cycles and two results each; the artifacts contain ten pairs but four unique passages. `reports/maib_chunk_applicability/20261115T131345Z/summary.json` contains 0/10 model labels of useful. The chapter neither generalises beyond the sample nor reports uncalibrated confidence as performance.

38. **VERIFIED — Development evaluator.** Frozen requests verify `gpt-6.1-sol`, low effort, 5,000 completion tokens, and the applicability rubric; five cycles were hard-coded rather than randomly sampled. Live narrative, structured state, OOW proposal, retrieval context, and one passage were shown, while the earlier Captain verdict was withheld. Outputs are identified as evaluator-model labels.

39. **VERIFIED — Four-passage trial.** `tools/test_maib_decision_style_extraction.py` and its final summary contain five states representing four unique passages. Two passage responses yield seven cards and two yield empty card arrays labelled background; all four pass schema and quote checks, including the empty outputs. No independent semantic review is recorded, so the chapter does not claim general effectiveness.

40. **UNRESOLVED — Simulation environment provenance.** Scenario files under `mission/scenarios/imazu/01`–`22`, campaign manifests, `rag/maib.py`, and scorer code verify initial conditions, 30-s lock-step cadence, freeze, warp-20 correction, Captain inputs, and top-two runtime retrieval differences. The exact MOOS-IvP release/build and repeated-run seeds are not recorded. **[NEEDS EVIDENCE: versioned simulator build manifest and seed/repetition protocol.]**

41. **VERIFIED — Simulation scores.** `tools/IMAZU_SCORING_CONTRACT.md`, `tools/score_baseline_logs.py`, and `tools/evaluate_run.py` verify the seven components, weights, 500 m clearance threshold, 15 m collision threshold, non-arrival cap, and verdict rules. Citation is COLREG rule agreement, not MAIB quotation quality. The low 0.001–0.020 values are retained on the metric's 0–1 scale.

42. **UNRESOLVED — Paired-trial comparability.** Each reported pair uses the same 22 scenario IDs and scorer within that pair; lock-step manifests also match warp and cadence. However, raw-versus-card changes Captain architecture/prompt/retrieval, and base-versus-critic changes architecture and evidence. Only one rollout per case is present, with no repeated seeds. **[NEEDS EVIDENCE: matched prompts/architecture/retrieval except the tested factor, plus repeated seeded rollouts.]** The chapter makes descriptive, not causal, comparisons.

43. **FIXED — Simulation verdict presentation.** Tables report warp-5 raw/cards as 12/9/1 versus 13/7/2 pass/clearance/collision and lock-step base/critic as 17/5 versus 22/0. They are explicitly two distinct, non-equivalent comparisons.

44. **FIXED — Final-card runtime test.** No runtime index hash or campaign manifest matches `accidents/cards_decision_style_curated/cards.retained.jsonl`. The final 503-card condition is reported as unrun, and a matched runtime ablation is future work.

45. **FIXED — Chapter hierarchy.** `Chapter_4_revised.md` now orders corpus/schema, development studies, final extraction/validation/curation, retrieval methods, results/cost, separate simulation evidence, validity/reproducibility, and RQ1. Methods precede their result tables.

46. **FIXED — Evaluative filler.** Repeated meta-commentary and generic feasibility language were removed. Each limitation is attached to the relevant measurement or consolidated in §4.8.

47. **FIXED — Unsupported adjectives and causal language.** Claims such as “robust,” “strongest,” “safe,” “reliable,” “demonstrates,” and “improved” are absent as unqualified conclusions. `safe` appears only as the literal model-rubric label with an explicit non-certification definition; “causal” appears in schema vocabulary or denials of causal identification.

48. **FIXED — Figure/table discipline.** Tables 4.1–4.5 appear after their terms and methods are defined and use consistent report/chunk/page/card terminology. The irrelevant page-recovery table is absent from the chapter.

49. **VERIFIED — References and reproducibility mapping.** Existing bibliography keys for BM25, sentence transformers/BGE context, RRF, and cross-encoder methods were checked in the repository bibliography. The runtime implementation is mapped to `agents/agent_captain.py`, `rag/maib.py`, the pilot INI, and the diagnostic command in §4.8. Unknown versions and missing relevance labels remain explicit.

50. **FIXED — RQ1 answer.** §4.9 states the supported extraction sequence and identifies the intended retrieval task as global lesson-card ranking from a narrative state. It explicitly reports that no labelled comparison establishes the best retrieval method and makes no claim that final cards improve live navigation.

## Acceptance-check summary

The count ledger is `Chapter_4_numbers.md`; the full fixed/tuned/not-documented inventory is `Chapter_4_configuration.md`; and `tools/audit_chapter4_artifacts.py` performs read-only count and invariant checks. Original experiment requests, outputs, manifests, scores, and logs were not modified.
