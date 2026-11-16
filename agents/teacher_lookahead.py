"""Privileged candidate-trajectory projections for teacher data generation.

This module is deliberately not part of the runtime controller.  It supplies a
Luna teacher with a compact comparison of the actions the student can execute.
Targets are propagated at constant course and speed; own ship uses the
simulator's configured turn-rate and acceleration limits.  The result is an
advisory projection, not a command or a deterministic action override.
"""

from __future__ import annotations

import math
from typing import Any


COURSE_CHANGES_DEG = (-60.0, -45.0, -30.0, 30.0, 45.0, 60.0)


def _number(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _bearing(x: float, y: float, target: dict[str, Any] | None) -> float | None:
    if not isinstance(target, dict):
        return None
    tx, ty = target.get("x"), target.get("y")
    if not isinstance(tx, (int, float)) or not isinstance(ty, (int, float)):
        return None
    if math.isclose(float(tx), x) and math.isclose(float(ty), y):
        return None
    return math.degrees(math.atan2(float(tx) - x, float(ty) - y)) % 360.0


def _advance(x: float, y: float, heading: float, speed: float, dt: float) -> tuple[float, float]:
    radians = math.radians(heading)
    return x + speed * math.sin(radians) * dt, y + speed * math.cos(radians) * dt


def _turn_toward(heading: float, target: float, maximum_change: float) -> float:
    error = (target - heading + 180.0) % 360.0 - 180.0
    change = max(-maximum_change, min(maximum_change, error))
    return (heading + change) % 360.0


def _classification(
    distance_m: float, critical_m: float, minimum_m: float, margin_m: float
) -> str:
    if distance_m < critical_m:
        return "critical"
    if distance_m < minimum_m:
        return "unsafe"
    if distance_m < minimum_m + margin_m:
        return "marginal"
    return "safe"


def _candidate_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        candidate.get("action"),
        float(candidate.get("degrees") or 0.0),
        float(candidate.get("speed_mps") or 0.0),
    )


