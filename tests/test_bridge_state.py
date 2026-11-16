import unittest

from agents.agent_lookout import AgentLookout
from agents.agent_captain import AgentCaptain
from agents.schemas.lookout import LookoutAssessment
from bridge.state import BridgeSnapshot


class EncounterClassificationTests(unittest.TestCase):
    @staticmethod
    def enrich(own_x, own_y, own_heading, own_speed, x, y, heading, speed):
        return BridgeSnapshot._enrich_contact(
            {
                "position": {"x": own_x, "y": own_y},
                "heading": own_heading,
                "speed": own_speed,
            },
            {
                "name": "CONTACT",
                "nav": {"x": x, "y": y, "heading": heading, "speed": speed},
            },
        )

    def test_cycle_16_geometry_says_we_are_overtaking_fisher(self):
        contact = self.enrich(
            -844.5522662744419,
            -6950.196698259789,
            8.892548130231587,
            4.75,
            -993.77,
            -6399.98,
            17.99,
            5.0,
        )

        self.assertEqual(contact["sector"], "port bow")
        self.assertEqual(contact["encounter_type"], "we_are_overtaking_contact")

    def test_subcritical_cpa_is_critical_regardless_of_tcpa(self):
        cases = (
            5000,  # 500 s TCPA
            2500,  # 250 s TCPA
            1000,  # 100 s TCPA
        )
        for distance in cases:
            with self.subTest(distance=distance):
                contact = self.enrich(0, 0, 0, 5, 0, distance, 180, 5)
                self.assertEqual(contact["risk"], "critical")

    def test_contact_astern_and_closing_is_overtaking_us(self):
        contact = self.enrich(0, 0, 0, 5, 0, -100, 0, 7)

        self.assertEqual(contact["sector"], "astern")
        self.assertEqual(contact["encounter_type"], "contact_overtaking_us")

    def test_contact_ahead_and_slower_is_overtaken_by_us(self):
        contact = self.enrich(0, 0, 0, 7, 0, 100, 0, 5)

        self.assertEqual(contact["sector"], "ahead")
        self.assertEqual(contact["encounter_type"], "we_are_overtaking_contact")

    def test_stopped_contact_keeps_collision_projection_without_overtaking_label(self):
        contact = self.enrich(
            -11.7077, 544.5893, 345.9916, 1.54,
            -167.97, 626.82, 345.0, 0.0,
        )

        self.assertEqual(contact["sector"], "port bow")
        self.assertEqual(contact["encounter_type"], "stationary_contact")
        self.assertGreater(contact["closing_speed"], 0.0)
        self.assertIsNotNone(contact["tcpa_s"])

    def test_observed_separation_within_ten_metres_fails_mission(self):
        base = {
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0, "y": 0, "heading": 0, "speed": 0},
            "contacts": [],
            "minimum_range_contact": "CONTACT",
        }
        failure = BridgeSnapshot(1, 0.0, {
            **base, "minimum_observed_range": 9.9,
        }).state_for_agent()
        clear = BridgeSnapshot(2, 0.0, {
            **base, "minimum_observed_range": 10.1,
        }).state_for_agent()

        self.assertTrue(failure["collision"])
        self.assertTrue(failure["mission_failed"])
        self.assertEqual(failure["mission_failure_reason"], "collision")
        self.assertFalse(clear["collision"])
        self.assertFalse(clear["mission_failed"])

    def test_cycle_11_parallel_contact_retains_physical_tcpa_beyond_horizon(self):
        contact = self.enrich(
            -796.1855966080117,
            -6825.181639542073,
            21.99054337733715,
            5.0,
            -935.72,
            -6817.09,
            22.02,
            5.0,
        )

        self.assertEqual(contact["encounter_type"], "parallel")
        self.assertGreater(contact["tcpa_s"], 600.0)
        self.assertLess(contact["cpa_distance"], contact["range"])
        self.assertEqual(
            contact["projection_status"],
            "beyond_planning_horizon",
        )

    def test_cycle_16_ferry_return_projection_is_beyond_planning_horizon(self):
        contact = self.enrich(
            -688.9177395331135,
            -7449.831748678663,
            353.80042165407485,
            0.44,
            -1109.86,
            -3373.02,
            174.25,
            4.18,
        )

        self.assertEqual(contact["projection_status"], "beyond_planning_horizon")
        self.assertGreater(contact["tcpa_s"], 600.0)
        self.assertLess(contact["cpa_distance"], contact["range"])

    def test_intersecting_fifteen_degree_tracks_are_crossing_not_parallel(self):
        contact = self.enrich(
            0.0, -4820.225, 0.0, 5.0,
            -1262.07, -4710.21, 15.0, 5.0,
        )

        self.assertEqual(contact["encounter_type"], "crossing")
        self.assertIsNotNone(contact["track_intersection"])
        self.assertGreater(contact["tcpa_s"], 600.0)
        self.assertEqual(contact["projection_status"], "beyond_planning_horizon")
        self.assertEqual(contact["risk"], "medium")


