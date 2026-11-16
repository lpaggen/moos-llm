"""Select applicable COLREG rules from deterministic bridge facts.

This module classifies applicability; it does not decide whether a proposed
maneuver complies with the selected rules.
"""

from __future__ import annotations

import math
from typing import Any


BASE_RULES = {2, 5, 6, 7}
STANDING_RULES = BASE_RULES | {11}
COLLISION_RISK_LEVELS = {"medium", "high", "critical"}
SPECIAL_VESSEL_TYPES = {
    "sailing": "sailing",
    "sail": "sailing",
    "fishing": "fishing",
    "trawler": "fishing",
    "not_under_command": "not_under_command",
    "nuc": "not_under_command",
    "restricted_maneuverability": "restricted_maneuverability",
    "ram": "restricted_maneuverability",
    "constrained_by_draft": "constrained_by_draft",
    "cbd": "constrained_by_draft",
}

RULE_GUIDANCE = {
    2: "Remain responsible for ordinary seamanship and precautions required by the circumstances.",
    5: "Maintain a proper lookout using all available means.",
    6: "Proceed at a safe speed that permits effective avoiding action and stopping.",
    7: "Use all available information to assess collision risk; if in doubt, treat risk as existing.",
    8: "Take positive, substantial action in ample time, avoid successive small alterations, and verify that it produces safe passing distance. Course alterations may be as large as 60 degrees; when safe, prefer one decisive 30-60 degree alteration over repeated 15-degree legs.",
    9: "In a narrow channel, keep near the starboard outer limit when safe and practicable.",
    10: "Follow the applicable traffic-separation lane and crossing requirements.",
    11: "The in-sight conduct rules apply.",
    12: "Apply the sailing-vessel wind-side and windward/leeward responsibilities supplied in the encounter role.",
    13: "The overtaking vessel must keep out of the way until finally past and clear.",
    14: "In a head-on power-driven encounter, both vessels should alter course to starboard for port-to-port passing.",
    15: "In a crossing encounter, the vessel with the other on her starboard side must keep out of the way and, if circumstances admit, avoid crossing ahead.",
    16: "A give-way vessel must take early and substantial action to keep well clear.",
    17: "A stand-on vessel initially keeps course and speed, but must act when collision cannot be avoided by the give-way vessel alone.",
    18: "Apply the responsibility hierarchy for vessels of differing types or maneuverability.",
    19: "In restricted visibility, proceed at safe speed and avoid a port alteration for a vessel forward of the beam unless overtaking.",
    34: "An in-sight course alteration requires the matching maneuvering sound signal.",
}


def _encounter_guidance(encounter_type: str, role: str, active_rules: set[int]) -> str:
    """Translate classified duties into concise, geometry-aware helm guidance."""
    if 19 in active_rules:
        return (
            "Restricted-visibility encounter: use Rule 19 guidance; do not apply "
            "the in-sight crossing rules unless the contact is classified in sight."
        )
    if encounter_type == "crossing" and role == "give_way":
        return (
            "Give way early and substantially. The contact is on own ship's "
            "starboard side: avoid crossing ahead. When sea room and other "
            "traffic permit, make one readily apparent, decisive starboard "
            "alteration (30-60 degrees is available) to pass astern; do not "
            "default to repeated 15-degree legs. Otherwise reduce speed or "
            "stop. Do not turn port "
            "toward or across the stand-on vessel's track."
        )
    if encounter_type == "crossing" and role == "stand_on":
        return (
            "Stand on initially by keeping course and speed while monitoring. "
            "If the give-way vessel is not taking effective action, act in time; "
            "avoid a port alteration toward a vessel on own port side when practicable."
        )
    if encounter_type == "head_on" and role == "both_give_way":
        return (
            "Make an early, substantial alteration to starboard so both vessels "
            "pass port-to-port; verify that CPA increases."
        )
    if encounter_type == "we_are_overtaking_contact":
        return (
            "Own ship is overtaking and must keep clear until finally past and "
            "clear. Choose the passing side from sea room and traffic, using an "
            "early, substantial maneuver."
        )
    if encounter_type == "contact_overtaking_us":
        return (
            "Own ship is being overtaken and is stand-on; maintain predictable "
            "course and speed unless action becomes necessary to avoid collision."
        )
    if active_rules:
        return (
            "Apply the listed active rules with early, substantial action where "
            "own ship is required to keep clear, and verify increasing separation."
        )
    return (
        "No new collision-avoidance maneuver is prescribed. Continue monitoring."
    )


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _vessel_category(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"ship", "motor", "power", "power_driven"}:
        return "power_driven"
    return SPECIAL_VESSEL_TYPES.get(normalized, "unknown")


