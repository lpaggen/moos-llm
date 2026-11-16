# Evaluation scoring

[`tools/evaluate_run.py`](tools/evaluate_run.py) grades a recorded ship mission
using trajectories, chronological decisions, and audited COLREG findings.
Everything is deterministic; no LLM judges the scores. Scores range from 0 to 1
(higher is better). Below, `clip(x)` means limiting `x` to that range.

The **weights are fixed design choices**, not computed from data or learned.
They sum to 1. Python callers can override them with six nonnegative weights
summing to 1; the CLI uses the defaults.

| Component | Weight | What it measures and how it is computed |
|---|---:|---|
| Safety | **35%** | Clearance from other vessels: `clip(d_min / 500 m)`. `d_min` is the smallest realized separation across all contacts, using linearly interpolated positions on a one-second grid, including the final instant. No contacts gives 1. |
| Physical compliance | **20%** | Safe, COLREG-correct actions: `clip(1 − sum of physical finding penalties)`. Penalties below. |
| Temporal efficiency | **10%** | Time against a direct-route baseline: `clip((D / nominal_speed) / elapsed_time)`. `D` is start-to-goal distance; elapsed time ends at the first recorded position within 20 m of the goal. |
| Spatial efficiency | **10%** | Route directness: `clip(D / sailed_distance)`. Sailed distance is the sum of recorded position-to-position distances up to arrival. |
| Manoeuvre / decisiveness | **10%** | Avoiding contradictory orders: `clip(1 − reversal_rate / 0.30)`. `reversal_rate = reversals / ((N − 1) × max(1, contacts))`, where `N` is the number of decisions. Fewer than two decisions gives 1. |
| Smoothness | **15%** | Gentle motion: `0.5 × clip(1 − mean_heading_rate / 5) + 0.5 × clip(1 − mean_speed_rate / 1)`. Rates are absolute changes divided by each interval's duration, then averaged equally across intervals; units are degrees/s and m/s². Heading changes wrap at 360°. |

Turn reversals are left ↔ right; speed reversals are accelerate ↔ decelerate.
They are counted independently. Holding course and stopping do not reset the
remembered directions. Repeating the same order adds no reversal.

For an arrived, collision-free run:

```text
score = 0.35×safety + 0.20×compliance + 0.10×temporal
      + 0.10×spatial + 0.10×manoeuvre + 0.15×smoothness
```

**Overrides:** clearance strictly below **15 m** forces score **0** and collision
failure. Failure to arrive sets temporal/spatial scores to 0 and uses
`min(0.20, 0.20×compliance + 0.10×manoeuvre + 0.15×smoothness)`.
Arrival without collision gives `PASS`, or `PASS_WITH_CPA_VIOLATION` if clearance
is below the configured safe distance (default 500 m). Other compliance findings
lower the score but do not change that verdict. Safe distance, collision radius,
and arrival radius are configurable.

Physical compliance deductions (absolute deductions from the component score):

| Finding code | Penalty |
|---|---:|
| `B_wrong_direction`, `P_port_toward_contact`, `D_no_action_when_required`, `B_17c` | **0.15 each** |
| `P_wrong_side_pass`, `cpa_violation` | **0.30 each** |
| `C_degrees_over_limit` | **0.03** |

Each checkpoint/code pair deducts once; the same code at different checkpoints
deducts repeatedly. Wrong-side passing deducts once per run. CPA violation is
recomputed from clearance and deducts once per non-collision run. For example,
250 m clearance gives safety 0.5 and a 0.30 compliance deduction; with all other
components perfect, the composite is **0.765**. Thus close passes affect both
safety and compliance.

**Explanation compliance has zero composite weight.** It separately reports
`clip(1 − explanation penalties)`: role fabrication **0.15**; encounter mismatch,
unclassified encounter, and misapplied Rule 8 citation **0.05 each**; fabricated
risk **0.03**. Role fabrication supersedes fabricated risk at the same checkpoint.
Collision sets both compliance scores to 0.

Verification: the scoring formulas match the supplied design reference and all
14 evaluator tests pass, covering hand-calculated scores, gates, thresholds,
deduplication, reversals, smoothness, validation, and CLI execution.
The smoothness formula is **linear**, despite the design prose calling it quadratic.
The one-second safety grid can miss subsecond collisions; arrival uses recorded
rows, and irregular sampling changes the equally averaged smoothness rates.
Safety/smoothness use the full recording, while efficiency stops at arrival.
Empty decisions earn full decisiveness marks; missing audited findings can make
compliance overly optimistic. Only CPA violations are detected automatically here.

[`tools/score_baseline_logs.py`](tools/score_baseline_logs.py) reuses these weights
but estimates decisions and findings from `audit.jsonl` or legacy
`telemetry.jsonl` logs, and reconstructs arrival and clearance with separate
policies. Its outputs are retrospective estimates. A complete 22-case run from
`run_imazu_evaluation.sh` automatically writes `scores/scores.json` and
`scores/scores.csv` inside the run directory.
See [input format and usage](tools/evaluate_run.md) for running the evaluator.
