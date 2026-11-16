"""Contracts for VHF interpretation, auditing, and captain advisories."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .base import Schema


VHFIntent = Literal[
    "distress",
    "urgency",
    "safety_broadcast",
    "collision_warning",
    "position_report",
    "request_entry",
    "request_departure",
    "request_information",
    "maneuver_request",
    "traffic_information",
    "instruction",
    "acknowledgement",
    "conversation",
    "unknown",
]
VHFAction = Literal["none", "stop", "resume"]
VHFRequestedAction = Literal[
    "none",
    "maintain_course",
    "alter_course",
    "rejoin_route",
    "set_speed",
    "stop",
    "resume",
    "proceed",
    "skip",
]
VHFTurnDirection = Literal["none", "port", "starboard"]
VHFDirectiveLifetime = Literal[
    "transient",
    "until_cancelled",
    "until_condition_met",
]


class VHFInterpretation(Schema):
    """Structured output produced by the VHF interpretation model."""

    speaker: str | None = Field(
        default=None,
        description="Named transmitting station after 'this is', when stated.",
    )
    addressee: str | None = Field(
        default=None,
        description="Station called before 'this is', when stated.",
    )
    addressed_to_own_ship: bool = Field(
        default=False,
        description="True when the stated addressee matches operational_context.own_ship.",
    )
    source_type: Literal["vessel", "vts", "unknown"] = "unknown"
    identity_status: Literal["verified", "ambiguous", "unverified"] = "unverified"
    intent: VHFIntent = Field(
        description=(
            "Honor explicit SMCP markers: WARNING about navigation danger is "
            "collision_warning, INFORMATION is traffic_information, INSTRUCTION "
            "is instruction, and REQUEST for a manoeuvre is maneuver_request. "
            "request_information applies only when the speaker asks for information."
        )
    )
    location: str | None = None
    destination: str | None = None
    heading_degrees: int | None = Field(default=None, ge=0, le=359)
    speed_knots: float | None = Field(default=None, ge=0)
    normalized_message: str
    requested_action: VHFRequestedAction = Field(
        default="none",
        description="Requested control using the current navigation action vocabulary.",
    )
    requested_turn_direction: VHFTurnDirection = Field(
        default="none",
        description="Port/starboard only for requested_action=alter_course.",
    )
    requested_turn_degrees: float | None = Field(default=None, gt=0.0, le=60.0)
    requested_speed_knots: float | None = Field(default=None, gt=0.0)
    requested_course_degrees: int | None = Field(default=None, ge=0, le=359)
    directive_lifetime: VHFDirectiveLifetime = Field(
        default="transient",
        description=(
            "How long an explicit instruction or request remains operationally "
            "active. Use until_cancelled only for wording such as until further "
            "notice or await further instructions; use until_condition_met only "
            "when the transmission states an observable terminating condition."
        ),
    )
    directive_end_condition: str | None = Field(
        default=None,
        description=(
            "Literal cancellation condition stated by the transmission. Required "
            "for until_condition_met; otherwise null."
        ),
    )
    reported_facts: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    missing_critical_information: list[str] = Field(default_factory=list)
    relay_required: bool = Field(
        description=(
            "True only for a clear stop/hold order or a clear release to "
            "resume while operational_context.currently_paused is true."
        )
    )
    recommended_action: VHFAction = Field(
        default="none",
        description=(
            "stop for a clear hold; resume for a clear proceed/release while "
            "currently paused; otherwise none."
        ),
    )
    relay_reason: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_required: bool
    suggested_clarification: str | None = None

    @model_validator(mode="after")
    def validate_relay_decision(self) -> "VHFInterpretation":
        if self.identity_status == "verified" and (
            not self.speaker or not self.addressee
        ):
            raise ValueError(
                "verified identity requires both a named speaker and addressee"
            )
        if self.addressed_to_own_ship and not self.addressee:
            raise ValueError("addressed_to_own_ship requires a named addressee")
        if self.intent == "maneuver_request" and self.requested_action == "none":
            raise ValueError("maneuver_request requires a requested action")
        if self.relay_required and self.recommended_action == "none":
            raise ValueError("a relayed transmission requires an action")
        if not self.relay_required and self.recommended_action != "none":
            raise ValueError("a non-relayed transmission cannot recommend an action")
        if self.requested_action == "alter_course":
            if self.requested_turn_direction not in {"port", "starboard"}:
                raise ValueError(
                    "alter_course requests require requested_turn_direction port or starboard"
                )
        elif (
            self.requested_turn_direction != "none"
            or self.requested_turn_degrees is not None
        ):
            raise ValueError(
                "non-alter_course requests require turn direction none and no turn magnitude"
            )
        if self.requested_action != "set_speed" and self.requested_speed_knots is not None:
            raise ValueError(
                "only set_speed requests may specify requested_speed_knots"
            )
        if self.directive_lifetime == "until_condition_met":
            if not self.directive_end_condition:
                raise ValueError(
                    "until_condition_met requires directive_end_condition"
                )
        elif self.directive_end_condition is not None:
            raise ValueError(
                "directive_end_condition is only valid for until_condition_met"
            )
        return self

    def actionable(self, minimum_confidence: float = 0.80) -> bool:
        return (
            self.relay_required
            and self.recommended_action in {"stop", "resume"}
            and self.addressed_to_own_ship
            and not self.clarification_required
            and self.confidence >= minimum_confidence
        )


class AudioSource(Schema):
    """Authoritative receiver-side metadata; never generated by the LLM."""

    device: str | None = None
    channel: int | None = Field(default=None, ge=1)
    audio_file: str | None = None
    audio_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_kind: Literal["audio", "scenario_text"] = "audio"
    scenario_id: str | None = None
    scenario_event_id: str | None = None
    injected_at_sim_s: float | None = None


class TranscriptionRecord(Schema):
    """Raw ASR result retained for debugging and training."""

    text: str
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    model: str


class VHFEvent(Schema):
    """Complete immutable VHF audit record."""

    schema_version: Literal[1] = 1
    message_id: UUID
    received_at: datetime
    source: AudioSource
    transcription: TranscriptionRecord
    interpretation: VHFInterpretation


class VHFAdvisory(Schema):
    """Minimal operational payload delivered to the captain."""

    type: Literal["vhf_advisory"] = "vhf_advisory"
    schema_version: Literal[1] = 1
    message_id: UUID
    speaker: str | None = None
    addressee: str | None = None
    addressed_to_own_ship: bool = False
    source_type: Literal["vessel", "vts", "unknown"] = "unknown"
    identity_status: Literal["verified", "ambiguous", "unverified"] = "unverified"
    message: str
    intent: VHFIntent
    requested_action: VHFRequestedAction = "none"
    requested_turn_direction: VHFTurnDirection = "none"
    requested_turn_degrees: float | None = Field(default=None, gt=0.0, le=60.0)
    requested_speed_knots: float | None = Field(default=None, gt=0.0)
    requested_course_degrees: int | None = Field(default=None, ge=0, le=359)
    directive_lifetime: VHFDirectiveLifetime = "transient"
    directive_end_condition: str | None = None
    reported_facts: list[str] = Field(default_factory=list)
    recommended_action: VHFAction
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_required: bool
