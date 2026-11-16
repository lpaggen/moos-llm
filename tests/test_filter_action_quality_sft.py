import unittest

from sft.filter_action_quality_sft import action_quality_issue


def payload(*, lookahead=None, contacts=None):
    value = {"state": {"contacts": contacts or []}}
    if lookahead is not None:
        value["teacher_candidate_lookahead"] = {"candidates": lookahead}
    return value


def decision(action, degrees=0.0, reason="safe action"):
    return {"action": action, "degrees": degrees, "speed": 0.0, "reason": reason}


class ActionQualityTest(unittest.TestCase):
    def test_rejects_numeric_port_when_reason_requires_starboard(self):
        issue = action_quality_issue(
            decision("alter_course", -30, "Both vessels must alter to starboard."),
            payload(),
        )
        self.assertEqual(issue, "port_output_but_reason_requires_starboard")

    def test_rejects_port_without_lookahead(self):
        issue = action_quality_issue(
            decision("alter_course", -30, "Alter to port."), payload()
        )
        self.assertEqual(issue, "port_action_has_no_candidate_lookahead")

    def test_retains_port_only_when_clear_and_starboard_is_not_clear(self):
        candidates = [
            {"action": "alter_course", "degrees": -60, "projected_minimum_separation_m": 633, "all_contacts_at_or_above_minimum": True},
            {"action": "alter_course", "degrees": 30, "projected_minimum_separation_m": 31, "all_contacts_at_or_above_minimum": False},
        ]
        self.assertIsNone(
            action_quality_issue(
                decision(
                    "alter_course",
                    -60,
                    "Alter to port because every available starboard turn worsens separation.",
                ),
                payload(lookahead=candidates),
            )
        )

    def test_rejects_port_without_visible_starboard_exception(self):
        candidates = [
            {"action": "alter_course", "degrees": -60, "projected_minimum_separation_m": 633, "all_contacts_at_or_above_minimum": True},
            {"action": "alter_course", "degrees": 30, "projected_minimum_separation_m": 31, "all_contacts_at_or_above_minimum": False},
        ]
        issue = action_quality_issue(
            decision("alter_course", -60, "Alter to port to pass astern."),
            payload(lookahead=candidates),
        )
        self.assertEqual(issue, "port_action_lacks_explicit_starboard_exception")

    def test_rejects_port_when_starboard_candidate_is_clear(self):
        candidates = [
            {"action": "alter_course", "degrees": -30, "projected_minimum_separation_m": 700, "all_contacts_at_or_above_minimum": True},
            {"action": "alter_course", "degrees": 30, "projected_minimum_separation_m": 650, "all_contacts_at_or_above_minimum": True},
        ]
        issue = action_quality_issue(
            decision(
                "alter_course",
                -30,
                "Alter to port because the starboard alternative is unsafe.",
            ),
            payload(lookahead=candidates),
        )
        self.assertEqual(issue, "port_action_used_despite_safe_starboard_candidate")

    def test_rejects_rejoin_during_acute_projection(self):
        contacts = [{
            "name": "TS1",
            "projection_status": "valid",
            "cpa_distance_m": 20,
            "tcpa_s": 80,
        }]
        issue = action_quality_issue(decision("rejoin_route"), payload(contacts=contacts))
        self.assertEqual(issue, "rejoin_route_during_acute_sub500m_projection")


if __name__ == "__main__":
    unittest.main()
