#!/usr/bin/env python3
"""Audit every mission in a teacher campaign against a strict clearance gate."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from tools.build_teacher_dataset import audit_quality


def seed_from_path(path: Path) -> int:
    try:
        return int(path.stem.removeprefix("seed_"))
    except ValueError as error:
        raise ValueError(f"cannot determine seed from {path}") from error


def audit_campaign(
    campaign_dir: Path, minimum_range_m: float, require_complete: bool = False
) -> dict[str, Any]:
    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.is_file():
        raise ValueError(f"missing campaign manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    audits = sorted(campaign_dir.glob("seed_*.jsonl"))
    results_path = campaign_dir / "results.jsonl"
    result_rows = []
    if results_path.is_file():
        for line_number, line in enumerate(
            results_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                result_rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{results_path}:{line_number}: malformed result JSON"
                ) from error
    result_seeds = [int(row["seed"]) for row in result_rows]
    if len(result_seeds) != len(set(result_seeds)):
        raise ValueError("duplicate seed entries in results.jsonl")

    missions = []
    reasons: Counter[str] = Counter()
    accepted_ranges = []
    for audit in audits:
        accepted, reason, minimum = audit_quality(audit, minimum_range_m)
        reasons[reason] += 1
        if accepted and minimum is not None:
            accepted_ranges.append(float(minimum))
        missions.append({
            "seed": seed_from_path(audit),
            "audit": audit.name,
            "accepted": accepted,
            "reason": reason,
            "minimum_observed_range_m": minimum,
        })

    expected = int(manifest.get("runs") or 0)
    start_seed = int(manifest.get("start_seed") or 0)
    expected_seeds = set(range(start_seed, start_seed + expected))
    audit_seeds = {mission["seed"] for mission in missions}
    result_seed_set = set(result_seeds)
    integrity = {
        "missing_audits": sorted(expected_seeds - audit_seeds),
        "missing_results": sorted(expected_seeds - result_seed_set),
        "unexpected_audits": sorted(audit_seeds - expected_seeds),
        "unexpected_results": sorted(result_seed_set - expected_seeds),
    }
    if require_complete and any(integrity.values()):
        raise ValueError(
            "campaign is incomplete or inconsistent: "
            + json.dumps(integrity, sort_keys=True)
        )

    accepted = reasons.get("accepted", 0)
    return {
        "campaign_dir": str(campaign_dir.resolve()),
        "minimum_range_m": minimum_range_m,
        "expected_missions": expected,
        "audits_examined": len(audits),
        "accepted_missions": accepted,
        "acceptance_fraction": accepted / len(audits) if audits else 0.0,
        "reasons": dict(sorted(reasons.items())),
        "accepted_minimum_range_m": {
            "minimum": min(accepted_ranges) if accepted_ranges else None,
            "maximum": max(accepted_ranges) if accepted_ranges else None,
        },
        "integrity": integrity,
        "missions": missions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--minimum-range-m", type=float, required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--minimum-accepted-missions", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.minimum_range_m < 0:
        parser.error("--minimum-range-m must be non-negative")
    if args.minimum_accepted_missions < 0:
        parser.error("--minimum-accepted-missions must be non-negative")
    try:
        report = audit_campaign(
            args.campaign_dir, args.minimum_range_m, args.require_complete
        )
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    if report["accepted_missions"] < args.minimum_accepted_missions:
        parser.error(
            f"only {report['accepted_missions']} missions passed; "
            f"require at least {args.minimum_accepted_missions}"
        )
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
