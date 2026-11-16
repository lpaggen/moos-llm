import unittest

from tools.score_baseline_logs import (
    FROZEN_WEIGHTS,
    SCORING_CONTRACT_VERSION,
    action_from_decision,
    citation_axis,
    compliance_findings_for_step,
    contact_at,
    encounter,
    ground_truth,
    inferred_action,
    wrong_side_at_closest,
)


def nav(x=0, y=0, heading=0, speed=5, time=0):
    return dict(x=x, y=y, heading=heading, speed=speed, time=time)


class BaselineScoringTests(unittest.TestCase):
    def test_frozen_contract_weights_are_exact_and_sum_to_one(self):
        self.assertEqual(SCORING_CONTRACT_VERSION, "imazu-frozen-v1")
        self.assertEqual(FROZEN_WEIGHTS, {
            "safety": 0.35,
            "compliance": 0.10,
            "citation": 0.10,
            "temporal": 0.10,
            "spatial": 0.10,
            "manoeuvre": 0.10,
            "smoothness": 0.15,
        })
        self.assertAlmostEqual(sum(FROZEN_WEIGHTS.values()), 1.0)

    def test_citation_axis_uses_full_eriksen_set_including_persistent_rules(self):
        events = [
            {"event": "oow_colregs_oracle", "cycle_id": 1, "classification": {
                "primary_contact": "TS1", "contacts": [{
                    "contact": "TS1", "applicable_rules": [2, 5, 6, 7, 15, 16],
                }],
            }},
            {"event": "oow_decision", "cycle_id": 1, "decision": {
                "reason": "Rules 2, 5, 6, 7, 15 and 16 apply.",
            }},
        ]

        result = citation_axis(events)

        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["records"][0]["expected_rules"], [2, 5, 6, 7, 15, 16])

    def test_citation_axis_uses_captain_final_decision_in_critic_run(self):
        events = [
            {"event": "oow_colregs_oracle", "cycle_id": 1, "classification": {
                "primary_contact": "TS1", "contacts": [{
                    "contact": "TS1", "applicable_rules": [2, 5, 6, 7],
                }],
            }},
            {"event": "oow_decision", "cycle_id": 1,
             "decision": {"reason": "Rule 13."}},
            {"event": "captain_decision", "cycle_id": 1,
             "decision": {"reason": "Rules 2, 5, 6 and 7."}},
        ]

        result = citation_axis(events)

        self.assertEqual(result["decision_source"], "captain_decision")
        self.assertEqual(result["score"], 1.0)

    def test_citation_axis_fails_closed_without_oracle_coverage(self):
        result = citation_axis([
            {"event": "oow_decision", "cycle_id": 1,
             "decision": {"reason": "Rules 2, 5, 6 and 7."}},
        ])

        self.assertEqual(result["oracle_cycles"], 0)
        self.assertEqual(result["score"], 0.0)

    def test_reciprocal_head_on(self):
        self.assertEqual(encounter(nav(), nav(y=1000, heading=180)), ("head_on", "give_way"))

    def test_crossing_roles(self):
        self.assertEqual(encounter(nav(), nav(x=100, y=100, heading=270)), ("crossing", "give_way"))
        self.assertEqual(encounter(nav(), nav(x=-100, y=100, heading=90)), ("crossing", "stand_on"))

    def test_geometry_bands(self):
        self.assertEqual(ground_truth(nav(), nav(y=1000, heading=180), 500)["band"], "acute")
        self.assertEqual(ground_truth(nav(), nav(y=3000, heading=180), 500)["band"], "early")
        self.assertEqual(ground_truth(nav(), nav(y=-1000, heading=180), 500)["band"], "passed")
        self.assertEqual(ground_truth(nav(), nav(x=600, y=1000, heading=180), 500)["band"], "safe")

    def test_action_proxy_wraps_and_applies_deadband(self):
        self.assertEqual(inferred_action(nav(heading=359), nav(heading=1)), ("turn_right", 2))
        self.assertEqual(inferred_action(nav(heading=1), nav(heading=359)), ("turn_left", 2))
        self.assertEqual(inferred_action(nav(), nav(heading=0.5)), ("hold_course", 0))
        self.assertEqual(inferred_action(nav(), nav(speed=4)), ("slow_down", 0))

    def test_audited_decision_direction_is_not_inferred_from_route_recovery(self):
        self.assertEqual(
            action_from_decision({"action": "alter_course", "degrees": 30}),
            ("turn_right", 30),
        )
        self.assertEqual(
            action_from_decision({"action": "rejoin_route", "degrees": 0}),
            ("hold_course", 0),
        )

    def test_contact_interpolation_does_not_freeze_stale_reports(self):
        track = [nav(heading=359), nav(x=100, heading=1, time=10)]
        middle = contact_at(track, 5)
        self.assertEqual(middle["x"], 50)
        self.assertEqual(middle["heading"], 0)
        self.assertIsNone(contact_at(track, 11))

    def test_give_way_contact_can_justify_action_despite_simultaneous_stand_on_contact(self):
        truths = [
            dict(contact="stand_on", cpa=100, tcpa=500, band="early",
                 kind="crossing", role="stand_on", relative_bearing=300),
            dict(contact="give_way", cpa=300, tcpa=200, band="acute",
                 kind="crossing", role="give_way", relative_bearing=40),
        ]

        findings = compliance_findings_for_step("turn_right", 30, truths, 8)

        self.assertNotIn("B_17c", [finding["code"] for finding in findings])

    def test_findings_name_the_contact_that_triggers_them(self):
        truths = [
            dict(contact="port_contact", cpa=200, tcpa=100, band="acute",
                 kind="crossing", role="stand_on", relative_bearing=300),
        ]

        findings = compliance_findings_for_step("turn_left", 15, truths, 9)

        self.assertEqual(
            {finding["ground_truth"]["contact"] for finding in findings},
            {"port_contact"},
        )

    def test_acute_stand_on_obligation_justifies_starboard_action(self):
        truths = [
            dict(contact="early_stand_on", cpa=100, tcpa=500, band="early",
                 kind="crossing", role="stand_on", relative_bearing=300),
            dict(contact="acute_stand_on", cpa=150, tcpa=60, band="acute",
                 kind="crossing", role="stand_on", relative_bearing=290),
        ]

        findings = compliance_findings_for_step("turn_right", 60, truths, 10)

        self.assertNotIn("B_17c", [finding["code"] for finding in findings])

    def test_continuing_active_avoidance_is_not_no_action(self):
        truths = [
            dict(contact="acute", cpa=100, tcpa=50, band="acute",
                 kind="crossing", role="stand_on", relative_bearing=300),
        ]

        findings = compliance_findings_for_step(
            "hold_course", 0, truths, 11, active_avoidance=True,
        )

        self.assertNotIn("D_no_action_when_required",
                         [finding["code"] for finding in findings])

    def test_recorded_critical_contact_justifies_action_across_threshold_mismatch(self):
        truths = [
            dict(contact="early", cpa=100, tcpa=500, band="early",
                 kind="crossing", role="stand_on", relative_bearing=300,
                 recorded_requires_action=False),
            dict(contact="critical", cpa=530, tcpa=100, band="safe",
                 kind="crossing", role="stand_on", relative_bearing=290,
                 recorded_requires_action=True),
        ]

        findings = compliance_findings_for_step("turn_right", 60, truths, 12)

        self.assertNotIn("B_17c", [finding["code"] for finding in findings])

    def test_crossing_contact_ahead_means_own_ship_passed_astern(self):
        own = nav(x=0, y=0, heading=0)

        self.assertFalse(wrong_side_at_closest(
            own, nav(x=0, y=100, heading=270), "crossing",
        ))
        self.assertTrue(wrong_side_at_closest(
            own, nav(x=0, y=-100, heading=270), "crossing",
        ))


if __name__ == "__main__":
    unittest.main()
