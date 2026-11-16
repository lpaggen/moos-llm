# Imazu Warp-5 experiments: audited record

Audit date: 2026-11-02 (Europe/Brussels)

This document records every Imazu evaluation directory whose manifest has `time_warp: 5`. It separates the controlled 22-case comparison from older campaigns, reruns, smoke tests, and incomplete replays. Values are copied from, or recomputed directly against, the paths cited below. Raw campaign status and retrospective scoring verdict are deliberately kept separate.

## Audit method and scope

The record was checked in five independent passes:

1. **Manifest pass:** searched every `logs/evaluations/imazu/*/manifest.json`, not only directories whose names contain `warp5`.
2. **Completeness pass:** counted case directories, `audit.jsonl`, `result.json`, and `results.jsonl` independently.
3. **Prompt pass:** hashed the complete recorded `system_prompt` field and inspected the exact recorded user field. RAG prompts are marked reconstructed where the final augmented message was not logged.
4. **Score pass:** recomputed aggregate means from all per-case scores and compared them with persisted score summaries. Every persisted summary agrees with the arithmetic mean of its 22 case entries (difference exactly zero, apart from one floating-point residual of `5.55e-17`).
5. **Semantic pass:** checked model identity, adapter path, supplied COLREG information, rerun overlay rules, citation-audit definitions, and raw runner outcomes against the audits and source code.

The primary comparison contains seven 22-case variants:

- Base narrative, with the documented rerun overlay.
- Base raw state, with the documented rerun overlay.
- Base plus deterministic Eriksen roles and applicable rules.
- COLREG summary RAG.
- Luna SFT v0, retained as an invalid/collapsed diagnostic.
- Luna privileged-teacher SFT v1.
- Independent captain with MAIB accident-report RAG.

The historical Warp-5 runs are inventoried later but are not silently mixed into this comparison.

## Scoring contract

The persisted retrospective scores use these axis weights:

| Axis | Weight |
|---|---:|
| Safety | 0.35 |
| Compliance | 0.20 |
| Temporal efficiency | 0.10 |
| Spatial efficiency | 0.10 |
| Manoeuvre quality | 0.10 |
| Smoothness | 0.15 |

The configured safe-distance reference is 500 m. For an arrived, non-collision case, the composite is the weighted sum of the six axes. The retrospective scorer assigns zero if its reconstructed minimum clearance is below 15 m. A non-arrival is capped at 0.2 and is calculated from the compliance, manoeuvre, and smoothness contributions. A non-collision arrival below 500 m is `PASS_WITH_CPA_VIOLATION`; one at or above 500 m is `PASS`.

The compliance axis is **not** citation accuracy. It is a retrospective geometry/action estimate. The original non-MAIB score artifacts reconstructed actions from observed heading and speed changes. The MAIB score artifact instead used recorded `captain_validated` executable decisions. This difference is declared in the artifacts' `score_kind`/`policies` fields and means that the compliance and manoeuvre axes are not perfectly method-identical across those artifacts. Citation accuracy is reported separately.

The scorer also reconstructs encounter roles geometrically using its own thresholds and interpolates contact geometry only within recorded track spans. Therefore, the score is an evaluation estimate, not a direct simulator-native COLREG verdict.

## Controlled comparison: aggregate results

| Variant | Mean score | Completed-case mean | PASS | CPA violation | Scorer collision | Scorer non-arrival | Safety | Compliance | Temporal | Spatial | Manoeuvre | Smoothness |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Base narrative, combined reruns | **0.825218** | 0.825218 | 19 | 3 | 0 | 0 | 0.959695 | 0.465909 | 0.951337 | 0.989778 | 0.540735 | 0.986390 |
| MAIB RAG independent captain | 0.776562 | 0.813541 | 12 | 9 | 1 | 0 | 0.682268 | **0.718182** | 0.819529 | 0.829844 | 1.000000 | 0.986207 |
| Base + Eriksen/all applicable rules | 0.712419 | 0.737086 | 9 | 12 | 0 | 1 | 0.772350 | 0.209091 | 0.929797 | 0.952002 | 0.649148 | 0.990958 |
| Base raw state, combined reruns | 0.695929 | 0.774234 | 11 | 8 | 0 | 3 | 0.702757 | 0.306818 | 0.835024 | 0.857070 | 0.793823 | 0.990406 |
| Luna privileged-teacher SFT v1 | 0.674153 | 0.674153 | 7 | 15 | 0 | 0 | 0.581657 | 0.188636 | 0.975659 | 0.988310 | 0.876292 | 0.992128 |
| COLREG summary RAG | 0.626605 | 0.789715 | 12 | 4 | 0 | 6 | 0.659387 | 0.293182 | 0.676180 | 0.709862 | 0.626938 | 0.972008 |
| Luna SFT v0, invalid/collapsed | 0.314186 | 0.656900 | 0 | 9 | 8 | 5 | 0.261711 | 0.022727 | 0.401196 | 0.405059 | 0.968283 | 0.997348 |

