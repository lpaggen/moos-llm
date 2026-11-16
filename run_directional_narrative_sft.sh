#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export EXACT_SFT_DATASET=${DIRECTIONAL_SFT_DATASET:-$ROOT_DIR/sft/output/privileged_teacher_narrative_directional_v7}
export EXACT_SFT_MODEL_OUTPUT=${DIRECTIONAL_SFT_MODEL_OUTPUT:-$ROOT_DIR/models/qwen3_8b_oow_privileged_luna_narrative_directional_v7}
export EXACT_SFT_HELDOUT_OUTPUT=${DIRECTIONAL_SFT_HELDOUT_OUTPUT:-$ROOT_DIR/sft/output/privileged_teacher_narrative_directional_v7_heldout}
export EXACT_SFT_SMOKE_OUTPUT=${DIRECTIONAL_SFT_SMOKE_OUTPUT:-$ROOT_DIR/logs/evaluations/imazu/privileged_luna_narrative_directional_v7_smoke}
export EXACT_SFT_PREFLIGHT_MODE=narrative-directional
export EXACT_SFT_EVAL_ARCHITECTURE=finetuned-directional-narrative
export EXACT_SFT_MINIMUM_SOURCE_CLEARANCE_M=500
export EXACT_SFT_SEED=20261109
export EXACT_SFT_TRAIN_FILE=train_natural.jsonl
export EXACT_SFT_SAMPLING_MAINTAIN_FRACTION=0.50

"$ROOT_DIR/run_exact_runtime_sft_training.sh"
