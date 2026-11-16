import unittest

from agents.agent_colregs import AgentColregs
from agents.colregs import classify_colregs
from agents.colregs.repository import ColregsRepository
from agents.schemas.colregs import ColregsVerdict


class ColregsClassifierTests(unittest.TestCase):
    def classify(self, contact, **overrides):
        options = {
            "visibility_range_m": 10000.0,
            "visibility_condition": "clear",
        }
        options.update(overrides)
        return classify_colregs(
            {"vessel_type": "ship", "contacts": [contact]},
            **options,
        )

    def test_head_on_power_vessels_selects_rule_14(self):
        result = self.classify({
            "name": "TARGET",
            "vessel_type": "ship",
            "range": 500,
            "encounter_type": "head_on",
            "relative_bearing": 0,
            "risk": "medium",
        })
        self.assertEqual(result["encounters"][0]["own_role"], "both_give_way")
        self.assertIn(14, result["applicable_rules"])
        self.assertIn(
            "starboard",
            result["encounters"][0]["maneuver_guidance"],
        )

    def test_crossing_contact_on_starboard_makes_own_ship_give_way(self):
        result = self.classify({
            "name": "TARGET",
            "vessel_type": "ship",
            "range": 500,
            "encounter_type": "crossing",
            "relative_bearing": 45,
            "risk": "medium",
        })
        self.assertEqual(result["encounters"][0]["own_role"], "give_way")
        self.assertIn(16, result["applicable_rules"])
        self.assertNotIn(17, result["applicable_rules"])
        guidance = result["encounters"][0]["maneuver_guidance"]
        self.assertIn("starboard alteration", guidance)
        self.assertIn("pass astern", guidance)
        self.assertIn("avoid crossing ahead", guidance)
        self.assertIn("30-60 degrees", guidance)
        self.assertIn("repeated 15-degree legs", guidance)
        self.assertIn("Do not turn port", guidance)
        self.assertIn("avoid crossing ahead", result["rule_guidance"]["15"])
        self.assertIn("as large as 60 degrees", result["rule_guidance"]["8"])

    def test_crossing_contact_on_port_makes_own_ship_stand_on(self):
        result = self.classify({
            "name": "TARGET",
            "vessel_type": "ship",
            "range": 500,
            "encounter_type": "crossing",
            "relative_bearing": 315,
            "risk": "medium",
        })
        self.assertEqual(result["encounters"][0]["own_role"], "stand_on")
        self.assertIn(17, result["applicable_rules"])
        self.assertIn(
            "Stand on initially",
            result["encounters"][0]["maneuver_guidance"],
        )

    def test_contact_outside_simulated_visibility_does_not_select_section_two(self):
        result = self.classify({
            "name": "TARGET",
            "vessel_type": "ship",
            "range": 12000,
            "encounter_type": "head_on",
            "risk": "medium",
        })
        self.assertNotIn(11, result["applicable_rules"])
        self.assertNotIn(14, result["applicable_rules"])

    def test_restricted_visibility_selects_rule_19(self):
        result = self.classify(
            {"name": "TARGET", "vessel_type": "ship", "range": 15000},
            visibility_condition="restricted",
        )
        self.assertIn(19, result["applicable_rules"])

    def test_close_contact_can_be_in_sight_in_restricted_visibility(self):
        result = self.classify(
            {
                "name": "TARGET",
                "vessel_type": "ship",
                "range": 500,
                "encounter_type": "head_on",
                "risk": "medium",
            },
            visibility_condition="restricted",
        )
        self.assertIn(11, result["applicable_rules"])
        self.assertIn(14, result["applicable_rules"])
        self.assertNotIn(19, result["encounters"][0]["applicable_rules"])

    def test_low_risk_crossing_geometry_has_no_active_crossing_duties(self):
        result = self.classify({
            "name": "TARGET",
            "vessel_type": "ship",
            "range": 500,
            "encounter_type": "crossing",
            "relative_bearing": 315,
            "risk": "low",
        })
        encounter = result["encounters"][0]
        self.assertEqual(encounter["own_role"], "not_applicable")
        self.assertEqual(encounter["standing_rules"], [2, 5, 6, 7, 11])
        self.assertEqual(encounter["active_encounter_rules"], [])
        self.assertNotIn(15, result["applicable_rules"])
        self.assertNotIn(17, result["applicable_rules"])

    def test_own_ship_overtaking_selects_rules_13_and_16(self):
        result = self.classify({
            "name": "TARGET",
            "vessel_type": "ship",
            "range": 500,
            "encounter_type": "we_are_overtaking_contact",
            "relative_bearing": 10,
            "risk": "low",
        })
        encounter = result["encounters"][0]
        self.assertEqual(encounter["own_role"], "give_way")
        self.assertEqual(encounter["active_encounter_rules"], [13, 16])
        self.assertNotIn(8, encounter["standing_rules"])

    def test_unknown_type_is_reported(self):
        result = classify_colregs(
            {"contacts": [{"name": "TARGET", "range": 500}]},
            visibility_range_m=10000,
        )
        self.assertIn("own_ship.vessel_type", result["missing_facts"])
        self.assertIn("contacts.TARGET.vessel_type", result["missing_facts"])

    def test_stale_contact_is_recorded_but_does_not_select_encounter_rule(self):
        result = classify_colregs(
            {
                "vessel_type": "ship",
                "contacts": [{
                    "name": "TARGET",
                    "vessel_type": "ship",
                    "range": 100,
                    "age_s": 12,
                    "encounter_type": "head_on",
                }],
            },
            visibility_range_m=10000,
            stale_after_s=5,
        )
        self.assertNotIn(14, result["applicable_rules"])
        self.assertEqual(result["skipped_contacts"][0]["reason"], "stale")
        self.assertIn("contacts.TARGET.age_s", result["missing_facts"])

    def test_head_on_rule_is_not_applied_to_sailing_vessels(self):
        result = classify_colregs(
            {
                "vessel_type": "sailing",
                "contacts": [{
                    "name": "TARGET",
                    "vessel_type": "sailing",
                    "range": 500,
                    "encounter_type": "head_on",
                    "risk": "medium",
                }],
            },
            visibility_range_m=10000,
        )
        self.assertIn(12, result["applicable_rules"])
        self.assertNotIn(14, result["applicable_rules"])
        self.assertEqual(result["encounters"][0]["own_role"], "unknown")
        self.assertIn("own_ship.wind_side", result["missing_facts"])
        self.assertIn("contacts.TARGET.wind_side", result["missing_facts"])

    def test_crossing_rule_is_gated_on_two_power_driven_vessels(self):
        result = classify_colregs(
            {
                "vessel_type": "ship",
                "contacts": [{
                    "name": "TARGET",
                    "vessel_type": "sailing",
                    "range": 500,
                    "encounter_type": "crossing",
                    "relative_bearing": 45,
                    "risk": "medium",
                }],
            },
            visibility_range_m=10000,
        )
        self.assertNotIn(15, result["applicable_rules"])
        self.assertNotIn(16, result["applicable_rules"])
        self.assertIn(18, result["applicable_rules"])

    def test_rule_12_uses_wind_side_when_available(self):
        result = classify_colregs(
            {
                "vessel_type": "sailing",
                "wind_side": "port",
                "contacts": [{
                    "name": "TARGET",
                    "vessel_type": "sailing",
                    "wind_side": "starboard",
                    "range": 500,
                    "encounter_type": "crossing",
                    "risk": "medium",
                }],
            },
            visibility_range_m=10000,
        )
        self.assertEqual(result["encounters"][0]["own_role"], "give_way")
        self.assertIn(12, result["applicable_rules"])

    def test_top_level_rule_11_is_standing_not_active(self):
        result = self.classify({
            "name": "TARGET",
            "vessel_type": "ship",
            "range": 500,
            "risk": "low",
        })
        self.assertIn(11, result["standing_rules"])
        self.assertNotIn(11, result["active_encounter_rules"])

    def test_course_change_selects_rule_34_obligation(self):
        result = classify_colregs(
            {
                "vessel_type": "ship",
                "contacts": [{
                    "name": "TARGET",
                    "vessel_type": "ship",
                    "range": 100,
                    "age_s": 1,
                    "encounter_type": "head_on",
                }],
            },
            visibility_range_m=10000,
            stale_after_s=5,
            decision={"action": "alter_course", "degrees": 20},
        )
        self.assertIn(34, result["applicable_rules"])
        self.assertEqual(result["decision_obligations"][0]["status"], "not_commanded")

    def test_course_change_recognizes_accompanying_horn_command(self):
        result = classify_colregs(
            {
                "vessel_type": "ship",
                "contacts": [{
                    "name": "TARGET",
                    "vessel_type": "ship",
                    "range": 100,
                    "age_s": 1,
                    "encounter_type": "head_on",
                }],
            },
            visibility_range_m=10000,
            stale_after_s=5,
            decision={
                "action": "alter_course",
                "degrees": 20,
                "auxiliary_commands": [{
                    "type": "sound_horn",
                    "signal": "altering_course_to_starboard",
                }],
            },
        )
        self.assertEqual(result["decision_obligations"][0]["status"], "commanded")


