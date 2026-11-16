#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DATASET=${EXACT_SFT_DATASET:-$ROOT_DIR/sft/output/privileged_teacher_narrative_directional_v7}
OUTPUT=${EXACT_SFT_MODEL_OUTPUT:-$ROOT_DIR/models/qwen3_8b_oow_privileged_luna_narrative_directional_v7}
HELDOUT_OUTPUT=${EXACT_SFT_HELDOUT_OUTPUT:-$ROOT_DIR/sft/output/privileged_teacher_narrative_directional_v7_heldout}
SMOKE_OUTPUT=${EXACT_SFT_SMOKE_OUTPUT:-$ROOT_DIR/logs/evaluations/imazu/privileged_luna_narrative_directional_v7_smoke}

if [[ "${EXACT_SFT_PREFLIGHT_MODE:-narrative-directional}" == "narrative-only" ]]; then
    "$ROOT_DIR/.venv/bin/python" -m sft.preflight_narrative_ablation_sft \
        "$DATASET" \
        --minimum-source-clearance-m "${EXACT_SFT_MINIMUM_SOURCE_CLEARANCE_M:-500}"
elif [[ "${EXACT_SFT_PREFLIGHT_MODE:-narrative-directional}" == "narrative-directional" ]]; then
    "$ROOT_DIR/.venv/bin/python" -m sft.preflight_directional_sft \
        "$DATASET" \
        --minimum-source-clearance-m "${EXACT_SFT_MINIMUM_SOURCE_CLEARANCE_M:-500}"
else
    preflight_args=()
    if [[ "${EXACT_SFT_SAFE_MISSION_PROFILE:-0}" == "1" ]]; then
        preflight_args+=(--minimum-source-clearance-m "${EXACT_SFT_MINIMUM_SOURCE_CLEARANCE_M:-500}")
        preflight_args+=(--safe-mission-profile)
    fi
    "$ROOT_DIR/.venv/bin/python" -m sft.preflight_exact_runtime_sft \
        "$DATASET" "${preflight_args[@]}"
fi
[[ ! -e "$OUTPUT" ]] || {
    echo "run_exact_runtime_sft_training.sh: model output already exists: $OUTPUT" >&2
    exit 2
}
[[ ! -e "$HELDOUT_OUTPUT" ]] || {
    echo "run_exact_runtime_sft_training.sh: held-out output already exists: $HELDOUT_OUTPUT" >&2
    exit 2
}
[[ ! -e "$SMOKE_OUTPUT" ]] || {
    echo "run_exact_runtime_sft_training.sh: smoke output already exists: $SMOKE_OUTPUT" >&2
    exit 2
}

sampling_args=()
if [[ -n "${EXACT_SFT_SAMPLING_MAINTAIN_FRACTION:-}" ]]; then
    sampling_args+=(--sampling-maintain-fraction "$EXACT_SFT_SAMPLING_MAINTAIN_FRACTION")
fi

"$ROOT_DIR/.venv/bin/python" -m sft.train_qwen3_oow \
    --dataset-dir "$DATASET" \
    --train-file "${EXACT_SFT_TRAIN_FILE:-train_balanced.jsonl}" \
    --model Qwen/Qwen3-8B \
    --output-dir "$OUTPUT" \
    --mode qlora \
    --max-length 4096 \
    --epochs 1 \
    --learning-rate 2e-5 \
    --batch-size 1 \
    --gradient-accumulation 16 \
    --lora-rank 16 \
    --lora-alpha 32 \
    --seed "${EXACT_SFT_SEED:-20261021}" \
    "${sampling_args[@]}"

"$ROOT_DIR/.venv/bin/python" -m sft.evaluate_qwen3_oow \
    --adapter "$OUTPUT" \
    --test-file "$DATASET/test_natural.jsonl" \
    --output-dir "$HELDOUT_OUTPUT" \
    --batch-size 4 \
    --max-new-tokens 384
"$ROOT_DIR/.venv/bin/python" -m sft.check_exact_runtime_metrics \
    "$HELDOUT_OUTPUT/metrics.json"

"$ROOT_DIR/run_imazu_evaluation.sh" \
    --architecture "${EXACT_SFT_EVAL_ARCHITECTURE:-finetuned-directional-narrative}" \
    --adapter "$OUTPUT" \
    --cases 13,16,22 \
    --time-warp 5 \
    --max-cycles 240 \
    --timeout 4800 \
    --output "$SMOKE_OUTPUT" \
    --nogui
"$ROOT_DIR/.venv/bin/python" -m sft.check_imazu_smoke "$SMOKE_OUTPUT"

echo "Adapter passed held-out generation and Imazu smoke gates: $OUTPUT"
