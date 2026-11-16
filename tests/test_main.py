import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import main


class ProcessCycleTests(unittest.IsolatedAsyncioTestCase):
    def test_vhf_reports_expire_after_three_prompt_cycles(self):
        report = {
            "advisory": {"message_id": "radio-1"},
            "received_cycle": 12,
        }

        arrival = main._recent_vhf_reports([report], 12)
        first_history = main._recent_vhf_reports([report], 13)
        second_history = main._recent_vhf_reports([report], 14)
        expired = main._recent_vhf_reports([report], 15)

        self.assertEqual(arrival[0]["context_status"], "newly_received")
        self.assertEqual(arrival[0]["age_cycles"], 0)
        self.assertEqual(first_history[0]["context_status"], "recent_already_presented")
        self.assertEqual(first_history[0]["age_cycles"], 1)
        self.assertEqual(second_history[0]["age_cycles"], 2)
        self.assertEqual(expired, [])

    def test_persistent_vhf_instruction_survives_history_window_until_cleared(self):
        report = {
            "advisory": {
                "message_id": "00000000-0000-0000-0000-000000000001",
                "addressed_to_own_ship": True,
                "directive_lifetime": "until_cancelled",
                "directive_end_condition": None,
            },
            "received_cycle": 12,
        }

        active = main._recent_vhf_reports([report], 40)
        self.assertEqual(len(active), 1)
        self.assertEqual(
            active[0]["context_status"], "active_persistent_instruction"
        )

        retained, cleared, rejected = main._apply_vhf_instruction_clearances(
            active,
            [{
                "type": "clear_vhf_instruction",
                "message_id": "00000000-0000-0000-0000-000000000001",
                "reason": "Harbour VTS explicitly released the hold.",
            }],
        )
        self.assertEqual(retained, [])
        self.assertEqual(len(cleared), 1)
        self.assertEqual(rejected, [])

    def test_transient_vhf_message_cannot_be_cleared_as_persistent(self):
        report = {
            "advisory": {
                "message_id": "00000000-0000-0000-0000-000000000002",
                "addressed_to_own_ship": True,
                "directive_lifetime": "transient",
            },
            "received_cycle": 12,
        }
        retained, cleared, rejected = main._apply_vhf_instruction_clearances(
            [report],
            [{
                "type": "clear_vhf_instruction",
                "message_id": "00000000-0000-0000-0000-000000000002",
                "reason": "Done.",
            }],
        )
        self.assertEqual(retained, [report])
        self.assertEqual(cleared, [])
        self.assertEqual(rejected[0]["error"], "message is not persistent")

    async def test_maib_rag_uses_captain_as_final_decision_maker(self):
        state = {
            "mission_complete": False,
            "mission_failed": False,
            "allowed_actions": ["maintain_course", "alter_course"],
        }
        snapshot = Mock(version=7, received_at=1.0)
        snapshot.state_for_agent.return_value = state
        retrieval = Mock()
        retrieval.as_dict.return_value = {
            "chunk_id": "maib-test",
            "text": "The investigation found that action was delayed.",
        }
        oow_decision = Mock()
        oow_decision.model_dump.return_value = {
            "action": "maintain_course", "degrees": 0.0, "speed": 0.0,
            "reason": "OOW recommendation.", "auxiliary_commands": [],
        }
        captain_decision = Mock()
        captain_decision.model_dump.return_value = {
            "action": "alter_course", "degrees": 30.0, "speed": 0.0,
            "reason": "Live geometry and [maib-test] support early action.",
            "auxiliary_commands": [],
        }
        validated = {
            **captain_decision.model_dump.return_value,
            "distance_m": 500.0,
        }
        captain = SimpleNamespace(
            queue=asyncio.Queue(),
            maib_retriever=object(),
            rag_decision_enabled=True,
            model="test-captain",
            system_prompt="captain prompt",
            last_prompt_payload={"maib_case_evidence": {"excerpts": "..."}},
            last_evidence_assessment={"analogy": "..."},
            last_navigation_assessment={"controlling_contact": "test-contact"},
            retrieve_evidence=Mock(return_value=("crossing close CPA", [retrieval])),
            decide=Mock(return_value=captain_decision),
            validate_decision=Mock(return_value=validated),
            command_from_decision=Mock(return_value={"action": "alter_course"}),
        )
        oow = SimpleNamespace(
            model="test-oow",
            system_prompt="oow prompt",
            last_colregs_retrieval=None,
            deterministic_input=Mock(return_value={
                "narrative": "state", "constraints": {}, "state": {},
                "vhf_communications": [{
                    "message_id": "radio-1",
                    "speaker": "BRAVO",
                    "addressee": "ALPHA",
                    "addressed_to_own_ship": True,
                    "requested_action": "alter_course",
                    "requested_turn_direction": "port",
                }],
            }),
            decide=Mock(return_value=oow_decision),
        )
        vhf = SimpleNamespace(
            input_dir="inbox",
            processed_dir="processed",
            process_directory=AsyncMock(return_value=[]),
        )
        audit = Mock()

        with (
            patch.object(main.BRIDGE_STATE, "refresh", return_value=snapshot),
            patch.object(main, "audit", audit),
            patch.object(main, "send_command") as send,
        ):
            complete = await main.process_cycle(captain, oow, vhf)

        self.assertFalse(complete)
        captain.retrieve_evidence.assert_called_once()
        captain.decide.assert_called_once()
        review_state = captain.decide.call_args.args[0]
        self.assertEqual(
            review_state["oow_proposal"], oow_decision.model_dump.return_value
        )
        self.assertIn("Approve it unless", review_state["task"])
        self.assertIn("specific, material", review_state["task"])
        self.assertEqual(
            review_state["vhf_communications"][0]["message_id"], "radio-1"
        )
        captain.validate_decision.assert_called_once_with(
            captain_decision.model_dump.return_value, state
        )
        send.assert_called_once_with(
            {"action": "alter_course"}, main.BRIDGE_HOST, main.BRIDGE_PORT
        )
        events = [call.args[0] for call in audit.call_args_list]
        self.assertIn("maib_retrieval", events)
        self.assertIn("captain_prompt_input", events)
        self.assertIn("captain_decision", events)
        self.assertIn("captain_oow_comparison", events)
        context_call = next(
            call for call in audit.call_args_list
            if call.args[0] == "vhf_decision_context"
        )
        self.assertEqual(
            context_call.kwargs["decision_makers"], ["oow", "captain_critic"]
        )

    async def test_vhf_advisory_reaches_oow_decision_prompt(self):
        state = {
            "mission_complete": False,
            "mission_failed": False,
            "allowed_actions": ["maintain_course"],
        }
        snapshot = Mock(version=12, received_at=1.0)
        snapshot.state_for_agent.return_value = state
        decision = Mock()
        decision.model_dump.return_value = {
            "action": "maintain_course",
            "degrees": 0.0,
            "speed": 0.0,
            "reason": "Assessed the radio request against traffic.",
            "auxiliary_commands": [],
        }
        advisory = {
            "message_id": "radio-12",
            "speaker": "BRAVO",
            "addressee": "ALPHA",
            "addressed_to_own_ship": True,
            "requested_action": "alter_course",
            "requested_turn_direction": "port",
        }
        captain = SimpleNamespace(
            queue=asyncio.Queue(),
            maib_retriever=None,
            rag_decision_enabled=False,
            validate_decision=Mock(return_value={
                **decision.model_dump.return_value,
                "distance_m": 0.0,
            }),
            command_from_decision=Mock(return_value=None),
        )
        await captain.queue.put({
            "source": "vhf",
            "report": {"advisory": advisory, "source": {"channel": 13}},
        })
        transformed = {
            "narrative": "RECEIVED VHF COMMUNICATIONS: BRAVO requests port.",
            "user_prompt": '{"vhf_communications":[{"message_id":"radio-12"}]}',
            "constraints": {"allowed_actions": ["maintain_course"]},
            "state": state,
            "vhf_communications": [advisory],
        }
        oow = SimpleNamespace(
            model="test-oow",
            system_prompt="VHF evidence contract",
            last_colregs_retrieval=None,
            last_paper_colregs=None,
            deterministic_input=Mock(return_value=transformed),
            decision_trigger=Mock(return_value={
                "call_model": True, "reason": "active_vhf_communication"
            }),
            decide=Mock(return_value=decision),
        )
        vhf = SimpleNamespace(
            input_dir="inbox",
            processed_dir="processed",
            last_failures=[],
            process_directory=AsyncMock(return_value=[]),
        )
        audit = Mock()

        with (
            patch.object(main.BRIDGE_STATE, "refresh", return_value=snapshot),
            patch.object(main, "ACTIVE_VHF_REPORTS", []),
            patch.object(main, "audit", audit),
            patch.object(main, "send_command"),
        ):
            terminal = await main.process_cycle(captain, oow, vhf)

        self.assertFalse(terminal)
        passed_reports = oow.deterministic_input.call_args.kwargs["vhf_advisories"]
        self.assertEqual(passed_reports[0]["advisory"]["message_id"], "radio-12")
        oow.decide.assert_called_once_with(transformed)
        events = [call.args[0] for call in audit.call_args_list]
        self.assertIn("vhf_to_captain", events)
        self.assertIn("vhf_decision_context", events)
        prompt_call = next(
            call for call in audit.call_args_list
            if call.args[0] == "oow_prompt_input"
        )
        self.assertIn("radio-12", prompt_call.kwargs["user_prompt"])

    async def test_cycle_limit_terminates_without_model_or_command(self):
        state = {
            "mission_complete": False,
            "mission_failed": False,
            "minimum_observed_range": 400.0,
        }
        snapshot = Mock(version=120, received_at=1.0)
        snapshot.state_for_agent.return_value = state
        captain = Mock()
        oow = Mock()
        vhf = Mock()
        audit = Mock()

        with (
            patch.object(main.BRIDGE_STATE, "refresh", return_value=snapshot),
            patch.object(main, "audit", audit),
            patch.dict("os.environ", {"MOOS_LLM_MAX_CYCLES": "120"}),
        ):
            terminal = await main.process_cycle(captain, oow, vhf)

        self.assertTrue(terminal)
        oow.decide.assert_not_called()
        captain.command_from_decision.assert_not_called()
        events = [call.args[0] for call in audit.call_args_list]
        self.assertIn("cycle_limit_reached", events)

    async def test_resume_is_not_rewritten_by_collision_guard(self):
        state = {
            "mission_complete": False,
            "paused": True,
            "stopped": True,
            "allowed_actions": ["resume", "skip"],
        }
        snapshot = Mock(version=28, received_at=1.0)
        snapshot.state_for_agent.return_value = state
        decision = Mock()
        decision.model_dump.return_value = {
            "action": "resume",
            "degrees": 0.0,
            "speed": 0.0,
            "reason": "Resume.",
            "auxiliary_commands": [],
        }
        captain = SimpleNamespace(
            queue=asyncio.Queue(),
            maib_retriever=None,
            enforce_collision_rules=Mock(side_effect=AssertionError(
                "execution path must not override the OOW decision"
            )),
            validate_decision=Mock(return_value={
                "action": "resume",
                "degrees": 0.0,
                "distance_m": 0.0,
                "speed": 0.0,
                "reason": "Resume.",
                "auxiliary_commands": [],
            }),
            command_from_decision=Mock(return_value={
                "type": "command", "class": "control", "action": "resume"
            }),
        )
        oow = SimpleNamespace(
            model="test-model",
            system_prompt="prompt",
            last_colregs_retrieval=None,
            deterministic_input=Mock(return_value={
                "narrative": "state", "constraints": {}, "state": {},
            }),
            decide=Mock(return_value=decision),
        )
        vhf = SimpleNamespace(
            input_dir="inbox",
            processed_dir="processed",
            process_directory=AsyncMock(return_value=[]),
        )

        with (
            patch.object(main.BRIDGE_STATE, "refresh", return_value=snapshot),
            patch.object(main.BRIDGE_STATE, "recent_history", return_value=[]),
            patch.object(main, "audit"),
            patch.object(main, "send_command") as send,
        ):
            await main.process_cycle(captain, oow, vhf)

        captain.enforce_collision_rules.assert_not_called()
        captain.validate_decision.assert_called_once_with(
            decision.model_dump.return_value, state
        )
        send.assert_called_once()

    async def test_collision_is_a_terminal_mission_failure(self):
        state = {
            "mission_complete": True,
            "mission_failed": True,
            "mission_failure_reason": "collision",
            "minimum_range_contact": "RANDOM_TS1",
            "minimum_observed_range": 7.5,
            "nav": {"x": 1.0, "y": 2.0},
        }
        snapshot = Mock(version=4, received_at=1.0)
        snapshot.state_for_agent.return_value = state
        audit = Mock()

        with (
            patch.object(main.BRIDGE_STATE, "refresh", return_value=snapshot),
            patch.object(main.BRIDGE_STATE, "recent_history", return_value=[]),
            patch.object(main, "audit", audit),
        ):
            terminal = await main.process_cycle(Mock(), Mock(), Mock())

        self.assertTrue(terminal)
        events = [call.args[0] for call in audit.call_args_list]
        self.assertIn("mission_failed", events)
        self.assertNotIn("mission_complete", events)

    def test_audit_json_conversion_serializes_vhf_datetime(self):
        received_at = datetime(2026, 9, 9, 9, 41, tzinfo=timezone.utc)

        converted = main._jsonable({"received_at": received_at})

        self.assertEqual(converted["received_at"], "2026-09-09T09:41:00+00:00")
        self.assertIn("received_at", json.dumps(converted))

    async def test_invalid_oow_decision_is_rejected_without_override_or_command(self):
        state = {"mission_complete": False}
        snapshot = Mock(version=9, received_at=1.0)
        snapshot.state_for_agent.return_value = state

        captain = SimpleNamespace(
            queue=asyncio.Queue(),
            maib_retriever=object(),
            validate_decision=Mock(side_effect=ValueError("invalid agent parameters")),
            command_from_decision=Mock(),
        )
        decision = Mock()
        decision.model_dump.return_value = {
            "action": "maintain_course", "degrees": 0.0, "speed": 20.0,
            "reason": "Invalid.", "auxiliary_commands": [],
        }
        oow = SimpleNamespace(
            model="oow-finetuned",
            system_prompt="prompt",
            last_colregs_retrieval=None,
            deterministic_input=Mock(return_value={
                "narrative": "state", "constraints": {}, "state": {},
            }),
            decide=Mock(return_value=decision),
        )
        vhf = SimpleNamespace(
            input_dir="inbox",
            processed_dir="processed",
            process_directory=AsyncMock(return_value=[]),
        )
        audit = Mock()

        with (
            patch.object(main.BRIDGE_STATE, "refresh", return_value=snapshot),
            patch.object(main.BRIDGE_STATE, "recent_history", return_value=[]),
            patch.object(main, "audit", audit),
            patch.object(main, "send_command"),
        ):
            complete = await main.process_cycle(captain, oow, vhf)

        self.assertFalse(complete)
        captain.command_from_decision.assert_not_called()
        events = [call.args[0] for call in audit.call_args_list]
        self.assertIn("oow_decision_rejected", events)
        performance = [
            call for call in audit.call_args_list
            if call.args and call.args[0] == "cycle_performance"
        ]
        self.assertEqual(len(performance), 1)
        self.assertEqual(performance[0].kwargs["retrieval_duration_s"], 0.0)
        self.assertEqual(performance[0].kwargs["captain_duration_s"], 0.0)

    async def test_repeated_invalid_model_output_does_not_stop_simulation(self):
        state = {"mission_complete": False}
        snapshot = Mock(version=10, received_at=1.0)
        snapshot.state_for_agent.return_value = state
        captain = SimpleNamespace(queue=asyncio.Queue(), maib_retriever=None)
        oow = SimpleNamespace(
            model="oow-finetuned",
            system_prompt="prompt",
            last_colregs_retrieval=None,
            deterministic_input=Mock(return_value={
                "narrative": "state", "constraints": {}, "state": {},
            }),
            decide=Mock(side_effect=RuntimeError("OOW returned invalid JSON twice")),
        )
        vhf = SimpleNamespace(
            input_dir="inbox",
            processed_dir="processed",
            process_directory=AsyncMock(return_value=[]),
        )
        audit = Mock()

        with (
            patch.object(main.BRIDGE_STATE, "refresh", return_value=snapshot),
            patch.object(main, "audit", audit),
        ):
            complete = await main.process_cycle(captain, oow, vhf)

        self.assertFalse(complete)
        events = [call.args[0] for call in audit.call_args_list]
        self.assertIn("oow_decision_failed", events)

if __name__ == "__main__":
    unittest.main()
