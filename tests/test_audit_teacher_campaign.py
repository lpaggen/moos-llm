import json
from pathlib import Path
import tempfile
import unittest

from tools.audit_teacher_campaign import audit_campaign


class AuditTeacherCampaignTests(unittest.TestCase):
    def test_reports_strict_acceptance_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "campaign.json").write_text(json.dumps({
                "runs": 2, "start_seed": 10,
            }))
            (root / "results.jsonl").write_text(
                json.dumps({"seed": 10}) + "\n" + json.dumps({"seed": 11}) + "\n"
            )
            for seed, clearance in ((10, 500.0), (11, 499.9)):
                rows = [
                    {"event": "bridge_snapshot", "state": {
                        "minimum_observed_range": clearance,
                    }},
                    {"event": "mission_complete"},
                ]
                (root / f"seed_{seed}.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in rows)
                )

            report = audit_campaign(root, 500.0, require_complete=True)

        self.assertEqual(report["accepted_missions"], 1)
        self.assertEqual(report["reasons"], {"accepted": 1, "minimum_range": 1})
        self.assertEqual(report["integrity"], {
            "missing_audits": [], "missing_results": [],
            "unexpected_audits": [], "unexpected_results": [],
        })

