import json
import tempfile
import unittest
from pathlib import Path

from tools.evaluate_colregs_citations import (
    evaluate_audit,
    extract_rule_citations,
    summarize,
)


class ColregsCitationEvaluationTests(unittest.TestCase):
    def test_clear_water_cycle_still_expects_persistent_rules(self):
        rows = [
            {"event": "oow_colregs_oracle", "cycle_id": 1,
             "classification": {"primary_contact": None, "contacts": []}},
            {"event": "oow_decision", "cycle_id": 1,
             "decision": {"reason": "Rules 2, 5, 6 and 7."}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            record = evaluate_audit(path)[0]

        self.assertEqual(record["expected_rules"], [2, 5, 6, 7])
        self.assertEqual(record["f1"], 1.0)

    def test_extracts_only_explicit_rule_clauses(self):
        reason = "CPA is 15 m. Rules 7, 8, and 16 apply; Rule 17(a)(ii) does not."
        self.assertEqual(extract_rule_citations(reason), {7, 8, 16, 17})
        self.assertEqual(extract_rule_citations("CPA 15, TCPA 60."), set())

    def test_compares_decision_with_primary_eriksen_contact(self):
        rows = [
            {
                "event": "oow_colregs_oracle", "cycle_id": 3,
                "classification": {
                    "primary_contact": "TS2",
                    "contacts": [
                        {"contact": "TS1", "state": "SF", "applicable_rules": [2, 5, 6, 7]},
                        {"contact": "TS2", "state": "GW", "applicable_rules": [2, 5, 6, 7, 8, 15, 16]},
                    ],
                },
            },
            {
                "event": "oow_decision", "cycle_id": 3,
                "decision": {"action": "alter_course", "reason": "Rules 7, 8, 15, and 13."},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            record = evaluate_audit(path, case=4)[0]

        self.assertEqual(record["expected_rules"], [2, 5, 6, 7, 8, 15, 16])
        self.assertEqual(record["correct_rules"], [7, 8, 15])
        self.assertEqual(record["missing_rules"], [2, 5, 6, 16])
        self.assertEqual(record["unsupported_rules"], [13])
        self.assertFalse(record["exact_match"])

    def test_summary_reports_set_quality_separately_from_presence(self):
        records = [{
            "status": "evaluated", "citation_present": True, "exact_match": False,
            "expected_rules": [2, 5, 6, 7], "cited_rules": [13, 16],
            "correct_rules": [], "missing_rules": [2, 5, 6, 7],
            "unsupported_rules": [13, 16], "precision": 0.0, "recall": 0.0, "f1": 0.0,
        }]
        result = summarize(records)
        self.assertEqual(result["citation_presence_rate"], 1.0)
        self.assertEqual(result["exact_match_rate"], 0.0)
        self.assertEqual(result["micro_f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
