#!/usr/bin/env python3
"""Remove locally unsafe or semantically contradictory privileged-teacher labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sft.prepare_oow_sft import write_jsonl
from sft.prepare_privileged_teacher_sft import balance_train, completion, summarize


NATURAL_FILES = {
    "train": "train_natural.jsonl",
    "validation": "validation_natural.jsonl",
    "test": "test_natural.jsonl",
}
STARBOARD_REQUIRED = re.compile(
    r"(?:both vessels must alter to starboard|"
    r"starboard (?:alteration|turn) (?:is|remains) (?:needed|required|preferable)|"
    r"must alter to starboard)",
    re.IGNORECASE,
)
PORT_REQUIRED = re.compile(
    r"(?:port (?:alteration|turn) (?:is|remains) (?:needed|required|preferable)|"
    r"must alter to port)",
    re.IGNORECASE,
)
STARBOARD_ALTERNATIVE_UNSAFE = re.compile(
    r"(?:starboard.{0,100}(?:unsafe|worsen|worse|reduce separation|not preserve|"
    r"cannot preserve|fails? to preserve)|"
    r"(?:unsafe|worsen|worse|reduce separation|not preserve|cannot preserve|"
    r"fails? to preserve).{0,100}starboard)",
    re.IGNORECASE,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def candidate_for(
    lookahead: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any] | None:
    action = decision.get("action")
    degrees = float(decision.get("degrees") or 0.0)
    speed = float(decision.get("speed") or 0.0)
    matches: list[dict[str, Any]] = []
    for candidate in lookahead.get("candidates", []):
        if candidate.get("action") != action:
            continue
        if action == "alter_course" and abs(float(candidate.get("degrees") or 0.0) - degrees) > 1e-6:
            continue
        if action == "set_speed" and abs(
            float(candidate.get("speed_mps", candidate.get("speed", 0.0)) or 0.0)
            - speed
        ) > 0.15:
            continue
        matches.append(candidate)
    return matches[0] if matches else None


def candidate_is_clear(candidate: dict[str, Any], minimum_m: float) -> bool:
    projected = candidate.get("projected_minimum_separation_m")
    return (
        candidate.get("all_contacts_at_or_above_minimum") is True
        and isinstance(projected, (int, float))
        and float(projected) >= minimum_m
    )


def acute_contacts(state: dict[str, Any], minimum_m: float) -> list[str]:
    result: list[str] = []
    for contact in state.get("contacts", []):
        if contact.get("projection_status") != "valid":
            continue
        cpa = contact.get("cpa_distance_m")
        tcpa = contact.get("tcpa_s")
        if (
            isinstance(cpa, (int, float))
            and isinstance(tcpa, (int, float))
            and float(cpa) < minimum_m
            and 0.0 <= float(tcpa) <= 300.0
        ):
            result.append(str(contact.get("name") or "unknown"))
    return result


def action_quality_issue(
    decision: dict[str, Any], teacher_payload: dict[str, Any], minimum_m: float = 500.0
) -> str | None:
    action = decision.get("action")
    degrees = float(decision.get("degrees") or 0.0)
    reason = " ".join(str(decision.get("reason") or "").split())
    lookahead = teacher_payload.get("teacher_candidate_lookahead")
    lookahead = lookahead if isinstance(lookahead, dict) else None
    state = teacher_payload.get("state")
    state = state if isinstance(state, dict) else {}

    if action == "alter_course":
        if degrees < 0.0 and STARBOARD_REQUIRED.search(reason):
            return "port_output_but_reason_requires_starboard"
        if degrees > 0.0 and PORT_REQUIRED.search(reason):
            return "starboard_output_but_reason_requires_port"

    selected = candidate_for(lookahead, decision) if lookahead else None
    if lookahead and action in {"alter_course", "set_speed", "rejoin_route"}:
        if selected is None:
            return "action_missing_from_teacher_lookahead"
        if not candidate_is_clear(selected, minimum_m):
            return "selected_action_fails_500m_lookahead"

    if action == "alter_course" and degrees < 0.0:
        if not lookahead:
            return "port_action_has_no_candidate_lookahead"
        if not STARBOARD_ALTERNATIVE_UNSAFE.search(reason):
            return "port_action_lacks_explicit_starboard_exception"
        safe_starboard = [
            candidate
            for candidate in lookahead.get("candidates", [])
            if candidate.get("action") == "alter_course"
            and float(candidate.get("degrees") or 0.0) > 0.0
            and candidate_is_clear(candidate, minimum_m)
        ]
        if safe_starboard:
            return "port_action_used_despite_safe_starboard_candidate"

    acute = acute_contacts(state, minimum_m)
    if acute and action in {"maintain_course", "rejoin_route", "resume"}:
        if selected is None or not candidate_is_clear(selected, minimum_m):
            return f"{action}_during_acute_sub500m_projection"

    return None


def source_rows(source_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for filename in NATURAL_FILES.values():
        for row in read_jsonl(source_dir / filename):
            result[str(row["example_id"])] = row
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--minimum-range-m", type=float, default=500.0)
    parser.add_argument("--seed", type=int, default=20261109)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"output directory is not empty: {args.output_dir}")

    manifest = json.loads((args.input_dir / "manifest.json").read_text(encoding="utf-8"))
    teacher_source = Path(str(manifest["source_dataset"]))
    indexed_source = source_rows(teacher_source)
    kept: dict[str, list[dict[str, Any]]] = {}
    reasons: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    for split, filename in NATURAL_FILES.items():
        kept[split] = []
        for row in read_jsonl(args.input_dir / filename):
            source = indexed_source.get(str(row.get("example_id")))
            if source is None:
                issue = "missing_privileged_source_row"
            else:
                payload = json.loads(source["prompt"][-1]["content"])
                issue = action_quality_issue(
                    completion(row), payload, args.minimum_range_m
                )
            if issue is None:
                kept[split].append(row)
            else:
                reasons[issue] += 1
                rejected.append(
                    {
                        "split": split,
                        "example_id": row.get("example_id"),
                        "reason": issue,
                        "decision": completion(row),
                    }
                )

    balanced = balance_train(kept["train"], args.seed, 0.5)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "train_natural.jsonl", kept["train"])
    write_jsonl(args.output_dir / "train_balanced.jsonl", balanced)
    write_jsonl(args.output_dir / "validation_natural.jsonl", kept["validation"])
    write_jsonl(args.output_dir / "test_natural.jsonl", kept["test"])
    write_jsonl(
        args.output_dir / "paired_all.jsonl",
        kept["train"] + kept["validation"] + kept["test"],
    )
    write_jsonl(args.output_dir / "action_quality_rejections.jsonl", rejected)

    output_files = [
        "train_natural.jsonl",
        "train_balanced.jsonl",
        "validation_natural.jsonl",
        "test_natural.jsonl",
        "paired_all.jsonl",
        "action_quality_rejections.jsonl",
    ]
    updated = dict(manifest)
    updated.update(
        {
            "filtered_from_dataset": str(args.input_dir),
            "source_dataset": str(teacher_source),
            "action_quality_gate": {
                "minimum_range_m": args.minimum_range_m,
                "policy": (
                    "retain all source rows, reject semantically contradictory course "
                    "directions, reject projected-unsafe selected actions, require privileged "
                    "candidate evidence and a student-visible explanation that starboard is "
                    "unsafe for port actions, prefer a safe starboard candidate, "
                    "and reject passive/rejoin actions during acute sub-500m projections"
                ),
                "rejected_rows": len(rejected),
                "reasons": dict(sorted(reasons.items())),
            },
            "seed": args.seed,
            "splits": {
                "train_natural": summarize(kept["train"]),
                "train_balanced": summarize(balanced),
                "validation": summarize(kept["validation"]),
                "test": summarize(kept["test"]),
            },
            "dataset_sha256": {
                filename: hashlib.sha256((args.output_dir / filename).read_bytes()).hexdigest()
                for filename in output_files
            },
        }
    )
    (args.output_dir / "manifest.json").write_text(
        json.dumps(updated, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(updated["action_quality_gate"], indent=2))
    print(json.dumps(updated["splits"], indent=2))


if __name__ == "__main__":
    main()
