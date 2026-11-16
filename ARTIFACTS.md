# Thesis repository artifact boundary

This repository is intentionally limited to material required to inspect,
reproduce, or cite the thesis experiments.

## Included

- Executable Python, C++, and shell source, tests, schemas, prompts, and
  experiment configuration.
- Imazu, VHF, random-generation, and teacher scenario definitions under
  `mission/scenarios/`.
- Frozen scoring outputs under `reports/imazu_frozen_v1/` and the derived
  thesis tables, figures, latency summaries, and methodological reports under
  `reports/`.
- The annotated MAIB retrieval benchmark and derived extraction/retrieval
  outputs under `reports/maib_*`.
- The final training corpus used for the retained directional-v7 SFT model:
  `sft/output/privileged_teacher_narrative_directional_v7/`. Its manifest
  records row counts, mission-disjoint splits, filtering policy, and SHA-256
  hashes.
- Thesis prose/source files, bibliography, and the frozen scoring contract.

## Deliberately excluded from Git

- `logs/`: raw runtime and campaign logs. Thesis values are frozen in
  `reports/`; the scoring JSON retains per-case and per-cycle evidence.
- `models/`: base-model caches and trained adapters. Model identity and
  configuration are documented in the reports and manifests.
- `.venv/`, `build/`, `pLLMBridge`, CMake products, caches, and editor state.
- `accidents/`: source PDFs and intermediate extraction workspaces. The
  derived records actually evaluated are included in `reports/maib_*`.
- Superseded SFT datasets, experimental checkpoints, duplicate legacy code,
  and abandoned run directories.

## Primary result entry points

- `reports/imazu_frozen_v1/README.md`: frozen Imazu composite and axis scores.
- `reports/IMAZU_MODEL_RESULTS_THESIS_NOTES.md`: thesis-facing interpretation.
- `reports/IMAZU_AXIS_SCORES_BY_SCENARIO.csv`: fine-grained scenario results.
- `reports/SFT_DATA_DISTRIBUTION.md`: retained SFT corpus statistics.
- `reports/maib_state_retrieval_benchmark/`: annotated MAIB retrieval result.
- `tools/IMAZU_SCORING_CONTRACT.md`: exact scoring contract.

Large local artifacts remain outside Git and are not required to inspect the
reported numerical results. A fresh clone builds the C++ bridge out of tree in
`build/` when `run_simulator.sh` is invoked.
