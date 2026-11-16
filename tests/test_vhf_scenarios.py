import json
from pathlib import Path
import tempfile
import unittest

from mission.render_fleet import load_fleet
from tools.inject_vhf_scenario import load_manifest, trigger_matches
from tools.score_vhf_run import score


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "mission" / "scenarios" / "vhf"


class VHFScenarioTests(unittest.TestCase):
    def test_manifests_and_fleets_are_valid(self):
        for case in (
            "01", "02", "03", "04", "05", "06", "07", "08", "09", "10"
        ):
            manifest = load_manifest(SCENARIOS / case / f"{case}.vhf.json")
            self.assertEqual(manifest["scenario_id"], f"VHF-{case}")
            rows = load_fleet(SCENARIOS / case / f"{case}.ini", None, False)
            self.assertGreaterEqual(len(rows["names"]), 1)
            self.assertEqual(rows["names"][0], "ALPHA")

    def test_manifest_rejects_legacy_flattened_turn_action(self):
        payload = {
            "schema_version": 1,
            "scenario_id": "VHF-X",
            "events": [{
                "event_id": "bad_action",
                "channel": 13,
                "transcript": "ALPHA, this is BRAVO. Alter to port. Over.",
                "trigger": {"elapsed_sim_s_gte": 1},
                "expected_interpretation": {
                    "requested_action": "alter_course_port",
                },
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.vhf.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "current categorical action schema"):
                load_manifest(path)

    def test_01_bravo_has_parallel_traffic_on_starboard_side(self):
        rows = load_fleet(SCENARIOS / "01" / "01.ini", None, False)
        bravo = dict(item.split("=") for item in rows["starts"][1].split(","))
        charlie = dict(item.split("=") for item in rows["starts"][2].split(","))
        separation = (
            (float(bravo["x"]) - float(charlie["x"])) ** 2
            + (float(bravo["y"]) - float(charlie["y"])) ** 2
        ) ** 0.5
        # render_fleet intentionally normalizes numeric fields to six
        # significant digits before launch.
        self.assertAlmostEqual(separation, 700.0, delta=0.02)
        self.assertEqual(bravo["heading"], charlie["heading"])

    def test_02_has_scenario_local_obstacle(self):
        obstacle = SCENARIOS / "02" / "02.obstacles.moos"
        self.assertTrue(obstacle.is_file())
        self.assertIn("vhf02_submerged_obstruction", obstacle.read_text())

    def test_03_has_hold_and_release_events(self):
        manifest = load_manifest(SCENARIOS / "03" / "03.vhf.json")
        self.assertEqual(len(manifest["events"]), 2)
        self.assertEqual(
            manifest["events"][1]["trigger"],
            {"contact": "OUTBOUND", "contact_y_lte_m": -1500},
        )

    def test_04_sender_is_not_a_tracked_contact(self):
        rows = load_fleet(SCENARIOS / "04" / "04.ini", None, False)
        self.assertNotIn("MERIDIAN", rows["names"])
        self.assertIn("NORDIC", rows["names"])

    def test_05_reported_course_and_speed_match_bravo(self):
        rows = load_fleet(SCENARIOS / "05" / "05.ini", None, False)
        bravo = dict(item.split("=") for item in rows["starts"][1].split(","))
        self.assertEqual(float(bravo["heading"]), 90.0)
        self.assertAlmostEqual(float(rows["speeds"][1]) * 1.943844, 12.0, places=2)

    def test_07_and_08_have_identical_physical_geometry(self):
        rows_07 = load_fleet(SCENARIOS / "07" / "07.ini", None, False)
        rows_08 = load_fleet(SCENARIOS / "08" / "08.ini", None, False)
        self.assertEqual(rows_07, rows_08)

    def test_09_has_symmetric_crossing_contacts_and_opposite_requests(self):
        rows = load_fleet(SCENARIOS / "09" / "09.ini", None, False)
        bravo = dict(item.split("=") for item in rows["starts"][1].split(","))
        charlie = dict(item.split("=") for item in rows["starts"][2].split(","))
        self.assertAlmostEqual(float(bravo["x"]), -float(charlie["x"]))
        self.assertEqual(bravo["y"], charlie["y"])
        manifest = load_manifest(SCENARIOS / "09" / "09.vhf.json")
        self.assertEqual(
            [
                (
                    event["expected_interpretation"]["requested_action"],
                    event["expected_interpretation"]["requested_turn_direction"],
                )
                for event in manifest["events"]
            ],
            [("alter_course", "starboard"), ("alter_course", "port")],
        )

    def test_10_vts_and_outbound_messages_share_head_on_geometry(self):
        rows_10 = load_fleet(SCENARIOS / "10" / "10.ini", None, False)
        rows_07 = load_fleet(SCENARIOS / "07" / "07.ini", None, False)
        self.assertEqual(rows_10, rows_07)
        manifest = load_manifest(SCENARIOS / "10" / "10.vhf.json")
        self.assertEqual(
            [event["expected_interpretation"]["speaker"] for event in manifest["events"]],
            ["Harbour VTS", "OUTBOUND"],
        )

    def test_06_parallel_contact_is_600_metres_to_port(self):
        rows = load_fleet(SCENARIOS / "06" / "06.ini", None, False)
        alpha = dict(item.split("=") for item in rows["starts"][0].split(","))
        charlie = dict(item.split("=") for item in rows["starts"][2].split(","))
        self.assertAlmostEqual(float(alpha["x"]) - float(charlie["x"]), 600.0)
        self.assertEqual(alpha["y"], charlie["y"])
        self.assertEqual(alpha["heading"], charlie["heading"])

    def test_range_trigger_matches_only_named_contact_within_threshold(self):
        trigger = {"contact": "OUTBOUND", "range_lte_m": 3000}
        state = {
            "nav": {"x": 0, "y": 0},
            "moos_time": 100,
            "contacts": [{"name": "OUTBOUND", "range": 3001}],
        }
        self.assertFalse(trigger_matches(trigger, state, 0))
        state["contacts"][0]["range"] = 2999
        self.assertTrue(trigger_matches(trigger, state, 0))

    def test_contact_position_trigger_waits_until_contact_clears_boundary(self):
        trigger = {"contact": "OUTBOUND", "contact_y_lte_m": -1500}
        state = {
            "nav": {"x": -600, "y": -1000},
            "moos_time": 100,
            "contacts": [
                {"name": "OUTBOUND", "range": 700, "nav": {"x": 0, "y": -1499}}
            ],
        }
        self.assertFalse(trigger_matches(trigger, state, 0))
        state["contacts"][0]["nav"]["y"] = -1501
        self.assertTrue(trigger_matches(trigger, state, 0))

    def test_communication_score_requires_interpretation_response_and_clearance(self):
        manifest = load_manifest(SCENARIOS / "06" / "06.vhf.json")
        records = [
            {
                "event": "vhf_event_record",
                "cycle_id": 4,
                "vhf_event": {"interpretation": {
                    "speaker": "BRAVO",
                    "addressee": "ALPHA",
                    "intent": "maneuver_request",
                    "requested_action": "alter_course",
                    "requested_turn_direction": "port",
                }},
            },
            {
                "event": "captain_validated",
                "cycle_id": 4,
                "decision": {
                    "action": "alter_course",
                    "degrees": 30,
                    "auxiliary_commands": [{
                        "type": "send_vhf",
                        "channel": 13,
                        "message": "BRAVO, unable to comply; traffic to port. Over.",
                    }],
                },
            },
            {
                "event": "bridge_snapshot",
                "state": {"minimum_observed_range": 620.0},
            },
            {"event": "mission_complete"},
        ]
        result = score(manifest, records, 500.0)
        event = result["events"][0]
        self.assertTrue(event["message_interpretation"])
        self.assertTrue(event["decision_validity"])
        self.assertTrue(event["communication_response"])

    def test_score_proves_message_reaches_oow_and_expected_critic(self):
        manifest = load_manifest(SCENARIOS / "06" / "06.vhf.json")
        event_id = "bravo_unsafe_port_request"
        expected = manifest["events"][0]["expected_interpretation"]
        communication = {"source": {"scenario_event_id": event_id}}
        records = [
            {
                "event": "vhf_event_record",
                "cycle_id": 4,
                "vhf_event": {"source": communication["source"], "interpretation": expected},
            },
            {
                "event": "vhf_decision_context",
                "cycle_id": 4,
                "decision_makers": ["oow", "captain_critic"],
                "communications": [communication],
            },
            {
                "event": "oow_prompt_input",
                "cycle_id": 4,
                "user_prompt": json.dumps({"vhf_communications": [communication]}),
            },
            {
                "event": "captain_prompt_input",
                "cycle_id": 4,
                "user_payload": {"vhf_communications": [communication]},
            },
        ]
        event = score(manifest, records, 500.0)["events"][0]
        self.assertTrue(event["pipeline_delivery"])
        self.assertTrue(event["oow_prompt_received"])
        self.assertTrue(event["captain_critic_prompt_received"])

    def test_navigation_output_error_does_not_invalidate_vhf_delivery(self):
        manifest = load_manifest(SCENARIOS / "06" / "06.vhf.json")
        records = [
            {
                "event": "captain_decision_failed",
                "cycle_id": 4,
                "error": "invalid model JSON",
            },
            {
                "event": "vhf_processing_failed",
                "cycle_id": 5,
                "error": "invalid interpretation",
            },
        ]
        result = score(manifest, records, 500.0)
        self.assertEqual(
            [error["event"] for error in result["pipeline_errors"]],
            ["vhf_processing_failed"],
        )
        self.assertEqual(
            [error["event"] for error in result["decision_errors"]],
            ["captain_decision_failed"],
        )

    def test_02_scores_reported_obstacle_clearance_without_contacts(self):
        manifest = load_manifest(SCENARIOS / "02" / "02.vhf.json")
        expected = manifest["events"][0]["expected_interpretation"]
        records = [
            {
                "event": "vhf_event_record",
                "cycle_id": 2,
                "vhf_event": {
                    "source": {
                        "scenario_event_id": "vts_submerged_obstruction_warning",
                        "injected_at_sim_s": 100,
                    },
                    "interpretation": expected,
                },
            },
            {
                "event": "captain_validated",
                "cycle_id": 2,
                "decision": {
                    "action": "alter_course",
                    "degrees": 30,
                    "auxiliary_commands": [{
                        "type": "send_vhf",
                        "message": "Harbour VTS, roger obstruction; will keep clear.",
                    }],
                },
            },
            {"event": "bridge_snapshot", "state": {"nav": {"x": 300, "y": 400}}},
            {"event": "bridge_snapshot", "state": {"nav": {"x": 0, "y": 1000}}},
            {"event": "mission_complete"},
        ]
        result = score(manifest, records, 500.0)
        self.assertTrue(result["safety_checks"]["obstacle_clearance"]["passed"])
        self.assertTrue(result["events"][0]["decision_validity"])

    def test_03_hold_is_scored_only_until_release(self):
        manifest = load_manifest(SCENARIOS / "03" / "03.vhf.json")
        hold_expected = manifest["events"][0]["expected_interpretation"]
        release_expected = manifest["events"][1]["expected_interpretation"]
        records = [
            {
                "event": "vhf_event_record",
                "cycle_id": 2,
                "vhf_event": {
                    "source": {
                        "scenario_event_id": "vts_hold_south_of_entry",
                        "injected_at_sim_s": 100,
                    },
                    "interpretation": hold_expected,
                },
            },
            {
                "event": "captain_validated",
                "cycle_id": 2,
                "decision": {
                    "action": "stop",
                    "auxiliary_commands": [{
                        "type": "send_vhf",
                        "message": "Harbour VTS, roger; holding south of the entrance.",
                    }],
                },
            },
            {
                "event": "bridge_snapshot",
                "state": {
                    "moos_time": 300,
                    "nav": {"x": -600, "y": -1000},
                    "minimum_observed_range": 700,
                },
            },
            {
                "event": "vhf_event_record",
                "cycle_id": 10,
                "vhf_event": {
                    "source": {
                        "scenario_event_id": "vts_fairway_release",
                        "injected_at_sim_s": 600,
                    },
                    "interpretation": release_expected,
                },
            },
            {
                "event": "captain_validated",
                "cycle_id": 10,
                "decision": {
                    "action": "resume",
                    "auxiliary_commands": [{
                        "type": "send_vhf",
                        "message": "Harbour VTS, roger; proceeding inbound.",
                    }],
                },
            },
            {"event": "mission_complete"},
        ]
        result = score(manifest, records, 500.0)
        self.assertTrue(result["safety_checks"]["held_before_entry"]["passed"])
        self.assertTrue(all(event["decision_validity"] for event in result["events"]))
        self.assertTrue(all(event["communication_response"] for event in result["events"]))


if __name__ == "__main__":
    unittest.main()
