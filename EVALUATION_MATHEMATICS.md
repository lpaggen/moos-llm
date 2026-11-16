# Evaluation Function Mathematics

The automatically generated campaign scores use a deterministic six-axis
retrospective evaluation function. All component and composite scores are bounded
between zero and one. Define

$$
\operatorname{clip}(x)=\min(1,\max(0,x)).
$$

For a successful, collision-free run, the composite score is

$$
S=
0.35S_{\mathrm{safety}}
+0.20S_{\mathrm{compliance}}
+0.10S_{\mathrm{temporal}}
+0.10S_{\mathrm{spatial}}
+0.10S_{\mathrm{manoeuvre}}
+0.15S_{\mathrm{smoothness}}.
$$

The weights are fixed design choices and sum to one.

## 1. Safety

Let $d_{\min}$ be the minimum realized separation from any contact. The safety
score is

$$
S_{\mathrm{safety}}
=\operatorname{clip}\left(\frac{d_{\min}}{500}\right).
$$

Contact positions are linearly interpolated on a one-second grid. The
retrospective campaign scorer uses the smaller of the minimum logged range and
the minimum interpolated range over each contact's recorded overlap. If
$d_{\min}<15\,\mathrm{m}$, the run is classified as a collision and its total
score is forced to zero.

## 2. Physical COLREG Compliance

The physical compliance score is

$$
S_{\mathrm{compliance}}
=\operatorname{clip}\left(1-\sum_f p_f\right),
$$

where $p_f$ is the penalty associated with each unique finding.

| Finding | Penalty |
|---|---:|
| Wrong turn direction | 0.15 |
| Port alteration toward contact | 0.15 |
| No action during acute risk | 0.15 |
| Premature stand-on-vessel action | 0.15 |
| Wrong-side passing | 0.30 |
| Clearance below 500 m | 0.30 |
| Course alteration over 120 degrees | 0.03 |

Each checkpoint/code pair is deducted once, while the wrong-side-passing and
clearance findings are run-level deductions. The automatic campaign scorer does
not currently score explanation or rule-citation quality.

## 3. Temporal Efficiency

Let $D$ be the straight-line start-to-goal distance, $v_n$ the nominal speed,
and $T$ the realized travel time. The direct-route baseline time is

$$
T_{\mathrm{baseline}}=\frac{D}{v_n}.
$$

The temporal score is

$$
S_{\mathrm{temporal}}
=\operatorname{clip}\left(\frac{T_{\mathrm{baseline}}}{T}\right)
=\operatorname{clip}\left(\frac{D}{v_nT}\right).
$$

Arrival is detected when the interpolated trajectory enters a 20 m radius around
the goal. A logged mission-completion event is used as a fallback when coarse
trajectory observations miss the goal-radius crossing.

## 4. Spatial Efficiency

Let $L$ be the realized path length. The spatial score is

$$
S_{\mathrm{spatial}}
=\operatorname{clip}\left(\frac{D}{L}\right),
$$

where

$$
L=\sum_{i=1}^{n-1}
\sqrt{(x_{i+1}-x_i)^2+(y_{i+1}-y_i)^2}.
$$

## 5. Manoeuvre Decisiveness

Let $R$ be the number of contradictory direction changes, $N$ the number of
inferred action intervals, and $C$ the number of contacts. The reversal rate is

$$
r=\frac{R}{\max(0,N-1)\max(1,C)},
$$

and the manoeuvre score is

$$
S_{\mathrm{manoeuvre}}
=\operatorname{clip}\left(1-\frac{r}{0.30}\right).
$$

Contradictions are port-to-starboard or starboard-to-port reversals and
acceleration-to-deceleration or deceleration-to-acceleration reversals.

For automatic campaign scoring, actions are reconstructed between successive
bridge observations. The wrapped heading change is

$$
\Delta\psi_i=
((\psi_{i+1}-\psi_i+180)\bmod 360)-180.
$$

An interval is classified as follows:

- If $|\Delta\psi_i|\geq1^\circ$, it is a left or right turn.
- Otherwise, if $|\Delta v_i|\geq0.1\,\mathrm{m/s}$, it is an acceleration or
  deceleration.
- Otherwise, it is classified as holding course.

Heading changes take priority over simultaneous speed changes during this
reconstruction.

## 6. Smoothness

For each observation interval, the absolute heading and speed rates are

$$
\omega_i=\frac{|\Delta\psi_i|}{\Delta t_i},
\qquad
a_i=\frac{|\Delta v_i|}{\Delta t_i}.
$$

Their equally weighted arithmetic means are

