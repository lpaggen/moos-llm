import unittest

from sft.prepare_oow_sft import (
    pair_rows,
    rebalance_training,
    split_by_episode,
    to_chat_row,
)


def input_row(source, index, actions=("maintain_course", "stop")):
    candidates = [
        {"action": action, "degrees": 0.0, "distance_m": 0.0, "speed": 0.0}
        for action in actions
    ]
    return {
        "id": f"state::{source}::{index}",
        "source_file": source,
        "cycle_id": index,
        "state": {
            "allowed_actions": list(actions),
            "candidate_actions": candidates,
        },
        "narrative": "Bridge state.",
    }


def label(action):
    return {
        "action": action,
        "degrees": 0.0,
        "speed": 0.0,
        "reason": f"Choose {action}.",
        "auxiliary_commands": [],
    }


class PrepareOowSftTest(unittest.TestCase):
    def test_all_none_reference_condition(self):
        row = pair_rows([input_row("one", 1)], [label("maintain_course")])[0]
        chat = to_chat_row(row, {}, seed=17, reference_condition="none")

        self.assertEqual(chat["reference_mode"], "none")
        self.assertNotIn("COLREG REFERENCE MATERIAL", chat["prompt"][-1]["content"])
        self.assertTrue(chat["prompt"][-1]["content"].endswith("}]}"))

    def test_pairing_validates_actions(self):
        paired = pair_rows([input_row("one", 1)], [label("maintain_course")])
        self.assertEqual(paired[0]["source_file"], "one")

    def test_episode_splits_are_disjoint(self):
        inputs = []
        labels = []
        for episode in range(30):
            for cycle in range(3):
                inputs.append(input_row(f"seed_{episode}", cycle))
                labels.append(label("stop" if episode % 7 == 0 else "maintain_course"))
        splits = split_by_episode(pair_rows(inputs, labels), seed=17)
        episode_sets = {
            name: {row["source_file"] for row in rows}
            for name, rows in splits.items()
        }
        self.assertFalse(episode_sets["train"] & episode_sets["validation"])
        self.assertFalse(episode_sets["train"] & episode_sets["test"])
        self.assertFalse(episode_sets["validation"] & episode_sets["test"])
        self.assertEqual(sum(map(len, splits.values())), 90)

    def test_balancing_reduces_maintain_dominance(self):
        actions = (
            ["maintain_course"] * 100
            + ["set_speed"] * 10
            + ["alter_course"] * 10
            + ["resume"] * 10
            + ["stop"] * 10
        )
        inputs = [input_row("one", i, tuple(set(actions))) for i in range(len(actions))]
        # Supply the numeric candidate values required by the validator.
        for input_value in inputs:
            for candidate in input_value["state"]["candidate_actions"]:
                if candidate["action"] == "set_speed":
                    candidate["speed"] = 5.0
                elif candidate["action"] == "alter_course":
                    candidate["degrees_range"] = {"min": -30.0, "max": 30.0}
        labels = [label(action) for action in actions]
        for output in labels:
            if output["action"] == "set_speed":
                output["speed"] = 5.0
            elif output["action"] == "alter_course":
                output["degrees"] = 15.0
        balanced = rebalance_training(pair_rows(inputs, labels), seed=17)
        maintain_fraction = sum(
            row["label"]["action"] == "maintain_course" for row in balanced
        ) / len(balanced)
        self.assertLess(maintain_fraction, 0.8)


if __name__ == "__main__":
    unittest.main()
