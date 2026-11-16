from typing import Annotated, Literal

from .base_model import Model
from pydantic import Field


SignedCourseChange = Annotated[float, Field(ge=-60.0, le=60.0)]


class AlterCourse(Model):
    type: Literal["alter_course"] = "alter_course"
    degrees: SignedCourseChange
