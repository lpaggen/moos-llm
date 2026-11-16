#!/usr/bin/env python3
"""Report observable OOW score components from baseline telemetry.

No command stream or compliance findings are invented. Full composite scores
remain null; interpolated arrivals are reported separately from the evaluator's
recorded-row arrival policy.
"""
from __future__ import annotations

import argparse
import configparser
import csv
import json
import math
from pathlib import Path
import statistics

try:
    from tools.evaluate_run import (
        _tracks, efficiency_axes, interp_xy, manoeuvre_and_smoothness_axes,
        min_cpa_over_run, path_length,
    )
except ModuleNotFoundError:
    from evaluate_run import (
        _tracks, efficiency_axes, interp_xy, manoeuvre_and_smoothness_axes,
        min_cpa_over_run, path_length,
    )


def point(value):
    fields = dict(part.strip().split("=", 1) for part in value.split(","))
    return float(fields["x"]), float(fields["y"])


def event_log_path(case_dir):
    for name in ("audit.jsonl", "telemetry.jsonl"):
        path = case_dir / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"{case_dir}: expected audit.jsonl or telemetry.jsonl")


def case_log_paths(run):
    paths = []
    for case_dir in run.glob("case_*"):
        if not case_dir.is_dir():
            continue
        try:
            paths.append(event_log_path(case_dir))
        except FileNotFoundError:
            continue
    return sorted(paths, key=lambda path: int(path.parent.name.split("_")[1]))


def load_events(case_dir):
    path = event_log_path(case_dir)
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return path, events


def run_time_warp(case_dir):
    """Return the launch warp recorded for a case, defaulting to real time."""
    manifest = case_dir.parent / "manifest.json"
    if not manifest.is_file():
        return 1.0
    value = json.loads(manifest.read_text()).get("time_warp", 1.0)
    return max(1.0, float(value))


def timed_bridge_states(events, *, exclude_decision_pauses=False, time_warp=1.0):
    """Yield ready bridge states with an active-simulation timestamp.

    uSimMarine stops moving while lock-step inference is in progress, but the
    MOOS clock continues to advance.  A resume event records the wall duration
    of the pause.  At warp ``w`` that contaminates subsequent MOOS timestamps
    by ``pause_wall_seconds * w``.  Subtracting the cumulative contamination
    produces the zero-inference-latency timeline intended by lock-step runs.
    """
    cumulative_pause_sim_s = 0.0
    rows = []
    for event in events:
        if event.get("event") == "simulation_resumed_after_decision":
            if exclude_decision_pauses:
                cumulative_pause_sim_s += (
                    float(event.get("paused_wall_duration_s") or 0.0) * time_warp
                )
            continue
        if event.get("event") != "bridge_snapshot":
            continue
        state = event["state"]
        if state.get("ready") is False:
            continue
        rows.append((event, state, cumulative_pause_sim_s))
    return rows


def arrival_on_polyline(own, goal, radius):
    """First circle entry on a linear segment; this is an estimate between rows."""
    sailed = 0.0
    for i, a in enumerate(own):
        if math.hypot(a["x"] - goal[0], a["y"] - goal[1]) <= radius:
            return a["time"], sailed
        if i + 1 == len(own):
            break
        b = own[i + 1]
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        px, py = a["x"] - goal[0], a["y"] - goal[1]
        aa = dx * dx + dy * dy
        bb = 2 * (px * dx + py * dy)
        cc = px * px + py * py - radius * radius
        disc = bb * bb - 4 * aa * cc
        if aa > 0 and disc >= 0:
            fraction = (-bb - math.sqrt(disc)) / (2 * aa)
            if 0 <= fraction <= 1:
                return a["time"] + fraction * (b["time"] - a["time"]), sailed + fraction * math.sqrt(aa)
        sailed += math.sqrt(aa)
    return None


