#!/usr/bin/env python3
"""Interactive, method-blind terminal annotation for MAIB relevance labels."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import sys
import textwrap
from typing import Any


DEFAULT_LABELS = Path(
    "reports/maib_state_retrieval_benchmark/relevance_labels.csv"
)
REQUIRED_FIELDS = (
    "annotation_id",
    "state_id",
    "navigation_state",
    "card_finding",
    "transferable_lesson",
    "non_transfer_conditions",
    "supporting_quotations",
    "relevance_label",
    "reviewer_notes",
)


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def apply(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def heading(self, text: str) -> str:
        return self.apply("1;36", text)

    def identifier(self, text: str) -> str:
        return self.apply("1;35", text)

    def relevant(self, text: str) -> str:
        return self.apply("1;32", text)

    def partial(self, text: str) -> str:
        return self.apply("1;33", text)

    def irrelevant(self, text: str) -> str:
        return self.apply("1;31", text)

    def faint(self, text: str) -> str:
        return self.apply("2", text)


def read_sheet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        missing = set(REQUIRED_FIELDS) - set(fields)
        if missing:
            raise ValueError(f"missing CSV columns: {sorted(missing)}")
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError(f"annotation sheet is empty: {path}")
    seen: set[str] = set()
    for line_number, row in enumerate(rows, start=2):
        identifier = str(row.get("annotation_id") or "").strip()
        if not identifier or identifier in seen:
            raise ValueError(
                f"missing or duplicate annotation_id at {path}:{line_number}"
            )
        seen.add(identifier)
        label = str(row.get("relevance_label") or "").strip()
        if label not in {"", "0", "1", "2"}:
            raise ValueError(
                f"invalid relevance_label at {path}:{line_number}: {label!r}"
            )
        row["relevance_label"] = label
    return fields, rows


def write_sheet_atomic(
    path: Path, fields: list[str], rows: list[dict[str, str]]
) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def parse_json_list(raw: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [raw] if raw else []
    return value if isinstance(value, list) else [value]


def wrapped(
    text: str,
    width: int,
    indent: str = "",
    continuation_indent: str | None = None,
) -> str:
    clean = " ".join(str(text).split())
    return textwrap.fill(
        clean,
        width=max(30, width),
        initial_indent=indent,
        subsequent_indent=(
            indent if continuation_indent is None else continuation_indent
        ),
    )


def section(title: str, body: str, width: int, style: Style) -> list[str]:
    return [style.heading(title), wrapped(body, width), ""]


def render_row(
    row: dict[str, str],
    *,
    position: int,
    total: int,
    completed: int,
    width: int,
    style: Style,
) -> str:
    content_width = min(max(width - 2, 60), 110)
    lines = [
        style.identifier(
            f"MAIB relevance annotation — {row['annotation_id']} / "
            f"{row['state_id']}"
        ),
        style.faint(
            f"Row {position + 1} of {total}  •  completed {completed}/{total} "
            f"({completed / total:.0%})"
        ),
        "=" * content_width,
        "",
    ]
    lines.extend(
        section(
            "NAVIGATION STATE",
            row.get("navigation_state", ""),
            content_width,
            style,
        )
    )
    lines.extend(
        section(
            "INVESTIGATOR FINDING",
            row.get("card_finding", ""),
            content_width,
            style,
        )
    )
    lines.extend(
        section(
            "TRANSFERABLE LESSON",
            row.get("transferable_lesson", ""),
            content_width,
            style,
        )
    )

    lines.append(style.heading("DO NOT TRANSFER WHEN"))
    limitations = parse_json_list(row.get("non_transfer_conditions", ""))
    if not limitations:
        lines.append(style.faint("  (none supplied)"))
    for item in limitations:
        lines.append(wrapped(str(item), content_width, "  • ", "    "))
    lines.append("")

    lines.append(style.heading("SUPPORTING QUOTATIONS"))
    quotations = parse_json_list(row.get("supporting_quotations", ""))
    if not quotations:
        lines.append(style.faint("  (none supplied)"))
    for quotation in quotations:
        if isinstance(quotation, dict):
            page = quotation.get("page", "?")
            text = quotation.get("quote", "")
            lines.append(
                wrapped(
                    f"Page {page}: “{text}”", content_width, "  • ", "    "
                )
            )
        else:
            lines.append(wrapped(str(quotation), content_width, "  • ", "    "))
    lines.extend(["", "-" * content_width])

    existing = row.get("relevance_label", "")
    if existing:
        lines.append(f"Current label: {style.identifier(existing)}")
    note = str(row.get("reviewer_notes") or "").strip()
    if note:
        lines.append(wrapped(f"Current note: {note}", content_width))
    lines.extend(
        [
            f"{style.irrelevant('[0] Irrelevant')}   "
            f"{style.partial('[1] Partially relevant')}   "
            f"{style.relevant('[2] Relevant')}",
            style.faint(
                "Commands: 0/1/2 = label + next  •  b = back  •  s = skip  "
                "•  n = note  •  q = save and quit"
            ),
        ]
    )
    return "\n".join(lines)


def next_unlabelled(
    rows: list[dict[str, str]], start: int = 0
) -> int | None:
    for index in range(max(0, start), len(rows)):
        if not rows[index].get("relevance_label", "").strip():
            return index
    for index in range(0, max(0, start)):
        if not rows[index].get("relevance_label", "").strip():
            return index
    return None


def progress(rows: list[dict[str, str]]) -> int:
    return sum(row.get("relevance_label", "").strip() in {"0", "1", "2"} for row in rows)


def annotation_loop(
    path: Path,
    fields: list[str],
    rows: list[dict[str, str]],
    *,
    start_index: int,
    clear_screen: bool,
    style: Style,
) -> None:
    index = start_index
    while True:
        completed = progress(rows)
        if completed == len(rows):
            write_sheet_atomic(path, fields, rows)
            if clear_screen and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(style.relevant(f"Complete: all {len(rows)} pairs are labelled."))
            print("Run the benchmark scorer next:")
            print(
                "  .venv/bin/python tools/evaluate_maib_state_retrieval.py score"
            )
            return

        if clear_screen and sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        terminal_width = shutil.get_terminal_size(fallback=(100, 30)).columns
        print(
            render_row(
                rows[index],
                position=index,
                total=len(rows),
                completed=completed,
                width=terminal_width,
                style=style,
            )
        )
        try:
            command = input("Your judgment [0/1/2]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nSaved. Resume with the same command.")
            return

        if command in {"0", "1", "2"}:
            rows[index]["relevance_label"] = command
            write_sheet_atomic(path, fields, rows)
            following = next_unlabelled(rows, index + 1)
            if following is None:
                continue
            index = following
        elif command == "b":
            index = max(0, index - 1)
        elif command == "s" or command == "":
            index = (index + 1) % len(rows)
        elif command == "n":
            try:
                note = input("Optional reviewer note (blank clears it): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nNote unchanged.")
                continue
            rows[index]["reviewer_notes"] = note
            write_sheet_atomic(path, fields, rows)
        elif command == "q":
            write_sheet_atomic(path, fields, rows)
            print(
                f"Saved {progress(rows)}/{len(rows)} labels to {path}. "
                "Run the same command to resume."
            )
            return
        elif command in {"?", "h", "help"}:
            print(
                "\n2 = applicable mechanism and conditions satisfied\n"
                "1 = related mechanism, but important circumstances differ/are unknown\n"
                "0 = no applicable reasoning for this state\n"
            )
            input("Press Enter to continue...")
        else:
            print("Enter 0, 1, 2, b, s, n, q, or ?. ")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    result.add_argument(
        "--start",
        help="start at a particular annotation ID (for example P042)",
    )
    result.add_argument(
        "--review-all",
        action="store_true",
        help="start at the first row even when it is already labelled",
    )
    result.add_argument(
        "--no-clear",
        action="store_true",
        help="do not clear the terminal between pairs",
    )
    result.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colours",
    )
    result.add_argument(
        "--show-next",
        action="store_true",
        help="print the next pair and exit without editing the CSV",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    fields, rows = read_sheet(args.labels)
    if args.start:
        matches = [
            index
            for index, row in enumerate(rows)
            if row["annotation_id"].lower() == args.start.lower()
        ]
        if not matches:
            raise ValueError(f"unknown annotation ID: {args.start}")
        start_index = matches[0]
    elif args.review_all:
        start_index = 0
    else:
        candidate = next_unlabelled(rows)
        if candidate is None:
            start_index = 0
        else:
            start_index = candidate

    style = Style(
        enabled=(
            not args.no_color
            and "NO_COLOR" not in os.environ
            and sys.stdout.isatty()
        )
    )
    if args.show_next:
        print(
            render_row(
                rows[start_index],
                position=start_index,
                total=len(rows),
                completed=progress(rows),
                width=shutil.get_terminal_size(fallback=(100, 30)).columns,
                style=style,
            )
        )
        return
    annotation_loop(
        args.labels,
        fields,
        rows,
        start_index=start_index,
        clear_screen=not args.no_clear,
        style=style,
    )


if __name__ == "__main__":
    main()