$$
\bar{\omega}=\frac{1}{n-1}\sum_i\omega_i,
\qquad
\bar{a}=\frac{1}{n-1}\sum_i a_i.
$$

The smoothness score is

$$
S_{\mathrm{smoothness}}
=\frac{1}{2}\operatorname{clip}\left(1-\frac{\bar{\omega}}{5}\right)
+\frac{1}{2}\operatorname{clip}\left(1-\bar{a}\right).
$$

The reference rates are $5^\circ/\mathrm{s}$ and $1\,\mathrm{m/s^2}$. This is a
linear absolute-rate penalty, not a quadratic control-effort function.

## 7. CPA and TCPA Reconstruction

At each observation, relative position and velocity are

$$
\mathbf r=\mathbf p_c-\mathbf p_o,
\qquad
\mathbf v=\mathbf v_c-\mathbf v_o.
$$

Assuming constant velocity, time and distance at the closest point of approach
are

$$
TCPA=-\frac{\mathbf r\cdot\mathbf v}{\|\mathbf v\|^2},
$$

$$
CPA=\|\mathbf r+\mathbf v\,TCPA\|.
$$

The reconstructed risk horizon is

$$
H=\max\left(60,\frac{2d_{\mathrm{safe}}}{\max(\|\mathbf v\|,0.1)}\right).
$$

An encounter is classified as acute when $CPA<500\,\mathrm{m}$ and
$0\leq TCPA\leq H$. It is classified as early when the CPA condition holds but
$TCPA>H$. A negative TCPA indicates that the closest point has already passed.

## 8. Failure Overrides and Verdicts

The final score is

$$
S_{\mathrm{total}}=
\begin{cases}
0,
& d_{\min}<15\,\mathrm{m},\\[4pt]
\min\left(0.20,
0.20S_{\mathrm{compliance}}
+0.10S_{\mathrm{manoeuvre}}
+0.15S_{\mathrm{smoothness}}\right),
& \text{goal not reached},\\[4pt]
S,
& \text{otherwise}.
\end{cases}
$$

The verdict is `FAIL -- collision occurred` following a collision and
`FAIL -- did not reach the goal` following non-arrival. A successful run is
reported as `PASS_WITH_CPA_VIOLATION` when $15\leq d_{\min}<500\,\mathrm{m}$ and
as `PASS` when $d_{\min}\geq500\,\mathrm{m}$.

## 9. Campaign Aggregation

All 22 Imazu scenarios are weighted equally. The reported campaign score is the
arithmetic mean

$$
\bar S=\frac{1}{22}\sum_{j=1}^{22}S_j.
$$

The reported mean component values are calculated in the same way across the 22
cases.

## Retrospective Reconstruction Caveat

The canonical evaluator accepts complete trajectories, normalized chronological
decision events, and independently audited findings. Current campaign runs store
bridge observations in `audit.jsonl`; therefore, the automatic campaign scorer
reconstructs actions and physical findings from observed vessel motion. Contact
positions are interpolated only over periods covered by their recorded tracks,
with no extrapolation. Consequently, campaign scores should be described as
**retrospective estimates using reconstructed decisions and a deterministic
physical audit**, rather than direct scores of the model's command stream.

## Thesis-Ready Description

> Each navigation run was evaluated using a deterministic six-component score
> bounded between zero and one. The composite assigned weights of 0.35 to safety,
> 0.20 to physical COLREG compliance, 0.10 each to temporal efficiency, spatial
> efficiency, and manoeuvre decisiveness, and 0.15 to trajectory smoothness.
> Safety was proportional to the minimum realized vessel separation relative to
> a 500 m safe-distance threshold, while separation below 15 m constituted a
> collision and forced the total score to zero. Temporal and spatial efficiency
> compared realized travel time and path length with a straight-line voyage at
> nominal speed. Manoeuvre decisiveness penalized reversals between opposing turn
> or speed-change directions, normalized by the number of observation intervals
> and contacts. Smoothness was calculated from the mean absolute heading and
> speed rates. Physical COLREG violations produced fixed deductions from the
> compliance component. Runs that did not reach the destination were capped at
> 0.20. The final architecture-level result was the arithmetic mean of the scores
> obtained over all 22 Imazu scenarios.

## Implementation References

- `tools/evaluate_run.py` contains the canonical component equations, weights,
  penalties, and score gates.
- `tools/analyze_baseline_logs.py` reconstructs trajectories, arrival, path
  length, and realized separation from campaign logs.
- `tools/score_baseline_logs.py` reconstructs actions and COLREG findings and
  produces the per-case and campaign score files.

