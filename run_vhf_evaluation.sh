#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CASES="1-10"
CONDITION="vhf"
ARCHITECTURE="base-narrative"
TIMEOUT_SECONDS=1800
MAX_CYCLES=160
TIME_WARP=3
VHF_CONTEXT_CYCLES=${MOOS_LLM_VHF_CONTEXT_CYCLES:-3}
OUTPUT_DIR=""
RESUME="no"
CHECK_ONLY="no"
GUI="no"

usage() {
    cat <<'EOF'
Usage: ./run_vhf_evaluation.sh [OPTIONS]

Run the exact-text VHF benchmark with the untuned narrative OOW, optionally
reviewed by the MAIB-RAG critic captain.

  --cases SPEC      Cases such as 1-10 or 1,3,6 (default: 1-10)
  --condition NAME  vhf or no-vhf (default: vhf)
  --architecture A  base-narrative or captain-critic (default: base-narrative)
  --timeout N       Wall-clock seconds per case (default: 1800)
  --max-cycles N    Decision-cycle limit per case (default: 160)
  --time-warp N     MOOS time warp (default: 3)
  --output DIR      Output directory (default: timestamped)
  --resume          Skip terminal cases already scored in --output
  --gui             Open pMarineViewer
  --nogui           Run headless (default)
  --check           Validate every selected scenario without starting services
  --help, -h        Show this help

The runner keeps one vLLM server resident, exits each mission automatically,
scores each case, continues after navigation failures, and records the cycle
limit as a valid non-arrival outcome. It exits non-zero for unresolved timeouts
or pipeline failures. In the `vhf` condition it
requires every expected message in the OOW prompt; in `no-vhf` it verifies that
no VHF event contaminated the matched control.
EOF
}

while (($#)); do
    case "$1" in
        --cases) CASES=$2; shift 2 ;;
        --condition) CONDITION=$2; shift 2 ;;
        --architecture) ARCHITECTURE=$2; shift 2 ;;
        --timeout) TIMEOUT_SECONDS=$2; shift 2 ;;
        --max-cycles) MAX_CYCLES=$2; shift 2 ;;
        --time-warp) TIME_WARP=$2; shift 2 ;;
        --output) OUTPUT_DIR=$2; shift 2 ;;
        --resume) RESUME="yes"; shift ;;
        --gui) GUI="yes"; shift ;;
        --nogui) GUI="no"; shift ;;
        --check) CHECK_ONLY="yes"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "run_vhf_evaluation.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "$CONDITION" == "vhf" || "$CONDITION" == "no-vhf" ]] || {
    echo "--condition requires vhf or no-vhf" >&2
    exit 2
}
[[ "$ARCHITECTURE" == "base-narrative" || "$ARCHITECTURE" == "captain-critic" ]] || {
    echo "--architecture requires base-narrative or captain-critic" >&2
    exit 2
}

for value in "$TIMEOUT_SECONDS" "$MAX_CYCLES" "$TIME_WARP" "$VHF_CONTEXT_CYCLES"; do
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || {
        echo "timeout, max-cycles, time-warp, and VHF context cycles must be positive integers" >&2
        exit 2
    }
done

expand_cases() {
    local part first last item
    IFS=',' read -ra parts <<< "$CASES"
    for part in "${parts[@]}"; do
        if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            first=${BASH_REMATCH[1]}
            last=${BASH_REMATCH[2]}
            ((first <= last)) || { echo "invalid range: $part" >&2; return 2; }
            for ((item=first; item<=last; item++)); do printf '%d\n' "$item"; done
        elif [[ "$part" =~ ^[0-9]+$ ]]; then
            printf '%d\n' "$part"
        else
            echo "invalid case specification: $part" >&2
            return 2
        fi
    done
}

