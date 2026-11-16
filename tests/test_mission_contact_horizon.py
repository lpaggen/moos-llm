from pathlib import Path
import re
import unittest

from bridge.state import PLANNING_TCPA_S


class MissionContactHorizonTests(unittest.TestCase):
    def test_node_reports_cover_collision_planning_horizon(self):
        template = (
            Path(__file__).parents[1] / "mission" / "meta_shoreside.moos"
        ).read_text(encoding="utf-8")
        match = re.search(r"^\s*comms_range\s*=\s*([0-9.]+)", template, re.MULTILINE)

        self.assertIsNotNone(match)
        report_range_m = float(match.group(1))
        maximum_head_on_closing_speed_mps = 10.0
        self.assertGreaterEqual(
            report_range_m / maximum_head_on_closing_speed_mps,
            PLANNING_TCPA_S,
        )


if __name__ == "__main__":
    unittest.main()
