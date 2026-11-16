#!/usr/bin/env python3
"""Fail closed when held-out generation shows action collapse or invalid output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metrics", type=Path)
    parser.add_argument("--min-json-rate", type=float, default=0.99)
    parser.add_argument("--min-constraint-rate", type=float, default=0.99)
    parser.add_argument("--min-action-accuracy", type=float, default=0.40)
    parser.add_argument("--min-macro-f1", type=float, default=0.30)
    parser.add_argument("--max-maintain-fraction", type=float, default=0.75)
    parser.add_argument("--maintain-reference-margin", type=float, default=0.05)
    args = parser.parse_args()
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    failures = []
    for name, minimum in (
        ("strict_json_rate", args.min_json_rate),
        ("constraint_valid_rate", args.min_constraint_rate),
        ("exact_action_accuracy", args.min_action_accuracy),
        ("macro_f1", args.min_macro_f1),
    ):
        value = float(metrics.get(name, 0.0))
        if value < minimum:
            failures.append(f"{name}={value:.4f} < {minimum:.4f}")
    confusion = metrics.get("confusion_matrix") or {}
    predicted_total: dict[str, int] = {}
    for predictions in confusion.values():
        for action, count in predictions.items():
            predicted_total[action] = predicted_total.get(action, 0) + int(count)
    total = sum(predicted_total.values())
    maintain_fraction = predicted_total.get("maintain_course", 0) / total if total else 1.0
    reference_maintain = sum((confusion.get("maintain_course") or {}).values())
    reference_maintain_fraction = reference_maintain / total if total else 0.0
    effective_max_maintain_fraction = max(
        args.max_maintain_fraction,
        reference_maintain_fraction + args.maintain_reference_margin,
    )
    if maintain_fraction > effective_max_maintain_fraction:
        failures.append(
            f"predicted maintain_course fraction={maintain_fraction:.4f} > "
            f"effective ceiling={effective_max_maintain_fraction:.4f}"
        )
    report = {
        "passed": not failures,
        "maintain_course_fraction": maintain_fraction,
        "reference_maintain_course_fraction": reference_maintain_fraction,
        "effective_max_maintain_course_fraction": effective_max_maintain_fraction,
        "failures": failures,
    }
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
