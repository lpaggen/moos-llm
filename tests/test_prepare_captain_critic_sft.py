import argparse
import json
import tempfile
import unittest
from pathlib import Path

from sft.prepare_captain_critic_sft import (
    convert,
    counterfactual,
    request_record,
    validate_prepared,
    write_jsonl,
)


def teacher_result(custom_id: str, verdict: str) -> dict:
    decision = {
        "oow_verdict": verdict,
        "oow_critique": "No material defect identified" if verdict == "approve" else "Unsafe proposal rejected",
        "controlling_contact": "none",
        "own_role": "unknown",
        "encounter_phase": "clear",
        "geometry_check": "The accepted course is clear.",
        "control": {"action": "maintain_course"},
        "reason": "Maintain the accepted safe control.",
        "evidence_analogy": "none",
        "evidence_difference": "none",
        "transferable_lesson": "none",
        "auxiliary_commands": [],
    }
    return {
        "custom_id": custom_id,
        "response": {
            "body": {
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(decision)}],
                }]
            }
        },
        "error": None,
    }


class CaptainCriticSFTTests(unittest.TestCase):
    def test_counterfactual_never_repeats_the_gold_control(self):
        gold = {
            "action": "alter_course",
            "degrees": 30.0,
            "speed": 0.0,
            "reason": "accepted",
            "auxiliary_commands": [],
        }
        value = counterfactual(gold, {"contacts": []})
        self.assertEqual(value["action"], "alter_course")
        self.assertEqual(value["degrees"], -30.0)

    def test_request_uses_responses_api_and_requested_reasoning(self):
        row = request_record("example", "gpt-6-luna", "high", "system", {}, 2048)
        self.assertEqual(row["url"], "/v1/responses")
        self.assertEqual(row["body"]["reasoning"], {"effort": "high"})
        self.assertTrue(row["body"]["text"]["format"]["strict"])

    def test_validation_and_conversion_keep_only_contract_matching_labels(self):
        gold = {
            "action": "maintain_course",
            "degrees": 0.0,
            "speed": 0.0,
            "reason": "accepted",
            "auxiliary_commands": [],
        }
        bad = {
            "action": "alter_course",
            "degrees": 30.0,
            "speed": 0.0,
            "reason": "proposal",
            "auxiliary_commands": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requests = []
            manifest = []
            for custom_id, variant, proposal in (
                ("gold", "gold", gold),
                ("counterfactual", "counterfactual", bad),
            ):
                payload = {
                    "bridge_state": {},
                    "oow_proposal": proposal,
                    "decision_contract": {
                        "consequence_assessment": (
                            "Treat 500 metres as the hard minimum projected separation."
                        )
                    },
                    "candidate_action_consequences": {
                        "thresholds": {"hard_minimum_separation_m": 500.0},
                        "candidates": [{
                            "action": proposal["action"],
                            "degrees": proposal["degrees"],
                            "speed_mps": proposal["speed"],
                        }],
                    },
                }
                requests.append(
                    request_record(custom_id, "gpt-6-luna", "high", "system", payload, 2048)
                )
                manifest.append({
                    "custom_id": custom_id,
                    "variant": variant,
                    "split": "train",
                    "example_id": "source::decision::1",
                    "source_file": "source.jsonl",
                    "runtime_cycle_id": 1,
                    "gold_control": gold,
                    "oow_proposal": proposal,
                    "system_prompt": "system",
                    "user_payload": payload,
                })
            write_jsonl(root / "batch_input_001.jsonl", requests)
            write_jsonl(root / "manifest.jsonl", manifest)
            args = argparse.Namespace(
                work_dir=root, seed=7, organization_token_limit=20_000_000
            )
            validate_prepared(args)
            write_jsonl(
                root / "batch_output_001.jsonl",
                [teacher_result("gold", "approve"), teacher_result("counterfactual", "reject")],
            )
            convert(args)
            summary = json.loads((root / "conversion_summary.json").read_text())
            self.assertEqual(summary["accepted"]["train"], 2)
            self.assertEqual(summary["train_balanced"], 2)
            self.assertEqual(summary["rejected"], 0)


if __name__ == "__main__":
    unittest.main()
