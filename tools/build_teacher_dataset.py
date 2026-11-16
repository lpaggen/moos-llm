#!/usr/bin/env python3
"""Build a quality-filtered SFT dataset from one or more campaign directories."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    from tools.extract_oow_pairs import extract_pairs
except ModuleNotFoundError:
    from extract_oow_pairs import extract_pairs


def audit_quality(path: Path, minimum_range_m: float) -> tuple[bool, str, float | None]:
    terminal = None
    minimum_range = None
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                return False, "malformed_json", minimum_range
            if row.get("event") == "bridge_snapshot":
                observed = row.get("state", {}).get("minimum_observed_range")
                if observed is not None and (
                    isinstance(observed, bool)
                    or not isinstance(observed, (int, float))
                    or not math.isfinite(float(observed))
                    or float(observed) < 0.0
                ):
                    return False, "invalid_minimum_range", minimum_range
                if isinstance(observed, (int, float)):
                    minimum_range = observed if minimum_range is None else min(minimum_range, observed)
            elif row.get("event") in {"mission_complete", "mission_failed"}:
                event = row.get("event")
                if terminal is not None and terminal != event:
                    return False, "contradictory_terminal", minimum_range
                terminal = event
                observed = row.get("minimum_observed_range_m")
                if observed is not None and (
                    isinstance(observed, bool)
                    or not isinstance(observed, (int, float))
                    or not math.isfinite(float(observed))
                    or float(observed) < 0.0
                ):
                    return False, "invalid_minimum_range", minimum_range
                if isinstance(observed, (int, float)):
                    minimum_range = observed if minimum_range is None else min(minimum_range, observed)
    if terminal != "mission_complete":
        return False, terminal or "unfinished", minimum_range
    # A completed mission without an observed closest approach cannot prove
    # that it met the configured clearance gate.  Fail closed instead of
    # silently admitting unverifiable teacher data.
    if minimum_range is None:
        return False, "missing_minimum_range", None
    if minimum_range is not None and minimum_range < minimum_range_m:
        return False, "minimum_range", minimum_range
    return True, "accepted", minimum_range


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Campaign directories or audit JSONL files")
    parser.add_argument("--output", "-o", required=True, type=Path)
    parser.add_argument("--minimum-range-m", type=float, default=50.0)
    parser.add_argument("--include-actions", default="maintain_course,alter_course,set_speed,stop,resume,rejoin_route")
    args = parser.parse_args()

    paths = set()
    for item in args.inputs:
        if not item.exists():
            parser.error(f"input does not exist: {item}")
        if item.is_dir():
            paths.update(item.glob("seed_*.jsonl"))
            paths.update(item.rglob("audit.jsonl"))
        else:
            paths.add(item)
    allowed_actions = {value.strip() for value in args.include_actions.split(",") if value.strip()}
    accepted_audits = rejected = written = 0
    reasons: dict[str, int] = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for path in sorted(paths):
            accepted, reason, _ = audit_quality(path, args.minimum_range_m)
            if not accepted:
                rejected += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            accepted_audits += 1
            for pair in extract_pairs(path, validated_only=True, chat_format=True):
                decision = json.loads(pair["messages"][-1]["content"])
                if decision.get("action") not in allowed_actions:
                    continue
                output.write(json.dumps(pair, ensure_ascii=False) + "\n")
                written += 1
    print(
        f"Wrote {written} pairs from {accepted_audits} accepted audits; "
        f"rejected {rejected}: {json.dumps(reasons, sort_keys=True)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
