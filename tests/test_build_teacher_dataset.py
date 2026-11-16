import json
import math
from pathlib import Path
import tempfile
import unittest

from tools.build_teacher_dataset import audit_quality


class BuildTeacherDatasetTests(unittest.TestCase):
    def write_audit(self, rows):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "audit.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return path

    def test_accepts_completed_safe_mission(self):
        path = self.write_audit([
            {"event": "bridge_snapshot", "state": {"minimum_observed_range": 125}},
            {"event": "mission_complete"},
        ])
        self.assertEqual(audit_quality(path, 50), (True, "accepted", 125))

    def test_rejects_collision_unfinished_and_close_mission(self):
        collision = self.write_audit([{"event": "mission_failed", "minimum_observed_range_m": 20}])
        unfinished = self.write_audit([{"event": "bridge_snapshot", "state": {}}])
        close = self.write_audit([
            {"event": "bridge_snapshot", "state": {"minimum_observed_range": 40}},
            {"event": "mission_complete"},
        ])
        self.assertEqual(audit_quality(collision, 50)[1], "mission_failed")
        self.assertEqual(audit_quality(unfinished, 50)[1], "unfinished")
        self.assertEqual(audit_quality(close, 50)[1], "minimum_range")

    def test_rejects_completed_mission_without_auditable_clearance(self):
        missing = self.write_audit([{"event": "mission_complete"}])
        malformed = self.write_audit([
            {"event": "bridge_snapshot", "state": {"minimum_observed_range": 125}},
            {"event": "mission_complete"},
        ])
        malformed.write_text(malformed.read_text() + "not-json\n", encoding="utf-8")
        self.assertEqual(audit_quality(missing, 50)[1], "missing_minimum_range")
        self.assertEqual(audit_quality(malformed, 50)[1], "malformed_json")

    def test_rejects_nonfinite_or_negative_clearance(self):
        for observed in (math.nan, math.inf, -1):
            path = self.write_audit([
                {"event": "bridge_snapshot", "state": {
                    "minimum_observed_range": observed,
                }},
                {"event": "mission_complete"},
            ])
            self.assertEqual(audit_quality(path, 50)[1], "invalid_minimum_range")
