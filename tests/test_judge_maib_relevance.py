from pathlib import Path
import unittest

from tools.judge_maib_relevance import (
    DEFAULT_LABELS,
    JudgmentResponse,
    build_requests,
    validate_prepared,
    write_jsonl,
)


class ModelRelevanceJudgeTests(unittest.TestCase):
    def test_preparation_is_complete_and_method_blind(self):
        if not DEFAULT_LABELS.exists():
            self.skipTest("benchmark label sheet not present")
        requests, manifests, input_tokens = build_requests(
            DEFAULT_LABELS, "gpt-6.1-sol", 5000, "low"
        )
        self.assertEqual(len(requests), 22)
        self.assertEqual(sum(len(item["annotation_ids"]) for item in manifests), 137)
        self.assertGreater(input_tokens, 0)
        serialized = str(requests).lower()
        self.assertNotIn("method_ranks", serialized)
        self.assertNotIn("rrf_preferences", serialized)
        self.assertTrue(
            all(
                request["body"]["response_format"]["json_schema"]["strict"]
                for request in requests
            )
        )

    def test_response_schema_rejects_invalid_label_and_extra_fields(self):
        valid = {
            "assessments": [
                {
                    "annotation_id": "P001",
                    "relevance_label": 2,
                    "reason": "The observed state explicitly contains the mechanism.",
                }
            ]
        }
        JudgmentResponse.model_validate(valid)
        invalid = {"assessments": [{**valid["assessments"][0], "relevance_label": 3}]}
        with self.assertRaises(Exception):
            JudgmentResponse.model_validate(invalid)
        extra = {"assessments": [{**valid["assessments"][0], "method": "bm25"}]}
        with self.assertRaises(Exception):
            JudgmentResponse.model_validate(extra)


if __name__ == "__main__":
    unittest.main()
