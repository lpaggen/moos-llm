import json
from pathlib import Path
import tempfile
import unittest

from sft.prepare_teacher_campaigns import filter_token_lengths, read_episode


class PrepareTeacherCampaignsTests(unittest.TestCase):
    def test_token_filter_rejects_oversized_examples(self):
        class Encoding:
            def __init__(self, size):
                self.input_ids = list(range(size))

        class Tokenizer:
            @staticmethod
            def apply_chat_template(messages, **kwargs):
                return Encoding(len(messages[0]["content"]))

        row = {
            "example_id": "one", "prompt": [{"content": "12345"}],
            "completion": [],
        }
        from unittest.mock import patch
        with patch("transformers.AutoTokenizer.from_pretrained", return_value=Tokenizer()):
            accepted, rejected = filter_token_lengths([row], "unused", 4)
        self.assertEqual(accepted, [])
        self.assertEqual(rejected, [{"example_id": "one", "tokens": 5}])

    def test_completed_episode_is_converted_and_skip_is_excluded(self):
        decision = {
            "action": "maintain_course", "degrees": 0.0, "speed": 0.0,
            "reason": "Safe.", "auxiliary_commands": [],
        }
        skipped = {**decision, "action": "skip"}
        rows = [
            {"event": "oow_prompt_input", "cycle_id": 1, "user_prompt": "{}"},
            {"event": "oow_decision", "cycle_id": 1, "decision": decision},
            {"event": "captain_validated", "cycle_id": 1},
            {"event": "oow_prompt_input", "cycle_id": 2, "user_prompt": "{}"},
            {"event": "oow_decision", "cycle_id": 2, "decision": skipped},
            {"event": "captain_validated", "cycle_id": 2},
            {"event": "bridge_snapshot", "state": {"minimum_observed_range": 100}},
            {"event": "mission_complete"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed_1.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            episode, reason = read_episode(path, 50)
        self.assertEqual(reason, "accepted")
        self.assertEqual(len(episode), 1)
        self.assertEqual(episode[0]["label"]["action"], "maintain_course")
        self.assertEqual(episode[0]["completion"][0]["role"], "assistant")

    def test_failed_episode_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed_1.jsonl"
            path.write_text(json.dumps({"event": "mission_failed"}) + "\n")
            episode, reason = read_episode(path, 50)
        self.assertEqual(episode, [])
        self.assertEqual(reason, "mission_failed")
