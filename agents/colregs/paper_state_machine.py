"""Persistent COLREG encounter classifier based on Eriksen et al. (2020)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


SAFE = "SF"
OVERTAKING = "OT"
HEAD_ON = "HO"
GIVE_WAY = "GW"
STAND_ON = "SO"
EMERGENCY = "EM"

RULES = {
    SAFE: [2, 5, 6, 7],
    OVERTAKING: [2, 5, 6, 7, 8, 13],
    HEAD_ON: [2, 5, 6, 7, 8, 14],
    GIVE_WAY: [2, 5, 6, 7, 8, 15, 16],
    STAND_ON: [2, 5, 6, 7, 8, 15, 17],
    EMERGENCY: [2, 5, 6, 7, 8],
}
RULE_SUMMARIES = {
    2: "Remain responsible for prudent seamanship and precautions required by the circumstances.",
    5: "Maintain a proper lookout using all available means appropriate to the circumstances.",
    6: "Proceed at a safe speed that permits effective avoiding action and stopping.",
    7: "Use all available information to determine collision risk; if in doubt, treat risk as existing.",
    8: (
        "Take positive, substantial avoiding action in ample time, avoid successive "
        "small alterations, and verify safe passing distance."
    ),
    13: (
        "Overtaking vessel keeps out of the way of the vessel being overtaken "
        "until finally past and clear."
    ),
    14: (
        "Power-driven vessels meeting head-on or nearly head-on both alter course "
        "to starboard for port-to-port passing."
    ),
    15: (
        "In a crossing encounter, the power-driven vessel with the other on her "
        "starboard side keeps out of the way and should avoid crossing ahead when practicable."
    ),
    16: (
        "The give-way vessel takes early and substantial action to keep well clear."
    ),
    17: (
        "The stand-on vessel initially keeps course and speed, may act when the "
        "give-way vessel is not acting appropriately, and must act when collision "
        "cannot be avoided by the give-way vessel alone."
    ),
    18: (
        "A power-driven vessel keeps out of the way of vessels not under command, "
        "restricted in ability to maneuver, engaged in fishing, and sailing vessels."
    ),
}


def _vessel_type(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"ship", "motor", "power", "power_driven"}:
        return "power_driven"
    return normalized or "unknown"


@dataclass(frozen=True)
class PaperThresholds:
    cpa_entry_m: float = 900.0
    cpa_exit_m: float = 2000.0
    tcpa_entry_min_s: float = 0.0
    tcpa_entry_max_s: float = 270.0
    tcpa_exit_min_s: float = -20.0
    tcpa_exit_max_s: float = 290.0
    emergency_entry_s: float = 20.0
    emergency_exit_s: float = 25.0
    critical_distance_m: float = 555.6


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _velocity(heading_deg: float, speed: float) -> tuple[float, float]:
    radians = math.radians(heading_deg)
    return speed * math.sin(radians), speed * math.cos(radians)


def _bearing(observer: dict[str, Any], target: dict[str, Any]) -> float:
    dx = float(target["x"]) - float(observer["x"])
    dy = float(target["y"]) - float(observer["y"])
    absolute = math.degrees(math.atan2(dx, dy)) % 360.0
    return (absolute - float(observer["heading"])) % 360.0


def _relative_motion(
    own: dict[str, Any], contact: dict[str, Any], critical_distance_m: float
) -> dict[str, float | None]:
    relative_x = float(contact["x"]) - float(own["x"])
    relative_y = float(contact["y"]) - float(own["y"])
    own_vx, own_vy = _velocity(float(own["heading"]), float(own["speed"]))
    contact_vx, contact_vy = _velocity(
        float(contact["heading"]), float(contact["speed"])
    )
    relative_vx = contact_vx - own_vx
    relative_vy = contact_vy - own_vy
    speed_squared = relative_vx**2 + relative_vy**2
    if speed_squared <= 1e-9:
        tcpa = 0.0
    else:
        tcpa = -(
            relative_x * relative_vx + relative_y * relative_vy
        ) / speed_squared
    dcpa = math.hypot(
        relative_x + tcpa * relative_vx,
        relative_y + tcpa * relative_vy,
    )

    current_range = math.hypot(relative_x, relative_y)
    tcrit = 0.0 if current_range <= critical_distance_m else None
    a = speed_squared
    b = 2.0 * (relative_x * relative_vx + relative_y * relative_vy)
    c = relative_x**2 + relative_y**2 - critical_distance_m**2
    discriminant = b**2 - 4.0 * a * c
    if a > 1e-9 and discriminant >= 0.0:
        root = math.sqrt(discriminant)
        roots = [(-b - root) / (2.0 * a), (-b + root) / (2.0 * a)]
        future = [value for value in roots if value >= 0.0]
        if future and tcrit is None:
            tcrit = min(future)
    return {
        "range_m": current_range,
        "tcpa_s": tcpa,
        "dcpa_m": dcpa,
        "tcrit_s": tcrit,
    }


def _geometrical_state(own: dict[str, Any], contact: dict[str, Any], tcpa: float) -> str:
    if tcpa < 0.0:
        return SAFE
    own_bearing = _bearing(own, contact)
    contact_bearing = _bearing(contact, own)
    course_difference = abs(
        (float(contact["heading"]) - float(own["heading"]) + 180.0) % 360.0
        - 180.0
    )
    own_abaft_contact = 112.5 < contact_bearing < 247.5
    contact_abaft_own = 112.5 < own_bearing < 247.5
    broadly_same_course = course_difference <= 90.0
    if broadly_same_course and (own_abaft_contact or contact_abaft_own):
        return OVERTAKING
    if (own_bearing <= 15.0 or own_bearing >= 345.0) and course_difference >= 150.0:
        return HEAD_ON
    if 0.0 < own_bearing < 112.5:
        return GIVE_WAY
    if 247.5 < own_bearing < 360.0:
        return STAND_ON
    return SAFE


def _own_role(state: str, own: dict[str, Any], contact: dict[str, Any]) -> str:
    if state == OVERTAKING:
        return "give_way" if 112.5 < _bearing(contact, own) < 247.5 else "stand_on"
    if state == HEAD_ON:
        return "both_give_way"
    if state == GIVE_WAY:
        return "give_way"
    if state == STAND_ON:
        return "stand_on"
    if state == EMERGENCY:
        return "special_consideration"
    return "not_applicable"


class PaperColregsStateMachine:
    """Classify each contact and retain the paper's per-contact hysteresis state."""

    def __init__(self, thresholds: PaperThresholds | None = None) -> None:
        self.thresholds = thresholds or PaperThresholds()
        self.states: dict[str, str] = {}
        self.previous_motion: dict[str, dict[str, float | None]] = {}

    def classify(self, bridge_state: dict[str, Any]) -> dict[str, Any]:
        nav = bridge_state.get("nav") or {}
        own = {
            key: _number(nav.get(key))
            for key in ("x", "y", "heading", "speed")
        }
        if any(value is None for value in own.values()):
            return {"method": "eriksen_2020", "available": False, "contacts": []}

        present: set[str] = set()
        results = []
        for contact in bridge_state.get("contacts") or []:
            name = str(contact.get("name") or "")
            contact_nav = contact.get("nav") or {}
            target = {
                key: _number(contact_nav.get(key))
                for key in ("x", "y", "heading", "speed")
            }
            if not name or any(value is None for value in target.values()):
                continue
            present.add(name)
            motion = _relative_motion(own, target, self.thresholds.critical_distance_m)
            geometrical = _geometrical_state(own, target, float(motion["tcpa_s"]))
            previous = self.states.get(name, SAFE)
            current = previous
            reason = "state retained by hysteresis"
            tcpa = float(motion["tcpa_s"])
            dcpa = float(motion["dcpa_m"])
            tcrit = motion["tcrit_s"]

            standard_entry = (
                geometrical in {OVERTAKING, HEAD_ON, GIVE_WAY, STAND_ON}
                and dcpa < self.thresholds.cpa_entry_m
                and self.thresholds.tcpa_entry_min_s
                <= tcpa
                <= self.thresholds.tcpa_entry_max_s
            )
            emergency_entry = (
                geometrical in {HEAD_ON, GIVE_WAY}
                and tcpa > 0.0
                and tcrit is not None
                and tcrit < self.thresholds.emergency_entry_s
            )
            standard_exit = (
                dcpa >= self.thresholds.cpa_exit_m
                or not self.thresholds.tcpa_exit_min_s
                <= tcpa
                <= self.thresholds.tcpa_exit_max_s
            )
            emergency_exit = (
                tcpa <= 0.0
                or tcrit is None
                or tcrit >= self.thresholds.emergency_exit_s
            )

            if previous == SAFE:
                if emergency_entry:
                    current = EMERGENCY
                    reason = "safe-to-emergency entry criteria satisfied"
                elif standard_entry:
                    current = geometrical
                    reason = "safe-to-encounter entry criteria satisfied"
                else:
                    reason = "entry criteria not satisfied"
            elif previous == EMERGENCY and emergency_exit:
                current = SAFE
                reason = "emergency exit criteria satisfied"
            elif previous != EMERGENCY and standard_exit:
                current = SAFE
                reason = "encounter exit criteria satisfied"

            self.states[name] = current
            applicable_rules = list(RULES[current])
            role = _own_role(current, own, target)
            own_type = _vessel_type(bridge_state.get("vessel_type"))
            contact_type = _vessel_type(contact.get("vessel_type"))
            give_way_by_type = (
                own_type == "power_driven"
                and contact_type in {
                    "sailing", "fishing", "not_under_command",
                    "restricted_maneuverability",
                }
                and current != OVERTAKING
            )
            avoid_impeding = (
                own_type == "power_driven"
                and contact_type == "constrained_by_draft"
                and current != OVERTAKING
            )
            if give_way_by_type:
                applicable_rules = [2, 5, 6, 7, 8, 18]
                role = "give_way"
            elif avoid_impeding:
                applicable_rules = [2, 5, 6, 7, 8, 18]
                role = "avoid_impeding"
            if current == OVERTAKING and role == "give_way":
                if 16 not in applicable_rules:
                    applicable_rules.append(16)
            previous_motion = self.previous_motion.get(name)
            trend = {"assessment": "first_observation"}
            if previous_motion is not None:
                range_change = float(motion["range_m"]) - float(previous_motion["range_m"])
                trend = {
                    "assessment": "closing" if range_change < -0.5 else (
                        "opening" if range_change > 0.5 else "approximately_steady"
                    ),
                    "range_change_m": round(range_change, 3),
                    "dcpa_change_m": round(
                        float(motion["dcpa_m"]) - float(previous_motion["dcpa_m"]), 3
                    ),
                    "tcpa_change_s": round(
                        float(motion["tcpa_s"]) - float(previous_motion["tcpa_s"]), 3
                    ),
                }
            self.previous_motion[name] = motion
            results.append({
                "contact": name,
                "previous_state": previous,
                "state": current,
                "state_name": {
                    SAFE: "safe", OVERTAKING: "overtaking", HEAD_ON: "head_on",
                    GIVE_WAY: "give_way", STAND_ON: "stand_on", EMERGENCY: "emergency",
                }[current],
                "geometrical_state": geometrical,
                "own_role": role,
                "own_vessel_type": own_type,
                "contact_vessel_type": contact_type,
                "differing_vessel_type_responsibility": give_way_by_type or avoid_impeding,
                "applicable_rules": applicable_rules,
                "applicable_rule_summaries": {
                    str(rule): RULE_SUMMARIES[rule]
                    for rule in applicable_rules
                },
                "transition_reason": reason,
                "evolution_since_previous_snapshot": trend,
                **{key: round(value, 3) if isinstance(value, float) else value
                   for key, value in motion.items()},
                "own_relative_bearing_deg": round(_bearing(own, target), 3),
                "contact_relative_bearing_deg": round(_bearing(target, own), 3),
                "course_difference_deg": round(abs(
                    (float(target["heading"]) - float(own["heading"]) + 180.0)
                    % 360.0 - 180.0
                ), 3),
            })

        for missing in set(self.states) - present:
            self.states.pop(missing, None)
            self.previous_motion.pop(missing, None)
        state_priority = {
            EMERGENCY: 0,
            GIVE_WAY: 1,
            HEAD_ON: 1,
            OVERTAKING: 2,
            STAND_ON: 3,
            SAFE: 4,
        }
        results.sort(key=lambda contact: (
            state_priority[contact["state"]],
            contact["tcrit_s"] if contact["tcrit_s"] is not None else math.inf,
            contact["tcpa_s"] if contact["tcpa_s"] >= 0.0 else math.inf,
            contact["dcpa_m"],
            contact["range_m"],
            contact["contact"],
        ))
        for index, contact in enumerate(results, 1):
            contact["priority_rank"] = index
        return {
            "method": "eriksen_2020",
            "available": True,
            "assumptions": [
                "constant course and speed during projection",
                "power-driven vessels in sight",
                "paper state-machine thresholds",
            ],
            "thresholds": asdict(self.thresholds),
            "primary_contact": results[0]["contact"] if results else None,
            "priority_basis": (
                "encounter state, then earliest critical-boundary entry, earliest "
                "non-negative TCPA, smallest DCPA, and current range"
            ),
            "contacts": results,
        }
