import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sft.check_imazu_smoke import main


class CheckImazuSmokeTest(unittest.TestCase):
    def write_case(self, root: Path, case: int, clearance: float) -> None:
        directory = root / f"case_{case:02d}"
        directory.mkdir()
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "result": "complete",
                    "minimum_observed_range_m": clearance,
                    "actions": {"maintain_course": 5, "alter_course": 1},
                }
            ),
            encoding="utf-8",
        )

    def test_rejects_completed_case_below_clearance_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_case(root, 13, 499.9)
            with patch.object(sys, "argv", ["check_imazu_smoke", str(root), "--cases", "13"]):
                with self.assertRaises(SystemExit):
                    main()
            report = json.loads((root / "smoke_gate.json").read_text())
            self.assertFalse(report["passed"])
            self.assertIn("minimum clearance=499.900 m < 500.000 m", report["failures"][0])

    def test_accepts_completed_case_at_clearance_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_case(root, 13, 500.0)
            with patch.object(sys, "argv", ["check_imazu_smoke", str(root), "--cases", "13"]):
                main()
            report = json.loads((root / "smoke_gate.json").read_text())
            self.assertTrue(report["passed"])


if __name__ == "__main__":
    unittest.main()
