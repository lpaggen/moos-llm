import json
import unittest

from sft.evaluate_qwen3_oow import (
    score,
    validate_prediction,
    without_colreg_references,
)


def example(action="set_speed"):
    candidate = {
        "action": action,
        "degrees": 0.0,
        "distance_m": 0.0,
        "speed": 7.0 if action == "set_speed" else 0.0,
    }
    row = {
        "example_id": "one",
        "reference_mode": "none",
        "prompt": [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": "state\n\nPERMITTED ACTIONS\n"
                + json.dumps({
                    "allowed_actions": [action],
                    "candidate_actions": [candidate],
                }, separators=(",", ":"))
                + "\n\nCOLREG REFERENCE MATERIAL: NOT PROVIDED",
            },
        ],
    }
    decision = {
        "action": action,
        "degrees": 0.0,
        "speed": candidate["speed"],
        "reason": "Rule 8 supports the action.",
        "auxiliary_commands": [],
    }
    row["completion"] = [{"role": "assistant", "content": json.dumps(decision)}]
    return row, decision


class EvaluateQwen3OowTest(unittest.TestCase):
    def test_remove_colreg_references(self):
        row, _ = example()
        row["reference_mode"] = "relevant"
        row["prompt"][-1]["content"] = row["prompt"][-1]["content"].replace(
            "NOT PROVIDED", "PROVIDED BELOW\nRule 8 -- Action to Avoid Collision"
        )

        transformed = without_colreg_references(row)

        self.assertEqual(transformed["reference_mode"], "none")
        self.assertNotIn(
            "COLREG REFERENCE MATERIAL", transformed["prompt"][-1]["content"]
        )
        valid, errors = validate_prediction(example()[1], transformed)
        self.assertTrue(valid, errors)
        self.assertIn("Rule 8", row["prompt"][-1]["content"])

    def test_candidate_valid_decision(self):
        row, decision = example()
        valid, errors = validate_prediction(decision, row)
        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_exact_runtime_json_prompt_validates_without_candidates(self):
        row = {
            "prompt": [
                {"role": "system", "content": "system"},
                {
                    "role": "user",
                    "content": json.dumps({
                        "allowed_actions": ["alter_course", "set_speed"],
                        "briefing": "A contact is closing.",
                    }),
                },
            ],
        }
        decision = {
            "action": "alter_course",
            "degrees": 30.0,
            "speed": 0.0,
            "reason": "Alter to starboard under Rules 8 and 16.",
            "auxiliary_commands": [],
        }
        valid, errors = validate_prediction(decision, row)
        self.assertTrue(valid, errors)

    def test_directional_narrative_prediction_validates(self):
        decision = {
            "action": "alter_course",
            "turn_direction": "starboard",
            "turn_degrees": 30.0,
            "speed": 0.0,
            "reason": "Alter to starboard under Rules 8 and 14.",
            "auxiliary_commands": [],
        }
        row = {
            "prompt": [
                {"role": "system", "content": "directional"},
                {"role": "user", "content": "CURRENT BRIDGE STATE"},
            ],
            "completion": [{"role": "assistant", "content": json.dumps(decision)}],
        }
        valid, errors = validate_prediction(decision, row)
        self.assertTrue(valid, errors)
        invalid = {**decision, "turn_direction": "none"}
        valid, errors = validate_prediction(invalid, row)
        self.assertFalse(valid)
        self.assertIn("degrees_outside_candidate", errors)

    def test_score_perfect_prediction(self):
        row, decision = example()
        metrics = score([{
            "example": row,
            "gold": decision,
            "prediction": decision,
        }])
        self.assertEqual(metrics["strict_json_rate"], 1.0)
        self.assertEqual(metrics["exact_action_accuracy"], 1.0)
        self.assertEqual(metrics["macro_f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
