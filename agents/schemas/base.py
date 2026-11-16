"""Common schema configuration."""

from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    """Strict base class for externally visible payloads."""

    model_config = ConfigDict(extra="forbid")
