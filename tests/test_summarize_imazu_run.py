from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.summarize_imazu_run import summarize


class SummarizeImazuRunTests(unittest.TestCase):
    def test_summary_extracts_outcome_and_minimum_range(self) -> None:
        events = [
            {"event": "startup", "oow_model": "base", "rag_enabled": False},
            {"event": "bridge_snapshot", "state": {"minimum_observed_range": 120.0}},
            {"event": "bridge_snapshot", "state": {"minimum_observed_range": 80.0}},
            {"event": "captain_decision", "decision": {"action": "alter_course"}},
            {"event": "colregs_validation_failed"},
            {"event": "mission_complete"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            audit.write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
            result = summarize(audit, case=4, exit_status=0, timed_out=False)
        self.assertEqual(result["result"], "complete")
        self.assertEqual(result["minimum_observed_range_m"], 80.0)
        self.assertEqual(result["actions"], {"alter_course": 1})
        self.assertEqual(result["validation_failures"], 1)

    def test_clean_exit_without_terminal_event_is_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            audit.write_text(
                json.dumps({"event": "bridge_snapshot", "state": {}}) + "\n",
                encoding="utf-8",
            )
            result = summarize(
                audit, case=3, exit_status=0, timed_out=False
            )

        self.assertEqual(result["result"], "exited")

    def test_cycle_limit_is_reported_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            audit.write_text(
                json.dumps({"event": "cycle_limit_reached"}) + "\n",
                encoding="utf-8",
            )
            result = summarize(
                audit, case=3, exit_status=0, timed_out=False
            )

        self.assertEqual(result["result"], "cycle_limit")


if __name__ == "__main__":
    unittest.main()
