import json
import unittest

from tools.test_maib_chunk_applicability import (
    ApplicabilityResponse,
    DEFAULT_AUDIT_ROOT,
    DEFAULT_SAMPLES,
    build_pairs,
    canonical_exact_quote,
    request_body,
    validate_applicability,
)


class MaibChunkApplicabilityTests(unittest.TestCase):
    def test_outer_quote_marks_are_removed_only_when_inner_text_is_exact(self):
        source = "Visibility is clear. Projected CPA is 0 metres."

        self.assertEqual(
            canonical_exact_quote('"Visibility is clear."', source),
            "Visibility is clear.",
        )
        self.assertEqual(
            canonical_exact_quote("“Projected CPA is 0 metres.”", source),
            "Projected CPA is 0 metres.",
        )
        self.assertEqual(
            canonical_exact_quote('"Invented evidence"', source),
            '"Invented evidence"',
        )

    def test_default_samples_are_exact_recent_audit_pairs(self):
        pairs = build_pairs(DEFAULT_AUDIT_ROOT, list(DEFAULT_SAMPLES))

        self.assertEqual(len(pairs), 10)
        self.assertEqual(
            {(p["audit_reference"]["case"], p["audit_reference"]["cycle_id"]) for p in pairs},
            set(DEFAULT_SAMPLES),
        )
        self.assertTrue(all(p["narrative"].startswith("CURRENT BRIDGE STATE") for p in pairs))
        self.assertTrue(all(p["source_chunk"]["text"] for p in pairs))
        self.assertTrue(all(p["source_chunk"]["page"] > 0 for p in pairs))


    def test_api_prompt_does_not_reveal_prior_captain_evidence_verdict(self):
        pair = build_pairs(DEFAULT_AUDIT_ROOT, [("case_01", 44)], max_pairs=1)[0]
        body = request_body(pair, "gpt-6.1-sol", "low", 5000)
        sent = body["messages"][1]["content"]

        self.assertNotIn("prior_audit_outcome", sent)
        self.assertNotIn("evidence_assessment", sent)
        sent_payload = json.loads(sent)
        self.assertEqual(
            sent_payload["live_audit_state"]["rendered_narrative"], pair["narrative"]
        )
        self.assertEqual(sent_payload["source_chunk"]["text"], pair["source_chunk"]["text"])
        self.assertEqual(
            sent_payload["live_audit_state"]["oow_proposal_under_review"],
            pair["oow_proposal"],
        )


    def test_non_meaningful_response_requires_no_card_or_prompt_use(self):
        pair = build_pairs(DEFAULT_AUDIT_ROOT, [("case_01", 44)], max_pairs=1)[0]
        response = ApplicabilityResponse.model_validate({
        "chunk_id": pair["source_chunk"]["chunk_id"],
        "source_passage_type": "background_or_method",
        "source_contains_investigator_reasoning": False,
        "can_be_used_meaningfully": False,
        "confidence": 0.98,
        "source_assessment": "The passage explains submarine target-motion analysis rather than an investigator finding transferable to the live surface encounter.",
        "narrative_evidence": [],
        "analogous_facts": [],
        "material_differences": ["The live state supplies direct surface-vessel tracking, not passive submarine sonar ranging."],
        "decision_usefulness": "none",
        "decision_impact": "none",
        "use_in_decision_prompt": False,
        "lesson_card": None,
        })

        self.assertEqual(validate_applicability(response, pair), [])


    def test_meaningful_response_requires_verbatim_live_and_source_evidence(self):
        pairs = build_pairs(DEFAULT_AUDIT_ROOT, [("case_01", 44)])
        pair = next(p for p in pairs if p["source_chunk"]["chunk_id"] == "maib-14a8779cc4e3")
        response = ApplicabilityResponse.model_validate({
        "chunk_id": "maib-14a8779cc4e3",
        "source_passage_type": "investigator_analysis",
        "source_contains_investigator_reasoning": True,
        "can_be_used_meaningfully": True,
        "confidence": 0.86,
        "source_assessment": "The passage explicitly analyzes how an underestimated closing speed overstated the time available for avoiding action.",
        "narrative_evidence": ["Projected CPA is 0 metres with TCPA 480.3 seconds."],
        "analogous_facts": ["The live vessels are closing head-on with a projected zero-metre CPA."],
        "material_differences": ["The source involved a surfaced submarine and uncertainty in its estimate of ferry speed; the live track reports measured speed and TCPA."],
        "decision_usefulness": "supporting",
        "decision_impact": "It supports checking the time available from measured closing motion, but does not choose the live manoeuvre.",
        "use_in_decision_prompt": True,
        "lesson_card": {
            "pages": [7],
            "encounter_types": ["head_on"],
            "roles": ["uncertain"],
            "encounter_phases": ["risk_developing", "action_required"],
            "warning_signs": ["Estimated contact speed materially understates the true closing rate."],
            "causal_mechanisms": ["inadequate_cpa_assessment", "delayed_action"],
            "investigator_finding": "The submarine OOW underestimated the ferry's speed and therefore assessed that substantially more time remained for avoiding action than was actually available.",
            "transferable_lesson": "Base the available reaction time on a checked closing-rate assessment rather than an underestimated contact speed.",
            "non_transfer_conditions": ["The source does not determine the correct manoeuvre or COLREG role in a surface-vessel encounter."],
            "supporting_quotes": [{
                "page": 7,
                "quote": "the OOW almost certainly assessed that there was significantly more time to take avoiding action than was actually the case.",
            }],
        },
        })

        self.assertEqual(validate_applicability(response, pair), [])

        response.narrative_evidence = ["Invented live observation"]
        errors = validate_applicability(response, pair)
        self.assertTrue(any("narrative quotation is not verbatim" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
