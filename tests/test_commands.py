import unittest

from pydantic import ValidationError

from commands.base_model import CommandMessage
from agents.agent_captain import AgentCaptain
from agents.schemas.captain import CaptainDecision


class CommandMessageTests(unittest.TestCase):
    def test_course_change_can_replace_active_waypoint_without_contact(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        state = {
            "control": {"action_underway": True, "paused": False},
            "own_ship": {"speed": 5.0},
            "contacts": [],
        }
        decision = captain.validate_decision({
            "action": "alter_course", "degrees": -30, "speed": 0,
            "reason": "Observed traffic.", "auxiliary_commands": [],
        }, state)
        command = captain.command_from_decision(decision, state)
        self.assertEqual(command["action"], "alter_course")
        self.assertEqual(command["degrees"], -30)
        self.assertEqual(command["distance_m"], 500.0)
        self.assertEqual(command["contact"], "")
        speed = captain.command_from_decision(
            {"action": "set_speed", "speed": 3}, state
        )
        self.assertEqual(speed["action"], "set_speed")
        self.assertEqual(speed["speed"], 3)

    def test_rejoin_is_a_valid_interface_action(self):
        decision = CaptainDecision.model_validate({
            "action": "rejoin_route", "degrees": 0, "speed": 0,
            "reason": "Returning.", "auxiliary_commands": [],
        })
        message = CommandMessage.model_validate({
            "vessel_id": "LLM_SHIP",
            "commands": [{"type": "rejoin_route"}],
        })
        self.assertEqual(decision.action, "rejoin_route")
        self.assertEqual(message.commands[0].type, "rejoin_route")

    def test_navigation_horn_and_vhf_can_share_one_message(self):
        message = CommandMessage.model_validate({
            "vessel_id": "LLM_SHIP",
            "commands": [
                {"type": "alter_course", "degrees": 20},
                {"type": "sound_horn", "signal": "altering_course_to_starboard"},
                {"type": "send_vhf", "channel": 16, "message": "Altering to starboard."},
            ],
        })

        self.assertEqual(
            [command.type for command in message.commands],
            ["alter_course", "sound_horn", "send_vhf"],
        )

    def test_multiple_navigation_commands_are_rejected(self):
        with self.assertRaises(ValidationError):
            CommandMessage.model_validate({
                "vessel_id": "LLM_SHIP",
                "commands": [
                    {"type": "alter_course", "degrees": 20},
                    {"type": "set_speed", "speed": 4},
                ],
            })

    def test_duplicate_auxiliary_commands_are_rejected(self):
        with self.assertRaises(ValidationError):
            CommandMessage.model_validate({
                "vessel_id": "LLM_SHIP",
                "commands": [
                    {"type": "send_vhf", "channel": 16, "message": "One"},
                    {"type": "send_vhf", "channel": 16, "message": "Two"},
                ],
            })


if __name__ == "__main__":
    unittest.main()
