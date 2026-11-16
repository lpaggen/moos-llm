#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE="$ROOT_DIR/project.ini"
CONFIG_VALUE="$ROOT_DIR/tools/config_value.py"

config_value() {
    python3 "$CONFIG_VALUE" "$1" "$2" --config "$CONFIG_FILE"
}

usage() {
    cat <<'EOF'
Usage: ./run_simulator.sh [OPTIONS] [-- MISSION_ARGS...]

Starts vLLM, the configured MOOS mission, and pLLMBridge. Values are read from
project.ini. Arguments after -- are forwarded to the mission launch script.

Options:
  --scenario NAME    Scenario to run, for example imazu/03 or open_waters
  --tiff FILE        Override the scenario background (`none` disables it)
  --config FILE      Use an alternate project.ini
  --nogui            Run MOOS without pMarineViewer
  --rag               Enable MAIB RAG for this run
  --no-rag            Disable MAIB RAG for this run
  --colregs-rag       Enable COLREG RAG for the OOW
  --no-colregs-rag    Disable COLREG RAG (default)
  --colregs-tree      Select COLREG summaries with the deterministic decision tree
  --no-colregs-tree   Disable COLREG decision tree (default)
  --eriksen           Include persistent Eriksen classifier evidence in the OOW prompt
  --no-eriksen        Exclude Eriksen classifier evidence (default)
  --raw-state         Send processed numeric JSON instead of the prose narrative
  --narrative         Send the prose narrative (default)
  --reasoning         Enable Qwen thinking/reasoning for the OOW
  --no-reasoning      Disable Qwen thinking/reasoning (default)
  --oow base           Run untuned Qwen with full navigation guidance (default)
  --oow finetuned      Run the OOW LoRA adapter with its compact prompt
  --finetuned-prompt PROFILE
                       Fine-tuned prompt: compact (default), base-narrative,
                       narrative-only, or narrative-directional
  --controller NAME    Controller: llm (default) or moos-ivp
  --external-vllm      Use an already-running configured vLLM server
  --openai-api MODEL   Use OpenAI API instead of vLLM for the OOW
  --openai-prompt-profile PROFILE
                       OpenAI prompt: teacher (privileged, default) or runtime-base (exact local base prompt)
  --vllm-only          Start vLLM and wait; do not start a mission
  --time-warp N        Override configured simulation time warp
  --decision-interval N
                       Simulated seconds between decisions (default: project.ini)
  --freeze-during-decision
                       Freeze every vessel while the OOW/captain is deciding
  --no-freeze-during-decision
                       Leave vessel dynamics running during inference (default)
  --audit-log FILE    Write this run's agent audit to FILE
  --deploy            Deploy all scenario vehicles after launch
  --vhf-events        Require and inject the scenario's VHF sidecar events
  --no-vhf-events     Run VHF geometry without injecting its messages
  --check            Resolve and validate inputs without starting services
  --help, -h         Show this help
EOF
}

