import unittest
from pathlib import Path

from tools.test_maib_decision_style_extraction import (
    DEFAULT_AUDIT_ROOT,
    DEFAULT_SAMPLES,
    ExtractionResponse,
    batch_record,
    collect_material,
    prompt_payload,
    read_index,
    validate_response,
)


class DecisionStyleExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples, cls.chunks = collect_material(
            DEFAULT_AUDIT_ROOT, list(DEFAULT_SAMPLES)
        )

    def test_recent_audits_supply_five_examples_and_four_unique_chunks(self):
        self.assertEqual(len(self.examples), 5)
        self.assertEqual(len(self.chunks), 4)
        self.assertTrue(all(x["rendered_narrative"].startswith("CURRENT BRIDGE STATE") for x in self.examples))

    def test_prompt_defines_style_not_exact_state_applicability(self):
        payload = prompt_payload(self.examples, self.chunks[0])
        self.assertEqual(len(payload["target_decision_input_examples"]), 5)
        self.assertIn("compatible future state", payload["task"])
        self.assertIn("Do not require applicability to a particular example", payload["task"])

    def test_all_chunk_index_is_complete_and_batch_ids_are_unique(self):
        chunks = read_index(Path("accidents/index.jsonl"))
        self.assertEqual(len(chunks), 2373)
        records = [batch_record(chunk, {}) for chunk in chunks]
        self.assertEqual(len({item["custom_id"] for item in records}), 2373)

    def test_background_passage_can_be_rejected_without_cards(self):
        chunk = next(x for x in self.chunks if x["chunk_id"] == "maib-2891026537f2")
        response = ExtractionResponse.model_validate({
            "chunk_id": chunk["chunk_id"],
            "source_passage_type": "background_or_method",
            "source_contains_investigator_reasoning": False,
            "contains_extractable_lessons": False,
            "confidence": 0.99,
            "source_assessment": "The passage explains a submarine sensing method without stating an investigator finding or causal conclusion.",
            "lessons": [],
        })
        self.assertEqual(validate_response(response, chunk), [])

    def test_analysis_passage_can_yield_future_state_lesson(self):
        chunk = next(x for x in self.chunks if x["chunk_id"] == "maib-14a8779cc4e3")
        response = ExtractionResponse.model_validate({
            "chunk_id": chunk["chunk_id"],
            "source_passage_type": "investigator_analysis",
            "source_contains_investigator_reasoning": True,
            "contains_extractable_lessons": True,
            "confidence": 0.95,
            "source_assessment": "The passage causally connects underestimated contact speed with an overstated action window.",
            "lessons": [{
                "card": {
                    "pages": [7],
                    "encounter_types": ["unknown"],
                    "roles": ["uncertain"],
                    "encounter_phases": ["risk_developing", "action_required"],
                    "warning_signs": ["The available action window depends on an uncertain or unchecked contact-speed estimate."],
                    "causal_mechanisms": ["inadequate_cpa_assessment", "delayed_action"],
                    "investigator_finding": "The submarine OOW underestimated the ferry's speed and consequently assessed that more time remained for avoiding action than was actually available.",
                    "transferable_lesson": "Check the contact-speed and closing-time basis before relying on the apparent time remaining for collision avoidance.",
                    "non_transfer_conditions": ["The source does not establish a manoeuvre or surface-vessel COLREG role."],
                    "supporting_quotes": [{
                        "page": 7,
                        "quote": "the OOW almost certainly assessed that there was significantly more time to take avoiding action than was actually the case.",
                    }],
                },
                "relevant_input_features": ["range_or_closing_speed", "cpa_tcpa", "track_quality_or_uncertainty"],
                "potential_decision_use": "Use the lesson when assessing whether the displayed time to closest approach leaves a reliable action window.",
                "required_live_evidence": ["An explicit reason to doubt or compare the contact-speed or closing-time estimate."],
                "prohibited_inferences": ["Do not infer that a displayed TCPA is wrong merely because the source case contained an estimation error."],
            }],
        })
        self.assertEqual(validate_response(response, chunk), [])


if __name__ == "__main__":
    unittest.main()
