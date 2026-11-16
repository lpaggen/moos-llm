#!/usr/bin/env python3
"""Render the 22 Sawada Imazu scenarios from the checked-in source table."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path


NM_M = 1852.0
NORMAL_SPEED_KN = 12.0
OVERTAKEN_SPEED_KN = 8.4
OVERTAKING_CASES = {3, 7, 15, 17, 20, 22}
# Table 4 case 16 TS1 is also 4.2 NM from the collision origin, on a
# crossing course. At 8.4 kn it reaches that origin after 30 minutes.
SLOW_TARGET_CASES = OVERTAKING_CASES | {16}
COLORS = ("red", "orange", "cyan")
# Use the full source-table geometry: own ship sails from 6 NM south to
# 6 NM north, a 12 NM route with about 30 minutes initial TCPA.
SCENARIO_SCALE = 1.0
INITIAL_TCPA_MIN = 30.0
COMPACT_APPROACH_MIN = 3.0
COMPACT_DEPARTURE_MIN = 3.0


def metres_per_second(knots: float) -> float:
    return knots * NM_M / 3600.0


def fmt(value: float) -> str:
    if abs(value) < 0.0005:
        value = 0.0
    return f"{value:.3f}".rstrip("0").rstrip(".")


def load_constellations(path: Path) -> dict[int, list[dict[str, float]]]:
    cases: dict[int, list[dict[str, float]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            case = int(row["case"])
            cases[case].append(
                {
                    "target": int(row["target"]),
                    "heading": float(row["heading_deg"]),
                    "north": float(row["north_nm"]),
                    "east": float(row["east_nm"]),
                }
            )
    if set(cases) != set(range(1, 23)):
        raise ValueError("constellation table must contain cases 1 through 22")
    return cases


def render_case(case: int, targets: list[dict[str, float]]) -> str:
    normal_speed = metres_per_second(NORMAL_SPEED_KN)
    slow_speed = metres_per_second(OVERTAKEN_SPEED_KN)
    extent = 6.0 * NM_M * SCENARIO_SCALE
    lines = [
        f"# Imazu problem {case:02d}.",
        "#",
        "# Sawada et al. (2021), Appendix 1 / Table 4 convention, rotated so",
        "# the own ship travels north: 12.0 kn normal speed, 8.4 kn for TS1",
        f"# in selected cases, and positions give about {INITIAL_TCPA_MIN:.0f} min initial TCPA.",
        "# Coordinates are real metres; headings use the nautical convention.",
        "",
        "[DEFAULT]",
        "type = ship",
        f"speed = {fmt(normal_speed)}",
        f"max_speed = {fmt(normal_speed)}",
        "avoid = false",
        "",
        "[vehicle:LLM_SHIP]",
        "color = yellow",
        "avoid = true",
        f"start = x=0, y={fmt(-extent)}, heading=0",
        f"waypoint = x=0, y={fmt(extent)}",
        f"loiter = x=0, y={fmt(extent)}",
    ]
    for target in targets:
        heading = target["heading"]
        radians = math.radians(heading)
        waypoint_x = math.sin(radians) * extent
        waypoint_y = math.cos(radians) * extent
        target_number = int(target["target"])
        lines.extend(
            [
                "",
                f"[vehicle:IMAZU_TS{target_number}]",
                f"color = {COLORS[target_number - 1]}",
            ]
        )
        if case in SLOW_TARGET_CASES and target_number == 1:
            lines.append(f"speed = {fmt(slow_speed)}")
        lines.extend(
            [
                "start = "
                f"x={fmt(target['east'] * NM_M * SCENARIO_SCALE)}, "
                f"y={fmt(target['north'] * NM_M * SCENARIO_SCALE)}, heading={fmt(heading)}",
                f"waypoint = x={fmt(waypoint_x)}, y={fmt(waypoint_y)}",
                f"loiter = x={fmt(waypoint_x)}, y={fmt(waypoint_y)}",
            ]
        )
    return "\n".join(lines) + "\n"


def render_compact_case(case: int, targets: list[dict[str, float]]) -> str:
    normal_speed = metres_per_second(NORMAL_SPEED_KN)
    slow_speed = metres_per_second(OVERTAKEN_SPEED_KN)
    approach_s = COMPACT_APPROACH_MIN * 60.0
    departure_s = COMPACT_DEPARTURE_MIN * 60.0
    own_start_y = -(normal_speed * approach_s)
    own_goal_y = normal_speed * departure_s
    lines = [
        f"# Compact Imazu problem {case:02d} for teacher-data generation.",
        "# Vessel headings and speeds match the canonical case, but every track",
        f"# is synchronized to reach the encounter origin after {COMPACT_APPROACH_MIN:.0f} minutes.",
        "",
        "[DEFAULT]",
        "type = ship",
        f"speed = {fmt(normal_speed)}",
        f"max_speed = {fmt(normal_speed)}",
        "avoid = false",
        "",
        "[vehicle:LLM_SHIP]",
        "color = yellow",
        "avoid = true",
        f"start = x=0, y={fmt(own_start_y)}, heading=0",
        f"waypoint = x=0, y={fmt(own_goal_y)}",
        f"loiter = x=0, y={fmt(own_goal_y)}",
    ]
    for target in targets:
        target_number = int(target["target"])
        heading = target["heading"] % 360.0
        speed = (
            slow_speed
            if case in SLOW_TARGET_CASES and target_number == 1
            else normal_speed
        )
        radians = math.radians(heading)
        start_x = -math.sin(radians) * speed * approach_s
        start_y = -math.cos(radians) * speed * approach_s
        goal_x = math.sin(radians) * speed * departure_s
        goal_y = math.cos(radians) * speed * departure_s
        lines.extend([
            "",
            f"[vehicle:IMAZU_TS{target_number}]",
            f"color = {COLORS[target_number - 1]}",
        ])
        if speed != normal_speed:
            lines.append(f"speed = {fmt(speed)}")
        lines.extend([
            f"start = x={fmt(start_x)}, y={fmt(start_y)}, heading={fmt(heading)}",
            f"waypoint = x={fmt(goal_x)}, y={fmt(goal_y)}",
            f"loiter = x={fmt(goal_x)}, y={fmt(goal_y)}",
        ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parents[1] / "mission/scenarios/imazu",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    table = args.root / "imazu_constellations.csv"
    cases = load_constellations(table)
    stale: list[Path] = []
    for case, targets in sorted(cases.items()):
        if args.compact:
            destination = args.root / "teacher_compact" / f"{case:02d}" / f"{case:02d}.ini"
            rendered = render_compact_case(case, targets)
        else:
            destination = args.root / f"{case:02d}" / f"{case:02d}.ini"
            rendered = render_case(case, targets)
        if args.check:
            if not destination.exists() or destination.read_text(encoding="utf-8") != rendered:
                stale.append(destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
    if stale:
        for path in stale:
            print(f"stale: {path}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
