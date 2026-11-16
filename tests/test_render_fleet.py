import unittest
from pathlib import Path

from mission.render_fleet import load_fleet


class ImazuFleetTests(unittest.TestCase):
    def test_case_01_renders_own_ship_and_non_avoiding_target(self):
        config = (
            Path(__file__).resolve().parents[1]
            / "mission"
            / "scenarios"
            / "imazu"
            / "01"
            / "01.ini"
        )

        rows = load_fleet(config, amount=None, fast=False)

        self.assertEqual(rows["names"], ["LLM_SHIP", "IMAZU_TS1"])
        self.assertEqual(rows["avoids"], ["true", "false"])
        self.assertEqual(
            rows["starts"],
            ["x=0,y=-11112,heading=0", "x=0,y=11112,heading=180"],
        )
        self.assertEqual(rows["speeds"], ["6.173", "6.173"])


if __name__ == "__main__":
    unittest.main()
