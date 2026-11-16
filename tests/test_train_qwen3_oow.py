import json
import tempfile
import unittest
from pathlib import Path

from sft.train_qwen3_oow import balanced_epoch_counts, inspect_dataset


class TrainQwen3OowTest(unittest.TestCase):
    def test_balanced_epoch_keeps_every_row_and_balances_exposure(self):
        actions = ["maintain_course"] * 8 + ["alter_course", "set_speed"]
        maintain, maneuver = balanced_epoch_counts(actions, maintain_fraction=0.5)
        self.assertEqual((maintain, maneuver), (8, 8))
        self.assertGreaterEqual(maintain, 8)
        self.assertGreaterEqual(maneuver, 2)

    def test_balanced_epoch_rejects_single_class_data(self):
        with self.assertRaises(ValueError):
            balanced_epoch_counts(["maintain_course"], 0.5)

    def test_inspection_accepts_disjoint_chat_splits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for split, source in (
                ("train_balanced", "train_seed"),
                ("validation_natural", "validation_seed"),
                ("test_natural", "test_seed"),
            ):
                row = {
                    "example_id": split,
                    "source_file": source,
                    "reference_mode": "none",
                    "prompt": [{"role": "user", "content": "state"}],
                    "completion": [{
                        "role": "assistant",
                        "content": json.dumps({"action": "maintain_course"}),
                    }],
                }
                (root / f"{split}.jsonl").write_text(
                    json.dumps(row) + "\n", encoding="utf-8"
                )
            summary = inspect_dataset(root)
            self.assertEqual(summary["train"]["rows"], 1)
            self.assertEqual(summary["episode_overlap"]["train_test"], 0)


if __name__ == "__main__":
    unittest.main()
