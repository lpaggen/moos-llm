#!/usr/bin/env python3
"""Remove deterministic COLREG conclusions and action hints from JSONL inputs."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from bridge.narratives import continues_sequence, render_with_history


STATE_LEAKAGE_FIELDS = {"allowed_actions", "candidate_actions"}
CONTACT_LEAKAGE_FIELDS = {
    "encounter_type",
    "active_encounter",
    "risk",
    "last_computed_risk",
    "own_role",
    "standing_rules",
    "active_encounter_rules",
    "applicable_rules",
}


def sanitize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a factual state without policy labels or answer-bearing constraints."""
    clean = deepcopy(state)
    for field in STATE_LEAKAGE_FIELDS:
        clean.pop(field, None)
    contacts = clean.get("contacts")
    if isinstance(contacts, list):
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            for field in CONTACT_LEAKAGE_FIELDS:
                contact.pop(field, None)
    return clean


def sanitize_rows(rows: list[dict[str, Any]], *, window_size: int = 3,
                  max_window_gap: float = 120.0) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    previous_row: dict[str, Any] | None = None
    previous_state: dict[str, Any] | None = None

    for source_row in rows:
        row = deepcopy(source_row)
        state = row.get("state")
        if not isinstance(state, dict):
            raise ValueError(f"record {row.get('id')!r} has no object-valued state")
        state = sanitize_state(state)
        if previous_row is not None and not continues_sequence(
            previous_row, row, previous_state, state, max_window_gap
        ):
            history = []
        history.append(state)
        history = history[-window_size:]
        row["state"] = state
        row["narrative"] = render_with_history(history)
        output.append(row)
        previous_row = row
        previous_state = state
    return output


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as destination:
        for row in rows:
            destination.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--window-size", type=int, default=3)
    args = parser.parse_args()
    if args.in_place == (args.output is not None):
        parser.error("choose exactly one of OUTPUT or --in-place")
    if args.window_size < 1:
        parser.error("--window-size must be at least 1")

    rows = sanitize_rows(read_jsonl(args.input), window_size=args.window_size)
    if args.in_place:
        fd, temporary = tempfile.mkstemp(
            prefix=args.input.name + ".", suffix=".tmp", dir=args.input.parent
        )
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            write_jsonl(temporary_path, rows)
            os.replace(temporary_path, args.input)
        finally:
            temporary_path.unlink(missing_ok=True)
        destination = args.input
    else:
        destination = args.output
        assert destination is not None
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(destination, rows)
    print(f"Sanitized {len(rows)} rows: {destination}")


if __name__ == "__main__":
    main()
