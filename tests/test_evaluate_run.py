from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.evaluate_run import evaluate_run, manoeuvre_axis_from_decisions


def sample(time, x, y=0, vehicle="own", heading=90, speed=10):
    return dict(time=time, vehicle=vehicle, x=x, y=y, heading=heading, speed=speed)


class EvaluateRunTests(unittest.TestCase):
    def score(self, trajectory=None, **kwargs):
        options = dict(own_ship="own", start=(0, 0), goal=(100, 0),
                       nominal_speed=10, reached_radius_m=0,
                       decision_events=["hold_course"], findings=[])
        options.update(kwargs)
        if trajectory is None:
            trajectory = [sample(0, 0), sample(10, 100)]
        return evaluate_run(trajectory, **options)

    def parallel(self, clearance):
        return [sample(0, 0), sample(10, 100),
                sample(0, 0, clearance, "contact"), sample(10, 100, clearance, "contact")]

    def test_ideal_run_and_json_serialization(self):
        result = self.score()
        self.assertEqual(result["verdict"], "PASS")
        self.assertAlmostEqual(result["composite_score"], 1)
        self.assertIsNone(result["safety"]["min_cpa_m"])
        json.dumps(result, allow_nan=False)

    def test_collision_gate_with_interpolated_crossing(self):
        trajectory = [sample(0, 0), sample(10, 100),
                      sample(0, 100, vehicle="contact"), sample(10, 0, vehicle="contact")]
        result = self.score(trajectory)
        self.assertEqual(result["safety"]["min_cpa_m"], 0)
        self.assertEqual(result["composite_score"], 0)
        self.assertEqual(result["verdict"], "FAIL -- collision occurred")
        for axis in ("compliance", "explanation_compliance"):
            self.assertEqual(result[axis]["score"], 0)
            self.assertEqual(result[axis]["breakdown"][0]["code"], "collision")

    def test_near_miss_hand_calculated_composite(self):
        result = self.score(self.parallel(250))
        self.assertEqual(result["verdict"], "PASS_WITH_CPA_VIOLATION")
        self.assertEqual(result["safety"]["score"], 0.5)
        self.assertAlmostEqual(result["compliance"]["score"], 0.7)
        self.assertAlmostEqual(result["composite_score"], 0.765)

    def test_strict_clearance_thresholds(self):
        self.assertTrue(self.score(self.parallel(15))["safety"]["passed"])
        self.assertFalse(self.score(self.parallel(14.99))["safety"]["passed"])
        self.assertEqual(self.score(self.parallel(500))["verdict"], "PASS")

    def test_incomplete_stationary_run_is_capped(self):
        result = self.score([sample(0, 0, speed=0), sample(100, 0, speed=0)])
        self.assertEqual(result["verdict"], "FAIL -- did not reach the goal")
        self.assertEqual(result["composite_score"], 0.2)
        self.assertEqual(result["temporal"]["temporal_score"], 0)
        self.assertEqual(result["spatial"]["spatial_score"], 0)

    def test_efficiency_stops_at_first_arrival(self):
        trajectory = [sample(0, 0), sample(10, 0, 100), sample(30, 100), sample(50, 300)]
        result = self.score(trajectory)
        self.assertAlmostEqual(result["temporal"]["temporal_score"], 1 / 3)
        self.assertAlmostEqual(result["spatial"]["path_length_m"], 100 + 2 ** 0.5 * 100)
        self.assertAlmostEqual(result["spatial"]["spatial_score"], 1 / (1 + 2 ** 0.5))

    def test_explanation_does_not_change_composite(self):
        findings = [{"step": i, "code": "E_role_fabrication"} for i in range(10)]
        result = self.score(findings=findings)
        self.assertEqual(result["explanation_compliance"]["score"], 0)
        self.assertEqual(result["compliance"]["score"], 1)
        self.assertEqual(result["composite_score"], self.score()["composite_score"])

    def test_finding_deduplication_and_occurrences(self):
        result = self.score(findings=[
            {"step": 1, "code": "E_role_fabrication"},
            {"step": 1, "code": "A_fabricated_risk"},
            {"step": 1, "code": "B_wrong_direction"},
            {"step": 1, "code": "B_wrong_direction"},
            {"step": 2, "code": "B_wrong_direction"},
            {"code": "P_wrong_side_pass"},
        ])
        self.assertAlmostEqual(result["compliance"]["score"], 0.4)
        self.assertAlmostEqual(result["explanation_compliance"]["score"], 0.85)
        self.assertEqual(len(result["compliance"]["breakdown"]), 3)

    def test_cpa_violation_is_derived_once(self):
        result = self.score(self.parallel(250), findings=[{"code": "cpa_violation"}] * 3)
        self.assertEqual(len(result["compliance"]["breakdown"]), 1)
        result = self.score(self.parallel(600), findings=[{"code": "cpa_violation"}])
        self.assertEqual(result["compliance"]["score"], 1)

    def test_reversals_persist_across_hold_and_other_controls(self):
        actions = ["turn_right", "hold_course", "speed_up", "turn_left", "slow_down"]
        axis = manoeuvre_axis_from_decisions(actions, 2)
        self.assertEqual(axis["manoeuvre_count"], 2)
        self.assertEqual(axis["manoeuvre_rate"], 0.25)
        self.assertAlmostEqual(axis["manoeuvre_score"], 1 / 6)
        self.assertEqual(manoeuvre_axis_from_decisions([], 0)["manoeuvre_score"], 1)
        self.assertEqual(manoeuvre_axis_from_decisions(["turn_right"] * 5, 1)["manoeuvre_count"], 0)

    def test_smoothness_wraps_heading(self):
        result = self.score([sample(0, 0, heading=359, speed=9), sample(10, 100, heading=1, speed=10)])
        axis = result["manoeuvre"]
        self.assertAlmostEqual(axis["mean_abs_heading_rate"], 0.2)
        self.assertAlmostEqual(axis["mean_abs_speed_rate"], 0.1)
        self.assertAlmostEqual(axis["smoothness_score"], 0.93)

    def test_safety_includes_fractional_final_instant(self):
        trajectory = [sample(0, 0), sample(0.5, 100),
                      sample(0, 200, vehicle="contact"), sample(0.5, 100, vehicle="contact")]
        self.assertEqual(self.score(trajectory)["composite_score"], 0)

    def test_bad_inputs_rejected(self):
        cases = [
            dict(trajectory=[]),
            dict(trajectory=[sample(0, 0), sample(0, 1)]),
            dict(trajectory=[sample(0, float("nan"))]),
            dict(trajectory=[sample(0, 0), sample(10, 100), sample(5, 500, vehicle="contact")]),
            dict(nominal_speed=0), dict(safe_distance_m=-1),
            dict(goal=(0, 0)), dict(weights={"safety": 1}),
            dict(decision_events=["alter_course"]),
            dict(findings=[{"code": "unknown", "step": 1}]),
            dict(findings=[{"code": "B_wrong_direction"}]),
        ]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.score(**options)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trajectory = root / "trajectory.csv"
            with trajectory.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["time", "vehicle", "x", "y", "heading", "speed"])
                writer.writeheader()
                writer.writerows([sample(0, 0), sample(10, 100)])
            (root / "decisions.json").write_text('["hold_course"]')
            (root / "findings.json").write_text('[]')
            script = Path(__file__).resolve().parents[1] / "tools" / "evaluate_run.py"
            completed = subprocess.run([
                sys.executable, str(script), str(trajectory), "--own-ship", "own",
                "--start", "0", "0", "--goal", "100", "0", "--nominal-speed", "10",
                "--decisions", str(root / "decisions.json"),
                "--findings", str(root / "findings.json"),
            ], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(completed.stdout)["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
