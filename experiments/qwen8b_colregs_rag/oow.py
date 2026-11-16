"""Vanilla Qwen3-8B OOW using retrieved COLREG provisions."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from agents.schemas.captain import CaptainDecision

from .retrieval import ColregsRetriever, navigation_queries


SYSTEM_PROMPT = """You are the Officer of the Watch of a simulated autonomous surface vessel.
Use the observable navigation briefing and the retrieved verbatim COLREG provisions to choose one action.
Treat retrieved text as legal reference, not as proof that a particular encounter exists. Establish the encounter and vessel roles only from the observations.
When underway, skip is forbidden. When stopped or paused, choose resume if clear or skip only while a specific observed hazard makes resuming unsafe.
For the degrees field, output port or starboard for a normal alter_course, large_port or large_starboard for a substantial 45-degree alteration, and none for every other action. Never output a numeric course alteration.
For the speed field, output exactly one word: slow, normal, or fast for set_speed, and none for all other actions. Never output a numeric speed. Use normal for ordinary configured cruise speed. Use slow only for a specific observed hazard or close-quarters situation.
Return only the required JSON object. Cite relevant Rule numbers concisely in reason. /no_think"""


class RagOOW:
    def __init__(self, client: Any, retriever: ColregsRetriever, model: str = "Qwen/Qwen3-8B"):
        self.client = client
        self.retriever = retriever
        self.model = model

    def decide(
        self,
        narrative: str,
        allowed_actions: list[str],
        *,
        cruise_speed: float,
    ) -> tuple[CaptainDecision, dict]:
        queries = navigation_queries(narrative)
        if hasattr(self.retriever, "retrieve_many"):
            candidates, selected = self.retriever.retrieve_many(queries)
        else:  # Compatibility with lightweight retriever test doubles.
            candidates, selected = self.retriever.retrieve("\n\n---\n\n".join(queries))
        user_prompt = (
            f"BRIDGE BRIEFING\n{narrative.strip()}\n\n"
            "RETRIEVED COLREG PROVISIONS\n"
            f"{self.retriever.prompt_context(selected)}\n\n"
            f"ALLOWED ACTIONS\n{json.dumps(allowed_actions)}"
        )
        schema = deepcopy(CaptainDecision.model_json_schema())
        schema["properties"]["action"]["enum"] = allowed_actions
        schema["properties"]["auxiliary_commands"]["maxItems"] = 0
        schema["properties"]["degrees"] = {
            "type": "string",
            "enum": ["none", "port", "starboard", "large_port", "large_starboard"],
        }
        schema["properties"]["speed"] = {
            "type": "string",
            "enum": ["none", "slow", "normal", "fast"],
        }
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "oow_decision", "strict": True, "schema": schema},
            },
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OOW returned no content")
        payload = json.loads(content)
        intent = payload.get("speed")
        course_intent = payload.get("degrees")
        if payload.get("action") == "alter_course":
            if course_intent not in {"port", "starboard", "large_port", "large_starboard"}:
                raise ValueError("alter_course requires a normal or large port/starboard intent")
            magnitude = 45.0 if course_intent.startswith("large_") else 30.0
            payload["degrees"] = magnitude if course_intent.endswith("starboard") else -magnitude
        else:
            if course_intent != "none":
                raise ValueError("non-alter_course action requires degrees none")
            payload["degrees"] = 0.0
        if payload.get("action") == "set_speed":
            if intent not in {"slow", "normal", "fast"}:
                raise ValueError("set_speed requires slow, normal, or fast")
            payload["speed"] = cruise_speed if intent in {"normal", "fast"} else max(0.3, cruise_speed * 0.7)
        else:
            if intent != "none":
                raise ValueError("non-set_speed action requires speed none")
            payload["speed"] = 0.0
        decision = CaptainDecision.model_validate(payload)
        trace = {
            "model": self.model,
            "query": queries[0] if len(queries) == 1 else None,
            "queries": queries,
            "reranker": self.retriever.reranker.name,
            "candidates": [item.as_dict() for item in candidates],
            "selected": [item.as_dict() for item in selected],
            "decision": decision.model_dump(mode="json"),
            "speed_intent": intent,
            "course_intent": course_intent,
        }
        return decision, trace
