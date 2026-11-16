#!/usr/bin/env python3
"""Render a readable report of the authoritative 22 Imazu scenarios."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mission" / "scenarios" / "imazu" / "imazu_constellations.csv"
OUTPUT = ROOT / "reports" / "IMAZU_SCENARIO_CONFIGURATIONS.md"
SLOW_TS1_CASES = {3, 7, 15, 16, 17, 20, 22}


def heading_label(value: float) -> str:
    normalized = value % 360
    if value < 0:
        return f"{normalized:.0f} ({value:.0f})"
    return f"{normalized:.0f}"


def main() -> None:
    cases: dict[int, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            cases[int(row["case"])].append(row)
    if set(cases) != set(range(1, 23)):
        raise ValueError("Expected exactly Imazu cases 1 through 22")
    if sum(map(len, cases.values())) != 50:
        raise ValueError("Expected exactly 50 target-vessel entries")

    lines = [
        "# Configuration of the 22 Imazu scenarios",
        "",
        "This report is generated from the authoritative checked-in constellation table at `mission/scenarios/imazu/imazu_constellations.csv`. Coordinates below use the simulator frame: `x` is east, `y` is north, positions are in nautical miles, and headings are nautical degrees clockwise from north. Parenthesized headings preserve the negative notation used in the source table.",
        "",
        "## Common configuration",
        "",
        "- Own ship (`LLM_SHIP`) starts at `(x=0, y=-6 NM)`, heading `000°`, at `12.0 kn` (`6.173 m/s`).",
        "- Its waypoint and loiter point are `(x=0, y=+6 NM)`, producing a 12 NM northbound route through the encounter origin.",
        "- Normal targets travel at `12.0 kn`. TS1 travels at `8.4 kn` (`4.321 m/s`) in cases 3, 7, 15, 16, 17, 20, and 22.",
        "- Targets hold their initial course and speed and have collision avoidance disabled; only own ship uses the controller under evaluation.",
        "- The geometry synchronizes the unaltered tracks at the origin after approximately 30 minutes.",
        "- The simulator's Imazu/Sawada navigation domain uses 0.5 NM (926 m) general passing clearance and 1.0 NM (1,852 m) forward/bow-crossing clearance.",
        "- Separately, the frozen thesis scoring function evaluates the safety axis against a 500 m minimum-clearance threshold. That scoring threshold is not part of the scenario geometry.",
        "- Time warp is an evaluation launch parameter and does not change these physical coordinates, speeds, CPA, or TCPA values.",
        "",
        "## Compact overview",
        "",
        "Each target is shown as `TS: start (x east, y north); heading; speed`.",
        "",
        "| Case | Targets | Target configurations |",
        "|---:|---:|---|",
    ]

    for case in range(1, 23):
        descriptions = []
        for row in cases[case]:
            target = int(row["target"])
            x = float(row["east_nm"])
            y = float(row["north_nm"])
            heading = float(row["heading_deg"])
            speed = 8.4 if case in SLOW_TS1_CASES and target == 1 else 12.0
            descriptions.append(
                f"TS{target}: ({x:+.3f}, {y:+.3f}) NM; "
                f"{heading_label(heading)}°; {speed:.1f} kn"
            )
        lines.append(f"| {case} | {len(descriptions)} | {'<br>'.join(descriptions)} |")

    lines.extend([
        "",
        "## Exact target table",
        "",
        "The generated target waypoint is 6 NM from the origin along its heading. Every target loiters at that same waypoint.",
        "",
        "| Case | Vessel | Start x (NM E) | Start y (NM N) | Heading (°) | Speed (kn) | Waypoint x (NM E) | Waypoint y (NM N) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ])
    for case in range(1, 23):
        for row in cases[case]:
            target = int(row["target"])
            heading = float(row["heading_deg"])
            radians = math.radians(heading)
            waypoint_x = math.sin(radians) * 6.0
            waypoint_y = math.cos(radians) * 6.0
            speed = 8.4 if case in SLOW_TS1_CASES and target == 1 else 12.0
            lines.append(
                f"| {case} | TS{target} | {float(row['east_nm']):.3f} | "
                f"{float(row['north_nm']):.3f} | {heading_label(heading)} | "
                f"{speed:.1f} | {waypoint_x:.3f} | {waypoint_y:.3f} |"
            )

    lines.extend([
        "",
        "## Source files",
        "",
        "- `mission/scenarios/imazu/imazu_constellations.csv`: authoritative geometry.",
        "- `mission/scenarios/imazu/NN/NN.ini`: generated simulator configuration for case `NN`.",
        "- `tools/generate_imazu_scenarios.py`: deterministic renderer used to create and verify the INI files.",
        "- `mission/scenarios/imazu/README.md`: provenance, coordinate convention, corrections, and launch notes.",
    ])
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
