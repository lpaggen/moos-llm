from __future__ import annotations

import configparser
import math
import unittest
from pathlib import Path

from tools.generate_imazu_scenarios import (
    INITIAL_TCPA_MIN,
    NM_M,
    NORMAL_SPEED_KN,
    OVERTAKEN_SPEED_KN,
    SCENARIO_SCALE,
    load_constellations,
    metres_per_second,
    render_case,
)


ROOT = Path(__file__).resolve().parents[1]
IMAZU = ROOT / "mission/scenarios/imazu"


class ImazuScenarioTests(unittest.TestCase):
    def test_checked_in_scenarios_are_generated_from_source_table(self) -> None:
        cases = load_constellations(IMAZU / "imazu_constellations.csv")
        for case, targets in cases.items():
            actual = (IMAZU / f"{case:02d}/{case:02d}.ini").read_text(encoding="utf-8")
            self.assertEqual(actual, render_case(case, targets), f"case {case}")

    def test_source_scale_and_speeds_are_physical(self) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(IMAZU / "03/03.ini")
        own = parser["vehicle:LLM_SHIP"]
        target = parser["vehicle:IMAZU_TS1"]
        own_y = float(dict(item.strip().split("=") for item in own["start"].split(","))["y"])
        target_y = float(dict(item.strip().split("=") for item in target["start"].split(","))["y"])
        own_speed = float(own.get("speed", parser["DEFAULT"]["speed"]))
        target_speed = float(target["speed"])

        self.assertAlmostEqual(own_y, -6 * NM_M, places=3)
        waypoint_y = float(dict(item.strip().split("=") for item in own["waypoint"].split(","))["y"])
        self.assertAlmostEqual(waypoint_y - own_y, 12 * NM_M, places=3)
        self.assertAlmostEqual(target_y, -4.2 * NM_M * SCENARIO_SCALE, places=3)
        self.assertAlmostEqual(own_speed, metres_per_second(NORMAL_SPEED_KN), places=3)
        self.assertAlmostEqual(target_speed, metres_per_second(OVERTAKEN_SPEED_KN), places=3)
        tcpa = (target_y - own_y) / (own_speed - target_speed)
        self.assertTrue(math.isclose(tcpa, INITIAL_TCPA_MIN * 60.0, abs_tol=1.0), tcpa)

    def test_initial_ranges_are_outside_caution_envelope(self) -> None:
        cases = load_constellations(IMAZU / "imazu_constellations.csv")
        own = (0.0, -6.0 * NM_M * SCENARIO_SCALE)
        for case, targets in cases.items():
            for target in targets:
                position = (
                    target["east"] * NM_M * SCENARIO_SCALE,
                    target["north"] * NM_M * SCENARIO_SCALE,
                )
                initial_range = math.hypot(position[0] - own[0], position[1] - own[1])
                self.assertGreater(
                    initial_range,
                    0.5 * NM_M,
                    (case, target["target"], initial_range),
                )

    def test_corrected_paper_table_entries(self) -> None:
        cases = load_constellations(IMAZU / "imazu_constellations.csv")
        self.assertEqual(cases[12], [
            dict(target=1, heading=-45.0, north=-4.243, east=4.243),
            dict(target=2, heading=-10.0, north=-5.909, east=1.042),
        ])
        self.assertEqual(cases[16][0],
                         dict(target=1, heading=45.0, north=-2.970, east=-2.970))
        self.assertEqual(sum(map(len, cases.values())), 50)

    def test_all_targets_meet_own_ship_after_thirty_minutes(self) -> None:
        # Independent relative-motion check catches speed errors even when
        # generated INIs and the CSV agree with one another.
        for number in range(1, 23):
            config = configparser.ConfigParser()
            config.read(IMAZU / f"{number:02d}/{number:02d}.ini")
            def state(section):
                fields = dict(part.strip().split("=", 1)
                              for part in config[section]["start"].split(","))
                angle = math.radians(float(fields["heading"]))
                speed = float(config[section]["speed"])
                return (float(fields["x"]), float(fields["y"]),
                        speed * math.sin(angle), speed * math.cos(angle))
            ox, oy, ovx, ovy = state("vehicle:LLM_SHIP")
            for section in config.sections():
                if section == "vehicle:LLM_SHIP":
                    continue
                x, y, vx, vy = state(section)
                dx, dy, rvx, rvy = x - ox, y - oy, vx - ovx, vy - ovy
                tcpa = -(dx * rvx + dy * rvy) / (rvx * rvx + rvy * rvy)
                cpa = math.hypot(dx + rvx * tcpa, dy + rvy * tcpa)
                with self.subTest(case=number, target=section):
                    self.assertAlmostEqual(tcpa, 1800.0, delta=1.0)
                    self.assertLess(cpa, 2.0)


if __name__ == "__main__":
    unittest.main()
