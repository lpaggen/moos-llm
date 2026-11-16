from typing import Literal

from .base_model import Degrees, Model


class MaintainCourse(Model):
    type: Literal["maintain_course"] = "maintain_course"

    # None means maintain the course active when received.
    course_deg: Degrees | None = None
