import unittest
from pathlib import Path

from experiments.qwen8b_colregs_rag.retrieval import ColregsRetriever, navigation_queries


ROOT = Path(__file__).resolve().parents[1]


class ColregsRagTests(unittest.TestCase):
    def test_navigation_queries_are_compact_and_per_contact(self):
        narrative = """CURRENT BRIDGE STATE

LLM_SHIP is underway, heading 90.0 degrees, speed 5.83 knots.

Own-ship vessel type is power driven.

Tracked contacts:

- Contact ALPHA (power driven): range 0.237 nautical miles, starboard bow (relative bearing 31.0 degrees). Heading 180.0 degrees, speed 4.00 knots, closing speed 2.00 knots. Projected CPA is 0.023 nautical miles with TCPA 96.0 seconds.
- Contact BRAVO (power driven): range 0.500 nautical miles, port bow (relative bearing -40.0 degrees). No future CPA is projected under the current motion.

OPERATING CONDITIONS

Visibility is clear; narrow-channel context is not indicated.

RECENT TRAFFIC DEVELOPMENTS (last 10.0 seconds)

- ALPHA relative bearing changed from 30.0 to 31.0 degrees; relative bearing increased by 1.0 degrees.
"""
        queries = navigation_queries(narrative)
        self.assertEqual(len(queries), 2)
        self.assertIn("range: 439 m", queries[0])
        self.assertIn("CPA: 43 m", queries[0])
        self.assertIn("TCPA: 96 s", queries[0])
        self.assertIn("bearing nearly steady", queries[0])
        self.assertIn("port bow", queries[1])
        self.assertNotIn("NAVIGATION SCALE", queries[0])

    def test_structured_state_preserves_latched_overtaking_encounter(self):
        narrative = """CURRENT BRIDGE STATE

OWN is underway, heading 0.0 degrees, speed 10.0 knots.

Own-ship vessel type is power driven.

Tracked contacts:

- Contact TARGET (power driven): range 0.500 nautical miles, starboard bow (relative bearing 40.0 degrees). Heading 0.0 degrees, speed 5.0 knots, closing speed -1.0 knots. No future CPA is projected under the current motion.

OPERATING CONDITIONS

Visibility is clear.
"""
        state = {"contacts": [{
            "name": "TARGET",
            "encounter_type": "we_are_overtaking_contact",
            "relative_bearing_deg": 40.0,
        }]}
        query = navigation_queries(narrative, state)[0]
        self.assertIn("established overtaking encounter remains active", query)
        self.assertIn("own ship give-way vessel", query)
        self.assertNotIn("diverging encounter", query)

    def test_temporal_contact_event_is_not_a_contact_query(self):
        narrative = """OWN is underway, heading 0.0 degrees.
Own-ship vessel type is power driven.
Tracked contacts:
- Contact TARGET (power driven): range 1.000 nautical miles, ahead (relative bearing 0.0 degrees).
RECENT TRAFFIC DEVELOPMENTS
- Contact TARGET appeared.
OPERATING CONDITIONS
Visibility is clear.
"""
        queries = navigation_queries(narrative)
        self.assertEqual(len(queries), 1)


    def test_crossing_query_retrieves_crossing_rules(self):
        retriever = ColregsRetriever(
            ROOT / "agents/colregs/colregs.json", candidate_k=12, top_k=6
        )
        _, selected = retriever.retrieve(
            "two power driven vessels crossing collision risk contact on starboard side keep out of way"
        )
        rules = {item.rule for item in selected}
        self.assertIn(15, rules)
        self.assertIn(16, rules)

    def test_starboard_bow_query_prioritizes_crossing_rules(self):
        retriever = ColregsRetriever(
            ROOT / "agents/colregs/colregs.json", candidate_k=12, top_k=6
        )
        _, selected = retriever.retrieve(
            "power driven contact on starboard bow CPA TCPA clear visibility"
        )
        rules = {item.rule for item in selected}
        self.assertIn(15, rules)
        self.assertIn(16, rules)
        self.assertNotIn(22, rules)
        self.assertNotIn(35, rules)

    def test_candidate_trace_retains_unselected_documents(self):
        retriever = ColregsRetriever(
            ROOT / "agents/colregs/colregs.json", candidate_k=10, top_k=3
        )
        candidates, selected = retriever.retrieve("head on power driven vessels")
        self.assertEqual(len(candidates), 10)
        self.assertEqual(len(selected), 3)
        self.assertEqual(sum(item.selected for item in candidates), 3)

    def test_prompt_context_uses_guidance_after_authoritative_retrieval(self):
        retriever = ColregsRetriever(
            ROOT / "agents/colregs/colregs.json", candidate_k=12, top_k=6
        )
        _, selected = retriever.retrieve(
            "crossing encounter own ship give-way vessel collision risk"
        )
        context = retriever.prompt_context(selected)
        self.assertIn("Rule 15 — Crossing Situation", context)
        self.assertIn("OPERATIONAL SUMMARY", context)
        self.assertIn("avoid passing in front", context.lower())
        self.assertNotIn("When two power-driven vessels are crossing", context)


if __name__ == "__main__":
    unittest.main()
