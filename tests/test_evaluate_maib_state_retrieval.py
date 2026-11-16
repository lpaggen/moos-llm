import csv
import math
from pathlib import Path
import tempfile
import unittest

from tools.evaluate_maib_state_retrieval import (
    METHODS,
    anonymized_pool,
    calculate_metrics,
    dcg,
    markdown_results,
    paired_differences,
    read_labels,
    select_states,
    write_label_sheet,
)


class StateRetrievalEvaluationTests(unittest.TestCase):
    def test_archived_selection_reconstructs_all_22_runtime_queries(self):
        root = Path(
            "logs/evaluations/imazu/"
            "maib_lesson_cards_captain_review_prompt_fixed_full_warp5"
        )
        if not root.exists():
            self.skipTest("archived Imazu campaign is not present")
        states = select_states(root)
        self.assertEqual(len(states), 22)
        self.assertEqual(
            [state["scenario_id"] for state in states],
            [f"imazu-{case:02d}" for case in range(1, 23)],
        )
        self.assertTrue(all(state["relevant_contacts"] for state in states))
        self.assertTrue(
            all("The OOW proposal under review" in state["query"] for state in states)
        )

    def test_pool_is_deduplicated_and_method_blind(self):
        states = [
            {
                "scenario_id": "imazu-01",
                "query": "state one",
                "metadata_preferences": {},
            }
        ]
        cards = [
            {
                "card_id": card_id,
                "investigator_finding": f"finding {card_id}",
                "transferable_lesson": f"lesson {card_id}",
                "non_transfer_conditions": ["condition"],
                "supporting_quotes": [{"page": 1, "quote": "quotation"}],
            }
            for card_id in ("a", "b", "c")
        ]
        rankings = [
            {
                "scenario_id": "imazu-01",
                "methods": {
                    "bm25": [
                        {"rank": 1, "card_id": "a"},
                        {"rank": 2, "card_id": "b"},
                    ],
                    "bge_dense": [
                        {"rank": 1, "card_id": "a"},
                        {"rank": 2, "card_id": "c"},
                    ],
                    "rrf": [
                        {"rank": 1, "card_id": "b"},
                        {"rank": 2, "card_id": "c"},
                    ],
                    "rrf_preferences": [
                        {"rank": 1, "card_id": "a"},
                        {"rank": 2, "card_id": "b"},
                    ],
                },
            }
        ]
        sheet, key = anonymized_pool(states, rankings, cards, seed=7)
        repeated_sheet, repeated_key = anonymized_pool(
            states, rankings, cards, seed=7
        )
        self.assertEqual(sheet, repeated_sheet)
        self.assertEqual(key, repeated_key)
        self.assertEqual(len(sheet), 3)
        self.assertEqual(len(key), 3)
        for row in sheet:
            self.assertNotIn("method", row)
            self.assertNotIn("rank", row)
            self.assertNotIn("card_id", row)
            self.assertNotIn("scenario_id", row)
            self.assertEqual(row["relevance_label"], "")
        self.assertEqual(
            {(row["scenario_id"], row["card_id"]) for row in key},
            {("imazu-01", "a"), ("imazu-01", "b"), ("imazu-01", "c")},
        )

    def test_metrics_use_binary_relevance_for_precision_and_graded_ndcg(self):
        rankings = []
        key = []
        labels = {}
        for case in range(1, 23):
            scenario = f"imazu-{case:02d}"
            rankings.append(
                {
                    "scenario_id": scenario,
                    "methods": {
                        "bm25": [
                            {"rank": 1, "card_id": "a"},
                            {"rank": 2, "card_id": "c"},
                        ],
                        "bge_dense": [
                            {"rank": 1, "card_id": "b"},
                            {"rank": 2, "card_id": "c"},
                        ],
                        "rrf": [
                            {"rank": 1, "card_id": "a"},
                            {"rank": 2, "card_id": "b"},
                        ],
                        "rrf_preferences": [
                            {"rank": 1, "card_id": "b"},
                            {"rank": 2, "card_id": "a"},
                        ],
                    },
                }
            )
            for card_id, label in (("a", 2), ("b", 1), ("c", 0)):
                annotation = f"{case:02d}-{card_id}"
                key.append(
                    {
                        "annotation_id": annotation,
                        "scenario_id": scenario,
                        "card_id": card_id,
                    }
                )
                labels[annotation] = label

        summary, per_state = calculate_metrics(rankings, key, labels)
        by_method = {row["method"]: row for row in summary}
        self.assertEqual(len(per_state), 22 * len(METHODS))
        self.assertEqual(by_method["bm25"]["precision_at_2"], 0.5)
        self.assertEqual(by_method["bm25"]["hit_at_2"], 1.0)
        self.assertEqual(by_method["bge_dense"]["precision_at_2"], 0.0)
        self.assertEqual(by_method["bge_dense"]["hit_at_2"], 0.0)
        self.assertEqual(by_method["rrf"]["ndcg_at_2"], 1.0)
        expected_reversed = dcg([1, 2]) / dcg([2, 1])
        self.assertTrue(
            math.isclose(
                by_method["rrf_preferences"]["ndcg_at_2"],
                expected_reversed,
            )
        )
        paired = paired_differences(per_state)
        dense_minus_bm25 = next(
            row
            for row in paired
            if row["method_a"] == "bm25" and row["method_b"] == "bge_dense"
        )
        self.assertEqual(dense_minus_bm25["mean_delta_precision_at_2"], -0.5)
        self.assertEqual(dense_minus_bm25["mean_delta_hit_at_2"], -1.0)
        cards = [
            {"card_id": card_id, "investigator_finding": f"finding {card_id}"}
            for card_id in ("a", "b", "c")
        ]
        rendered = markdown_results(
            summary, per_state, paired, rankings, cards, key, labels
        )
        self.assertIn("## Paired method differences", rendered)
        self.assertIn("## Per-scenario results", rendered)
        self.assertIn("## Representative successes and failures", rendered)

    def test_label_reader_rejects_incomplete_human_labels(self):
        rows = [
            {
                "annotation_id": "P001",
                "state_id": "S001",
                "navigation_state": "state",
                "card_finding": "finding",
                "transferable_lesson": "lesson",
                "non_transfer_conditions": "[]",
                "supporting_quotations": "[]",
                "relevance_label": "",
                "reviewer_notes": "",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.csv"
            write_label_sheet(path, rows)
            with self.assertRaisesRegex(ValueError, "must be exactly 0, 1, or 2"):
                read_labels(path)
            rows[0]["relevance_label"] = "2"
            write_label_sheet(path, rows)
            self.assertEqual(read_labels(path), {"P001": 2})


if __name__ == "__main__":
    unittest.main()
