# Imazu model results: thesis notes

This document consolidates the controlled 22-scenario, time-warp-5 results for
the OOW and Captain variants used in the thesis. Headline numbers come from the
frozen `imazu-frozen-v1` artifacts generated on 2026-11-03. The cycle examples
come directly from the corresponding `audit.jsonl` files.

This is the current results summary. The older
`IMAZU_WARP5_EXPERIMENTS_AUDITED.md` and
`imazu_evaluation_dossier/REPORT.md` remain useful for provenance and full
per-case tables, but their headline composites predate the frozen citation axis
and must not replace the scores below.

The final retained-configuration table, including directional narrative SFT v7
and audited decision-latency distributions, is in
`IMAZU_RETAINED_CONFIGS_LATENCY.md`. That table supersedes the older SFT rows in
this document; SFT v0, v1, and v4 are no longer retained thesis configurations.

## Evaluation method

Each condition is evaluated on the same 22 Imazu scenarios at time warp 5. The
frozen composite uses these weights:

| Axis | Weight |
|---|---:|
| Safety (minimum realized clearance relative to 500 m) | 0.35 |
| Physical COLREG compliance | 0.10 |
| Rule-citation agreement | 0.10 |
| Temporal efficiency | 0.10 |
| Spatial efficiency | 0.10 |
| Manoeuvre quality | 0.10 |
| Smoothness | 0.15 |

The safety axis is `min(1, minimum clearance / 500 m)`. A reconstructed
clearance below 15 m is scored as a collision and makes the complete case score
zero. A case that does not arrive is capped at 0.20. Rule-citation agreement is
macro per-cycle F1 against the Eriksen applicable-rule set, union persistent
Rules 2, 5, 6, and 7. Explanatory prose does not otherwise affect physical
compliance.

Base narrative and raw state use predefined rerun overlays. Narrative uses
rerun cases 2--16 and original cases 1 and 17--22. Raw state uses rerun cases
1--4 and original cases 5--22. Superseded cases are not counted twice.

## Architectures compared

| Condition | Executing architecture | State representation | Additional knowledge supplied at inference |
|---|---|---|---|
| Base narrative | Qwen3-8B OOW | Deterministic natural-language bridge narrative with recent developments | No supplied role or applicable-rule list |
| Base raw state | Qwen3-8B OOW | Structured JSON state, including recent states | No supplied role or applicable-rule list |
| Base + Eriksen/all rules | Qwen3-8B OOW | Narrative plus deterministic encounter assessment | Own role, encounter state, persistent rules, and applicable rule summaries |
| COLREG summary RAG | Qwen3-8B OOW with retrieval | Narrative plus retrieved operational rule summaries | Retrieved summaries; not verbatim COLREG text and no authoritative per-contact role |
| Privileged-teacher SFT v1 | Qwen3-8B LoRA OOW | Same base-narrative runtime input | No privileged evidence at inference; privileged Luna evidence was used only to create filtered targets |
| Narrative OOW + Captain critic | Qwen3-8B OOW followed by a Qwen3-8B reviewing Captain | OOW proposal plus bridge state and MAIB lesson-card evidence | Captain could approve or replace the OOW control; no deterministic override |
| Independent MAIB-RAG Captain | Qwen3-8B Captain acting independently | Structured bridge state plus zero or two accident-report excerpts | Advisory MAIB evidence; no deterministic role/action override |
| Luna SFT v0 | Earlier Qwen3-8B LoRA OOW | Same base narrative | Invalid training provenance; retained only as a pipeline-failure diagnostic |

No completed matched Warp-5 OOW experiment supplied full verbatim COLREG text.
The COLREG-RAG condition retrieved from the full corpus but rendered maintained
operational summaries into the model-facing context.

The deterministic MOOS-IvP reference controller is reported separately below
because it was evaluated at time warp 50 rather than time warp 5. It used
`BHV_AvdColregsV22`, received simulator state directly, and did not use a
language-model prompt, retrieval, or generated rule citations.

## Aggregate results