def _candidates(
    state: dict[str, Any],
    maneuver_distance_m: float,
    additional_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    own = state.get("own_ship") or {}
    stopped = bool(
        own.get("stopped")
        or own.get("paused")
        or str(own.get("movement_status") or "").lower() in {"paused", "stalled", "stopped"}
    )
    if stopped:
        candidates = [
            {"action": "skip", "label": "remain stopped"},
            {"action": "resume", "label": "resume mission route"},
        ]
    else:
        candidates = [{"action": "maintain_course", "label": "continue active plan"}]
        for degrees in COURSE_CHANGES_DEG:
            side = "port" if degrees < 0 else "starboard"
            candidates.append({
                "action": "alter_course",
                "degrees": degrees,
                "maneuver_distance_m": maneuver_distance_m,
                "label": f"alter {abs(degrees):.0f} degrees {side}",
            })

        recovery = max(0.0, _number(own.get("recovery_speed"), _number(own.get("speed"))))
        current_target = max(0.0, _number(own.get("target_speed"), _number(own.get("speed"))))
        speed_targets = {round(recovery * fraction, 2) for fraction in (0.25, 0.5, 0.75, 1.0)}
        for speed in sorted(speed_targets):
            if speed > 0.0 and not math.isclose(speed, current_target, abs_tol=0.01):
                candidates.append({"action": "set_speed", "speed_mps": speed, "label": f"set speed {speed:.2f} m/s"})
        candidates.append({"action": "stop", "label": "full stop"})
        if (state.get("current_maneuver") or {}).get("action") == "alter_course":
            candidates.append({"action": "rejoin_route", "label": "cancel alteration and rejoin mission route"})

    existing = {_candidate_key(candidate) for candidate in candidates}
    for supplied in additional_candidates or []:
        if not isinstance(supplied, dict) or supplied.get("action") is None:
            continue
        candidate = dict(supplied)
        if candidate.get("action") == "alter_course":
            candidate.setdefault("maneuver_distance_m", maneuver_distance_m)
        candidate.setdefault("label", "supplied proposal")
        key = _candidate_key(candidate)
        if key not in existing:
            candidates.append(candidate)
            existing.add(key)
    return candidates


def _project_candidate(
    state: dict[str, Any],
    candidate: dict[str, Any],
    *,
    horizon_s: float,
    timestep_s: float,
    turn_rate_deg_s: float,
    acceleration_mps2: float,
    deceleration_mps2: float,
    minimum_separation_m: float,
    margin_m: float,
    critical_separation_m: float,
    waypoint_completion_radius_m: float,
) -> dict[str, Any]:
    own = state.get("own_ship") or {}
    x, y = _number(own.get("x")), _number(own.get("y"))
    heading = _number(own.get("heading")) % 360.0
    speed = max(0.0, _number(own.get("speed")))
    target_speed = max(0.0, _number(own.get("target_speed"), speed))
    recovery_speed = max(0.0, _number(own.get("recovery_speed"), target_speed or speed))
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else None
    maneuver = state.get("current_maneuver") or {}
    current_waypoint = maneuver.get("temporary_waypoint") if maneuver.get("temporary_waypoint_active") else None

    action = candidate["action"]
    waypoint: dict[str, float] | None = current_waypoint if isinstance(current_waypoint, dict) else None
    resume_after_waypoint = waypoint is not None
    if action == "alter_course":
        target_heading = (heading + float(candidate["degrees"])) % 360.0
        distance = float(candidate["maneuver_distance_m"])
        waypoint = {
            "x": x + distance * math.sin(math.radians(target_heading)),
            "y": y + distance * math.cos(math.radians(target_heading)),
        }
        resume_after_waypoint = True
    elif action == "rejoin_route":
        waypoint = mission
        resume_after_waypoint = False
    elif action == "set_speed":
        target_speed = float(candidate["speed_mps"])
    elif action in {"stop", "skip"}:
        target_speed = 0.0
    elif action == "resume":
        target_speed = recovery_speed
        waypoint = mission
        resume_after_waypoint = False

    contacts = []
    for contact in state.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        if not all(isinstance(contact.get(key), (int, float)) for key in ("x", "y", "heading", "speed")):
            continue
        contacts.append({
            "name": str(contact.get("name") or "unknown"),
            "x": float(contact["x"]),
            "y": float(contact["y"]),
            "heading": float(contact["heading"]) % 360.0,
            "speed": max(0.0, float(contact["speed"])),
            "minimum": math.inf,
            "time": 0.0,
            "initial": math.hypot(float(contact["x"]) - x, float(contact["y"]) - y),
        })

    steps = max(0, int(math.ceil(horizon_s / timestep_s)))
    path_length = 0.0
    for step in range(steps + 1):
        time_s = min(step * timestep_s, horizon_s)
        for contact in contacts:
            separation = math.hypot(contact["x"] - x, contact["y"] - y)
            if separation < contact["minimum"]:
                contact["minimum"] = separation
                contact["time"] = time_s
        if step == steps:
            break

        dt = min(timestep_s, horizon_s - time_s)
        if speed < target_speed:
            speed = min(target_speed, speed + acceleration_mps2 * dt)
        elif speed > target_speed:
            speed = max(target_speed, speed - deceleration_mps2 * dt)

        desired = _bearing(x, y, waypoint)
        if desired is not None:
            heading = _turn_toward(heading, desired, turn_rate_deg_s * dt)
            if math.hypot(float(waypoint["x"]) - x, float(waypoint["y"]) - y) <= max(
                waypoint_completion_radius_m, speed * dt
            ):
                waypoint = mission if resume_after_waypoint else None
                resume_after_waypoint = False
        old_x, old_y = x, y
        x, y = _advance(x, y, heading, speed, dt)
        path_length += math.hypot(x - old_x, y - old_y)
        for contact in contacts:
            contact["x"], contact["y"] = _advance(
                contact["x"], contact["y"], contact["heading"], contact["speed"], dt
            )

    contact_results = []
    for contact in contacts:
        final_separation = math.hypot(contact["x"] - x, contact["y"] - y)
        contact_results.append({
            "contact": contact["name"],
            "minimum_separation_m": round(contact["minimum"], 1),
            "time_to_minimum_s": round(contact["time"], 1),
            "final_separation_m": round(final_separation, 1),
            "final_trend": "opening" if final_separation > contact["minimum"] + 1.0 else "not_opening",
            "classification": _classification(
                contact["minimum"], critical_separation_m, minimum_separation_m, margin_m
            ),
        })
    contact_results.sort(key=lambda item: item["minimum_separation_m"])
    worst = contact_results[0] if contact_results else None
    mission_distance = None
    if isinstance(mission, dict) and isinstance(mission.get("x"), (int, float)) and isinstance(mission.get("y"), (int, float)):
        mission_distance = round(math.hypot(float(mission["x"]) - x, float(mission["y"]) - y), 1)

    return {
        **candidate,
        "projected_minimum_separation_m": worst["minimum_separation_m"] if worst else None,
        "classification": worst["classification"] if worst else "no_contacts",
        "all_contacts_at_or_above_minimum": all(
            contact["minimum"] >= minimum_separation_m for contact in contacts
        ),
        "worst_contact": worst["contact"] if worst else None,
        "contacts": contact_results,
        "final_mission_distance_m": mission_distance,
        "projected_path_length_m": round(path_length, 1),
        "final_heading_deg": round(heading, 1),
        "final_speed_mps": round(speed, 2),
    }


def build_teacher_candidate_lookahead(
    state: dict[str, Any],
    *,
    additional_candidates: list[dict[str, Any]] | None = None,
    horizon_s: float = 600.0,
    timestep_s: float = 1.0,
    maneuver_distance_m: float = 500.0,
    minimum_separation_m: float = 500.0,
    critical_separation_m: float = 150.0,
    margin_m: float = 50.0,
    turn_rate_deg_s: float = 2.5,
    acceleration_mps2: float = 0.20,
    deceleration_mps2: float = 0.15,
    waypoint_completion_radius_m: float = 3.0,
) -> dict[str, Any]:
    """Compare executable actions under explicit, conservative assumptions."""
    if (
        horizon_s <= 0
        or timestep_s <= 0
        or maneuver_distance_m <= 0
        or waypoint_completion_radius_m <= 0
    ):
        raise ValueError("lookahead horizon, timestep, and maneuver distance must be positive")
    if minimum_separation_m <= 0 or critical_separation_m <= 0 or margin_m < 0:
        raise ValueError("minimum separation must be positive and margin non-negative")
    if critical_separation_m >= minimum_separation_m:
        raise ValueError("critical separation must be below the hard minimum")
    candidates = [
        _project_candidate(
            state,
            candidate,
            horizon_s=horizon_s,
            timestep_s=timestep_s,
            turn_rate_deg_s=turn_rate_deg_s,
            acceleration_mps2=acceleration_mps2,
            deceleration_mps2=deceleration_mps2,
            minimum_separation_m=minimum_separation_m,
            margin_m=margin_m,
            critical_separation_m=critical_separation_m,
            waypoint_completion_radius_m=waypoint_completion_radius_m,
        )
        for candidate in _candidates(
            state, maneuver_distance_m, additional_candidates
        )
    ]
    return {
        "purpose": "privileged teacher decision aid; never an executable override",
        "assumptions": {
            "target_motion": "constant course and speed",
            "own_ship_model": "finite turn rate and acceleration/deceleration toward candidate command",
            "horizon_s": horizon_s,
            "timestep_s": timestep_s,
            "turn_rate_deg_s": turn_rate_deg_s,
            "acceleration_mps2": acceleration_mps2,
            "deceleration_mps2": deceleration_mps2,
            "temporary_maneuver_distance_m": maneuver_distance_m,
            "waypoint_completion_radius_m": waypoint_completion_radius_m,
        },
        "thresholds": {
            "hard_minimum_separation_m": minimum_separation_m,
            "critical_separation_m": critical_separation_m,
            "practical_margin_m": margin_m,
            "safe_at_or_above_m": minimum_separation_m + margin_m,
            "classification": {
                "critical": f"below {critical_separation_m:g} m",
                "unsafe": f"{critical_separation_m:g} m to below {minimum_separation_m:g} m",
                "marginal": f"{minimum_separation_m:g} m to below {minimum_separation_m + margin_m:g} m",
                "safe": f"at least {minimum_separation_m + margin_m:g} m",
            },
        },
        "warning": (
            "Approximate projection only. Check COLREG role, observed trends, uncertainty, and all contacts; "
            "do not copy projection-only evidence into the student-visible reason."
        ),
        "candidates": candidates,
    }