The strongest controlled result is the combined base-narrative run at `0.825218`. The MAIB-RAG captain is second at `0.776562`, but it has one scorer-defined collision (case 19, 14.073 m) and nine additional sub-500 m cases. The SFT v1 run completes every case but does not beat the base narrative.

### Exact per-case composite scores

| Case | Narrative | Raw | Eriksen | COLREG-RAG | SFT-v0 | SFT-v1 | MAIB-RAG |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.910860 | 0.989703 | 0.670761 | 0.633556 | 0.841374 | 0.929471 | 0.967622 |
| 2 | 0.890532 | 0.909556 | 0.908992 | 0.918680 | 0.838295 | 0.531023 | 0.988293 |
| 3 | 0.894285 | 0.912875 | 0.935922 | 0.200000 | 0.761857 | 0.947495 | 0.555652 |
| 4 | 0.552829 | 0.200000 | 0.446992 | 0.859925 | 0.569094 | 0.464114 | 0.523616 |
| 5 | 0.919338 | 0.925276 | 0.893371 | 0.620077 | 0.000000 | 0.869949 | 0.992556 |
| 6 | 0.892701 | 0.499906 | 0.620531 | 0.892395 | 0.547782 | 0.611827 | 0.956723 |
| 7 | 0.876481 | 0.905996 | 0.894914 | 0.172788 | 0.200000 | 0.824698 | 0.620846 |
| 8 | 0.921606 | 0.896221 | 0.923817 | 0.794091 | 0.596861 | 0.927235 | 0.994534 |
| 9 | 0.895046 | 0.640236 | 0.741950 | 0.889905 | 0.609587 | 0.928295 | 0.991808 |
| 10 | 0.717488 | 0.759382 | 0.636117 | 0.857305 | 0.000000 | 0.615756 | 0.959676 |
| 11 | 0.753696 | 0.594390 | 0.699118 | 0.479798 | 0.609123 | 0.675248 | 0.985465 |
| 12 | 0.732316 | 0.746464 | 0.684873 | 0.866920 | 0.538129 | 0.640633 | 0.950184 |
| 13 | 0.585555 | 0.542466 | 0.194426 | 0.605856 | 0.000000 | 0.516540 | 0.670910 |
| 14 | 0.767502 | 0.511663 | 0.667955 | 0.899752 | 0.200000 | 0.485215 | 0.833209 |
| 15 | 0.887218 | 0.913346 | 0.878061 | 0.200000 | 0.200000 | 0.527081 | 0.573757 |
| 16 | 0.772069 | 0.785725 | 0.768898 | 0.769991 | 0.000000 | 0.501505 | 0.991244 |
| 17 | 0.760260 | 0.766099 | 0.738784 | 0.193207 | 0.000000 | 0.510725 | 0.565220 |
| 18 | 0.911350 | 0.200000 | 0.597306 | 0.891385 | 0.000000 | 0.570619 | 0.961716 |
| 19 | 0.847370 | 0.200000 | 0.640671 | 0.758359 | 0.200000 | 0.570853 | 0.000000 |
| 20 | 0.887272 | 0.906361 | 0.668793 | 0.197050 | 0.000000 | 0.716431 | 0.517937 |
| 21 | 0.892854 | 0.595974 | 0.612474 | 0.897452 | 0.200000 | 0.573447 | 0.949508 |
| 22 | 0.886179 | 0.908807 | 0.848498 | 0.186824 | 0.000000 | 0.893199 | 0.533892 |

### Exact per-case reconstructed minimum clearance (m)

The score artifact calls this `min_cpa_m`, but it is the smaller of the latched logged minimum range and the scorer's one-second interpolated overlap minimum. It should not be confused with the model's instantaneous constant-motion CPA projection.

