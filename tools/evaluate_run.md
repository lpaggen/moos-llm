# OOW run scoring

`tools/evaluate_run.py` implements the deterministic **scoring layer** in
`evaluation_function_design_and_verification (1).md`, using only the Python
standard library. It reports safety, physical compliance, explanation compliance,
temporal/spatial efficiency, decision reversals, smoothness, composite and verdict.

Run from the repository root:

```sh
python3 tools/evaluate_run.py trajectory.csv \
  --own-ship LLM_SHIP --start 0 -5556 --goal 0 5556 \
  --nominal-speed 6.173 --safe-distance 500 \
  --decisions decisions.json --findings findings.json \
  --output score.json
```

Trajectory CSV columns are `time,vehicle,x,y,heading,speed`. Coordinates and
distances are metres, times are simulation seconds, speeds are m/s and headings
are degrees. Record from mission start, including all contacts. Rows may be
interleaved or unordered; each vehicle must have unique timestamps. Every contact
track must cover the own-ship recording. No extrapolation is allowed. Safety uses
linear interpolation on a one-second grid, including the final instant, as in
the design; subsecond encounters between samples can be missed. Arrival is the
first recorded own-ship row within `--reached-radius` (default 20 m). Path and
elapsed time stop there; safety and smoothness use the full recording.

`decisions.json` contains a chronological array of action strings or objects with
an `action` field. Supported actions are `turn_right`, `turn_left`, `speed_up`,
`slow_down`, `hold_course`, `stop`. Example:

```json
["turn_right", "hold_course", "turn_left"]
```

Normalize runtime commands before scoring: `maintain_course` maps to `hold_course`,
and signed `alter_course` maps to `turn_right` for positive degrees or `turn_left`
for negative degrees. `set_speed` requires comparison with the previous commanded
speed to resolve `speed_up`/`slow_down`. The evaluator rejects unnormalized actions
instead of silently ignoring them. Empty decision streams are permitted for runs
with no decisions, but receive full decisiveness marks; provide actual decisions
for meaningful controller comparisons.

`findings.json` is an explicit array of **audited findings**:

```json
[
  {"code": "B_wrong_direction", "step": 3},
  {"code": "E_encounter_mismatch", "step": 4},
  {"code": "P_wrong_side_pass"}
]
```

All twelve codes in the reference are supported. Checkpoint codes require `step`;
run-level `P_wrong_side_pass` needs no step. Repeated codes at distinct checkpoints
deduct separately; duplicate `(step, code)` entries deduct once. Role fabrication
supersedes fabricated risk at the same step. `cpa_violation` is always recomputed
from realized clearance, overriding supplied occurrences.

This script does **not** implement the document's separate ground-truth auditor
(`score_trajectory`, encounter classification, risk horizon, or wrong-side-pass
detection). Those helpers belong to a different simulator and are absent here;
the document does not fully specify their thresholds. Supply their findings from
an independent auditor. An explicit `[]` asserts that auditing found no findings;
omitting `--findings` is an error. Existing `audit.jsonl` and plot CSV files have
different schemas and cannot be passed directly. No automatic batch-run integration
or LLM explanation calls are included.

Python callers can import `evaluate_run` from `tools.evaluate_run` and pass an
iterable of trajectory dictionaries with the same fields, mission parameters,
`decision_events`, and `findings`. Optional `weights` must specify all six scored
axes and sum to one. Explanation compliance never contributes to the composite.
Collision forces zero; failure to arrive caps the composite at 0.2. No contacts
means `min_cpa_m` is JSON `null` and safety scores one. The smoothness calculation
uses the document's explicit linear absolute-rate formula, despite its prose
calling it quadratic. With no contacts, reversal normalization uses one contact
to avoid division by zero. Coincident start and goal are rejected because their
efficiency baseline is undefined.

Verification:

```sh
python3 -m unittest discover -s tests -p 'test_evaluate_run.py' -v
```
