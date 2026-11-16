#!/usr/bin/env python3
"""Repeated-inference consistency benchmark for the base and reasoning OOW.

The benchmark freezes exact recorded base-narrative prompts, repeats inference
without advancing the simulator, and measures whether the selected executable
control changes for identical evidence.  It deliberately reports consistency,
not correctness: a consistently wrong action is still consistent.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from agents.agent_oow import _canonicalize_unused_decision_fields
from agents.schemas.captain import CaptainDecision
from tools.config_value import get


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDS = ROOT / "reports/imazu_evaluation_dossier/prompt_records.jsonl"
DEFAULT_FIXTURES = ROOT / "benchmarks/oow_action_consistency/fixtures.jsonl"
DEFAULT_VARIANT = "Base narrative (combined reruns)"
ACTIONS = [
    "maintain_course", "alter_course", "rejoin_route", "set_speed",
    "stop", "resume", "skip",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as destination:
        destination.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def event_decisions(path: Path) -> dict[int, dict[str, Any]]:
    decisions: dict[int, dict[str, Any]] = {}
    if not path.is_file():
        return decisions
    for event in read_jsonl(path):
        if event.get("event") != "oow_decision":
            continue
        decision = event.get("decision")
        if isinstance(decision, dict) and isinstance(event.get("cycle_id"), int):
            decisions[int(event["cycle_id"])] = decision
    return decisions


def allowed_actions(user_prompt: str) -> list[str]:
    try:
        payload = json.loads(user_prompt)
    except json.JSONDecodeError:
        return ACTIONS
    actions = payload.get("allowed_actions") if isinstance(payload, dict) else None
    return [str(item) for item in actions] if isinstance(actions, list) else ACTIONS


def normalize_thinking_prompt(system_prompt: str, reasoning: bool) -> str:
    prompt = system_prompt.replace("/no_think", "").replace("/think", "").rstrip()
    return f"{prompt}\n\n{'/think' if reasoning else '/no_think'}"


def response_format(actions: list[str]) -> dict[str, Any]:
    schema = CaptainDecision.model_json_schema()
    schema["properties"]["action"]["enum"] = actions
    return {
        "type": "json_schema",
        "json_schema": {"name": "oow_decision", "strict": True, "schema": schema},
    }


def control_signature(decision: dict[str, Any]) -> str:
    action = str(decision["action"])
    if action == "alter_course":
        degrees = float(decision["degrees"])
        direction = "starboard" if degrees > 0 else "port"
        return f"alter_course:{direction}:{abs(degrees):g}"
    if action == "set_speed":
        return f"set_speed:{float(decision['speed']):.2f}"
    return action


def direction(decision: dict[str, Any]) -> str:
    if decision.get("action") != "alter_course":
        return "none"
    return "starboard" if float(decision.get("degrees", 0)) > 0 else "port"


def cited_rules(reason: Any) -> list[int]:
    if not isinstance(reason, str):
        return []
    found: set[int] = set()
    for match in re.finditer(r"\bRules?\s+((?:\d{1,2}(?:\s*,\s*|\s+and\s+)?)+)", reason):
        found.update(int(value) for value in re.findall(r"\d{1,2}", match.group(1)))
    return sorted(found)


def command_select(args: argparse.Namespace) -> None:
    records = [
        row for row in read_jsonl(args.records)
        if row.get("variant") == args.variant
        and row.get("effective_user_prompt_provenance") == "recorded_exact"
    ]
    decision_cache: dict[tuple[str, int], dict[int, dict[str, Any]]] = {}
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        run = str(row["source_run"])
        case = int(row["case"])
        key = (run, case)
        if key not in decision_cache:
            audit = ROOT / "logs/evaluations/imazu" / run / f"case_{case:02d}" / "audit.jsonl"
            decision_cache[key] = event_decisions(audit)
        decision = decision_cache[key].get(int(row["cycle_id"]))
        if not decision or decision.get("action") not in ACTIONS:
            continue
        system_path = (
            ROOT / "reports/imazu_evaluation_dossier/system_prompts"
            / f"{row['system_prompt_sha256']}.txt"
        )
        if not system_path.is_file():
            continue
        user_prompt = str(row["effective_user_prompt"])
        fixture = {
            "source_run": run,
            "case": case,
            "cycle_id": int(row["cycle_id"]),
            "system_prompt": system_path.read_text(encoding="utf-8"),
            "user_prompt": user_prompt,
            "system_prompt_sha256": str(row["system_prompt_sha256"]),
            "user_prompt_sha256": sha256_text(user_prompt),
            "reference_decision": decision,
            "reference_action": str(decision["action"]),
        }
        fixture["fixture_id"] = (
            f"{run}-case{case:02d}-cycle{int(row['cycle_id']):04d}-"
            f"{fixture['user_prompt_sha256'][:10]}"
        )
        candidates[str(decision["action"])].append(fixture)

    rng = random.Random(args.seed)
    for values in candidates.values():
        rng.shuffle(values)
    # Deliberately cover maneuver states while retaining random selection within
    # each stratum.  The fallback fills unavailable rare-action slots randomly.
    requested = {
        "maintain_course": 3,
        "alter_course": 4,
        "rejoin_route": 2,
        "set_speed": 1,
    }
    selected: list[dict[str, Any]] = []
    for action, count in requested.items():
        selected.extend(candidates[action][:count])
    remaining = [
        row for values in candidates.values() for row in values if row not in selected
    ]
    rng.shuffle(remaining)
    selected.extend(remaining[: max(0, args.count - len(selected))])
    selected = selected[: args.count]
    if len(selected) < args.count:
        raise SystemExit(f"only {len(selected)} eligible exact prompt/decision pairs found")
    rng.shuffle(selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as destination:
        for row in selected:
            destination.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    counts = Counter(row["reference_action"] for row in selected)
    manifest = {
        "fixtures": len(selected),
        "seed": args.seed,
        "variant": args.variant,
        "reference_actions": dict(sorted(counts.items())),
        "fixtures_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


def infer_once(
    client: OpenAI,
    fixture: dict[str, Any],
    mode: str,
    model: str,
    temperature: float,
    seed: int,
    max_reasoning_tokens: int,
    max_final_tokens: int,
) -> dict[str, Any]:
    reasoning = mode == "reasoning"
    system = normalize_thinking_prompt(str(fixture["system_prompt"]), reasoning)
    user = str(fixture["user_prompt"])
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    reasoning_trace = None
    reasoning_complete = None
    reasoning_finish_reason = None
    deliberation_usage = None
    started = time.monotonic()
    if reasoning:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            seed=seed,
            max_tokens=max_reasoning_tokens,
        )
        message = response.choices[0].message
        reasoning_trace = message.content or ""
        separate = getattr(message, "reasoning_content", None)
        if separate and "<think>" not in reasoning_trace:
            reasoning_trace = f"<think>{separate}</think>\n{reasoning_trace}"
        reasoning_complete = "</think>" in reasoning_trace
        reasoning_finish_reason = response.choices[0].finish_reason
        usage = getattr(response, "usage", None)
        deliberation_usage = usage.model_dump() if hasattr(usage, "model_dump") else None
        closed_trace = reasoning_trace if reasoning_complete else f"{reasoning_trace.rstrip()}\n</think>"
        messages = [
            {"role": "system", "content": normalize_thinking_prompt(str(fixture["system_prompt"]), False)},
            {"role": "user", "content": user},
            {"role": "assistant", "content": closed_trace},
            {"role": "user", "content": "Using the completed analysis above, return only the final JSON decision matching the required schema. Do not deliberate again."},
        ]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        seed=seed + 1_000_000,
        max_tokens=max_final_tokens,
        response_format=response_format(allowed_actions(user)),
    )
    content = response.choices[0].message.content or ""
    elapsed = time.monotonic() - started
    usage = getattr(response, "usage", None)
    result: dict[str, Any] = {
        "valid": False,
        "raw_response": content,
        "latency_s": elapsed,
        "reasoning_complete": reasoning_complete,
        "reasoning_finish_reason": reasoning_finish_reason,
        "reasoning_characters": len(reasoning_trace or ""),
        "deliberation_usage": deliberation_usage,
        "finalization_usage": usage.model_dump() if hasattr(usage, "model_dump") else None,
    }
    try:
        payload = _canonicalize_unused_decision_fields(json.loads(content))
        decision = CaptainDecision.model_validate(payload).model_dump(mode="json")
        result.update({
            "valid": True,
            "decision": decision,
            "action": decision["action"],
            "direction": direction(decision),
            "control_signature": control_signature(decision),
            "cited_rules": cited_rules(decision.get("reason")),
        })
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def command_run(args: argparse.Namespace) -> None:
    fixtures = read_jsonl(args.fixtures)
    if not fixtures:
        raise SystemExit("fixture file is empty")
    output = args.output / "predictions.jsonl"
    existing = read_jsonl(output) if output.is_file() else []
    if existing and not args.resume:
        raise SystemExit(f"{output} exists; use --resume or choose another output")
    done = {(r["fixture_id"], r["mode"], int(r["repetition"])) for r in existing}
    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout)
    total = len(fixtures) * len(args.modes) * args.repetitions
    completed = len(done)
    for fixture in fixtures:
        for mode in args.modes:
            for repetition in range(args.repetitions):
                key = (fixture["fixture_id"], mode, repetition)
                if key in done:
                    continue
                run_seed = args.seed + repetition + 10_000 * fixtures.index(fixture) + (500_000 if mode == "reasoning" else 0)
                row = {
                    "fixture_id": fixture["fixture_id"],
                    "source_run": fixture["source_run"],
                    "case": fixture["case"],
                    "cycle_id": fixture["cycle_id"],
                    "reference_action": fixture["reference_action"],
                    "mode": mode,
                    "repetition": repetition,
                    "seed": run_seed,
                    "temperature": args.temperature,
                    "model": args.model,
                }
                try:
                    row.update(infer_once(
                        client, fixture, mode, args.model, args.temperature, run_seed,
                        args.max_reasoning_tokens, args.max_final_tokens,
                    ))
                except Exception as exc:  # preserve every failed trial
                    row.update({"valid": False, "error": f"{type(exc).__name__}: {exc}"})
                append_jsonl(output, row)
                completed += 1
                print(
                    f"[{completed}/{total}] {fixture['fixture_id']} {mode} #{repetition + 1}: "
                    f"{row.get('control_signature', row.get('error', 'invalid'))}",
                    flush=True,
                )
    write_report(args.output, read_jsonl(output), fixtures)


def entropy(counts: Counter[str], allowed_count: int = len(ACTIONS)) -> float:
    total = sum(counts.values())
    if not total:
        return 0.0
    raw = -sum((n / total) * math.log(n / total) for n in counts.values() if n)
    return raw / math.log(allowed_count)


def pairwise_disagreement(counts: Counter[str]) -> float:
    total = sum(counts.values())
    return 1.0 - sum((count / total) ** 2 for count in counts.values()) if total else 0.0


def write_report(output_dir: Path, rows: list[dict[str, Any]], fixtures: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["fixture_id"]), str(row["mode"]))].append(row)
    per_state: list[dict[str, Any]] = []
    fixture_map = {row["fixture_id"]: row for row in fixtures}
    for (fixture_id, mode), trials in sorted(grouped.items()):
        valid = [row for row in trials if row.get("valid")]
        actions = Counter(str(row["action"]) for row in valid)
        controls = Counter(str(row["control_signature"]) for row in valid)
        directions = Counter(str(row["direction"]) for row in valid if row["action"] == "alter_course")
        n = len(trials)
        modal_action, modal_count = actions.most_common(1)[0] if actions else ("<invalid>", 0)
        modal_control, modal_control_count = controls.most_common(1)[0] if controls else ("<invalid>", 0)
        per_state.append({
            "fixture_id": fixture_id,
            "case": fixture_map[fixture_id]["case"],
            "cycle_id": fixture_map[fixture_id]["cycle_id"],
            "reference_action": fixture_map[fixture_id]["reference_action"],
            "mode": mode,
            "trials": n,
            "valid": len(valid),
            "invalid_rate": 1 - len(valid) / n,
            "modal_action": modal_action,
            "modal_action_share": modal_count / len(valid) if valid else 0.0,
            "action_entropy_normalized": entropy(actions),
            "action_pairwise_disagreement": pairwise_disagreement(actions),
            "modal_control": modal_control,
            "modal_control_share": modal_control_count / len(valid) if valid else 0.0,
            "port_count": directions["port"],
            "starboard_count": directions["starboard"],
            "action_distribution": dict(sorted(actions.items())),
            "control_distribution": dict(sorted(controls.items())),
            "mean_latency_s": statistics.mean(float(row.get("latency_s", 0)) for row in trials),
            "reasoning_truncation_rate": (
                sum(row.get("reasoning_finish_reason") == "length" for row in trials) / n
                if mode == "reasoning" else None
            ),
        })
    modes = sorted({row["mode"] for row in per_state})
    aggregate: dict[str, Any] = {}
    for mode in modes:
        subset = [row for row in per_state if row["mode"] == mode]
        aggregate[mode] = {
            "states": len(subset),
            "trials": sum(row["trials"] for row in subset),
            "macro_modal_action_share": statistics.mean(row["modal_action_share"] for row in subset),
            "macro_modal_control_share": statistics.mean(row["modal_control_share"] for row in subset),
            "macro_action_entropy_normalized": statistics.mean(row["action_entropy_normalized"] for row in subset),
            "macro_action_pairwise_disagreement": statistics.mean(row["action_pairwise_disagreement"] for row in subset),
            "invalid_rate": sum(row["trials"] - row["valid"] for row in subset) / sum(row["trials"] for row in subset),
            "mean_latency_s": statistics.mean(row["mean_latency_s"] for row in subset),
            "reasoning_truncation_rate": (
                statistics.mean(row["reasoning_truncation_rate"] for row in subset)
                if mode == "reasoning" else None
            ),
        }
    paired = []
    for fixture_id in fixture_map:
        values = {row["mode"]: row for row in per_state if row["fixture_id"] == fixture_id}
        if "base" in values and "reasoning" in values:
            paired.append({
                "fixture_id": fixture_id,
                "base_modal_action": values["base"]["modal_action"],
                "reasoning_modal_action": values["reasoning"]["modal_action"],
                "modal_action_changed": values["base"]["modal_action"] != values["reasoning"]["modal_action"],
            })
    summary = {
        "fixtures": len(fixtures),
        "aggregate": aggregate,
        "cross_mode_modal_action_changes": sum(row["modal_action_changed"] for row in paired),
        "cross_mode_states_compared": len(paired),
        "per_state": per_state,
        "paired": paired,
        "interpretation": "Consistency metrics do not establish action correctness or navigation safety.",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "per_state.csv").open("w", encoding="utf-8", newline="") as destination:
        fields = [key for key in per_state[0] if key not in {"action_distribution", "control_distribution"}]
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in per_state)
    lines = [
        "# OOW repeated-inference action-consistency benchmark", "",
        f"Fixed states: {len(fixtures)}. Trials: {len(rows)}.", "",
        "Consistency is not correctness: this benchmark measures sensitivity to repeated inference on identical evidence.", "",
        "## Aggregate", "",
        "| Mode | Trials | Modal action agreement | Modal full-control agreement | Normalized action entropy | Pairwise action disagreement | Invalid | Mean latency | Reasoning truncated |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in modes:
        item = aggregate[mode]
        truncation = "--" if item["reasoning_truncation_rate"] is None else f"{100*item['reasoning_truncation_rate']:.1f}%"
        lines.append(
            f"| {mode} | {item['trials']} | {100*item['macro_modal_action_share']:.1f}% | "
            f"{100*item['macro_modal_control_share']:.1f}% | {item['macro_action_entropy_normalized']:.3f} | "
            f"{100*item['macro_action_pairwise_disagreement']:.1f}% | {100*item['invalid_rate']:.1f}% | "
            f"{item['mean_latency_s']:.2f}s | {truncation} |"
        )
    lines += ["", "## Per fixed state", "", "| State | Mode | Reference | Modal action | Agreement | Entropy | Port / starboard | Distribution |", "|---|---|---|---|---:|---:|---:|---|"]
    for row in per_state:
        distribution = ", ".join(f"{key}:{value}" for key, value in row["action_distribution"].items())
        lines.append(
            f"| {row['fixture_id']} | {row['mode']} | {row['reference_action']} | {row['modal_action']} | "
            f"{100*row['modal_action_share']:.1f}% | {row['action_entropy_normalized']:.3f} | "
            f"{row['port_count']} / {row['starboard_count']} | {distribution} |"
        )
    lines += ["", f"The base and reasoning modal actions differed on **{summary['cross_mode_modal_action_changes']}/{summary['cross_mode_states_compared']}** fixed states.", ""]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"aggregate": aggregate, "cross_mode_modal_action_changes": summary["cross_mode_modal_action_changes"]}, indent=2))


def command_report(args: argparse.Namespace) -> None:
    write_report(args.output, read_jsonl(args.output / "predictions.jsonl"), read_jsonl(args.fixtures))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select", help="freeze a stratified random prompt sample")
    select.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    select.add_argument("--variant", default=DEFAULT_VARIANT)
    select.add_argument("--count", type=int, default=10)
    select.add_argument("--seed", type=int, default=20261112)
    select.add_argument("--output", type=Path, default=DEFAULT_FIXTURES)
    select.set_defaults(function=command_select)
    run = sub.add_parser("run", help="repeat base and reasoning inference")
    run.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--modes", nargs="+", choices=("base", "reasoning"), default=["base", "reasoning"])
    run.add_argument("--repetitions", type=int, default=100)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--seed", type=int, default=20261112)
    run.add_argument("--model", default=get("models", "default_local"))
    run.add_argument("--base-url", default=f"http://{get('hosts', 'vllm')}:{get('ports', 'vllm')}/v1")
    run.add_argument("--api-key", default=get("llm", "api_key"))
    run.add_argument("--timeout", type=float, default=300.0)
    run.add_argument("--max-reasoning-tokens", type=int, default=2048)
    run.add_argument("--max-final-tokens", type=int, default=384)
    run.add_argument("--resume", action="store_true")
    run.set_defaults(function=command_run)
    report = sub.add_parser("report", help="rebuild reports from predictions")
    report.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    report.add_argument("--output", type=Path, required=True)
    report.set_defaults(function=command_report)
    return root


def main() -> None:
    args = parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