def _crossing_role(relative_bearing: float | None) -> str:
    if relative_bearing is None:
        return "unknown"
    bearing = relative_bearing % 360.0
    if 0.0 < bearing < 112.5:
        return "give_way"
    if 247.5 < bearing < 360.0:
        return "stand_on"
    return "unknown"


def _wind_side(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"port", "starboard"} else None


def _sailing_role(
    state: dict[str, Any], contact: dict[str, Any]
) -> tuple[str, list[str]]:
    """Resolve Rule 12 when the bridge supplies the necessary wind facts."""
    name = str(contact.get("name") or "unknown")
    own_side = _wind_side(state.get("wind_side"))
    contact_side = _wind_side(contact.get("wind_side"))
    missing: list[str] = []
    if own_side is None:
        missing.append("own_ship.wind_side")
    if contact_side is None:
        missing.append(f"contacts.{name}.wind_side")
    if missing:
        return "unknown", missing
    if own_side != contact_side:
        return ("give_way" if own_side == "port" else "stand_on"), []

    own_windward = state.get("windward")
    contact_windward = contact.get("windward")
    if not isinstance(own_windward, bool):
        missing.append("own_ship.windward")
    if not isinstance(contact_windward, bool):
        missing.append(f"contacts.{name}.windward")
    if missing:
        return "unknown", missing
    if own_windward == contact_windward:
        return "unknown", [f"contacts.{name}.windward_relationship"]
    return ("give_way" if own_windward else "stand_on"), []


