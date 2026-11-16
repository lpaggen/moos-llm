#!/usr/bin/env python3
"""Convert prepared teacher SFT data to an exact runtime prompt profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agents.agent_oow import BASE_SYSTEM_PROMPT
from sft.prepare_oow_sft import write_jsonl


SOURCE_FILES = (
    "paired_all.jsonl",
    "train_natural.jsonl",
    "validation_natural.jsonl",
    "test_natural.jsonl",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(row)
    return rows


def base_narrative_row(row: dict[str, Any]) -> dict[str, Any]:
    """Match the local base OOW's narrative-mode system and user messages."""
    prompt = row.get("prompt")
    completion = row.get("completion")
    if not isinstance(prompt, list) or len(prompt) < 2:
        raise ValueError(f"{row.get('example_id')}: prompt must contain system and user messages")
    if not isinstance(completion, list) or not completion:
        raise ValueError(f"{row.get('example_id')}: completion must not be empty")
    try:
        source_payload = json.loads(prompt[-1]["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{row.get('example_id')}: invalid user payload") from error
    if not isinstance(source_payload, dict):
        raise ValueError(f"{row.get('example_id')}: user payload must be an object")

    allowed_actions = source_payload.get("allowed_actions")
    briefing = source_payload.get("narrative") or source_payload.get("briefing")
    if not isinstance(allowed_actions, list) or not allowed_actions:
        raise ValueError(f"{row.get('example_id')}: missing allowed_actions")
    if not isinstance(briefing, str) or not briefing.strip():
        raise ValueError(f"{row.get('example_id')}: missing narrative briefing")

    runtime_payload: dict[str, Any] = {
        "allowed_actions": allowed_actions,
        "briefing": briefing,
    }
    action_review = source_payload.get("active_action_review")
    if isinstance(action_review, dict):
        runtime_payload["active_action_review"] = action_review

    transformed = dict(row)
    transformed["prompt"] = [
        {"role": "system", "content": BASE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                runtime_payload, ensure_ascii=False, separators=(",", ":")
            ),
        },
    ]
    transformed.pop("token_count", None)
    return transformed


def filter_token_lengths(
    rows: list[dict[str, Any]], tokenizer_path: str, max_length: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
    accepted = []
    rejected = []
    for source_row in rows:
        row = base_narrative_row(source_row)
        encoded = tokenizer.apply_chat_template(
            row["prompt"] + row["completion"],
            tokenize=True,
            add_generation_prompt=False,
        )
        token_ids = encoded.input_ids if hasattr(encoded, "input_ids") else encoded
        if len(token_ids) > max_length:
            rejected.append({
                "example_id": row.get("example_id"),
                "tokens": len(token_ids),
            })
            continue
        row["token_count"] = len(token_ids)
        accepted.append(row)
    return accepted, rejected


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter()
    for row in rows:
        try:
            actions[json.loads(row["completion"][0]["content"])["action"]] += 1
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"{row.get('example_id')}: invalid completion") from error
    return {
        "rows": len(rows),
        "missions": len({row.get("source_file") for row in rows}),
        "actions": dict(sorted(actions.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument(
        "--tokenizer",
        default="models/qwen3_8b_oow_colregs_qlora",
    )
    args = parser.parse_args()

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"output directory is not empty: {args.output_dir}")
    missing = [name for name in SOURCE_FILES if not (args.source_dir / name).is_file()]
    if missing:
        parser.error("missing source files: " + ", ".join(missing))

    converted: dict[str, list[dict[str, Any]]] = {}
    rejected: dict[str, list[dict[str, Any]]] = {}
    for name in SOURCE_FILES:
        accepted, oversized = filter_token_lengths(
            read_jsonl(args.source_dir / name), args.tokenizer, args.max_length
        )
        converted[name] = accepted
        rejected[name] = oversized

    # The trainer expects this historical filename; no balancing is performed.
    converted["train_balanced.jsonl"] = list(converted["train_natural.jsonl"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in converted.items():
        write_jsonl(args.output_dir / name, rows)

    split_rows = {
        "train": converted["train_natural.jsonl"],
        "validation": converted["validation_natural.jsonl"],
        "test": converted["test_natural.jsonl"],
    }
    mission_sets = {
        name: {row.get("source_file") for row in rows}
        for name, rows in split_rows.items()
    }
    overlap = {
        "train_validation": len(mission_sets["train"] & mission_sets["validation"]),
        "train_test": len(mission_sets["train"] & mission_sets["test"]),
        "validation_test": len(mission_sets["validation"] & mission_sets["test"]),
    }
    manifest = {
        "source_dataset": str(args.source_dir),
        "prompt_profile": "base_narrative_runtime_matched",
        "system_prompt_sha256": hashlib.sha256(
            BASE_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "user_payload_fields": [
            "allowed_actions",
            "briefing",
            "active_action_review (when present)",
        ],
        "removed_user_payload_fields": [
            "state",
            "deterministic_colregs_oracle",
            "legacy briefing when a regenerated narrative is present",
            "narrative (renamed to briefing)",
        ],
        "tokenizer": args.tokenizer,
        "max_length": args.max_length,
        "oversized_examples_rejected": {
            name: len(rows) for name, rows in rejected.items()
        },
        "oversized_examples": rejected,
        "balancing": "none; train_balanced.jsonl duplicates the natural training split",
        "all": summarize(converted["paired_all.jsonl"]),
        "splits": {name: summarize(rows) for name, rows in split_rows.items()},
        "mission_overlap": overlap,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
