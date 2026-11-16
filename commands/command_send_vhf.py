from typing import Literal

from .base_model import Model

from pydantic import Field


class SendVHF(Model):
    type: Literal["send_vhf"] = "send_vhf"

    channel: int = Field(ge=1, le=88)
    message: str = Field(min_length=1, max_length=512)
