# Imazu scenario implementation

These 22 scenarios implement the Imazu encounter set as reproduced in
Appendix 1, Table 4 of Sawada, Sato, and Majima (2021), *Automatic ship
collision avoidance using deep reinforcement learning with LSTM in continuous
action spaces*, DOI `10.1007/s00773-020-00755-0`.

## Coordinate and vessel convention

- `imazu_constellations.csv` records the paper's fixed-coordinate target
  positions, rotated into the simulator's nautical frame: paper X becomes
  north, paper Y becomes east, and heading 0 degrees is north.
- The source-table coordinates use their full scale (`SCENARIO_SCALE = 1.0`).
  Own ship starts 6 NM south of the encounter origin, heads north, and has a
  waypoint 6 NM north of the origin, giving a 12 NM route. All target positions
  and waypoints use the same scale.
- Normal vessel speed is 12.0 kn (6.173 m/s).
- In cases 3, 7, 15, 17, 20, and 22, the target directly ahead travels at 8.4 kn
  (4.321 m/s).
- Case 16 TS1 also travels at 8.4 kn: its Table 4 start is
  `(X, Y) = (-2.970, -2.970) NM`, 4.2 NM from the collision origin.
  This speed follows from the paper's 30-minute encounter timing; it is a
  crossing target, rather than an overtaking target.
- Target ships hold course and speed. Only own ship runs the collision-
  avoidance system under evaluation.
- Scenario coordinates are physical metres (`1 NM = 1852 m`). They are not
  visually compressed, so CPA, TCPA, range thresholds, and reported speeds
  remain dimensionally meaningful.

The generated positions place the ships on collision courses with an intended
initial TCPA of approximately 30 minutes. Initial ranges span approximately
1.05–12.0 NM; even the closest start is about 1937 m away, outside the published
0.5 NM general safe-passing distance. A complete own-ship route is 12 NM.
Small deviations from exactly 1800 seconds are
expected from published rounding and simulator dynamics.

The source table was checked directly against Table 4 (PDF page 14, printed
page 522) and Figure 18 of the
[Sawada paper](https://link.springer.com/content/pdf/10.1007/s00773-020-00755-0.pdf).
Case 12 contains two targets: `(-4.243, 4.243, -45°)` and
`(-5.909, 1.042, -10°)` in the paper's `(X, Y, heading)` convention.
An earlier transcription incorrectly added a head-on target to case 12 and
placed case 16 TS1 at `(-4.243, -4.243) NM`; both were corrected. The table has
50 target entries across the 22 cases. The verification details are in
`coordinate_verification.json` in this directory.

When launched through `run_simulator.sh`, these cases automatically use the
Sawada evaluation domain: 0.5 NM general passing clearance and 1.0 NM forward
or bow-crossing clearance. The larger domain is scenario-specific; non-Imazu
missions retain the default project safety envelope.

Imazu runs also use a scenario-specific 10x MOOS time warp. Vessel speeds,
CPA, and TCPA remain expressed in simulation time, but a nominal 60-minute
route takes approximately six minutes of wall-clock simulation time plus
model-inference overhead. The OOW idle polling interval is divided by the same
factor so time acceleration does not add a tenfold polling delay.

## Rebuilding and checking

The INI files are generated from the CSV so the 22 copies cannot silently
drift:

```bash
python tools/generate_imazu_scenarios.py
python tools/generate_imazu_scenarios.py --check
```

## Architecture evaluation

To record the MOOS-IvP baseline over all 22 cases at the restored 12 NM scale:

```bash
./run_imazu_baseline.sh --cases 1-22 --gui --deploy --time-warp 50 --timeout 600 \
  --output logs/evaluations/imazu/moos_ivp_colregs_baseline_12nm
```

The timeout is wall-clock seconds per case. At 50x time warp, a nominal
60-minute route takes about 72 seconds before detours and startup overhead.
The output directory separates these recordings from the earlier 6 NM suite.
`--gui` opens pMarineViewer for each case; `--deploy` deploys all scenario
vehicles automatically. Omit `--gui` to run without the viewer.

Run one architecture at a time over all 22 cases:

```bash
./run_imazu_evaluation.sh --architecture base
./run_imazu_evaluation.sh --architecture base-reasoning
./run_imazu_evaluation.sh --architecture colregs-rag
./run_imazu_evaluation.sh --architecture colregs-rag-reasoning
```

The `base` profiles use untuned Qwen and the full OOW prompt, without
retrieval. The reasoning variant replaces `/no_think` with `/think`. The
COLREG-RAG profiles retrieve against the authoritative corpus and present the
matched operational summaries to the model.

Use `--cases 1,3,12` for a subset, `--output DIR` for a stable destination,
and `--resume` to skip cases with an existing result. Each output directory
contains a manifest, per-case audit and runtime logs, per-case `result.json`,
and combined `results.jsonl`. Summaries include outcome, minimum separation,
actions, architecture flags, validation failures, and rule-citation rate.
