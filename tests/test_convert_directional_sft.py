import unittest

from agents.agent_oow import _resolve_directional_decision_fields
from sft.convert_directional_sft import convert_decision
from sft.preflight_directional_sft import validate_decision


class DirectionalSftTest(unittest.TestCase):
    def test_converts_signed_labels_without_changing_magnitude(self):
        common = {"speed": 0.0, "reason": "Rules 8 and 14.", "auxiliary_commands": []}
        self.assertEqual(
            convert_decision({"action": "alter_course", "degrees": 30.0, **common})[
                "turn_direction"
            ],
            "starboard",
        )
        port = convert_decision({"action": "alter_course", "degrees": -45.0, **common})
        self.assertEqual(port["turn_direction"], "port")
        self.assertEqual(port["turn_degrees"], 45.0)

    def test_runtime_resolves_category_to_internal_signed_command(self):
        common = {
            "action": "alter_course",
            "speed": 0.0,
            "reason": "Rule 8.",
            "auxiliary_commands": [],
        }
        starboard = _resolve_directional_decision_fields(
            {**common, "turn_direction": "starboard", "turn_degrees": 30.0}
        )
        port = _resolve_directional_decision_fields(
            {**common, "turn_direction": "port", "turn_degrees": 30.0}
        )
        self.assertEqual(starboard["degrees"], 30.0)
        self.assertEqual(port["degrees"], -30.0)
        self.assertNotIn("turn_direction", starboard)

    def test_rejects_turn_fields_on_non_course_action(self):
        with self.assertRaises(ValueError):
            _resolve_directional_decision_fields(
                {
                    "action": "maintain_course",
                    "turn_direction": "starboard",
                    "turn_degrees": 30.0,
                }
            )

    def test_preflight_contract_is_exact(self):
        decision = {
            "action": "alter_course",
            "turn_direction": "starboard",
            "turn_degrees": 30.0,
            "speed": 0.0,
            "reason": "Rules 8 and 14.",
            "auxiliary_commands": [],
        }
        self.assertEqual(validate_decision(decision), decision)
        with self.assertRaises(ValueError):
            validate_decision({**decision, "degrees": 30.0})


if __name__ == "__main__":
    unittest.main()
