#!/usr/bin/env python3
"""Prepare runtime-prompt SFT data from privileged Luna teacher labels.

The teacher may use numeric state and the deterministic Eriksen/COLREG oracle.
The student receives only the exact base-narrative runtime messages. Examples
whose completion explicitly depends on unavailable oracle/classifier evidence
are rejected instead of pretending that the teacher and student saw the same
input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from agents.agent_oow import BASE_SYSTEM_PROMPT
from sft.prepare_exact_runtime_sft import validate_decision
from sft.prepare_oow_sft import write_jsonl
from sft.prepare_prompt_matched_sft import base_narrative_row


SPLIT_FILES = {
    "train": "train_natural.jsonl",
    "validation": "validation_natural.jsonl",
    "test": "test_natural.jsonl",
}

PRIVILEGED_REASON_MARKERS = (
    "teacher_candidate_lookahead",
    "candidate lookahead",
    "lookahead table",
    "candidate projection",
    "projected minimum separation",
    "time to minimum",
    "projection shows",
    "projected path",
    "privileged evidence",
    "privileged table",
)


def privileged_reason_marker(reason: Any) -> str | None:
    normalized = " ".join(str(reason or "").lower().split())
    return next(
        (marker for marker in PRIVILEGED_REASON_MARKERS if marker in normalized),
        None,
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def completion(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["completion"][0]["content"])


@lru_cache(maxsize=None)
def validate_source_audit(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_file():
        raise ValueError(f"missing source audit {path}")
    with path.open(encoding="utf-8") as source:
        for line in source:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "oow_prompt_input":
                continue
            try:
                payload = json.loads(event["user_prompt"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(payload.get("state"), dict)
                and isinstance(payload.get("deterministic_colregs_oracle"), dict)
                and "teacher demonstrations" in " ".join(
                    str(event.get("system_prompt") or "").split()
                )
            ):
                model = event.get("model")
                if not isinstance(model, str) or not model:
                    raise ValueError(f"{path}: teacher model identity is missing")
                return model
    raise ValueError(f"{path}: no privileged Luna teacher prompts found")


def prepare_row(row: dict[str, Any]) -> dict[str, Any]:
    source_prompt = row.get("prompt")
    if not isinstance(source_prompt, list) or len(source_prompt) < 2:
        raise ValueError(f"{row.get('example_id')}: missing teacher prompt")
    teacher_payload = json.loads(source_prompt[-1]["content"])
    if not isinstance(teacher_payload.get("state"), dict):
        raise ValueError(f"{row.get('example_id')}: teacher state is missing")
    source_file = row.get("source_file")
    if not isinstance(source_file, str):
        raise ValueError(f"{row.get('example_id')}: source audit is missing")
    validate_source_audit(source_file)

    transformed = base_narrative_row(row)
    student_payload = json.loads(transformed["prompt"][-1]["content"])
    decision = completion(transformed)
    validate_decision(decision, student_payload["allowed_actions"])
    leaked_marker = privileged_reason_marker(decision.get("reason"))
    if leaked_marker is not None:
        raise ValueError(
            f"{row.get('example_id')}: completion leaks unavailable {leaked_marker!r}"
        )
    transformed["prompt_sha256"] = hashlib.sha256(
        json.dumps(
            transformed["prompt"], ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    transformed.pop("reference_mode", None)
    return transformed


def filter_tokens(
    rows: list[dict[str, Any]], tokenizer_path: str, max_length: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tokenizer = _tokenizer(tokenizer_path)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        encoded = tokenizer.apply_chat_template(
            row["prompt"] + row["completion"],
            tokenize=True,
            add_generation_prompt=False,
        )
        token_ids = encoded.input_ids if hasattr(encoded, "input_ids") else encoded
        if len(token_ids) > max_length:
            rejected.append({"example_id": row.get("example_id"), "tokens": len(token_ids)})
            continue
        row["token_count"] = len(token_ids)
        accepted.append(row)
    return accepted, rejected


@lru_cache(maxsize=1)
def _tokenizer(tokenizer_path: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)


def deduplicate(
    splits: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    seen: set[str] = set()
    result: dict[str, list[dict[str, Any]]] = {}
    rejected = 0
    for split in ("train", "validation", "test"):
        unique = []
        for row in splits[split]:
            digest = row["prompt_sha256"]
            if digest in seen:
                rejected += 1
                continue
            seen.add(digest)
            unique.append(row)
        result[split] = unique
    return result, rejected


def balance_train(
    rows: list[dict[str, Any]], seed: int, max_maintain_fraction: float
) -> list[dict[str, Any]]:
    maintain = [row for row in rows if completion(row)["action"] == "maintain_course"]
    maneuvers = [row for row in rows if completion(row)["action"] != "maintain_course"]
    if not maneuvers:
        raise ValueError("no non-maintain teacher decisions remain")
    cap = int(len(maneuvers) * max_maintain_fraction / (1.0 - max_maintain_fraction))
    rng = random.Random(seed)
    rng.shuffle(maintain)
    selected = maneuvers + maintain[:cap]
    selected.sort(key=lambda row: (str(row.get("source_file")), int(row.get("cycle_id") or 0)))
    return selected


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(completion(row)["action"] for row in rows)
    directions = Counter()
    for row in rows:
        decision = completion(row)
        if decision["action"] == "alter_course":
            directions["port" if float(decision["degrees"]) < 0 else "starboard"] += 1
    total = len(rows)
    return {
        "rows": total,
        "missions": len({row.get("source_file") for row in rows}),
        "actions": dict(sorted(actions.items())),
        "action_fraction": {
            action: round(count / total, 6) for action, count in sorted(actions.items())
        } if total else {},
        "alter_course_directions": dict(sorted(directions.items())),
        "token_range": (
            [min(row["token_count"] for row in rows), max(row["token_count"] for row in rows)]
            if rows else None
        ),
    }


def teacher_models(source_manifest: dict[str, Any]) -> dict[str, int]:
    models: Counter[str] = Counter()
    for value in source_manifest.get("inputs", []):
        manifest = Path(value) / "campaign.json"
        if not manifest.is_file():
            continue
        campaign = json.loads(manifest.read_text(encoding="utf-8"))
        model = campaign.get("openai_model")
        if isinstance(model, str) and model:
            models[model] += int(campaign.get("runs") or 0)
    return dict(sorted(models.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tokenizer", default="models/qwen3_8b_oow_colregs_qlora")
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--max-maintain-fraction", type=float, default=0.50)
    parser.add_argument("--seed", type=int, default=20261021)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"output directory is not empty: {args.output_dir}")
    if not 0.25 <= args.max_maintain_fraction <= 0.60:
        parser.error("--max-maintain-fraction must be between 0.25 and 0.60")

    source_manifest = json.loads(
        (args.source_dir / "manifest.json").read_text(encoding="utf-8")
    )
    declared_models = teacher_models(source_manifest)
    if not declared_models:
        raise SystemExit("source dataset has no OpenAI teacher campaign provenance")

    splits: dict[str, list[dict[str, Any]]] = {}
    rejection_reasons: Counter[str] = Counter()
    oversized: list[dict[str, Any]] = []
    for split, filename in SPLIT_FILES.items():
        prepared = []
        for row in read_jsonl(args.source_dir / filename):
            try:
                prepared.append(prepare_row(row))
            except ValueError as error:
                rejection_reasons[str(error).split(":", 1)[0]] += 1
        splits[split], split_oversized = filter_tokens(
            prepared, args.tokenizer, args.max_length
        )
        oversized.extend(split_oversized)
    splits, duplicates = deduplicate(splits)
    accepted_source_files = {
        str(row["source_file"])
        for rows in splits.values()
        for row in rows
    }
    models = Counter(
        validate_source_audit(source_file)
        for source_file in accepted_source_files
    )
    balanced = balance_train(
        splits["train"], args.seed, args.max_maintain_fraction
    )

    mission_sets = {
        split: {row.get("source_file") for row in rows}
        for split, rows in splits.items()
    }
    overlap = {
        "train_validation": mission_sets["train"] & mission_sets["validation"],
        "train_test": mission_sets["train"] & mission_sets["test"],
        "validation_test": mission_sets["validation"] & mission_sets["test"],
    }
    if any(overlap.values()):
        raise SystemExit("mission leakage remains after preparation")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "train_natural.jsonl", splits["train"])
    write_jsonl(args.output_dir / "train_balanced.jsonl", balanced)
    write_jsonl(args.output_dir / "validation_natural.jsonl", splits["validation"])
    write_jsonl(args.output_dir / "test_natural.jsonl", splits["test"])
    write_jsonl(
        args.output_dir / "paired_all.jsonl",
        splits["train"] + splits["validation"] + splits["test"],
    )

    dataset_files = {
        "train_natural": args.output_dir / "train_natural.jsonl",
        "train_balanced": args.output_dir / "train_balanced.jsonl",
        "validation": args.output_dir / "validation_natural.jsonl",
        "test": args.output_dir / "test_natural.jsonl",
        "paired_all": args.output_dir / "paired_all.jsonl",
    }

    manifest = {
        "ready_for_training": not any(overlap.values()),
        "provenance": (
            "Privileged Luna labels generated from numeric state plus deterministic "
            "Eriksen/COLREG oracle and, where recorded, a teacher-only candidate "
            "trajectory lookahead; student inputs use the exact base-narrative runtime prompt"
        ),
        "teacher_visibility": (
            "privileged_state_eriksen_colregs_oracle_and_optional_candidate_lookahead"
        ),
        "student_prompt_profile": "base_narrative_runtime",
        "source_dataset": str(args.source_dir),
        "source_minimum_range_m": source_manifest.get("minimum_range_m"),
        "teacher_models_by_audit": dict(sorted(models.items())),
        "system_prompt_sha256": hashlib.sha256(BASE_SYSTEM_PROMPT.encode()).hexdigest(),
        "required_user_fields": ["allowed_actions", "briefing"],
        "optional_user_fields": ["active_action_review"],
        "max_length": args.max_length,
        "max_maintain_fraction": args.max_maintain_fraction,
        "seed": args.seed,
        "rejected_explicit_hidden_evidence_or_invalid_targets": sum(rejection_reasons.values()),
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "oversized_examples_rejected": len(oversized),
        "duplicate_prompts_rejected": duplicates,
        "splits": {
            "train_natural": summarize(splits["train"]),
            "train_balanced": summarize(balanced),
            "validation": summarize(splits["validation"]),
            "test": summarize(splits["test"]),
        },
        "mission_overlap": {name: len(value) for name, value in overlap.items()},
        "dataset_sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in dataset_files.items()
        },
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
