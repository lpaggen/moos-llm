"""Contracts for COLREG decision validation."""

from typing import Literal

from pydantic import Field

from .base import Schema


class ColregsVerdict(Schema):
    verdict: Literal["approve", "reject", "indeterminate"]
    validated_rule_ids: list[int]
    violated_rule_ids: list[int]
    reason: str = Field(min_length=1, max_length=512)
    confidence: Literal["low", "medium", "high"]
    missing_facts: list[str]
