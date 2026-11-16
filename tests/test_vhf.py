import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import wave

from agents.agent_vhf import AgentVHF
from agents.schemas.vhf import VHFInterpretation


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 160)


class VHFDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_transcription_is_quarantined_without_crashing_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            audio = inbox / "call.wav"
            write_wav(audio)

            agent = AgentVHF.__new__(AgentVHF)
            agent.input_dir = inbox
            agent.processed_dir = root / "processed"
            agent.failed_dir = root / "failed"
            agent.last_failures = []
            agent.whisper = SimpleNamespace(
                transcribe=lambda _path: (_ for _ in ()).throw(
                    RuntimeError("CUDA out of memory")
                )
            )
            agent.captain_queue = asyncio.Queue()

            events = await agent.process_directory(
                bridge_snapshot=SimpleNamespace(state_for_agent=lambda: {})
            )

            self.assertEqual(events, [])
            self.assertFalse(audio.exists())
            self.assertTrue((agent.failed_dir / "call.wav").exists())
            self.assertIn("CUDA out of memory", agent.last_failures[0]["error"])

    async def test_exact_scenario_transcript_is_interpreted_and_relayed_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            scenario_input = inbox / "vhf-06_request.json"
            scenario_input.write_text(
                '{"schema_version":1,"scenario_id":"VHF-06",'
                '"event_id":"bravo_unsafe_port_request",'
                '"channel":13,"language":"en",'
                '"transcript":"ALPHA, this is BRAVO. Alter course to port. Over."}',
                encoding="utf-8",
            )

            agent = AgentVHF.__new__(AgentVHF)
            agent.input_dir = inbox
            agent.processed_dir = root / "processed"
            agent.failed_dir = root / "failed"
            agent.last_failures = []
            agent.minimum_confidence = 0.8
            agent.captain_queue = asyncio.Queue()
            agent.interpret = lambda *_args, **_kwargs: VHFInterpretation(
                speaker="BRAVO",
                addressee="ALPHA",
                addressed_to_own_ship=True,
                source_type="vessel",
                identity_status="verified",
                intent="maneuver_request",
                normalized_message="BRAVO requests ALPHA alter course to port.",
                requested_action="alter_course",
                requested_turn_direction="port",
                relay_required=False,
                recommended_action="none",
                confidence=0.99,
                clarification_required=False,
            )
            snapshot = SimpleNamespace(
                version=1,
                state_for_agent=lambda: {
                    "vehicle": "ALPHA",
                    "contacts": [{"name": "BRAVO"}],
                },
            )

            events = await agent.process_directory(bridge_snapshot=snapshot)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].source.input_kind, "scenario_text")
            self.assertEqual(events[0].source.channel, 13)
            self.assertEqual(events[0].source.scenario_id, "VHF-06")
            self.assertEqual(
                events[0].source.scenario_event_id,
                "bravo_unsafe_port_request",
            )
            self.assertEqual(events[0].transcription.model, "scenario-exact-text")
            self.assertTrue((agent.processed_dir / scenario_input.name).exists())
            queued = await agent.captain_queue.get()
            json.dumps(queued)
            self.assertFalse(queued["report"]["actionable"])
            self.assertEqual(
                queued["report"]["advisory"]["requested_action"],
                "alter_course",
            )
            self.assertEqual(
                queued["report"]["advisory"]["requested_turn_direction"],
                "port",
            )
            self.assertTrue(
                queued["report"]["advisory"]["addressed_to_own_ship"]
            )

    def test_directional_request_uses_current_action_contract(self):
        request = VHFInterpretation(
            speaker="BRAVO",
            addressee="ALPHA",
            addressed_to_own_ship=True,
            source_type="vessel",
            identity_status="verified",
            intent="maneuver_request",
            normalized_message="Alter course twenty degrees to starboard.",
            requested_action="alter_course",
            requested_turn_direction="starboard",
            requested_turn_degrees=20,
            relay_required=False,
            recommended_action="none",
            confidence=0.99,
            clarification_required=False,
        )

        self.assertEqual(request.requested_action, "alter_course")
        self.assertEqual(request.requested_turn_direction, "starboard")
        self.assertEqual(request.requested_turn_degrees, 20)

    def test_direction_without_alter_course_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-alter_course"):
            VHFInterpretation(
                intent="instruction",
                normalized_message="Proceed inbound.",
                requested_action="proceed",
                requested_turn_direction="starboard",
                relay_required=False,
                recommended_action="none",
                confidence=0.99,
                clarification_required=False,
            )

    async def test_explicit_release_is_json_safe_and_actionable_while_paused(self):
        agent = AgentVHF.__new__(AgentVHF)
        agent.minimum_confidence = 0.8
        agent.captain_queue = asyncio.Queue()
        interpretation = VHFInterpretation(
            speaker="Harbour VTS",
            addressee="ALPHA",
            addressed_to_own_ship=True,
            source_type="vts",
            identity_status="verified",
            intent="instruction",
            normalized_message="OUTBOUND has cleared. You may proceed inbound.",
            requested_action="proceed",
            requested_turn_direction="none",
            relay_required=True,
            recommended_action="resume",
            confidence=1.0,
            clarification_required=False,
        )

        advisory = await agent.relay_to_captain(
            interpretation,
            currently_paused=True,
            message_id="release-event",
            source={"scenario_event_id": "vts_fairway_release"},
            transcription={"text": "You may proceed inbound."},
        )
        queued = await agent.captain_queue.get()

        json.dumps(queued)
        self.assertTrue(queued["report"]["actionable"])
        self.assertEqual(advisory["requested_action"], "proceed")
        self.assertEqual(advisory["recommended_action"], "resume")


if __name__ == "__main__":
    unittest.main()