mapfile -t CASE_LIST < <(expand_cases)
((${#CASE_LIST[@]} > 0)) || { echo "no VHF cases selected" >&2; exit 2; }
for case_number in "${CASE_LIST[@]}"; do
    ((case_number >= 1 && case_number <= 10)) || {
        echo "VHF case outside 1..10: $case_number" >&2
        exit 2
    }
done

COMMON_FLAGS=(
    --controller llm
    --oow base
    --narrative
    --no-colregs-rag
    --no-colregs-tree
    --no-eriksen
    --no-reasoning
)
if [[ "$ARCHITECTURE" == "captain-critic" ]]; then
    ARCHITECTURE_SLUG="narrative_oow_captain_critic"
    RAG_FLAG="--rag"
    for required_asset in accidents/index.jsonl accidents/embeddings.npy; do
        [[ -s "$ROOT_DIR/$required_asset" ]] || {
            echo "critic captain requires nonempty $required_asset" >&2
            exit 2
        }
    done
else
    ARCHITECTURE_SLUG="base_narrative"
    RAG_FLAG="--no-rag"
fi
RUN_FLAGS=("${COMMON_FLAGS[@]}" "$RAG_FLAG")
if [[ "$CONDITION" == "vhf" ]]; then
    EVENT_FLAG="--vhf-events"
else
    EVENT_FLAG="--no-vhf-events"
fi

if [[ "$CHECK_ONLY" == "yes" ]]; then
    for case_number in "${CASE_LIST[@]}"; do
        printf -v case_id '%02d' "$case_number"
        check_args=(
            "${RUN_FLAGS[@]}"
            --scenario "vhf/$case_id"
            --tiff none
            --time-warp "$TIME_WARP"
            "$EVENT_FLAG"
            --check
        )
        [[ "$GUI" == "no" ]] && check_args+=(--nogui)
        "$ROOT_DIR/run_simulator.sh" "${check_args[@]}"
    done
    echo "VHF benchmark configuration valid: architecture $ARCHITECTURE, condition $CONDITION, cases $CASES at warp $TIME_WARP"
    exit 0
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    run_id=$(date -u +%Y%m%dT%H%M%SZ)_${ARCHITECTURE_SLUG}_${CONDITION}_warp${TIME_WARP}
    OUTPUT_DIR="$ROOT_DIR/logs/evaluations/vhf/$run_id"
elif [[ "$OUTPUT_DIR" != /* ]]; then
    OUTPUT_DIR="$ROOT_DIR/$OUTPUT_DIR"
fi
mkdir -p "$OUTPUT_DIR"

# A resumed campaign may use bug fixes that did not exist when its first cases
# ran. Never rewrite the original manifest/provenance in that situation. Keep a
# separate timestamped snapshot so mixed-version campaigns remain auditable.
SNAPSHOT_DIR="$OUTPUT_DIR"
if [[ "$RESUME" == "yes" && -s "$OUTPUT_DIR/manifest.json" && -s "$OUTPUT_DIR/provenance.sha256" ]]; then
    resume_id="$(date -u +%Y%m%dT%H%M%SZ)_${BASHPID}"
    SNAPSHOT_DIR="$OUTPUT_DIR/resume_history/$resume_id"
    mkdir -p "$SNAPSHOT_DIR"
    echo "Preserving original VHF provenance; resume snapshot: $SNAPSHOT_DIR"
fi

cp "$ROOT_DIR/project.ini" "$SNAPSHOT_DIR/project.ini"
{
    sha256sum "$SNAPSHOT_DIR/project.ini"
    sha256sum "$ROOT_DIR/run_vhf_evaluation.sh" "$ROOT_DIR/run_simulator.sh"
    sha256sum "$ROOT_DIR/main.py" "$ROOT_DIR/agents/agent_vhf.py" "$ROOT_DIR/agents/agent_oow.py"
    sha256sum "$ROOT_DIR/agents/schemas/vhf.py" "$ROOT_DIR/agents/schemas/captain.py"
    sha256sum "$ROOT_DIR/agents/skills/vhf.md" "$ROOT_DIR/agents/skills/oow_vhf.md" \
        "$ROOT_DIR/agents/skills/oow_decision.md"
    if [[ "$ARCHITECTURE" == "captain-critic" ]]; then
        sha256sum "$ROOT_DIR/agents/agent_captain.py" "$ROOT_DIR/agents/skills/captain.md"
        sha256sum "$ROOT_DIR/rag/maib.py" "$ROOT_DIR/accidents/index.jsonl" "$ROOT_DIR/accidents/embeddings.npy"
    fi
    for case_number in "${CASE_LIST[@]}"; do
        printf -v case_id '%02d' "$case_number"
        sha256sum \
            "$ROOT_DIR/mission/scenarios/vhf/$case_id/$case_id.ini" \
            "$ROOT_DIR/mission/scenarios/vhf/$case_id/$case_id.vhf.json"
    done
} > "$SNAPSHOT_DIR/provenance.sha256"

exact_text=false
[[ "$CONDITION" == "vhf" ]] && exact_text=true
printf '{"suite":"VHF","architecture":"%s","condition":"%s","cases":"%s","timeout_seconds":%d,"max_cycles":%d,"time_warp":%d,"vhf_context_cycles":%d,"vhf_persistent_directives":true,"exact_text":%s,"created_utc":"%s"}\n' \
    "$ARCHITECTURE_SLUG" "$CONDITION" "$CASES" "$TIMEOUT_SECONDS" "$MAX_CYCLES" "$TIME_WARP" "$VHF_CONTEXT_CYCLES" "$exact_text" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$SNAPSHOT_DIR/manifest.json"

VLLM_SUPERVISOR_PID=""
cleanup_vllm() {
    trap - EXIT INT TERM
    if [[ "$VLLM_SUPERVISOR_PID" =~ ^[1-9][0-9]*$ ]]; then
        kill -TERM -- "-$VLLM_SUPERVISOR_PID" 2>/dev/null || true
        wait "$VLLM_SUPERVISOR_PID" 2>/dev/null || true
    fi
}
trap cleanup_vllm EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

setsid "$ROOT_DIR/run_simulator.sh" "${RUN_FLAGS[@]}" --vllm-only \
    > "$OUTPUT_DIR/vllm_supervisor.log" 2>&1 &
VLLM_SUPERVISOR_PID=$!

EVALUATION_MODEL=$("$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/tools/config_value.py" models default_local)
VLLM_STARTUP_TIMEOUT=$("$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/tools/config_value.py" runtime vllm_startup_timeout_seconds 2>/dev/null || echo 600)
"$ROOT_DIR/.venv/bin/python" -m tools.wait_vllm_ready \
    --model "$EVALUATION_MODEL" --timeout "$VLLM_STARTUP_TIMEOUT" \
    --process-pid "$VLLM_SUPERVISOR_PID"

incomplete_cases=0
for case_number in "${CASE_LIST[@]}"; do
    printf -v case_id '%02d' "$case_number"
    case_dir="$OUTPUT_DIR/case_$case_id"
    audit="$case_dir/audit.jsonl"
    status_file="$case_dir/status.json"
    score_file="$case_dir/vhf_score.json"
    mkdir -p "$case_dir/runtime"

    if [[ "$RESUME" == "yes" && -s "$status_file" && -s "$score_file" ]] && \
       jq -e '.run_finished == true and .pipeline_complete == true' "$status_file" >/dev/null; then
        echo "[VHF-$case_id] finished and scored; skipping"
        continue
    fi

    "$ROOT_DIR/.venv/bin/python" -m tools.wait_vllm_ready \
        --model "$EVALUATION_MODEL" --timeout 5 \
        --process-pid "$VLLM_SUPERVISOR_PID"

    : > "$audit"
    cp "$ROOT_DIR/mission/scenarios/vhf/$case_id/$case_id.ini" "$case_dir/scenario.ini"
    cp "$ROOT_DIR/mission/scenarios/vhf/$case_id/$case_id.vhf.json" "$case_dir/vhf_manifest.json"
    echo "[VHF-$case_id] running $ARCHITECTURE condition $CONDITION at warp $TIME_WARP"

    simulator_args=(
        "${RUN_FLAGS[@]}"
        --external-vllm
        --scenario "vhf/$case_id"
        --tiff none
        --time-warp "$TIME_WARP"
        "$EVENT_FLAG"
        --deploy
        --audit-log "$audit"
    )
    [[ "$GUI" == "no" ]] && simulator_args+=(--nogui)

    set +e
    timeout --signal=TERM --kill-after=20 "$TIMEOUT_SECONDS" \
        env MOOS_LLM_EXIT_ON_COMPLETE=1 MOOS_LLM_MAX_CYCLES="$MAX_CYCLES" \
        MOOS_LLM_VLLM_SUPERVISOR_PID="$VLLM_SUPERVISOR_PID" \
        "$ROOT_DIR/run_simulator.sh" "${simulator_args[@]}" \
        2>&1 | tee "$case_dir/console.log"
    exit_status=${PIPESTATUS[0]}
    set -e

    for runtime_log in agents bridge mission deploy vhf_injector; do
        source_log="$ROOT_DIR/logs/$runtime_log.log"
        [[ -f "$source_log" ]] && cp "$source_log" "$case_dir/runtime/$runtime_log.log"
    done

    mission_complete=false
    mission_failed=false
    cycle_limit=false
    timed_out=false
    grep -Eq '"event"[[:space:]]*:[[:space:]]*"mission_complete"' "$audit" && mission_complete=true
    grep -Eq '"event"[[:space:]]*:[[:space:]]*"mission_failed"' "$audit" && mission_failed=true
    grep -Eq '"event"[[:space:]]*:[[:space:]]*"cycle_limit_reached"' "$audit" && cycle_limit=true
    ((exit_status == 124)) && timed_out=true
    terminal=false
    [[ "$mission_complete" == "true" || "$mission_failed" == "true" ]] && terminal=true
    run_finished=false
    [[ "$terminal" == "true" || "$cycle_limit" == "true" ]] && run_finished=true

    pipeline_complete=false
    if [[ -s "$audit" ]]; then
        "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/score_vhf_run.py" \
            --manifest "$ROOT_DIR/mission/scenarios/vhf/$case_id/$case_id.vhf.json" \
            --audit "$audit" --output "$score_file" >/dev/null
        if [[ "$CONDITION" == "vhf" ]]; then
            # Completeness measures transport into the decision context.
            # Interpretation accuracy is an experimental outcome in the score,
            # not a reason to classify an otherwise complete run as broken.
            if jq -e '
                (.events_received == .events_expected)
                and (.pipeline_errors | length == 0)
                and ([.events[].pipeline_delivery] | all)
            ' "$score_file" >/dev/null; then
                pipeline_complete=true
            fi
        elif jq -e '
            (.events_received == 0)
            and (.pipeline_errors | length == 0)
        ' "$score_file" >/dev/null && \
            ! grep -Eq '"event"[[:space:]]*:[[:space:]]*"vhf_event_record"' "$audit"; then
            # For the matched control, completeness means the event injector
            # stayed disabled and no VHF observation contaminated the prompt.
            pipeline_complete=true
        fi
    fi

    printf '{"case":%d,"exit_status":%d,"mission_complete":%s,"mission_failed":%s,"terminal":%s,"cycle_limit":%s,"run_finished":%s,"timed_out":%s,"pipeline_complete":%s}\n' \
        "$case_number" "$exit_status" "$mission_complete" "$mission_failed" \
        "$terminal" "$cycle_limit" "$run_finished" "$timed_out" "$pipeline_complete" \
        > "$status_file"
    cat "$status_file"

    if [[ "$run_finished" != "true" || "$timed_out" == "true" || "$pipeline_complete" != "true" ]]; then
        ((incomplete_cases+=1))
    fi
done

: > "$OUTPUT_DIR/status.jsonl"
: > "$OUTPUT_DIR/vhf_scores.jsonl"
for case_number in "${CASE_LIST[@]}"; do
    printf -v case_id '%02d' "$case_number"
    status_file="$OUTPUT_DIR/case_$case_id/status.json"
    score_file="$OUTPUT_DIR/case_$case_id/vhf_score.json"
    [[ -s "$status_file" ]] && jq -c . "$status_file" >> "$OUTPUT_DIR/status.jsonl"
    [[ -s "$score_file" ]] && jq -c . "$score_file" >> "$OUTPUT_DIR/vhf_scores.jsonl"
done

if ((incomplete_cases > 0)); then
    echo "VHF evaluation finished with $incomplete_cases incomplete case(s): $OUTPUT_DIR" >&2
    exit 1
fi

echo "VHF evaluation finished successfully: $OUTPUT_DIR"
