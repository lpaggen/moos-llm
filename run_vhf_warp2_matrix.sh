#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export MOOS_LLM_VHF_CONTEXT_CYCLES=3
CHECK_ONLY=no

usage() {
    cat <<'EOF'
Usage: ./run_vhf_warp2_matrix.sh [--check]

Run the four retained VHF/no-VHF × base/Captain conditions at warp 2.

  --check     Validate all four configurations without starting vLLM or MOOS
  --help, -h  Show this help
EOF
}

while (($#)); do
    case "$1" in
        --check) CHECK_ONLY=yes; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "run_vhf_warp2_matrix.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

run_condition() {
    local architecture=$1
    local condition=$2
    local output=$3
    local check_args=()
    [[ "$CHECK_ONLY" == yes ]] && check_args+=(--check)
    "$ROOT_DIR/run_vhf_evaluation.sh" \
        --architecture "$architecture" \
        --condition "$condition" \
        --cases 1-10 \
        --time-warp 2 \
        --max-cycles 240 \
        --timeout 7200 \
        --output "$output" \
        --resume \
        --nogui \
        "${check_args[@]}"
}

matrix_status=0
run_condition base-narrative vhf \
    logs/evaluations/vhf/base_narrative_vhf_radio_lifecycle_warp2 || matrix_status=1
run_condition base-narrative no-vhf \
    logs/evaluations/vhf/base_narrative_no_vhf_radio_lifecycle_warp2 || matrix_status=1
run_condition captain-critic vhf \
    logs/evaluations/vhf/narrative_oow_captain_critic_vhf_radio_lifecycle_warp2 || matrix_status=1
run_condition captain-critic no-vhf \
    logs/evaluations/vhf/narrative_oow_captain_critic_no_vhf_radio_lifecycle_warp2 || matrix_status=1

if ((matrix_status == 0)); then
    echo "VHF warp-2 matrix complete."
else
    echo "VHF warp-2 matrix attempted every condition; one or more cases remain incomplete." >&2
fi
exit "$matrix_status"
