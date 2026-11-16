import json
import unittest

from agents.agent_oow import BASE_SYSTEM_PROMPT
from sft.prepare_prompt_matched_sft import base_narrative_row


class PreparePromptMatchedSftTests(unittest.TestCase):
    def test_converts_teacher_payload_to_base_runtime_shape(self):
        row = {
            "example_id": "example-1",
            "source_file": "mission.jsonl",
            "prompt": [
                {"role": "system", "content": "teacher export"},
                {
                    "role": "user",
                    "content": json.dumps({
                        "state": {"own_ship": {}},
                        "allowed_actions": ["maintain_course", "alter_course"],
                        "briefing": "legacy nautical briefing",
                        "narrative": "runtime metre briefing",
                        "deterministic_colregs_oracle": {"contacts": []},
                        "active_action_review": {"active_action": "alter_course"},
                    }),
                },
            ],
            "completion": [{"role": "assistant", "content": "{}"}],
            "token_count": 999,
        }

        converted = base_narrative_row(row)
        payload = json.loads(converted["prompt"][1]["content"])

        self.assertEqual(converted["prompt"][0]["content"], BASE_SYSTEM_PROMPT)
        self.assertEqual(payload, {
            "allowed_actions": ["maintain_course", "alter_course"],
            "briefing": "runtime metre briefing",
            "active_action_review": {"active_action": "alter_course"},
        })
        self.assertNotIn("token_count", converted)
        self.assertEqual(converted["completion"], row["completion"])

    def test_falls_back_to_existing_briefing(self):
        row = {
            "example_id": "example-2",
            "prompt": [
                {"role": "system", "content": "old"},
                {"role": "user", "content": json.dumps({
                    "allowed_actions": ["maintain_course"],
                    "briefing": "only briefing",
                })},
            ],
            "completion": [{"role": "assistant", "content": "{}"}],
        }
        converted = base_narrative_row(row)
        payload = json.loads(converted["prompt"][1]["content"])
        self.assertEqual(payload["briefing"], "only briefing")


if __name__ == "__main__":
    unittest.main()