class LookoutFilteringTests(unittest.TestCase):
    def setUp(self):
        self.lookout = AgentLookout(
            client=None,
            captain_queue=None,
            stale_after_s=5,
        )

    def test_stale_cycle_7_contacts_are_not_sent_to_model(self):
        snapshot = BridgeSnapshot(
            version=7,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {
                    "x": -551.0050898274329,
                    "y": -6339.4525895092465,
                    "heading": 302.0125432329587,
                    "speed": 5.0,
                },
                "contacts": [
                    {
                        "name": "INBOUND_2",
                        "age_s": 305.22604990005493,
                        "nav": {
                            "x": -786.05,
                            "y": -6454.97,
                            "heading": 22.98,
                            "speed": 5.0,
                        },
                    },
                    {
                        "name": "FISHER_1",
                        "age_s": 321.7358179092407,
                        "nav": {
                            "x": -993.77,
                            "y": -6399.98,
                            "heading": 17.99,
                            "speed": 5.0,
                        },
                    },
                ],
            },
        )

        report = self.lookout.build_watch_report(snapshot)

        self.assertEqual(report["contacts"], [])
        self.assertIn("2 distant or stale contact(s) suppressed", report["watch_note"])

    def test_priority_cannot_be_emergency_for_low_risk_contact(self):
        report = {"contacts": [{"name": "FAR", "risk": "low"}]}

        self.assertEqual(
            self.lookout._deterministic_priority(report),
            "caution",
        )

    def test_priority_is_clear_without_fresh_contacts(self):
        self.assertEqual(
            self.lookout._deterministic_priority({"contacts": []}),
            "clear",
        )

    def test_three_stable_same_direction_samples_are_parallel(self):
        track = [
            {
                "range": distance,
                "relative_bearing": bearing,
                "own_heading": 10.0,
                "contact_heading": 12.0,
            }
            for distance, bearing in [(205.0, 88.0), (202.0, 89.0), (200.0, 90.0)]
        ]

        self.assertTrue(self.lookout._is_parallel_track(track))

    def test_stable_track_does_not_erase_overtaking_encounter(self):
        snapshot = BridgeSnapshot(
            version=3,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0, "y": -423, "heading": 0, "speed": 3.93},
                "contacts": [{
                    "name": "IMAZU_TS1",
                    "age_s": 0.1,
                    "nav": {"x": 0, "y": -123, "heading": 0, "speed": 3.25},
                }],
            },
        )
        history = [
            {
                "version": index,
                "moos_time": float(index),
                "x": 0,
                "y": -500 + index,
                "heading": 0,
                "speed": 3.93,
                "contacts": [{
                    "name": "IMAZU_TS1",
                    "range": distance,
                    "relative_bearing": 0,
                    "heading": 0,
                    "age_s": 0.1,
                }],
            }
            for index, distance in enumerate((305, 303, 300), start=1)
        ]

        report = self.lookout.build_watch_report(snapshot, own_ship_history=history)

        self.assertEqual(
            report["contacts"][0]["encounter_type"],
            "we_are_overtaking_contact",
        )

    def test_high_risk_contact_is_ordered_before_low_risk_contact(self):
        contacts = [
            {"name": "SLOW", "risk": "low", "range": 250, "cpa_distance": 0},
            {"name": "CROSSING", "risk": "high", "range": 325,
             "cpa_distance": 14, "tcpa_s": 109},
        ]

        ordered = sorted(contacts, key=self.lookout._contact_priority)

        self.assertEqual(ordered[0]["name"], "CROSSING")

    def test_single_frame_cannot_be_stabilized_as_parallel(self):
        self.assertFalse(
            self.lookout._is_parallel_track(
                [{
                    "range": 200.0,
                    "relative_bearing": 90.0,
                    "own_heading": 10.0,
                    "contact_heading": 12.0,
                }]
            )
        )

    def test_tcpa_duration_uses_correct_units(self):
        self.assertEqual(
            self.lookout._format_duration(51506.41237102424),
            "14 h 18 min",
        )

    def test_head_on_cannot_be_described_as_overtaking(self):
        assessment = LookoutAssessment(
            priority="emergency",
            brief="FERRY_RETURN overtaking us head-on",
            decisive_contacts=[{
                "name": "FERRY_RETURN",
                "reason": "head-on overtaking with critical risk",
            }],
            situation="FERRY_RETURN is overtaking us head-on.",
            caveat="Insufficient history.",
        )
        report = {"contacts": [{
            "name": "FERRY_RETURN",
            "sector": "ahead",
            "encounter_type": "head_on",
            "risk": "critical",
            "closing_speed": 9.9,
            "cpa_distance": 32.2,
            "tcpa_display": "25.2 s",
        }]}

        corrected = self.lookout._enforce_encounter_semantics(assessment, report)

        combined = " ".join([
            corrected.brief,
            corrected.situation,
            corrected.decisive_contacts[0].reason,
        ]).lower()
        self.assertIn("head on", combined)
        self.assertNotIn("overtak", combined)

    def test_stationary_contact_cannot_be_described_as_overtaking_us(self):
        assessment = LookoutAssessment(
            priority="emergency",
            brief="IMAZU_TS1 overtaking us on the port bow",
            decisive_contacts=[{
                "name": "IMAZU_TS1",
                "reason": "Overtaking us with range decreasing",
            }],
            situation="IMAZU_TS1 is approaching us from the port bow.",
            caveat="Constant-velocity projection.",
        )
        report = {"contacts": [{
            "name": "IMAZU_TS1",
            "sector": "port bow",
            "encounter_type": "stationary_contact",
            "risk": "critical",
            "closing_speed": 1.03,
            "cpa_distance": 131.7,
            "tcpa_display": "76.4 s",
        }]}

        corrected = self.lookout._enforce_encounter_semantics(assessment, report)

        combined = " ".join([
            corrected.brief,
            corrected.situation,
            corrected.decisive_contacts[0].reason,
        ]).lower()
        self.assertIn("stationary contact", combined)
        self.assertIn("own ship closing", corrected.situation.lower())
        self.assertNotIn("overtak", combined)
        self.assertNotIn("approaching us", combined)


