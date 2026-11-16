import unittest

from bridge.candidate_actions import compute_candidate_actions


class CandidateActionsTest(unittest.TestCase):
    def test_stopped_ship_can_hold_or_resume(self):
        candidates = compute_candidate_actions({
            "own_ship": {
                "movement_status": "stalled",
                "stopped": True,
                "paused": False,
            }
        })
        self.assertEqual(
            [candidate["action"] for candidate in candidates],
            ["skip", "resume"],
        )

    def test_underway_ship_gets_navigation_candidates(self):
        candidates = compute_candidate_actions({
            "own_ship": {
                "movement_status": "underway",
                "stopped": False,
                "paused": False,
                "speed": 12.0,
                "target_speed": 14.0,
            },
            "contacts": [],
        })
        self.assertEqual(
            [candidate["action"] for candidate in candidates],
            ["maintain_course", "alter_course", "set_speed", "stop"],
        )
        self.assertEqual(candidates[2]["speed"], 14.0)

    def test_threat_bounds_turn_away_and_halves_target_speed(self):
        candidates = compute_candidate_actions({
            "own_ship": {
                "movement_status": "underway",
                "speed": 12.0,
                "target_speed": 14.0,
            },
            "contacts": [{
                "risk": "high",
                "sector": "port bow",
                "track_quality": "fresh",
            }],
        })
        self.assertEqual(candidates[1]["degrees_range"], {"min": 5.0, "max": 60.0})
        self.assertEqual(candidates[2]["speed"], 7.0)

    def test_stale_risk_does_not_constrain_maneuver(self):
        candidates = compute_candidate_actions({
            "own_ship": {
                "movement_status": "underway",
                "speed": 12.0,
                "target_speed": 14.0,
            },
            "contacts": [{
                "risk": "high",
                "sector": "starboard bow",
                "track_quality": "stale",
            }],
        })
        self.assertEqual(candidates[1]["degrees_range"], {"min": -60.0, "max": 60.0})
        self.assertEqual(candidates[2]["speed"], 14.0)


if __name__ == "__main__":
    unittest.main()