| Rank | Condition | Mean score | Clean pass | CPA violation | Collision | Non-arrival | Safety | Compliance | Citation F1 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Base narrative, combined reruns | **0.859067** | 19 | 3 | 0 | 0 | **0.959695** | 0.788636 | 0.022402 |
| 2 | Base + Eriksen/all rules | 0.856358 | 9 | 12 | 0 | 1 | 0.772350 | 0.613636 | **0.952939** |
| 3 | Privileged-teacher SFT v1 | 0.747614 | 7 | 15 | 0 | 0 | 0.581657 | 0.438636 | 0.560276 |
| 4 | Narrative OOW + Captain critic | 0.717124 | 13 | 7 | 2 | 0 | 0.747168 | 0.696364 | 0.000947 |
| 5 | Independent MAIB-RAG Captain (diagnostic) | 0.705090 | 12 | 9 | 1 | 0 | 0.682268 | 0.718182 | 0.004142 |
| 6 | Base raw state, combined reruns | 0.701804 | 11 | 8 | 0 | 3 | 0.702757 | 0.463636 | 0.009595 |
| 7 | COLREG summary RAG | 0.664911 | 12 | 4 | 0 | 6 | 0.659387 | 0.504545 | 0.460409 |
| 8 | Luna SFT v0, invalid provenance | 0.361124 | 0 | 9 | 8 | 5 | 0.261711 | 0.143182 | 0.784583 |

The overall mean includes every scenario. Collision cases score zero and
non-arrivals are capped, so applying the weights to the displayed mean axes is
not generally sufficient to reconstruct the overall mean.

### Deterministic MOOS-IvP reference baseline (warp 50)

| Controller | Mean score | Completed-case mean | Clean pass | CPA violation | Collision | Non-arrival | Safety | Compliance | Citation F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MOOS-IvP `BHV_AvdColregsV22` | **0.625646** | 0.785264 | 16 | 0 | 0 | 6 | 1.000000 | 0.479545 | 0.000000 |

All 22 scenarios were attempted. The controller maintained at least 800.412 m
of reconstructed clearance in every scenario, so every case received a safety
axis of 1.0 and there were no collisions or 500 m CPA violations. It reached
the goal in 16 cases; cases 3, 7, 15, 17, 20, and 22 did not arrive and were
therefore capped at 0.20. The zero citation score is structural: a deterministic
MOOS-IvP behavior does not emit OOW/Captain rule citations. Its physical
compliance score was reconstructed from observed heading and speed changes,
using the same retrospective geometry policies as the model runs.

## Clearance and terminal-outcome diagnostics

The aggregate score can conceal materially different behavior. The table below
therefore reports the realized-clearance distribution and terminal outcomes.
“Below 500 m” counts every scenario whose reconstructed minimum clearance was
strictly below the evaluation threshold, including collisions and non-arrivals.

| Condition | Completed | Below 500 m | Rate below 500 m | Collisions | Non-arrivals | Median clearance | Worst clearance |
|---|---:|---:|---:|---:|---:|---:|---:|
| Base narrative | 22/22 | 3 | 13.6% | 0 | 0 | 769.8 m | case 13: 296.1 m |
| Base + Eriksen/all rules | 21/22 | 13 | 59.1% | 0 | 1 | 406.7 m | case 13: 45.9 m |
| Privileged-teacher SFT v1 | 22/22 | 15 | 68.2% | 0 | 0 | 256.7 m | case 4: 54.3 m |
| Narrative OOW + Captain critic | 20/22 | 9 | 40.9% | 2 | 0 | 682.0 m | case 1: 0.1 m |
| Independent MAIB-RAG Captain | 21/22 | 10 | 45.5% | 1 | 0 | 610.7 m | case 19: 14.1 m |
| Base raw state | 19/22 | 11 | 50.0% | 0 | 3 | 570.8 m | case 18: 17.4 m |
| COLREG summary RAG | 16/22 | 10 | 45.5% | 0 | 6 | 738.6 m | case 7: 37.9 m |
| Luna SFT v0, invalid provenance | 9/22 | 22 | 100.0% | 8 | 5 | 36.1 m | case 20: 0.2 m |
| MOOS-IvP, warp 50 | 16/22 | 0 | 0.0% | 0 | 6 | 1,017.5 m | case 16: 800.4 m |

