#!/usr/bin/env python3
"""Prepare episode-disjoint, validated chat data for OOW supervised tuning.

The input states and gold decisions are paired by line. Outputs include the
natural episode split, a training-only balanced split, chat-formatted JSONL,
and an audit manifest. Validation and test data are never oversampled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from agents.skill_loader import load_skill


ACTIONS = (
    "skip",
    "maintain_course",
    "alter_course",
    "set_speed",
    "stop",
    "resume",
    "rejoin_route",
)
DEFAULT_SPLIT_RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}
DEFAULT_REFERENCE_RATIOS = {"none": 0.7, "relevant": 0.25, "distractor": 0.05}
DEFAULT_BALANCED_TARGETS = {
    "maintain_course": 0.40,
    "set_speed": 0.22,
    "alter_course": 0.15,
    "resume": 0.08,
    "skip": 0.08,
    "stop": 0.05,
    "rejoin_route": 0.02,
}

SYSTEM_PROMPT = load_skill("oow_decision")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as destination:
        for row in rows:
            destination.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            count += 1
    return count


def load_rule_corpus(path: Path) -> dict[int, dict[str, str]]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    rules: dict[int, dict[str, str]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            number = value.get("number")
            text = value.get("text_verbatim")
            if isinstance(number, int) and isinstance(text, str) and text.strip():
                rules[number] = {
                    "title": str(value.get("title") or ""),
                    "text": text.strip(),
                }
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(corpus)
    if not rules:
        raise ValueError(f"no rules found in {path}")
    return rules


def validate_pair(
    input_row: dict[str, Any], label: dict[str, Any], line_number: int
) -> None:
    state = input_row.get("state")
    if not isinstance(state, dict):
        raise ValueError(f"line {line_number}: input has no object-valued state")
    action = label.get("action")
    if action not in ACTIONS:
        raise ValueError(f"line {line_number}: unsupported action {action!r}")
    allowed = state.get("allowed_actions")
    candidates = state.get("candidate_actions")
    if not isinstance(allowed, list) or not isinstance(candidates, list):
        raise ValueError(f"line {line_number}: input lacks action constraints")
    if action not in allowed:
        raise ValueError(f"line {line_number}: {action!r} is not allowed")
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and item.get("action") == action
        ),
        None,
    )
    if candidate is None:
        raise ValueError(f"line {line_number}: {action!r} has no candidate")

    required = {"action", "degrees", "speed", "reason", "auxiliary_commands"}
    if set(label) != required:
        raise ValueError(
            f"line {line_number}: label fields must be exactly {sorted(required)}"
        )
    if not isinstance(label["reason"], str) or not label["reason"].strip():
        raise ValueError(f"line {line_number}: reason must be non-empty")
    if not isinstance(label["auxiliary_commands"], list):
        raise ValueError(f"line {line_number}: auxiliary_commands must be a list")

    degrees = float(label["degrees"])
    speed = float(label["speed"])
    if action == "alter_course":
        degree_range = candidate.get("degrees_range") or {}
        minimum = float(degree_range.get("min", -60.0))
        maximum = float(degree_range.get("max", 60.0))
        if degrees == 0.0 or not minimum <= degrees <= maximum:
            raise ValueError(
                f"line {line_number}: degrees {degrees} outside [{minimum}, {maximum}]"
            )
    elif degrees != 0.0:
        raise ValueError(f"line {line_number}: non-course action has nonzero degrees")

    if action == "set_speed":
        expected = float(candidate.get("speed", 0.0))
        if speed <= 0.0 or not math.isclose(speed, expected, abs_tol=1e-9):
            raise ValueError(
                f"line {line_number}: speed {speed} does not match candidate {expected}"
            )
    elif speed != 0.0:
        raise ValueError(f"line {line_number}: non-speed action has nonzero speed")


def pair_rows(
    inputs: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(inputs) != len(labels):
        raise ValueError(
            f"line count mismatch: {len(inputs)} inputs versus {len(labels)} labels"
        )
    paired = []
    for index, (input_row, label) in enumerate(zip(inputs, labels), start=1):
        validate_pair(input_row, label, index)
        source_file = input_row.get("source_file")
        if not isinstance(source_file, str) or not source_file:
            raise ValueError(f"line {index}: source_file is required for episode splitting")
        paired.append(
            {
                "example_id": input_row.get("id") or f"line::{index}",
                "source_file": source_file,
                "cycle_id": input_row.get("cycle_id"),
                "input": input_row,
                "label": label,
            }
        )
    return paired


def split_by_episode(
    rows: list[dict[str, Any]], seed: int
) -> dict[str, list[dict[str, Any]]]:
    """Greedily stratify whole episodes by row and action totals."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["source_file"]].append(row)

    total_actions = Counter(row["label"]["action"] for row in rows)
    targets = {
        split: {
            "rows": len(rows) * ratio,
            "actions": {
                action: total_actions[action] * ratio for action in ACTIONS
            },
        }
        for split, ratio in DEFAULT_SPLIT_RATIOS.items()
    }
    assigned: dict[str, list[dict[str, Any]]] = {
        split: [] for split in DEFAULT_SPLIT_RATIOS
    }
    row_counts = Counter()
    action_counts: dict[str, Counter[str]] = {
        split: Counter() for split in DEFAULT_SPLIT_RATIOS
    }

    rng = random.Random(seed)
    episodes = list(groups.items())
    rng.shuffle(episodes)
    episodes.sort(key=lambda item: len(item[1]), reverse=True)

    for _, episode_rows in episodes:
        episode_actions = Counter(row["label"]["action"] for row in episode_rows)
        best_split = None
        best_score = None
        for split in DEFAULT_SPLIT_RATIOS:
            projected_rows = row_counts[split] + len(episode_rows)
            row_target = max(1.0, targets[split]["rows"])
            # Prefer the split with the lowest projected utilization. Unlike
            # squared distance from the final target, this does not fill the
            # small validation/test buckets first and strand action-heavy
            # episodes there.
            score = projected_rows / row_target
            episode_total = max(1, len(episode_rows))
            action_utilization = 0.0
            for action, count in episode_actions.items():
                action_target = targets[split]["actions"][action]
                if action_target:
                    projected = action_counts[split][action] + count
                    action_utilization += (count / episode_total) * (
                        projected / action_target
                    )
            score += 0.75 * action_utilization
            # Strongly discourage material overfill while retaining flexibility.
            if projected_rows > row_target * 1.05:
                score += 10.0 * (projected_rows / row_target - 1.05)
            score += rng.random() * 1e-9
            if best_score is None or score < best_score:
                best_score = score
                best_split = split
        assert best_split is not None
        assigned[best_split].extend(episode_rows)
        row_counts[best_split] += len(episode_rows)
        action_counts[best_split].update(episode_actions)

    for split in assigned:
        assigned[split].sort(
            key=lambda row: (row["source_file"], row.get("cycle_id") or -1)
        )
    return assigned


