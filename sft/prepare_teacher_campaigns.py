#!/usr/bin/env python3
"""Prepare quality-filtered teacher campaigns for mission-disjoint OOW SFT."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from bridge.narratives import render_with_history
from sft.prepare_oow_sft import split_by_episode, write_jsonl
from tools.build_teacher_dataset import audit_quality
from tools.extract_oow_pairs import extract_pairs


DEFAULT_ACTIONS = {
    "maintain_course",
    "alter_course",
    "set_speed",
    "stop",
    "resume",
    "rejoin_route",
}


def campaign_audits(inputs: list[Path]) -> list[Path]:
    paths: set[Path] = set()
    for item in inputs:
        if not item.exists():
            raise FileNotFoundError(item)
        if item.is_dir():
            paths.update(item.glob("seed_*.jsonl"))
            paths.update(item.rglob("audit.jsonl"))
        else:
            paths.add(item)
    return sorted(path.resolve() for path in paths)


def read_episode(path: Path, minimum_range_m: float) -> tuple[list[dict[str, Any]], str]:
    accepted, reason, _ = audit_quality(path, minimum_range_m)
    if not accepted:
        return [], reason
    rows = []
    history: list[dict[str, Any]] = []
    source_file = str(path)
    for index, pair in enumerate(
        extract_pairs(path, validated_only=True, chat_format=True), start=1
    ):
        messages = pair["messages"]
        try:
            user_payload = json.loads(messages[-2]["content"])
        except (IndexError, TypeError, json.JSONDecodeError):
            user_payload = None
        state = user_payload.get("state") if isinstance(user_payload, dict) else None
        if isinstance(state, dict):
            observed_at = state.get("observed_at_s")
            previous_at = history[-1].get("observed_at_s") if history else None
            if (
                previous_at is not None
                and observed_at is not None
                and (
                    float(observed_at) <= float(previous_at)
                    or float(observed_at) - float(previous_at) > 120.0
                )
            ):
                history.clear()
            history.append(state)
            history = history[-3:]
            user_payload["narrative"] = render_with_history(history)
            messages[-2]["content"] = json.dumps(
                user_payload, ensure_ascii=False, separators=(",", ":")
            )
        decision = json.loads(messages[-1]["content"])
        if decision.get("action") not in DEFAULT_ACTIONS:
            continue
        rows.append({
            "example_id": f"{source_file}::decision::{index}",
            "source_file": source_file,
            "cycle_id": index,
            "reference_mode": "none",
            "sample_occurrence": 0,
            "prompt": messages[:-1],
            "completion": [messages[-1]],
            "label": decision,
        })
    return rows, "accepted"


def training_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "label"}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "missions": len({row["source_file"] for row in rows}),
        "actions": dict(sorted(Counter(row["label"]["action"] for row in rows).items())),
    }


def filter_token_lengths(
    rows: list[dict[str, Any]], tokenizer_path: str, max_length: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
    accepted = []
    rejected = []
    for row in rows:
        encoded = tokenizer.apply_chat_template(
            row["prompt"] + row["completion"],
            tokenize=True,
            add_generation_prompt=False,
        )
        token_ids = encoded.input_ids if hasattr(encoded, "input_ids") else encoded
        if len(token_ids) > max_length:
            rejected.append({
                "example_id": row["example_id"],
                "tokens": len(token_ids),
            })
        else:
            row["token_count"] = len(token_ids)
            accepted.append(row)
    return accepted, rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--minimum-range-m", type=float, default=50.0)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument(
        "--tokenizer",
        default="models/qwen3_8b_oow_colregs_qlora",
        help="Local Qwen tokenizer used to reject examples that would be truncated",
    )
    args = parser.parse_args()

    rows = []
    rejection_reasons: Counter[str] = Counter()
    audits = campaign_audits(args.inputs)
    for audit in audits:
        episode, reason = read_episode(audit, args.minimum_range_m)
        if reason != "accepted":
            rejection_reasons[reason] += 1
            continue
        if not episode:
            rejection_reasons["no_usable_decisions"] += 1
            continue
        rows.extend(episode)

    if not rows:
        raise SystemExit("No quality-filtered teacher decisions were found")
    rows, oversized = filter_token_lengths(rows, args.tokenizer, args.max_length)
    if not rows:
        raise SystemExit("Every teacher decision exceeds the configured token limit")
    splits = split_by_episode(rows, args.seed)
    output = args.output_dir
    write_jsonl(output / "paired_all.jsonl", (training_row(row) for row in rows))
    for name, split_rows in splits.items():
        write_jsonl(
            output / f"{name}_natural.jsonl",
            (training_row(row) for row in split_rows),
        )
    write_jsonl(
        output / "train_balanced.jsonl",
        (training_row(row) for row in splits["train"]),
    )

    mission_sets = {
        name: {row["source_file"] for row in split_rows}
        for name, split_rows in splits.items()
    }
    overlap = {
        "train_validation": sorted(mission_sets["train"] & mission_sets["validation"]),
        "train_test": sorted(mission_sets["train"] & mission_sets["test"]),
        "validation_test": sorted(mission_sets["validation"] & mission_sets["test"]),
    }
    manifest = {
        "seed": args.seed,
        "inputs": [str(path) for path in args.inputs],
        "audits_examined": len(audits),
        "minimum_range_m": args.minimum_range_m,
        "tokenizer": args.tokenizer,
        "max_length": args.max_length,
        "oversized_decisions_rejected": len(oversized),
        "oversized_examples": oversized,
        "excluded_actions": ["skip"],
        "rejected_missions": dict(sorted(rejection_reasons.items())),
        "balancing": "none; train_balanced.jsonl is the natural training split",
        "all": summarize(rows),
        "splits": {name: summarize(split_rows) for name, split_rows in splits.items()},
        "mission_overlap": {key: len(value) for key, value in overlap.items()},
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
