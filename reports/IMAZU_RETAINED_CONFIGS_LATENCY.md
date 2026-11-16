# Retained Imazu configurations: performance and decision latency

This table is the current thesis-facing comparison set. It retains the final
directional narrative SFT v7 and excludes every superseded fine-tuning run
(SFT v0, privileged SFT v1, and narrative-ablation SFT v4). All conditions in
the primary table attempted the same 22 Imazu scenarios at time warp 5.

## Navigation results and latency

| Condition | Mean composite | Clean pass | CPA violation | Collision | Non-arrival | Decision attempts | Failed model decisions | Mean latency | Median latency | P95 latency | Mean relative to base |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Base narrative | **0.859067** | **19** | **3** | 0 | 0 | 4,077 | 0 (0.0%) | 2.084 s | 1.831 s | 3.708 s | 1.00x |
| Base raw state | 0.701804 | 11 | 8 | 0 | 3 | 3,452 | 0 (0.0%) | 2.412 s | 2.343 s | 3.939 s | 1.16x |
| Base + Eriksen/all rules | 0.856358 | 9 | 12 | 0 | 1 | 3,205 | 0 (0.0%) | 2.976 s | 2.649 s | 4.633 s | 1.43x |
| COLREG summary RAG | 0.664911 | 12 | 4 | 0 | 6 | 4,116 | 0 (0.0%) | **1.940 s** | **1.444 s** | **3.541 s** | 0.93x |
| Narrative OOW + Captain critic | 0.717124 | 13 | 7 | 2 | 0 | 1,171 | 65 Captain failures (5.6%) | 12.993 s | 11.623 s | 26.492 s | 6.23x |
| Independent MAIB-RAG Captain | 0.705090 | 12 | 9 | 1 | 0 | 1,867 | 0 (0.0%) | 5.556 s | 5.362 s | 8.742 s | 2.67x |
| **Directional narrative SFT v7** | 0.850140 | 10 | 12 | 0 | 0 | 2,516 | 64 OOW failures (2.5%) | 4.610 s | 4.313 s | 6.673 s | 2.21x |

The composite and outcome categories use the frozen `imazu-frozen-v1`
contract. A CPA violation means reconstructed minimum clearance below 500 m
without a scorer-defined collision. Collisions and non-arrivals are mutually
exclusive terminal categories in this table.

## Timing definition

Latency is taken from every audited, non-skipped `cycle_performance` record.
The mean therefore describes the wall-clock delay actually incurred by a
decision attempt, including attempts whose model output subsequently failed.
The median is the 50th percentile and P95 is the nearest-rank 95th percentile.
Time warp does not accelerate model inference.

For the standard OOW and critic pipelines, executing latency is the recorded
end-to-end `total_duration_s`. For the independent MAIB-RAG Captain, the
executing action comes from the Captain. Its latency is therefore Captain plus
retrieval/lookout/revalidation time; the separate OOW call was recorded only
as a post-decision diagnostic and is excluded from its 5.556-second executing
latency. Including that diagnostic OOW call gives a recorded mean cycle time of
8.801 seconds.

## Mean component timings

| Condition | OOW mean | Captain mean | Executing mean | Recorded total mean |
|---|---:|---:|---:|---:|
| Base narrative | 2.082 s | -- | 2.084 s | 2.084 s |
| Base raw state | 2.410 s | -- | 2.412 s | 2.412 s |
| Base + Eriksen/all rules | 2.974 s | -- | 2.976 s | 2.976 s |
| COLREG summary RAG | 1.937 s | -- | 1.940 s | 1.940 s |
| Narrative OOW + Captain critic | 2.956 s | 10.024 s | 12.993 s | 12.993 s |
| Independent MAIB-RAG Captain | 3.241 s (diagnostic only) | 5.518 s | 5.556 s | 8.801 s |
| Directional narrative SFT v7 | 4.607 s | -- | 4.610 s | 4.610 s |

Retrieval durations recorded by the MAIB pipelines were small relative to
generation (critic mean 8.7 ms; independent Captain mean 38.5 ms). The COLREG
summary-RAG audit records zero in the dedicated retrieval timer, so its table
entry must be interpreted as measured end-to-end cycle latency rather than
evidence that retrieval itself had zero computational cost.

## Interpretation

- Base narrative remains the strongest composite and has no recorded decision
  failures in the retained campaign.
- SFT v7 is close to the two best composites, but it does not beat base
  narrative: `0.850140` versus `0.859067`. It is also 2.21 times slower on
  average and violates the 500 m threshold in 12 rather than 3 scenarios.
- Eriksen/all-rules obtains a similar aggregate score largely through its very
  high citation agreement, while its physical clearance outcome is materially
  worse than base narrative.
- The critic architecture is the slowest condition by a large margin because
  it serially invokes both OOW and Captain models. Its 65 Captain failures are
  retained in the latency denominator.
- Decision count is not a throughput measure by itself. Runs differ in mission
  duration and terminal outcome, so latency comparisons should use per-attempt
  distributions rather than total campaign time.

## Provenance and exclusions

- Base narrative uses rerun cases 2--16 and original cases 1 and 17--22.
- Base raw state uses rerun cases 1--4 and original cases 5--22.
- SFT v7 is
  `logs/evaluations/imazu/privileged_luna_narrative_directional_v7_full_warp5`.
  It completed all 22 cases. Its score artifact reports safety `0.781155`,
  physical compliance `0.522727`, citation F1 `0.824767`, temporal efficiency
  `0.959169`, spatial efficiency `0.989282`, manoeuvre quality `0.983158`, and
  smoothness `0.992171`.
- The incomplete high-reasoning lockstep campaign is not included: it was
  interrupted during case 7 and is not a matched 22-case result.
- MOOS-IvP is omitted from the latency comparison because it is a deterministic
  controller rather than a model decision pipeline and its reference campaign
  used warp 50.
- The reported latency is empirical for the recorded campaigns. Prompt length,
  output length, server version, caching, and machine load were not held fixed
  as a dedicated latency microbenchmark, so small differences (especially
  COLREG summary RAG versus base narrative) should not be treated as intrinsic
  architecture speedups.
