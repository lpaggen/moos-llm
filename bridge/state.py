"""Process-local, versioned snapshots of the raw bridge state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from collections import deque
from threading import RLock
from time import monotonic
from typing import Any, Callable
import math
import os

from agents.colregs import classify_colregs
from tools.config_value import get


StateReader = Callable[[], dict[str, Any]]
COLREG_VISIBILITY_RANGE_M = float(get("colregs", "visibility_range_m"))
COLREG_VISIBILITY_CONDITION = get("colregs", "visibility_condition")
COLREG_NARROW_CHANNEL = get("colregs", "narrow_channel").lower() == "true"
COLREG_TRAFFIC_SEPARATION_SCHEME = (
    get("colregs", "traffic_separation_scheme").lower() == "true"
)
COLREG_STALE_AFTER_S = float(get("lookout", "stale_after_s"))
MIN_SAFE_CPA_M = float(os.environ.get("MOOS_LLM_MIN_SAFE_CPA_M") or get("ship", "MIN_SAFE_CPA_M", fallback="150.0"))
CAUTION_CPA_M = float(os.environ.get("MOOS_LLM_CAUTION_CPA_M") or get("ship", "CAUTION_CPA_M", fallback="300.0"))
BOW_CROSSING_M = float(os.environ.get("MOOS_LLM_BOW_CROSSING_M") or MIN_SAFE_CPA_M)
CRITICAL_CPA_M = float(os.environ.get("MOOS_LLM_CRITICAL_CPA_M") or get("ship", "CRITICAL_CPA_M", fallback="150.0"))
PLANNING_TCPA_S = float(get("ship", "PLANNING_TCPA_S", fallback="600.0"))
CPA_HORIZON_S = PLANNING_TCPA_S
INTERVENTION_TCPA_S = float(get("ship", "INTERVENTION_TCPA_S", fallback="300.0"))
EMERGENCY_TCPA_S = float(get("ship", "EMERGENCY_TCPA_S", fallback="120.0"))
COLLISION_DISTANCE_M = float(get("outcomes", "collision_distance_m", fallback="10.0"))
NEAR_COLLISION_DISTANCE_M = float(
    get("outcomes", "near_collision_distance_m", fallback="50.0")
)


@dataclass(frozen=True, slots=True)
class BridgeSnapshot:
    """One state sample shared by every agent in a decision cycle."""

    version: int
    received_at: float
    _raw_state: dict[str, Any]

    @property
    def raw_state(self) -> dict[str, Any]:
        """Expose state read-only-by-copy to consumers."""
        return deepcopy(self._raw_state)

    def state_for_agent(self) -> dict[str, Any]:
        """Return an isolated state with deterministic navigation geometry."""
        state = self.raw_state
        own_ship = self._own_ship_view(state)
        contacts = state.get("contacts", [])
        state["contacts"] = [
            self._enrich_contact(own_ship, contact)
            for contact in contacts
            if isinstance(contact, dict)
        ]
        # Expose no candidate-derived maneuver limits. A stopped/paused vessel
        # may either resume or explicitly skip issuing a bridge command.
        state.pop("candidate_actions", None)
        # This is descriptive API metadata, not a policy enforcement point.
        # The OOW may issue any supported command in any control state.
        state["allowed_actions"] = [
            "maintain_course",
            "alter_course",
            "set_speed",
            "stop",
            "resume",
            "skip",
        ]
        current_action = state.get("current_action")
        if (
            state.get("action_underway")
            and isinstance(current_action, dict)
            and current_action.get("action") == "alter_course"
        ):
            state["allowed_actions"].insert(2, "rejoin_route")
        state["stopped"] = own_ship["stopped"]
        state["movement_status"] = own_ship["movement_status"]
        state["colregs_context"] = classify_colregs(
            state,
            visibility_range_m=COLREG_VISIBILITY_RANGE_M,
            visibility_condition=COLREG_VISIBILITY_CONDITION,
            narrow_channel=COLREG_NARROW_CHANNEL,
            traffic_separation_scheme=COLREG_TRAFFIC_SEPARATION_SCHEME,
            stale_after_s=COLREG_STALE_AFTER_S,
        )
        minimum_range = state.get("minimum_observed_range")
        observed_range = (
            float(minimum_range)
            if isinstance(minimum_range, (int, float)) and math.isfinite(float(minimum_range))
            else None
        )
        state["collision"] = (
            observed_range is not None and observed_range <= COLLISION_DISTANCE_M
        )
        state["near_collision"] = (
            observed_range is not None
            and COLLISION_DISTANCE_M < observed_range <= NEAR_COLLISION_DISTANCE_M
        )
        state["mission_failed"] = state["collision"]
        state["mission_failure_reason"] = "collision" if state["collision"] else None
        return state

    @staticmethod
    def _velocity(heading: float, speed: float) -> tuple[float, float]:
        radians = math.radians(heading)
        return speed * math.sin(radians), speed * math.cos(radians)

    @classmethod
    def _enrich_contact(
        cls,
        own_ship: dict[str, Any],
        contact: dict[str, Any],
    ) -> dict[str, Any]:
        """Calculate relative-motion facts from one coherent state sample."""
        enriched = deepcopy(contact)
        own_position = own_ship.get("position", {})
        contact_nav = contact.get("nav", {})
        values = (
            own_position.get("x"),
            own_position.get("y"),
            own_ship.get("heading"),
            own_ship.get("speed"),
            contact_nav.get("x"),
            contact_nav.get("y"),
            contact_nav.get("heading"),
            contact_nav.get("speed"),
        )
        if any(value is None for value in values):
            return enriched

        own_x, own_y, own_heading, own_speed, x, y, heading, speed = map(
            float, values
        )
        relative_x = x - own_x
        relative_y = y - own_y
        distance = math.hypot(relative_x, relative_y)
        own_vx, own_vy = cls._velocity(own_heading, own_speed)
        contact_vx, contact_vy = cls._velocity(heading, speed)
        relative_vx = contact_vx - own_vx
        relative_vy = contact_vy - own_vy
        velocity_squared = relative_vx**2 + relative_vy**2
        radial_rate = relative_x * relative_vx + relative_y * relative_vy
        closing_speed = -radial_rate / distance if distance > 0.0 else 0.0

        tcpa_s: float | None = None
        cpa_distance = distance
        projection_status = "no_future_cpa"
        if velocity_squared > 1e-9 and radial_rate < 0.0:
            # Always retain the physical CPA/TCPA. The planning horizon is a
            # decision flag, not a reason to replace CPA with current range or
            # erase TCPA from the observation.
            tcpa_s = -radial_rate / velocity_squared
            cpa_distance = math.hypot(
                relative_x + relative_vx * tcpa_s,
                relative_y + relative_vy * tcpa_s,
            )
            projection_status = (
                "valid" if tcpa_s <= CPA_HORIZON_S
                else "beyond_planning_horizon"
            )

        # Intersection of the two forward course rays is distinct from CPA:
        # it says where the tracks cross, while CPA/TCPA accounts for whether
        # the vessels reach that geometry at compatible times.
        own_dx, own_dy = cls._velocity(own_heading, 1.0)
        contact_dx, contact_dy = cls._velocity(heading, 1.0)
        direction_cross = own_dx * contact_dy - own_dy * contact_dx
        track_intersection: dict[str, float] | None = None
        if abs(direction_cross) > math.sin(math.radians(2.0)):
            own_distance_to_crossing = (
                relative_x * contact_dy - relative_y * contact_dx
            ) / direction_cross
            contact_distance_to_crossing = (
                relative_x * own_dy - relative_y * own_dx
            ) / direction_cross
            if own_distance_to_crossing >= 0.0 and contact_distance_to_crossing >= 0.0:
                track_intersection = {
                    "x": own_x + own_dx * own_distance_to_crossing,
                    "y": own_y + own_dy * own_distance_to_crossing,
                    "own_eta_s": own_distance_to_crossing / own_speed
                    if own_speed > 1e-9 else math.inf,
                    "contact_eta_s": contact_distance_to_crossing / speed
                    if speed > 1e-9 else math.inf,
                }

        relative_bearing = cls._relative_bearing_deg(own_ship, contact)
        sector = cls._bearing_sector(relative_bearing)
        contact_relative_bearing = cls._relative_bearing_deg(
            {
                "position": {"x": x, "y": y},
                "heading": heading,
            },
            {
                "nav": {"x": own_x, "y": own_y},
            },
        )
        heading_difference = abs(
            ((heading - own_heading + 180.0) % 360.0) - 180.0
        )
        ahead = (
            relative_bearing is not None
            and (relative_bearing <= 15.0 or relative_bearing >= 345.0)
        )
        # A stopped contact has no operative course, so its last reported
        # heading must not turn it into an overtaking or crossing vessel.  We
        # still retain relative-motion CPA/TCPA: own ship can be closing on a
        # stationary obstruction even though Rules 13--15 do not describe the
        # encounter.
        if speed <= 0.2:
            encounter_type = "stationary_contact"
        elif closing_speed > 0.0 and ahead and heading_difference >= 150.0:
            encounter_type = "head_on"
        elif closing_speed > 0.0 and heading_difference <= 30.0:
            contact_astern = (
                relative_bearing is not None
                and 112.5 < relative_bearing < 247.5
            )
            own_astern = (
                contact_relative_bearing is not None
                and 112.5 < contact_relative_bearing < 247.5
            )
            if contact_astern:
                encounter_type = "contact_overtaking_us"
            elif own_astern:
                encounter_type = "we_are_overtaking_contact"
            elif track_intersection is not None or heading_difference > 2.0:
                # Similar headings are not parallel. If their forward tracks
                # cross (or materially converge), this is crossing geometry.
                encounter_type = "crossing"
            else:
                encounter_type = "parallel"
        elif closing_speed > 0.0 and tcpa_s is not None:
            encounter_type = "crossing"
        else:
            encounter_type = "diverging"
        encounter_type = str(
            contact.get("_active_encounter") or encounter_type
        )

        required_cpa_m = MIN_SAFE_CPA_M
        if encounter_type in {"crossing", "head_on"} and sector in {
            "ahead", "port bow", "starboard bow"
        }:
            required_cpa_m = BOW_CROSSING_M

        # Desired passing clearances and risk classification are separate.
        # In particular, a breach of the desired 0.5 NM passing distance is
        # not by itself a critical emergency.
        if (
            projection_status == "valid"
            and tcpa_s is not None
            and cpa_distance < CRITICAL_CPA_M
        ):
            risk = "critical"
        elif (
            tcpa_s is not None
            and cpa_distance < MIN_SAFE_CPA_M
            and tcpa_s <= INTERVENTION_TCPA_S
        ):
            risk = "high"
        elif (
            tcpa_s is not None
            and cpa_distance < CAUTION_CPA_M
            and tcpa_s <= PLANNING_TCPA_S
        ):
            risk = "medium"
        elif (
            tcpa_s is not None
            and cpa_distance < required_cpa_m
        ):
            # A known future breach of the required clearance remains a
            # conflict outside the tactical horizon. The horizon controls
            # urgency, not whether the geometry is falsely called safe.
            risk = "medium"
        else:
            risk = "low"

        enriched.update(
            {
                "range": distance,
                "relative_bearing": relative_bearing,
                "sector": sector,
                "closing_speed": closing_speed,
                "cpa_distance": cpa_distance,
                "tcpa_s": tcpa_s,
                "track_intersection": track_intersection,
                "required_cpa_m": required_cpa_m,
                "projection_status": projection_status,
                "encounter_type": encounter_type,
                "risk": risk,
            }
        )
        return enriched

    @staticmethod
    def _goal_geometry(own_ship: dict[str, Any], mission_objective: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(mission_objective, dict):
            return None

        x = own_ship.get("position", {}).get("x")
        y = own_ship.get("position", {}).get("y")
        goal_x = mission_objective.get("x")
        goal_y = mission_objective.get("y")
        heading = own_ship.get("heading")

        if x is None or y is None or goal_x is None or goal_y is None:
            return None

        dx = float(goal_x) - float(x)
        dy = float(goal_y) - float(y)
        distance = math.hypot(dx, dy)
        bearing_to_goal = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
        heading_error = None
        if heading is not None:
            heading_error = ((bearing_to_goal - float(heading) + 180.0) % 360.0) - 180.0

        route_status = "unknown"
        if heading_error is not None:
            if abs(heading_error) <= 30.0:
                route_status = "on_route"
            elif abs(heading_error) <= 90.0:
                route_status = "deviating"
            else:
                route_status = "away_from_goal"

        return {
            "distance_to_goal_m": distance,
            "bearing_to_goal_deg": bearing_to_goal,
            "heading_error_deg": heading_error,
            "distance_trend": None,
            "route_status": route_status,
        }

    @staticmethod
    def _relative_bearing_deg(
        own_ship: dict[str, Any],
        contact: dict[str, Any],
    ) -> float | None:
        own_pos = own_ship.get("position", {})
        contact_nav = contact.get("nav", {})
        own_x = own_pos.get("x")
        own_y = own_pos.get("y")
        contact_x = contact_nav.get("x")
        contact_y = contact_nav.get("y")
        own_heading = own_ship.get("heading")

        if (
            own_x is None
            or own_y is None
            or contact_x is None
            or contact_y is None
            or own_heading is None
        ):
            return None

        dx = float(contact_x) - float(own_x)
        dy = float(contact_y) - float(own_y)
        bearing = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
        return (bearing - float(own_heading) + 360.0) % 360.0

    @staticmethod
    def _bearing_sector(relative_bearing_deg: float | None) -> str | None:
        if relative_bearing_deg is None:
            return None

        bearing = relative_bearing_deg % 360.0
        if bearing < 22.5 or bearing >= 337.5:
            return "ahead"
        if bearing < 67.5:
            return "starboard bow"
        if bearing < 112.5:
            return "starboard beam"
        if bearing < 157.5:
            return "starboard quarter"
        if bearing < 202.5:
            return "astern"
        if bearing < 247.5:
            return "port quarter"
        if bearing < 292.5:
            return "port beam"
        return "port bow"

    def _own_ship_view(self, state: dict[str, Any]) -> dict[str, Any]:
        nav = state.get("nav", {})
        control_state = state.get("control_state", {})
        if not isinstance(nav, dict):
            nav = {}
        if not isinstance(control_state, dict):
            control_state = {}
        speed = nav.get("speed")
        paused = bool(state.get("paused", control_state.get("paused", False)))
        underway = bool(speed is not None and float(speed) > 0.2 and not paused)
        stopped = bool(speed is not None and float(speed) <= 0.2)
        movement_status = (
            "paused" if paused else "stalled" if stopped else "underway"
        )
        own_ship = {
            "name": state.get("vehicle"),
            "position": {
                "x": nav.get("x"),
                "y": nav.get("y"),
            },
            "heading": nav.get("heading"),
            "speed": nav.get("speed"),
            "target_speed": state.get("target_speed"),
            "recovery_speed": state.get("recovery_speed"),
            "paused": paused,
            "action_underway": bool(
                state.get("action_underway", control_state.get("action_underway", False))
            ),
            "underway": underway,
            "stopped": stopped,
            "movement_status": movement_status,
            "current_action": state.get("current_action"),
            "mission_objective": state.get("mission_objective"),
            "next_waypoint": state.get("next_waypoint"),
            "temporary_waypoint": state.get("temporary_waypoint"),
        }
        return {
            **own_ship,
            "mission_navigation": self._goal_geometry(own_ship, state.get("mission_objective")),
        }

    def captain_state_for_agent(
        self,
        situation_priority: str | None = None,
    ) -> dict[str, Any]:
        """Return a compact captain-facing view of the bridge state."""
        state = self.state_for_agent()
        own_ship = self._own_ship_view(state)
        mission_navigation = own_ship.get("mission_navigation")
        control = {
            "paused": own_ship["paused"],
            "stopped": own_ship["stopped"],
            "movement_status": own_ship["movement_status"],
            "target_speed": own_ship["target_speed"],
            "recovery_speed": own_ship["recovery_speed"],
            "action_underway": own_ship["action_underway"],
            "current_action": self._captain_action_view(
                own_ship.get("current_action")
            ),
        }
        target_speed = float(control.get("target_speed") or 0.0)
        recovery_speed = float(control.get("recovery_speed") or 0.0)
        reduced_speed = bool(
            target_speed > 0.0 and recovery_speed > target_speed + 1e-6
        )
        control["reduced_speed"] = reduced_speed
        allowed_actions = state.get("allowed_actions") or []

        contacts = [
            contact for contact in state.get("contacts", [])
            if isinstance(contact, dict)
        ]
        traffic_clear_for_recovery = not any(
            contact.get("risk") in {"critical", "high", "medium"}
            or float(contact.get("closing_speed") or 0.0) > 0.05
            for contact in contacts
        )
        restore_cruise = bool(
            traffic_clear_for_recovery
            and reduced_speed
            and not control["paused"]
            and not control["stopped"]
            and not control["action_underway"]
        )
        return {
            "version": self.version,
            "own_ship": {
                "name": own_ship.get("name"),
                "position": own_ship.get("position"),
                "heading": own_ship.get("heading"),
                "speed": own_ship.get("speed"),
                "underway": own_ship.get("underway"),
                "stopped": own_ship.get("stopped"),
                "movement_status": own_ship.get("movement_status"),
            },
            "mission": {
                "objective": state.get("mission_objective"),
                "next_waypoint": state.get("next_waypoint"),
                "temporary_waypoint": state.get("temporary_waypoint"),
                "navigation": mission_navigation,
            },
            "control": control,
            "speed_control": {
                "measured_speed": own_ship.get("speed"),
                "current_target": target_speed,
                "normal_cruise_target": recovery_speed,
                "reduced_speed": reduced_speed,
                "recovery_required": restore_cruise,
                "instruction": (
                    "Traffic is clear: restore normal cruise with set_speed."
                    if restore_cruise
                    else "Evaluate speed together with traffic conditions."
                ),
            },
            "decision_context": self._captain_decision_context(
                control,
                mission_navigation,
                situation_priority,
            ),
            "allowed_actions": allowed_actions,
        }

    @staticmethod
    def _captain_action_view(action: Any) -> dict[str, Any] | None:
        if not isinstance(action, dict):
            return None

        waypoints = action.get("waypoints")
        waypoint = None
        if isinstance(waypoints, list) and waypoints:
            last = waypoints[-1]
            if isinstance(last, dict):
                waypoint = [last.get("x"), last.get("y")]

        return {
            "action": action.get("action"),
            "temporary_waypoint_target": waypoint,
            "speed": action.get("speed"),
        }

    @staticmethod
    def _captain_decision_context(
        control: dict[str, Any],
        mission_navigation: Any,
        situation_priority: str | None = None,
    ) -> dict[str, Any]:
        action_underway = bool(control.get("action_underway"))
        route_status = (
            mission_navigation.get("route_status")
            if isinstance(mission_navigation, dict)
            else None
        )

        if control.get("paused"):
            return {
                "primary_decision": "resume_stopped_vessel",
                "default_when_clear": "resume",
                "hold_only_if": "specific_observed_hazard_makes_resume_unsafe",
                "available_outcomes": ["resume", "skip_temporary_safety_hold"],
            }
        if control.get("stopped"):
            return {
                "primary_decision": "resume_stopped_vessel",
                "default_when_clear": "resume",
                "hold_only_if": "specific_observed_hazard_makes_resume_unsafe",
                "available_outcomes": ["resume", "skip_temporary_safety_hold"],
            }
        if situation_priority == "clear" and control.get("reduced_speed"):
            return {
                "primary_decision": "restore_normal_cruise_speed",
                "reason": "traffic_clear_after_prior_speed_reduction",
                "required_outcome": "set_speed_to_recovery_target",
            }
        if action_underway:
            return {
                "primary_decision": "supervise_active_maneuver",
                "route_status_during_maneuver": route_status,
                "available_outcomes": [
                    "continue_active_maneuver",
                    "replace_course_or_speed_command",
                    "emergency_stop",
                ],
                "route_control": "mission_route_resumes_on_temporary_waypoint_completion",
                "mission_objective_status": "unchanged_original_destination",
            }
        if route_status == "away_from_goal":
            return {
                "primary_decision": "allow_normal_route_recovery",
                "available_outcomes": [
                    "maintain_course_for_route_controller",
                    "traffic_avoidance_if_required",
                ],
            }
        return {
            "primary_decision": "traffic_and_route_watch",
        }

    def lookout_brief_for_agent(self) -> dict[str, Any]:
        """Return a bridge-brief style view for the lookout."""
        state = self.state_for_agent()
        own_ship = self._own_ship_view(state)

        return {
            "time": {
                "version": self.version,
                "received_at": self.received_at,
                "moos_time": state.get("moos_time"),
            },
            "own_ship": own_ship,
            "traffic": [
                {
                    "name": contact.get("name"),
                    "type": contact.get("type"),
                    "position": {
                        "x": contact.get("nav", {}).get("x"),
                        "y": contact.get("nav", {}).get("y"),
                    },
                    "bearing": self._relative_bearing_deg(own_ship, contact),
                    "sector": self._bearing_sector(
                        self._relative_bearing_deg(own_ship, contact)
                    ),
                    "range": contact.get("range"),
                    "heading": contact.get("nav", {}).get("heading"),
                    "speed": contact.get("nav", {}).get("speed"),
                    "age_s": contact.get("age_s"),
                    "closing_speed": contact.get("closing_speed"),
                    "cpa_distance": contact.get("cpa_distance"),
                    "tcpa_s": contact.get("tcpa_s"),
                    "projection_status": contact.get("projection_status"),
                    "risk": contact.get("risk"),
                    "encounter_type": contact.get("encounter_type"),
                }
                for contact in state.get("contacts", [])
                if isinstance(contact, dict)
            ],
            "environment": state.get("environment"),
            "controls": {
                "allowed_actions": state.get("allowed_actions"),
            },
        }


class BridgeStateStore:
    """Fetch bridge state once and retain the latest complete snapshot."""

    def __init__(self, state_reader: StateReader) -> None:
        self._state_reader = state_reader
        self._lock = RLock()
        self._snapshot: BridgeSnapshot | None = None
        self._history: deque[dict[str, Any]] = deque(maxlen=8)
        self._active_encounters: dict[str, str] = {}
        self._contact_vessel_types: dict[str, str] = {}

    def _apply_encounter_persistence(self, retained_state: dict[str, Any]) -> None:
        """Latch active encounters through avoidance maneuvers until resolved."""
        provisional = BridgeSnapshot(0, 0.0, retained_state).state_for_agent()
        own_ship = BridgeSnapshot._own_ship_view(
            BridgeSnapshot(0, 0.0, retained_state), provisional
        )
        raw_by_name = {
            contact.get("name"): contact
            for contact in retained_state.get("contacts", [])
            if isinstance(contact, dict) and contact.get("name")
        }
        present: set[str] = set()

        for contact in provisional.get("contacts", []):
            if not isinstance(contact, dict) or not contact.get("name"):
                continue
            name = str(contact["name"])
            present.add(name)
            raw_contact = raw_by_name[name]
            vessel_type = str(raw_contact.get("vessel_type") or "").strip()
            if vessel_type:
                normalized = vessel_type.lower().replace("-", "_").replace(" ", "_")
                self._contact_vessel_types[name] = (
                    "power_driven"
                    if normalized in {"ship", "motor", "power", "power_driven"}
                    else normalized
                )
            if name in self._contact_vessel_types:
                raw_contact["_colregs_vessel_type"] = self._contact_vessel_types[name]

            encounter = contact.get("encounter_type")
            risk = contact.get("risk")
            established = encounter in {
                "we_are_overtaking_contact", "contact_overtaking_us"
            } or (
                encounter in {"head_on", "crossing"}
                and risk in {"medium", "high", "critical"}
            )
            if name not in self._active_encounters and established:
                self._active_encounters[name] = str(encounter)

            latched = self._active_encounters.get(name)
            if latched is None:
                continue
            age = float(contact.get("age_s") or 0.0)
            if latched in {"we_are_overtaking_contact", "contact_overtaking_us"}:
                bearing = contact.get("relative_bearing")
                if latched == "contact_overtaking_us":
                    nav = contact.get("nav") if isinstance(contact.get("nav"), dict) else {}
                    bearing = BridgeSnapshot._relative_bearing_deg(
                        {"position": {"x": nav.get("x"), "y": nav.get("y")}, "heading": nav.get("heading")},
                        {"nav": own_ship.get("position", {})},
                    )
                abaft = bearing is not None and 112.5 < float(bearing) < 247.5
                resolved = (
                    age <= COLREG_STALE_AFTER_S
                    and abaft
                    and float(contact.get("closing_speed") or 0.0) < -0.05
                    and float(contact.get("range") or 0.0) >= MIN_SAFE_CPA_M
                )
            else:
                resolved = (
                    age <= COLREG_STALE_AFTER_S
                    and risk not in {"medium", "high", "critical"}
                    and float(contact.get("closing_speed") or 0.0) <= 0.0
                    and contact.get("projection_status") == "no_future_cpa"
                )
            if resolved:
                self._active_encounters.pop(name, None)
            else:
                raw_contact["_active_encounter"] = latched

        for missing in set(self._active_encounters) - present:
            self._active_encounters.pop(missing, None)
            self._contact_vessel_types.pop(missing, None)

    def refresh(self) -> BridgeSnapshot:
        raw_state = self._state_reader()
        if not isinstance(raw_state, dict):
            raise TypeError("bridge state reader must return a JSON object")

        # Copy at the boundary: callers/readers cannot mutate retained state.
        retained_state = deepcopy(raw_state)
        with self._lock:
            self._apply_encounter_persistence(retained_state)
            version = 1 if self._snapshot is None else self._snapshot.version + 1
            self._snapshot = BridgeSnapshot(
                version=version,
                received_at=monotonic(),
                _raw_state=retained_state,
            )
            self._record_history(self._snapshot)
            return self._snapshot

    def current(self) -> BridgeSnapshot:
        """Read the latest sample without performing bridge I/O."""
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("bridge state has not been refreshed yet")
            return self._snapshot

    def _record_history(self, snapshot: BridgeSnapshot) -> None:
        state = snapshot.state_for_agent()
        nav = state.get("nav", {})
        if not isinstance(nav, dict):
            nav = {}

        self._history.append(
            {
                "version": snapshot.version,
                "moos_time": state.get("moos_time"),
                "x": nav.get("x"),
                "y": nav.get("y"),
                "heading": nav.get("heading"),
                "speed": nav.get("speed"),
                "contacts": [
                    {
                        "name": contact.get("name"),
                        "x": contact.get("nav", {}).get("x"),
                        "y": contact.get("nav", {}).get("y"),
                        "heading": contact.get("nav", {}).get("heading"),
                        "speed": contact.get("nav", {}).get("speed"),
                        "range": contact.get("range"),
                        "relative_bearing": contact.get("relative_bearing"),
                        "sector": contact.get("sector"),
                        "closing_speed": contact.get("closing_speed"),
                        "encounter_type": contact.get("encounter_type"),
                        "age_s": contact.get("age_s"),
                    }
                    for contact in state.get("contacts", [])
                    if isinstance(contact, dict)
                ],
            }
        )

    def recent_history(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            if limit <= 0:
                return []
            return deepcopy(list(self._history)[-limit:])