NOGUI=""
SCENARIO=""
TIFF_FILE=""
CHECK_ONLY="no"
RAG_MODE="config"
COLREG_RAG_MODE="off"
COLREG_TREE_MODE="off"
ERIKSEN_PROMPT_MODE="off"
OOW_INPUT_FORMAT="narrative"
REASONING_MODE="off"
OOW_MODE="base"
FINETUNED_PROMPT_PROFILE="compact"
CONTROLLER="llm"
TIME_WARP_OVERRIDE=""
DECISION_INTERVAL=""
FREEZE_DURING_DECISION="off"
NAVIGATION_PROFILE="default"
PROFILE_MIN_SAFE_CPA=""
PROFILE_CAUTION_CPA=""
PROFILE_BOW_CROSSING=""
PROFILE_CRITICAL_CPA=""
PROFILE_MANEUVER_DISTANCE=""
AUDIT_LOG_FILE=""
AUTO_DEPLOY="no"
EXTERNAL_VLLM="no"
OPENAI_API="no"
OPENAI_MODEL=""
OPENAI_PROMPT_PROFILE="teacher"
VLLM_ONLY="no"
VHF_MANIFEST=""
VHF_BENCHMARK="off"
VHF_RUN_INBOX=""
VHF_EVENT_MODE="auto"
SCENARIO_OBSTACLES=""
MISSION_ARGS=()
while (($#)); do
    case "$1" in
        --config)
            if (($# < 2)); then
                echo "run_simulator.sh: --config requires a file" >&2
                exit 2
            fi
            CONFIG_FILE=$(realpath "$2")
            shift 2
            ;;
        --scenario)
            if (($# < 2)); then
                echo "run_simulator.sh: --scenario requires a name or INI file" >&2
                exit 2
            fi
            SCENARIO=$2
            shift 2
            ;;
        --scenario=*)
            SCENARIO=${1#--scenario=}
            shift
            ;;
        --tiff)
            if (($# < 2)); then
                echo "run_simulator.sh: --tiff requires a file or 'none'" >&2
                exit 2
            fi
            TIFF_FILE=$2
            shift 2
            ;;
        --tiff=*)
            TIFF_FILE=${1#--tiff=}
            shift
            ;;
        --nogui)
            NOGUI="--nogui"
            shift
            ;;
        --rag)
            RAG_MODE="on"
            shift
            ;;
        --no-rag)
            RAG_MODE="off"
            shift
            ;;
        --colregs-rag)
            COLREG_RAG_MODE="on"
            shift
            ;;
        --no-colregs-rag)
            COLREG_RAG_MODE="off"
            shift
            ;;
        --colregs-tree)
            COLREG_TREE_MODE="on"
            shift
            ;;
        --no-colregs-tree)
            COLREG_TREE_MODE="off"
            shift
            ;;
        --eriksen)
            ERIKSEN_PROMPT_MODE="on"
            shift
            ;;
        --no-eriksen)
            ERIKSEN_PROMPT_MODE="off"
            shift
            ;;
        --raw-state)
            OOW_INPUT_FORMAT="raw-state"
            shift
            ;;
        --narrative)
            OOW_INPUT_FORMAT="narrative"
            shift
            ;;
        --reasoning)
            REASONING_MODE="on"
            shift
            ;;
        --no-reasoning)
            REASONING_MODE="off"
            shift
            ;;
        --oow)
            if (($# < 2)) || [[ "$2" != "base" && "$2" != "finetuned" ]]; then
                echo "run_simulator.sh: --oow requires 'base' or 'finetuned'" >&2
                exit 2
            fi
            OOW_MODE=$2
            shift 2
            ;;
        --finetuned-prompt)
            if (($# < 2)) || [[ "$2" != "compact" && "$2" != "base-narrative" && "$2" != "narrative-only" && "$2" != "narrative-directional" ]]; then
                echo "run_simulator.sh: --finetuned-prompt requires 'compact', 'base-narrative', 'narrative-only', or 'narrative-directional'" >&2
                exit 2
            fi
            FINETUNED_PROMPT_PROFILE=$2
            shift 2
            ;;
        --controller)
            if (($# < 2)) || [[ "$2" != "llm" && "$2" != "moos-ivp" ]]; then
                echo "run_simulator.sh: --controller requires 'llm' or 'moos-ivp'" >&2
                exit 2
            fi
            CONTROLLER=$2
            shift 2
            ;;
        --external-vllm)
            EXTERNAL_VLLM="yes"
            shift
            ;;
        --openai-api)
            if (($# < 2)); then
                echo "run_simulator.sh: --openai-api requires a model" >&2
                exit 2
            fi
            OPENAI_API="yes"
            OPENAI_MODEL=$2
            shift 2
            ;;
        --openai-prompt-profile)
            if (($# < 2)) || [[ "$2" != "teacher" && "$2" != "runtime-base" ]]; then
                echo "run_simulator.sh: --openai-prompt-profile requires 'teacher' or 'runtime-base'" >&2
                exit 2
            fi
            OPENAI_PROMPT_PROFILE=$2
            shift 2
            ;;
        --vllm-only)
            VLLM_ONLY="yes"
            shift
            ;;
        --time-warp)
            TIME_WARP_OVERRIDE=$2
            shift 2
            ;;
        --decision-interval)
            if (($# < 2)); then
                echo "run_simulator.sh: --decision-interval requires seconds" >&2
                exit 2
            fi
            DECISION_INTERVAL=$2
            shift 2
            ;;
        --freeze-during-decision)
            FREEZE_DURING_DECISION="on"
            shift
            ;;
        --no-freeze-during-decision)
            FREEZE_DURING_DECISION="off"
            shift
            ;;
        --audit-log)
            if (($# < 2)); then
                echo "run_simulator.sh: --audit-log requires a file" >&2
                exit 2
            fi
            AUDIT_LOG_FILE=$2
            shift 2
            ;;
        --deploy)
            AUTO_DEPLOY="yes"
            shift
            ;;
        --vhf-events)
            VHF_EVENT_MODE="on"
            shift
            ;;
        --no-vhf-events)
            VHF_EVENT_MODE="off"
            shift
            ;;
        --check)
            CHECK_ONLY="yes"
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            MISSION_ARGS+=("$@")
            break
            ;;
        *)
            echo "run_simulator.sh: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "run_simulator.sh: configuration not found: $CONFIG_FILE" >&2
    exit 1
fi

MODEL=$(config_value models default_local)
OOW_FINETUNED_MODEL=$(config_value oow finetuned_model_name)
OOW_ADAPTER_PATH=${MOOS_LLM_OOW_ADAPTER_PATH:-$(config_value oow adapter_path)}
VLLM_HOST=$(config_value hosts vllm)
VLLM_PORT=$(config_value ports vllm)
VLLM_API_KEY=$(config_value llm api_key)
MAX_MODEL_LEN=$(config_value llm max_model_len)

