from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from agents.agent_oow import (
    AgentOOW,
    BASE_SYSTEM_PROMPT,
    SFT_ABLATION_SYSTEM_PROMPT,
    SFT_DIRECTIONAL_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    TEACHER_SYSTEM_PROMPT,
    _canonicalize_unused_decision_fields,
    _openai_compatible_schema,
)


class AgentOOWTests(unittest.TestCase):
    def test_canonicalizes_only_fields_unused_by_selected_action(self):
        self.assertEqual(
            _canonicalize_unused_decision_fields({
                "action": "maintain_course", "degrees": 12, "speed": 6.17,
            }),
            {"action": "maintain_course", "degrees": 0.0, "speed": 0.0},
        )
        self.assertEqual(
            _canonicalize_unused_decision_fields({
                "action": "alter_course", "degrees": 30, "speed": 6.17,
            }),
            {"action": "alter_course", "degrees": 30, "speed": 0.0},
        )
        self.assertEqual(
            _canonicalize_unused_decision_fields({
                "action": "set_speed", "degrees": 12, "speed": 4.0,
            }),
            {"action": "set_speed", "degrees": 0.0, "speed": 4.0},
        )

    def test_directional_profile_uses_plain_narrative_and_categorical_contract(self):
        with patch.dict(
            "os.environ",
            {
                "MOOS_LLM_OOW_MODE": "finetuned",
                "MOOS_LLM_FINETUNED_PROMPT_PROFILE": "narrative-directional",
            },
        ):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input(
            {
                "moos_time": 10.0,
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
                "mission_objective": {"x": 0.0, "y": 1000.0},
                "contacts": [],
            }
        )
        self.assertEqual(oow.system_prompt, SFT_DIRECTIONAL_SYSTEM_PROMPT)
        self.assertTrue(transformed["user_prompt"].startswith("CURRENT BRIDGE STATE"))
        self.assertFalse(transformed["user_prompt"].lstrip().startswith("{"))

    def test_directional_profile_resolves_structured_output_before_execution(self):
        captured = {}

        class Completions:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({
                        "action": "alter_course",
                        "turn_direction": "starboard",
                        "turn_degrees": 30.0,
                        "speed": 0.0,
                        "reason": "Alter to starboard under Rules 8 and 14.",
                        "auxiliary_commands": [],
                    }))
                )])

        with patch.dict(
            "os.environ",
            {
                "MOOS_LLM_OOW_MODE": "finetuned",
                "MOOS_LLM_FINETUNED_PROMPT_PROFILE": "narrative-directional",
            },
        ):
            oow = AgentOOW(
                client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
                model="test-model",
            )
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        })
        decision = oow.decide(transformed)
        schema = captured["response_format"]["json_schema"]["schema"]
        self.assertNotIn("degrees", schema["properties"])
        self.assertIn("turn_direction", schema["properties"])
        self.assertEqual(decision.degrees, 30.0)
        self.assertEqual(oow.last_model_decision["turn_direction"], "starboard")

    def test_openai_schema_translates_discriminated_union(self):
        schema = {
            "items": {
                "discriminator": {"propertyName": "type"},
                "oneOf": [{"type": "string"}, {"type": "number"}],
            }
        }

        translated = _openai_compatible_schema(schema)

        self.assertNotIn("discriminator", translated["items"])
        self.assertNotIn("oneOf", translated["items"])
        self.assertEqual(
            translated["items"]["anyOf"],
            [{"type": "string"}, {"type": "number"}],
        )

    def setUp(self):
        self.client = SimpleNamespace()
        self.oow = AgentOOW(client=self.client, model="test-model")

    def test_runtime_transform_uses_unrestricted_actions_and_training_prompt(self):
        transformed = self.oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 1.0, "y": 2.0, "heading": 90.0, "speed": 3.0},
            "mission_objective": {"x": 100.0, "y": 2.0},
            "contacts": [],
            "allowed_actions": ["maintain_course"],
            "candidate_actions": [{"action": "maintain_course"}],
            "colregs_context": {
                "own_vessel_type": "power_driven",
                "applicable_rules": [8, 15, 16],
                "rule_guidance": {
                    "15": "Avoid crossing ahead and pass astern when practicable."
                },
                "assumptions": {},
            },
        })

        self.assertIn("CURRENT BRIDGE STATE", transformed["narrative"])
        self.assertNotIn("candidate_actions", transformed["state"])
        self.assertNotIn("AUTHORITATIVE COLREG ASSESSMENT", transformed["user_prompt"])
        self.assertNotIn("Avoid crossing ahead", transformed["user_prompt"])
        self.assertNotIn("colregs_context", transformed["state"])
        prompt = json.loads(transformed["user_prompt"])
        self.assertEqual(prompt["briefing"], transformed["narrative"])
        self.assertEqual(prompt["allowed_actions"], transformed["constraints"]["allowed_actions"])
        self.assertNotIn("state", prompt)
        self.assertNotIn("PERMITTED ACTIONS", transformed["user_prompt"])
        constraints = transformed["constraints"]
        self.assertEqual(constraints["allowed_actions"], [
            "maintain_course", "alter_course", "set_speed", "stop",
            "resume", "skip",
        ])
        self.assertIn("/no_think", SYSTEM_PROMPT)
        self.assertIn("Decision questions", self.oow.system_prompt)
        self.assertIn("30, 45, and 60 degree alterations", self.oow.system_prompt)
        self.assertIn("not limited to 15 degrees", self.oow.system_prompt)
        self.assertIn("must concisely cite every COLREG Rule", self.oow.system_prompt)
        self.assertIn(
            "Do not omit a directly applicable rule",
            " ".join(self.oow.system_prompt.split()),
        )
        self.assertIn('for example, "Rules 13 and 16"', self.oow.system_prompt)
        self.assertEqual(self.oow.system_prompt, BASE_SYSTEM_PROMPT)

    def test_vhf_benchmark_adds_structured_and_narrative_evidence(self):
        with patch.dict("os.environ", {"MOOS_LLM_VHF_BENCHMARK": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input(
            {
                "moos_time": 10.0,
                "vehicle": "ALPHA",
                "nav": {"x": 0.0, "y": -1000.0, "heading": 0.0, "speed": 6.173},
                "mission_objective": {"x": 0.0, "y": 1000.0},
                "contacts": [],
            },
            vhf_advisories=[{
                "received_cycle": 9,
                "age_cycles": 1,
                "context_status": "recent_already_presented",
                "advisory": {
                    "message_id": "00000000-0000-0000-0000-000000000001",
                    "speaker": "Harbour VTS",
                    "addressee": "ALPHA",
                    "addressed_to_own_ship": True,
                    "source_type": "vts",
                    "identity_status": "verified",
                    "intent": "instruction",
                    "message": "Proceed into the fairway.",
                    "requested_action": "proceed",
                    "requested_turn_direction": "none",
                    "reported_facts": [],
                    "confidence": 0.99,
                    "clarification_required": False,
                }
            }],
        )
        prompt = json.loads(transformed["user_prompt"])
        self.assertEqual(prompt["vhf_communications"][0]["requested_action"], "proceed")
        self.assertTrue(
            prompt["vhf_communications"][0]["addressed_to_own_ship"]
        )
        self.assertEqual(
            prompt["vhf_communications"][0]["requested_turn_direction"], "none"
        )
        self.assertIn("RADIO MESSAGES", transformed["narrative"])
        self.assertIn("recent_already_presented", transformed["narrative"])
        self.assertIn("Do not acknowledge them again", transformed["narrative"])
        self.assertIn("live tracked traffic", oow.system_prompt)
        self.assertTrue(oow.system_prompt.rstrip().endswith("/no_think"))
        self.assertEqual(
            oow.decision_trigger(transformed)["reason"],
            "non_teacher_mode",
        )

    def test_persistent_vhf_instruction_has_separate_active_section(self):
        with patch.dict("os.environ", {"MOOS_LLM_VHF_BENCHMARK": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input(
            {
                "moos_time": 10.0,
                "vehicle": "ALPHA",
                "nav": {"x": 0.0, "y": -1000.0, "heading": 0.0, "speed": 0.0},
                "mission_objective": {"x": 0.0, "y": 1000.0},
                "contacts": [],
            },
            vhf_advisories=[{
                "received_cycle": 2,
                "age_cycles": 8,
                "context_status": "active_persistent_instruction",
                "advisory": {
                    "message_id": "00000000-0000-0000-0000-000000000003",
                    "speaker": "Harbour VTS",
                    "addressee": "ALPHA",
                    "addressed_to_own_ship": True,
                    "source_type": "vts",
                    "identity_status": "verified",
                    "intent": "instruction",
                    "message": "Remain in the holding area until further notice.",
                    "requested_action": "stop",
                    "requested_turn_direction": "none",
                    "directive_lifetime": "until_cancelled",
                    "directive_end_condition": None,
                    "reported_facts": [],
                    "confidence": 0.99,
                    "clarification_required": False,
                },
            }],
        )
        self.assertIn("ACTIVE RADIO INSTRUCTIONS", transformed["narrative"])
        self.assertNotIn("\n\nRADIO MESSAGES\n\n", transformed["narrative"])
        self.assertIn("until_cancelled", transformed["narrative"])
        self.assertIn("clear_vhf_instruction", transformed["narrative"])

    def test_model_input_excludes_simulator_navigation_judgments(self):
        transformed = self.oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0, "y": 0, "heading": 30, "speed": 5},
            "mission_objective": {"x": 0, "y": 1000},
            "next_waypoint": {"x": 0, "y": 1000},
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 30},
            "temporary_waypoint": {"x": 50000, "y": 86603},
            "decision_obligations": ["Choose rejoin_route"],
            "contacts": [{
                "name": "TS1",
                "nav": {"x": 100, "y": 100, "heading": 270, "speed": 5},
                "range": 141, "relative_bearing": 15,
                "projection_status": "valid", "cpa_distance": 20, "tcpa_s": 30,
                "required_cpa_m": 926, "risk": "critical",
                "encounter_type": "crossing", "own_role": "give_way",
                "maneuver_guidance": "Choose rejoin_route",
            }],
            "colregs_context": {
                "applicable_rules": [15, 16],
                "rule_guidance": {"16": "Choose rejoin_route"},
                "encounters": [{"contact": "TS1", "risk": "critical",
                                "maneuver_guidance": "Choose rejoin_route"}],
                "assumptions": {"visibility_condition": "clear", "risk": "low"},
            },
        })
        forbidden = {
            "safe_to_rejoin", "future_clearance_breach", "unsafe_contacts", "risk",
            "maneuver_guidance", "decision_obligations", "encounter_type", "own_role",
            "active_encounter_rules", "applicable_rules", "rule_guidance",
            "required_cpa_m", "rejoin_route_assessment", "colregs_context",
        }

        def check_keys(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value), value)
                for child in value.values():
                    check_keys(child)
            elif isinstance(value, list):
                for child in value:
                    check_keys(child)

        check_keys(transformed)
        self.assertNotIn("saved_route_geometry", transformed["state"])
        self.assertEqual(transformed["state"]["contacts"][0]["cpa_distance_m"], 20)
        prompt = json.loads(transformed["user_prompt"])
        check_keys(prompt)
        self.assertEqual(prompt["briefing"], transformed["narrative"])
        self.assertNotIn("state", prompt)
        self.assertNotIn("Choose rejoin_route", transformed["user_prompt"])
        self.assertNotIn("BREACH", transformed["user_prompt"])

    def test_reasoning_profile_replaces_no_think_control_token(self):
        with patch.dict("os.environ", {"MOOS_LLM_REASONING": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        self.assertIn("/think", oow.system_prompt)
        self.assertNotIn("/no_think", oow.system_prompt)
        self.assertIn("every COLREG Rule", oow.system_prompt)

    def test_reasoning_uses_verified_deliberation_before_json_finalization(self):
        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(
                            content=(
                                "<think>The vessel has no tracked contacts, so the "
                                "safe action is to retain the current mission plan.</think>"
                                "I will maintain course."
                            )
                        ))],
                        usage=None,
                    )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                        "action": "maintain_course",
                        "degrees": 0.0,
                        "speed": 0.0,
                        "reason": "No tracked contact requires avoiding action.",
                        "auxiliary_commands": [],
                    })))],
                    usage=None,
                )

        with patch.dict("os.environ", {"MOOS_LLM_REASONING": "on"}):
            oow = AgentOOW(
                client=SimpleNamespace(
                    chat=SimpleNamespace(completions=Completions())
                ),
                model="test-model",
            )
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        })
        decision = oow.decide(transformed)

        self.assertEqual(decision.action, "maintain_course")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("response_format", calls[0])
        self.assertIn("response_format", calls[1])
        self.assertIn("/think", calls[0]["messages"][0]["content"])
        self.assertIn("/no_think", calls[1]["messages"][0]["content"])
        self.assertTrue(oow.last_response_metadata["reasoning_verified"])
        self.assertGreater(oow.last_response_metadata["reasoning_characters"], 20)

    def test_reasoning_accepts_token_limited_but_substantive_think_trace(self):
        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return SimpleNamespace(
                        choices=[SimpleNamespace(
                            message=SimpleNamespace(content=(
                                "<think>This is a substantive navigation analysis "
                                "that reached the output-token limit before closure."
                            )),
                            finish_reason="length",
                        )],
                        usage=None,
                    )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                        "action": "maintain_course",
                        "degrees": 0.0,
                        "speed": 0.0,
                        "reason": "No tracked contact requires avoiding action.",
                        "auxiliary_commands": [],
                    })))],
                    usage=None,
                )

        with patch.dict("os.environ", {"MOOS_LLM_REASONING": "on"}):
            oow = AgentOOW(
                client=SimpleNamespace(
                    chat=SimpleNamespace(completions=Completions())
                ),
                model="test-model",
            )
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        })
        decision = oow.decide(transformed)

        self.assertEqual(decision.action, "maintain_course")
        self.assertTrue(oow.last_response_metadata["reasoning_verified"])
        self.assertFalse(oow.last_response_metadata["reasoning_complete"])
        self.assertEqual(oow.last_response_metadata["reasoning_finish_reason"], "length")
        self.assertTrue(
            calls[1]["messages"][2]["content"].rstrip().endswith("</think>")
        )

    def test_teacher_prompt_uses_runtime_trigger_and_500m_training_gate(self):
        with patch.dict("os.environ", {
            "MOOS_LLM_OPENAI_API": "on",
            "MOOS_LLM_OPENAI_PROMPT_PROFILE": "teacher",
            "MOOS_LLM_TEACHER_TRIGGER_RANGE_M": "2500",
        }):
            oow = AgentOOW(client=SimpleNamespace(), model="gpt-6-luna")
        normalized = " ".join(oow.system_prompt.split())
        self.assertIn("2,500 metre monitoring range", normalized)
        self.assertNotIn("1,500 metre monitoring range", normalized)
        self.assertIn("never falls below 500 metres", normalized)
        self.assertIn("hard lower bound throughout the mission", normalized)
        self.assertIn("margin above that bound", normalized)
        self.assertIn("Privileged candidate lookahead", oow.system_prompt)
        self.assertIn("never executes or overrides a command", normalized)

    def test_raw_state_profile_omits_narrative_from_model_payload(self):
        with patch.dict("os.environ", {"MOOS_LLM_OOW_INPUT_FORMAT": "raw-state"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        })
        payload = json.loads(transformed["user_prompt"])
        self.assertIn("state", payload)
        self.assertIn("safety_limits", payload)
        self.assertNotIn("briefing", payload)
        self.assertNotIn("narrative", payload)
        self.assertEqual(payload["state"]["own_ship"]["speed"], 3.0)
        self.assertEqual(payload["safety_limits"]["intervention_cpa_m"], 500.0)
        self.assertIn("processed numeric `state`", oow.system_prompt)
        normalized_prompt = " ".join(oow.system_prompt.split())
        self.assertIn("metres per second", normalized_prompt)
        self.assertNotIn("briefing uses metres", normalized_prompt)

    def test_narrative_and_raw_state_inputs_are_strictly_separated(self):
        bridge_state = {
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "target_speed": 6.17,
            "recovery_speed": 6.17,
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [{
                "name": "TS1",
                "range": 500.0,
                "relative_bearing": 20.0,
                "nav": {"x": 171.0, "y": 470.0, "heading": 180.0, "speed": 3.0},
                "projection_status": "valid",
                "cpa_distance": 50.0,
                "tcpa_s": 80.0,
            }],
        }
        with patch.dict("os.environ", {"MOOS_LLM_OOW_INPUT_FORMAT": "narrative"}):
            narrative_oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        with patch.dict("os.environ", {"MOOS_LLM_OOW_INPUT_FORMAT": "raw-state"}):
            raw_oow = AgentOOW(client=SimpleNamespace(), model="test-model")

        narrative_payload = json.loads(
            narrative_oow.deterministic_input(bridge_state)["user_prompt"]
        )
        raw_payload = json.loads(raw_oow.deterministic_input(bridge_state)["user_prompt"])

        self.assertEqual(
            narrative_payload["allowed_actions"], raw_payload["allowed_actions"]
        )
        self.assertEqual(set(narrative_payload), {"allowed_actions", "briefing"})
        self.assertEqual(
            set(raw_payload), {"allowed_actions", "state", "safety_limits"}
        )
        self.assertNotIn("state", narrative_payload)
        self.assertNotIn("safety_limits", narrative_payload)
        self.assertNotIn("briefing", raw_payload)
        self.assertNotIn("narrative", raw_payload)

    def test_narrative_and_raw_state_preserve_history_without_cross_format_leakage(self):
        first = {
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        }
        second = {
            **first,
            "moos_time": 20.0,
            "nav": {"x": 0.0, "y": 30.0, "heading": 0.0, "speed": 3.0},
        }
        with patch.dict("os.environ", {"MOOS_LLM_OOW_INPUT_FORMAT": "narrative"}):
            narrative_oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        with patch.dict("os.environ", {"MOOS_LLM_OOW_INPUT_FORMAT": "raw-state"}):
            raw_oow = AgentOOW(client=SimpleNamespace(), model="test-model")

        narrative_oow.deterministic_input(first)
        raw_oow.deterministic_input(first)
        narrative_payload = json.loads(
            narrative_oow.deterministic_input(second)["user_prompt"]
        )
        raw_payload = json.loads(raw_oow.deterministic_input(second)["user_prompt"])

        self.assertIn("NAVIGATION PROGRESS", narrative_payload["briefing"])
        self.assertEqual(len(raw_payload["previous_states"]), 1)
        self.assertEqual(raw_payload["previous_states"][0]["observed_at_s"], 10.0)
        self.assertNotIn("state", narrative_payload)
        self.assertNotIn("briefing", raw_payload)

    def test_decide_sends_selected_payload_unchanged_to_local_model(self):
        bridge_state = {
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        }

        for input_format, expected_key, forbidden_key in (
            ("narrative", "briefing", "state"),
            ("raw-state", "state", "briefing"),
        ):
            with self.subTest(input_format=input_format):
                captured = {}

                class Completions:
                    @staticmethod
                    def create(**kwargs):
                        captured.update(kwargs)
                        return SimpleNamespace(choices=[SimpleNamespace(
                            message=SimpleNamespace(content=(
                                '{"action":"maintain_course","degrees":0,"speed":0,'
                                '"reason":"Clear; Rules 5, 6, and 7.",'
                                '"auxiliary_commands":[]}'
                            ))
                        )])

                with patch.dict(
                    "os.environ", {"MOOS_LLM_OOW_INPUT_FORMAT": input_format}
                ):
                    oow = AgentOOW(
                        client=SimpleNamespace(
                            chat=SimpleNamespace(completions=Completions())
                        ),
                        model="test-model",
                    )
                transformed = oow.deterministic_input(bridge_state)
                oow.decide(transformed)

                sent = captured["messages"][1]["content"]
                self.assertEqual(sent, transformed["user_prompt"])
                payload = json.loads(sent)
                self.assertIn(expected_key, payload)
                self.assertNotIn(forbidden_key, payload)

    def test_eriksen_profile_exposes_classifier_to_local_base(self):
        with patch.dict("os.environ", {"MOOS_LLM_ERIKSEN_PROMPT": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "contacts": [{
                "name": "TS1",
                "range": 1400.0,
                "nav": {"x": 0.0, "y": 1400.0, "heading": 180.0, "speed": 6.0},
            }],
        })
        payload = json.loads(transformed["user_prompt"])
        self.assertNotIn("state", payload)
        self.assertNotIn("deterministic_colregs_oracle", payload)
        self.assertIn("COLREG ASSESSMENT", payload["briefing"])
        self.assertIn(
            "Own ship and TS1 are meeting head-on. Both vessels should alter course to starboard",
            payload["briefing"],
        )
        self.assertNotIn("hysteresis", payload["briefing"])
        self.assertNotIn("Classifier update", payload["briefing"])
        for rule in (2, 5, 6, 7, 8, 14):
            self.assertIn(f"Rule {rule}:", payload["briefing"])
        self.assertIn("persistent state HO (head_on)", payload["briefing"])
        self.assertIn("current geometrical state HO", payload["briefing"])
        self.assertIn("own-ship role both_give_way", payload["briefing"])
        self.assertIn("persistent Eriksen", oow.system_prompt)
        self.assertIn("complete applicable Rule set", oow.system_prompt)
        self.assertIn("Rules 2, 5, 6, and 7", oow.system_prompt)
        self.assertIn("does not prescribe a maneuver", oow.system_prompt)
        self.assertNotIn("teacher demonstrations", oow.system_prompt)

    def test_finetuned_profile_uses_compact_training_contract(self):
        with patch.dict("os.environ", {"MOOS_LLM_OOW_MODE": "finetuned"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        self.assertEqual(oow.system_prompt, SYSTEM_PROMPT)
        self.assertIn("Officer of the Watch", oow.system_prompt)
        self.assertIn("Return only one JSON object", oow.system_prompt)
        self.assertNotIn("Navigation context", oow.system_prompt)
        self.assertNotIn("Decision questions", oow.system_prompt)
        self.assertNotIn("Helm behavior", oow.system_prompt)

    def test_narrative_only_ablation_uses_plain_narrative_message(self):
        with patch.dict(
            "os.environ",
            {
                "MOOS_LLM_OOW_MODE": "finetuned",
                "MOOS_LLM_FINETUNED_PROMPT_PROFILE": "narrative-only",
            },
        ):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 3.0},
            "mission_objective": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        })
        self.assertEqual(oow.system_prompt, SFT_ABLATION_SYSTEM_PROMPT)
        self.assertEqual(transformed["user_prompt"], transformed["narrative"])
        self.assertTrue(transformed["user_prompt"].startswith("CURRENT BRIDGE STATE"))
        self.assertNotIn('"briefing"', transformed["user_prompt"])
        self.assertNotIn('"allowed_actions"', transformed["user_prompt"])

    def test_openai_teacher_uses_detailed_current_prompt(self):
        with patch.dict("os.environ", {"MOOS_LLM_OPENAI_API": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        self.assertTrue(oow.system_prompt.startswith("# Role and objective"))
        self.assertIn("Teacher-data clearance contract", oow.system_prompt)
        normalized_prompt = " ".join(oow.system_prompt.split())
        self.assertIn("teacher demonstrations", normalized_prompt)
        self.assertIn("single most defensible tactical navigation decision", normalized_prompt)
        self.assertIn("Is VHF communication necessary or materially useful", normalized_prompt)
        self.assertIn("Never delay required avoiding action", normalized_prompt)
        self.assertIn("is the contact taking the action required of it by the COLREGs", normalized_prompt)
        self.assertIn("do not wait until that distance has already been lost", normalized_prompt)
        self.assertIn("Never choose set_speed with the existing target", normalized_prompt.replace("`", ""))
        self.assertIn("Rule 17", oow.system_prompt)
        self.assertIn("can replace the active plan", oow.system_prompt)
        self.assertIn("temporary waypoint 500 metres", oow.system_prompt)
        self.assertIn("Reaching the supplied mission objective", oow.system_prompt)
        self.assertIn("Do not infer non-compliance from one new observation", normalized_prompt)
        self.assertIn("relative to own ship's current measured heading", oow.system_prompt)
        self.assertIn("least delay, distance, and heading deviation", oow.system_prompt)
        self.assertIn("Stay below 900 characters", oow.system_prompt)
        self.assertIn("complete English sentences", oow.system_prompt)
        self.assertNotIn("do not issue another course or speed maneuver", oow.system_prompt)
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "contacts": [{
                "name": "TS1",
                "range": 1400.0,
                "nav": {"x": 0.0, "y": 1400.0, "heading": 180.0, "speed": 6.0},
            }],
        })
        payload = json.loads(transformed["user_prompt"])
        self.assertEqual(
            payload["deterministic_colregs_oracle"]["contacts"][0]["state"],
            "HO",
        )
        self.assertIn(
            "starboard",
            payload["deterministic_colregs_oracle"]["contacts"][0]
            ["applicable_rule_summaries"]["14"],
        )
        self.assertIn("teacher_candidate_lookahead", payload)
        lookahead = payload["teacher_candidate_lookahead"]
        self.assertEqual(
            lookahead["assumptions"]["temporary_maneuver_distance_m"], 500.0
        )
        self.assertEqual(lookahead["thresholds"]["hard_minimum_separation_m"], 500.0)

    def test_openai_runtime_base_profile_is_exact_and_unprivileged(self):
        with patch.dict(
            "os.environ",
            {
                "MOOS_LLM_OPENAI_API": "on",
                "MOOS_LLM_OPENAI_PROMPT_PROFILE": "runtime-base",
            },
        ):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        self.assertEqual(oow.system_prompt, BASE_SYSTEM_PROMPT)
        self.assertIn("/no_think", oow.system_prompt)
        transformed = oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "contacts": [{
                "name": "TS1",
                "range": 1400.0,
                "nav": {"x": 0.0, "y": 1400.0, "heading": 180.0, "speed": 6.0},
            }],
        })
        payload = json.loads(transformed["user_prompt"])
        self.assertEqual(set(payload), {"allowed_actions", "briefing"})
        self.assertNotIn("state", payload)
        self.assertNotIn("deterministic_colregs_oracle", payload)
        self.assertNotIn("COLREG ASSESSMENT", payload["briefing"])

    def test_openai_runtime_base_profile_preserves_far_contacts(self):
        state = {
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "mission_objective": {"x": 0.0, "y": 5000.0},
            "contacts": [{
                "name": "FAR_CONTACT",
                "range": 2800.0,
                "nav": {"x": 0.0, "y": 2800.0, "heading": 180.0, "speed": 6.0},
            }],
        }
        with patch.dict("os.environ", {
            "MOOS_LLM_OPENAI_API": "on",
            "MOOS_LLM_OPENAI_PROMPT_PROFILE": "runtime-base",
            "MOOS_LLM_OOW_MODE": "base",
        }):
            teacher = AgentOOW(client=SimpleNamespace(), model="test-model")
        with patch.dict("os.environ", {
            "MOOS_LLM_OPENAI_API": "off",
            "MOOS_LLM_OOW_MODE": "base",
        }):
            runtime = AgentOOW(client=SimpleNamespace(), model="test-model")

        teacher_input = teacher.deterministic_input(state)
        runtime_input = runtime.deterministic_input(state)
        self.assertEqual(teacher.system_prompt, runtime.system_prompt)
        self.assertEqual(teacher_input["user_prompt"], runtime_input["user_prompt"])
        self.assertIn("FAR_CONTACT", teacher_input["user_prompt"])

    def test_openai_teacher_does_not_change_finetuned_prompt(self):
        with patch.dict(
            "os.environ",
            {"MOOS_LLM_OPENAI_API": "on", "MOOS_LLM_OOW_MODE": "finetuned"},
        ):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        self.assertIn("Officer of the Watch", oow.system_prompt)
        self.assertNotIn("teacher demonstrations", oow.system_prompt)

    def test_teacher_skips_distant_safe_contact(self):
        with patch.dict("os.environ", {"MOOS_LLM_OPENAI_API": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input({
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "contacts": [{
                "name": "TS1",
                "range": 2000.0,
                "nav": {"x": 0.0, "y": -2000.0, "heading": 180.0, "speed": 6.0},
            }],
        })
        self.assertEqual(oow.decision_trigger(transformed)["call_model"], False)
        self.assertEqual(transformed["state"]["contacts"], [])
        self.assertEqual(oow.last_paper_colregs["contacts"], [])

    def test_teacher_calls_only_for_contact_within_monitoring_radius(self):
        with patch.dict("os.environ", {"MOOS_LLM_OPENAI_API": "on"}):
            nearby = AgentOOW(client=SimpleNamespace(), model="test-model")
            active = AgentOOW(client=SimpleNamespace(), model="test-model")
        near_state = {
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "contacts": [{
                "name": "TS1",
                "range": 1400.0,
                "nav": {"x": 0.0, "y": -1400.0, "heading": 180.0, "speed": 6.0},
            }],
        }
        self.assertTrue(nearby.decision_trigger(nearby.deterministic_input(near_state))["call_model"])
        active_state = {
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "contacts": [{
                "name": "TS1",
                "range": 2400.0,
                "nav": {"x": 0.0, "y": 2400.0, "heading": 180.0, "speed": 6.0},
            }],
        }
        trigger = active.decision_trigger(active.deterministic_input(active_state))
        self.assertFalse(trigger["call_model"])
        self.assertEqual(trigger["reason"], "no_relevant_contact")

    def test_runtime_encounter_gate_is_opt_in_and_does_not_change_prompt(self):
        state = {
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "mission_objective": {"x": 0.0, "y": 5000.0},
            "contacts": [],
        }
        with patch.dict("os.environ", {
            "MOOS_LLM_OPENAI_API": "off",
            "MOOS_LLM_OOW_MODE": "base",
            "MOOS_LLM_ENCOUNTER_ONLY": "off",
        }):
            always = AgentOOW(client=SimpleNamespace(), model="test-model")
        with patch.dict("os.environ", {
            "MOOS_LLM_OPENAI_API": "off",
            "MOOS_LLM_OOW_MODE": "base",
            "MOOS_LLM_ENCOUNTER_ONLY": "on",
            "MOOS_LLM_TEACHER_TRIGGER_RANGE_M": "6000",
        }):
            gated = AgentOOW(client=SimpleNamespace(), model="test-model")

        always_input = always.deterministic_input(state)
        gated_input = gated.deterministic_input(state)
        self.assertTrue(always.decision_trigger(always_input)["call_model"])
        self.assertFalse(gated.decision_trigger(gated_input)["call_model"])
        self.assertEqual(always.system_prompt, gated.system_prompt)
        self.assertEqual(always_input["user_prompt"], gated_input["user_prompt"])

    def test_teacher_prompt_requests_active_course_review(self):
        with patch.dict("os.environ", {"MOOS_LLM_OPENAI_API": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input({
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 20.0, "speed": 3.0},
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 20.0},
            "temporary_waypoint": {"x": 50.0, "y": 100.0},
            "contacts": [],
        })
        payload = json.loads(transformed["user_prompt"])

        self.assertEqual(payload["active_action_review"]["active_action"], "alter_course")
        self.assertIn("still necessary", payload["active_action_review"]["instruction"])
        self.assertIn("compounds the diversion", payload["active_action_review"]["instruction"])
        self.assertIn("resulting heading", payload["active_action_review"]["instruction"])
        self.assertNotIn("ACTION REVIEW REQUEST", transformed["narrative"])

    def test_teacher_prompt_requests_reduced_speed_review(self):
        with patch.dict("os.environ", {"MOOS_LLM_OPENAI_API": "on"}):
            oow = AgentOOW(client=SimpleNamespace(), model="test-model")
        transformed = oow.deterministic_input({
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 20.0, "speed": 3.0},
            "target_speed": 3.0,
            "recovery_speed": 6.0,
            "contacts": [],
        })
        payload = json.loads(transformed["user_prompt"])

        review = payload["active_action_review"]
        self.assertEqual(review["active_action"], "set_speed")
        self.assertEqual(review["current_target_speed"], 3.0)
        self.assertEqual(review["normal_speed"], 6.0)
        self.assertIn("choose set_speed at normal speed now", review["instruction"])

    def test_temporal_history_is_rendered(self):
        base = {
            "vehicle": "LLM_SHIP",
            "mission_objective": {"x": 100.0, "y": 0.0},
            "contacts": [],
            "allowed_actions": ["maintain_course"],
            "candidate_actions": [{"action": "maintain_course", "degrees": 0.0, "distance_m": 0.0, "speed": 0.0}],
            "colregs_context": {"assumptions": {}},
        }
        first = {**base, "moos_time": 10.0, "nav": {"x": 0.0, "y": 0.0, "heading": 90.0, "speed": 3.0}}
        second = {**base, "moos_time": 20.0, "nav": {"x": 20.0, "y": 0.0, "heading": 90.0, "speed": 3.0}}
        self.oow.deterministic_input(first)
        transformed = self.oow.deterministic_input(second)
        self.assertIn("NAVIGATION PROGRESS", transformed["narrative"])

    def test_stopped_vessel_is_limited_to_resume_or_skip(self):
        transformed = self.oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0},
            "contacts": [],
        })
        self.assertEqual(
            transformed["constraints"],
            {"allowed_actions": ["resume", "skip"]},
        )
        self.assertNotIn("candidate_actions", transformed["state"])
        self.assertIn("STOPPED VESSEL STATE", transformed["narrative"])
        review = json.loads(transformed["user_prompt"])["active_action_review"]
        self.assertIn("OWN SHIP IS STOPPED OR PAUSED", review["instruction"])
        self.assertIn("choose skip", review["instruction"])
        self.assertNotIn("OWN SHIP IS STOPPED OR PAUSED", transformed["narrative"])

    def test_active_maneuver_requests_rejoin_review(self):
        transformed = self.oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 20.0, "speed": 3.0},
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 20.0},
            "temporary_waypoint": {"x": 50.0, "y": 100.0},
            "contacts": [],
        })

        self.assertEqual(
            transformed["constraints"]["allowed_actions"],
            ["maintain_course", "alter_course", "rejoin_route", "set_speed", "stop",
             "resume", "skip"],
        )
        self.assertIn("Commanded alteration: 20 degrees to starboard", transformed["narrative"])
        review = json.loads(transformed["user_prompt"])["active_action_review"]
        self.assertIn("choose rejoin_route now", review["instruction"])
        self.assertNotIn("choose rejoin_route now", transformed["narrative"])

    def test_course_and_reduced_speed_are_reviewed_independently(self):
        transformed = self.oow.deterministic_input({
            "moos_time": 10.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0.0, "y": 0.0, "heading": 20.0, "speed": 0.5},
            "target_speed": 0.5,
            "recovery_speed": 6.17,
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 30.0},
            "temporary_waypoint": {"x": 50.0, "y": 100.0},
            "contacts": [],
        })

        review = json.loads(transformed["user_prompt"])["active_action_review"]
        self.assertEqual(review["active_action"], "alter_course_and_set_speed")
        self.assertIn("choosing set_speed does not cancel", review["instruction"])
        self.assertIn("choose set_speed at normal speed now", review["instruction"])

    def test_decide_constrains_structured_output_to_current_actions(self):
        captured = {}

        class Completions:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=(
                        '{"action":"maintain_course","degrees":0,"speed":0,'
                        '"reason":"Clear.","auxiliary_commands":[]}'
                    )))]
                )

        self.oow.client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        transformed = {
            "user_prompt": "state",
            "constraints": {"allowed_actions": ["maintain_course", "resume"]},
        }

        self.oow.decide(transformed)

        action_schema = captured["response_format"]["json_schema"]["schema"]
        self.assertEqual(
            action_schema["properties"]["action"]["enum"],
            ["maintain_course", "resume"],
        )
        self.assertNotIn("skip", action_schema["properties"]["action"]["enum"])

    def test_decide_accepts_measured_speed_in_unused_field(self):
        class Completions:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=(
                        '{"action":"maintain_course","degrees":0,"speed":6.17,'
                        '"reason":"Clear.","auxiliary_commands":[]}'
                    )))]
                )

        self.oow.client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        decision = self.oow.decide({
            "user_prompt": "state",
            "constraints": {"allowed_actions": ["maintain_course"]},
        })

        self.assertEqual(decision.action, "maintain_course")
        self.assertEqual(decision.speed, 0.0)

    def test_rag_set_speed_resolves_qualitative_intent(self):
        class Retriever:
            reranker = SimpleNamespace(name="test")

            def retrieve(self, query):
                return [], []

            @staticmethod
            def prompt_context(items):
                return ""

        class Completions:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=(
                        '{"action":"set_speed","degrees":"none","speed":"slow",'
                        '"reason":"Safe speed.","auxiliary_commands":[]}'
                    )
                ))])

        self.oow.colregs_retriever = Retriever()
        self.oow.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        decision = self.oow.decide({
            "user_prompt": "state",
            "narrative": "underway",
            "state": {"own_ship": {"recovery_speed": 8.0}},
            "constraints": {"allowed_actions": ["set_speed"]},
        })
        self.assertAlmostEqual(decision.speed, 5.6)
        self.assertEqual(
            self.oow.last_colregs_retrieval["resolved_speed_intent"], "slow"
        )
        self.assertIn("Decision questions", self.oow.system_prompt)

    def test_invalid_rag_speed_combination_retries_then_fails_cleanly(self):
        class Retriever:
            reranker = SimpleNamespace(name="test")

            def retrieve(self, query):
                return [], []

            @staticmethod
            def prompt_context(items):
                return ""

        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=(
                        '{"action":"maintain_course","degrees":"none","speed":"normal",'
                        '"reason":"Clear.","auxiliary_commands":[]}'
                    )
                ))])

        self.oow.colregs_retriever = Retriever()
        self.oow.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        with self.assertRaisesRegex(RuntimeError, "invalid JSON twice"):
            self.oow.decide({
                "user_prompt": "state",
                "narrative": "underway",
                "state": {"own_ship": {"recovery_speed": 8.0}},
                "constraints": {"allowed_actions": ["maintain_course"]},
            })
        self.assertEqual(len(calls), 2)
        self.assertIn("response was invalid", calls[1]["messages"][-1]["content"])

    def test_rag_course_intent_is_allowed_outside_intervention_envelope(self):
        class Retriever:
            reranker = SimpleNamespace(name="test")

            def retrieve(self, query):
                return [], []

            @staticmethod
            def prompt_context(items):
                return ""

        class Completions:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=(
                        '{"action":"alter_course","degrees":"port","speed":"none",'
                        '"reason":"Turn.","auxiliary_commands":[]}'
                    )
                ))])

        self.oow.colregs_retriever = Retriever()
        self.oow.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        decision = self.oow.decide({
            "user_prompt": "state",
            "narrative": "underway",
            "state": {
                "own_ship": {"recovery_speed": 8.0},
                "contacts": [{
                    "projection_status": "valid",
                    "cpa_distance_m": 108.0,
                    "tcpa_s": 411.3,
                }],
            },
            "constraints": {"allowed_actions": ["alter_course"]},
        })
        self.assertEqual(decision.action, "alter_course")
        self.assertEqual(decision.degrees, -30.0)

    def test_rag_large_course_intent_is_not_gated_by_simulator_risk(self):
        class Retriever:
            reranker = SimpleNamespace(name="test")

            def retrieve(self, query):
                return [], []

            @staticmethod
            def prompt_context(items):
                return ""

        class Completions:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=(
                        '{"action":"alter_course","degrees":"large_starboard",'
                        '"speed":"none","reason":"Substantial turn.",'
                        '"auxiliary_commands":[]}'
                    )
                ))])

        self.oow.colregs_retriever = Retriever()
        self.oow.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        decision = self.oow.decide({
            "user_prompt": "state",
            "narrative": "underway",
            "state": {
                "own_ship": {"recovery_speed": 6.173},
                "contacts": [],
            },
            "constraints": {"allowed_actions": ["alter_course"]},
        })
        self.assertEqual(decision.degrees, 45.0)


if __name__ == "__main__":
    unittest.main()
