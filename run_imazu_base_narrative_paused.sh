#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${ROOT_DIR}/logs/evaluations/imazu/base_narrative_lockstep_30s_warp20"

# Frozen paused-inference baseline:
# - narrative OOW without reasoning, RAG, Eriksen labels, or supplied COLREGs
# - MOOS runs at warp 20 between decisions
# - simulation time is frozen while the model is answering
# - decisions are scheduled every 30 simulated seconds while contacts are in range
# - completed cases are retained and skipped when this command is resumed
exec "${ROOT_DIR}/run_imazu_evaluation.sh" \
  --architecture base \
  --cases 1-22 \
  --timeout 3600 \
  --max-cycles 240 \
  --time-warp 20 \
  --decision-interval 30 \
  --freeze-during-decision \
  --encounter-only \
  --encounter-range 6000 \
  --output "${OUTPUT_DIR}" \
  --resume \
  --nogui \
  "$@"