class CaptainStateTests(unittest.TestCase):
    def test_stop_validation_does_not_override_model_navigation_judgment(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        captain.stale_after_s = 5.0
        state = {
            "contacts": [{
                "name": "FERRY_RETURN",
                "risk": "low",
                "age_s": 0.2,
                "range": 4098.0,
                "cpa_distance": 7.4,
                "tcpa_s": 887.1,
            }],
            "control": {"paused": False},
        }

        validated = captain.validate_decision(
            {"action": "stop", "degrees": 0, "speed": 0,
             "reason": "Observed situation.", "auxiliary_commands": []}, state
        )
        self.assertEqual(validated["action"], "stop")

    def test_captain_state_has_one_navigation_and_action_location(self):
        action = {
            "action": "alter_course",
            "class": "maneuver",
            "speed": 5.0,
            "completion_radius": 3.0,
            "waypoints": [{"x": -488.85, "y": -6217.23}],
        }
        snapshot = BridgeSnapshot(
            version=9,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 45.0, "speed": 5.0},
                "mission_objective": {"x": 0.0, "y": -1000.0},
                "next_waypoint": {"x": 0.0, "y": -500.0},
                "temporary_waypoint": {"x": 25.0, "y": -250.0},
                "action_underway": True,
                "current_action": action,
            },
        )

        state = snapshot.captain_state_for_agent()

        self.assertNotIn("mission_navigation", state)
        self.assertNotIn("current_action", state)
        self.assertNotIn("control_state", state)
        self.assertNotIn("mission_navigation", state["own_ship"])
        self.assertNotIn("current_action", state["own_ship"])
        self.assertEqual(
            state["control"]["current_action"],
            {
                "action": "alter_course",
                "temporary_waypoint_target": [-488.85, -6217.23],
                "speed": 5.0,
            },
        )
        self.assertEqual(state["mission"]["objective"], {"x": 0.0, "y": -1000.0})
        self.assertEqual(state["mission"]["next_waypoint"], {"x": 0.0, "y": -500.0})
        self.assertEqual(
            state["mission"]["temporary_waypoint"],
            {"x": 25.0, "y": -250.0},
        )
        self.assertEqual(
            state["decision_context"]["primary_decision"],
            "supervise_active_maneuver",
        )
        self.assertEqual(
            state["mission"]["navigation"]["route_status"],
            "away_from_goal",
        )
        self.assertEqual(
            state["allowed_actions"],
            ["maintain_course", "alter_course", "rejoin_route", "set_speed", "stop", "resume", "skip"],
        )
        self.assertNotIn("candidate_actions", state)

    def test_temporary_maneuver_exposes_rejoin_action(self):
        snapshot = BridgeSnapshot(
            version=11,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 30.0, "speed": 5.0},
                "mission_objective": {"x": 0.0, "y": 500.0},
                "next_waypoint": {"x": 0.0, "y": 500.0},
                "temporary_waypoint": {"x": 112.5, "y": 194.9},
                "action_underway": True,
                "current_action": {
                    "action": "alter_course",
                    "class": "maneuver",
                    "waypoints": [{"x": 112.5, "y": 194.9}],
                },
                "contacts": [{
                    "name": "CONTACT",
                    "age_s": 0.1,
                    "nav": {"x": 200.0, "y": 300.0, "heading": 270.0, "speed": 5.0},
                }],
            },
        )

        self.assertIn(
            "rejoin_route",
            snapshot.captain_state_for_agent()["allowed_actions"],
        )

    def test_rejoin_route_restores_active_mission_route(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        state = {
            "current_maneuver": {"action": "alter_course"},
            "control": {"paused": False, "action_underway": True},
        }
        decision = captain.validate_decision(
            {"action": "rejoin_route", "reason": "Conflict has cleared."}, state
        )
        command = captain.command_from_decision(decision, state)
        self.assertEqual(command["class"], "maneuver")
        self.assertEqual(command["action"], "rejoin_route")

    def test_navigation_and_horn_become_one_multi_command_envelope(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        command = captain.command_from_decision(
            {
                "action": "alter_course",
                "degrees": 20.0,
                "distance_m": 200.0,
                "auxiliary_commands": [{
                    "type": "sound_horn",
                    "signal": "altering_course_to_starboard",
                }],
            },
            {
                "control": {"paused": False, "action_underway": False},
                "contacts": [{
                    "name": "CONTACT",
                    "risk": "high",
                    "closing_speed": 2.0,
                    "tcpa_s": 60.0,
                }],
            },
        )

        self.assertEqual(command["type"], "commands")
        self.assertEqual(
            [item["action"] for item in command["commands"]],
            ["alter_course", "sound_horn"],
        )

    def test_vhf_can_be_sent_without_a_navigation_command(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        command = captain.command_from_decision(
            {
                "action": "maintain_course",
                "auxiliary_commands": [{
                    "type": "send_vhf",
                    "channel": 16,
                    "message": "Vessel on my starboard bow, state your intentions.",
                }],
            },
            {"control": {"paused": False, "action_underway": False}},
        )

        self.assertEqual(command["type"], "commands")
        self.assertEqual(len(command["commands"]), 1)
        self.assertEqual(command["commands"][0]["action"], "send_vhf")

    def test_captain_schema_rejects_duplicate_auxiliary_types(self):
        from pydantic import ValidationError
        from agents.schemas.captain import CaptainDecision

        with self.assertRaises(ValidationError):
            CaptainDecision.model_validate({
                "action": "maintain_course",
                "degrees": 0,
                "speed": 0,
                "reason": "Monitor traffic.",
                "auxiliary_commands": [
                    {"type": "send_vhf", "channel": 16, "message": "One"},
                    {"type": "send_vhf", "channel": 16, "message": "Two"},
                ],
            })

    def test_captain_schema_requires_horn_to_match_course_change(self):
        from pydantic import ValidationError
        from agents.schemas.captain import CaptainDecision

        with self.assertRaises(ValidationError):
            CaptainDecision.model_validate({
                "action": "alter_course",
                "degrees": -20,
                "speed": 0,
                "reason": "Alter to port.",
                "auxiliary_commands": [{
                    "type": "sound_horn",
                    "signal": "altering_course_to_starboard",
                }],
            })

    def test_idle_captain_gets_signed_alter_course_not_generic_turns(self):
        snapshot = BridgeSnapshot(
            version=10,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 5.0},
            },
        )

        state = snapshot.captain_state_for_agent()

        self.assertIn("alter_course", state["allowed_actions"])
        self.assertNotIn("turn_starboard", state["allowed_actions"])
        self.assertNotIn("turn_port", state["allowed_actions"])
        self.assertNotIn("candidate_actions", state)

    def test_bridge_supplied_legacy_turn_candidates_are_removed(self):
        state = BridgeSnapshot(
            version=10,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 5.0},
                "allowed_actions": ["maintain_course", "turn_port", "turn_starboard"],
                "candidate_actions": [
                    {"action": "maintain_course"},
                    {"action": "turn_port", "degrees": 30.0},
                    {"action": "turn_starboard", "degrees": 30.0},
                ],
            },
        ).state_for_agent()

        self.assertEqual(state["allowed_actions"], [
            "maintain_course", "alter_course", "set_speed", "stop",
            "resume", "skip",
        ])
        self.assertNotIn("candidate_actions", state)

    def test_alter_course_is_not_candidate_limited_close_to_next_waypoint(self):
        snapshot = BridgeSnapshot(
            version=10,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 5.0},
                "next_waypoint": {"x": 0.0, "y": 449.0},
            },
        )

        state = snapshot.captain_state_for_agent()

        self.assertIn("alter_course", state["allowed_actions"])
        self.assertNotIn("candidate_actions", state)

    def test_alter_course_remains_available_with_a_full_leg(self):
        snapshot = BridgeSnapshot(
            version=10,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 5.0},
                "next_waypoint": {"x": 0.0, "y": 450.0},
            },
        )

        state = snapshot.captain_state_for_agent()

        self.assertIn("alter_course", state["allowed_actions"])

    def test_paused_vessel_exposes_safe_hold_and_resume(self):
        snapshot = BridgeSnapshot(
            version=11,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0},
                "paused": True,
                "current_action": {"action": "alter_course"},
            },
        )

        state = snapshot.state_for_agent()
        captain_state = snapshot.captain_state_for_agent()

        self.assertEqual(state["allowed_actions"], ["maintain_course", "alter_course", "set_speed", "stop", "resume", "skip"])
        self.assertNotIn("candidate_actions", state)
        self.assertEqual(
            captain_state["allowed_actions"], ["maintain_course", "alter_course", "set_speed", "stop", "resume", "skip"]
        )
        self.assertNotIn("candidate_actions", captain_state)
        self.assertEqual(
            captain_state["decision_context"],
            {
                "primary_decision": "resume_stopped_vessel",
                "default_when_clear": "resume",
                "hold_only_if": "specific_observed_hazard_makes_resume_unsafe",
                "available_outcomes": ["resume", "skip_temporary_safety_hold"],
            },
        )

    def test_paused_vessel_skip_validates_and_emits_no_command(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        state = BridgeSnapshot(
            version=13,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0},
                "paused": True,
            },
        ).state_for_agent()

        validated = captain.validate_decision(
            {
                "action": "skip",
                "degrees": 0.0,
                "speed": 0.0,
                "reason": "Remain stopped for another observation cycle.",
                "auxiliary_commands": [],
            },
            state,
        )

        self.assertEqual(validated["action"], "skip")
        self.assertIsNone(captain.command_from_decision(validated, state))

    def test_signed_course_change_becomes_one_bounded_maneuver(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        state = BridgeSnapshot(
            version=10,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 5.0},
            },
        ).state_for_agent()

        validated = captain.validate_decision(
            {
                "action": "alter_course",
                "degrees": -12.0,
                "reason": "Small port correction.",
            },
            state,
        )
        command = captain.command_from_decision(validated, state)

        self.assertEqual(command["action"], "alter_course")
        self.assertEqual(command["degrees"], -12.0)
        self.assertEqual(command["distance_m"], 500.0)

    def test_set_speed_accepts_any_positive_speed(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        state = BridgeSnapshot(
            version=11,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 5.0},
            },
        ).state_for_agent()

        target = 7.25
        validated = captain.validate_decision(
            {
                "action": "set_speed",
                "degrees": 0.0,
                "speed": target,
                "reason": "Adjust tactical speed.",
            },
            state,
        )
        command = captain.command_from_decision(validated, state)
        self.assertEqual(command["action"], "set_speed")
        self.assertEqual(command["speed"], target)

        second = captain.validate_decision({
            "action": "set_speed", "degrees": 0.0, "speed": target + 1.0,
            "reason": "Choose another positive target.",
        }, state)
        self.assertEqual(second["speed"], target + 1.0)

    def test_speed_candidate_uses_helm_target_units_and_restores_cruise(self):
        slowed = BridgeSnapshot(
            version=12,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.5},
                "target_speed": 2.5,
                "recovery_speed": 20.0,
            },
        ).captain_state_for_agent("clear")

        self.assertEqual(slowed["control"]["target_speed"], 2.5)
        self.assertEqual(slowed["control"]["recovery_speed"], 20.0)
        self.assertTrue(slowed["control"]["reduced_speed"])
        self.assertTrue(slowed["speed_control"]["recovery_required"])
        self.assertEqual(
            slowed["decision_context"]["primary_decision"],
            "restore_normal_cruise_speed",
        )
        self.assertIn("set_speed", slowed["allowed_actions"])
        self.assertNotIn("candidate_actions", slowed)

    def test_resume_recovers_unpaused_stall(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        state = BridgeSnapshot(
            version=12,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0},
                "paused": False,
                "recovery_speed": 5.0,
            },
        ).state_for_agent()

        command = captain.command_from_decision(
            {"action": "resume", "degrees": 0.0, "speed": 0.0},
            state,
        )

        self.assertEqual(command["action"], "resume")

    def test_uncommanded_stop_is_exposed_as_stalled(self):
        snapshot = BridgeSnapshot(
            version=13,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0},
                "paused": False,
                "recovery_speed": 5.0,
            },
        )

        bridge_state = snapshot.state_for_agent()
        state = snapshot.captain_state_for_agent()

        self.assertEqual(bridge_state["movement_status"], "stalled")
        self.assertTrue(bridge_state["stopped"])
        self.assertEqual(state["control"]["movement_status"], "stalled")
        self.assertTrue(state["control"]["stopped"])
        self.assertFalse(state["control"]["paused"])
        self.assertEqual(state["allowed_actions"], ["maintain_course", "alter_course", "set_speed", "stop", "resume", "skip"])
        self.assertNotIn("candidate_actions", state)

    def test_alter_course_does_not_copy_measured_speed(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        state = BridgeSnapshot(
            version=14,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.21},
            },
        ).state_for_agent()

        validated = captain.validate_decision(
            {"action": "alter_course", "degrees": 10.0, "reason": "Avoid."},
            state,
        )
        command = captain.command_from_decision(validated, state)

        self.assertEqual(command["speed"], 0.0)

    def test_diverging_traffic_requires_cruise_recovery(self):
        snapshot = BridgeSnapshot(
            version=15,
            received_at=0.0,
            _raw_state={
                "vehicle": "LLM_SHIP",
                "nav": {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 2.0},
                "target_speed": 5.0,
                "recovery_speed": 20.0,
                "contacts": [{
                    "name": "OPENING",
                    "nav": {"x": 0.0, "y": -100.0, "heading": 180.0, "speed": 3.0},
                }],
            },
        )
        state = snapshot.captain_state_for_agent("caution")
        self.assertTrue(state["speed_control"]["recovery_required"])
        self.assertIn("set_speed", state["allowed_actions"])
        self.assertNotIn("candidate_actions", state)

    def test_captain_can_replace_course_command_during_active_maneuver(self):
        captain = AgentCaptain.__new__(AgentCaptain)
        validated = captain.validate_decision(
            {"action": "alter_course", "degrees": 20.0, "speed": 0.0,
             "reason": "Another relative turn.", "auxiliary_commands": []},
            {"action_underway": True,
             "temporary_waypoint": {"x": 50.0, "y": 100.0}, "contacts": []},
        )
        self.assertEqual(validated["action"], "alter_course")
        self.assertEqual(validated["degrees"], 20.0)
        self.assertEqual(validated["distance_m"], 500.0)

if __name__ == "__main__":
    unittest.main()
