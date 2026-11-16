#!/usr/bin/env python3
"""Verify the paid teacher sees the exact unprivileged local runtime prompt."""

from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from agents.agent_oow import AgentOOW, BASE_SYSTEM_PROMPT


def main() -> None:
    states = [
        {
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [
                {
                    "name": "FAR_CONTACT",
                    "range": 2800.0,
                    "nav": {
                        "x": 0.0,
                        "y": 2800.0,
                        "heading": 180.0,
                        "speed": 6.0,
                    },
                }
            ],
        },
        {
            "moos_time": 20.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 100.0, "heading": 0.0, "speed": 0.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
            "paused": True,
        },
    ]
    common_environment = {
        "MOOS_LLM_OOW_MODE": "base",
        "MOOS_LLM_OOW_INPUT_FORMAT": "narrative",
        "MOOS_LLM_REASONING": "off",
        "MOOS_LLM_ERIKSEN_PROMPT": "off",
        "MOOS_LLM_COLREG_RAG": "off",
        "MOOS_LLM_COLREG_TREE": "off",
    }
    with patch.dict(
        os.environ,
        {
            **common_environment,
            "MOOS_LLM_OPENAI_API": "on",
            "MOOS_LLM_OPENAI_PROMPT_PROFILE": "runtime-base",
        },
        clear=False,
    ):
        oow = AgentOOW(client=SimpleNamespace(), model="prompt-contract-check")
    with patch.dict(
        os.environ,
        {**common_environment, "MOOS_LLM_OPENAI_API": "off"},
        clear=False,
    ):
        runtime_oow = AgentOOW(client=SimpleNamespace(), model="prompt-contract-check")
    if oow.system_prompt != runtime_oow.system_prompt:
        raise SystemExit("teacher and local runtime system prompts differ")
    if oow.system_prompt != BASE_SYSTEM_PROMPT:
        raise SystemExit("teacher system prompt differs from local base runtime")
    observed_field_sets = []
    observed_prompts = []
    for state in states:
        transformed = oow.deterministic_input(state)
        runtime_transformed = runtime_oow.deterministic_input(state)
        if transformed["user_prompt"] != runtime_transformed["user_prompt"]:
            raise SystemExit("teacher and local runtime user prompts differ")
        payload = json.loads(transformed["user_prompt"])
        fields = set(payload)
        if not {"allowed_actions", "briefing"} <= fields:
            raise SystemExit(f"missing runtime user fields: {sorted(fields)}")
        if fields - {"allowed_actions", "briefing", "active_action_review"}:
            raise SystemExit(f"privileged or unexpected user fields: {sorted(fields)}")
        if "state" in payload or "deterministic_colregs_oracle" in payload:
            raise SystemExit("teacher prompt contains privileged state/oracle")
        observed_field_sets.append(sorted(fields))
        observed_prompts.append(transformed["user_prompt"])
    if "FAR_CONTACT" not in observed_prompts[0]:
        raise SystemExit(
            "runtime-base teacher dropped a contact retained at local inference"
        )
    report = {
        "passed": True,
        "system_prompt_sha256": hashlib.sha256(BASE_SYSTEM_PROMPT.encode()).hexdigest(),
        "contains_no_think": "/no_think" in BASE_SYSTEM_PROMPT,
        "observed_user_field_sets": observed_field_sets,
        "forbidden_user_fields": ["state", "deterministic_colregs_oracle"],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
