"""Exact rule-number lookup after deterministic applicability classification."""

from typing import Any

from agents.colregs.classifier import classify_colregs
from experiments.qwen8b_colregs_rag.retrieval import DEFAULT_GUIDANCE, load_ship_guidance
from tools.config_value import get


class DecisionTreeGuidance:
    def __init__(self) -> None:
        self.guidance = load_ship_guidance(DEFAULT_GUIDANCE)

    def select(self, state: dict[str, Any], *, stale_after_s: float) -> dict[str, Any]:
        context = classify_colregs(
            state,
            visibility_range_m=float(get("colregs", "visibility_range_m")),
            visibility_condition=get("colregs", "visibility_condition"),
            narrow_channel=get("colregs", "narrow_channel").lower() == "true",
            traffic_separation_scheme=get("colregs", "traffic_separation_scheme").lower() == "true",
            stale_after_s=stale_after_s,
        )
        missing = set(context["applicable_rules"]) - self.guidance.keys()
        if missing:
            raise ValueError(f"No simplified guidance for selected rules: {sorted(missing)}")
        return {
            "selector": "deterministic_colregs_tree",
            "applicable_rules": context["applicable_rules"],
            "classification": context,
            "selected": [
                {"rule": rule, "ship_guidance": list(self.guidance[rule])}
                for rule in context["applicable_rules"]
            ],
        }

    @staticmethod
    def prompt_context(selection: dict[str, Any]) -> str:
        # Supply the same maintained summaries as RAG, without injecting the
        # classifier's additional maneuver advice or encounter-role labels.
        return "\n\n".join(
            f"Rule {item['rule']}\nOPERATIONAL SUMMARY\n"
            + "\n".join(f"- {line}" for line in item["ship_guidance"])
            for item in selection["selected"]
        )
