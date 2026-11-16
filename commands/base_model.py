from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


Degrees = Annotated[float, Field(ge=0.0, lt=360.0)]
HelmSpeed = Annotated[float, Field(ge=0.0)]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


from .command_alter_course import AlterCourse
from .command_maintain_course import MaintainCourse
from .command_rejoin_route import RejoinRoute
from .command_resume import Resume
from .command_send_vhf import SendVHF
from .command_set_speed import SetSpeed
from .command_sound_horn import SoundSignal
from .command_stop import Stop


Command = Annotated[
    MaintainCourse
    | RejoinRoute
    | AlterCourse
    | SetSpeed
    | Stop
    | Resume
    | SoundSignal
    | SendVHF,
    Field(discriminator="type"),
]


class CommandMessage(Model):
    command_id: UUID = Field(default_factory=uuid4)
    vessel_id: str
    commands: tuple[Command, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_command_mix(self) -> "CommandMessage":
        communication_types = {"sound_horn", "send_vhf"}
        navigation = [c for c in self.commands if c.type not in communication_types]
        auxiliary = [c.type for c in self.commands if c.type in communication_types]
        if len(navigation) > 1:
            raise ValueError("at most one navigation command is allowed")
        if len(auxiliary) != len(set(auxiliary)):
            raise ValueError("auxiliary command types must be unique")
        return self
