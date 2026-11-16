#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RUNS=10
START_SEED=1
MIN_CONTACTS=1
MAX_CONTACTS=50
TIMEOUT_SECONDS=1200
MAX_CYCLES=120
SCALE_M=6000
TIME_WARP=""
RAG_FLAG="--no-rag"
OOW_MODE="base"
OPENAI_MODEL=""
OPENAI_PROMPT_PROFILE="teacher"
SCENARIO_PROFILE="stress"
GUI_FLAG="--nogui"
MINIMUM_RANGE_M=500
CAMPAIGN_DIR_OVERRIDE=""
RESUME=0
INTERRUPTED_SIGNAL=""
CAMPAIGN_LOCK_FILE="${TMPDIR:-/tmp}/moos-llm-campaign.lock"

on_interrupt() {
    INTERRUPTED_SIGNAL=$1
}

# A scenario failure should not abort the campaign, but an operator interrupt
# must.  Bash defers this trap while it waits for the foreground `timeout`
# command, then the loop exits after the simulator has handled the same signal
# and cleaned up its process groups.
trap 'on_interrupt INT' INT
trap 'on_interrupt TERM' TERM

usage() {
    cat <<'EOF'
Usage: ./run_random_campaign.sh [OPTIONS]

Options:
  --runs N           Number of scenarios (default: 10)
  --seed N           First seed; increments each run (default: 1)
  --min-contacts N   Minimum targets, 1-50 (default: 1)
  --max-contacts N   Maximum targets, 1-50 (default: 50)
  --timeout N        Maximum seconds per scenario (default: 1200)
  --max-cycles N     Maximum model decisions per scenario (default: 120)
  --scale-m N        Scenario disk radius in metres (default: 6000)
  --profile NAME     Scenario profile: stress, classic, curriculum, or targeted (default: stress)
  --time-warp N      Simulation time acceleration
  --openai-model ID  Use the OpenAI API as full-prompt teacher
  --openai-prompt-profile PROFILE
                     OpenAI prompt: teacher (default) or runtime-base
  --minimum-range-m N
                     Post-run SFT acceptance gate in metres (default: 500)
  --campaign-dir DIR Stable output directory, required for resumable campaigns
  --resume            Resume a matching --campaign-dir without repeating seeds
  --gui              Open pMarineViewer for each scenario
  --nogui            Run without pMarineViewer (default)
  --rag              Enable MAIB RAG
  --no-rag           Disable MAIB RAG (default)
  --oow MODE         OOW model: base or finetuned (default: base)
  --help, -h         Show this help
EOF
}

while (($#)); do
    case "$1" in
        --runs) RUNS=$2; shift 2 ;;
        --seed) START_SEED=$2; shift 2 ;;
        --min-contacts) MIN_CONTACTS=$2; shift 2 ;;
        --max-contacts) MAX_CONTACTS=$2; shift 2 ;;
        --timeout) TIMEOUT_SECONDS=$2; shift 2 ;;
        --max-cycles) MAX_CYCLES=$2; shift 2 ;;
        --scale-m) SCALE_M=$2; shift 2 ;;
        --profile) SCENARIO_PROFILE=$2; shift 2 ;;
        --time-warp) TIME_WARP=$2; shift 2 ;;
        --openai-model) OPENAI_MODEL=$2; shift 2 ;;
        --openai-prompt-profile)
            [[ "${2:-}" == "teacher" || "${2:-}" == "runtime-base" ]] || {
                echo "--openai-prompt-profile requires teacher or runtime-base" >&2; exit 2;
            }
            OPENAI_PROMPT_PROFILE=$2; shift 2 ;;
        --minimum-range-m) MINIMUM_RANGE_M=$2; shift 2 ;;
        --campaign-dir) CAMPAIGN_DIR_OVERRIDE=$2; shift 2 ;;
        --resume) RESUME=1; shift ;;
        --gui) GUI_FLAG=""; shift ;;
        --nogui) GUI_FLAG="--nogui"; shift ;;
        --rag) RAG_FLAG="--rag"; shift ;;
        --no-rag) RAG_FLAG="--no-rag"; shift ;;
        --oow)
            [[ "${2:-}" == "base" || "${2:-}" == "finetuned" ]] || {
                echo "--oow requires base or finetuned" >&2; exit 2;
            }
            OOW_MODE=$2; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "run_random_campaign.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

