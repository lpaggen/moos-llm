#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPETITIONS=${CONSISTENCY_REPETITIONS:-100}
TEMPERATURE=${CONSISTENCY_TEMPERATURE:-0}
OUTPUT=${CONSISTENCY_OUTPUT:-$ROOT_DIR/logs/benchmarks/oow_action_consistency_runtime_greedy}
FIXTURES=${CONSISTENCY_FIXTURES:-$ROOT_DIR/benchmarks/oow_action_consistency/fixtures.jsonl}
EXTERNAL=0
RESUME=0

usage() {
    cat <<'EOF'
Usage: ./run_oow_action_consistency_benchmark.sh [options]

Options:
  --repetitions N    Repeats per fixed state and mode (default: 100)
  --temperature X    Decoding temperature (default: 0, exact runtime condition)
  --output DIR       Benchmark output directory
  --external-vllm    Use an already-running configured vLLM server
  --resume           Resume an interrupted output directory
EOF
}

while (($#)); do
    case "$1" in
        --repetitions) REPETITIONS=$2; shift 2 ;;
        --temperature) TEMPERATURE=$2; shift 2 ;;
        --output) OUTPUT=$2; shift 2 ;;
        --external-vllm) EXTERNAL=1; shift ;;
        --resume) RESUME=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "$REPETITIONS" =~ ^[1-9][0-9]*$ ]] || { echo "--repetitions must be positive" >&2; exit 2; }
mkdir -p "$(dirname -- "$FIXTURES")" "$OUTPUT"
if [[ ! -s "$FIXTURES" ]]; then
    "$ROOT_DIR/.venv/bin/python" -m tools.benchmark_oow_action_consistency select --output "$FIXTURES"
fi

VLLM_PID=""
cleanup() {
    trap - EXIT INT TERM
    if [[ "$VLLM_PID" =~ ^[1-9][0-9]*$ ]]; then
        kill -TERM -- "-$VLLM_PID" 2>/dev/null || true
        wait "$VLLM_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

MODEL=$($ROOT_DIR/.venv/bin/python -m tools.config_value models default_local)
if ((EXTERNAL == 0)); then
    setsid "$ROOT_DIR/run_simulator.sh" --reasoning --vllm-only >"$OUTPUT/vllm_supervisor.log" 2>&1 &
    VLLM_PID=$!
    "$ROOT_DIR/.venv/bin/python" -m tools.wait_vllm_ready --model "$MODEL" --timeout 600 --process-pid "$VLLM_PID"
else
    "$ROOT_DIR/.venv/bin/python" -m tools.wait_vllm_ready --model "$MODEL" --timeout 10
fi

args=(run --fixtures "$FIXTURES" --output "$OUTPUT" --repetitions "$REPETITIONS" --temperature "$TEMPERATURE" --model "$MODEL")
((RESUME == 1)) && args+=(--resume)
"$ROOT_DIR/.venv/bin/python" -m tools.benchmark_oow_action_consistency "${args[@]}"

echo "Benchmark report: $OUTPUT/REPORT.md"