| Case | Narrative | Raw | Eriksen | COLREG-RAG | SFT-v0 | SFT-v1 | MAIB-RAG |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 592.174 | 957.582 | 350.840 | 293.498 | 497.016 | 664.376 | 1777.256 |
| 2 | 901.974 | 857.891 | 657.175 | 1149.941 | 494.548 | 127.823 | 1411.960 |
| 3 | 1053.034 | 1083.526 | 611.579 | 46.198 | 463.262 | 628.396 | 49.816 |
| 4 | 301.223 | 33.221 | 73.020 | 1545.429 | 203.844 | 54.285 | 33.919 |
| 5 | 981.417 | 1027.266 | 676.203 | 281.663 | 0.903 | 768.110 | 1087.540 |
| 6 | 532.478 | 91.525 | 296.960 | 760.558 | 168.069 | 250.630 | 691.249 |
| 7 | 1391.628 | 1153.891 | 648.204 | 37.926 | 29.075 | 650.192 | 175.268 |
| 8 | 847.552 | 961.768 | 611.660 | 761.172 | 215.892 | 982.580 | 1444.828 |
| 9 | 892.370 | 300.908 | 475.535 | 1689.014 | 234.912 | 501.715 | 1219.407 |
| 10 | 882.008 | 713.959 | 347.421 | 985.814 | 7.120 | 262.685 | 771.451 |
| 11 | 659.327 | 245.983 | 423.381 | 131.334 | 239.633 | 351.924 | 1152.997 |
| 12 | 459.277 | 468.850 | 390.052 | 845.712 | 145.737 | 297.930 | 875.759 |
| 13 | 296.141 | 215.975 | 45.876 | 288.880 | 0.474 | 118.935 | 369.498 |
| 14 | 507.857 | 98.388 | 347.232 | 789.113 | 34.384 | 59.059 | 433.511 |
| 15 | 1197.837 | 641.538 | 801.926 | 44.754 | 28.452 | 137.222 | 117.446 |
| 16 | 642.335 | 499.977 | 515.829 | 893.650 | 11.881 | 98.846 | 530.113 |
| 17 | 1121.339 | 877.036 | 838.282 | 47.184 | 3.734 | 123.238 | 146.443 |
| 18 | 570.664 | 17.422 | 255.131 | 880.366 | 14.831 | 182.595 | 1009.012 |
| 19 | 682.488 | 25.602 | 340.225 | 716.708 | 42.800 | 204.203 | 14.073 |
| 20 | 803.945 | 834.528 | 354.701 | 38.193 | 0.231 | 423.321 | 106.589 |
| 21 | 735.672 | 232.471 | 295.470 | 1031.038 | 37.781 | 205.533 | 1174.340 |
| 22 | 1333.942 | 1207.583 | 804.745 | 43.630 | 4.246 | 711.502 | 58.390 |

## Exactly what each controlled model received

| Variant | Executing model | State form | COLREG/role information supplied to executing model | Recorded system prompt SHA-256 |
|---|---|---|---|---|
| Base narrative | `Qwen/Qwen3-8B` | Deterministic natural-language briefing, sometimes including recent developments and active-action review | No upstream role or applicable-rule list | `44cbd1455f11abd7f6eef77c8108ef761a6aaa28538d3fa9e087453cc0eb0ebf` |
| Base raw state | `Qwen/Qwen3-8B` | JSON state, safety limits, and up to two previous states | No upstream role or applicable-rule list | `b4849b75638be6b16de27797fbe0925b4ebf7ef995ad0f2e457a506bdaade3a2` |
| Eriksen/all-rules | `Qwen/Qwen3-8B` | Narrative plus deterministic `COLREG ASSESSMENT` | Yes: persistent encounter classification, own-ship role, standing Rules 2/5/6/7, and encounter-specific applicable rules with concise summaries | `4ef6e1921cdcf98fe92f766da618cd3e3ced0cb566084a45a74bc87560174cb0` |
| COLREG RAG | `Qwen/Qwen3-8B` | Narrative plus retrieved rule summaries | Yes: retrieved **operational summaries**, but no authoritative per-contact role/classification | `89641cd7e7d45832df33358b0d96dcbfa068808a995b768961ea1aa03112c97c` |
| Luna SFT v0 | `oow-finetuned` adapter on Qwen3-8B | Same base-narrative runtime payload | No COLREG/role evidence at inference | Same base-narrative hash `44cbd...` |
| Luna SFT v1 | `oow-finetuned` adapter on Qwen3-8B | Same base-narrative runtime payload | No COLREG/role evidence at inference | Same base-narrative hash `44cbd...` |
| MAIB RAG captain | Independent `Qwen/Qwen3-8B` captain; a separate `Qwen/Qwen3-8B` OOW is run only for post-decision comparison | Captain receives structured bridge state/control summary and zero or two report excerpts | No deterministic action override; report evidence is advisory. The captain infers COLREG role/rules from bridge state | Captain hash `0a41f94ec483c3de5112137b4051ce847b9495b46501235706d78b198f8d3941` |

