import unittest

from agents.teacher_lookahead import build_teacher_candidate_lookahead


class TeacherLookaheadTests(unittest.TestCase):
    def test_head_on_projection_marks_maintain_as_critical(self):
        result = build_teacher_candidate_lookahead({
            "own_ship": {
                "x": 0.0, "y": 0.0, "heading": 0.0, "speed": 5.0,
                "target_speed": 5.0, "recovery_speed": 5.0,
            },
            "mission": {"x": 0.0, "y": 5000.0},
            "contacts": [{
                "name": "HEAD_ON", "x": 0.0, "y": 2000.0,
                "heading": 180.0, "speed": 5.0,
            }],
        })
        maintain = next(
            candidate for candidate in result["candidates"]
            if candidate["action"] == "maintain_course"
        )
        self.assertEqual(maintain["classification"], "critical")
        self.assertLess(maintain["projected_minimum_separation_m"], 150.0)
        self.assertFalse(maintain["all_contacts_at_or_above_minimum"])

    def test_stop_uses_finite_deceleration(self):
        result = build_teacher_candidate_lookahead({
            "own_ship": {
                "x": 0.0, "y": 0.0, "heading": 0.0, "speed": 6.0,
                "target_speed": 6.0, "recovery_speed": 6.0,
            },
            "mission": {"x": 0.0, "y": 5000.0},
            "contacts": [],
        }, horizon_s=120.0)
        stop = next(candidate for candidate in result["candidates"] if candidate["action"] == "stop")
        self.assertGreater(stop["projected_path_length_m"], 100.0)
        self.assertLess(stop["projected_path_length_m"], 130.0)
        self.assertEqual(stop["final_speed_mps"], 0.0)

    def test_worst_contact_controls_multi_contact_classification(self):
        result = build_teacher_candidate_lookahead({
            "own_ship": {
                "x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0,
                "target_speed": 0.0, "recovery_speed": 5.0,
                "stopped": True,
            },
            "mission": {"x": 0.0, "y": 5000.0},
            "contacts": [
                {"name": "SAFE", "x": 600.0, "y": 0.0, "heading": 0.0, "speed": 0.0},
                {"name": "UNSAFE", "x": 499.0, "y": 0.0, "heading": 0.0, "speed": 0.0},
            ],
        }, horizon_s=10.0)
        skip = next(candidate for candidate in result["candidates"] if candidate["action"] == "skip")
        self.assertEqual(skip["worst_contact"], "UNSAFE")
        self.assertEqual(skip["classification"], "unsafe")
        self.assertEqual(skip["contacts"][1]["classification"], "safe")

    def test_threshold_boundaries_and_course_candidate_set(self):
        result = build_teacher_candidate_lookahead({
            "own_ship": {
                "x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0,
                "target_speed": 0.0, "recovery_speed": 4.0,
                "stopped": True,
            },
            "mission": {"x": 0.0, "y": 1000.0},
            "contacts": [{
                "name": "BOUNDARY", "x": 500.0, "y": 0.0,
                "heading": 0.0, "speed": 0.0,
            }],
        }, horizon_s=1.0)
        skip = next(candidate for candidate in result["candidates"] if candidate["action"] == "skip")
        self.assertEqual(skip["classification"], "marginal")
        self.assertEqual(result["thresholds"]["safe_at_or_above_m"], 550.0)

        just_below = build_teacher_candidate_lookahead({
            "own_ship": {
                "x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0,
                "target_speed": 0.0, "recovery_speed": 4.0,
                "stopped": True,
            },
            "contacts": [{
                "name": "ROUNDING_EDGE", "x": 499.96, "y": 0.0,
                "heading": 0.0, "speed": 0.0,
            }],
        }, horizon_s=1.0)
        edge_skip = next(
            candidate for candidate in just_below["candidates"]
            if candidate["action"] == "skip"
        )
        self.assertEqual(edge_skip["projected_minimum_separation_m"], 500.0)
        self.assertEqual(edge_skip["classification"], "unsafe")
        self.assertFalse(edge_skip["all_contacts_at_or_above_minimum"])

        underway = build_teacher_candidate_lookahead({
            "own_ship": {
                "x": 0.0, "y": 0.0, "heading": 0.0, "speed": 4.0,
                "target_speed": 4.0, "recovery_speed": 4.0,
            },
            "mission": {"x": 0.0, "y": 1000.0},
            "contacts": [],
        }, horizon_s=1.0)
        degrees = [
            candidate["degrees"] for candidate in underway["candidates"]
            if candidate["action"] == "alter_course"
        ]
        self.assertEqual(degrees, [-60.0, -45.0, -30.0, 30.0, 45.0, 60.0])


if __name__ == "__main__":
    unittest.main()
