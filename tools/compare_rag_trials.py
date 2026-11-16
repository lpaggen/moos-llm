#!/usr/bin/env python3
"""Summarize paired agent audit logs for RAG experiments."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


def records(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def summarize(path: Path) -> dict:
    rows = list(records(path))
    events = Counter(row.get("event") for row in rows)
    decisions = [row["decision"] for row in rows if row.get("event") == "captain_decision"]
    commands = [row.get("command") for row in rows if row.get("event") == "command_from_decision"]
    commands = [command for command in commands if command]
    retrievals = [row for row in rows if row.get("event") == "maib_retrieval"]
    performance = [row for row in rows if row.get("event") == "cycle_performance"]
    snapshots = [row.get("state", {}) for row in rows if row.get("event") == "bridge_snapshot"]
    return {
        "path": str(path),
        "cycles": events["bridge_snapshot"],
        "rag_enabled": next((row.get("rag_enabled") for row in rows if row.get("event") == "startup"), None),
        "decisions": dict(Counter(item.get("action") for item in decisions)),
        "commands": dict(Counter(item.get("class", item.get("type")) for item in commands)),
        "safety_commands": events["collision_safety_command"],
        "safety_overrides": events["captain_safety_override"],
        "stale_decision_vetoes": events["stale_decision_veto"],
        "validation_failures": events["colregs_validation_failed"],
        "retrievals_with_results": sum(bool(row.get("results")) for row in retrievals),
        "retrieved_chunks": [
            result.get("chunk_id")
            for row in retrievals
            for result in row.get("results", [])
        ],
        "median_retrieval_s": statistics.median(
            row.get("retrieval_duration_s", 0.0) for row in performance
        ) if performance else None,
        "median_captain_s": statistics.median(
            row.get("captain_duration_s", 0.0) for row in performance
        ) if performance else None,
        "final_nav": snapshots[-1].get("nav") if snapshots else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.logs:
        print(json.dumps(summarize(path), indent=2))


if __name__ == "__main__":
    main()