For the modern non-RAG variants, `audit.jsonl` records the complete system prompt and serialized user message in every `oow_prompt_input` event. Their user-message provenance is therefore direct, not reconstructed. The exact full texts are available cycle by cycle in the audits and, for the first six variants, in `reports/imazu_evaluation_dossier/prompt_records.jsonl` with deduplicated system prompts under `reports/imazu_evaluation_dossier/system_prompts/`.

### Critical correction: COLREG RAG used summaries, not full rule text

No completed OOW Warp-5 experiment in this inventory fed full verbatim COLREG provisions to the model.

The retriever uses the full rule text for candidate retrieval and records that text under `candidates`/`selected` in `oow_colregs_retrieval`. However, `experiments/qwen8b_colregs_rag/retrieval.py::prompt_context` replaces every selected rule having a maintained `ship_guidance` entry with an `OPERATIONAL SUMMARY`. Across the audited RAG selections, every selected rule had such a summary. The rules selected at least once were 2, 5, 6, 7, 8, 9, and 11–18; none lacked guidance.

The recorded RAG system prompt says “retrieved verbatim COLREG provisions.” That sentence is inaccurate relative to the actual `prompt_context` implementation. It must not be used as evidence that the model saw verbatim provisions.

Prompt provenance differs by RAG campaign:

- `qwen3_8b_colregs_rag` (2026-09-29): 2,323 retrieval cycles. Its legacy audit does not record the user prompt, so the exact final model-facing user strings are unavailable. The checked-in implementation at the run date nevertheless renders operational summaries, not full provisions.
- `colregs_rag_matched_warp5` (2026-10-19): 4,116 retrieval cycles, always selecting Rules 2, 5, 6, 7, and 8. The audit records the pre-retrieval payload and selected retrieval records, not the final concatenated user string. The final string in the older dossier was reconstructed while the guidance file still had the same 2026-10-05 modification timestamp used for the run; it is therefore strongly supported but not a directly logged byte-for-byte message.
- `colregs_rag_collision_replay_warp5`: 146 retrieval cycles, likewise summary-based.
- `colregs_rag_collision_replay_warp5_240cycles`: failed before any retrieval or OOW prompt.

### MAIB RAG prompt and retrieval facts

The executable captain has 1,867 recorded `captain_prompt_input` events, all using the same 6,237-character system prompt (SHA-256 `0a41f94e...`). The exact structured user payload fields are `priority`, `task`, `bridge_state`, `bridge_control_summary`, `maib_case_evidence`, and `decision_contract`.

There are 1,867 `maib_retrieval` events:

- 1,354 returned no evidence because retrieval was not requested for a navigation-relevant closing contact.
- 513 returned exactly two excerpts each.
- Six unique report chunks were used, for 1,026 total inserted excerpts.
- The most frequently inserted chunk IDs were `maib-9d32103c8615` (353), `maib-2891026537f2` (260), `maib-94b3ac20e24d` (186), `maib-4d4fe6c2fc6c` (148), `maib-5890999fdce9` (77), and `maib-2fa2ed573663` (2).

These are full accident-report excerpts from the existing archive index, not the still-pending generated reasoning cards. The captain's decision is executable and there is no deterministic action override. The separate OOW's recommendation is logged only in `captain_oow_comparison` after the captain has already decided.

One stale/pre-start `oow_prompt_input` record in case 1 has a different OOW prompt hash (`159271...`). The 1,867 active OOW cycles use hash `dffa47ec...`. This anomaly does not affect the captain prompt count or captain decisions.

