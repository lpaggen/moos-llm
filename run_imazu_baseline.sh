#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CASES="1-22"
TIMEOUT_SECONDS=300
TIME_WARP=50
OUTPUT_DIR="$ROOT_DIR/logs/evaluations/imazu/moos_ivp_colregs_baseline"
RESUME="no"
GUI="no"
AUTO_DEPLOY="no"

usage() {
    cat <<'EOF'
Usage: ./run_imazu_baseline.sh [OPTIONS]

Record clean MOOS-IvP BHV_AvdColregsV22 telemetry for the IMAZU suite.

  --cases SPEC    Cases such as 1-22 or 1,3,12 (default: 1-22)
  --timeout N     Wall seconds per case (default: 300)
  --time-warp N   MOOS time warp (default: 50)
  --output DIR    Output directory
  --resume        Skip cases with a completed status file
  --gui           Open pMarineViewer for each scenario
  --deploy        Automatically deploy all vehicles after launch
EOF
}

while (($#)); do
    case "$1" in
        --cases) CASES=$2; shift 2 ;;
        --timeout) TIMEOUT_SECONDS=$2; shift 2 ;;
        --time-warp) TIME_WARP=$2; shift 2 ;;
        --output) OUTPUT_DIR=$2; shift 2 ;;
        --resume) RESUME="yes"; shift ;;
        --gui) GUI="yes"; shift ;;
        --deploy) AUTO_DEPLOY="yes"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "--timeout must be positive" >&2; exit 2; }
[[ "$TIME_WARP" =~ ^[1-9][0-9]*$ ]] || { echo "--time-warp must be positive" >&2; exit 2; }
[[ "$OUTPUT_DIR" == /* ]] || OUTPUT_DIR="$ROOT_DIR/$OUTPUT_DIR"

expand_cases() {
    local part first last item
    IFS=',' read -ra parts <<< "$CASES"
    for part in "${parts[@]}"; do
        if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            first=${BASH_REMATCH[1]}; last=${BASH_REMATCH[2]}
            ((first <= last)) || { echo "invalid range: $part" >&2; return 2; }
            for ((item=first; item<=last; item++)); do printf '%d\n' "$item"; done
        elif [[ "$part" =~ ^[0-9]+$ ]]; then
            printf '%d\n' "$part"
        else
            echo "invalid case specification: $part" >&2; return 2
        fi
    done
}

mapfile -t CASE_LIST < <(expand_cases)
mkdir -p "$OUTPUT_DIR"
printf '{"suite":"IMAZU","controller":"MOOS-IvP BHV_AvdColregsV22","cases":"%s","time_warp":%d,"telemetry":"per-contact COLREG mode/submode/relevance/priority/index"}\n' \
    "$CASES" "$TIME_WARP" > "$OUTPUT_DIR/manifest.json"

for case_number in "${CASE_LIST[@]}"; do
    ((case_number >= 1 && case_number <= 22)) || { echo "case outside 1..22: $case_number" >&2; exit 2; }
    printf -v case_id '%02d' "$case_number"
    case_dir="$OUTPUT_DIR/case_$case_id"
    audit="$case_dir/telemetry.jsonl"
    status_file="$case_dir/status.json"
    if [[ "$RESUME" == "yes" && -s "$status_file" ]] && \
       grep -q '"mission_complete":true' "$status_file"; then
        echo "[IMAZU $case_id] complete; skipping"
        continue
    fi

    mkdir -p "$case_dir/runtime"
    cp "$ROOT_DIR/mission/scenarios/imazu/$case_id/$case_id.ini" "$case_dir/scenario.ini"
    echo "[IMAZU $case_id] recording MOOS-IvP COLREG baseline"
    simulator_args=(
        --controller moos-ivp --scenario "imazu/$case_id" --tiff none
        --time-warp "$TIME_WARP" --audit-log "$audit"
    )
    [[ "$GUI" == "no" ]] && simulator_args+=(--nogui)
    [[ "$AUTO_DEPLOY" == "yes" ]] && simulator_args+=(--deploy)
    set +e
    timeout --signal=TERM --kill-after=20 "$TIMEOUT_SECONDS" \
        env MOOS_LLM_EXIT_ON_COMPLETE=1 "$ROOT_DIR/run_simulator.sh" \
        "${simulator_args[@]}"
    exit_status=$?
    set -e

    mission_complete=false
    grep -Eq '"event"[[:space:]]*:[[:space:]]*"mission_complete"' "$audit" 2>/dev/null && mission_complete=true
    timed_out=false
    ((exit_status == 124)) && timed_out=true
    printf '{"case":%d,"exit_status":%d,"mission_complete":%s,"timed_out":%s}\n' \
        "$case_number" "$exit_status" "$mission_complete" "$timed_out" > "$status_file"

    for runtime_log in agents bridge mission deploy; do
        source_log="$ROOT_DIR/logs/$runtime_log.log"
        [[ -f "$source_log" ]] && cp "$source_log" "$case_dir/runtime/$runtime_log.log"
    done
    [[ -f "$ROOT_DIR/mission/targ_LLM_SHIP.bhv" ]] && cp "$ROOT_DIR/mission/targ_LLM_SHIP.bhv" "$case_dir/rendered_ownship.bhv"
    [[ -f "$ROOT_DIR/mission/targ_LLM_SHIP.moos" ]] && cp "$ROOT_DIR/mission/targ_LLM_SHIP.moos" "$case_dir/rendered_ownship.moos"
    cat "$status_file"
done

echo "Baseline recording finished: $OUTPUT_DIR"
