import unittest

from sft.strip_teacher_leakage import sanitize_rows


class StripTeacherLeakageTests(unittest.TestCase):
    def test_removes_labels_constraints_and_regenerates_narrative(self):
        rows = [{
            "id": "one", "cycle_id": 1, "source_file": "episode.jsonl",
            "state": {
                "observed_at_s": 10,
                "own_ship": {"name": "OWN", "x": 0, "y": 0, "heading": 0, "speed": 5},
                "mission": {"x": 0, "y": 1000},
                "contacts": [{
                    "name": "TS1", "range_m": 500, "relative_bearing_deg": 0,
                    "heading": 180, "speed": 5, "closing_speed": 10,
                    "projection_status": "valid", "cpa_distance_m": 50,
                    "tcpa_s": 50, "track_quality": "fresh",
                    "encounter_type": "head_on", "risk": "critical",
                    "own_role": "both_give_way", "standing_rules": [2, 5, 6, 7],
                    "active_encounter_rules": [8, 14],
                }],
                "allowed_actions": ["alter_course"],
                "candidate_actions": [{"action": "alter_course", "degrees_range": {"min": 5, "max": 30}}],
            },
            "narrative": "AUTOMATED TRAFFIC ASSESSMENT: alter to starboard",
        }]

        clean = sanitize_rows(rows)[0]
        contact = clean["state"]["contacts"][0]
        self.assertNotIn("allowed_actions", clean["state"])
        self.assertNotIn("candidate_actions", clean["state"])
        for field in ("encounter_type", "risk", "own_role", "standing_rules", "active_encounter_rules"):
            self.assertNotIn(field, contact)
        self.assertNotIn("AUTOMATED TRAFFIC ASSESSMENT", clean["narrative"])
        self.assertNotIn("starboard", clean["narrative"].lower())
        self.assertIn(
            "Projected CPA is 50 metres with TCPA 50.0 seconds",
            clean["narrative"],
        )


if __name__ == "__main__":
    unittest.main()
