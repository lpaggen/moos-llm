#!/usr/bin/env python3
"""
observations.py

Reduce bridge-state JSONL to a compact factual navigation record:

- own-ship MOOS navigation state
- mission waypoint/objective
- contact motion state
- deterministic relative geometry and track history

Explicitly excludes:
- candidate/allowed actions
- encounter, risk, role, and rule labels
- non-navigational current-action internals
- debug/raw-report strings
- readiness plumbing and per-contact MOOS timestamps
- collision/mission bookkeeping

Accepted input rows:
1) {"bridge_state": {...}}             # your extracted bridge_states.jsonl
2) {"event":"bridge_snapshot","state":{...}}  # original run logs

Output:
{"id":..., "cycle_id":..., "state":{...}}
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional


COLLISION_RISK_LEVELS = {"medium", "high", "critical"}

# The simulator normally refreshes usable contact data within five seconds.
# Five to sixty seconds (twelve missed freshness windows) is stale but may
# still support an already-established encounter relationship. Beyond sixty
# seconds the motion solution is treated as unreliable.
FRESH_TRACK_MAX_AGE_S = 5.0
RELIABLE_TRACK_MAX_AGE_S = 60.0
REACQUIRED_TRACK_MAX_AGE_S = 1.0
REACQUISITION_MIN_JUMP_M = 100.0

def r1(x):
    return round(float(x), 1) if x is not None else None


def r2(x):
    return round(float(x), 2) if x is not None else None


def r0(x):
    return int(round(float(x))) if x is not None else None


def normalized_speed(x):
    """Preserve simulator speeds in their native metres-per-second unit."""
    return r2(x)


def clean_none(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: clean_none(v)
            for k, v in obj.items()
            if v is not None
        }
    if isinstance(obj, list):
        return [clean_none(v) for v in obj]
    return obj


def encounter_rules(
    encounter_type: Optional[str],
    risk: Optional[str],
    relative_bearing: Optional[float],
    *,
    in_sight: bool,
    visibility_condition: str,
    active_encounter: Optional[str] = None,
) -> tuple[str, list[int], list[int]]:
    """Rebuild a consistent per-contact rule split from deterministic facts."""
    standing = {2, 5, 6, 7}
    active: set[int] = set()
    role = "not_applicable"
    collision_risk = risk in COLLISION_RISK_LEVELS

    if visibility_condition == "restricted" and not in_sight:
        active.add(19)
        role = "restricted_visibility"
    elif in_sight:
        standing.add(11)
        effective_encounter = active_encounter or encounter_type
        if collision_risk:
            active.add(8)
        if effective_encounter == "we_are_overtaking_contact":
            active.update({13, 16})
            role = "give_way"
        elif effective_encounter == "contact_overtaking_us":
            active.add(13)
            role = "stand_on"
        elif effective_encounter == "head_on" and (collision_risk or active_encounter):
            active.update({8, 14})
            role = "both_give_way"
        elif effective_encounter == "crossing" and (collision_risk or active_encounter):
            active.update({8, 15})
            bearing = float(relative_bearing or 0.0) % 360.0
            if 0.0 < bearing < 112.5:
                active.add(16)
                role = "give_way"
            elif 247.5 < bearing < 360.0:
                active.add(17)
                role = "stand_on"

    return role, sorted(standing), sorted(active)


def track_quality(age_s: Any) -> str:
    """Classify observation confidence using the documented simulator windows."""
    try:
        age = float(age_s or 0.0)
    except (TypeError, ValueError):
        return "unreliable"
    if age <= FRESH_TRACK_MAX_AGE_S:
        return "fresh"
    if age <= RELIABLE_TRACK_MAX_AGE_S:
        return "stale"
    return "unreliable"


def _relative_bearing(observer: Dict[str, Any], target: Dict[str, Any]) -> Optional[float]:
    try:
        dx = float(target["x"]) - float(observer["x"])
        dy = float(target["y"]) - float(observer["y"])
        absolute = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
        return (absolute - float(observer["heading"]) + 360.0) % 360.0
    except (KeyError, TypeError, ValueError):
        return None


def _colregs_vessel_type(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"ship", "motor", "power", "power_driven"}:
        return "power_driven"
    return normalized or None


def apply_encounter_persistence(
    bridge_state: Dict[str, Any],
    active_encounters: Dict[str, str],
    vessel_types: Optional[Dict[str, str]] = None,
    track_states: Optional[Dict[str, Dict[str, Any]]] = None,
    clear_range_m: float = 150.0,
    fresh_after_s: float = 5.0,
) -> Dict[str, Any]:
    """Persist encounter episodes and track metadata across successive samples."""
    state = copy.deepcopy(bridge_state)
    nav = state.get("nav") if isinstance(state.get("nav"), dict) else {}
    vessel_types = vessel_types if vessel_types is not None else {}
    track_states = track_states if track_states is not None else {}
    context = state.get("colregs_context")
    context_encounters = (
        context.get("encounters", []) if isinstance(context, dict) else []
    )
    assessments = {
        item.get("contact"): item
        for item in context_encounters
        if isinstance(item, dict) and item.get("contact")
    }
    present: set[str] = set()

    for contact in state.get("contacts") or []:
        if not isinstance(contact, dict) or not contact.get("name"):
            continue
        name = str(contact["name"])
        present.add(name)
        contact_nav = contact.get("nav") if isinstance(contact.get("nav"), dict) else {}
        age = float(contact.get("age_s") or 0.0)
        previous_track = track_states.get(name)
        generation = int((previous_track or {}).get("generation", 0))
        reacquired = False
        if previous_track is not None:
            coordinates = (
                previous_track.get("x"), previous_track.get("y"),
                contact_nav.get("x"), contact_nav.get("y"),
            )
            previous_time = previous_track.get("observed_at_s")
            observed_time = state.get("moos_time")
            if all(value is not None for value in coordinates):
                jump = math.hypot(
                    float(coordinates[2]) - float(coordinates[0]),
                    float(coordinates[3]) - float(coordinates[1]),
                )
                elapsed = max(
                    0.0,
                    float(observed_time or 0.0) - float(previous_time or 0.0),
                )
                reported_speed = max(
                    float(previous_track.get("speed") or 0.0),
                    float(contact_nav.get("speed") or 0.0),
                )
                plausible_jump = max(
                    REACQUISITION_MIN_JUMP_M,
                    elapsed * (reported_speed + 5.0),
                )
                reacquired = (
                    float(previous_track.get("age_s") or 0.0) > FRESH_TRACK_MAX_AGE_S
                    and age <= REACQUIRED_TRACK_MAX_AGE_S
                    and jump > plausible_jump
                )
        if reacquired:
            generation += 1
            active_encounters.pop(name, None)
            vessel_types.pop(name, None)
            contact["_track_reacquired"] = True
        contact["_track_generation"] = generation
        contact["_track_quality"] = track_quality(age)

        assessment = assessments.get(name, {})
        reported_type = assessment.get("contact_vessel_type")
        if not reported_type or reported_type == "unknown":
            reported_type = _colregs_vessel_type(contact.get("vessel_type"))
        if reported_type and reported_type != "unknown":
            vessel_types[name] = str(reported_type)
        if name in vessel_types:
            contact["_colregs_vessel_type"] = vessel_types[name]

        encounter = contact.get("encounter_type")
        risk = contact.get("risk")
        established = encounter in {
            "we_are_overtaking_contact", "contact_overtaking_us"
        } or (
            encounter in {"head_on", "crossing"}
            and risk in COLLISION_RISK_LEVELS
        )
        if name not in active_encounters and established:
            active_encounters[name] = str(encounter)

        track_states[name] = {
            "x": contact_nav.get("x"),
            "y": contact_nav.get("y"),
            "speed": contact_nav.get("speed"),
            "age_s": age,
            "observed_at_s": state.get("moos_time"),
            "generation": generation,
        }

        latched = active_encounters.get(name)
        if latched is None:
            continue

        closing = float(contact.get("closing_speed") or 0.0)
        distance = float(contact.get("range") or 0.0)
        if latched in {"we_are_overtaking_contact", "contact_overtaking_us"}:
            bearing = (
                contact.get("relative_bearing")
                if latched == "we_are_overtaking_contact"
                else _relative_bearing(contact_nav, nav)
            )
            abaft = bearing is not None and 112.5 < float(bearing) < 247.5
            resolved = (
                age <= fresh_after_s
                and abaft
                and closing < -0.05
                and distance >= clear_range_m
            )
        else:
            resolved = (
                age <= fresh_after_s
                and risk not in COLLISION_RISK_LEVELS
                and closing <= 0.0
                and contact.get("projection_status") == "no_future_cpa"
            )

        if resolved:
            active_encounters.pop(name, None)
        else:
            contact["encounter_type"] = latched
            contact["_active_encounter"] = latched

    for missing in set(active_encounters) - present:
        active_encounters.pop(missing, None)
    return state


def compact_state(bs: Dict[str, Any]) -> Dict[str, Any]:
    nav = bs.get("nav") if isinstance(bs.get("nav"), dict) else {}

    # The mission objective is the fixed destination. next_waypoint may be an
    # intermediate route point (or a controller target while avoiding) and
    # must never be presented to the OOW as though the destination changed.
    objective = bs.get("mission_objective")
    if not isinstance(objective, dict):
        objective = bs.get("next_waypoint")
    if not isinstance(objective, dict):
        objective = {}

    objective_geometry: Dict[str, Any] = {}
    if all(isinstance(value, (int, float)) for value in (
        nav.get("x"), nav.get("y"), nav.get("heading"),
        objective.get("x"), objective.get("y"),
    )):
        dx = float(objective["x"]) - float(nav["x"])
        dy = float(objective["y"]) - float(nav["y"])
        bearing = math.degrees(math.atan2(dx, dy)) % 360.0
        heading_error = (bearing - float(nav["heading"]) + 180.0) % 360.0 - 180.0
        objective_geometry = {
            "distance_m": r1(math.hypot(dx, dy)),
            "bearing_deg": r1(bearing),
            "heading_error_deg": r1(heading_error),
        }

    colregs = bs.get("colregs_context") if isinstance(bs.get("colregs_context"), dict) else {}
    state: Dict[str, Any] = {
        # Retain one state-level clock so downstream renderers can express
        # temporal changes over a real elapsed interval.  The name is neutral
        # because this is evidence, not a COLREG conclusion.
        "observed_at_s": r2(bs.get("moos_time")),
        "own_ship": {
            "name": bs.get("vehicle"),
            "vessel_type": bs.get("vessel_type"),
            "x": r1(nav.get("x")),
            "y": r1(nav.get("y")),
            "heading": r1(nav.get("heading")),
            "speed": normalized_speed(nav.get("speed")),
            "target_speed": r2(bs.get("target_speed")),
            "recovery_speed": r2(bs.get("recovery_speed")),
            "movement_status": bs.get("movement_status"),
            "stopped": bs.get("stopped"),
            "paused": bs.get("paused"),
            "colregs_vessel_type": colregs.get("own_vessel_type"),
        },
            "mission": {
                "x": r1(objective.get("x")),
                "y": r1(objective.get("y")),
                **objective_geometry,
            },
        "contacts": [],
    }

    current_action = bs.get("current_action")
    temporary_waypoint = bs.get("temporary_waypoint")
    if (
        bs.get("action_underway")
        and isinstance(current_action, dict)
        and current_action.get("action") == "alter_course"
    ):
        maneuver: Dict[str, Any] = {
            "action": "alter_course",
            "degrees": r1(current_action.get("degrees")),
            "temporary_waypoint_active": isinstance(temporary_waypoint, dict),
        }
        if isinstance(temporary_waypoint, dict):
            maneuver["temporary_waypoint"] = {
                "x": r1(temporary_waypoint.get("x")),
                "y": r1(temporary_waypoint.get("y")),
            }
        state["current_maneuver"] = clean_none(maneuver)

    assumptions = colregs.get("assumptions")
    if isinstance(assumptions, dict):
        state["conditions"] = {
            key: assumptions[key]
            for key in ("visibility_condition", "visibility_range_m",
                        "narrow_channel", "traffic_separation_scheme")
            if key in assumptions
        }

    for c in bs.get("contacts") or []:
        if not isinstance(c, dict):
            continue

        cnav = c.get("nav") if isinstance(c.get("nav"), dict) else {}
        quality = c.get("_track_quality", track_quality(c.get("age_s")))

        contact = {
            "name": c.get("name"),
            "vessel_type": c.get("vessel_type"),
            "colregs_vessel_type": c.get("_colregs_vessel_type"),
            "x": r1(cnav.get("x")),
            "y": r1(cnav.get("y")),
            "heading": r1(cnav.get("heading")),
            "speed": normalized_speed(cnav.get("speed")),
            "range_m": r0(c.get("range")),
            "relative_bearing_deg": r1(c.get("relative_bearing")),
            "sector": c.get("sector"),
            "closing_speed": normalized_speed(c.get("closing_speed")),
            "projection_status": c.get("projection_status"),
            "track_intersection": c.get("track_intersection"),
            "age_s": r2(c.get("age_s")),
            "track_quality": quality,
            "track_generation": c.get("_track_generation", 0),
            "track_reacquired": c.get("_track_reacquired", False),
        }

        if c.get("projection_status") in {"valid", "beyond_planning_horizon"}:
            contact["cpa_distance_m"] = r0(c.get("cpa_distance"))
            contact["tcpa_s"] = r1(c.get("tcpa_s"))

        state["contacts"].append(clean_none(contact))

    return clean_none(state)


def get_bridge_state(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if isinstance(row.get("bridge_state"), dict):
        return row["bridge_state"]

    if row.get("event") == "bridge_snapshot" and isinstance(row.get("state"), dict):
        return row["state"]

    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_jsonl")
    ap.add_argument("output_jsonl")
    args = ap.parse_args()

    n_in = 0
    n_out = 0
    active_encounters: Dict[str, str] = {}
    vessel_types: Dict[str, str] = {}
    track_states: Dict[str, Dict[str, Any]] = {}
    previous_source: Optional[str] = None
    previous_cycle: Any = None

    with open(args.input_jsonl, "r", encoding="utf-8") as fin, \
         open(args.output_jsonl, "w", encoding="utf-8") as fout:

        for line in fin:
            if not line.strip():
                continue
            n_in += 1

            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(row, dict):
                continue

            bs = get_bridge_state(row)
            if bs is None:
                continue

            source = row.get("source_file") or str(Path(args.input_jsonl).resolve())
            cycle = row.get("cycle_id")
            if source != previous_source or (
                previous_cycle is not None
                and cycle is not None
                and float(cycle) <= float(previous_cycle)
            ):
                active_encounters.clear()
                vessel_types.clear()
                track_states.clear()
            bs = apply_encounter_persistence(
                bs, active_encounters, vessel_types, track_states
            )

            out = {
                "id": row.get("id"),
                "cycle_id": row.get("cycle_id"),
                "source_file": source,
                "state": compact_state(bs),
            }
            fout.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n")
            n_out += 1
            previous_source = source
            previous_cycle = cycle

    print(f"Input rows:  {n_in}")
    print(f"Output rows: {n_out}")
    print(f"Wrote:       {Path(args.output_jsonl).resolve()}")


if __name__ == "__main__":
    main()