def rebalance_training(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Downsample common actions and mildly oversample rare actions."""
    by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_action[row["label"]["action"]].append(row)

    present_targets = {
        action: fraction
        for action, fraction in DEFAULT_BALANCED_TARGETS.items()
        if by_action.get(action)
    }
    target_total = max(
        math.ceil(len(by_action[action]) / fraction)
        for action, fraction in present_targets.items()
        if action != "maintain_course"
    )
    rng = random.Random(seed)
    balanced: list[dict[str, Any]] = []
    for action, fraction in present_targets.items():
        source = list(by_action[action])
        rng.shuffle(source)
        desired = max(1, round(target_total * fraction))
        if desired <= len(source):
            chosen = source[:desired]
        else:
            chosen = [deepcopy(source[index % len(source)]) for index in range(desired)]
        for occurrence, row in enumerate(chosen):
            row = deepcopy(row)
            row["sample_occurrence"] = occurrence // len(source)
            balanced.append(row)
    rng.shuffle(balanced)
    return balanced


def relevant_rule_ids(row: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    state = row["input"].get("state", {})
    for contact in state.get("contacts", []):
        if not isinstance(contact, dict):
            continue
        ids.update(x for x in contact.get("standing_rules", []) if isinstance(x, int))
        ids.update(
            x for x in contact.get("active_encounter_rules", []) if isinstance(x, int)
        )
    return sorted(ids)


def stable_fraction(key: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def reference_mode(row: dict[str, Any], seed: int) -> str:
    value = stable_fraction(str(row["example_id"]), seed)
    cumulative = 0.0
    for mode in ("none", "relevant", "distractor"):
        cumulative += DEFAULT_REFERENCE_RATIOS[mode]
        if value < cumulative:
            return mode
    return "none"


def render_references(
    row: dict[str, Any], mode: str, rules: dict[int, dict[str, str]], seed: int
) -> str:
    if mode == "none":
        return "COLREG REFERENCE MATERIAL: NOT PROVIDED"
    ids = [number for number in relevant_rule_ids(row) if number in rules]
    headings = ["COLREG REFERENCE MATERIAL: PROVIDED BELOW"]
    for number in ids:
        rule = rules[number]
        headings.append(
            f"Rule {number} — {rule['title']}\n{rule['text']}"
        )
    if mode == "distractor":
        distractors = [number for number in sorted(rules) if number not in ids]
        if distractors:
            index = int(stable_fraction(str(row["example_id"]), seed + 1) * len(distractors))
            number = distractors[min(index, len(distractors) - 1)]
            rule = rules[number]
            headings.append(
                "ADDITIONAL REFERENCE (may be irrelevant to this encounter)\n"
                f"Rule {number} — {rule['title']}\n{rule['text']}"
            )
    return "\n\n".join(headings)


def to_chat_row(
    row: dict[str, Any],
    rules: dict[int, dict[str, str]],
    seed: int,
    reference_condition: str = "mixed",
) -> dict[str, Any]:
    input_row = row["input"]
    state = input_row["state"]
    mode = "none" if reference_condition == "none" else reference_mode(row, seed)
    constraints = {
        "allowed_actions": state["allowed_actions"],
        "candidate_actions": state["candidate_actions"],
    }
    user = (
        str(input_row.get("narrative") or "").strip()
        + "\n\nPERMITTED ACTIONS\n"
        + json.dumps(constraints, ensure_ascii=False, separators=(",", ":"))
    )
    if reference_condition != "none":
        user += "\n\n" + render_references(row, mode, rules, seed)
    return {
        "example_id": row["example_id"],
        "source_file": row["source_file"],
        "cycle_id": row.get("cycle_id"),
        "reference_mode": mode,
        "sample_occurrence": row.get("sample_occurrence", 0),
        "prompt": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "completion": [
            {
                "role": "assistant",
                "content": json.dumps(
                    row["label"], ensure_ascii=False, separators=(",", ":")
                ),
            }
        ],
    }


def statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(row["label"]["action"] for row in rows)
    total = len(rows)
    return {
        "rows": total,
        "episodes": len({row["source_file"] for row in rows}),
        "actions": {
            action: {
                "count": actions[action],
                "fraction": round(actions[action] / total, 6) if total else 0.0,
            }
            for action in ACTIONS
            if actions[action]
        },
        "unique_reasons": len({row["label"]["reason"] for row in rows}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", help="State/narrative JSONL")
    parser.add_argument("labels", help="Line-aligned captain decision JSONL")
    parser.add_argument("output_dir", help="Directory for prepared datasets")
    parser.add_argument(
        "--colregs",
        default="agents/colregs/colregs.json",
        help="Verbatim COLREG JSON corpus",
    )
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument(
        "--reference-condition",
        choices=("mixed", "none"),
        default="mixed",
        help=(
            "COLREG reference treatment. 'mixed' uses the configured 70/25/5 "
            "mixture; 'none' removes verbatim COLREG text from every prompt."
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.inputs)
    label_path = Path(args.labels)
    output_dir = Path(args.output_dir)
    rules = load_rule_corpus(Path(args.colregs))
    paired = pair_rows(read_jsonl(input_path), read_jsonl(label_path))
    splits = split_by_episode(paired, args.seed)
    balanced = rebalance_training(splits["train"], args.seed)

    write_jsonl(output_dir / "paired_all.jsonl", paired)
    for split, rows in splits.items():
        write_jsonl(output_dir / f"{split}_natural.jsonl", rows)
        write_jsonl(
            output_dir / "chat" / f"{split}_natural.jsonl",
            (
                to_chat_row(row, rules, args.seed, args.reference_condition)
                for row in rows
            ),
        )
    write_jsonl(output_dir / "train_balanced.jsonl", balanced)
    write_jsonl(
        output_dir / "chat" / "train_balanced.jsonl",
        (
            to_chat_row(row, rules, args.seed, args.reference_condition)
            for row in balanced
        ),
    )

    episode_sets = {
        split: {row["source_file"] for row in rows} for split, rows in splits.items()
    }
    overlap = {
        "train_validation": sorted(episode_sets["train"] & episode_sets["validation"]),
        "train_test": sorted(episode_sets["train"] & episode_sets["test"]),
        "validation_test": sorted(
            episode_sets["validation"] & episode_sets["test"]
        ),
    }
    manifest = {
        "seed": args.seed,
        "inputs": str(input_path),
        "labels": str(label_path),
        "colregs": str(args.colregs),
        "reference_condition": args.reference_condition,
        "split_ratios": DEFAULT_SPLIT_RATIOS,
        "reference_ratios": (
            {"none": 1.0, "relevant": 0.0, "distractor": 0.0}
            if args.reference_condition == "none"
            else DEFAULT_REFERENCE_RATIOS
        ),
        "balanced_training_targets": DEFAULT_BALANCED_TARGETS,
        "natural": {split: statistics(rows) for split, rows in splits.items()},
        "balanced_train": statistics(balanced),
        "episode_overlap": overlap,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
