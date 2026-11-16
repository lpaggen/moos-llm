#!/usr/bin/env python3
"""Prove an exact-runtime SFT dataset is safe to hand to the trainer."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from agents.agent_oow import BASE_SYSTEM_PROMPT
from sft.prepare_exact_runtime_sft import (
    EXPECTED_USER_FIELDS,
    FORBIDDEN_TARGET_TERMS,
    OPTIONAL_USER_FIELDS,
    validate_decision,
)


FILES = {
    "train_natural": "train_natural.jsonl",
    "train_balanced": "train_balanced.jsonl",
    "validation": "validation_natural.jsonl",
    "test": "test_natural.jsonl",
}
MINIMUM_BALANCED_ACTIONS = {
    "alter_course": 50,
    "set_speed": 30,
    "rejoin_route": 30,
    "stop": 8,
    "resume": 8,
}


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--minimum-missions", type=int, default=60)
    parser.add_argument("--minimum-source-clearance-m", type=float)
    parser.add_argument(
        "--safe-mission-profile",
        action="store_true",
        help=(
            "Require core tactical-action coverage but report sparse stop/resume/port "
            "coverage as warnings. Use only with a mission-level clearance gate, since "
            "forcing rare actions from unsafe missions would defeat that gate."
        ),
    )
    args = parser.parse_args()
    manifest_path = args.dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    warnings: list[str] = []
    expected_hash = hashlib.sha256(BASE_SYSTEM_PROMPT.encode()).hexdigest()
    if manifest.get("system_prompt_sha256") != expected_hash:
        failures.append("manifest system prompt hash does not match current runtime")
    if not manifest.get("ready_for_training"):
        failures.append("preparation manifest is not marked ready")
    teacher_models = manifest.get("teacher_models_by_audit")
    if not isinstance(teacher_models, dict) or not teacher_models:
        failures.append("manifest has no OpenAI teacher model provenance")
    source_clearance = None
    source_dataset = manifest.get("source_dataset")
    if isinstance(source_dataset, str):
        source_manifest_path = Path(source_dataset) / "manifest.json"
        if source_manifest_path.is_file():
            source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
            source_clearance = source_manifest.get("minimum_range_m")
    if args.minimum_source_clearance_m is not None:
        if not isinstance(source_clearance, (int, float)):
            failures.append("source manifest has no numeric minimum_range_m")
        elif float(source_clearance) < args.minimum_source_clearance_m:
            failures.append(
                f"source clearance gate is {source_clearance} m; require "
                f"{args.minimum_source_clearance_m} m"
            )

    datasets: dict[str, list[dict]] = {}
    prompt_hashes: set[str] = set()
    for split, filename in FILES.items():
        path = args.dataset / filename
        if not path.is_file():
            failures.append(f"missing {filename}")
            continue
        rows = read_rows(path)
        datasets[split] = rows
        for index, row in enumerate(rows, 1):
            prompt = row.get("prompt")
            completion = row.get("completion")
            if not isinstance(prompt, list) or len(prompt) != 2:
                failures.append(f"{filename}:{index}: prompt must have two messages")
                continue
            if prompt[0] != {"role": "system", "content": BASE_SYSTEM_PROMPT}:
                failures.append(f"{filename}:{index}: system prompt mismatch")
            try:
                payload = json.loads(prompt[1]["content"])
                decision = json.loads(completion[0]["content"])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                failures.append(f"{filename}:{index}: malformed user or completion JSON")
                continue
            fields = set(payload) if isinstance(payload, dict) else set()
            if not EXPECTED_USER_FIELDS <= fields or fields - EXPECTED_USER_FIELDS - OPTIONAL_USER_FIELDS:
                failures.append(f"{filename}:{index}: user field mismatch")
            try:
                validate_decision(decision, payload.get("allowed_actions", []))
            except ValueError as error:
                failures.append(f"{filename}:{index}: {error}")
            if int(row.get("token_count", 0)) > int(manifest.get("max_length", 4096)):
                failures.append(f"{filename}:{index}: token limit exceeded")
            if split != "train_balanced":
                digest = row.get("prompt_sha256")
                if digest in prompt_hashes:
                    failures.append(f"{filename}:{index}: duplicate prompt across natural splits")
                prompt_hashes.add(digest)

    natural_sets = {
        split: {row.get("source_file") for row in datasets.get(split, [])}
        for split in ("train_natural", "validation", "test")
    }
    for left, right in (
        ("train_natural", "validation"),
        ("train_natural", "test"),
        ("validation", "test"),
    ):
        if natural_sets[left] & natural_sets[right]:
            failures.append(f"mission leakage between {left} and {right}")
    missions = len(set().union(*natural_sets.values()))
    if missions < args.minimum_missions:
        failures.append(f"only {missions} accepted missions; require {args.minimum_missions}")

    balanced_actions = Counter()
    alter_directions = Counter()
    for row in datasets.get("train_balanced", []):
        decision = json.loads(row["completion"][0]["content"])
        balanced_actions[decision["action"]] += 1
        if decision["action"] == "alter_course":
            alter_directions[
                "port" if float(decision["degrees"]) < 0.0 else "starboard"
            ] += 1
    balanced_total = sum(balanced_actions.values())
    maintain_fraction = (
        balanced_actions["maintain_course"] / balanced_total if balanced_total else 1.0
    )
    if maintain_fraction > float(manifest.get("max_maintain_fraction", 0.5)) + 1e-9:
        failures.append(f"balanced maintain fraction is {maintain_fraction:.4f}")
    required_actions = dict(MINIMUM_BALANCED_ACTIONS)
    if args.safe_mission_profile:
        required_actions.pop("stop")
        required_actions.pop("resume")
    for action, minimum in required_actions.items():
        if balanced_actions[action] < minimum:
            failures.append(
                f"balanced {action} examples={balanced_actions[action]}; require {minimum}"
            )
    required_directions = ("starboard",) if args.safe_mission_profile else ("port", "starboard")
    for direction in required_directions:
        if alter_directions[direction] < 20:
            failures.append(
                f"balanced alter_course {direction} examples={alter_directions[direction]}; "
                "require 20"
            )
    if args.safe_mission_profile:
        for action in ("stop", "resume"):
            if balanced_actions[action] < MINIMUM_BALANCED_ACTIONS[action]:
                warnings.append(
                    f"sparse safe-mission coverage for {action}: "
                    f"{balanced_actions[action]} examples"
                )
        if alter_directions["port"] < 20:
            warnings.append(
                "sparse safe-mission coverage for port alterations: "
                f"{alter_directions['port']} examples"
            )

    report = {
        "ready_for_training": not failures,
        "system_prompt_sha256": expected_hash,
        "teacher_input_contract": {
            "required": sorted(EXPECTED_USER_FIELDS),
            "optional": sorted(OPTIONAL_USER_FIELDS),
            "forbidden": ["state", "deterministic_colregs_oracle"],
        },
        "forbidden_target_terms": list(FORBIDDEN_TARGET_TERMS),
        "teacher_models_by_audit": teacher_models,
        "source_minimum_clearance_m": source_clearance,
        "safe_mission_profile": args.safe_mission_profile,
        "accepted_missions": missions,
        "balanced_actions": dict(sorted(balanced_actions.items())),
        "balanced_alter_course_directions": dict(sorted(alter_directions.items())),
        "balanced_maintain_fraction": maintain_fraction,
        "warnings": warnings,
        "failures": failures,
    }
    output = args.dataset / "preflight.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