MOOS_HOST=$(config_value hosts moos)
MOOS_BASE_PORT=$(config_value ports moos_base)
BRIDGE_PORT=$(config_value ports bridge)
OWN_SHIP_VEHICLE_INDEX=$(config_value mission own_ship_vehicle_index)
OWN_SHIP_MOOS_PORT=$((MOOS_BASE_PORT + OWN_SHIP_VEHICLE_INDEX))

TIME_WARP=$(config_value runtime time_warp)
STARTUP_TIMEOUT=$(config_value runtime startup_timeout_seconds)
VLLM_STARTUP_TIMEOUT=$(config_value runtime vllm_startup_timeout_seconds 2>/dev/null || echo 600)
MISSION_RELATIVE_DIR=$(config_value mission directory)
FLEET_CONFIG=$(config_value mission fleet_config)
LOGS_RELATIVE_DIR=$(config_value paths runtime_logs)

MISSION_DIR="$ROOT_DIR/$MISSION_RELATIVE_DIR"
LOGS_DIR="$ROOT_DIR/$LOGS_RELATIVE_DIR"
BRIDGE_BUILD_DIR="$ROOT_DIR/build"
BRIDGE_EXECUTABLE="$BRIDGE_BUILD_DIR/pLLMBridge"
if [[ "$OOW_ADAPTER_PATH" != /* ]]; then
    OOW_ADAPTER_PATH="$ROOT_DIR/$OOW_ADAPTER_PATH"
fi
if [[ "$OPENAI_API" == "yes" ]]; then
    [[ "$OOW_MODE" == "base" ]] || { echo "OpenAI teacher mode requires --oow base" >&2; exit 2; }
    [[ "$EXTERNAL_VLLM" == "no" ]] || { echo "Choose --openai-api or --external-vllm" >&2; exit 2; }
    OOW_MODEL="$OPENAI_MODEL"
elif [[ "$OOW_MODE" == "finetuned" && ! -f "$OOW_ADAPTER_PATH/adapter_config.json" ]]; then
    echo "run_simulator.sh: OOW adapter not found: $OOW_ADAPTER_PATH" >&2
    exit 1
else
    OOW_MODEL="$MODEL"
fi
if [[ "$OPENAI_API" == "no" && "$OOW_MODE" == "finetuned" ]]; then
    OOW_MODEL="$OOW_FINETUNED_MODEL"
fi

resolve_scenario() {
    local requested=$1
    local candidate=""
    local scenario_dir=""

    if [[ -f "$requested" ]]; then
        candidate=$(realpath "$requested")
    elif [[ -f "$MISSION_DIR/$requested" ]]; then
        candidate=$(realpath "$MISSION_DIR/$requested")
    elif [[ -f "$MISSION_DIR/scenarios/$requested" ]]; then
        candidate=$(realpath "$MISSION_DIR/scenarios/$requested")
    elif [[ -f "$MISSION_DIR/scenarios/$requested.ini" ]]; then
        candidate=$(realpath "$MISSION_DIR/scenarios/$requested.ini")
    elif [[ -d "$MISSION_DIR/scenarios/$requested" ]]; then
        scenario_dir="$MISSION_DIR/scenarios/$requested"
        if [[ -f "$scenario_dir/fleet.ini" ]]; then
            candidate=$(realpath "$scenario_dir/fleet.ini")
        else
            local scenario_name
            scenario_name=$(basename "$scenario_dir")
            if [[ -f "$scenario_dir/$scenario_name.ini" ]]; then
                candidate=$(realpath "$scenario_dir/$scenario_name.ini")
            fi
        fi
    fi

    if [[ -z "$candidate" ]]; then
        echo "run_simulator.sh: scenario not found: $requested" >&2
        echo "Expected a scenario name (imazu/03), directory, or INI path." >&2
        return 1
    fi
    printf '%s\n' "$candidate"
}

PROFILE_MIN_SAFE_CPA=$(config_value ship MIN_SAFE_CPA_M)
PROFILE_CAUTION_CPA=$(config_value ship CAUTION_CPA_M)
PROFILE_BOW_CROSSING="$PROFILE_MIN_SAFE_CPA"
PROFILE_CRITICAL_CPA=$(config_value ship CRITICAL_CPA_M)
PROFILE_MANEUVER_DISTANCE=$(config_value ship DEFAULT_MANEUVER_DISTANCE_M)

if [[ -n "$SCENARIO" ]]; then
    FLEET_CONFIG=$(resolve_scenario "$SCENARIO")
    candidate_vhf_manifest="${FLEET_CONFIG%.ini}.vhf.json"
    candidate_obstacles="${FLEET_CONFIG%.ini}.obstacles.moos"
    if [[ "$FLEET_CONFIG" == "$MISSION_DIR/scenarios/vhf/"* ]]; then
        VHF_BENCHMARK="on"
    fi
    if [[ "$VHF_EVENT_MODE" != "off" && -f "$candidate_vhf_manifest" ]]; then
        VHF_MANIFEST="$candidate_vhf_manifest"
        VHF_BENCHMARK="on"
    elif [[ "$VHF_EVENT_MODE" == "on" ]]; then
        echo "run_simulator.sh: VHF event sidecar not found: $candidate_vhf_manifest" >&2
        exit 1
    fi
    if [[ -f "$candidate_obstacles" ]]; then
        SCENARIO_OBSTACLES="$candidate_obstacles"
        MISSION_ARGS+=("--obstacles=$SCENARIO_OBSTACLES")
    fi
    if [[ "$FLEET_CONFIG" == "$MISSION_DIR/scenarios/imazu/"* ]]; then
        NAVIGATION_PROFILE="imazu"
        TIME_WARP=$(config_value imazu TIME_WARP)
        PROFILE_MIN_SAFE_CPA=$(config_value imazu SAFE_PASSING_DISTANCE_M)
        PROFILE_CAUTION_CPA=$(config_value imazu CAUTION_DISTANCE_M)
        PROFILE_BOW_CROSSING=$(config_value imazu BOW_CROSSING_DISTANCE_M)
        PROFILE_CRITICAL_CPA=$(config_value imazu CRITICAL_DISTANCE_M)
        PROFILE_MANEUVER_DISTANCE=$(config_value imazu MANEUVER_DISTANCE_M)
    fi
elif [[ "$FLEET_CONFIG" != /* ]]; then
    FLEET_CONFIG="$MISSION_DIR/$FLEET_CONFIG"
fi
if [[ -n "$TIME_WARP_OVERRIDE" ]]; then
    [[ "$TIME_WARP_OVERRIDE" =~ ^[1-9][0-9]*$ ]] || {
        echo "run_simulator.sh: --time-warp must be a positive integer" >&2
        exit 2
    }
    TIME_WARP=$TIME_WARP_OVERRIDE
fi
if [[ -n "$DECISION_INTERVAL" ]]; then
    [[ "$DECISION_INTERVAL" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] && \
        awk -v value="$DECISION_INTERVAL" 'BEGIN { exit !(value > 0) }' || {
        echo "run_simulator.sh: --decision-interval must be a positive number" >&2
        exit 2
    }
else
    DECISION_INTERVAL=$(config_value runtime poll_seconds)
fi
if [[ ! -f "$FLEET_CONFIG" ]]; then
    echo "run_simulator.sh: scenario configuration not found: $FLEET_CONFIG" >&2
    exit 1
fi

if [[ -z "$TIFF_FILE" ]]; then
    case "$FLEET_CONFIG" in
        */scenarios/imazu/*)
            TIFF_FILE="$MISSION_DIR/scenarios/imazu/ocean_background_4096.tif"
            ;;
        */scenarios/open_waters/*)
            TIFF_FILE="$MISSION_DIR/scenarios/open_waters/denhelder_texel.tif"
            ;;
    esac
elif [[ "$TIFF_FILE" != "none" && "$TIFF_FILE" != /* ]]; then
    if [[ -f "$TIFF_FILE" ]]; then
        TIFF_FILE=$(realpath "$TIFF_FILE")
    else
        TIFF_FILE="$MISSION_DIR/$TIFF_FILE"
    fi
fi

if [[ -n "$TIFF_FILE" && "$TIFF_FILE" != "none" ]]; then
    if [[ "$TIFF_FILE" != *.tif ]]; then
        echo "run_simulator.sh: TIFF background must use the .tif suffix: $TIFF_FILE" >&2
        exit 2
    fi
    TIFF_INFO="${TIFF_FILE%.tif}.info"
    if [[ ! -r "$TIFF_FILE" || ! -r "$TIFF_INFO" ]]; then
        echo "run_simulator.sh: background requires readable files:" >&2
        echo "  $TIFF_FILE" >&2
        echo "  $TIFF_INFO" >&2
        exit 1
    fi
fi

if [[ "$COLREG_RAG_MODE" == "on" && "$COLREG_TREE_MODE" == "on" ]]; then
    echo "Choose --colregs-rag or --colregs-tree, not both" >&2
    exit 2
fi

if [[ "$CHECK_ONLY" == "yes" ]]; then
    if [[ -n "$VHF_MANIFEST" ]]; then
        "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/inject_vhf_scenario.py" \
            --manifest "$VHF_MANIFEST" --inbox /tmp/moos-llm-vhf-check --check
    fi
    echo "Configuration is valid."
    echo "  project:     $CONFIG_FILE"
    echo "  scenario:    $FLEET_CONFIG"
    echo "  background:  ${TIFF_FILE:-mission default}"
    echo "  RAG:         $RAG_MODE"
    echo "  COLREG RAG:  $COLREG_RAG_MODE"
    echo "  COLREG tree: $COLREG_TREE_MODE"
    echo "  Eriksen:     $ERIKSEN_PROMPT_MODE"
    echo "  OOW input:   $OOW_INPUT_FORMAT"
    echo "  reasoning:   $REASONING_MODE"
    echo "  OOW:         $OOW_MODE ($OOW_MODEL)"
    echo "  inference:   $([[ "$OPENAI_API" == "yes" ]] && echo OpenAI API || echo local vLLM)"
    echo "  controller:  $CONTROLLER"
    echo "  nav profile: $NAVIGATION_PROFILE"
    echo "  time warp:   ${TIME_WARP}x"
    echo "  lockstep:    $FREEZE_DURING_DECISION"
    echo "  interval:    ${DECISION_INTERVAL}s simulated"
    echo "  auto-deploy: $AUTO_DEPLOY"
    echo "  VHF event:   ${VHF_MANIFEST:-none}"
    echo "  obstacles:   ${SCENARIO_OBSTACLES:-mission default}"
    exit 0
fi

BRIDGE_SOURCE_NEWER=""
if [[ -x "$BRIDGE_EXECUTABLE" ]]; then
    BRIDGE_SOURCE_NEWER=$(find "$ROOT_DIR/bridge/src" -type f \
        \( -name '*.cpp' -o -name '*.h' \) -newer "$BRIDGE_EXECUTABLE" \
        -print -quit)
fi
if [[ ! -x "$BRIDGE_EXECUTABLE" || -n "$BRIDGE_SOURCE_NEWER" ]]; then
    echo "run_simulator.sh: building current pLLMBridge sources" >&2
    cmake -S "$ROOT_DIR/bridge" -B "$BRIDGE_BUILD_DIR"
    cmake --build "$BRIDGE_BUILD_DIR" --target pLLMBridge --parallel
fi
if [[ ! -x "$MISSION_DIR/launch.sh" ]]; then
    echo "run_simulator.sh: mission launcher not found: $MISSION_DIR/launch.sh" >&2
    exit 1
fi
if [[ "$CONTROLLER" == "llm" && "$OPENAI_API" == "no" ]] && ! command -v uv >/dev/null 2>&1; then
    echo "run_simulator.sh: uv is required to start vLLM" >&2
    exit 1
fi

mkdir -p "$LOGS_DIR"

PROCESS_GROUPS=()
cleanup() {
    trap - EXIT INT TERM
    for process_group in "${PROCESS_GROUPS[@]}"; do
        if [[ "$process_group" =~ ^[1-9][0-9]*$ ]]; then
            kill -TERM -- "-$process_group" 2>/dev/null || true
        fi
    done
    # Do not return control to an evaluation runner while a child can still
    # append to this run's audit or operate the previous mission. Give every
    # managed group a short graceful shutdown window, then force stragglers
    # down and reap the group leaders.
    local deadline=$((SECONDS + 10))
    local any_alive="yes"
    while [[ "$any_alive" == "yes" && $SECONDS -lt $deadline ]]; do
        any_alive="no"
        for process_group in "${PROCESS_GROUPS[@]}"; do
            if [[ "$process_group" =~ ^[1-9][0-9]*$ ]] && \
               kill -0 -- "-$process_group" 2>/dev/null; then
                any_alive="yes"
            fi
        done
        [[ "$any_alive" == "yes" ]] && sleep 0.1
    done
    for process_group in "${PROCESS_GROUPS[@]}"; do
        if [[ "$process_group" =~ ^[1-9][0-9]*$ ]] && \
           kill -0 -- "-$process_group" 2>/dev/null; then
            kill -KILL -- "-$process_group" 2>/dev/null || true
        fi
        wait "$process_group" 2>/dev/null || true
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_group() {
    local name=$1
    local log_file=$2
    shift 2
    echo "Starting $name; log: $log_file"
    setsid "$@" >"$log_file" 2>&1 &
    LAST_PROCESS_GROUP=$!
    PROCESS_GROUPS+=("$LAST_PROCESS_GROUP")
}

wait_for_tcp() {
    local host=$1
    local port=$2
    local label=$3
    local deadline=$((SECONDS + STARTUP_TIMEOUT))

    while ((SECONDS < deadline)); do
        if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
            exec 3>&-
            exec 3<&-
            return 0
        fi
        sleep 0.25
    done

    echo "run_simulator.sh: timed out waiting for $label at $host:$port" >&2
    return 1
}

wait_for_vllm() (
    cd "$ROOT_DIR"
    MOOS_LLM_CONFIG="$CONFIG_FILE" "$ROOT_DIR/.venv/bin/python" -m tools.wait_vllm_ready \
        --model "$OOW_MODEL" --timeout "$VLLM_STARTUP_TIMEOUT" "$@"
)

BRIDGE_HOST=$(config_value hosts bridge)

# The mission launcher already clears stale MOOS processes. Do the equivalent
# for the two services owned by this supervisor so restarting is one command.
# The vLLM match is restricted to the configured model and port.
VLLM_STALE_PATTERN="vllm[[:space:]]+serve[[:space:]]+$MODEL.*--port[=[:space:]]+$VLLM_PORT"
AGENT_STALE_PATTERN="$ROOT_DIR/.venv/bin/python[[:space:]]+$ROOT_DIR/main.py"
if { [[ "$OPENAI_API" == "no" && "$EXTERNAL_VLLM" == "no" ]] && pgrep -f "$VLLM_STALE_PATTERN" >/dev/null 2>&1; } || \
   pgrep -x pLLMBridge >/dev/null 2>&1 || \
   pgrep -f "$AGENT_STALE_PATTERN" >/dev/null 2>&1; then
    echo "Stopping stale simulator services"
    if [[ "$OPENAI_API" == "no" && "$EXTERNAL_VLLM" == "no" ]]; then
        pkill -TERM -f "$VLLM_STALE_PATTERN" 2>/dev/null || true
    fi
    pkill -TERM -x pLLMBridge 2>/dev/null || true
    pkill -TERM -f "$AGENT_STALE_PATTERN" 2>/dev/null || true
    for _ in {1..20}; do
        if { [[ "$OPENAI_API" == "yes" || "$EXTERNAL_VLLM" == "yes" ]] || ! pgrep -f "$VLLM_STALE_PATTERN" >/dev/null 2>&1; } && \
           ! pgrep -x pLLMBridge >/dev/null 2>&1 && \
           ! pgrep -f "$AGENT_STALE_PATTERN" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
    if [[ "$OPENAI_API" == "no" && "$EXTERNAL_VLLM" == "no" ]]; then
        pkill -KILL -f "$VLLM_STALE_PATTERN" 2>/dev/null || true
    fi
    pkill -KILL -x pLLMBridge 2>/dev/null || true
    pkill -KILL -f "$AGENT_STALE_PATTERN" 2>/dev/null || true
fi

VLLM_COMMAND=(
    uv run --locked vllm serve "$MODEL"
    --host "$VLLM_HOST"
    --port "$VLLM_PORT"
    --api-key "$VLLM_API_KEY"
    --max-model-len "$MAX_MODEL_LEN"
    --structured-outputs-config
    '{"backend":"xgrammar","disable_any_whitespace":true}'
)
if [[ "$OOW_MODE" == "finetuned" ]]; then
    VLLM_COMMAND+=(
        --enable-lora
        --max-lora-rank 32
        --lora-modules "$OOW_FINETUNED_MODEL=$OOW_ADAPTER_PATH"
    )
fi
if [[ "$CONTROLLER" == "llm" && "$OPENAI_API" == "no" && "$EXTERNAL_VLLM" == "no" ]]; then
    start_group \
        "vLLM" \
        "$LOGS_DIR/vllm.log" \
        env \
            VLLM_USE_V2_MODEL_RUNNER=0 \
            VLLM_WEIGHT_OFFLOADING_DISABLE_UVA=1 \
        "${VLLM_COMMAND[@]}"
    VLLM_PROCESS_GROUP=$LAST_PROCESS_GROUP
    wait_for_vllm --process-pid "$VLLM_PROCESS_GROUP"
elif [[ "$CONTROLLER" == "llm" && "$OPENAI_API" == "no" ]]; then
    if [[ -n "${MOOS_LLM_VLLM_SUPERVISOR_PID:-}" ]]; then
        wait_for_vllm --process-pid "$MOOS_LLM_VLLM_SUPERVISOR_PID"
    else
        wait_for_vllm
    fi
fi

if [[ "$VLLM_ONLY" == "yes" ]]; then
    [[ "$CONTROLLER" == "llm" && "$EXTERNAL_VLLM" == "no" ]] || {
        echo "run_simulator.sh: --vllm-only requires a managed LLM controller" >&2
        exit 2
    }
    echo "vLLM is ready at $VLLM_HOST:$VLLM_PORT; waiting"
    wait "$VLLM_PROCESS_GROUP"
    exit $?
fi

MISSION_COMMAND=(
    bash -c 'cd "$1" && shift && exec ./launch.sh "$@"' _
    "$MISSION_DIR"
    --xlaunched
    "--config=$FLEET_CONFIG"
    "$TIME_WARP"
)
if [[ "$CONTROLLER" == "moos-ivp" ]]; then
    MISSION_COMMAND+=(--mmod=baseline)
else
    # Never leave MMOD empty: nsplug treats an empty macro as matching the
    # baseline conditional. The explicit llm value removes all MOOS-IvP
    # collision-avoidance behaviors from the rendered helm.
    MISSION_COMMAND+=(--mmod=llm)
fi
if [[ -n "$TIFF_FILE" ]]; then
    MISSION_COMMAND+=("--tiff=$TIFF_FILE")
fi
if [[ -n "$NOGUI" ]]; then
    MISSION_COMMAND+=("$NOGUI")
fi
MISSION_COMMAND+=("${MISSION_ARGS[@]}")
if [[ "$AUTO_DEPLOY" == "yes" ]]; then
    MISSION_COMMAND+=("--start-deployed")
fi
if [[ "$FREEZE_DURING_DECISION" == "on" ]]; then
    MISSION_COMMAND+=("--start-paused")
fi
start_group "MOOS mission" "$LOGS_DIR/mission.log" "${MISSION_COMMAND[@]}"

# launch.sh first removes stale MOOS processes. Give that cleanup phase time
# to finish so readiness checks cannot accidentally connect to an old DB.
sleep 2
wait_for_tcp "$MOOS_HOST" "$OWN_SHIP_MOOS_PORT" "own-ship MOOSDB"

VEHICLE_COUNT=$(wc -l < "$MISSION_DIR/vnames.txt")
VEHICLE_PORTS=""
for ((VEHICLE_INDEX=1; VEHICLE_INDEX<=VEHICLE_COUNT; VEHICLE_INDEX++)); do
    VEHICLE_PORT=$((MOOS_BASE_PORT + VEHICLE_INDEX))
    VEHICLE_PORTS+="${VEHICLE_PORTS:+,}$VEHICLE_PORT"
    wait_for_tcp "$MOOS_HOST" "$VEHICLE_PORT" "vehicle $VEHICLE_INDEX MOOSDB"
done

start_group \
    "pLLMBridge" \
    "$LOGS_DIR/bridge.log" \
    "$BRIDGE_EXECUTABLE" \
        "$MOOS_HOST" \
        "$OWN_SHIP_MOOS_PORT" \
        "$BRIDGE_PORT" \
        "$TIME_WARP" \
        "$VEHICLE_PORTS"
BRIDGE_PROCESS_GROUP=$LAST_PROCESS_GROUP

wait_for_tcp "$BRIDGE_HOST" "$BRIDGE_PORT" "pLLMBridge"

if [[ "$AUTO_DEPLOY" == "yes" ]]; then
    # MOOSDB can accept connections before pHelmIvP has posted its startup
    # flags. Deploying in that gap lets the later DEPLOY=false initialization
    # erase the command, especially at high time warp.
    sleep 3
    for ((VEHICLE_INDEX=1; VEHICLE_INDEX<=VEHICLE_COUNT; VEHICLE_INDEX++)); do
        VEHICLE_PORT=$((MOOS_BASE_PORT + VEHICLE_INDEX))
        wait_for_tcp "$MOOS_HOST" "$VEHICLE_PORT" "vehicle $VEHICLE_INDEX MOOSDB"
        DEPLOYED="no"
        for DEPLOY_ATTEMPT in 1 2 3 4 5; do
            if uPokeDB \
                --host="$MOOS_HOST" \
                --port="$VEHICLE_PORT" \
                --quiet \
                DEPLOY=true \
                MOOS_MANUAL_OVERRIDE=false \
                RETURN=false \
                LLM_STOP=false \
                >>"$LOGS_DIR/deploy.log" 2>&1; then
                DEPLOYED="yes"
                break
            fi
            sleep 1
        done
        if [[ "$DEPLOYED" != "yes" ]]; then
            echo "run_simulator.sh: failed to deploy vehicle on port $VEHICLE_PORT" >&2
            exit 1
        fi
    done
fi

if [[ "$VHF_BENCHMARK" == "on" ]]; then
    VHF_SCENARIO_NAME=$(basename "$(dirname "$FLEET_CONFIG")")
    VHF_RUN_INBOX="$ROOT_DIR/logs/vhf_runtime/${VHF_SCENARIO_NAME}_$$/inbox"
    mkdir -p "$VHF_RUN_INBOX"
fi

if [[ -n "$VHF_MANIFEST" ]]; then
    start_group \
        "VHF scenario injector" \
        "$LOGS_DIR/vhf_injector.log" \
        "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/inject_vhf_scenario.py" \
            --manifest "$VHF_MANIFEST" \
            --inbox "$VHF_RUN_INBOX" \
            --host "$BRIDGE_HOST" \
            --port "$BRIDGE_PORT"
fi

if [[ -z "$AUDIT_LOG_FILE" ]]; then
    AUDIT_LOG_FILE="$LOGS_DIR/agent_audit.jsonl"
elif [[ "$AUDIT_LOG_FILE" != /* ]]; then
    AUDIT_LOG_FILE="$ROOT_DIR/$AUDIT_LOG_FILE"
fi
mkdir -p "$(dirname "$AUDIT_LOG_FILE")"
if [[ "$CONTROLLER" == "llm" ]]; then
    start_group \
        "agents" \
        "$LOGS_DIR/agents.log" \
        env \
        MOOS_LLM_CONFIG="$CONFIG_FILE" \
        MOOS_LLM_RAG="$RAG_MODE" \
        MOOS_LLM_COLREG_RAG="$COLREG_RAG_MODE" \
        MOOS_LLM_COLREG_TREE="$COLREG_TREE_MODE" \
        MOOS_LLM_ERIKSEN_PROMPT="$ERIKSEN_PROMPT_MODE" \
        MOOS_LLM_OOW_INPUT_FORMAT="$OOW_INPUT_FORMAT" \
        MOOS_LLM_REASONING="$REASONING_MODE" \
        MOOS_LLM_OOW_MODE="$OOW_MODE" \
        MOOS_LLM_FINETUNED_PROMPT_PROFILE="$FINETUNED_PROMPT_PROFILE" \
        MOOS_LLM_NAVIGATION_PROFILE="$NAVIGATION_PROFILE" \
        MOOS_LLM_TIME_WARP="$TIME_WARP" \
        MOOS_LLM_FREEZE_DURING_DECISION="$FREEZE_DURING_DECISION" \
        MOOS_LLM_DECISION_INTERVAL_S="$DECISION_INTERVAL" \
        MOOS_LLM_MOOS_HOST="$MOOS_HOST" \
        MOOS_LLM_VEHICLE_PORTS="$VEHICLE_PORTS" \
        MOOS_LLM_MIN_SAFE_CPA_M="$PROFILE_MIN_SAFE_CPA" \
        MOOS_LLM_CAUTION_CPA_M="$PROFILE_CAUTION_CPA" \
        MOOS_LLM_BOW_CROSSING_M="$PROFILE_BOW_CROSSING" \
        MOOS_LLM_CRITICAL_CPA_M="$PROFILE_CRITICAL_CPA" \
        MOOS_LLM_MANEUVER_DISTANCE_M="$PROFILE_MANEUVER_DISTANCE" \
        MOOS_LLM_OOW_MODEL="$OOW_MODEL" \
        MOOS_LLM_OPENAI_API="$OPENAI_API" \
        MOOS_LLM_OPENAI_PROMPT_PROFILE="$OPENAI_PROMPT_PROFILE" \
        MOOS_LLM_VHF_BENCHMARK="$VHF_BENCHMARK" \
        MOOS_LLM_VHF_INPUT_DIR="${VHF_RUN_INBOX:-$(config_value vhf input_dir)}" \
        MOOS_LLM_AUDIT_LOG="$AUDIT_LOG_FILE" \
        HF_HUB_OFFLINE=1 \
        TRANSFORMERS_OFFLINE=1 \
        "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/main.py"
    AGENT_PROCESS_GROUP=$LAST_PROCESS_GROUP
else
    start_group \
        "baseline telemetry" \
        "$LOGS_DIR/agents.log" \
        "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/record_bridge_run.py" \
        --host "$BRIDGE_HOST" --port "$BRIDGE_PORT" --output "$AUDIT_LOG_FILE"
    AGENT_PROCESS_GROUP=$LAST_PROCESS_GROUP
fi

echo "Simulator is running. Press Ctrl-C to stop it."
echo "  model:       $MODEL"
echo "  scenario:    $FLEET_CONFIG"
echo "  background:  ${TIFF_FILE:-mission default}"
echo "  vLLM:        http://$VLLM_HOST:$VLLM_PORT"
echo "  own-ship DB: $MOOS_HOST:$OWN_SHIP_MOOS_PORT"
echo "  bridge:      $BRIDGE_HOST:$BRIDGE_PORT"
echo "  RAG:         $RAG_MODE"
echo "  COLREG RAG:  $COLREG_RAG_MODE"
echo "  COLREG tree: $COLREG_TREE_MODE"
echo "  Eriksen:     $ERIKSEN_PROMPT_MODE"
echo "  OOW input:   $OOW_INPUT_FORMAT"
echo "  reasoning:   $REASONING_MODE"
echo "  OOW:         $OOW_MODE ($OOW_MODEL)"
echo "  controller:  $CONTROLLER"
echo "  nav profile: $NAVIGATION_PROFILE"
echo "  time warp:   ${TIME_WARP}x"
echo "  lockstep:    $FREEZE_DURING_DECISION"
echo "  interval:    ${DECISION_INTERVAL}s simulated"
echo "  audit:       $AUDIT_LOG_FILE"
echo "  auto-deploy: $AUTO_DEPLOY"
echo "  VHF event:   ${VHF_MANIFEST:-none}"

set +e
if [[ "$CONTROLLER" == "llm" ]]; then
    if [[ "$OPENAI_API" == "yes" || "$EXTERNAL_VLLM" == "yes" ]]; then
        wait -n "$BRIDGE_PROCESS_GROUP" "$AGENT_PROCESS_GROUP"
    else
        wait -n "$VLLM_PROCESS_GROUP" "$BRIDGE_PROCESS_GROUP" "$AGENT_PROCESS_GROUP"
    fi
else
    wait "$AGENT_PROCESS_GROUP"
fi
status=$?
set -e
echo "run_simulator.sh: a supervised process exited with status $status" >&2
exit "$status"
