# Appendix — Chapter 4 configuration and operational definitions

“Fixed” means the value is present in code, a frozen request, a manifest, or an output artifact. It does not mean the value was empirically optimised. No Chapter 4 parameter has a documented tuning sweep; accordingly, no row is classified as **Tuned**. “Not documented” means the audited artifacts do not preserve the value or rationale.

| Component | Choice or definition | Actual value | Classification | Evidence / note |
|---|---|---|---|---|
| Corpus acquisition | Search date and exact query/filters | Not preserved | Not documented | No dated MAIB search log found |
| Corpus acquisition | Initial search-result count | Draft says 147 | Not documented | Cannot be reconstructed from current artifacts |
| Corpus acquisition | Exclusion register / duplicate policy | Not preserved | Not documented | 107 local PDFs are the reproducible evaluation boundary |
| PDF input | Evaluated files | `accidents/*.pdf`, 107 files | Fixed | Local corpus |
| PDF extraction | Library/API | `pypdf.PdfReader`; `page.extract_text()` default mode | Fixed | `tools/ingest_maib.py` |
| PDF extraction | Exact build-time `pypdf` version | Not recorded; current environment 6.18.0 | Not documented | Version is absent from index provenance |
| PDF extraction | Page numbering | One-based physical PDF page index | Fixed | `enumerate(reader.pages, start=1)` |
| PDF extraction | OCR / image transcription | None | Fixed | Image-only material omitted |
| PDF extraction | Tables/layout | Plain extracted text only; no structural reconstruction | Fixed | Default `pypdf` extraction |
| PDF extraction | Text cleaning | Collapse all whitespace with `\s+`, trim, split on whitespace | Fixed | `tools/ingest_maib.py:chunks` |
| PDF extraction | Unicode cleaning before chunking | None beyond Python text handling | Fixed | No Unicode normaliser in indexer |
| Chunking | Maximum words | 400 | Fixed | CLI default; no tuning record |
| Chunking | Overlap / stride | 60 / 340 words | Fixed | CLI default; no tuning record |
| Chunking | Minimum emitted fragment | 80 words, inclusive | Fixed | Hard-coded in `chunks`; CLI validation requires size > 80 |
| Chunking | Short-page behaviour | Emit no chunk below 80 words | Fixed | `chunks` generator |
| Chunking | Boundary | Never crosses a physical PDF page | Fixed | Per-page call to `chunks` |
| Chunk identifier | Definition | SHA-256 of filename, page, ordinal, text; first 12 hex; `maib-` prefix | Fixed | `tools/ingest_maib.py` |
| Chunk order in extraction | Sort key | `(page, chunk_id)` | Fixed | `group_reports`; original within-page ordinal is not stored |
| Pilot selector | Analysis markers | `analysis`, `conclusion`, `safety issues`, `causal factor`, `contributory factor`, `the investigation found`, `the maib found`, `lessons learned`, `actions taken`, `recommendation` | Fixed | Heuristic, not tuned |
| Pilot selector | Navigation markers | Full tuple in `tools/build_maib_lesson_cards.py:NAVIGATION_MARKERS` | Fixed | Heuristic, not tuned |
| Pilot selector | Marker weights | Analysis 5; navigation 3; `collision` occurrence 1 capped at 3 | Fixed | Heuristic, not tuned |
| Pilot selector | Matching | Lowercased substring presence; no stemming; repeated markers count once | Fixed | `passage_score` |
| Pilot selector | Rank/ties | Score descending, page ascending, chunk ID ascending | Fixed | `select_passages` |
| Pilot selector | Passage cap / fallback | Top 8 positive; otherwise first 2 ranked | Fixed | Heuristic, not tuned |
| Pilot generation | Card cap | 0–3 cards per report | Fixed | Pydantic response field `max_length=3` |
| Pilot generation | Model/effort/output limit | `gpt-6.1-sol`; low; 1,600 completion tokens | Fixed | Frozen pilot request file |
| Lesson-card schema | Cards per final report | Zero or more; no schema maximum | Fixed | `AllLessonCardsResponse` |
| Lesson-card schema | Pages per card | 1–4 generated, then reduced to verified quote pages | Fixed | `LessonCardCandidate`; final validator |
| Lesson-card schema | Quotes per card | 1–3; each 8–700 characters | Fixed | `SupportingQuote`; `LessonCardCandidate` |
| Lesson-card schema | Required list sizes | Encounter 1–4; role 1–4; phase 1–5; warning 1–6; mechanism 1–5; non-transfer 1–5 | Fixed | Pydantic field constraints |
| Lesson-card schema | Schema version | No explicit version | Not documented | Schema is embedded in each request |
| Final extraction | Input grouping | All indexed chunks grouped once per source report | Fixed | Final manifest |
| Final extraction | Requests | 107 | Fixed | Frozen Batch input |
| Final extraction | Model | `gpt-6.1-sol` | Fixed | Frozen Batch input |
| Final extraction | Reasoning effort | `low` | Fixed | Frozen Batch input |
| Final extraction | Completion limit | 12,000; retry 20,000 | Fixed | Initial and retry Batch inputs |
| Final extraction | Endpoint | `/v1/chat/completions` | Fixed | Frozen Batch input |
| Final extraction | Batch completion window | 24 h | Fixed | `tools/build_maib_lesson_cards.py:submit` |
| Final extraction | Output constraint | Strict JSON Schema | Fixed | `response_format.json_schema.strict=true` |
| Final extraction | Temperature/top-p/seed | Omitted | Not documented | Provider defaults applied; values not frozen |
| Final extraction | Model snapshot/system fingerprint | No dated snapshot; `system_fingerprint=null` in responses | Not documented | Raw Batch output |
| Final extraction | Task criteria | Exact frozen `GENERATION_SYSTEM_PROMPT` | Fixed | `tools/build_maib_lesson_cards_gpt_screened.py`; embedded request messages |
| Quote validation | Normalisation | NFKC; remove soft hyphen; collapse whitespace; trim | Fixed | `tools/build_maib_lesson_cards.py:normalized` |
| Quote validation | Match boundary | Normalised exact substring within concatenated indexed chunks on the claimed page | Fixed | `quote_supported` |
| Quote validation | Failure policy | Reject whole candidate if any quote fails | Fixed | `validate_generate` |
| Accepted page list | Definition | Sorted unique page numbers of verified supporting quotes | Fixed | `validate_generate` |
| Card ID | Definition | `maib-lesson-` + first 16 hex of SHA-256 over source path and normalised finding | Fixed | `validate_generate` |
| Duplicate candidate | Definition | Repeated card ID within one validation run | Fixed | `seen` set; exact normalised finding within source usually required |
| Curation | Input | Initial 998 source-valid cards only | Fixed | Curation manifest |
| Curation | Grouping | 102 source-report groups | Fixed | `group_cards` |
| Curation | Model/effort/output limit | `gpt-6.1-sol`; low; 8,000 completion tokens | Fixed | Frozen curation requests |
| Curation | Decision relevance | `direct`, `adjacent`, `out_of_scope` under prompt definitions | Fixed | Model-assigned rubric, not expert ground truth |
| Curation | Grounding quality | `strong`, `partial`, `weak` under prompt definitions | Fixed | Model-assigned rubric |
| Curation | Transfer quality | `safe`, `review`, `reject` under prompt definitions | Fixed | Model-assigned rubric; `safe` is not certification |
| Curation | Duplicate scope | Earlier card in same report group only | Fixed | Prompt plus validation |
| Curation | Disposition precedence | duplicate→exclude; direct+strong+safe→retain; out-of-scope/weak/reject→exclude; else review | Fixed | `tools/curate_maib_lesson_cards.py:disposition` |
| Curation | Coverage invariant | Returned IDs must exactly equal manifest IDs in order; three outputs partition all IDs | Fixed | `validate`; `audit` |
| Development appraisal | Sample selection | Five hard-coded cycles, two retrieved results each | Fixed | `DEFAULT_SAMPLES`; purposive, not random |
| Development appraisal | Judge/effort/limit | `gpt-6.1-sol`; low; 5,000 completion tokens | Fixed | Prepared requests |
| Development appraisal | Prior Captain verdict shown? | No | Fixed | Stored outside prompt payload |
| Decision-style trial | Passages / example states | Four unique passages / five states | Fixed | Final summary and collection code |
| Decision-style trial | Judge/effort/limit | `gpt-6.1-sol`; low; 5,000 completion tokens | Fixed | Prepared requests |
| Intended retrieval | Query unit | One narrative bridge state plus numerical encounter observations | Fixed | `AgentCaptain.retrieve_evidence` |
| Intended retrieval | Candidate unit / scope | Every lesson card in the global runtime index | Fixed | `MaibRetriever` |
| Intended retrieval | Query construction | Literal template in §4.5.2; first 3 of all contacts after risk-category/range ordering; risk is not printed | Fixed | `natural_query_from_state` |
| Intended retrieval | Retrieval gate | Require at least one contact with closing speed >0, TCPA ≥0, and CPA below clearance; no TCPA upper bound | Fixed | `navigation_relevant_contacts` |
| Intended retrieval | Imazu clearance override | 926 m from `imazu.SAFE_PASSING_DISTANCE_M` via `MOOS_LLM_MIN_SAFE_CPA_M` | Fixed | `run_simulator.sh`; retrieval audit |
| Intended retrieval | Hidden preferences | Encounter type and phase derived from numeric geometry; role set empty | Fixed | `retrieval_preferences_from_state` |
| Intended retrieval | Pilot corpus | 255 cards from 103 reports | Fixed | `accidents/cards/index.jsonl` |
| Intended retrieval | Output cutoff / diversity | Top 2; at most one card per source report | Fixed | Pilot INI; `MaibRetriever.retrieve` |
| Intended retrieval | Lexical settings | BM25 `k1=1.5`, `b=0.75`; global card corpus; unique query terms | Fixed | `MaibRetriever._bm25_scores` |
| Intended retrieval | Evidence weights | 0.25 rule-heavy non-analytical; 1.12 analytical; otherwise 1.0 | Fixed | `MaibRetriever._evidence_weight` |
| Intended retrieval | Hybrid candidates / fusion | Top 50 BM25 union top 50 BGE; sum available `1/(60+r)` terms | Fixed | `MaibRetriever.retrieve` |
| Intended retrieval | Pilot embedding / reranker | `BAAI/bge-small-en-v1.5`; no cross-encoder | Fixed | `experiments/maib_lesson_cards_pilot.ini` |
| Intended retrieval | Labelled relevance set / comparison metrics | None preserved | Not documented / not evaluated | Four diagnostic queries have no labels |
| Secondary source-page diagnostic | Evaluation unit | One retained card with target set of verified quote pages | Fixed | `evaluate_rankings` |
| Secondary source-page diagnostic | Candidate scope | All chunks from the known source report | Fixed | `build_rankings` |
| Secondary source-page diagnostic | Query | Exact generic string in `card_query`; identical for all cards in generic mode | Fixed | `query_mode=generic` provenance |
| Secondary diagnostic BM25 | Tokenizer | Lowercase; `head_on`→`head on`; regex `[a-z0-9]+` | Fixed | `rag.maib.tokenize` |
| Secondary diagnostic BM25 | Parameters | `k1=1.5`, `b=0.75` | Fixed | No tuning log |
| Secondary diagnostic BM25 | IDF | `log(1 + (N-n+0.5)/(n+0.5))` | Fixed | `bm25_order` |
| Secondary diagnostic BM25 | Query frequency | Unique query-term set | Fixed | `terms=set(tokenize(query))` |
| Secondary diagnostic BM25 | Corpus | One source report per ranking | Fixed | `source_indexes` |
| Secondary diagnostic BM25 | Tie handling | Chunk ID ascending | Fixed | `bm25_order` |
| Secondary diagnostic BGE | Model | `BAAI/bge-small-en-v1.5` | Fixed | Provenance |
| Secondary diagnostic BGE | Frozen model revision | Not recorded; current cache has `5c38ec…` only | Not documented | Model name alone was persisted |
| Secondary diagnostic BGE | Passage format | `Report: … Page: … Passage: …` | Fixed | `tools/embed_maib.py` |
| Secondary diagnostic BGE | Query instruction | `Represent this sentence for searching relevant passages: ` | Fixed | `build_rankings` |
| Secondary diagnostic BGE | Pooling/dimension/max length | CLS / 384 / 512 tokens | Fixed in current cached model | Snapshot revision was not frozen with experiment |
| Secondary diagnostic BGE | Normalisation/similarity | L2 normalised passage and query vectors; dot product (cosine) | Fixed | Embed and benchmark code |
| Secondary diagnostic BGE | Passage cache / query batch / device | `accidents/embeddings.npy`; 32; CPU | Fixed | Code and provenance |
| Secondary diagnostic BGE | Tie handling | Chunk ID ascending | Fixed | `dense_order` |
| Secondary diagnostic RRF | Inputs / constant | Complete BM25 and BGE rankings; `1/(60+r)` | Fixed | `reciprocal_rank_fusion` |
| Secondary diagnostic cross-encoder | Model / depth | `cross-encoder/ms-marco-MiniLM-L6-v2`; first 20 RRF chunks | Fixed | Diagnostic provenance |
| Secondary diagnostic cross-encoder | Frozen revision | Not recorded; current cache has `233902…` only | Not documented | Model name alone was persisted |
| Secondary diagnostic page ranking | Conversion | Keep first occurrence of each physical page | Fixed | `unique_page_ranking` |
| Secondary diagnostic metrics | Measures | Page Hit@1/3/5, page recall@5, MRR, binary nDCG@5 | Fixed | `ranking_metrics` |
| Secondary diagnostic bootstrap | Comparison / cluster / draws / seed | Reranker−RRF / report / 2,000 / 20261113 | Fixed | `paired_cluster_bootstrap` |
| Runtime simulation | Scenarios | Imazu cases 1–22 | Fixed | Scenario INIs and run manifests |
| Runtime simulation | Repetitions/seeds | One rollout per condition/case; no repetition seed set preserved | Not documented | Cannot estimate run-to-run variation |
| Warp-5 scorer | Contract | `imazu-frozen-v1` | Fixed | Frozen score artifacts |
| Lock-step cadence | Warp / decision interval / pause | 20 / 30 simulated s / freeze during decision | Fixed | Manifests |
| Lock-step scorer | Contract | `imazu-lockstep-v2`; temporal pause correction only | Fixed | `scores_v2` artifacts |
| Simulation | Study-specific safe distance / collision radius / arrival radius | 500 m / 15 m / 20 m | Fixed | Scorer |
| Simulation | Composite weights | .35 safety; .10 each compliance, citation, temporal, spatial, manoeuvre; .15 smoothness | Fixed | Score artifacts |
| Simulation | Raw-passage runtime retrieval | Global hybrid top-2 with source diversity; no reranker configured | Fixed | Experiment config and `MaibRetriever` |
| Simulation | Pilot-card runtime retrieval | Same hybrid base plus generated metadata preferences; top-2 | Fixed | Experiment config and `MaibRetriever._ranking_key` |
| Simulation | Final 503-card runtime condition | None | Not documented / not run | No matching runtime index or manifest |
| Runtime software | Exact MOOS-IvP version/build | Not recorded in Chapter 4 artifacts; current binaries report Release Bundle 24.8 and MOOSDB V10 | Not documented | Current values do not prove the historical experiment version; see `reports/SOFTWARE_VERSIONS.md` |
| Runtime software | Exact vLLM/Python/OS/WSL/CUDA versions | Not recorded in campaign artifacts; audit-date values are inventoried separately | Not documented | See `reports/SOFTWARE_VERSIONS.md`; do not substitute current values for historical versions |
| Retrieval timing | CPU hardware model | Not recorded in retrieval provenance | Not documented | Do not present full-pipeline hardware-normalised latency |

## Fixed versus tuned summary

- **Fixed:** all implemented thresholds, prompts, model names, ranking parameters, cutoffs, schema constraints, and scoring weights listed above.
- **Tuned:** none supported by a tuning log or held-out selection experiment.
- **Not documented:** corpus search/exclusion history, build-time PDF library version, provider sampling defaults, explicit schema version, frozen Hugging Face revisions, repeated-run seeds, exact runtime software release, and retrieval hardware details.