## Rerun composition and non-cherry-picked source mapping

The canonical combined results use fixed case replacement rules:

- **Base narrative:** rerun cases 2–16; original cases 1 and 17–22.
- **Base raw state:** rerun cases 1–4; original cases 5–22.
- No other controlled campaign is merged.

The original and rerun prompts are identical within each architecture: narrative hash `44cbd...`; raw-state hash `b4849...`. The combined score sources are recorded per case in `reports/imazu_evaluation_dossier/per_case_scores.csv`.

For transparency, the un-overlaid original raw-state matched campaign scores `0.658771` (10 PASS, 8 CPA violations, 4 non-arrivals). The overlay raises it to `0.695929` (11 PASS, 8 CPA violations, 3 non-arrivals). The earlier `base_raw_state_warp5` campaign, which is not used in the controlled comparison, scores `0.483487` (5 PASS, 10 CPA violations, 3 scorer collisions, 4 non-arrivals).

## Citation audit: separate metric

Citation metrics compare each decision's cited rule set to the per-cycle Eriksen oracle's complete applicable-rule set, including standing Rules 2, 5, 6, and 7. They do not enter the physical composite score.

| Audit source | Decisions | Citation present | Exact set | Macro precision | Macro recall | Macro F1 | Micro F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Base narrative original campaign | 1,301 | 0.919293 | 0.080707 | 0.106457 | 0.088229 | 0.092322 | 0.019253 |
| Base raw-state original campaign | 3,073 | 0.995444 | 0.004556 | 0.027335 | 0.011471 | 0.015057 | 0.015619 |
| Eriksen/all-rules | 3,205 | 1.000000 | 0.813105 | 0.860197 | 0.877219 | 0.857036 | 0.681417 |
| COLREG summary RAG | 4,116 | 1.000000 | 0.000000 | 0.610524 | 0.309657 | 0.403146 | 0.424079 |

The citation audit itself requires a caveat. In Eriksen/all-rules, Rules 20–100 were each emitted unsupported 42 times and Rules 101–231 were each emitted unsupported 41 times. This is runaway enumeration, not meaningful knowledge. Base narrative contains unsupported Rule 78 once; raw state contains unsupported Rule 78 fourteen times and Rule 74 eighty-four times. The citation files retain every decision and count under each campaign's `citation_audit/colregs_citations.json`.

## Fine-tuning provenance

### Luna SFT v0: invalid/collapsed diagnostic

Runtime adapter: `models/qwen3_8b_oow_luna_base_narrative_20261021`.

The repository explicitly marks its transformed dataset invalid in `sft/output/teacher_campaigns_luna_20261021_base_narrative/INVALID_SFT_PROVENANCE.json`:

- `valid: false`, `do_not_train: true`.
- 70.99% of targets were `maintain_course`; there was no real balancing.
- 1,383 completions explicitly referred to hidden oracle/classifier evidence unavailable in the student prompt.
- The privileged teacher labels were considered potentially reusable only after filtering and balancing.

The adapter's Warp-5 score (`0.314186`) is therefore retained as a pipeline-failure diagnostic, not a fair estimate of properly prepared SFT.

Its recorded QLoRA run used Qwen3-8B, 4,096-token sequences, 2 epochs, learning rate `1e-4`, effective batch size 16, LoRA rank/alpha 32/64, dropout 0.05, and seed 20261021. The training split had 4,784 rows from 130 missions: 3,396 `maintain_course`, 537 `alter_course`, 399 `rejoin_route`, 386 `set_speed`, 35 `resume`, and 31 `stop`.

### Luna privileged-teacher SFT v1

Runtime adapter: `models/qwen3_8b_oow_privileged_luna_v1`.

The dataset manifest is `sft/output/privileged_teacher_base_narrative_v1/manifest.json`:

- Teacher visibility: numeric state plus deterministic Eriksen/COLREG oracle.
- Student input: exact base-narrative runtime prompt, system hash `44cbd...`.
- Rejected targets explicitly referencing unavailable evidence: 1,383.
- Training missions: 130; validation missions: 17; test missions: 17, split by mission.
- Natural training set: 3,678 rows, including 2,457 `maintain_course` rows (66.8026%).
- Balanced training set actually prepared: 2,442 rows, with 1,221 `maintain_course` rows (exactly 50%).
- Other balanced-train actions: 510 `alter_course`, 318 `rejoin_route`, 334 `set_speed`, 31 `stop`, and 28 `resume`.
- Alter-course direction distribution: 401 starboard, 109 port.
- Validation: 457 natural rows. Test: 455 natural rows.
- Maximum sequence length 4,096; seed 20261021.

