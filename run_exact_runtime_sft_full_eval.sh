#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODEL=${EXACT_SFT_MODEL_OUTPUT:-$ROOT_DIR/models/qwen3_8b_oow_privileged_luna_narrative_directional_v7}
HELDOUT=${EXACT_SFT_HELDOUT_OUTPUT:-$ROOT_DIR/sft/output/privileged_teacher_narrative_directional_v7_heldout}
SMOKE=${EXACT_SFT_SMOKE_OUTPUT:-$ROOT_DIR/logs/evaluations/imazu/privileged_luna_narrative_directional_v7_smoke}
OUTPUT=${EXACT_SFT_FULL_EVAL_OUTPUT:-$ROOT_DIR/logs/evaluations/imazu/privileged_luna_narrative_directional_v7_full_warp5}

"$ROOT_DIR/.venv/bin/python" -m sft.check_exact_runtime_metrics "$HELDOUT/metrics.json"
"$ROOT_DIR/.venv/bin/python" -m sft.check_imazu_smoke "$SMOKE"
[[ ! -e "$OUTPUT" ]] || {
    echo "run_exact_runtime_sft_full_eval.sh: output already exists: $OUTPUT" >&2
    exit 2
}

"$ROOT_DIR/run_imazu_evaluation.sh" \
    --architecture "${EXACT_SFT_EVAL_ARCHITECTURE:-finetuned-directional-narrative}" \
    --adapter "$MODEL" \
    --cases 1-22 \
    --time-warp 5 \
    --max-cycles 240 \
    --timeout 4800 \
    --output "$OUTPUT" \
    --nogui

echo "Exact-runtime full Imazu evaluation finished: $OUTPUT"
