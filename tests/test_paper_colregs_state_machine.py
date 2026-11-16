import unittest
import math

from agents.colregs.paper_state_machine import (
    EMERGENCY,
    GIVE_WAY,
    HEAD_ON,
    SAFE,
    STAND_ON,
    PaperColregsStateMachine,
)


def state(contact_x, contact_y, contact_heading, *, own_speed=6.0, contact_speed=6.0):
    return {
        "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": own_speed},
        "contacts": [{
            "name": "TS1",
            "nav": {
                "x": contact_x,
                "y": contact_y,
                "heading": contact_heading,
                "speed": contact_speed,
            },
        }],
    }


class PaperColregsStateMachineTests(unittest.TestCase):
    def test_head_on_enters_and_cites_rule_14(self):
        result = PaperColregsStateMachine().classify(
            state(0.0, 1800.0, 180.0)
        )["contacts"][0]
        self.assertEqual(result["geometrical_state"], HEAD_ON)
        self.assertEqual(result["state"], HEAD_ON)
        self.assertEqual(result["applicable_rules"], [2, 5, 6, 7, 8, 14])
        self.assertIn("alter course to starboard", result["applicable_rule_summaries"]["14"])

    def test_starboard_crossing_is_give_way(self):
        result = PaperColregsStateMachine().classify(
            state(900.0, 900.0, 270.0)
        )["contacts"][0]
        self.assertEqual(result["geometrical_state"], GIVE_WAY)
        self.assertEqual(result["state"], GIVE_WAY)
        self.assertEqual(result["applicable_rules"], [2, 5, 6, 7, 8, 15, 16])
        self.assertTrue({"15", "16"}.issubset(result["applicable_rule_summaries"]))

    def test_port_bow_outside_head_on_sector_is_stand_on(self):
        bearing = math.radians(341.0)
        result = PaperColregsStateMachine().classify(
            state(
                1800.0 * math.sin(bearing),
                1800.0 * math.cos(bearing),
                161.0,
            )
        )["contacts"][0]

        self.assertEqual(result["geometrical_state"], STAND_ON)
        self.assertEqual(result["state"], STAND_ON)
        self.assertEqual(result["own_role"], "stand_on")
        self.assertEqual(result["applicable_rules"], [2, 5, 6, 7, 8, 15, 17])

    def test_opening_contact_is_safe(self):
        result = PaperColregsStateMachine().classify(
            state(0.0, -500.0, 180.0)
        )["contacts"][0]
        self.assertEqual(result["state"], SAFE)

    def test_active_state_must_exit_to_safe_before_reclassification(self):
        machine = PaperColregsStateMachine()
        first = machine.classify(state(0.0, 1800.0, 180.0))["contacts"][0]
        self.assertEqual(first["state"], HEAD_ON)
        crossing_geometry = machine.classify(state(900.0, 900.0, 270.0))["contacts"][0]
        self.assertEqual(crossing_geometry["geometrical_state"], GIVE_WAY)
        self.assertEqual(crossing_geometry["state"], HEAD_ON)
        exited = machine.classify(state(0.0, -2500.0, 180.0))["contacts"][0]
        self.assertEqual(exited["state"], SAFE)

    def test_immediate_head_on_can_enter_emergency(self):
        result = PaperColregsStateMachine().classify(
            state(0.0, 200.0, 180.0)
        )["contacts"][0]
        self.assertEqual(result["state"], EMERGENCY)
        self.assertEqual(result["applicable_rules"], [2, 5, 6, 7, 8])
        self.assertEqual(result["own_role"], "special_consideration")

    def test_primary_contact_prefers_emergency_over_nearest_safe_contact(self):
        snapshot = state(0.0, 300.0, 180.0)
        snapshot["contacts"].append({
            "name": "NEAR_SAFE",
            "nav": {"x": 50.0, "y": -50.0, "heading": 180.0, "speed": 6.0},
        })
        result = PaperColregsStateMachine().classify(snapshot)
        self.assertEqual(result["primary_contact"], "TS1")
        self.assertEqual(result["contacts"][0]["priority_rank"], 1)

    def test_evolution_reports_closing_range(self):
        machine = PaperColregsStateMachine()
        machine.classify(state(0.0, 1800.0, 180.0))
        result = machine.classify(state(0.0, 1600.0, 180.0))["contacts"][0]
        self.assertEqual(
            result["evolution_since_previous_snapshot"]["assessment"], "closing"
        )
        self.assertEqual(
            result["evolution_since_previous_snapshot"]["range_change_m"], -200.0
        )

    def test_power_vessel_gives_way_to_sailing_contact_under_rule_18(self):
        snapshot = state(900.0, 900.0, 270.0)
        snapshot["vessel_type"] = "ship"
        snapshot["contacts"][0]["vessel_type"] = "sailing"
        result = PaperColregsStateMachine().classify(snapshot)["contacts"][0]
        self.assertEqual(result["own_role"], "give_way")
        self.assertEqual(result["applicable_rules"], [2, 5, 6, 7, 8, 18])

    def test_rule_13_controls_when_overtaking_special_vessel(self):
        snapshot = state(0.0, 600.0, 0.0, own_speed=12.0, contact_speed=4.0)
        snapshot["vessel_type"] = "ship"
        snapshot["contacts"][0]["vessel_type"] = "fishing"
        result = PaperColregsStateMachine().classify(snapshot)["contacts"][0]
        self.assertEqual(result["state"], "OT")
        self.assertIn(13, result["applicable_rules"])
        self.assertNotIn(18, result["applicable_rules"])
