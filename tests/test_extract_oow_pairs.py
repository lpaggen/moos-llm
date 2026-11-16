import json
from pathlib import Path
import tempfile
import unittest

from tools.extract_oow_pairs import _format_pair, _is_rare_stop_or_port, extract_pairs


class ExtractOowPairsTests(unittest.TestCase):
    def test_rare_filter_accepts_only_stop_and_port_alterations(self):
        self.assertTrue(_is_rare_stop_or_port({"action": "stop", "degrees": 0}))
        self.assertTrue(_is_rare_stop_or_port({"action": "alter_course", "degrees": -30}))
        self.assertFalse(_is_rare_stop_or_port({"action": "alter_course", "degrees": 30}))
        self.assertFalse(_is_rare_stop_or_port({"action": "maintain_course", "degrees": 0}))

    def extract(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            return list(extract_pairs(path))

    def test_pairs_by_cycle_and_keeps_only_oow_input_and_output(self):
        decision = {"action": "maintain_course", "degrees": 0, "speed": 0}
        pairs = self.extract([
            {"event": "oow_prompt_input", "cycle_id": 1, "narrative": "first", "system_prompt": "system"},
            {"event": "oow_prompt_input", "cycle_id": 2, "narrative": "second"},
            {"event": "captain_validated", "cycle_id": 1, "decision": decision},
            {"event": "oow_decision", "cycle_id": 2, "decision": decision},
            {"event": "oow_decision", "cycle_id": 1, "decision": decision},
            {"event": "oow_decision", "cycle_id": 3, "decision": decision},
        ])
        self.assertEqual(pairs, [
            {"input": {"narrative": "second"}, "output": decision},
            {"input": {"system_prompt": "system", "narrative": "first"}, "output": decision},
        ])

    def test_failed_and_previous_session_inputs_are_not_paired(self):
        self.assertEqual(self.extract([
            {"event": "oow_prompt_input", "cycle_id": 1, "narrative": "old"},
            {"event": "startup"},
            {"event": "oow_decision", "cycle_id": 1, "decision": {"action": "stop"}},
            {"event": "oow_prompt_input", "cycle_id": 2, "narrative": "failed"},
            {"event": "oow_decision_failed", "cycle_id": 2},
            {"event": "oow_decision", "cycle_id": 2, "decision": {"action": "stop"}},
            {"event": "oow_prompt_input", "cycle_id": 3, "narrative": "unfinished"},
        ]), [])

    def test_chat_export_removes_teacher_oracle(self):
        pair = {
            "input": {
                "user_prompt": json.dumps({
                    "state": {"own_ship": {"speed": 3}},
                    "deterministic_colregs_oracle": {"contacts": [{"state": "HO"}]},
                })
            },
            "output": {"action": "maintain_course"},
        }
        exported = _format_pair(pair, True)
        student_input = json.loads(exported["messages"][1]["content"])
        self.assertNotIn("deterministic_colregs_oracle", student_input)
        self.assertIn("state", student_input)
