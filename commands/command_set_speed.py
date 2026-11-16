from typing import Literal

from .base_model import HelmSpeed, Model


class SetSpeed(Model):
    type: Literal["set_speed"] = "set_speed"

    speed: HelmSpeed
