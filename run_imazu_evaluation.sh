#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ARCHITECTURE="base"
CASES="1-22"
TIMEOUT_SECONDS=4800
MAX_CYCLES=240
TIME_WARP=10
DECISION_INTERVAL=10
FREEZE_DURING_DECISION="no"
ENCOUNTER_ONLY="no"
ENCOUNTER_TRIGGER_RANGE=6000
OUTPUT_DIR=""
RESUME="no"
CHECK_ONLY="no"
GUI="yes"
CONFIG_FILE="$ROOT_DIR/project.ini"
OPENAI_MODEL=""
ADAPTER_PATH=""

usage() {
    cat <<'EOF'
Usage: ./run_imazu_evaluation.sh --architecture NAME [OPTIONS]

Run one OOW architecture over a reproducible set of Imazu scenarios.

Architectures:
  base                    Untuned Qwen, full prompt, no retrieval, /no_think
  base-raw-state          Untuned Qwen, processed numeric JSON, no narrative
  base-eriksen            Untuned Qwen, full prompt plus Eriksen roles and all applicable rules, /no_think
  base-reasoning          Untuned Qwen, full prompt, no retrieval, /think
  colregs-rag             Untuned Qwen, full prompt, COLREG summary RAG, /no_think
  colregs-rag-reasoning   Untuned Qwen, full prompt, COLREG summary RAG, /think
  colregs-tree            Untuned Qwen, full prompt, decision-tree summaries, /no_think
  colregs-tree-reasoning  Untuned Qwen, full prompt, decision-tree summaries, /think
  maib-rag                Untuned narrative OOW plus MAIB-RAG captain critic, /no_think
  finetuned               Qwen with the local SFT LoRA adapter, compact prompt, /no_think
  finetuned-base-narrative
                          Qwen with an SFT LoRA adapter, full base prompt, /no_think
  finetuned-narrative-only
                          Qwen SFT ablation: short contract plus plain narrative
  finetuned-directional-narrative
                          Qwen SFT: categorical port/starboard plus turn magnitude
  teacher                 OpenAI full-prompt teacher (default model: gpt-6-luna)

Options:
  --architecture NAME  Run exactly one architecture (default: base)
  --config FILE        Alternate project.ini (for selecting a separate adapter)
  --cases SPEC         Cases such as 1-22 or 1,3,12 (default: 1-22)
  --timeout N          Wall-clock seconds allowed per case (default: 4800)
  --max-cycles N       Decision-cycle limit per case (default: 240)
  --time-warp N        MOOS time warp (default: 10)
  --decision-interval N
                       Simulated seconds between decisions (default: 10)
  --freeze-during-decision
                       Freeze all vessel dynamics during model inference
  --no-freeze-during-decision
                       Preserve the historical asynchronous behavior (default)
  --encounter-only     Call the model only while traffic is within the trigger
                       range or an encounter/action requires follow-up
  --no-encounter-only  Call the model every decision interval (default)
  --encounter-range N  Contact range in metres for encounter-only inference
                       (default: 6000)
  --openai-model ID    OpenAI model for the teacher architecture
  --adapter PATH       Override the configured LoRA adapter for a fine-tuned architecture
  --output DIR         Output directory (default: timestamped by architecture)
  --resume             Skip cases that already have a result.json
  --gui                Open pMarineViewer for each scenario (default)
  --nogui              Run without pMarineViewer
  --check              Validate selected scenarios without running them
  --list-architectures Print architecture names and exit
  --help, -h           Show this help
EOF
}

