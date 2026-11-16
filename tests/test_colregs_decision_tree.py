import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.agent_oow import AgentOOW
from experiments.colregs_decision_tree.selection import DecisionTreeGuidance


def state(encounter="crossing", bearing=45, risk="medium"):
    return {
        "moos_time": 10, "vessel_type": "ship",
        "nav": {"x": 0, "y": 0, "heading": 0, "speed": 6.173},
        "contacts": [{
            "name": "TS1", "vessel_type": "ship", "age_s": 0,
            "nav": {"x": 500, "y": 500, "heading": 270, "speed": 6.173},
            "range": 700, "relative_bearing": bearing,
            "encounter_type": encounter, "risk": risk,
        }],
    }


class DecisionTreeTests(unittest.TestCase):
    def test_selects_encounter_duties_and_preserves_existing_summaries(self):
        provider = DecisionTreeGuidance()
        cases = [
            ("head_on", 0, {8, 14}),
            ("crossing", 45, {8, 15, 16}),
            ("crossing", 315, {8, 15, 17}),
            ("we_are_overtaking_contact", 0, {8, 13, 16}),
        ]
        for encounter, bearing, expected in cases:
            with self.subTest(encounter=encounter, bearing=bearing):
                selected = provider.select(state(encounter, bearing), stale_after_s=5)
                self.assertEqual(set(selected["applicable_rules"]), {2, 5, 6, 7, 11} | expected)
                for item in selected["selected"]:
                    self.assertEqual(item["ship_guidance"], list(provider.guidance[item["rule"]]))

    def test_stale_and_no_risk_contacts_do_not_add_crossing_rules(self):
        provider = DecisionTreeGuidance()
        low = provider.select(state(risk="low"), stale_after_s=5)
        self.assertNotIn(15, low["applicable_rules"])
        stale = state()
        stale["contacts"][0]["age_s"] = 20
        selected = provider.select(stale, stale_after_s=5)
        self.assertEqual(selected["applicable_rules"], [2, 5, 6, 7])
        self.assertEqual(selected["classification"]["skipped_contacts"][0]["reason"], "stale")

    def test_runtime_uses_tree_without_constructing_retriever(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "action": "alter_course", "degrees": "large_starboard", "speed": "none",
                "reason": "Rule 15: pass behind.", "auxiliary_commands": [],
            })))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.dict("os.environ", {"MOOS_LLM_COLREG_TREE": "on", "MOOS_LLM_COLREG_RAG": "off"}), patch(
            "agents.agent_oow.ColregsRetriever", side_effect=AssertionError("RAG constructed")
        ):
            oow = AgentOOW(client=client, model="test", stale_age_s=5)
            transformed = oow.deterministic_input(state())
            decision = oow.decide(transformed)
        self.assertIsNone(oow.colregs_retriever)
        self.assertEqual(decision.degrees, 45)
        self.assertEqual(oow.last_colregs_retrieval["selector"], "deterministic_colregs_tree")
        self.assertIn(15, oow.last_colregs_retrieval["applicable_rules"])
        prompt = captured["messages"][1]["content"]
        self.assertIn("DECISION-TREE COLREG GUIDANCE", prompt)
        self.assertIn("Avoid passing in front of it.", prompt)
        self.assertNotIn("maneuver_guidance", prompt)
        self.assertNotIn("30-60 degrees is available", prompt)
        schema = captured["response_format"]["json_schema"]["schema"]
        self.assertIn("large_starboard", schema["properties"]["degrees"]["enum"])

    def test_tree_and_rag_cannot_be_enabled_together(self):
        with patch.dict("os.environ", {"MOOS_LLM_COLREG_TREE": "on", "MOOS_LLM_COLREG_RAG": "on"}):
            with self.assertRaisesRegex(ValueError, "not both"):
                AgentOOW(client=SimpleNamespace())
