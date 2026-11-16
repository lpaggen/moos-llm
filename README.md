# Multi-Agentic LLM Ship Captain

MOOS-IvP research environment for evaluating language-model Officers of the
Watch, Captain critics, COLREG retrieval, VHF interpretation, and supervised
fine-tuning on the Imazu collision-avoidance scenarios.

## Active entry points

| Task | Command |
|---|---|
| Run one simulator mission | `./run_simulator.sh --help` |
| Run an Imazu model condition | `./run_imazu_evaluation.sh --help` |
| Run the paused 30-second narrative baseline | `./run_imazu_base_narrative_paused.sh` |
| Run the MOOS-IvP baseline | `./run_imazu_baseline.sh` |
| Run the VHF benchmark | `./run_vhf_evaluation.sh --help` |
| Run the retained warp-2 VHF matrix | `./run_vhf_warp2_matrix.sh` |
| Generate random teacher missions | `./run_random_campaign.sh --help` |
| Train the retained directional-v7 SFT profile | `./run_directional_narrative_sft.sh` |
| Run the OOW consistency benchmark | `bash run_oow_action_consistency_benchmark.sh --help` |
| Stop a campaign safely | `./stop_campaign.sh` |

The model/runtime configuration is [project.ini](project.ini). The primary
agent implementation is under `agents/`; MOOS bridge code is under `bridge/`;
scenario definitions are under `mission/scenarios/`; and scoring/analysis
utilities are under `tools/`.

## Current retained artifacts

- Imazu thesis results: `reports/imazu_frozen_v1/` and
  `reports/IMAZU_MODEL_RESULTS_THESIS_NOTES.md`.
- Current SFT dataset report: `reports/SFT_DATA_DISTRIBUTION.md`.
- Current SFT dataset: `sft/output/privileged_teacher_narrative_directional_v7/`.
- Frozen evaluation mathematics: `EVALUATION_MATHEMATICS.md`.

See [sft/README.md](sft/README.md) for the retained training lineage and
[mission/scenarios/imazu/README.md](mission/scenarios/imazu/README.md) for the
22-scenario benchmark.

## Repository scope

The Git repository contains source code, scenario/configuration inputs, thesis
source, the final v7 SFT dataset, and the compact result artifacts used in the
thesis. Raw simulator logs, model weights/adapters, virtual environments,
build products, source accident-report PDFs, and superseded experiments are
deliberately excluded because they are large, machine-specific, redistributable
from their original sources, or not used by the reported results. See
[`ARTIFACTS.md`](ARTIFACTS.md) for the exact inclusion boundary and provenance
map.