while (($#)); do
    case "$1" in
        --architecture) ARCHITECTURE=$2; shift 2 ;;
        --config) CONFIG_FILE=$(realpath "$2"); shift 2 ;;
        --cases) CASES=$2; shift 2 ;;
        --timeout) TIMEOUT_SECONDS=$2; shift 2 ;;
        --max-cycles) MAX_CYCLES=$2; shift 2 ;;
        --time-warp) TIME_WARP=$2; shift 2 ;;
        --decision-interval) DECISION_INTERVAL=$2; shift 2 ;;
        --freeze-during-decision) FREEZE_DURING_DECISION="yes"; shift ;;
        --no-freeze-during-decision) FREEZE_DURING_DECISION="no"; shift ;;
        --encounter-only) ENCOUNTER_ONLY="yes"; shift ;;
        --no-encounter-only) ENCOUNTER_ONLY="no"; shift ;;
        --encounter-range) ENCOUNTER_TRIGGER_RANGE=$2; shift 2 ;;
        --openai-model) OPENAI_MODEL=$2; shift 2 ;;
        --adapter) ADAPTER_PATH=$(realpath "$2"); shift 2 ;;
        --output) OUTPUT_DIR=$2; shift 2 ;;
        --resume) RESUME="yes"; shift ;;
        --gui) GUI="yes"; shift ;;
        --nogui) GUI="no"; shift ;;
        --check) CHECK_ONLY="yes"; shift ;;
        --list-architectures)
            printf '%s\n' base base-raw-state base-eriksen base-reasoning colregs-rag colregs-rag-reasoning colregs-tree colregs-tree-reasoning maib-rag finetuned finetuned-base-narrative finetuned-narrative-only finetuned-directional-narrative teacher
            exit 0
            ;;
        --help|-h) usage; exit 0 ;;
        *) echo "run_imazu_evaluation.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$ARCHITECTURE" in
    base) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow base) ;;
    base-raw-state) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --raw-state --no-reasoning --oow base) ;;
    base-eriksen) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --eriksen --narrative --no-reasoning --oow base) ;;
    base-reasoning) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --reasoning --oow base) ;;
    colregs-rag) ARCH_FLAGS=(--no-rag --colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow base) ;;
    colregs-rag-reasoning) ARCH_FLAGS=(--no-rag --colregs-rag --no-colregs-tree --no-eriksen --narrative --reasoning --oow base) ;;
    colregs-tree) ARCH_FLAGS=(--no-rag --no-colregs-rag --colregs-tree --no-eriksen --narrative --no-reasoning --oow base) ;;
    colregs-tree-reasoning) ARCH_FLAGS=(--no-rag --no-colregs-rag --colregs-tree --no-eriksen --narrative --reasoning --oow base) ;;
    maib-rag) ARCH_FLAGS=(--rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow base) ;;
    finetuned) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow finetuned) ;;
    finetuned-base-narrative) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow finetuned --finetuned-prompt base-narrative) ;;
    finetuned-narrative-only) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow finetuned --finetuned-prompt narrative-only) ;;
    finetuned-directional-narrative) ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow finetuned --finetuned-prompt narrative-directional) ;;
    teacher)
        OPENAI_MODEL=${OPENAI_MODEL:-${OPENAI_TEACHER_MODEL:-gpt-6-luna}}
        [[ -n "${OPENAI_API_KEY:-}" ]] || {
            echo "OPENAI_API_KEY is required for the teacher architecture" >&2
            exit 2
        }
        ARCH_FLAGS=(--no-rag --no-colregs-rag --no-colregs-tree --no-eriksen --narrative --no-reasoning --oow base --openai-api "$OPENAI_MODEL")
        ;;
    *) echo "unknown architecture: $ARCHITECTURE" >&2; usage >&2; exit 2 ;;
esac
if [[ -n "$ADAPTER_PATH" ]]; then
    case "$ARCHITECTURE" in
        finetuned|finetuned-base-narrative|finetuned-narrative-only|finetuned-directional-narrative) ;;
        *) echo "--adapter is valid only for a fine-tuned architecture" >&2; exit 2 ;;
    esac
    [[ -f "$ADAPTER_PATH/adapter_config.json" ]] || {
        echo "LoRA adapter not found: $ADAPTER_PATH" >&2
        exit 2
    }
    [[ ! -f "$ADAPTER_PATH/INVALID_SFT_PROVENANCE.json" ]] || {
        echo "Refusing invalidated adapter: $ADAPTER_PATH" >&2
        cat "$ADAPTER_PATH/INVALID_SFT_PROVENANCE.json" >&2
        exit 2
    }
    export MOOS_LLM_OOW_ADAPTER_PATH="$ADAPTER_PATH"
