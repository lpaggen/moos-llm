#!/usr/bin/env python3
"""Convert a validated SFT dataset to short-contract + narrative-only prompts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from agents.agent_oow import SFT_ABLATION_SYSTEM_PROMPT
from sft.prepare_exact_runtime_sft import validate_decision
from sft.prepare_oow_sft import write_jsonl


FILES = (
    "train_natural.jsonl",
    "train_balanced.jsonl",
    "validation_natural.jsonl",
    "test_natural.jsonl",
    "paired_all.jsonl",
)


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@lru_cache(maxsize=1)
def tokenizer(path: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(path, local_files_only=True)


def convert_row(row: dict[str, Any], tokenizer_path: str, max_length: int) -> dict[str, Any]:
    try:
        source_payload = json.loads(row["prompt"][-1]["content"])
        decision = json.loads(row["completion"][0]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{row.get('example_id')}: malformed source row") from error
    if not isinstance(source_payload, dict):
        raise ValueError(f"{row.get('example_id')}: source user message is not JSON")
    narrative = source_payload.get("briefing")
    allowed = source_payload.get("allowed_actions")
    if not isinstance(narrative, str) or not narrative.strip():
        raise ValueError(f"{row.get('example_id')}: missing narrative")
    if not isinstance(allowed, list) or not allowed:
        raise ValueError(f"{row.get('example_id')}: missing allowed actions")
    validate_decision(decision, allowed)

    converted = dict(row)
    converted["prompt"] = [
        {"role": "system", "content": SFT_ABLATION_SYSTEM_PROMPT},
        {"role": "user", "content": narrative},
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


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(
        json.loads(row["completion"][0]["content"])["action"] for row in rows
    )
    return {
        "rows": len(rows),
        "missions": len({row.get("source_file") for row in rows}),
        "actions": dict(sorted(actions.items())),
        "token_range": [
            min(row["token_count"] for row in rows),
            max(row["token_count"] for row in rows),
        ] if rows else None,
    }


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

    converted: dict[str, list[dict[str, Any]]] = {}
    for name in FILES:
        converted[name] = [
            convert_row(row, args.tokenizer, args.max_length)
            for row in read_rows(args.source_dir / name)
        ]

    natural_names = (
        "train_natural.jsonl",
        "validation_natural.jsonl",
        "test_natural.jsonl",
    )
    seen: set[str] = set()
    for name in natural_names:
        for row in converted[name]:
            digest = row["prompt_sha256"]
            if digest in seen:
                raise SystemExit(f"duplicate narrative prompt across natural splits: {digest}")
            seen.add(digest)

    mission_sets = {
        name: {row.get("source_file") for row in converted[name]}
        for name in natural_names
    }
    if (
        mission_sets[natural_names[0]] & mission_sets[natural_names[1]]
        or mission_sets[natural_names[0]] & mission_sets[natural_names[2]]
        or mission_sets[natural_names[1]] & mission_sets[natural_names[2]]
    ):
        raise SystemExit("mission leakage in source splits")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in converted.items():
        write_jsonl(args.output_dir / name, rows)

    output_hashes = {
        name: hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
        for name in FILES
    }
    manifest = {
        "ready_for_training": True,
        "ablation": "short_system_prompt_plus_plain_narrative",
        "source_dataset": str(args.source_dir),
        "source_minimum_range_m": source_manifest.get("source_minimum_range_m"),
        "action_quality_gate": source_manifest.get("action_quality_gate"),
        "filtered_from_dataset": source_manifest.get("filtered_from_dataset"),
        "system_prompt_sha256": hashlib.sha256(
            SFT_ABLATION_SYSTEM_PROMPT.encode()
        ).hexdigest(),
        "system_prompt_characters": len(SFT_ABLATION_SYSTEM_PROMPT),
        "user_message": "plain deterministic narrative only",
        "excluded_user_fields": [
            "allowed_actions",
            "active_action_review",
            "state",
            "deterministic_colregs_oracle",
        ],
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
