"""Interpret bridge contact state and produce a captain-facing watch assessment."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any

from pydantic import ValidationError

from bridge.state import BridgeSnapshot

from .skill_loader import load_skill
from .schemas.lookout import LookoutAssessment, LookoutTime
from tools.config_value import get


SYSTEM_PROMPT = load_skill("watch")
LOGGER = logging.getLogger(__name__)

DEFAULT_STALE_AFTER_S = float(get("lookout", "stale_after_s"))
DEFAULT_MAX_OUTPUT_TOKENS = int(get("lookout", "max_output_tokens"))
DEFAULT_MODEL = get("models", "default_local")
try:
    IMMEDIATE_WATCH_RANGE_M = float(get("lookout", "immediate_watch_range_m"))
except LookupError:
    IMMEDIATE_WATCH_RANGE_M = 1000.0
try:
    GOAL_PROXIMITY_M = float(get("lookout", "goal_proximity_m"))
except LookupError:
    GOAL_PROXIMITY_M = 800.0


RISK_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


class AgentLookout:
    """Convert bridge state into a structured watch assessment."""

    def __init__(
        self,
        client: Any,
        model: str = DEFAULT_MODEL,
        *,
        captain_queue: asyncio.Queue,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.client = client
        self.model = model
        self.captain_queue = captain_queue
        self.stale_after_s = stale_after_s
        self.max_output_tokens = max_output_tokens

        if stale_after_s < 0:
            raise ValueError("stale_after_s cannot be negative")

        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")


    @staticmethod
    def _float_or_default(value: Any, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default

        if result != result or result in (float("inf"), float("-inf")):
            return default

        return result

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        """Replace non-finite floats, which are not valid JSON, with null."""
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_safe(item) for item in value]
        return value

    @classmethod
    def _is_closing(cls, contact: dict[str, Any]) -> bool:
        return cls._float_or_default(contact.get("closing_speed"), 0.0) > 0.0

    @classmethod
    def _normalize_contact(cls, contact: dict[str, Any]) -> dict[str, Any]:
        return {
            **contact,
            "range": cls._float_or_default(contact.get("range"), float("inf")),
            "cpa_distance": cls._float_or_default(
                contact.get("cpa_distance"),
                float("inf"),
            ),
            "tcpa_s": (
                None
                if contact.get("tcpa_s") is None
                else cls._float_or_default(contact.get("tcpa_s"), float("inf"))
            ),
            "closing_speed": cls._float_or_default(
                contact.get("closing_speed"),
                0.0,
            ),
            "age_s": cls._float_or_default(contact.get("age_s"), 0.0),
        }

    @staticmethod
    def _contact_priority(
        contact: dict[str, Any],
    ) -> tuple[float, ...]:
        contact = AgentLookout._normalize_contact(contact)
        tcpa = contact.get("tcpa_s")

        return (
            float(RISK_ORDER.get(str(contact.get("risk")), 4)),
            0.0 if AgentLookout._is_closing(contact) else 1.0,
            float("inf") if tcpa is None else max(0.0, float(tcpa)),
            float(contact.get("cpa_distance", float("inf"))),
            float(contact.get("range", float("inf"))),
        )

    def _contact_justification(
        self,
        contact: dict[str, Any],
    ) -> str:
        contact = self._normalize_contact(contact)
        name = contact["name"]
        risk = contact["risk"]
        encounter = str(
            contact.get("encounter_type", "unknown")
        ).replace("_", " ")

        cpa = contact.get("cpa_distance", float("inf"))
        tcpa = contact.get("tcpa_s")
        closing = self._is_closing(contact)

        timing = (
            "no future CPA"
            if tcpa is None
            else f"TCPA {self._format_duration(float(tcpa))}"
        )

        motion = "closing" if closing else "not closing"

        return (
            f"{name}: {risk} risk, {encounter}, {motion}; "
            f"CPA {cpa:.1f} m, {timing}."
        )

    @staticmethod
    def _format_duration(seconds: float | None) -> str | None:
        if seconds is None or not math.isfinite(seconds) or seconds < 0:
            return None
        if seconds < 120.0:
            return f"{seconds:.1f} s"
        if seconds < 3600.0:
            return f"{seconds / 60.0:.1f} min"
        hours = int(seconds // 3600.0)
        minutes = int(round((seconds - hours * 3600.0) / 60.0))
        if minutes == 60:
            hours += 1
            minutes = 0
        return f"{hours} h {minutes} min"

    @classmethod
    def _proximity_level(cls, contact: dict[str, Any]) -> str:
        distance = cls._float_or_default(contact.get("range"), float("inf"))

        if distance <= 100.0:
            return "critical"
        if distance <= 250.0:
            return "high"
        if distance <= 500.0:
            return "medium"
        if distance <= 1000.0:
            return "low"
        return "clear"

    @classmethod
    def _is_immediate_contact(cls, contact: dict[str, Any]) -> bool:
        distance = cls._float_or_default(contact.get("range"), float("inf"))
        cpa = contact.get("cpa_distance", float("inf"))
        tcpa = contact.get("tcpa_s")
        closing_speed = cls._float_or_default(contact.get("closing_speed"), 0.0)
        encounter = str(contact.get("encounter_type") or "").lower()
        risk = str(contact.get("risk") or "").lower()
        bearing = contact.get("relative_bearing")

        if distance <= IMMEDIATE_WATCH_RANGE_M:
            return True
        if closing_speed > 0.0:
            return True
        if cls._float_or_default(cpa, float("inf")) <= IMMEDIATE_WATCH_RANGE_M:
            return True
        if tcpa is not None and cls._float_or_default(tcpa, float("inf")) <= 900.0:
            return True
        if risk in {"critical", "high", "medium"}:
            return True
        if encounter in {
            "crossing",
            "head_on",
            "contact_overtaking_us",
            "we_are_overtaking_contact",
        }:
            return True
        if isinstance(bearing, str) and bearing in {"ahead", "port bow", "starboard bow", "abeam"}:
            return distance <= max(IMMEDIATE_WATCH_RANGE_M, GOAL_PROXIMITY_M)
        return False

    @classmethod
    def _is_suppressed_contact(cls, contact: dict[str, Any]) -> bool:
        distance = cls._float_or_default(contact.get("range"), float("inf"))
        closing_speed = cls._float_or_default(contact.get("closing_speed"), 0.0)
        tcpa = contact.get("tcpa_s")
        cpa = cls._float_or_default(contact.get("cpa_distance"), float("inf"))
        bearing = str(contact.get("relative_bearing") or "").lower()
        return (
            distance > IMMEDIATE_WATCH_RANGE_M
            and closing_speed <= 0.0
            and (tcpa is None or cls._float_or_default(tcpa, float("inf")) > 900.0)
            and cpa > IMMEDIATE_WATCH_RANGE_M
            and bearing in {"astern", "port quarter", "starboard quarter", ""}
        )

    def _is_fresh_contact(self, contact: dict[str, Any]) -> bool:
        """Return whether contact motion is recent enough for CPA projection."""
        return self._float_or_default(
            contact.get("age_s"),
            float("inf"),
        ) <= self.stale_after_s

    @classmethod
    def _contact_track(
        cls,
        name: str,
        history: list[dict[str, Any]],
        stale_after_s: float,
    ) -> list[dict[str, Any]]:
        track: list[dict[str, Any]] = []
        for sample in history:
            for contact in sample.get("contacts", []):
                if not isinstance(contact, dict) or contact.get("name") != name:
                    continue
                if cls._float_or_default(
                    contact.get("age_s"), float("inf")
                ) > stale_after_s:
                    continue
                track.append(
                    {
                        "version": sample.get("version"),
                        "moos_time": sample.get("moos_time"),
                        "range": contact.get("range"),
                        "relative_bearing": contact.get("relative_bearing"),
                        "sector": contact.get("sector"),
                        "own_heading": sample.get("heading"),
                        "contact_heading": contact.get("heading"),
                        "closing_speed": contact.get("closing_speed"),
                    }
                )
        return track

    @classmethod
    def _is_parallel_track(cls, track: list[dict[str, Any]]) -> bool:
        """Recognize steady same-direction motion across at least three frames."""
        if len(track) < 3:
            return False

        ranges: list[float] = []
        bearings: list[float] = []
        for sample in track:
            own_heading = cls._float_or_default(
                sample.get("own_heading"), float("inf")
            )
            contact_heading = cls._float_or_default(
                sample.get("contact_heading"), float("inf")
            )
            if not math.isfinite(own_heading) or not math.isfinite(contact_heading):
                return False
            heading_difference = abs(
                ((contact_heading - own_heading + 180.0) % 360.0) - 180.0
            )
            if heading_difference > 30.0:
                return False
            ranges.append(cls._float_or_default(sample.get("range"), float("inf")))
            bearings.append(
                cls._float_or_default(sample.get("relative_bearing"), float("inf"))
            )

        if not all(math.isfinite(value) for value in ranges + bearings):
            return False
        range_tolerance = max(25.0, sum(ranges) / len(ranges) * 0.1)
        range_stable = max(ranges) - min(ranges) <= range_tolerance
        radians = [math.radians(value) for value in bearings]
        mean = math.atan2(
            sum(math.sin(value) for value in radians),
            sum(math.cos(value) for value in radians),
        )
        bearing_spread = max(
            abs(((math.degrees(value - mean) + 180.0) % 360.0) - 180.0)
            for value in radians
        )
        return range_stable and bearing_spread <= 15.0

    @staticmethod
    def _deterministic_priority(report: dict[str, Any]) -> str:
        """Derive alert priority only from deterministic bridge risk."""
        risks = {
            str(contact.get("risk") or "low").lower()
            for contact in report.get("contacts", [])
            if isinstance(contact, dict)
        }
        if "critical" in risks:
            return "emergency"
        if "high" in risks:
            return "danger"
        if "medium" in risks or risks:
            return "caution"
        return "clear"

    @classmethod
    def _enforce_encounter_semantics(
        cls,
        assessment: LookoutAssessment,
        report: dict[str, Any],
    ) -> LookoutAssessment:
        """Replace model prose that contradicts deterministic encounter types."""
        contacts = sorted([
            contact for contact in report.get("contacts", [])
            if isinstance(contact, dict) and contact.get("name")
        ], key=cls._contact_priority)
        if not contacts:
            return assessment

        encounter_words = {
            "head_on": {"overtak", "crossing", "parallel", "diverging"},
            "parallel": {"head-on", "head on", "crossing", "overtak"},
            "crossing": {"head-on", "head on", "parallel", "overtak"},
            "diverging": {"head-on", "head on", "crossing", "overtak"},
            "contact_overtaking_us": {"head-on", "head on", "crossing", "parallel"},
            "we_are_overtaking_contact": {"head-on", "head on", "crossing", "parallel"},
            "stationary_contact": {
                "head-on", "head on", "crossing", "parallel", "overtak",
                "underway", "approach",
            },
        }
        prose = " ".join(
            [assessment.brief, assessment.situation]
            + [item.reason for item in assessment.decisive_contacts]
        ).lower()
        top_risk = str(contacts[0].get("risk") or "low").lower()
        wrong_primary = (
            top_risk in {"critical", "high"}
            and not assessment.brief.lower().startswith(
                str(contacts[0]["name"]).lower()
            )
        )
        contradictory = wrong_primary or any(
            token in prose
            for contact in contacts
            for token in encounter_words.get(
                str(contact.get("encounter_type") or ""), set()
            )
        )
        if not contradictory:
            return assessment

        primary = contacts[0]
        name = str(primary["name"])
        encounter = str(primary.get("encounter_type") or "unknown").replace("_", " ")
        risk = str(primary.get("risk") or "unknown")
        sector = str(primary.get("sector") or "unknown sector")
        cpa = cls._float_or_default(primary.get("cpa_distance"), float("inf"))
        tcpa = primary.get("tcpa_display") or "no future CPA"
        closing = cls._float_or_default(primary.get("closing_speed"), 0.0)
        brief = f"{name} {encounter}, {risk} risk; CPA {cpa:.1f} m in {tcpa}"
        motion = (
            f"own ship closing at {closing:.1f} m/s"
            if encounter == "stationary contact" and closing > 0.0
            else f"closing {closing:.1f} m/s"
        )
        situation = (
            f"{name} {sector}, {encounter}, {motion}; "
            f"CPA {cpa:.1f} m in {tcpa}."
        )
        decisive = []
        for contact in contacts[:3]:
            contact_encounter = str(
                contact.get("encounter_type") or "unknown"
            ).replace("_", " ")
            contact_cpa = cls._float_or_default(
                contact.get("cpa_distance"), float("inf")
            )
            contact_tcpa = contact.get("tcpa_display") or "no future CPA"
            decisive.append({
                "name": str(contact["name"]),
                "reason": (
                    f"{contact_encounter}, CPA {contact_cpa:.1f} m in {contact_tcpa}"
                )[:80],
            })
        LOGGER.warning("Lookout encounter prose contradicted bridge geometry; replaced")
        return LookoutAssessment.model_validate(
            {
                **assessment.model_dump(),
                "brief": brief[:120],
                "situation": situation[:120],
                "decisive_contacts": decisive,
            }
        )

    @staticmethod
    def _underway(own_ship: dict[str, Any]) -> bool:
        speed = own_ship.get("speed")
        paused = bool(own_ship.get("paused", False))
        return (speed is not None and float(speed) > 0.2) and not paused

    def _watch_summary(
        self,
        contacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        contacts = [self._normalize_contact(contact) for contact in contacts]
        ordered = sorted(
            contacts,
            key=self._contact_priority,
        )

        risk_counts = {
            level: sum(
                contact.get("risk") == level
                for contact in contacts
            )
            for level in (
                "critical",
                "high",
                "medium",
                "low",
            )
        }

        stale_contacts = [
            contact["name"]
            for contact in contacts
            if contact.get("age_s", 0.0) > self.stale_after_s
        ]

        proximity_levels = [
            self._proximity_level(contact)
            for contact in contacts
        ]
        proximity_counts = {
            level: sum(item == level for item in proximity_levels)
            for level in ("critical", "high", "medium", "low", "clear")
        }
        close_contacts = [
            contact
            for contact in ordered
            if self._proximity_level(contact) in {"critical", "high", "medium"}
        ]

        relevant = [
            contact
            for contact in ordered
            if (
                contact.get("risk") != "low"
                or self._is_closing(contact)
                or self._proximity_level(contact) in {"critical", "high", "medium"}
            )
        ]

        if not contacts and not any(
            self._is_immediate_contact(contact) for contact in contacts
        ):
            level = "clear"
            headline = "No navigation-relevant contacts currently require attention."

        elif risk_counts["critical"] or proximity_counts["critical"]:
            level = "critical"
            headline = (
                "Immediate collision risk requires captain attention."
            )

        elif risk_counts["high"] or proximity_counts["high"] or len(close_contacts) >= 3:
            level = "high"
            headline = "A close-quarters situation is developing."

        elif risk_counts["medium"] or proximity_counts["medium"]:
            level = "medium"
            headline = (
                "Traffic is close enough to require active watchkeeping."
            )

        else:
            level = "low"
            headline = (
                "No material collision risk is currently predicted."
            )

        nearest = ordered[0] if ordered else None
        situation_parts: list[str] = []
        if nearest and nearest.get("name"):
            side = nearest.get("relative_bearing")
            side_text = (
                f"on the {side}"
                if isinstance(side, str) and side
                else "in the vicinity"
            )
            encounter = str(nearest.get("encounter_type", "unknown")).replace(
                "_",
                " ",
            )
            closing = "closing" if self._is_closing(nearest) else "not closing"
            proximity = self._proximity_level(nearest)
            if proximity in {"critical", "high"}:
                intersection = "paths may intersect"
            elif proximity == "medium":
                intersection = "paths need active monitoring"
            else:
                intersection = "paths do not appear likely to intersect"
            situation_parts.append(
                f"Nearest contact {nearest['name']} is {side_text}, {encounter}, {closing}; {intersection}."
            )

        if len(contacts) > 1:
            situation_parts.append(
                f"{len(contacts)} contacts total; traffic is "
                f"{'clustered' if len(contacts) > 3 else 'moderate'}."
            )

        if close_contacts:
            situation_parts.append(
                "Close contacts: "
                + ", ".join(
                    f"{contact['name']} {contact.get('range', float('inf')):.1f} m"
                    for contact in close_contacts[:3]
                )
                + "."
            )

        if stale_contacts:
            situation_parts.append(
                f"Stale contact data for {', '.join(stale_contacts[:3])}."
            )

        if not situation_parts:
            situation_parts.append("No nearby traffic requiring attention.")

        situation = " ".join(situation_parts)

        return {
            "overall_risk": level,
            "headline": headline,
            "contact_count": len(contacts),
            "risk_counts": risk_counts,
            "priority_order": [
                contact["name"]
                for contact in ordered
            ],
            "most_urgent_contact": (
                ordered[0]["name"]
                if ordered
                else None
            ),
            "stale_contacts": stale_contacts,
            "proximity_counts": proximity_counts,
            "situation": situation,
            "justifications": (
                [
                    self._contact_justification(contact)
                    for contact in relevant[:4]
                ]
                or [
                    "No closing or elevated-risk contact "
                    "requires explanation."
                ]
            ),
        }

    def build_watch_report(
        self,
        bridge_snapshot: BridgeSnapshot,
        own_ship_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Extract the deterministic facts the lookout needs from the bridge.
        """
        state = bridge_snapshot.state_for_agent()
        bridge_brief = bridge_snapshot.lookout_brief_for_agent()

        own_ship = bridge_brief.get("own_ship", {})
        mission = own_ship.get("mission_objective") or state.get("mission_objective")

        all_contacts = []
        history = own_ship_history or []
        for contact in bridge_brief.get("traffic", []):
            position = contact.get("position", {})
            normalized = self._normalize_contact(
                {
                    "name": contact.get("name"),
                    "x": position.get("x"),
                    "y": position.get("y"),
                    "heading": contact.get("heading"),
                    "speed": contact.get("speed"),
                    "range": contact.get("range"),
                    "age_s": contact.get("age_s"),
                    "relative_bearing": contact.get("bearing"),
                    "sector": contact.get("sector"),
                    "closing_speed": contact.get("closing_speed"),
                    "cpa_distance": contact.get("cpa_distance"),
                    "tcpa_s": contact.get("tcpa_s"),
                    "tcpa_display": self._format_duration(contact.get("tcpa_s")),
                    "projection_status": contact.get("projection_status"),
                    "encounter_type": contact.get("encounter_type"),
                    "risk": contact.get("risk"),
                }
            )
            track = self._contact_track(
                str(normalized.get("name")),
                history,
                self.stale_after_s,
            )
            # Track stability can confirm an ambiguous parallel/diverging
            # contact, but it must never erase a geometrically established
            # overtaking or crossing encounter.
            if (
                normalized.get("encounter_type") in {"parallel", "diverging"}
                and self._is_parallel_track(track)
            ):
                normalized["encounter_type"] = "parallel"
                normalized["motion_trend"] = "steady_parallel"
            elif len(track) >= 2:
                normalized["motion_trend"] = "evolving"
            else:
                normalized["motion_trend"] = "insufficient_history"
            normalized["track_history"] = track
            all_contacts.append(normalized)
        fresh_contacts = [
            contact
            for contact in all_contacts
            if self._is_fresh_contact(contact)
        ]
        stale_contacts = [
            contact
            for contact in all_contacts
            if not self._is_fresh_contact(contact)
        ]
        contacts = sorted([
            contact
            for contact in fresh_contacts
            if self._is_immediate_contact(contact)
        ], key=self._contact_priority)
        distant_contacts = [
            contact
            for contact in fresh_contacts
            if not self._is_immediate_contact(contact)
        ]

        suppressed_count = len(distant_contacts) + len(stale_contacts)
        if contacts:
            watch_note = (
                f"{len(contacts)} navigation-relevant ship(s) within or projected into "
                f"{int(IMMEDIATE_WATCH_RANGE_M)} m."
            )
        else:
            watch_note = (
                "No navigation-relevant contacts currently require attention. "
                f"{suppressed_count} distant or stale contact(s) suppressed."
            )

        return {
            "own_ship": {
                "x": own_ship.get("position", {}).get("x"),
                "y": own_ship.get("position", {}).get("y"),
                "heading": own_ship.get("heading"),
                "speed": own_ship.get("speed"),
            },
            "mission": {
                "x": mission.get("x") if isinstance(mission, dict) else None,
                "y": mission.get("y") if isinstance(mission, dict) else None,
            },
            "contacts": [
                contact
                for contact in contacts
                if self._is_immediate_contact(contact)
            ],
            "watch_note": watch_note,
        }


    def assess(
        self,
        bridge_snapshot: BridgeSnapshot,
        own_ship_history: list[dict[str, Any]] | None = None,
        report: dict[str, Any] | None = None,
    ) -> LookoutAssessment:
        """Ask the lookout LLM to assess the current navigation situation."""

        if report is None:
            report = self.build_watch_report(
                bridge_snapshot,
                own_ship_history=own_ship_history,
            )
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    self._json_safe(report),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            },
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "watch_assessment",
                "strict": True,
                "schema": LookoutAssessment.model_json_schema(),
            },
        }

        last_error: Exception | None = None
        for attempt in range(1, 3):
            response = None
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_tokens=self.max_output_tokens,
                    messages=messages,
                    response_format=response_format,
                )
                content = response.choices[0].message.content
                if not content:
                    raise RuntimeError("Lookout returned no assessment")
                assessment = LookoutAssessment.model_validate_json(content)
                deterministic_priority = self._deterministic_priority(report)
                if assessment.priority != deterministic_priority:
                    LOGGER.warning(
                        "Lookout priority %s overridden by deterministic priority %s",
                        assessment.priority,
                        deterministic_priority,
                    )
                    assessment = assessment.model_copy(
                        update={"priority": deterministic_priority}
                    )
                assessment = self._enforce_encounter_semantics(
                    assessment,
                    report,
                )
                # Time/version are telemetry, not prose for the model to
                # reproduce. Overwrite hallucinated values deterministically.
                assessment = assessment.model_copy(update={
                    "time": LookoutTime(
                        version=bridge_snapshot.version,
                        received_at=bridge_snapshot.received_at,
                        moos_time=bridge_snapshot.state_for_agent().get("moos_time"),
                    )
                })
                return assessment
            except (ValidationError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                choice = (
                    response.choices[0]
                    if response is not None and response.choices
                    else None
                )
                raw_content = (
                    choice.message.content
                    if choice is not None and choice.message.content
                    else ""
                )
                usage = getattr(response, "usage", None)
                preview = raw_content[:240].encode("unicode_escape").decode("ascii")
                LOGGER.warning(
                    "Lookout response rejected (attempt=%d, finish_reason=%r, "
                    "completion_tokens=%r, chars=%d, preview=%r): %s",
                    attempt,
                    getattr(choice, "finish_reason", None),
                    getattr(usage, "completion_tokens", None),
                    len(raw_content),
                    preview,
                    exc,
                )
                if attempt == 1:
                    continue
                raise RuntimeError(
                    "Lookout returned invalid JSON twice"
                ) from last_error

        raise RuntimeError("Lookout returned invalid JSON") from last_error


    async def relay_to_captain(
        self,
        assessment: LookoutAssessment,
    ) -> None:
        """Publish the lookout assessment to the captain."""

        await self.captain_queue.put(
            {
                "source": "lookout",
                "report": assessment.model_dump(),
            }
        )