fi
ARCH_FLAGS+=(--config "$CONFIG_FILE")
export MOOS_LLM_CONFIG="$CONFIG_FILE"

# A configured RAG experiment must never degrade silently to an empty
# retriever. Resolve paths from the repository root before starting vLLM or a
# simulator process.
if [[ "$ARCHITECTURE" == "maib-rag" ]]; then
    RAG_INDEX=$("$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/config_value.py" maib_rag index_path --config "$CONFIG_FILE")
    RAG_EMBEDDINGS=$("$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/config_value.py" maib_rag embeddings_path --config "$CONFIG_FILE")
    [[ "$RAG_INDEX" == /* ]] || RAG_INDEX="$ROOT_DIR/$RAG_INDEX"
    [[ "$RAG_EMBEDDINGS" == /* ]] || RAG_EMBEDDINGS="$ROOT_DIR/$RAG_EMBEDDINGS"
    [[ -s "$RAG_INDEX" ]] || {
        echo "MAIB RAG index is missing or empty: $RAG_INDEX" >&2
        exit 2
    }
    [[ -s "$RAG_EMBEDDINGS" ]] || {
        echo "MAIB RAG embeddings are missing or empty: $RAG_EMBEDDINGS" >&2
        exit 2
    }
fi

[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "--timeout must be positive" >&2; exit 2; }
[[ "$MAX_CYCLES" =~ ^[1-9][0-9]*$ ]] || { echo "--max-cycles must be positive" >&2; exit 2; }
[[ "$TIME_WARP" =~ ^[1-9][0-9]*$ ]] || { echo "--time-warp must be positive" >&2; exit 2; }
[[ "$DECISION_INTERVAL" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] && \
    awk -v value="$DECISION_INTERVAL" 'BEGIN { exit !(value > 0) }' || {
    echo "--decision-interval must be a positive number" >&2
    exit 2
}
[[ "$ENCOUNTER_TRIGGER_RANGE" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] && \
    awk -v value="$ENCOUNTER_TRIGGER_RANGE" 'BEGIN { exit !(value > 0) }' || {
    echo "--encounter-range must be a positive number" >&2
    exit 2
}
LOCKSTEP_FLAGS=(--decision-interval "$DECISION_INTERVAL")
if [[ "$FREEZE_DURING_DECISION" == "yes" ]]; then
    LOCKSTEP_FLAGS+=(--freeze-during-decision)
else
    LOCKSTEP_FLAGS+=(--no-freeze-during-decision)
fi

expand_cases() {
    local part first last item
    IFS=',' read -ra parts <<< "$CASES"
    for part in "${parts[@]}"; do
        if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            first=${BASH_REMATCH[1]}; last=${BASH_REMATCH[2]}
            ((first <= last)) || { echo "invalid case range: $part" >&2; return 2; }
            for ((item=first; item<=last; item++)); do printf '%d\n' "$item"; done
        elif [[ "$part" =~ ^[0-9]+$ ]]; then
            printf '%d\n' "$part"
        else
            echo "invalid case specification: $part" >&2; return 2
        fi
    done
}

mapfile -t CASE_LIST < <(expand_cases)
for case_number in "${CASE_LIST[@]}"; do
    ((case_number >= 1 && case_number <= 22)) || { echo "case must be in 1..22: $case_number" >&2; exit 2; }
done

"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/generate_imazu_scenarios.py" --check
if [[ "$ARCHITECTURE" == "teacher" ]]; then
    "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/generate_imazu_scenarios.py" --compact
fi
if [[ "$CHECK_ONLY" == "yes" ]]; then
    for case_number in "${CASE_LIST[@]}"; do
        printf -v case_id '%02d' "$case_number"
        scenario_name="imazu/$case_id"
        [[ "$ARCHITECTURE" == "teacher" ]] && scenario_name="imazu/teacher_compact/$case_id"
        check_args=("${ARCH_FLAGS[@]}" "${LOCKSTEP_FLAGS[@]}" --scenario "$scenario_name" --tiff none --time-warp "$TIME_WARP" --check)
        [[ "$GUI" == "no" ]] && check_args+=(--nogui)
        "$ROOT_DIR/run_simulator.sh" "${check_args[@]}"
    done
    echo "Imazu architecture configuration valid: $ARCHITECTURE ($CASES)"
    exit 0
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)_$ARCHITECTURE
    OUTPUT_DIR="$ROOT_DIR/logs/evaluations/imazu/$RUN_ID"
elif [[ "$OUTPUT_DIR" != /* ]]; then
    OUTPUT_DIR="$ROOT_DIR/$OUTPUT_DIR"
fi
mkdir -p "$OUTPUT_DIR"

# Preserve the exact configuration and hash every implementation/input artifact
# that can affect this campaign. This makes an output directory independently
# auditable even when the working tree changes later.
SNAPSHOT_DIR="$OUTPUT_DIR"
if [[ "$RESUME" == "yes" && -s "$OUTPUT_DIR/manifest.json" && -s "$OUTPUT_DIR/provenance.sha256" ]]; then
    resume_id="$(date -u +%Y%m%dT%H%M%SZ)_${BASHPID}"
    SNAPSHOT_DIR="$OUTPUT_DIR/resume_history/$resume_id"
    mkdir -p "$SNAPSHOT_DIR"
    echo "Preserving original Imazu provenance; resume snapshot: $SNAPSHOT_DIR"
fi
cp "$CONFIG_FILE" "$SNAPSHOT_DIR/project.ini"
CONFIG_SHA256=$(sha256sum "$SNAPSHOT_DIR/project.ini" | awk '{print $1}')
RAG_INDEX_SHA256=""
RAG_EMBEDDINGS_SHA256=""
if [[ "$ARCHITECTURE" == "maib-rag" ]]; then
    RAG_INDEX_SHA256=$(sha256sum "$RAG_INDEX" | awk '{print $1}')
    RAG_EMBEDDINGS_SHA256=$(sha256sum "$RAG_EMBEDDINGS" | awk '{print $1}')
fi
{
    sha256sum "$SNAPSHOT_DIR/project.ini"
    sha256sum "$ROOT_DIR/run_imazu_evaluation.sh" "$ROOT_DIR/run_simulator.sh"
    sha256sum "$ROOT_DIR/main.py" "$ROOT_DIR/agents/agent_captain.py" "$ROOT_DIR/rag/maib.py"
    sha256sum "$ROOT_DIR/agents/agent_oow.py" "$ROOT_DIR/bridge/bridge.py"
    sha256sum "$ROOT_DIR/bridge/src/MoosBridge.cpp" "$ROOT_DIR/bridge/src/MoosBridge.h" "$ROOT_DIR/bridge/src/main.cpp"
    sha256sum "$ROOT_DIR/mission/launch.sh" "$ROOT_DIR/mission/launch_vehicle.sh" "$ROOT_DIR/mission/plugs.moos"
    sha256sum "$ROOT_DIR/mission/meta_vehicle.moos" "$ROOT_DIR/mission/meta_shoreside.moos"
    sha256sum /home/lpagg/moos-ivp/ivp/src/uSimMarineV22/USM_Model.cpp /home/lpagg/moos-ivp/bin/uSimMarineV22
    sha256sum "$ROOT_DIR/agents/skills/oow_decision.md" "$ROOT_DIR/agents/skills/captain.md"
    if [[ "$ARCHITECTURE" == "maib-rag" ]]; then
        sha256sum "$RAG_INDEX" "$RAG_EMBEDDINGS"
    fi
} > "$SNAPSHOT_DIR/provenance.sha256"

printf '{"suite":"imazu","architecture":"%s","cases":"%s","timeout_seconds":%d,"max_cycles":%d,"time_warp":%d,"freeze_during_decision":%s,"decision_interval_simulated_s":%s,"encounter_only":%s,"encounter_trigger_range_m":%s,"openai_model":"%s","adapter_path":"%s","config_snapshot":"project.ini","config_sha256":"%s","rag_index_sha256":"%s","rag_embeddings_sha256":"%s","provenance":"provenance.sha256","created_utc":"%s"}\n' \
    "$ARCHITECTURE" "$CASES" "$TIMEOUT_SECONDS" "$MAX_CYCLES" "$TIME_WARP" "$([[ "$FREEZE_DURING_DECISION" == "yes" ]] && echo true || echo false)" "$DECISION_INTERVAL" "$([[ "$ENCOUNTER_ONLY" == "yes" ]] && echo true || echo false)" "$ENCOUNTER_TRIGGER_RANGE" "$OPENAI_MODEL" "$ADAPTER_PATH" "$CONFIG_SHA256" "$RAG_INDEX_SHA256" "$RAG_EMBEDDINGS_SHA256" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$SNAPSHOT_DIR/manifest.json"

# Keep the model resident across the full suite. Per-case supervisors use the
# configured external API and therefore never stop or restart this process.
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
if [[ "$ARCHITECTURE" != "teacher" ]]; then
    setsid "$ROOT_DIR/run_simulator.sh" "${ARCH_FLAGS[@]}" --vllm-only \
        >"$OUTPUT_DIR/vllm_supervisor.log" 2>&1 &
    VLLM_SUPERVISOR_PID=$!

    # Wait outside the per-case timeout, before any mission or stale-agent cleanup.
    model_key=default_local
    model_section=models
    if [[ "$ARCHITECTURE" == "finetuned" || "$ARCHITECTURE" == "finetuned-base-narrative" || "$ARCHITECTURE" == "finetuned-narrative-only" || "$ARCHITECTURE" == "finetuned-directional-narrative" ]]; then
        model_section=oow
        model_key=finetuned_model_name
    fi
    EVALUATION_MODEL=$("$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/config_value.py" "$model_section" "$model_key" --config "$CONFIG_FILE")
    VLLM_STARTUP_TIMEOUT=$("$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/config_value.py" runtime vllm_startup_timeout_seconds --config "$CONFIG_FILE" 2>/dev/null || echo 600)
    cd "$ROOT_DIR"
    "$ROOT_DIR/.venv/bin/python" -m tools.wait_vllm_ready \
        --model "$EVALUATION_MODEL" --timeout "$VLLM_STARTUP_TIMEOUT" \
        --process-pid "$VLLM_SUPERVISOR_PID"
fi

for case_number in "${CASE_LIST[@]}"; do
    printf -v case_id '%02d' "$case_number"
    case_dir="$OUTPUT_DIR/case_$case_id"
    audit="$case_dir/audit.jsonl"
    result="$case_dir/result.json"
    if [[ "$RESUME" == "yes" && -s "$result" ]]; then
        existing_result=$("$ROOT_DIR/.venv/bin/python" -c \
            'import json,sys; print(json.load(open(sys.argv[1])).get("result", "failed"))' \
            "$result" 2>/dev/null || echo failed)
        if [[ "$existing_result" != "failed" ]]; then
            echo "[Imazu $case_id] already has terminal result $existing_result; skipping"
            continue
        fi
        echo "[Imazu $case_id] previous infrastructure failure found; rerunning"
    fi
    # Fail immediately if the shared server has died instead of timing out
    # every remaining scenario and overwriting their results with failures.
    if [[ "$ARCHITECTURE" != "teacher" ]]; then
        "$ROOT_DIR/.venv/bin/python" -m tools.wait_vllm_ready \
            --model "$EVALUATION_MODEL" --timeout 5 \
            --process-pid "$VLLM_SUPERVISOR_PID"
    fi
    mkdir -p "$case_dir/runtime"
    : > "$audit"
    scenario_name="imazu/$case_id"
    scenario_source="$ROOT_DIR/mission/scenarios/imazu/$case_id/$case_id.ini"
    if [[ "$ARCHITECTURE" == "teacher" ]]; then
        scenario_name="imazu/teacher_compact/$case_id"
        scenario_source="$ROOT_DIR/mission/scenarios/imazu/teacher_compact/$case_id/$case_id.ini"
    fi
    cp "$scenario_source" "$case_dir/scenario.ini"
    echo "[Imazu $case_id] running $ARCHITECTURE with $scenario_name"
    simulator_args=(--deploy "${ARCH_FLAGS[@]}" "${LOCKSTEP_FLAGS[@]}" --scenario "$scenario_name"
        --tiff none --time-warp "$TIME_WARP" --audit-log "$audit")
    [[ "$ARCHITECTURE" != "teacher" ]] && simulator_args+=(--external-vllm)
    [[ "$GUI" == "no" ]] && simulator_args+=(--nogui)
    set +e
    timeout --signal=TERM --kill-after=20 "$TIMEOUT_SECONDS" \
        env MOOS_LLM_EXIT_ON_COMPLETE=1 MOOS_LLM_MAX_CYCLES="$MAX_CYCLES" \
        MOOS_LLM_ENCOUNTER_ONLY="$ENCOUNTER_ONLY" \
        MOOS_LLM_TEACHER_TRIGGER_RANGE_M="$ENCOUNTER_TRIGGER_RANGE" \
        MOOS_LLM_VLLM_SUPERVISOR_PID="$VLLM_SUPERVISOR_PID" \
        "$ROOT_DIR/run_simulator.sh" \
        "${simulator_args[@]}"
    status=$?
    set -e
    for runtime_log in agents bridge mission vllm deploy; do
        source_log="$ROOT_DIR/logs/$runtime_log.log"
        [[ -f "$source_log" ]] && cp "$source_log" "$case_dir/runtime/$runtime_log.log"
    done
    [[ -f "$ROOT_DIR/mission/targ_LLM_SHIP.bhv" ]] && cp "$ROOT_DIR/mission/targ_LLM_SHIP.bhv" "$case_dir/rendered_ownship.bhv"
    [[ -f "$ROOT_DIR/mission/targ_LLM_SHIP.moos" ]] && cp "$ROOT_DIR/mission/targ_LLM_SHIP.moos" "$case_dir/rendered_ownship.moos"
    mission_complete=false
    grep -Eq '"event"[[:space:]]*:[[:space:]]*"mission_complete"' "$audit" 2>/dev/null && mission_complete=true
    timed_out=false
    ((status == 124)) && timed_out=true
    printf '{"case":%d,"exit_status":%d,"mission_complete":%s,"timed_out":%s}\n' \
        "$case_number" "$status" "$mission_complete" "$timed_out" > "$case_dir/status.json"
    summary_args=("$audit" --case "$case_number" --exit-status "$status" --architecture "$ARCHITECTURE")
    ((status == 124)) && summary_args+=(--timed-out)
    "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/summarize_imazu_run.py" "${summary_args[@]}" > "$result"
    cat "$result"
done

: > "$OUTPUT_DIR/results.jsonl"
for case_number in "${CASE_LIST[@]}"; do
    printf -v case_id '%02d' "$case_number"
    result="$OUTPUT_DIR/case_$case_id/result.json"
    [[ -s "$result" ]] && cat "$result" >> "$OUTPUT_DIR/results.jsonl"
done

full_suite=yes
for case_number in {1..22}; do
    printf -v case_id '%02d' "$case_number"
    case_dir="$OUTPUT_DIR/case_$case_id"
    for required in audit.jsonl scenario.ini status.json; do
        [[ -s "$case_dir/$required" ]] || full_suite=no
    done
done
if [[ "$full_suite" == "yes" ]]; then
    echo "Scoring complete 22-case Imazu campaign"
    "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/score_baseline_logs.py" \
        "$OUTPUT_DIR" --output "$OUTPUT_DIR/scores"
fi

if [[ "$ARCHITECTURE" == "teacher" ]]; then
    teacher_audits=()
    for case_number in "${CASE_LIST[@]}"; do
        printf -v case_id '%02d' "$case_number"
        audit="$OUTPUT_DIR/case_$case_id/audit.jsonl"
        [[ -s "$audit" ]] && teacher_audits+=("$audit")
    done
    if ((${#teacher_audits[@]})); then
        "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/extract_oow_pairs.py" \
            "${teacher_audits[@]}" --output "$OUTPUT_DIR/teacher_pairs.jsonl"
        "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/extract_oow_pairs.py" \
            "${teacher_audits[@]}" --validated-only --chat-format \
            --output "$OUTPUT_DIR/sft_validated.jsonl"
    fi
fi

echo "Imazu evaluation finished: $OUTPUT_DIR"
