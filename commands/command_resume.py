from typing import Literal

from .base_model import Model


class Resume(Model):
    type: Literal["resume"] = "resume"