The following scenario-by-scenario matrix reports minimum reconstructed
clearance in metres. Bold values are strictly below 500 m, `C` denotes a
collision, and `N` denotes failure to reach the destination. SFT v0 is retained
only as an invalid-provenance diagnostic; MOOS-IvP was run at warp 50.

| Scenario | Narrative | Raw | Eriksen | COLREG RAG | SFT v1 | Captain critic | MAIB independent | SFT v0 | MOOS-IvP |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 592.2 | 957.6 | **350.8** | **293.5** | 664.4 | **0.1 C** | 1,777.3 | **497.0** | 924.7 |
| 2 | 902.0 | 857.9 | 657.2 | 1,149.9 | **127.8** | **471.1** | 1,412.0 | **494.5** | 1,480.6 |
| 3 | 1,053.0 | 1,083.5 | 611.6 | **46.2 N** | 628.4 | 1,260.4 | **49.8** | **463.3** | 1,348.1 N |
| 4 | **301.2** | **33.2 N** | **73.0** | 1,545.4 | **54.3** | **220.8** | **33.9** | **203.8** | 1,007.7 |
| 5 | 981.4 | 1,027.3 | 676.2 | **281.7** | 768.1 | 719.2 | 1,087.5 | **0.9 C** | 1,379.6 |
| 6 | 532.5 | **91.5** | **297.0** | 760.6 | **250.6** | 512.4 | 691.2 | **168.1** | 1,204.4 |
| 7 | 1,391.6 | 1,153.9 | 648.2 | **37.9 N** | 650.2 | **11.1 C** | **175.3** | **29.1 N** | 1,347.3 N |
| 8 | 847.6 | 961.8 | 611.7 | 761.2 | 982.6 | 1,464.1 | 1,444.8 | **215.9** | 1,379.3 |
| 9 | 892.4 | **300.9** | **475.5** | 1,689.0 | 501.7 | 1,200.6 | 1,219.4 | **234.9** | 929.1 |
| 10 | 882.0 | 714.0 | **347.4** | 985.8 | **262.7** | 1,120.2 | 771.5 | **7.1 C** | 1,003.5 |
| 11 | 659.3 | **246.0** | **423.4** | **131.3** | **351.9** | **56.6** | 1,153.0 | **239.6** | 861.1 |
| 12 | **459.3** | **468.8** | **390.1** | 845.7 | **297.9** | **350.9** | 875.8 | **145.7** | 1,027.4 |
| 13 | **296.1** | **216.0** | **45.9 N** | **288.9** | **118.9** | **31.6** | **369.5** | **0.5 C** | 978.5 |
| 14 | 507.9 | **98.4** | **347.2** | 789.1 | **59.1** | **483.9** | **433.5** | **34.4 N** | 1,277.9 |
| 15 | 1,197.8 | 641.5 | 801.9 | **44.8 N** | **137.2** | **92.9** | **117.4** | **28.5 N** | 1,332.4 N |
| 16 | 642.3 | **499.98** | 515.8 | 893.7 | **98.8** | 1,118.9 | 530.1 | **11.9 C** | 800.4 |
| 17 | 1,121.3 | 877.0 | 838.3 | **47.2 N** | **123.2** | 1,017.9 | **146.4** | **3.7 C** | 983.9 N |
| 18 | 570.7 | **17.4 N** | **255.1** | 880.4 | **182.6** | 644.7 | 1,009.0 | **14.8 C** | 851.6 |
| 19 | 682.5 | **25.6 N** | **340.2** | 716.7 | **204.2** | 903.3 | **14.1 C** | **42.8 N** | 923.5 |
| 20 | 803.9 | 834.5 | **354.7** | **38.2 N** | **423.3** | 818.4 | **106.6** | **0.2 C** | 1,258.9 N |
| 21 | 735.7 | **232.5** | **295.5** | 1,031.0 | **205.5** | 722.2 | 1,174.3 | **37.8 N** | 916.4 |
| 22 | 1,333.9 | 1,207.6 | 804.7 | **43.6 N** | 711.5 | 1,646.5 | **58.4** | **4.2 C** | 1,336.9 N |

Across the seven valid language-model configurations, scenario 13 is the only
scenario below 500 m for every configuration. Scenario 4 is below threshold for
six of seven configurations, while scenarios 11, 12, and 14 are below threshold
for five. Scenario 8 is the only scenario in which all seven remain above
500 m. These repeated cross-model failures identify scenario difficulty rather
than a failure unique to one prompt or architecture.

