"""Regenerate committed JSON Schema artifacts from the Pydantic models."""

from __future__ import annotations

import json
from pathlib import Path

from .captain import CaptainDecision
from .vhf import VHFAdvisory, VHFEvent, VHFInterpretation
from .lookout import LookoutAssessment


SCHEMAS = {
    "captain-decision.schema.json": CaptainDecision,
    "vhf-advisory.schema.json": VHFAdvisory,
    "vhf-event.schema.json": VHFEvent,
    "vhf-interpretation.schema.json": VHFInterpretation,
    "watch-assessment.schema.json": LookoutAssessment,
}


def main() -> None:
    output_dir = Path(__file__).parent / "json"
    output_dir.mkdir(exist_ok=True)
    for filename, model in SCHEMAS.items():
        schema = model.model_json_schema(mode="validation")
        (output_dir / filename).write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
