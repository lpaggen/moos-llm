import json
import tempfile
import unittest
from pathlib import Path

from agents.agent_oow import BASE_SYSTEM_PROMPT
from sft.prepare_exact_runtime_sft import (
    balanced_training_rows,
    episode_rows,
    validate_audit_provenance,
    validate_decision,
)


def labeled_row(action: str, cycle_id: int) -> dict:
    return {
        "source_file": "episode",
        "cycle_id": cycle_id,
        "label": {"action": action},
    }


class ExactRuntimeSFTTests(unittest.TestCase):
    def test_balancing_caps_maintain_without_duplicating_manoeuvres(self):
        rows = [labeled_row("maintain_course", index) for index in range(20)]
        rows += [labeled_row("alter_course", 20 + index) for index in range(3)]
        rows += [labeled_row("set_speed", 23 + index) for index in range(2)]
        balanced = balanced_training_rows(rows, seed=7, max_maintain_fraction=0.5)
        actions = [item["label"]["action"] for item in balanced]
        self.assertEqual(actions.count("maintain_course"), 5)
        self.assertEqual(actions.count("alter_course"), 3)
        self.assertEqual(actions.count("set_speed"), 2)

    def test_rejects_privileged_target_reference(self):
        with self.assertRaisesRegex(ValueError, "unavailable evidence"):
            validate_decision(
                {
                    "action": "maintain_course",
                    "degrees": 0.0,
                    "speed": 0.0,
                    "reason": "The oracle classifies this encounter as safe.",
                    "auxiliary_commands": [],
                },
                ["maintain_course"],
            )

    def test_accepts_skip_when_runtime_allows_it(self):
        validate_decision(
            {
                "action": "skip",
                "degrees": 0.0,
                "speed": 0.0,
                "reason": "Remain stopped while the crossing contact clears under Rule 7.",
                "auxiliary_commands": [],
            },
            ["resume", "skip"],
        )

    def test_reads_only_exact_recorded_runtime_messages(self):
        payload = {
            "allowed_actions": ["maintain_course"],
            "briefing": "Safe and opening.",
        }
        decision = {
            "action": "maintain_course",
            "degrees": 0.0,
            "speed": 0.0,
            "reason": "The contact is opening, so no maneuver is required under Rule 7.",
            "auxiliary_commands": [],
        }
        events = [
            {
                "event": "startup",
                "openai_api": True,
                "openai_prompt_profile": "runtime-base",
                "oow_model": "gpt-6-luna",
            },
            {
                "event": "oow_prompt_input",
                "cycle_id": 1,
                "system_prompt": BASE_SYSTEM_PROMPT,
                "user_prompt": json.dumps(payload, separators=(",", ":")),
            },
            {"event": "oow_decision", "cycle_id": 1, "decision": decision},
            {"event": "captain_validated", "cycle_id": 1},
        ]
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            audit.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            rows = episode_rows(audit)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"][0]["content"], BASE_SYSTEM_PROMPT)
        self.assertEqual(json.loads(rows[0]["prompt"][1]["content"]), payload)
        self.assertEqual(json.loads(rows[0]["completion"][0]["content"]), decision)

    def test_rejects_audit_without_exact_teacher_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            audit.write_text(
                json.dumps({
                    "event": "startup",
                    "openai_api": True,
                    "openai_prompt_profile": "teacher",
                }) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "runtime-base"):
                validate_audit_provenance(audit)


if __name__ == "__main__":
    unittest.main()
