import argparse
import json
from pathlib import Path
import tempfile
import unittest

from tools.curate_maib_lesson_cards import (
    CardAssessment,
    audit,
    disposition,
    normalized_pages,
    validate,
)


class CurateMaibLessonCardsTests(unittest.TestCase):
    def assessment(self, **overrides):
        values = {
            "card_id": "card-1",
            "decision_relevance": "direct",
            "grounding_quality": "strong",
            "transfer_quality": "safe",
            "duplicate_of": None,
            "reason": "The quoted finding directly informs current collision-risk appraisal.",
        }
        values.update(overrides)
        return CardAssessment.model_validate(values)

    def test_only_direct_strong_safe_unique_card_is_retained(self):
        self.assertEqual(disposition(self.assessment()), "retain")
        self.assertEqual(
            disposition(self.assessment(decision_relevance="adjacent")), "review"
        )
        self.assertEqual(
            disposition(self.assessment(grounding_quality="partial")), "review"
        )
        self.assertEqual(
            disposition(self.assessment(transfer_quality="review")), "review"
        )

    def test_out_of_scope_weak_rejected_or_duplicate_is_excluded(self):
        self.assertEqual(
            disposition(self.assessment(decision_relevance="out_of_scope")),
            "exclude",
        )
        self.assertEqual(
            disposition(self.assessment(grounding_quality="weak")), "exclude"
        )
        self.assertEqual(
            disposition(self.assessment(transfer_quality="reject")), "exclude"
        )
        self.assertEqual(
            disposition(self.assessment(duplicate_of="card-0")), "exclude"
        )

    def test_page_normalization_retains_only_quoted_pages(self):
        card = {
            "pages": [3, 4, 5],
            "supporting_quotes": [
                {"page": 3, "quote": "first quotation"},
                {"page": 5, "quote": "second quotation"},
            ],
        }
        cleaned, removed = normalized_pages(card)
        self.assertEqual(cleaned["pages"], [3, 5])
        self.assertEqual(removed, [4])
        self.assertEqual(card["pages"], [3, 4, 5])

    def test_validation_partitions_and_audits_complete_response(self):
        card = {
            "card_id": "card-1",
            "source_file": "accidents/example.pdf",
            "pages": [3, 4],
            "supporting_quotes": [{"page": 3, "quote": "supported quotation"}],
        }
        assessment = self.assessment().model_dump()
        output = {
            "custom_id": "maib-curate-example",
            "response": {
                "body": {
                    "choices": [{
                        "message": {
                            "content": json.dumps({"assessments": [assessment]})
                        }
                    }]
                }
            },
        }
        manifest = {
            "custom_id": "maib-curate-example",
            "source_file": "accidents/example.pdf",
            "cards": [card],
        }
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            (work_dir / "manifest.jsonl").write_text(json.dumps(manifest) + "\n")
            (work_dir / "batch_output.jsonl").write_text(json.dumps(output) + "\n")
            args = argparse.Namespace(work_dir=work_dir)
            validate(args)
            audit(args)
            retained = json.loads((work_dir / "cards.retained.jsonl").read_text())
            self.assertEqual(retained["pages"], [3])
            result = json.loads((work_dir / "audit.json").read_text())
            self.assertTrue(result["audit_passed"])


if __name__ == "__main__":
    unittest.main()
