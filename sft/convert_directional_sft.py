#!/usr/bin/env python3
"""Derive categorical-turn SFT data without modifying source labels or logs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from agents.agent_oow import SFT_DIRECTIONAL_SYSTEM_PROMPT
from sft.prepare_narrative_ablation_sft import read_rows, summarize, tokenizer
from sft.prepare_oow_sft import write_jsonl


FILES = (
    "train_natural.jsonl",
    "train_balanced.jsonl",
    "validation_natural.jsonl",
    "test_natural.jsonl",
    "paired_all.jsonl",
)


def convert_decision(decision: dict[str, Any]) -> dict[str, Any]:
    action = decision.get("action")
    degrees = decision.get("degrees")
    if not isinstance(degrees, (int, float)):
        raise ValueError("source degrees must be numeric")
    degrees = float(degrees)
    if action == "alter_course":
        if degrees == 0.0 or abs(degrees) > 60.0:
            raise ValueError(f"invalid source course alteration: {degrees}")
        direction = "starboard" if degrees > 0.0 else "port"
        magnitude = abs(degrees)
    else:
        if degrees != 0.0:
            raise ValueError(f"unused source degrees for {action}: {degrees}")
        direction = "none"
        magnitude = 0.0
    return {
        "action": action,
        "turn_direction": direction,
        "turn_degrees": magnitude,
        "speed": decision.get("speed"),
        "reason": decision.get("reason"),
        "auxiliary_commands": decision.get("auxiliary_commands"),
    }


def convert_row(
    row: dict[str, Any], tokenizer_path: str, max_length: int
) -> dict[str, Any]:
    converted = dict(row)
    prompt = row.get("prompt")
    if not isinstance(prompt, list) or len(prompt) != 2:
        raise ValueError(f"{row.get('example_id')}: expected two prompt messages")
    converted["prompt"] = [
        {"role": "system", "content": SFT_DIRECTIONAL_SYSTEM_PROMPT},
        dict(prompt[1]),
    ]
    try:
        source_decision = json.loads(row["completion"][0]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{row.get('example_id')}: malformed completion") from error
    categorical = convert_decision(source_decision)
    converted["completion"] = [
        {
            "role": "assistant",
            "content": json.dumps(
                categorical, ensure_ascii=False, separators=(",", ":")
            ),
        }
    ]
    canonical = json.dumps(
        converted["prompt"], ensure_ascii=False, separators=(",", ":")
    )
    converted["prompt_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    encoded = tokenizer(tokenizer_path).apply_chat_template(
        converted["prompt"] + converted["completion"],
        tokenize=True,
        add_generation_prompt=False,
    )
    token_ids = encoded.input_ids if hasattr(encoded, "input_ids") else encoded
    if len(token_ids) > max_length:
        raise ValueError(
            f"{row.get('example_id')}: {len(token_ids)} tokens exceeds {max_length}"
        )
    converted["token_count"] = len(token_ids)
    return converted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tokenizer", default="models/qwen3_8b_oow_colregs_qlora")
    parser.add_argument("--max-length", type=int, default=4096)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"output directory is not empty: {args.output_dir}")

    source_manifest = json.loads(
        (args.source_dir / "manifest.json").read_text(encoding="utf-8")
    )
    if not source_manifest.get("ready_for_training"):
        raise SystemExit("source dataset is not marked ready for training")
    missing = [name for name in FILES if not (args.source_dir / name).is_file()]
    if missing:
        parser.error("missing source files: " + ", ".join(missing))

    converted = {
        name: [
            convert_row(row, args.tokenizer, args.max_length)
            for row in read_rows(args.source_dir / name)
        ]
        for name in FILES
    }
    natural = (
        "train_natural.jsonl",
        "validation_natural.jsonl",
        "test_natural.jsonl",
    )
    seen: set[str] = set()
    mission_sets: dict[str, set[str]] = {}
    for name in natural:
        mission_sets[name] = {
            str(row.get("source_file")) for row in converted[name]
        }
        for row in converted[name]:
            digest = str(row["prompt_sha256"])
            if digest in seen:
                raise SystemExit(f"duplicate prompt across natural splits: {digest}")
            seen.add(digest)
    if (
        mission_sets[natural[0]] & mission_sets[natural[1]]
        or mission_sets[natural[0]] & mission_sets[natural[2]]
        or mission_sets[natural[1]] & mission_sets[natural[2]]
    ):
        raise SystemExit("mission leakage in source splits")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in converted.items():
        write_jsonl(args.output_dir / name, rows)
    source_hashes = {
        name: hashlib.sha256((args.source_dir / name).read_bytes()).hexdigest()
        for name in FILES
    }
    output_hashes = {
        name: hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
        for name in FILES
    }
    manifest = {
        "ready_for_training": True,
        "ablation": "short_prompt_plain_narrative_categorical_turn_direction",
        "source_dataset": str(args.source_dir),
        "source_dataset_sha256": source_hashes,
        "source_minimum_range_m": source_manifest.get("source_minimum_range_m"),
        "action_quality_gate": source_manifest.get("action_quality_gate"),
        "transformation": (
            "lossless label migration: signed degrees become categorical port/starboard "
            "plus unsigned magnitude; original source files and audit logs are unchanged"
        ),
        "system_prompt_sha256": hashlib.sha256(
            SFT_DIRECTIONAL_SYSTEM_PROMPT.encode()
        ).hexdigest(),
        "system_prompt_characters": len(SFT_DIRECTIONAL_SYSTEM_PROMPT),
        "max_length": args.max_length,
        "splits": {
            "train_natural": summarize(converted["train_natural.jsonl"]),
            "train_balanced": summarize(converted["train_balanced.jsonl"]),
            "validation": summarize(converted["validation_natural.jsonl"]),
            "test": summarize(converted["test_natural.jsonl"]),
        },
        "mission_overlap": {
            "train_validation": 0,
            "train_test": 0,
            "validation_test": 0,
        },
        "dataset_sha256": output_hashes,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