for value in "$RUNS" "$START_SEED" "$MIN_CONTACTS" "$MAX_CONTACTS" "$TIMEOUT_SECONDS" "$MAX_CYCLES"; do
    [[ "$value" =~ ^[0-9]+$ ]] || { echo "Numeric options require integers" >&2; exit 2; }
done
if [[ -n "$TIME_WARP" && ! "$TIME_WARP" =~ ^[1-9][0-9]*$ ]]; then
    echo "time-warp must be a positive integer" >&2
    exit 2
fi
((RUNS > 0 && TIMEOUT_SECONDS > 0)) || { echo "runs and timeout must be positive" >&2; exit 2; }
[[ "$SCALE_M" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "scale-m must be positive" >&2; exit 2; }
[[ "$SCENARIO_PROFILE" == "stress" || "$SCENARIO_PROFILE" == "classic" || "$SCENARIO_PROFILE" == "curriculum" || "$SCENARIO_PROFILE" == "targeted" ]] || {
    echo "--profile requires stress, classic, curriculum, or targeted" >&2; exit 2;
}
awk "BEGIN { exit !($SCALE_M > 0) }" || { echo "scale-m must be positive" >&2; exit 2; }
[[ "$MINIMUM_RANGE_M" =~ ^[0-9]+([.][0-9]+)?$ ]] || {
    echo "minimum-range-m must be a non-negative number" >&2; exit 2;
}
awk "BEGIN { exit !($MINIMUM_RANGE_M >= 0) }" || {
    echo "minimum-range-m must be non-negative" >&2; exit 2;
}
if ((RESUME)) && [[ -z "$CAMPAIGN_DIR_OVERRIDE" ]]; then
    echo "--resume requires --campaign-dir" >&2
    exit 2
fi
if [[ -n "$OPENAI_MODEL" && -z "${OPENAI_API_KEY:-}" ]]; then
    echo "OPENAI_API_KEY is required with --openai-model" >&2
    exit 2
fi
((MIN_CONTACTS >= 1 && MIN_CONTACTS <= MAX_CONTACTS && MAX_CONTACTS <= 50)) || {
    echo "require 1 <= min-contacts <= max-contacts <= 50" >&2
    exit 2
}

exec 9>"$CAMPAIGN_LOCK_FILE"
if ! flock -n 9; then
    echo "run_random_campaign.sh: another MOOS campaign is already running" >&2
    echo "Stop it before starting a campaign with a different time warp." >&2
    exit 1
fi

if [[ -n "$CAMPAIGN_DIR_OVERRIDE" ]]; then
    if [[ "$CAMPAIGN_DIR_OVERRIDE" == /* ]]; then
        CAMPAIGN_DIR=$CAMPAIGN_DIR_OVERRIDE
    else
        CAMPAIGN_DIR="$ROOT_DIR/$CAMPAIGN_DIR_OVERRIDE"
    fi
    CAMPAIGN_ID=$(basename -- "$CAMPAIGN_DIR")
else
    CAMPAIGN_ID=$(date -u +%Y%m%dT%H%M%SZ)_seed${START_SEED}
    CAMPAIGN_DIR="$ROOT_DIR/logs/campaigns/$CAMPAIGN_ID"
fi
SCENARIO_DIR="$ROOT_DIR/mission/scenarios/random/generated/$CAMPAIGN_ID"
mkdir -p "$CAMPAIGN_DIR" "$SCENARIO_DIR"
if [[ -f "$CAMPAIGN_DIR/campaign.json" ]]; then
    ((RESUME)) || {
        echo "Campaign already exists; use --resume with the same configuration: $CAMPAIGN_DIR" >&2
        exit 2
    }
    jq -e \
        --argjson runs "$RUNS" --argjson start "$START_SEED" \
        --argjson min_contacts "$MIN_CONTACTS" --argjson max_contacts "$MAX_CONTACTS" \
        --argjson scale "$SCALE_M" --arg profile "$SCENARIO_PROFILE" \
        --argjson cycles "$MAX_CYCLES" --arg warp "$TIME_WARP" \
        --arg rag "$RAG_FLAG" --arg oow "$OOW_MODE" --arg model "$OPENAI_MODEL" \
        --arg prompt "$OPENAI_PROMPT_PROFILE" --argjson clearance "$MINIMUM_RANGE_M" \
        '.runs == $runs and .start_seed == $start and
         .min_contacts == $min_contacts and .max_contacts == $max_contacts and
         .scale_m == $scale and .profile == $profile and .max_cycles == $cycles and
         .time_warp == $warp and .rag == $rag and .oow == $oow and
         .openai_model == $model and .openai_prompt_profile == $prompt and
         .minimum_range_m == $clearance' \
        "$CAMPAIGN_DIR/campaign.json" >/dev/null || {
            echo "Refusing to resume: campaign configuration does not match $CAMPAIGN_DIR/campaign.json" >&2
            exit 2
        }
else
    printf '{"campaign_id":"%s","runs":%d,"start_seed":%d,"min_contacts":%d,"max_contacts":%d,"scale_m":%s,"profile":"%s","max_cycles":%d,"time_warp":"%s","rag":"%s","oow":"%s","openai_model":"%s","openai_prompt_profile":"%s","minimum_range_m":%s}\n' \
        "$CAMPAIGN_ID" "$RUNS" "$START_SEED" "$MIN_CONTACTS" "$MAX_CONTACTS" "$SCALE_M" "$SCENARIO_PROFILE" "$MAX_CYCLES" "$TIME_WARP" "$RAG_FLAG" "$OOW_MODE" "$OPENAI_MODEL" "$OPENAI_PROMPT_PROFILE" "$MINIMUM_RANGE_M" \
        >"$CAMPAIGN_DIR/campaign.json"
fi

for ((index=0; index<RUNS; index++)); do
    seed=$((START_SEED + index))
    scenario="$SCENARIO_DIR/seed_${seed}.ini"
    audit="$CAMPAIGN_DIR/seed_${seed}.jsonl"
    if [[ -f "$CAMPAIGN_DIR/results.jsonl" ]] && \
       jq -e --argjson seed "$seed" 'select(.seed == $seed)' \
           "$CAMPAIGN_DIR/results.jsonl" >/dev/null; then
        [[ -f "$audit" ]] || {
            echo "Seed $seed is recorded as finished but its audit is missing: $audit" >&2
            exit 2
        }
        echo "[$((index + 1))/$RUNS] Seed $seed already finished; skipping"
        continue
    fi
    if [[ -f "$audit" ]]; then
        interrupted="$audit.interrupted.$(date -u +%Y%m%dT%H%M%SZ)"
        mv -- "$audit" "$interrupted"
        echo "Preserved incomplete audit as $interrupted"
    fi
    echo "[$((index + 1))/$RUNS] Generating seed $seed"
    "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/generate_random_scenario.py" \
        --seed "$seed" --min-contacts "$MIN_CONTACTS" \
        --max-contacts "$MAX_CONTACTS" --scale-m "$SCALE_M" \
        --profile "$SCENARIO_PROFILE" --output "$scenario"
    cp "${scenario%.ini}.json" "$CAMPAIGN_DIR/seed_${seed}.scenario.json"

    echo "[$((index + 1))/$RUNS] Running seed $seed"
    set +e
    simulator_args=(--deploy "$RAG_FLAG" --oow "$OOW_MODE" --scenario "$scenario")
    if [[ -n "$GUI_FLAG" ]]; then
        simulator_args+=("$GUI_FLAG")
    fi
    if [[ -n "$TIME_WARP" ]]; then
        simulator_args+=(--time-warp "$TIME_WARP")
    fi
    if [[ -n "$OPENAI_MODEL" ]]; then
        simulator_args+=(--openai-api "$OPENAI_MODEL" --openai-prompt-profile "$OPENAI_PROMPT_PROFILE")
    fi
    timeout --signal=TERM --kill-after=20 "$TIMEOUT_SECONDS" \
        env MOOS_LLM_EXIT_ON_COMPLETE=1 MOOS_LLM_MAX_CYCLES="$MAX_CYCLES" "$ROOT_DIR/run_simulator.sh" \
        "${simulator_args[@]}" \
        --tiff "$ROOT_DIR/mission/scenarios/imazu/ocean_background_4096.tif" \
        --audit-log "$audit"
    status=$?
    set -e
    if [[ -n "$INTERRUPTED_SIGNAL" || "$status" == 130 ]]; then
        echo "Campaign interrupted; stopping after seed $seed" >&2
        if [[ "$INTERRUPTED_SIGNAL" == "TERM" ]]; then
            exit 143
        fi
        exit 130
    fi
    run_logs="$CAMPAIGN_DIR/seed_${seed}.runtime"
    mkdir -p "$run_logs"
    for runtime_log in agents bridge mission vllm deploy; do
        source_log="$ROOT_DIR/logs/${runtime_log}.log"
        [[ -f "$source_log" ]] && cp "$source_log" "$run_logs/${runtime_log}.log"
    done
    if grep -q '"event": "mission_failed"' "$audit" 2>/dev/null; then result="collision"
    elif grep -q '"event": "mission_complete"' "$audit" 2>/dev/null; then result="complete"
    elif ((status == 0)); then result="exited"
    elif ((status == 124)); then result="timeout"
    else result="failed"
    fi
    printf '{"seed":%d,"result":"%s","exit_status":%d,"audit":"seed_%d.jsonl"}\n' \
        "$seed" "$result" "$status" "$seed" >>"$CAMPAIGN_DIR/results.jsonl"
    echo "[$((index + 1))/$RUNS] Seed $seed: $result (status $status)"
    if [[ -n "$OPENAI_MODEL" && "$status" -ne 0 ]] && \
       ! grep -Eq '"event": "mission_(complete|failed)"' "$audit" 2>/dev/null; then
        echo "Paid teacher campaign aborted after infrastructure/API failure on seed $seed" >&2
        echo "Inspect $run_logs/agents.log before resuming or starting a new campaign" >&2
        exit "$status"
    fi
done

audits=("$CAMPAIGN_DIR"/seed_*.jsonl)
"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/extract_oow_pairs.py" \
    "${audits[@]}" --output "$CAMPAIGN_DIR/teacher_pairs.jsonl"
"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/extract_oow_pairs.py" \
    "${audits[@]}" --validated-only --chat-format \
    --output "$CAMPAIGN_DIR/sft_validated.jsonl"
if [[ "$SCENARIO_PROFILE" == "targeted" ]]; then
    "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/extract_oow_pairs.py" \
        "${audits[@]}" --validated-only --chat-format --rare-stop-or-port \
        --output "$CAMPAIGN_DIR/sft_rare_stop_port.jsonl"
fi
"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/tools/build_teacher_dataset.py" \
    "$CAMPAIGN_DIR" --minimum-range-m "$MINIMUM_RANGE_M" \
    --output "$CAMPAIGN_DIR/sft_quality_filtered.jsonl"
"$ROOT_DIR/.venv/bin/python" -m tools.audit_teacher_campaign \
    "$CAMPAIGN_DIR" --minimum-range-m "$MINIMUM_RANGE_M" \
    --require-complete --minimum-accepted-missions 1 \
    --output "$CAMPAIGN_DIR/quality_report.json"

echo "Campaign finished: $CAMPAIGN_DIR"