class ColregsRepositoryTests(unittest.TestCase):
    def test_rule_11_and_12_texts_are_not_confused(self):
        application, sailing = ColregsRepository().retrieve([11, 12])
        self.assertEqual(application["title"], "Application")
        self.assertEqual(
            application["text_verbatim"],
            "Rules in this Section apply to vessels in sight of one another.",
        )
        self.assertEqual(sailing["title"], "Sailing Vessels")
        self.assertIn("two sailing vessels", sailing["text_verbatim"])

    def test_exact_lookup_does_not_confuse_annex_numbers(self):
        rules = ColregsRepository().retrieve([1, 14, 34])
        self.assertEqual([rule["number"] for rule in rules], [1, 14, 34])
        self.assertEqual(rules[1]["title"], "Head-on Situation")

    def test_verdict_cannot_cite_unretrieved_rule(self):
        verdict = ColregsVerdict(
            verdict="reject",
            validated_rule_ids=[],
            violated_rule_ids=[19],
            reason="Unsupported citation",
            confidence="high",
            missing_facts=[],
        )
        with self.assertRaisesRegex(ValueError, "unretrieved"):
            AgentColregs._check_verdict(
                verdict,
                {"colregs_context": {"applicable_rules": [2, 7, 8]}},
            )


if __name__ == "__main__":
    unittest.main()
