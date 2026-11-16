#!/usr/bin/env python3
"""Validate the short-contract, narrative-only SFT ablation dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from agents.agent_oow import SFT_ABLATION_SYSTEM_PROMPT
from sft.prepare_exact_runtime_sft import ALLOWED_ACTIONS, validate_decision


FILES = {
    "train_natural": "train_natural.jsonl",
    "train_balanced": "train_balanced.jsonl",
    "validation": "validation_natural.jsonl",
    "test": "test_natural.jsonl",
}


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--minimum-source-clearance-m", type=float, default=500.0)
    args = parser.parse_args()
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    failures: list[str] = []
    expected_hash = hashlib.sha256(SFT_ABLATION_SYSTEM_PROMPT.encode()).hexdigest()
    if manifest.get("system_prompt_sha256") != expected_hash:
        failures.append("short system prompt hash mismatch")
    clearance = manifest.get("source_minimum_range_m")
    if not isinstance(clearance, (int, float)) or clearance < args.minimum_source_clearance_m:
        failures.append("source clearance gate is missing or too low")

    datasets: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for split, filename in FILES.items():
        path = args.dataset / filename
        if not path.is_file():
            failures.append(f"missing {filename}")
            continue
        datasets[split] = rows(path)
        for index, row in enumerate(datasets[split], 1):
            prompt = row.get("prompt")
            if not isinstance(prompt, list) or len(prompt) != 2:
                failures.append(f"{filename}:{index}: expected two prompt messages")
                continue
            if prompt[0] != {"role": "system", "content": SFT_ABLATION_SYSTEM_PROMPT}:
                failures.append(f"{filename}:{index}: system prompt mismatch")
            narrative = prompt[1].get("content") if isinstance(prompt[1], dict) else None
            if not isinstance(narrative, str) or "CURRENT BRIDGE STATE" not in narrative:
                failures.append(f"{filename}:{index}: user message is not a bridge narrative")
            if isinstance(narrative, str) and narrative.lstrip().startswith("{"):
                failures.append(f"{filename}:{index}: JSON wrapper remains")
            try:
                decision = json.loads(row["completion"][0]["content"])
                validate_decision(decision, sorted(ALLOWED_ACTIONS))
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as error:
                failures.append(f"{filename}:{index}: invalid completion: {error}")
            if split != "train_balanced":
                digest = row.get("prompt_sha256")
                if digest in seen:
                    failures.append(f"{filename}:{index}: duplicate natural prompt")
                seen.add(digest)

    mission_sets = {
        split: {row.get("source_file") for row in datasets.get(split, [])}
        for split in ("train_natural", "validation", "test")
    }
    if (
        mission_sets["train_natural"] & mission_sets["validation"]
        or mission_sets["train_natural"] & mission_sets["test"]
        or mission_sets["validation"] & mission_sets["test"]
    ):
        failures.append("mission leakage")
    actions = Counter()
    for row in datasets.get("train_balanced", []):
        actions[json.loads(row["completion"][0]["content"])["action"]] += 1
    total = sum(actions.values())
    if not total or actions["maintain_course"] / total > 0.5 + 1e-9:
        failures.append("balanced maintain_course fraction exceeds 0.5")
    for action, minimum in (("alter_course", 50), ("rejoin_route", 30), ("set_speed", 30)):
        if actions[action] < minimum:
            failures.append(f"insufficient {action} coverage")

    report = {
        "ready_for_training": not failures,
        "ablation": manifest.get("ablation"),
        "system_prompt_sha256": expected_hash,
        "system_prompt_characters": len(SFT_ABLATION_SYSTEM_PROMPT),
        "source_minimum_clearance_m": clearance,
        "plain_narrative_user_message": True,
        "balanced_actions": dict(sorted(actions.items())),
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