def classify_colregs(
    state: dict[str, Any],
    *,
    visibility_range_m: float,
    visibility_condition: str = "clear",
    narrow_channel: bool = False,
    traffic_separation_scheme: bool = False,
    stale_after_s: float | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return rule IDs and encounter roles supported by current state facts."""
    if visibility_range_m <= 0 or not math.isfinite(visibility_range_m):
        raise ValueError("visibility_range_m must be a positive finite number")

    condition = visibility_condition.strip().lower()
    if condition not in {"clear", "restricted"}:
        raise ValueError("visibility_condition must be 'clear' or 'restricted'")

    rules = set(BASE_RULES)
    if narrow_channel:
        rules.add(9)
    if traffic_separation_scheme:
        rules.add(10)

    own_type = _vessel_category(state.get("vessel_type"))
    encounters: list[dict[str, Any]] = []
    skipped_contacts: list[dict[str, Any]] = []
    missing_facts: set[str] = set()
    if own_type == "unknown":
        missing_facts.add("own_ship.vessel_type")

    for contact in state.get("contacts", []):
        if not isinstance(contact, dict):
            continue
        age_s = _number(contact.get("age_s"))
        if stale_after_s is not None and (age_s is None or age_s > stale_after_s):
            name = contact.get("name") or "unknown"
            missing_facts.add(f"contacts.{name}.age_s")
            skipped_contacts.append({
                "contact": contact.get("name"),
                "reason": "missing_age" if age_s is None else "stale",
                "age_s": age_s,
            })
            continue
        distance = _number(contact.get("range"))
        # Visibility range is the simulator's deterministic substitute for a
        # visual sensor. Even in restricted visibility a very close contact
        # may be in sight, in which case Section II still applies.
        in_sight = distance is not None and distance <= visibility_range_m
        contact_type = _vessel_category(
            contact.get("_colregs_vessel_type")
            or contact.get("vessel_type")
            or contact.get("type")
        )
        encounter_type = str(contact.get("encounter_type") or "unknown")
        role = "not_applicable"
        standing_rules = set(BASE_RULES)
        active_rules: set[int] = set()
        collision_risk = contact.get("risk") in COLLISION_RISK_LEVELS

        if contact_type == "unknown":
            missing_facts.add(f"contacts.{contact.get('name') or 'unknown'}.vessel_type")

        if condition == "restricted" and not in_sight:
            active_rules.add(19)
            role = "restricted_visibility"
        elif in_sight:
            standing_rules.add(11)
            if collision_risk:
                active_rules.add(8)
            if encounter_type in {"we_are_overtaking_contact", "contact_overtaking_us"}:
                active_rules.add(13)
                role = "give_way" if encounter_type == "we_are_overtaking_contact" else "stand_on"
                if role == "give_way":
                    active_rules.add(16)
            elif collision_risk and own_type == contact_type == "sailing":
                active_rules.add(12)
                role, sailing_missing = _sailing_role(state, contact)
                missing_facts.update(sailing_missing)
            elif (
                collision_risk
                and own_type == contact_type == "power_driven"
                and encounter_type == "head_on"
            ):
                active_rules.add(14)
                role = "both_give_way"
            elif (
                collision_risk
                and own_type == contact_type == "power_driven"
                and encounter_type == "crossing"
            ):
                active_rules.add(15)
                role = _crossing_role(_number(contact.get("relative_bearing")))
                if role == "give_way":
                    active_rules.add(16)
                elif role == "stand_on":
                    active_rules.add(17)
                else:
                    missing_facts.add(f"contacts.{contact.get('name') or 'unknown'}.crossing_role")

            if collision_risk and own_type != "unknown" and contact_type != "unknown" and (
                own_type != "power_driven" or contact_type != "power_driven"
            ):
                active_rules.add(18)

        contact_rules = standing_rules | active_rules
        rules.update(contact_rules)
        encounters.append({
            "contact": contact.get("name"),
            "contact_vessel_type": contact_type,
            "in_sight": in_sight,
            "encounter_type": encounter_type,
            "own_role": role,
            "age_s": age_s,
            "range_m": distance,
            "relative_bearing_deg": _number(contact.get("relative_bearing")),
            "closing_speed": _number(contact.get("closing_speed")),
            "cpa_distance_m": _number(contact.get("cpa_distance")),
            "tcpa_s": _number(contact.get("tcpa_s")),
            "projection_status": contact.get("projection_status"),
            "risk": contact.get("risk"),
            "standing_rules": sorted(standing_rules),
            "active_encounter_rules": sorted(active_rules),
            "applicable_rules": sorted(contact_rules),
            "maneuver_guidance": _encounter_guidance(
                encounter_type, role, active_rules
            ),
        })

    obligations: list[dict[str, Any]] = []
    action = str((decision or {}).get("action") or "")
    if action == "alter_course" and any(
        encounter["in_sight"] for encounter in encounters
    ):
        rules.add(34)
        auxiliary_commands = (decision or {}).get("auxiliary_commands", [])
        horn_commanded = any(
            isinstance(command, dict) and command.get("type") == "sound_horn"
            for command in auxiliary_commands
        )
        obligations.append({
            "rule": 34,
            "kind": "maneuvering_signal",
            "status": "commanded" if horn_commanded else "not_commanded",
            "reason": (
                "A sound-horn command accompanies the course alteration."
                if horn_commanded
                else "The course alteration has no accompanying sound-horn command."
            ),
        })

    assumptions = {
        "visibility_condition": condition,
        "visibility_range_m": visibility_range_m,
        "narrow_channel": narrow_channel,
        "traffic_separation_scheme": traffic_separation_scheme,
    }
    return {
        "own_vessel_type": own_type,
        "standing_rules": sorted(rules & STANDING_RULES),
        "active_encounter_rules": sorted(rules - STANDING_RULES),
        "applicable_rules": sorted(rules),
        "rule_guidance": {
            str(rule): RULE_GUIDANCE[rule]
            for rule in sorted(rules)
            if rule in RULE_GUIDANCE
        },
        "encounters": encounters,
        "decision_obligations": obligations,
        "skipped_contacts": skipped_contacts,
        "assumptions": assumptions,
        "missing_facts": sorted(missing_facts),
    }
