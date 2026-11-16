"""Contract for the lookout officer model output."""

from typing import Literal

from pydantic import Field

from .base import Schema


class DecisiveContact(Schema):
    name: str
    reason: str = Field(max_length=80)


class LookoutTime(Schema):
    version: int
    received_at: float
    moos_time: float | None = None


class LookoutOwnShip(Schema):
    name: str | None = None
    position: dict[str, float | None]
    heading: float | None = None
    speed: float | None = None
    paused: bool = False
    action_underway: bool = False
    current_action: str | None = None
    mission_objective: dict[str, float | None] | None = None


class LookoutOwnShipSample(Schema):
    version: int | None = None
    moos_time: float | None = None
    x: float | None = None
    y: float | None = None
    heading: float | None = None
    speed: float | None = None


class LookoutTrafficTrack(Schema):
    name: str | None = None
    type: str | None = None
    bearing: str | None = None
    sector: str | None = None
    range: float | None = None
    heading: float | None = None
    speed: float | None = None
    age_s: float | None = None
    closing_speed: float | None = None
    cpa_distance: float | None = None
    tcpa_s: float | None = None
    risk: str | None = None
    encounter_type: str | None = None


class LookoutControls(Schema):
    allowed_actions: list[str] | None = None


class LookoutAssessment(Schema):
    """Operational traffic brief delivered to the captain."""

    time: LookoutTime | None = None
    priority: Literal["clear", "caution", "danger", "emergency"]
    brief: str = Field(max_length=120)
    decisive_contacts: list[DecisiveContact] = Field(max_length=3)
    situation: str = Field(max_length=120)
    caveat: str = Field(max_length=80)


WatchAssessment = LookoutAssessment
