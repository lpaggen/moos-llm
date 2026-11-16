"""COLREG compliance review using deterministic retrieval and an LLM judge."""

from __future__ import annotations

import json
from typing import Any

from .colregs.classifier import classify_colregs
from .colregs.repository import ColregsRepository, default_repository
from .schemas.colregs import ColregsVerdict
from .skill_loader import load_skill
from tools.config_value import get


SYSTEM_PROMPT = load_skill("colregs")
DEFAULT_STALE_AFTER_S = float(get("lookout", "stale_after_s"))
DEFAULT_VISIBILITY_RANGE_M = float(get("colregs", "visibility_range_m"))
DEFAULT_VISIBILITY_CONDITION = get("colregs", "visibility_condition")
DEFAULT_NARROW_CHANNEL = get("colregs", "narrow_channel").lower() == "true"
DEFAULT_TSS = get("colregs", "traffic_separation_scheme").lower() == "true"
DEFAULT_MAX_OUTPUT_TOKENS = int(get("colregs", "max_output_tokens"))


class AgentColregs:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        repository: ColregsRepository | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.client = client
        self.model = model
        self.repository = repository or default_repository()
        self.max_output_tokens = max_output_tokens

    def build_request(
        self,
        decision: dict[str, Any],
        state: dict[str, Any],
        *,
        cycle_id: int,
    ) -> dict[str, Any]:
        context = classify_colregs(
            state,
            visibility_range_m=DEFAULT_VISIBILITY_RANGE_M,
            visibility_condition=DEFAULT_VISIBILITY_CONDITION,
            narrow_channel=DEFAULT_NARROW_CHANNEL,
            traffic_separation_scheme=DEFAULT_TSS,
            stale_after_s=DEFAULT_STALE_AFTER_S,
            decision=decision,
        )
        rules = self.repository.retrieve(context["applicable_rules"])
        return {
            "cycle_id": cycle_id,
            "captain_decision": decision,
            "navigation_state": {
                "own_ship": {
                    "vessel_type": state.get("vessel_type"),
                    "nav": state.get("nav"),
                    "paused": state.get("paused"),
                    "stopped": state.get("stopped"),
                },
                "control": state.get("control", state.get("control_state", {})),
                "action_underway": state.get("action_underway"),
            },
            "colregs_context": context,
            "retrieved_rules": rules,
        }

    @staticmethod
    def _check_verdict(verdict: ColregsVerdict, request: dict[str, Any]) -> None:
        applicable = set(request["colregs_context"]["applicable_rules"])
        validated = set(verdict.validated_rule_ids)
        violated = set(verdict.violated_rule_ids)
        unknown = (validated | violated) - applicable
        if unknown:
            raise ValueError(f"COLREGs verdict cited unretrieved rules: {sorted(unknown)}")
        if validated & violated:
            raise ValueError("COLREGs verdict marks rules both validated and violated")
        if verdict.verdict == "approve" and violated:
            raise ValueError("approved COLREGs verdict cannot contain violated rules")
        if verdict.verdict == "reject" and not violated:
            raise ValueError("rejected COLREGs verdict must identify a violated rule")

    def validate(
        self,
        decision: dict[str, Any],
        state: dict[str, Any],
        *,
        cycle_id: int,
    ) -> tuple[ColregsVerdict, dict[str, Any]]:
        request = self.build_request(decision, state, cycle_id=cycle_id)
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=self.max_output_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "colregs_verdict",
                    "strict": True,
                    "schema": ColregsVerdict.model_json_schema(),
                },
            },
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("COLREGs validator returned no verdict")
        verdict = ColregsVerdict.model_validate_json(content)
        self._check_verdict(verdict, request)
        return verdict, request
