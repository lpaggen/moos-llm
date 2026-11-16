import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.analyze_prompt_ablation import (
    assembled_user_prompt,
    campaign_metrics,
    comparison,
    render_retrieval,
)


class PromptAblationTests(unittest.TestCase):
    def test_reconstructs_rag_prompt(self):
        selected = [{"chunk_id": "colreg-8", "rule": 8, "title": "Action", "text": "full"}]
        context = render_retrieval(selected, {8: ["Act early."]})
        prompt = {
            "user_prompt": "state",
            "constraints": {"allowed_actions": ["maintain_course"]},
        }
        rendered = assembled_user_prompt(prompt, {"selected": selected}, {8: ["Act early."]})
        self.assertIn("OPERATIONAL SUMMARY\n- Act early.", context)
        self.assertIn("RETRIEVED COLREG PROVISIONS", rendered)
        self.assertIn('["maintain_course"]', rendered)

    def test_measures_case_timing_without_crossing_case_boundaries(self):
        with TemporaryDirectory() as directory:
            run = Path(directory)
            case = run / "case_01"
            case.mkdir()
            rows = [
                {"timestamp": "2026-01-01T00:00:00+00:00", "event": "oow_prompt_input", "cycle_id": 1,
                 "model": "test", "system_prompt": "sys", "user_prompt": "one", "constraints": {}},
                {"timestamp": "2026-01-01T00:00:02+00:00", "event": "oow_decision", "cycle_id": 1},
                {"timestamp": "2026-01-01T00:00:05+00:00", "event": "oow_prompt_input", "cycle_id": 2,
                 "model": "test", "system_prompt": "sys", "user_prompt": "two", "constraints": {}},
                {"timestamp": "2026-01-01T00:00:08+00:00", "event": "oow_decision", "cycle_id": 2},
                {"event": "cycle_performance", "cycle_id": 1, "oow_duration_s": 1.9},
                {"event": "cycle_performance", "cycle_id": 2, "oow_duration_s": 2.9},
            ]
            (case / "audit.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            result = campaign_metrics(run, {})
        self.assertEqual(result["inter_decision_seconds"]["mean"], 6)
        self.assertEqual(result["prompt_to_decision_seconds"]["mean"], 2.5)
        self.assertEqual(result["logged_oow_duration_seconds"]["mean"], 2.4)
        self.assertEqual(result["prompts"], 2)

    def test_negative_reduction_reports_prompt_growth(self):
        metric = lambda value: {"mean": value, "median": value}
        candidate = {
            "prompt_size": {key: metric(120) for key in ("characters", "utf8_bytes", "words", "estimated_tokens")},
            "system_prompt_characters": metric(120),
            "prompt_to_decision_seconds": metric(8),
        }
        baseline = {
            "prompt_size": {key: metric(100) for key in ("characters", "utf8_bytes", "words", "estimated_tokens")},
            "system_prompt_characters": metric(100),
            "prompt_to_decision_seconds": metric(10),
        }
        result = comparison(candidate, {"base": baseline})["base"]
        self.assertEqual(result["prompt_reduction_percent"]["characters"]["mean"], -20)
        self.assertEqual(result["decision_latency_reduction_percent"]["mean"], 20)


if __name__ == "__main__":
    unittest.main()
