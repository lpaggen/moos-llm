"""Contract for the captain model's selected navigation decision."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .base import Schema


CaptainAction = Literal[
    "skip",
    "maintain_course",
    "rejoin_route",
    "alter_course",
    "set_speed",
    "stop",
    "resume",
]


class SendVHFCommand(Schema):
    type: Literal["send_vhf"]
    channel: int = Field(ge=1, le=88)
    message: str = Field(min_length=1, max_length=512)


class SoundHornCommand(Schema):
    type: Literal["sound_horn"]
    signal: Literal[
        "altering_course_to_starboard",
        "altering_course_to_port",
        "doubt_or_danger",
    ]


class ClearVHFInstructionCommand(Schema):
    type: Literal["clear_vhf_instruction"]
    message_id: UUID
    reason: str = Field(min_length=1, max_length=256)


AuxiliaryCommand = Annotated[
    SendVHFCommand | SoundHornCommand | ClearVHFInstructionCommand,
    Field(discriminator="type"),
]


class CaptainNoArgumentControl(Schema):
    action: Literal["skip", "maintain_course", "rejoin_route", "stop", "resume"]


class CaptainCourseControl(Schema):
    action: Literal["alter_course"]
    course_intent: Literal[
        "port_15",
        "port_30",
        "port_45",
        "port_60",
        "starboard_15",
        "starboard_30",
        "starboard_45",
        "starboard_60",
    ]


class CaptainSpeedControl(Schema):
    action: Literal["set_speed"]
    speed: float = Field(gt=0.0)


CaptainControl = Annotated[
    CaptainNoArgumentControl | CaptainCourseControl | CaptainSpeedControl,
    Field(discriminator="action"),
]


class CaptainRagDecision(Schema):
    """Structured review and final control from the RAG-assisted captain."""

    oow_verdict: Literal["approve", "reject"] = Field(
        description=(
            "Approve unless a material observation-supported safety defect requires "
            "a different final control; reject only when selecting that replacement."
        )
    )
    oow_critique: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "Review finding. For approval, state that no material defect was identified "
            "and confirm the decisive geometry; criticism is not mandatory."
        ),
    )
    controlling_contact: str = Field(min_length=1, max_length=128)
    own_role: Literal["give_way", "stand_on", "not_applicable", "unknown"]
    encounter_phase: Literal["clear", "monitor", "action_required", "emergency"]
    geometry_check: str = Field(min_length=1, max_length=512)
    control: CaptainControl
    reason: str = Field(min_length=1, max_length=1024)
    evidence_analogy: str = Field(min_length=1, max_length=512)
    evidence_difference: str = Field(min_length=1, max_length=512)
    transferable_lesson: str = Field(min_length=1, max_length=512)
    auxiliary_commands: list[AuxiliaryCommand] = Field(max_length=3)

    def to_navigation_decision(self) -> "CaptainDecision":
        degrees = 0.0
        course_intent = getattr(self.control, "course_intent", "none")
        if course_intent != "none":
            direction, magnitude = course_intent.split("_", 1)
            degrees = float(magnitude) * (-1.0 if direction == "port" else 1.0)
        return CaptainDecision(
            action=self.control.action,
            degrees=degrees,
            speed=float(getattr(self.control, "speed", 0.0)),
            reason=self.reason,
            auxiliary_commands=self.auxiliary_commands,
        )


class CaptainDecision(Schema):
    """High-level tactical decision selected by the captain."""

    action: CaptainAction
    degrees: float = Field(ge=-60.0, le=60.0)
    speed: float = Field(ge=0.0)
    reason: str = Field(min_length=1, max_length=1024)
    auxiliary_commands: list[AuxiliaryCommand] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_auxiliary_commands(self) -> "CaptainDecision":
        if self.action == "alter_course":
            if self.degrees == 0.0:
                raise ValueError("alter_course requires non-zero degrees")
        elif self.degrees != 0.0:
            raise ValueError("only alter_course may specify non-zero degrees")

        if self.action == "set_speed":
            if self.speed <= 0.0:
                raise ValueError("set_speed requires a positive speed")
        elif self.speed != 0.0:
            raise ValueError("only set_speed may specify non-zero speed")

        command_types = [command.type for command in self.auxiliary_commands]
        if len(command_types) != len(set(command_types)):
            raise ValueError("auxiliary command types must be unique")
        if self.action == "skip" and self.auxiliary_commands:
            raise ValueError("skip cannot include auxiliary commands")

        horn = next(
            (command for command in self.auxiliary_commands
             if isinstance(command, SoundHornCommand)),
            None,
        )
        if horn is None or horn.signal == "doubt_or_danger":
            return self
        if self.action != "alter_course":
            raise ValueError("course-alteration horn signals require alter_course")
        if horn.signal == "altering_course_to_starboard" and self.degrees <= 0:
            raise ValueError("starboard horn signal requires positive degrees")
        if horn.signal == "altering_course_to_port" and self.degrees >= 0:
            raise ValueError("port horn signal requires negative degrees")
        return self
