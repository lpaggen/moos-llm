#!/usr/bin/env python3
"""Fail closed on categorical-turn narrative SFT data."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from agents.agent_oow import SFT_DIRECTIONAL_SYSTEM_PROMPT
from sft.convert_directional_sft import FILES
from sft.prepare_narrative_ablation_sft import read_rows


def validate_decision(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("completion is not an object")
    required = {
        "action", "turn_direction", "turn_degrees", "speed", "reason",
        "auxiliary_commands",
    }
    if set(value) != required:
        raise ValueError(f"completion fields differ: {sorted(set(value) ^ required)}")
    action = value["action"]
    direction = value["turn_direction"]
    magnitude = value["turn_degrees"]
    if not isinstance(magnitude, (int, float)):
        raise ValueError("turn_degrees is not numeric")
    if action == "alter_course":
        if direction not in {"port", "starboard"} or not 0 < magnitude <= 60:
            raise ValueError("invalid categorical course alteration")
    elif direction != "none" or magnitude != 0:
        raise ValueError("unused categorical turn fields are not none/zero")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("empty reason")
    if not isinstance(value["auxiliary_commands"], list):
        raise ValueError("auxiliary_commands is not a list")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--minimum-source-clearance-m", type=float, default=500.0)
    args = parser.parse_args()
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    failures: list[str] = []
    expected_hash = hashlib.sha256(SFT_DIRECTIONAL_SYSTEM_PROMPT.encode()).hexdigest()
    if manifest.get("system_prompt_sha256") != expected_hash:
        failures.append("directional system prompt hash mismatch")
    clearance = manifest.get("source_minimum_range_m")
    if not isinstance(clearance, (int, float)) or clearance < args.minimum_source_clearance_m:
        failures.append("source clearance gate is missing or too low")
    gate = manifest.get("action_quality_gate") or {}
    if gate.get("minimum_range_m") != args.minimum_source_clearance_m:
        failures.append("action-quality clearance gate is missing or mismatched")

    datasets: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for name in FILES:
        path = args.dataset / name
        if not path.is_file():
            failures.append(f"missing {name}")
            continue
        datasets[name] = read_rows(path)
        expected_file_hash = (manifest.get("dataset_sha256") or {}).get(name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_file_hash:
            failures.append(f"hash mismatch: {name}")
        for index, row in enumerate(datasets[name], 1):
            prompt = row.get("prompt")
            if not isinstance(prompt, list) or len(prompt) != 2:
                failures.append(f"{name}:{index}: expected two prompt messages")
                continue
            if prompt[0] != {"role": "system", "content": SFT_DIRECTIONAL_SYSTEM_PROMPT}:
                failures.append(f"{name}:{index}: system prompt mismatch")
            try:
                validate_decision(json.loads(row["completion"][0]["content"]))
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as error:
                failures.append(f"{name}:{index}: {error}")
            if name in {"train_natural.jsonl", "validation_natural.jsonl", "test_natural.jsonl"}:
                digest = row.get("prompt_sha256")
                if digest in seen:
                    failures.append(f"{name}:{index}: duplicate natural prompt")
                seen.add(digest)

    natural_names = ("train_natural.jsonl", "validation_natural.jsonl", "test_natural.jsonl")
    mission_sets = {
        name: {row.get("source_file") for row in datasets.get(name, [])}
        for name in natural_names
    }
    if (
        mission_sets[natural_names[0]] & mission_sets[natural_names[1]]
        or mission_sets[natural_names[0]] & mission_sets[natural_names[2]]
        or mission_sets[natural_names[1]] & mission_sets[natural_names[2]]
    ):
        failures.append("mission leakage")
    actions = Counter(
        json.loads(row["completion"][0]["content"])["action"]
        for row in datasets.get("train_natural.jsonl", [])
    )
    directions = Counter(
        json.loads(row["completion"][0]["content"])["turn_direction"]
        for row in datasets.get("train_natural.jsonl", [])
        if json.loads(row["completion"][0]["content"])["action"] == "alter_course"
    )
    for action, minimum in (("alter_course", 50), ("rejoin_route", 30), ("set_speed", 30)):
        if actions[action] < minimum:
            failures.append(f"insufficient {action} coverage")
    if directions["starboard"] < 50:
        failures.append("insufficient categorical starboard coverage")

    report = {
        "ready_for_training": not failures,
        "source_minimum_clearance_m": clearance,
        "system_prompt_sha256": expected_hash,
        "train_actions": dict(sorted(actions.items())),
        "alter_course_directions": dict(sorted(directions.items())),
        "failures": failures,
    }
    (args.dataset / "preflight.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
