# OOW action-consistency benchmark

This benchmark compares the same Qwen3-8B OOW with reasoning disabled and
enabled. It freezes exact recorded base-narrative messages and repeats inference
without advancing the simulator. The benchmark measures reproducibility, not
correctness or closed-loop safety.

The frozen sample is selected with a recorded seed and stratified across the
reference actions so routine `maintain_course` rows do not dominate. Each output
retains the source run, case, cycle and prompt hashes.

## Primary experiment: exact runtime decoding

```bash
./run_oow_action_consistency_benchmark.sh \
  --repetitions 100 \
  --temperature 0 \
  --output logs/benchmarks/oow_consistency_greedy_100
```

This is the defensible primary comparison because deployed OOW inference uses
temperature zero. It may show high consistency; that result must be reported
even if it does not support an instability hypothesis.

## Sensitivity experiment: stochastic decoding

```bash
./run_oow_action_consistency_benchmark.sh \
  --repetitions 100 \
  --temperature 0.7 \
  --output logs/benchmarks/oow_consistency_temp07_100
```

The stochastic condition measures sensitivity of the learned policy around
the same fixed states. It must be described as a stress test, not as the exact
deployed configuration.

Interrupted runs are append-only and resumable:

```bash
./run_oow_action_consistency_benchmark.sh \
  --repetitions 100 --temperature 0 \
  --output logs/benchmarks/oow_consistency_greedy_100 --resume
```

The outputs are `predictions.jsonl`, `summary.json`, `per_state.csv`, and
`REPORT.md`. Report modal-action agreement, full-control agreement, normalized
entropy, pairwise disagreement, invalid-output rate, port/starboard counts,
latency, and reasoning truncation. Do not interpret agreement as correctness.
