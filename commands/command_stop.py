from typing import Literal

from .base_model import Model


class Stop(Model):
    type: Literal["stop"] = "stop"
