#!/usr/bin/env python3
"""Reject an adapter that violates clearance or collapses in smoke cases."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cases", default="13,16,22")
    parser.add_argument("--max-maintain-fraction", type=float, default=0.95)
    parser.add_argument("--minimum-clearance-m", type=float, default=500.0)
    args = parser.parse_args()
    expected = [int(value) for value in args.cases.split(",")]
    failures = []
    actions: Counter[str] = Counter()
    cases = {}
    for case in expected:
        path = args.run / f"case_{case:02d}" / "result.json"
        if not path.is_file():
            failures.append(f"case {case}: missing result")
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        cases[str(case)] = {
            "result": result.get("result"),
            "minimum_observed_range_m": result.get("minimum_observed_range_m"),
            "actions": result.get("actions"),
        }
        actions.update(result.get("actions") or {})
        if result.get("result") != "complete":
            failures.append(f"case {case}: {result.get('result')}")
        clearance = result.get("minimum_observed_range_m")
        if not isinstance(clearance, (int, float)):
            failures.append(f"case {case}: missing finite minimum clearance")
        elif float(clearance) < args.minimum_clearance_m:
            failures.append(
                f"case {case}: minimum clearance={float(clearance):.3f} m < "
                f"{args.minimum_clearance_m:.3f} m"
            )
    total = sum(actions.values())
    maintain_fraction = actions["maintain_course"] / total if total else 1.0
    if maintain_fraction > args.max_maintain_fraction:
        failures.append(
            f"maintain_course fraction={maintain_fraction:.4f} > "
            f"{args.max_maintain_fraction:.4f}"
        )
    if sum(value for action, value in actions.items() if action != "maintain_course") < len(expected):
        failures.append("fewer than one non-maintain decision per smoke case on average")
    report = {
        "passed": not failures,
        "cases": cases,
        "aggregate_actions": dict(sorted(actions.items())),
        "maintain_course_fraction": maintain_fraction,
        "minimum_clearance_m": args.minimum_clearance_m,
        "failures": failures,
    }
    output = args.run / "smoke_gate.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
