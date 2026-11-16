#!/usr/bin/env python3
"""Summarize one Imazu agent audit as a compact evaluation record."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


def rows(path: Path) -> list[dict[str, Any]]:
    result = []
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            result.append(json.loads(line))
    return result


def finite(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def summarize(path: Path, case: int, exit_status: int, timed_out: bool, architecture: str | None = None) -> dict[str, Any]:
    audit = rows(path)
    events = Counter(row.get("event") for row in audit)
    snapshots = [row.get("state", {}) for row in audit if row.get("event") == "bridge_snapshot"]
    observed = [finite(state.get("minimum_observed_range")) for state in snapshots]
    observed = [value for value in observed if value is not None]
    terminal = "timeout" if timed_out else "failed"
    if events["mission_failed"]:
        terminal = "collision"
    elif events["mission_complete"]:
        terminal = "complete"
    elif events["cycle_limit_reached"]:
        terminal = "cycle_limit"
    elif exit_status == 0:
        terminal = "exited"
    decisions = [
        row
        for row in audit
        if row.get("event") in {"oow_decision", "captain_decision"}
    ]
    actions = Counter()
    cited_decisions = 0
    for row in decisions:
        decision = row.get("decision") or row.get("result") or {}
        if isinstance(decision, dict) and decision.get("action"):
            actions[str(decision["action"])] += 1
            reason = str(decision.get("reason") or "")
            cited_decisions += int(bool(re.search(r"\b(?:COLREG(?:s)?\s*)?Rule\s*\d+\b|\bCOLREG\s*\d+\b", reason, re.I)))
    startup = next((row for row in audit if row.get("event") == "startup"), {})
    return {
        "case": case,
        "architecture": architecture,
        "result": terminal,
        "exit_status": exit_status,
        "model": startup.get("oow_model"),
        "rag_enabled": startup.get("rag_enabled"),
        "colregs_rag_enabled": startup.get("colregs_rag_enabled"),
        "reasoning_enabled": startup.get("reasoning_enabled"),
        "cycles": events["bridge_snapshot"],
        "decisions": len(decisions),
        "decisions_with_rule_citations": cited_decisions,
        "rule_citation_rate": cited_decisions / len(decisions) if decisions else None,
        "actions": dict(sorted(actions.items())),
        "minimum_observed_range_m": min(observed) if observed else None,
        "near_collision_events": events["near_collision"],
        "validation_failures": events["colregs_validation_failed"],
        "audit": path.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", type=Path)
    parser.add_argument("--case", type=int, required=True)
    parser.add_argument("--exit-status", type=int, required=True)
    parser.add_argument("--timed-out", action="store_true")
    parser.add_argument("--architecture")
    args = parser.parse_args()
    print(json.dumps(summarize(args.audit, args.case, args.exit_status, args.timed_out, args.architecture), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
