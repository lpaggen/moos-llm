#!/usr/bin/env python3
"""Add state-aware navigation actions to compact/narrated MOOS JSONL rows.

The script preserves each input row and its narrative, adding only
``state.allowed_actions`` and ``state.candidate_actions``.  It operates on the
compact state produced by ``bridge.observations``; no raw campaign
log is required.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ELEVATED_RISKS = {"medium", "high", "critical"}
DEFAULT_MANEUVER_DISTANCE_M = 200.0
MIN_TARGET_SPEED = 0.3


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _course_range(contacts: list[dict[str, Any]]) -> dict[str, float]:
    """Mirror the bridge's preferred turn direction for the highest threat."""
    rank = {"critical": 0, "high": 1, "medium": 2}
    threats = sorted(
        (
            contact
            for contact in contacts
            if isinstance(contact, dict)
            and contact.get("risk") in ELEVATED_RISKS
            and contact.get("track_quality", "fresh") == "fresh"
        ),
        key=lambda contact: rank[str(contact["risk"])],
    )
    if not threats:
        return {"min": -60.0, "max": 60.0}

    sector = str(threats[0].get("sector") or "")
    if "port" in sector:
        return {"min": 5.0, "max": 60.0}
    if "starboard" in sector:
        return {"min": -60.0, "max": -5.0}
    if sector == "ahead":
        return {"min": 5.0, "max": 60.0}
    return {"min": -60.0, "max": 60.0}


def compute_candidate_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return executable candidates from the compact current state.

    A stopped/paused ship can explicitly skip issuing a command or resume.
    """
    own_ship = state.get("own_ship")
    own_ship = own_ship if isinstance(own_ship, dict) else {}
    status = str(own_ship.get("movement_status") or "").lower()
    stopped = bool(own_ship.get("stopped"))
    paused = bool(own_ship.get("paused"))

    no_way = stopped or paused or status in {"paused", "stalled", "stopped"}
    if no_way:
        return [
            {
                "action": "skip",
                "degrees": 0.0,
                "distance_m": 0.0,
                "speed": 0.0,
            },
            {
                "action": "resume",
                "degrees": 0.0,
                "distance_m": 0.0,
                "speed": 0.0,
            },
        ]

    contacts = state.get("contacts")
    contacts = contacts if isinstance(contacts, list) else []
    fresh_threat = any(
        isinstance(contact, dict)
        and contact.get("risk") in ELEVATED_RISKS
        and contact.get("track_quality", "fresh") == "fresh"
        for contact in contacts
    )
    current_speed = _number(own_ship.get("speed"))
    target_speed = _number(own_ship.get("target_speed"))
    normal_speed = max(MIN_TARGET_SPEED, target_speed or current_speed)
    speed_candidate = (
        max(MIN_TARGET_SPEED, normal_speed * 0.5)
        if fresh_threat
        else normal_speed
    )

    return [
        {
            "action": "maintain_course",
            "degrees": 0.0,
            "distance_m": 0.0,
            "speed": 0.0,
        },
        {
            "action": "alter_course",
            "degrees_range": _course_range(contacts),
            "distance_m": DEFAULT_MANEUVER_DISTANCE_M,
            "speed": 0.0,
        },
        {
            "action": "set_speed",
            "degrees": 0.0,
            "distance_m": 0.0,
            "speed": round(speed_candidate, 2),
        },
        {
            "action": "stop",
            "degrees": 0.0,
            "distance_m": 0.0,
            "speed": 0.0,
        },
    ]


def add_candidate_actions(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("state")
    if not isinstance(state, dict):
        raise ValueError("row has no object-valued 'state' field")
    candidates = compute_candidate_actions(state)
    state["allowed_actions"] = [candidate["action"] for candidate in candidates]
    state["candidate_actions"] = candidates
    return row


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add allowed_actions and candidate_actions to MOOS JSONL rows."
    )
    parser.add_argument("input", help="Input compact or narrated JSONL")
    parser.add_argument("output", help="Output enriched JSONL")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with input_path.open("r", encoding="utf-8") as source, output_path.open(
        "w", encoding="utf-8"
    ) as destination:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row is not a JSON object")
                add_candidate_actions(row)
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"{input_path}:{line_number}: {error}") from error
            destination.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1

    print(f"Enriched: {count}")
    print(f"Output:   {output_path.resolve()}")


if __name__ == "__main__":
    main()
