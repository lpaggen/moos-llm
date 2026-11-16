from typing import Literal

from .base_model import Model


class RejoinRoute(Model):
    type: Literal["rejoin_route"] = "rejoin_route"