Its recorded QLoRA run used one epoch, learning rate `2e-5`, effective batch size 16, LoRA rank/alpha 16/32, and dropout 0.05. The adapter targets `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, and `down_proj`.

This is a valid distillation design: the teacher may use privileged information to produce a better target, while the student receives only inference-time information. The important leakage check is that the serialized target must not refer to hidden evidence. The v1 pipeline performed that check and rejected the 1,383 violating targets.

## Complete Warp-5 campaign inventory

This table reports the runner's literal `result.json.result` values, not retrospective scorer verdicts. “Results” is the number of `result.json` files present.

| Directory | Manifest architecture | Requested cases | Max cycles | Results | Raw runner statuses |
|---|---|---|---:|---:|---|
| `20261003T213137Z_finetuned` | finetuned | `3-22` | 240 | 20 | complete=14, cycle_limit=6 |
| `20261005T103227Z_finetuned` | finetuned | `3-22` | 240 | 20 | complete=14, cycle_limit=6 |
| `20261007T085216Z_dpo` | dpo | `1-22` | 240 | 1 | collision=1 |
| `base_eriksen_all_rules_matched_warp5` | base-eriksen | `1-22` | 240 | 22 | complete=21, collision=1 |
| `base_narrative_matched_warp5` | base | `1-22` | 240 | 22 | complete=19, collision=2, failed=1 |
| `base_narrative_matched_warp5_rerun` | base | `2-16` | 240 | 15 | complete=15 |
| `base_raw_state_matched_warp5` | base-raw-state | `1-22` | 240 | 22 | complete=18, collision=4 |
| `base_raw_state_matched_warp5_rerun` | base-raw-state | `1-4` | 240 | 4 | complete=3, collision=1 |
| `base_raw_state_warp5` | base-raw-state | `1-22` | 240 | 22 | complete=15, collision=7 |
| `colregs_rag_collision_replay_warp5` | colregs-rag | `3,15,17,20,22` | 120 | 2 | complete=1, failed=1 |
| `colregs_rag_collision_replay_warp5_240cycles` | colregs-rag | `3,15,17,20,22` | 240 | 1 | failed=1 |
| `colregs_rag_matched_warp5` | colregs-rag | `1-22` | 240 | 22 | complete=16, collision=6 |
| `luna_base_narrative_sft_matched_warp5` | finetuned-base-narrative | `1-22` | 240 | 22 | complete=9, collision=13 |
| `maib_rag_independent_model_only_warp5` | maib-rag | current root manifest says `22` | 240 | 22 | complete=22 |
| `privileged_luna_v1_matched_warp5` | finetuned-base-narrative | `1-22` | 240 | 22 | complete=22 |
| `privileged_luna_v1_smoke` | finetuned-base-narrative | `13,16,22` | 240 | 3 | complete=3 |
| `qwen3_8b_colregs_rag` | colregs-rag | `1-22` | 120 | 22 | complete=15, cycle_limit=7 |
| `qwen3_8b_finetuned` | finetuned | `1-22` | 120 | 22 | complete=2, failed=20 |

The MAIB directory's current root `manifest.json` and `results.jsonl` reflect only a case-22 invocation: the manifest requests case 22 and `results.jsonl` has one line. They therefore do not summarize the whole directory. All 22 case directories contain `audit.jsonl`, `status.json`, and `result.json`, and the persisted score artifact contains 22 cases. Those case directories and the score artifact are the evidence for campaign completeness.

### Historical/diagnostic scores and outcomes

- `qwen3_8b_colregs_rag` (early 120-cycle summary-RAG): retrospective score `0.636070`; 12 PASS, 3 CPA violations, 7 non-arrivals. This is not the later matched 240-cycle RAG run.
- `base_raw_state_warp5` (superseded raw-state run): `0.483487`; 5 PASS, 10 CPA violations, 3 scorer collisions, 4 non-arrivals.
- `20261003T213137Z_finetuned` (cases 3–22 only): audit recomputation gives `0.605948`; 7 PASS, 7 CPA violations, 6 non-arrivals. It is not a 22-case mean.
- `20261005T103227Z_finetuned` (cases 3–22 only): audit recomputation gives `0.613641`; 7 PASS, 7 CPA violations, 6 non-arrivals. It is not a 22-case mean.
- `privileged_luna_v1_smoke` (only cases 13, 16, and 22): audit recomputation gives `0.762634`; 1 PASS and 2 CPA violations. It is a smoke gate, not an evaluation result.
- `20261007T085216Z_dpo`: only case 1 has a result, a runner collision at 0.216 m. It cannot be assigned a campaign mean.
- `qwen3_8b_finetuned`: cases 1–2 completed; cases 3–22 failed before producing usable bridge-state series. The retrospective scorer cannot score it and it must not be treated as zero or as a completed evaluation.
- `colregs_rag_collision_replay_warp5`: case 3 completed in 117 cycles with recorded minimum range 88.300 m; case 15 failed with exit status 143 after 31 cycles and recorded minimum 2,088.182 m; case 17 has an audit but no result; requested cases 20 and 22 have no case directories.
- `colregs_rag_collision_replay_warp5_240cycles`: only case 3 exists and failed before cycle 1 (`exit_status=1`, zero decisions, no minimum range). It is not evidence about RAG performance.

## Raw runner status versus retrospective verdict

Do not mix these labels:

- A runner `collision` comes from the campaign runtime/result logic.
- A retrospective scorer collision is defined by reconstructed minimum clearance below 15 m.
- A scorer non-arrival is determined from mission-completion/goal-crossing reconstruction and capped at 0.2.

This is why the COLREG-RAG runner reports six collisions while its retrospective score table reports six non-arrivals and zero scorer collisions. It is also why the MAIB runner reports all 22 cases complete while the scorer assigns case 19 a collision at 14.073 m. Both statements are correct under their respective definitions; neither should be relabelled as the other.

## Result-level conclusions supported by this audit

1. The base narrative is the strongest controlled Warp-5 variant by composite score and clean-pass count.
2. Supplying deterministic Eriksen role/rule information improves citation-set accuracy substantially but does not improve physical performance over the base narrative.
3. The completed COLREG RAG experiments are summary-RAG experiments. There is no completed full-verbatim-COLREG Warp-5 OOW experiment in this inventory.
4. The first Luna SFT result is invalid as a model-quality comparison because its own provenance marker documents imbalance and hidden-evidence references.
5. The corrected privileged-teacher SFT removes catastrophic completion failures but still underperforms base narrative.
6. The MAIB-RAG captain improves the composite relative to Eriksen, raw-state, COLREG-RAG, and both SFT variants, but not base narrative; it still has one scorer collision and nine other CPA violations.
7. MAIB retrieval is sparse and highly concentrated: only 513/1,867 cycles retrieve evidence, and six chunks account for every inserted excerpt.
8. The historical and incomplete runs are useful diagnostics but are not controlled peers of the seven-variant 22-case table.

## Authoritative local evidence

- Main per-case comparison: `reports/imazu_evaluation_dossier/per_case_scores.csv`
- Combined narrative score artifact: `reports/imazu_evaluation_dossier/scores/base_narrative_combined/scores.json`
- Combined raw-state score artifact: `reports/imazu_evaluation_dossier/scores/base_raw_state_combined/scores.json`
- Main prompt archive: `reports/imazu_evaluation_dossier/prompt_records.jsonl`
- Existing detailed dossier: `reports/imazu_evaluation_dossier/REPORT.md`
- Per-run score artifacts: `logs/evaluations/imazu/<run>/scores/scores.json`
- RAG rendering implementation: `experiments/qwen8b_colregs_rag/retrieval.py`
- RAG summary content: `experiments/qwen8b_colregs_rag/colregs_ship_guidance.json`
- Invalid SFT marker: `sft/output/teacher_campaigns_luna_20261021_base_narrative/INVALID_SFT_PROVENANCE.json`
- Corrected SFT manifest: `sft/output/privileged_teacher_base_narrative_v1/manifest.json`
- Every exact runtime prompt, decision, retrieval record, and cycle ID: each campaign's `case_XX/audit.jsonl`

No value in the controlled aggregate or per-case tables was inferred from a directory name. Every value traces to a manifest, audit event, result file, persisted score artifact, or the explicitly documented rerun overlay.
