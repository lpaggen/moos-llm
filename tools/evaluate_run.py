#!/usr/bin/env python3
"""Deterministic OOW scoring from recorded trajectories and audited findings.

Implements the scoring layer of evaluation_function_design_and_verification (1).md.
No simulator, model, network, or third-party Python dependencies are required.
See tools/evaluate_run.md for input contracts and scope.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_WEIGHTS = {
    "safety": 0.35, "compliance": 0.20, "temporal": 0.10,
    "spatial": 0.10, "manoeuvre": 0.10, "smoothness": 0.15,
}
COMPLIANCE_WEIGHTS = {
    "B_wrong_direction": 0.15, "P_port_toward_contact": 0.15,
    "D_no_action_when_required": 0.15, "B_17c": 0.15,
    "P_wrong_side_pass": 0.30, "cpa_violation": 0.30,
    "C_degrees_over_limit": 0.03, "E_role_fabrication": 0.15,
    "E_encounter_mismatch": 0.05, "E_unclassified_encounter": 0.05,
    "A_fabricated_risk": 0.03, "E_8c": 0.05,
}
EXPLANATION_CODES = {
    "E_role_fabrication", "E_encounter_mismatch", "E_unclassified_encounter",
    "A_fabricated_risk", "E_8c",
}
COMPLIANCE_CATEGORY = {
    code: "explanation" if code in EXPLANATION_CODES else "manoeuvre"
    for code in COMPLIANCE_WEIGHTS
}
ACTIONS = {"turn_right", "turn_left", "speed_up", "slow_down", "hold_course", "stop"}
Row = Mapping[str, Any]


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def heading_delta(after: float, before: float) -> float:
    return (after - before + 180.0) % 360.0 - 180.0


def _tracks(trajectory: Iterable[Row], own_ship: str) -> dict[str, list[dict[str, Any]]]:
    tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trajectory:
        vehicle = str(row["vehicle"]).strip()
        if not vehicle:
            raise ValueError("vehicle must be nonempty")
        sample = {key: _finite(row[key], key) for key in ("time", "x", "y", "heading", "speed")}
        if sample["speed"] < 0:
            raise ValueError("speed must be nonnegative (metres per second)")
        tracks[vehicle].append(sample)
    if own_ship not in tracks:
        raise ValueError(f"No trajectory for own ship {own_ship!r}")
    for vehicle, track in tracks.items():
        track.sort(key=lambda row: row["time"])
        if any(a["time"] == b["time"] for a, b in zip(track, track[1:])):
            raise ValueError(f"Duplicate trajectory time for {vehicle!r}")
    own = tracks[own_ship]
    for vehicle, track in tracks.items():
        if track[0]["time"] > own[0]["time"] or track[-1]["time"] < own[-1]["time"]:
            raise ValueError(f"Contact {vehicle!r} does not cover the own-ship recording")
    return dict(tracks)


def interp_xy(track: Sequence[Row], time: float, times: Sequence[float] | None = None) -> tuple[float, float]:
    """Interpolate a sorted track; refuse to extrapolate missing observations."""
    if not track or not track[0]["time"] <= time <= track[-1]["time"]:
        raise ValueError("Interpolation time outside recorded track")
    if times is None:
        times = [row["time"] for row in track]
    index = bisect_right(times, time) - 1
    a = track[index]
    if index == len(track) - 1:
        return a["x"], a["y"]
    b = track[index + 1]
    fraction = (time - a["time"]) / (b["time"] - a["time"])
    return (a["x"] + fraction * (b["x"] - a["x"]),
            a["y"] + fraction * (b["y"] - a["y"]))


def min_cpa_over_run(own: Sequence[Row], contact: Sequence[Row]) -> float:
    """Minimum realized range on a 1-second grid, including the final instant."""
    start, end = own[0]["time"], own[-1]["time"]
    own_times = [row["time"] for row in own]
    contact_times = [row["time"] for row in contact]
    closest = math.inf
    for index in range(math.ceil(end - start) + 1):
        time = min(start + index, end)
        ox, oy = interp_xy(own, time, own_times)
        cx, cy = interp_xy(contact, time, contact_times)
        closest = min(closest, math.hypot(cx - ox, cy - oy))
    return closest


def safety_axis(tracks: Mapping[str, Sequence[Row]], own_ship: str,
                collision_radius_m: float, safe_distance_m: float) -> dict[str, Any]:
    ranges = [min_cpa_over_run(tracks[own_ship], contact)
              for name, contact in tracks.items() if name != own_ship]
    minimum = min(ranges) if ranges else None
    return {
        "passed": minimum is None or minimum >= collision_radius_m,
        "min_cpa_m": minimum,
        "score": 1.0 if minimum is None else clamp(minimum / safe_distance_m),
    }


def path_length(track: Sequence[Row]) -> float:
    return sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"])
               for a, b in zip(track, track[1:]))


def efficiency_axes(own: Sequence[Row], start: tuple[float, float],
                    goal: tuple[float, float], nominal_speed: float,
                    reached_radius_m: float) -> tuple[dict[str, Any], dict[str, Any]]:
    straight = math.dist(start, goal)
    arrival = next((i for i, row in enumerate(own)
                    if math.hypot(row["x"] - goal[0], row["y"] - goal[1]) <= reached_radius_m), None)
    distance = path_length(own if arrival is None else own[:arrival + 1])
    duration = None if arrival is None else own[arrival]["time"] - own[0]["time"]
    time_ratio = None if duration is None else duration / (straight / nominal_speed)
    path_ratio = distance / straight
    return (
        {"arrived": arrival is not None, "time_actual_s": duration, "time_ratio": time_ratio,
         "temporal_score": 0.0 if arrival is None else (1.0 if time_ratio == 0 else clamp(1 / time_ratio))},
        {"path_length_m": distance, "path_ratio": path_ratio,
         "spatial_score": 0.0 if arrival is None else (1.0 if path_ratio == 0 else clamp(1 / path_ratio))},
    )


def manoeuvre_and_smoothness_axes(own: Sequence[Row]) -> dict[str, float]:
    heading_rates, speed_rates = [], []
    for a, b in zip(own, own[1:]):
        dt = b["time"] - a["time"]
        heading_rates.append(abs(heading_delta(b["heading"], a["heading"])) / dt)
        speed_rates.append(abs(b["speed"] - a["speed"]) / dt)
    hr = sum(heading_rates) / len(heading_rates) if heading_rates else 0.0
    sr = sum(speed_rates) / len(speed_rates) if speed_rates else 0.0
    # The document's explicit formula is linear in absolute rates, despite
    # the accompanying prose describing a quadratic control-effort penalty.
    return {"mean_abs_heading_rate": hr, "mean_abs_speed_rate": sr,
            "smoothness_score": 0.5 * clamp(1 - hr / 5.0) + 0.5 * clamp(1 - sr)}


def manoeuvre_axis_from_decisions(decision_events: Sequence[str | Row],
                                  n_contacts: int) -> dict[str, Any]:
    last_turn = last_speed = None
    count = 0
    for event in decision_events:
        action = event if isinstance(event, str) else event["action"]
        if action not in ACTIONS:
            raise ValueError(f"Unknown decision action {action!r}; normalize to the documented action vocabulary")
        if action in {"turn_right", "turn_left"}:
            count += int(last_turn is not None and last_turn != action)
            last_turn = action
        elif action in {"speed_up", "slow_down"}:
            count += int(last_speed is not None and last_speed != action)
            last_speed = action
    denominator = max(0, len(decision_events) - 1) * max(1, n_contacts)
    rate = count / denominator if denominator else 0.0
    return {"manoeuvre_count": count, "manoeuvre_rate": rate,
            "manoeuvre_score": clamp(1 - rate / 0.30)}


def _scored_axis(findings: Sequence[Row], category: str,
                 collision: bool) -> dict[str, Any]:
    if collision:
        return {"score": 0.0, "breakdown": [{"code": "collision", "deduction": -1.0}]}
    breakdown = [
        {"code": f["code"], "label": f.get("label", f["code"].replace("_", " ")),
         "at": f.get("step", "run"), "deduction": -COMPLIANCE_WEIGHTS[f["code"]]}
        for f in findings if COMPLIANCE_CATEGORY[f["code"]] == category
    ]
    return {"score": clamp(1 + sum(f["deduction"] for f in breakdown)), "breakdown": breakdown}


def compliance_axis(findings: Sequence[Row], collision: bool = False) -> dict[str, Any]:
    return _scored_axis(findings, "manoeuvre", collision)


def explanation_axis(findings: Sequence[Row], collision: bool = False) -> dict[str, Any]:
    return _scored_axis(findings, "explanation", collision)


def _canonical_findings(findings: Iterable[Row]) -> list[dict[str, Any]]:
    """One finding per (checkpoint, code); collapse duplicate risk diagnoses."""
    result: dict[tuple[Any, str], dict[str, Any]] = {}
    for finding in findings:
        code = finding["code"]
        if code not in COMPLIANCE_WEIGHTS:
            raise ValueError(f"Unknown compliance code {code!r}")
        step = finding.get("step")
        if code in {"P_wrong_side_pass", "cpa_violation"}:
            step = None
        elif step is None:
            raise ValueError(f"Checkpoint finding {code!r} requires a step")
        canonical = dict(finding)
        canonical.pop("step", None)
        if step is not None:
            canonical["step"] = step
        result[(step, code)] = canonical
    for step, code in list(result):
        if code == "A_fabricated_risk" and (step, "E_role_fabrication") in result:
            del result[(step, code)]
    return list(result.values())


def evaluate_run(trajectory: Iterable[Row], *, own_ship: str,
                 start: tuple[float, float], goal: tuple[float, float],
                 nominal_speed: float, decision_events: Sequence[str | Row],
                 findings: Sequence[Row], safe_distance_m: float = 500.0,
                 collision_radius_m: float = 15.0, reached_radius_m: float = 20.0,
                 weights: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Score a run. Decisions are chronological; findings are audited inputs.

    All distances use metres, speed m/s, time seconds, heading degrees.
    Explicit empty findings mean the caller audited the run and found no codes.
    """
    nominal_speed = _positive(nominal_speed, "nominal_speed")
    safe_distance_m = _positive(safe_distance_m, "safe_distance_m")
    collision_radius_m = _positive(collision_radius_m, "collision_radius_m")
    reached_radius_m = _finite(reached_radius_m, "reached_radius_m")
    if reached_radius_m < 0 or collision_radius_m > safe_distance_m:
        raise ValueError("Require reached_radius_m >= 0 and collision_radius_m <= safe_distance_m")
    start = tuple(_finite(v, "start coordinate") for v in start)
    goal = tuple(_finite(v, "goal coordinate") for v in goal)
    if len(start) != 2 or len(goal) != 2 or math.dist(start, goal) == 0:
        raise ValueError("start and goal must be distinct 2D points")
    w = {key: _finite(value, "weight") for key, value in
         (DEFAULT_WEIGHTS if weights is None else weights).items()}
    if w.keys() != DEFAULT_WEIGHTS.keys() or any(v < 0 for v in w.values()) or not math.isclose(sum(w.values()), 1.0):
        raise ValueError("Weights must contain the six scored axes, be nonnegative, and sum to 1")
    tracks = _tracks(trajectory, own_ship)
    own = tracks[own_ship]
    safety = safety_axis(tracks, own_ship, collision_radius_m, safe_distance_m)
    collision = not safety["passed"]
    # Always derive this once from physical clearance, never trust a supplied flag.
    audited = _canonical_findings([f for f in findings if f["code"] != "cpa_violation"])
    cpa_violation = not collision and safety["min_cpa_m"] is not None and safety["min_cpa_m"] < safe_distance_m
    if cpa_violation:
        audited.append({"code": "cpa_violation"})
    compliance = compliance_axis(audited, collision)
    compliance["findings"] = audited
    temporal, spatial = efficiency_axes(own, start, goal, nominal_speed, reached_radius_m)
    manoeuvre = manoeuvre_axis_from_decisions(decision_events, len(tracks) - 1)
    manoeuvre.update(manoeuvre_and_smoothness_axes(own))
    if collision:
        composite, verdict = 0.0, "FAIL -- collision occurred"
    elif not temporal["arrived"]:
        composite = min(0.2, w["compliance"] * compliance["score"]
                        + w["manoeuvre"] * manoeuvre["manoeuvre_score"]
                        + w["smoothness"] * manoeuvre["smoothness_score"])
        verdict = "FAIL -- did not reach the goal"
    else:
        composite = (w["safety"] * safety["score"] + w["compliance"] * compliance["score"]
                     + w["temporal"] * temporal["temporal_score"] + w["spatial"] * spatial["spatial_score"]
                     + w["manoeuvre"] * manoeuvre["manoeuvre_score"] + w["smoothness"] * manoeuvre["smoothness_score"])
        verdict = "PASS_WITH_CPA_VIOLATION" if cpa_violation else "PASS"
    return {"verdict": verdict, "composite_score": clamp(composite), "safety": safety,
            "compliance": compliance, "explanation_compliance": explanation_axis(audited, collision),
            "temporal": temporal, "spatial": spatial, "manoeuvre": manoeuvre}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectory", type=Path, help="CSV: time,vehicle,x,y,heading,speed")
    parser.add_argument("--own-ship", required=True)
    parser.add_argument("--start", nargs=2, type=float, required=True, metavar=("X", "Y"))
    parser.add_argument("--goal", nargs=2, type=float, required=True, metavar=("X", "Y"))
    parser.add_argument("--nominal-speed", type=float, required=True, help="metres per second")
    parser.add_argument("--decisions", type=Path, required=True, help="JSON array of chronological actions or action objects")
    parser.add_argument("--findings", type=Path, required=True, help="JSON array of audited {code, step} findings, including run-level codes")
    parser.add_argument("--safe-distance", type=float, default=500.0)
    parser.add_argument("--collision-radius", type=float, default=15.0)
    parser.add_argument("--reached-radius", type=float, default=20.0)
    parser.add_argument("--output", type=Path, help="Write JSON here (default: stdout)")
    args = parser.parse_args()
    try:
        decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
        findings = json.loads(args.findings.read_text(encoding="utf-8"))
        if not isinstance(decisions, list) or not isinstance(findings, list):
            raise ValueError("decisions and findings must be JSON arrays")
        with args.trajectory.open(newline="", encoding="utf-8-sig") as handle:
            result = evaluate_run(csv.DictReader(handle), own_ship=args.own_ship,
                                  start=tuple(args.start), goal=tuple(args.goal),
                                  nominal_speed=args.nominal_speed, decision_events=decisions,
                                  findings=findings, safe_distance_m=args.safe_distance,
                                  collision_radius_m=args.collision_radius, reached_radius_m=args.reached_radius)
        output = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