def analyze(case_dir, safe_distance=500.0, radius=20.0, *, exclude_decision_pauses=False):
    source, events = load_events(case_dir)
    warp = run_time_warp(case_dir)
    timed_states = timed_bridge_states(
        events,
        exclude_decision_pauses=exclude_decision_pauses,
        time_warp=warp,
    )
    states = [state for _, state, _ in timed_states]
    config = configparser.ConfigParser()
    config.read(case_dir / "scenario.ini")
    own_config = config["vehicle:LLM_SHIP"]
    start, goal = point(own_config["start"]), point(own_config["waypoint"])
    nominal = float(own_config["speed"])
    # Relative simulation time avoids loss of precision from huge warped epochs.
    origin = states[0]["moos_time"] - timed_states[0][2]
    own_by_time = {}
    contacts = {}
    ages = []
    for _, state, pause_sim_s in timed_states:
        time = state["moos_time"] - pause_sim_s - origin
        own_by_time[time] = dict(time=time, vehicle="LLM_SHIP", **state["nav"])
        for contact in state.get("contacts", []):
            if contact.get("ready") is False or not contact.get("nav"):
                continue
            contact_time = contact["moos_time"] - pause_sim_s - origin
            ages.append(time - contact_time)
            contacts.setdefault(contact["name"], {})[contact_time] = dict(
                time=contact_time, vehicle=contact["name"], **contact["nav"])
    own = sorted(own_by_time.values(), key=lambda row: row["time"])
    temporal, spatial = efficiency_axes(own, start, goal, nominal, radius)
    smooth = manoeuvre_and_smoothness_axes(own)
    full_rows = list(own)
    per_contact = {}
    for name, observations in contacts.items():
        track = sorted(observations.values(), key=lambda row: row["time"])
        full_rows.extend(track)
        lo, hi = max(own[0]["time"], track[0]["time"]), min(own[-1]["time"], track[-1]["time"])
        minimum = None
        if lo <= hi:
            clipped = [dict(time=lo, x=interp_xy(own, lo)[0], y=interp_xy(own, lo)[1])]
            clipped.extend(row for row in own if lo < row["time"] < hi)
            if hi > lo:
                x, y = interp_xy(own, hi)
                clipped.append(dict(time=hi, x=x, y=y))
            minimum = min_cpa_over_run(clipped, track)
        per_contact[name] = {"minimum_interpolated_range_m": minimum,
                             "overlap_start_s": lo, "overlap_end_s": hi,
                             "distinct_observations": len(track)}
    try:
        _tracks(full_rows, "LLM_SHIP")
        coverage_error = None
    except ValueError as error:
        coverage_error = str(error)
    ranges = [state["minimum_observed_range"] for state in states
              if state.get("minimum_observed_range") is not None]
    logged_min = min(ranges) if ranges else None
    estimated = arrival_on_polyline(own, goal, radius)
    estimate = {"arrived": estimated is not None, "time_actual_s": None,
                "temporal_score": 0.0, "spatial_score": 0.0, "path_length_m": None}
    if estimated:
        duration, distance = estimated
        baseline_distance = math.dist(start, goal)
        estimate.update(time_actual_s=duration, path_length_m=distance,
                        temporal_score=min(1.0, baseline_distance / nominal / duration) if duration else 1.0,
                        spatial_score=min(1.0, baseline_distance / distance) if distance else 1.0)
    status = json.loads((case_dir / "status.json").read_text())
    return {
        "case": int(case_dir.name.split("_")[1]), "source": str(source),
        "time_warp": warp,
        "decision_pause_simulated_s_excluded": (
            timed_states[-1][2] - timed_states[0][2]
            if exclude_decision_pauses else 0.0
        ),
        "mission_complete": status["mission_complete"], "timed_out": status["timed_out"],
        "samples": len(own), "median_sample_interval_s": statistics.median(b["time"] - a["time"] for a, b in zip(own, own[1:])),
        "recording_duration_s": own[-1]["time"], "nominal_speed_mps": nominal,
        "initial_distance_from_mission_start_m": math.dist(start, (own[0]["x"], own[0]["y"])),
        "minimum_logged_range_m": logged_min,
        "logged_range_below_safe_distance": logged_min is not None and logged_min < safe_distance,
        "logged_range_below_collision_radius": logged_min is not None and logged_min < 15.0,
        "safety_score_from_logged_range": min(1.0, logged_min / safe_distance) if logged_min is not None else None,
        "contact_overlap_ranges": per_contact, "full_contact_coverage_error": coverage_error,
        "maximum_contact_report_age_s": max(ages) if ages else None,
        "closest_recorded_goal_distance_m": min(math.hypot(row["x"] - goal[0], row["y"] - goal[1]) for row in own),
        "temporal_recorded_rows": temporal, "spatial_recorded_rows": spatial,
        "interpolated_goal_crossing_estimate": estimate, "smoothness": smooth,
        "full_recorded_path_m": path_length(own),
        "manoeuvre": None, "compliance": None, "explanation_compliance": None,
        "composite_score": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--safe-distance", type=float, default=500.0)
    parser.add_argument("--reached-radius", type=float, default=20.0)
    args = parser.parse_args()
    results = [analyze(path.parent, args.safe_distance, args.reached_radius)
               for path in case_log_paths(args.run)]
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {
        "run": str(args.run), "cases": len(results),
        "safe_distance_m": args.safe_distance, "reached_radius_m": args.reached_radius,
        "completed": sum(row["mission_complete"] for row in results),
        "timed_out_cases": [row["case"] for row in results if row["timed_out"]],
        "below_safe_distance_cases": [row["case"] for row in results if row["logged_range_below_safe_distance"]],
        "below_collision_radius_cases": [row["case"] for row in results if row["logged_range_below_collision_radius"]],
        "strict_recorded_row_arrivals": sum(row["temporal_recorded_rows"]["arrived"] for row in results),
        "interpolated_arrivals": sum(row["interpolated_goal_crossing_estimate"]["arrived"] for row in results),
        "limitations": ["Missing command stream for decision-based manoeuvre scoring",
                        "No audited physical compliance findings; explanation is not required for the composite",
                        "Contact tracks do not cover the full recording; overlap clearances are partial",
                        "Recording starts after mission start; efficiencies omit unrecorded initial travel",
                        "Interpolated arrivals assume linear motion between observations"],
    }
    (args.output / "components.json").write_text(json.dumps({"summary": summary, "cases": results}, indent=2, allow_nan=False) + "\n")
    with (args.output / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case", "mission_complete", "timed_out", "logged_min_range_m", "recorded_row_arrival", "interpolated_arrival", "estimated_temporal", "estimated_spatial", "smoothness", "composite"])
        for row in results:
            est = row["interpolated_goal_crossing_estimate"]
            writer.writerow([row["case"], row["mission_complete"], row["timed_out"], row["minimum_logged_range_m"], row["temporal_recorded_rows"]["arrived"], est["arrived"], est["temporal_score"], est["spatial_score"], row["smoothness"]["smoothness_score"], ""])
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