These diagnostics expose three results that the composite alone does not. The
base narrative condition provides the strongest joint completion-and-clearance
result among the language-model conditions. SFT v1 eliminates terminal failures
but does so with a policy that accepts small separation in most scenarios.
MOOS-IvP maintains the largest separation distribution but trades this against
six failures to reach the destination.

## Action distribution

The frozen audit normalizes executed actions into five directional categories:
hold course, turn starboard, turn port, speed up, and slow down. For the
language-model configurations these counts use audited `captain_validated`
decisions. Percentages are preferable to raw counts because scenarios contain
different numbers of decision cycles.

| Condition | Total decisions | Hold course | Turn starboard | Turn port | Speed up | Slow down |
|---|---:|---:|---:|---:|---:|---:|
| Base narrative | 4,077 | 3,040 (74.6%) | 1,032 (25.3%) | 0 (0.0%) | 5 (0.1%) | 0 (0.0%) |
| Base raw state | 3,452 | 3,038 (88.0%) | 413 (12.0%) | 1 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Base + Eriksen/all rules | 3,205 | 2,717 (84.8%) | 488 (15.2%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| COLREG summary RAG | 4,116 | 2,956 (71.8%) | 603 (14.7%) | 557 (13.5%) | 0 (0.0%) | 0 (0.0%) |
| Privileged-teacher SFT v1 | 2,739 | 2,610 (95.3%) | 109 (4.0%) | 20 (0.7%) | 0 (0.0%) | 0 (0.0%) |
| Narrative OOW + Captain critic | 1,171 | 1,000 (85.4%) | 151 (12.9%) | 20 (1.7%) | 0 (0.0%) | 0 (0.0%) |
| Independent MAIB-RAG Captain | 1,868 | 1,724 (92.3%) | 144 (7.7%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Luna SFT v0, invalid provenance | 1,878 | 1,855 (98.8%) | 10 (0.5%) | 13 (0.7%) | 0 (0.0%) | 0 (0.0%) |

The action distribution helps explain several physical results. Base narrative
is the most strongly starboard-oriented OOW condition, with starboard
alterations accounting for 25.3% of its decisions. SFT v1 holds course on
95.3% of decisions, consistent with its frequent no-action-when-required
findings and small median clearance. COLREG RAG makes nearly as many port turns
as starboard turns; this agrees with its 33 wrong-direction and 15
port-toward-contact retrospective deductions. Action frequency alone is not a
quality measure, but it identifies materially different learned or prompted
control policies.

MOOS-IvP is not directly comparable in this table because no discrete model
decision stream exists. Reconstruction from sampled heading and speed intervals
produces 10,158 observations: 3,492 holds (34.4%), 4,764 starboard turns
(46.9%), 1,777 port turns (17.5%), 48 speed increases (0.5%), and 77 speed
reductions (0.8%). These figures describe sampled controller motion rather than
the frequency of high-level decisions.

## Composite score by scenario

The following table reports the frozen composite for every scenario and
configuration. `C` denotes a collision, whose score is forced to zero, and `N`
denotes a non-arrival, whose score is capped at 0.20. SFT v0 is an
invalid-provenance diagnostic and MOOS-IvP was run at warp 50.

| Scenario | Narrative | Raw | Eriksen | COLREG RAG | SFT v1 | Captain critic | MAIB independent | SFT v0 | MOOS-IvP |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.902083 | 0.897825 | 0.862854 | 0.711181 | 0.936228 | 0.000000 C | 0.867622 | 0.949322 | 0.881650 |
| 2 | 0.901687 | 0.895066 | 0.995929 | 0.950722 | 0.660222 | 0.839220 | 0.888293 | 0.902518 | 0.782667 |
| 3 | 0.894285 | 0.892154 | 0.994819 | 0.200000 N | 0.928235 | 0.888522 | 0.502195 | 0.829276 | 0.200000 N |
| 4 | 0.653312 | 0.200000 N | 0.594788 | 0.869084 | 0.566607 | 0.619116 | 0.500418 | 0.733758 | 0.741912 |
| 5 | 0.900164 | 0.895197 | 0.994202 | 0.702204 | 0.946143 | 0.888155 | 0.893336 | 0.000000 C | 0.754064 |
| 6 | 0.897011 | 0.513403 | 0.818704 | 0.922310 | 0.742498 | 0.888689 | 0.856989 | 0.647312 | 0.747000 |
| 7 | 0.891079 | 0.893385 | 0.991871 | 0.200000 N | 0.937957 | 0.000000 C | 0.565846 | 0.200000 N | 0.200000 N |
| 8 | 0.901603 | 0.895855 | 0.994476 | 0.893943 | 0.961660 | 0.888161 | 0.894534 | 0.715248 | 0.754100 |
| 9 | 0.895758 | 0.659129 | 0.946621 | 0.917596 | 0.944921 | 0.885539 | 0.891808 | 0.772104 | 0.809374 |
| 10 | 0.793817 | 0.805250 | 0.784253 | 0.895713 | 0.673362 | 0.803002 | 0.874676 | 0.000000 C | 0.864684 |
| 11 | 0.863026 | 0.620632 | 0.893491 | 0.548995 | 0.801946 | 0.525197 | 0.885465 | 0.763094 | 0.766633 |
| 12 | 0.836261 | 0.843694 | 0.885354 | 0.904799 | 0.767215 | 0.701206 | 0.850478 | 0.632087 | 0.844686 |
| 13 | 0.644641 | 0.584096 | 0.200000 N | 0.676975 | 0.577128 | 0.463836 | 0.671239 | 0.000000 C | 0.736674 |
| 14 | 0.895297 | 0.518825 | 0.855494 | 0.926301 | 0.541517 | 0.827371 | 0.763732 | 0.200000 N | 0.750114 |
| 15 | 0.890135 | 0.895869 | 0.993368 | 0.200000 N | 0.593152 | 0.441909 | 0.533757 | 0.200000 N | 0.200000 N |
| 16 | 0.830499 | 0.795797 | 0.890274 | 0.832708 | 0.580758 | 0.851710 | 0.892355 | 0.000000 C | 0.714426 |
| 17 | 0.843677 | 0.834241 | 0.940321 | 0.200000 N | 0.578635 | 0.868815 | 0.495220 | 0.000000 C | 0.200000 N |
| 18 | 0.897399 | 0.200000 N | 0.789479 | 0.926156 | 0.685062 | 0.882193 | 0.861716 | 0.000000 C | 0.773373 |
| 19 | 0.890950 | 0.200000 N | 0.813449 | 0.831088 | 0.643371 | 0.873758 | 0.000000 C | 0.200000 N | 0.848952 |
| 20 | 0.891101 | 0.895159 | 0.857097 | 0.200000 N | 0.796585 | 0.872932 | 0.478898 | 0.000000 C | 0.200000 N |
| 21 | 0.896115 | 0.612228 | 0.750542 | 0.918273 | 0.649941 | 0.882925 | 0.849508 | 0.200000 N | 0.793905 |
| 22 | 0.889573 | 0.891875 | 0.992483 | 0.200000 N | 0.934369 | 0.884485 | 0.493892 | 0.000000 C | 0.200000 N |
| **Mean** | **0.859067** | **0.701804** | **0.856358** | **0.664911** | **0.747614** | **0.717124** | **0.705090** | **0.361124** | **0.625646** |

## Model-by-model interpretation

### Base narrative

This is the strongest condition. It reached the destination in all 22 cases,
passed the 500 m requirement in 19, and had no collision. Its natural-language
state representation appears easier for the small model to use than raw JSON,
while preserving high temporal and spatial efficiency.

An effective example is case 8: score `0.901603`, minimum clearance `847.552 m`.
At cycle 60 the OOW issued a 30-degree starboard alteration and completed the
scenario safely. The exact event is in
`logs/evaluations/imazu/base_narrative_matched_warp5_rerun/case_08/audit.jsonl`.

Its principal weakness is not physical navigation but explicit rule reporting.
Citation F1 is only `0.022402`. Reasons often contain only a short subset such
as “Rules 13 and 16”, omitting persistent Rules 2, 5, 6, and 7. It also did not
always preserve 500 m: case 4 reached only `301.223 m`, and at cycle 76 it
rejoined the route while the projected CPA was approximately 497 m. Case 13
reached `296.141 m`. These runs still arrived, explaining why the physical
result is strong despite imperfect clearance and citation behavior.

### Base raw state

Raw JSON can work: cases 1, 2, 3, 5, 7, 8, 15, and 22 all achieved more than
500 m. Case 1 scored `0.897825` with `957.582 m` minimum clearance.

However, the condition produced only 11 clean passes, eight CPA violations,
and three non-arrivals. In case 4, cycle 81, the model returned
`maintain_course` with projected CPA approximately 39 m and TCPA 208 s. The
case ultimately had a reconstructed minimum of `33.221 m` and did not arrive.
Cases 18 and 19 also failed to arrive, with clearances of `17.422 m` and
`25.602 m`. This supports the interpretation that the model extracts temporal
and relational structure less reliably from raw JSON than from the rendered
narrative.

### Base with Eriksen roles and applicable rules

Supplying deterministic roles and rules almost eliminated the citation problem:
citation F1 increased to `0.952939`. Case 3 illustrates a successful run: score
`0.994819`, clearance `611.579 m`; at cycle 4 the model ordered a 30-degree
starboard alteration while citing the persistent rules. Cases 2 and 8 likewise
combined a clean physical pass with near-perfect frozen scores.

The additional formal guidance did not improve aggregate physical safety.
There were 12 CPA violations and one non-arrival. Case 13 is the clearest
failure: score `0.200000`, minimum clearance `45.876 m`. At cycle 65 the model
ordered a 30-degree alteration for a 50 m CPA; at cycle 66 it immediately
ordered `rejoin_route` even though the same contact still had a 70 m CPA and
166.5 s TCPA. This alter/rejoin pattern continued and prevented arrival. The
result supports the hypothesis that explicit role information can anchor the
model too strongly to formal stand-on/give-way behavior without improving
closed-loop control.

Citation quality also needs a caveat: 41--42 decisions emitted runaway rule
lists extending through Rule 231. The high citation score therefore reflects
substantial correct set overlap, not uniformly trustworthy legal prose.

### COLREG summary RAG

Retrieval improved citation F1 to `0.460409`, well above the unconstrained base
prompts. It also produced strong individual cases: case 2 scored `0.950722`
with `1,149.941 m` clearance; at cycle 60 it issued a 45-degree starboard
alteration. Cases 4, 10, 14, 18, and 21 also achieved large safe clearances.

The condition nevertheless had six non-arrivals. In case 7, cycle 38, it
ordered a 30-degree port alteration while the retrospective geometry classified
the relevant crossing encounter as give-way; later cycles retained course in
acute overtaking geometry. The case reached only `37.926 m` and did not arrive.
Cases 3, 15, 17, 20, and 22 also failed to arrive. Frequent alter/rejoin cycles
and wrong-direction turns reduced both efficiency and physical performance.
Retrieving rule summaries therefore improved rule vocabulary more clearly than
it improved navigation.

### Privileged-teacher SFT v1

This corrected SFT condition eliminated the catastrophic failures of v0: all
22 scenarios arrived and none collided. It also had excellent temporal,
spatial, and smoothness axes. Case 8 scored `0.961660` with `982.580 m`
clearance; case 3 scored `0.928235` with `628.396 m`, and at cycle 48 issued a
30-degree starboard alteration for a projected zero-CPA encounter.

Its weakness is clearance. Fifteen of 22 cases remained below 500 m, producing
a mean safety axis of only `0.581657`. Case 4 reached `54.285 m`. At cycle 48
the numeric command was positive 30 degrees (starboard), while the explanation
called it a “port alteration” and assigned contradictory overtaking/stand-on
roles. Subsequent oscillation and port-toward-contact findings left the case
barely above the collision region. Thus SFT improved reliability and output
discipline, but it distilled a policy that accepted much smaller separations
than the frozen evaluator rewards.

### Narrative OOW with Captain critic

The Captain architecture produced 13 clean passes and several excellent cases:
case 3 scored `0.888522` with `1,260.427 m`; case 8 passed at `1,464.134 m`; and
case 22 passed at `1,646.517 m`. This demonstrates that a second model can
execute viable navigation decisions.

It did not function reliably as a safety critic. Cases 1 and 7 collided and
therefore scored zero. In case 1, cycle 34, the Captain rejected the OOW's
starboard alteration and selected `maintain_course` because it believed the
alteration would reduce CPA. It waited until cycle 36 to approve a 60-degree
alteration, then collided at `0.107 m` on cycle 37. The system prompt required
the Captain to perform many tasks and buried the narrow critic instruction, so
the experiment does not establish that a compact, purpose-built critic cannot
work; it establishes that this implemented critic did not improve the narrative
baseline.

The citation axis is effectively zero because Captain decisions almost never
cited the complete Eriksen-plus-persistent rule set.

### Independent MAIB-RAG Captain

This was a diagnostic architecture rather than the final OOW-plus-critic
comparison. It achieved 12 clean passes, including case 8 at `1,444.828 m`,
case 5 at `1,087.540 m`, and case 18 at `1,009.012 m`. Physical compliance was
relatively high (`0.718182`).

Case 19 reveals the central failure. At cycles 70--74 the Captain repeatedly
claimed that its current alteration maintained safe separation, including a
cycle-70 statement that CPA remained above 926 m. The reconstructed minimum was
actually `14.073 m`, which the frozen evaluator classifies as a collision.
Retrieval was also sparse and concentrated: 513 of 1,867 cycles returned two
excerpts, while 1,354 returned none; only six unique report chunks supplied all
1,026 inserted excerpts. Accident-report retrieval did not provide dependable
real-time geometry checking.

### Luna SFT v0: invalid diagnostic

This run must not be presented as a valid SFT model comparison. Its training
dataset was explicitly marked invalid: 70.99% of targets were
`maintain_course`, balancing was ineffective, and 1,383 targets referred to
oracle/classifier evidence absent from the student input.

The resulting policy produced only nine CPA-violation arrivals, eight
collisions, and five non-arrivals. In case 5, cycle 67, it selected
`maintain_course` despite one head-on contact having projected CPA 0 m/TCPA
97.8 s and another contact having CPA 50.7 m/TCPA 100.7 s. The realized
clearance was `0.903 m`. Its citation F1 of `0.784583` is not evidence of safe
navigation; it largely reflects the flawed target provenance.

## Cross-model conclusions suitable for the thesis

1. **Input representation mattered more than additional legal context.** The
   base narrative achieved the best physical and overall result, while the same
   base model using raw JSON had three non-arrivals and substantially lower
   safety.
2. **Knowing the applicable rules did not guarantee safer control.** Eriksen
   context raised citation F1 from `0.022` to `0.953` but reduced clean passes
   from 19 to 9.
3. **Retrieval improved access to terminology more than closed-loop behavior.**
   COLREG summary RAG improved citation agreement but introduced six
   non-arrivals. MAIB retrieval produced useful individual runs but could not
   substitute for reliable geometry assessment.
4. **SFT improved reliability but inherited its teacher's clearance policy.**
   Privileged SFT v1 removed collision and non-arrival failures but achieved the
   500 m clearance requirement in only seven scenarios.
5. **The implemented Captain did not improve the strongest OOW baseline.** The
   critic condition scored `0.717` versus `0.859` for narrative OOW alone and
   introduced two collisions.
6. **Citation correctness and physical navigation must be reported separately.**
   Their rankings differ sharply: Eriksen is best for citations, whereas base
   narrative is best for realized safety and overall score.

## Evidence and reproducibility

- Frozen aggregate report: `reports/imazu_frozen_v1/README.md`
- Per-condition frozen scores: `reports/imazu_frozen_v1/<condition>/scores.json`
- Frozen scoring contract: `tools/IMAZU_SCORING_CONTRACT.md`
- Full prompt archive and older detailed dossier:
  `reports/imazu_evaluation_dossier/`
- Complete Warp-5 provenance audit:
  `reports/IMAZU_WARP5_EXPERIMENTS_AUDITED.md`
- Exact decisions and cycle IDs:
  `logs/evaluations/imazu/<run>/case_XX/audit.jsonl`

The exact per-case current scores should be taken from the frozen `scores.csv`
files rather than copied from the older dossier, whose composite predates the
0.10 citation axis.
