from enum import StrEnum
from typing import Literal

from .base_model import Model


class SoundSignalCode(StrEnum):
    ALTERING_COURSE_TO_STARBOARD = "altering_course_to_starboard"
    ALTERING_COURSE_TO_PORT = "altering_course_to_port"
    DOUBT_OR_DANGER = "doubt_or_danger"


class SoundSignal(Model):
    type: Literal["sound_horn"] = "sound_horn"
    signal: SoundSignalCode
