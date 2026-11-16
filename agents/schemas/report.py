from dataclasses import dataclass
from typing import Any


@dataclass
class AgentReport:
    cycle_id: int
    source: str
    data: dict[str, Any]
