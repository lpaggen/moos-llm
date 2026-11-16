import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import asyncio
from uuid import UUID

from agents.agent_captain import AgentCaptain
from agents.schemas.captain import CaptainRagDecision
from rag.maib import (
    MaibRetriever,
    RetrievalResult,
    natural_query_from_state,
    query_from_state,
    retrieval_preferences_from_state,
)


class MaibRetrieverTests(unittest.TestCase):
    def test_captain_prompt_budget_compacts_oversized_retrieval_before_api_call(self):
        captain = AgentCaptain(
            client=Mock(),
            validator_queue=asyncio.Queue(),
            maib_retriever=Mock(),
            rag_decision_enabled=True,
            max_context_tokens=8192,
            max_output_tokens=512,
        )
        snapshot = Mock()
        snapshot.state_for_agent.return_value = {
            "vehicle": "LLM_SHIP",
            "vessel_type": "ship",
            "moos_time": 10.0,
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0},
            "mission_objective": {"x": 0.0, "y": 10000.0},
            "target_speed": 6.0,
            "recovery_speed": 6.0,
            "movement_status": "underway",
            "stopped": False,
            "paused": False,
            "contacts": [],
            "raw_report": "must not reach the compact prompt",
        }
        snapshot.captain_state_for_agent.return_value = {
            "own_ship": {"name": "duplicated"},
            "mission": {"objective": {"x": 0.0, "y": 10000.0}},
            "control": {"paused": False, "stopped": False},
            "allowed_actions": ["maintain_course"],
        }
        evidence = [RetrievalResult(
            chunk_id="oversized-evidence",
            report_title="Oversized test report",
            source_file="accidents/test.pdf",
            page=1,
            text="investigator evidence " * 5000,
            score=9.0,
        )]

        payload = captain.build_prompt_payload(
            {
                "task": "Review the OOW proposal.",
                "oow_proposal": {
                    "action": "maintain_course", "degrees": 0.0, "speed": 0.0,
                },
            },
            snapshot,
            retrieved_evidence=evidence,
        )
        messages, budget = captain._messages_within_budget(payload)

        self.assertLessEqual(
            budget["final_input_tokens"], budget["input_budget_tokens"]
        )
        self.assertTrue(budget["evidence_truncated"])
        sent_payload = json.loads(messages[1]["content"])
        self.assertNotIn("raw_report", messages[1]["content"])
        self.assertNotIn("own_ship", sent_payload["bridge_control_summary"])
        self.assertNotIn("mission", sent_payload["bridge_control_summary"])

    def test_captain_prompt_contains_retrieved_investigator_reasoning(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({
                "oow_verdict": "approve",
                "oow_critique": "The proposed control is proportionate to the observed geometry.",
                "control": {"action": "maintain_course"},
                "controlling_contact": "none",
                "own_role": "not_applicable",
                "encounter_phase": "clear",
                "geometry_check": "No live contact exists to create a geometry conflict.",
                "reason": "Live geometry does not support intervention.",
                "evidence_analogy": "Both cases require reliable collision-risk assessment.",
                "evidence_difference": "No contact exists in the live state.",
                "transferable_lesson": "Do not infer a hazard without observations.",
                "auxiliary_commands": [],
            })
        ))])
        client = Mock()
        client.chat.completions.create.return_value = response
        retriever = Mock()
        captain = AgentCaptain(
            client=client,
            validator_queue=asyncio.Queue(),
            maib_retriever=retriever,
            rag_decision_enabled=True,
        )
        snapshot = Mock()
        snapshot.state_for_agent.return_value = {
            "contacts": [{
                "name": "live-contact",
                "risk": "medium",
                "encounter_type": "crossing",
                "cpa_distance": 70.0,
                "tcpa_s": 120.0,
            }],
            "colregs_context": {
                "encounters": [{"contact": "live-contact", "own_role": "give_way"}],
            },
        }
        snapshot.captain_state_for_agent.return_value = {
            "movement_status": "underway",
            "contacts": [],
        }
        evidence = [RetrievalResult(
            chunk_id="maib-analysis-1",
            report_title="Example collision investigation",
            source_file="accidents/example.pdf",
            page=12,
            text="The investigation found that the OOW acted before assessing CPA.",
            score=9.0,
        )]

        decision = captain.decide(
            {
                "task": "Review the OOW proposal.",
                "oow_proposal": {
                    "action": "maintain_course", "degrees": 0.0, "speed": 0.0,
                    "auxiliary_commands": [{
                        "type": "clear_vhf_instruction",
                        "message_id": UUID(
                            "00000000-0000-0000-0000-000000000001"
                        ),
                        "reason": "The instruction was explicitly cancelled.",
                    }],
                },
            },
            snapshot,
            retrieved_evidence=evidence,
        )

        self.assertEqual(decision.action, "maintain_course")
        prompt = captain.last_prompt_payload
        self.assertIsNotNone(prompt)
        self.assertIn("maib-analysis-1", prompt["maib_case_evidence"]["excerpts"])
        self.assertIn("published-investigation reasoning", prompt["maib_case_evidence"]["status"])
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["messages"][0]["content"], captain.system_prompt)
        self.assertIn("maib_case_evidence", request["messages"][1]["content"])
        self.assertIn(
            '"message_id": "00000000-0000-0000-0000-000000000001"',
            request["messages"][1]["content"],
        )
        self.assertNotIn("oow_recommendation", request["messages"][1]["content"])
        self.assertIn(
            "Approval is the correct result",
            prompt["decision_contract"]["review"],
        )
        self.assertIn(
            "less than or equal to that horizon",
            prompt["decision_contract"]["numeric_checks"],
        )
        self.assertIn(
            "No material defect identified",
            prompt["decision_contract"]["approval_wording"],
        )
        self.assertIn("selective safety reviewer", captain.system_prompt)
        self.assertIn("0 <= TCPA <= 600", captain.system_prompt)
        self.assertEqual(prompt["bridge_state"]["contacts"][0]["name"], "live-contact")
        self.assertNotIn("risk", prompt["bridge_state"]["contacts"][0])
        self.assertNotIn("encounter_type", prompt["bridge_state"]["contacts"][0])
        self.assertNotIn("colregs_context", prompt["bridge_state"])
        self.assertEqual(captain.last_evidence_assessment["lesson"],
                         "Do not infer a hazard without observations.")

    def test_captain_rejects_inconsistent_action_arguments(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({
                "oow_verdict": "approve",
                "oow_critique": "The proposed maintain-course control is appropriate.",
                "control": {
                    "action": "maintain_course",
                    "course_intent": "starboard_15",
                    "speed": 11.99,
                },
                "controlling_contact": "none",
                "own_role": "not_applicable",
                "encounter_phase": "clear",
                "geometry_check": "No live contact exists to create a geometry conflict.",
                "reason": "Maintain the existing plan.",
                "evidence_analogy": "none",
                "evidence_difference": "none",
                "transferable_lesson": "none",
                "auxiliary_commands": [],
            })
        ))])
        client = Mock()
        client.chat.completions.create.return_value = response
        captain = AgentCaptain(
            client=client,
            validator_queue=asyncio.Queue(),
            maib_retriever=Mock(),
            rag_decision_enabled=True,
        )
        snapshot = Mock()
        snapshot.state_for_agent.return_value = {"contacts": []}
        snapshot.captain_state_for_agent.return_value = {"contacts": []}

        with self.assertRaises(ValueError):
            captain.decide({
                "oow_proposal": {
                    "action": "maintain_course", "degrees": 0.0, "speed": 0.0,
                },
            }, snapshot, retrieved_evidence=[])

    def test_captain_converts_explicit_port_intent_to_negative_degrees(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({
                "oow_verdict": "approve",
                "oow_critique": "The proposed port alteration improves separation.",
                "control": {
                    "action": "alter_course",
                    "course_intent": "port_30",
                },
                "controlling_contact": "test-contact",
                "own_role": "give_way",
                "encounter_phase": "action_required",
                "geometry_check": "The port alteration increases separation from the contact.",
                "reason": "Alter to port for the controlling encounter.",
                "evidence_analogy": "The encounter geometry is comparable.",
                "evidence_difference": "Visibility differs.",
                "transferable_lesson": "Verify that the alteration improves CPA.",
                "auxiliary_commands": [],
            })
        ))])
        client = Mock()
        client.chat.completions.create.return_value = response
        captain = AgentCaptain(
            client=client, validator_queue=asyncio.Queue(),
            maib_retriever=Mock(), rag_decision_enabled=True,
        )
        snapshot = Mock()
        snapshot.state_for_agent.return_value = {"contacts": []}
        snapshot.captain_state_for_agent.return_value = {"contacts": []}

        decision = captain.decide({
            "oow_proposal": {
                "action": "alter_course", "degrees": -30.0, "speed": 0.0,
            },
        }, snapshot, retrieved_evidence=[])

        self.assertEqual(decision.action, "alter_course")
        self.assertEqual(decision.degrees, -30.0)

    def test_retrieval_is_gated_without_navigation_relevant_contact(self):
        retriever = Mock()
        captain = AgentCaptain(
            client=Mock(), validator_queue=asyncio.Queue(),
            maib_retriever=retriever, rag_decision_enabled=True,
        )
        snapshot = Mock()
        snapshot.state_for_agent.return_value = {"contacts": []}

        query, results = captain.retrieve_evidence({}, snapshot)

        self.assertIn("not requested", query)
        self.assertEqual(results, [])
        retriever.retrieve.assert_not_called()

    def test_ranks_matching_encounter_above_unrelated_case(self):
        rows = [
            {"chunk_id": "crossing", "report_title": "Collision", "source_file": "a.pdf", "page": 8,
             "text": "A starboard crossing vessel was closing with collision risk and action was late."},
            {"chunk_id": "grounding", "report_title": "Grounding", "source_file": "b.pdf", "page": 4,
             "text": "The ship grounded after an incorrect chart datum was selected in harbour."},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            result = MaibRetriever(path, top_k=1).retrieve("starboard crossing closing collision risk")
        self.assertEqual(result[0].chunk_id, "crossing")
        self.assertEqual(result[0].page, 8)

    def test_missing_index_is_safe(self):
        self.assertEqual(MaibRetriever("missing.jsonl").retrieve("collision"), [])

    def test_query_contains_encounter_categories(self):
        query = query_from_state({"contacts": [{"encounter_type": "head_on", "risk": "critical"}]})
        self.assertIn("head on", query)
        self.assertIn("critical", query)

    def test_natural_query_uses_geometry_without_policy_labels_or_action_leakage(self):
        decision = {"allowed_actions": ["turn_starboard_15", "slow_5"]}
        bridge = {
            "movement_status": "underway_using_engine",
            "contacts": [{
                "name": "contact_a",
                "vessel_type": "cargo ship",
                "nav": {"heading": 270.0, "speed": 8.0},
                "encounter_type": "crossing_starboard",
                "relative_bearing": 45.0,
                "closing_speed": 1.5,
                "cpa_distance": 70,
                "tcpa_s": 120,
                "risk": "high",
            }],
            "colregs_context": {
                "assumptions": {"visibility_condition": "clear"},
                "encounters": [{"contact": "contact_a", "own_role": "give_way"}],
            },
        }
        query = natural_query_from_state(decision, bridge)
        self.assertIn("rapidly closing", query)
        self.assertIn("projected CPA 70.0 metres", query)
        self.assertIn("TCPA 120.0 seconds", query)
        self.assertNotIn("give way", query)
        self.assertNotIn("crossing starboard", query)
        self.assertNotIn("high", query)
        self.assertNotIn("turn_starboard_15", query)
        self.assertNotIn("slow_5", query)

    def test_returns_at_most_one_chunk_per_report(self):
        rows = [
            {"chunk_id": "a1", "report_title": "A", "source_file": "a.pdf", "page": 1,
             "text": "crossing collision starboard vessel"},
            {"chunk_id": "a2", "report_title": "A", "source_file": "a.pdf", "page": 2,
             "text": "crossing collision starboard vessel close"},
            {"chunk_id": "b1", "report_title": "B", "source_file": "b.pdf", "page": 1,
             "text": "crossing collision vessel"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            result = MaibRetriever(path, top_k=3).retrieve("crossing collision starboard")
        self.assertEqual(len(result), 2)
        self.assertEqual(len({item.source_file for item in result}), 2)

    def test_lesson_card_metadata_precedes_lexical_similarity(self):
        rows = [
            {
                "chunk_id": "crossing",
                "report_title": "Crossing",
                "source_file": "a.pdf",
                "page": 1,
                "source_type": "published_investigation_lesson_card",
                "encounter_types": ["crossing"],
                "roles": ["give_way"],
                "encounter_phases": ["action_required"],
                "text": "overtaking collision risk give way vessel " * 20,
            },
            {
                "chunk_id": "overtaking",
                "report_title": "Overtaking",
                "source_file": "b.pdf",
                "page": 1,
                "source_type": "published_investigation_lesson_card",
                "encounter_types": ["overtaking"],
                "roles": ["give_way"],
                "encounter_phases": ["action_required"],
                "text": "The vessel approached another vessel from astern.",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            result = MaibRetriever(path, top_k=1).retrieve(
                "overtaking collision risk give way vessel",
                metadata_preferences={
                    "encounter_types": {"overtaking"},
                    "roles": {"give_way"},
                    "encounter_phases": {"action_required"},
                },
            )
        self.assertEqual(result[0].chunk_id, "overtaking")

    def test_retrieval_preferences_follow_live_encounter(self):
        bridge = {
            "nav": {"x": 0.0, "y": 0.0, "heading": 0.0},
        }
        preferences = retrieval_preferences_from_state(
            bridge,
            [{
                "name": "target",
                "nav": {"x": 0.0, "y": 100.0, "heading": 0.0, "speed": 5.0},
                "relative_bearing": 0.0,
                "tcpa_s": 250.0,
            }],
        )
        self.assertEqual(preferences["encounter_types"], {"overtaking"})
        self.assertEqual(preferences["roles"], set())
        self.assertEqual(preferences["encounter_phases"], {"action_required"})

    def test_captain_observations_only_state_strips_all_policy_verdicts(self):
        state = {
            "collision": False,
            "colregs_context": {
                "encounters": [{"own_role": "give_way", "applicable_rules": [15, 16]}],
            },
            "contacts": [{
                "name": "target",
                "nav": {"heading": 90.0, "speed": 8.0},
                "range": 800.0,
                "relative_bearing": 45.0,
                "closing_speed": 2.0,
                "cpa_distance": 70.0,
                "tcpa_s": 120.0,
                "encounter_type": "crossing",
                "risk": "critical",
                "projection_status": "valid",
                "required_cpa_m": 926.0,
                "moos_colregs": {"mode": "give_way"},
            }],
        }

        cleaned = AgentCaptain.observations_only_state(state)

        self.assertNotIn("colregs_context", cleaned)
        self.assertNotIn("collision", cleaned)
        contact = cleaned["contacts"][0]
        self.assertEqual(contact["cpa_distance"], 70.0)
        self.assertEqual(contact["tcpa_s"], 120.0)
        self.assertNotIn("encounter_type", contact)
        self.assertNotIn("risk", contact)
        self.assertNotIn("projection_status", contact)
        self.assertNotIn("required_cpa_m", contact)
        self.assertNotIn("moos_colregs", contact)

    def test_retrieval_gate_ignores_supplied_risk_and_role_labels(self):
        captain = AgentCaptain(
            client=Mock(), validator_queue=asyncio.Queue(),
            maib_retriever=Mock(), rag_decision_enabled=True,
        )
        state = {
            "colregs_context": {
                "encounters": [{"contact": "target", "own_role": "stand_on"}],
            },
            "contacts": [{
                "name": "target",
                "closing_speed": 2.0,
                "cpa_distance": 70.0,
                "tcpa_s": 900.0,
                "risk": "low",
            }],
        }

        relevant = captain.navigation_relevant_contacts(state)

        self.assertEqual([item["name"] for item in relevant], ["target"])

    def test_live_control_validation_rejects_repeated_course_command(self):
        payload = {
            "oow_verdict": "approve",
            "oow_critique": "The proposed alteration matches the intended maneuver.",
            "control": {"action": "alter_course", "course_intent": "starboard_60"},
            "controlling_contact": "target",
            "own_role": "give_way",
            "encounter_phase": "action_required",
            "geometry_check": "The active alteration is improving separation.",
            "reason": "Continue the existing alteration.",
            "evidence_analogy": "none",
            "evidence_difference": "none",
            "transferable_lesson": "none",
            "auxiliary_commands": [],
        }

        decision = CaptainRagDecision.model_validate(payload)
        state = {
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 60.0},
        }
        proposal = {"action": "alter_course", "degrees": 60.0, "speed": 0.0}
        with self.assertRaises(ValueError):
            AgentCaptain.validate_rag_control_against_state(decision, state, proposal)

        payload["control"] = {"action": "maintain_course"}
        payload["oow_verdict"] = "reject"
        payload["oow_critique"] = "Continuing the active maneuver does not require reissuing it."
        decision = CaptainRagDecision.model_validate(payload)
        AgentCaptain.validate_rag_control_against_state(decision, state, proposal)
        self.assertEqual(decision.control.action, "maintain_course")

    def test_captain_retries_contract_error_without_overriding_model(self):
        invalid = {
            "oow_verdict": "approve",
            "oow_critique": "The proposal matches the requested course alteration.",
            "control": {"action": "alter_course", "course_intent": "starboard_60"},
            "controlling_contact": "target",
            "own_role": "give_way",
            "encounter_phase": "action_required",
            "geometry_check": "The active alteration is improving separation.",
            "reason": "Continue the active alteration.",
            "evidence_analogy": "none",
            "evidence_difference": "none",
            "transferable_lesson": "none",
            "auxiliary_commands": [],
        }
        corrected = {
            **invalid,
            "control": {"action": "maintain_course"},
            "oow_verdict": "reject",
            "oow_critique": "The active alteration should continue without being reissued.",
        }
        client = Mock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps(invalid)
            ))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps(corrected)
            ))]),
        ]
        captain = AgentCaptain(
            client=client, validator_queue=asyncio.Queue(),
            maib_retriever=Mock(), rag_decision_enabled=True,
        )
        snapshot = Mock()
        snapshot.state_for_agent.return_value = {
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 60.0},
            "contacts": [],
        }
        snapshot.captain_state_for_agent.return_value = {}

        decision = captain.decide({
            "oow_proposal": {
                "action": "alter_course", "degrees": 60.0, "speed": 0.0,
            },
        }, snapshot, retrieved_evidence=[])

        self.assertEqual(decision.action, "maintain_course")
        self.assertEqual(client.chat.completions.create.call_count, 2)
        retry_messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("violated the control contract", retry_messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
