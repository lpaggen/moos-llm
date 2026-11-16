import copy
import unittest

from bridge.observations import apply_encounter_persistence, compact_state
from bridge.narratives import (
    continues_sequence,
    render_with_history,
    temporal_developments,
)


def state(at, speed, contacts, movement="underway", x=0):
    return {
        "observed_at_s": at,
        "own_ship": {
            "name": "LLM_SHIP",
            "x": x,
            "y": 0,
            "heading": 10,
            "speed": speed,
            "movement_status": movement,
        },
        "mission": {"x": 100, "y": 0},
        "contacts": contacts,
    }


def contact(name, range_m, bearing, projection="valid", cpa=100, tcpa=30, age=0):
    result = {
        "name": name,
        "range_m": range_m,
        "relative_bearing_deg": bearing,
        "closing_speed": 2,
        "heading": 180,
        "speed": 3,
        "projection_status": projection,
        "age_s": age,
    }
    if projection == "valid":
        result.update(cpa_distance_m=cpa, tcpa_s=tcpa)
    return result


class TemporalNarrativeTests(unittest.TestCase):
    def test_renders_elapsed_time_and_factual_contact_deltas(self):
        old = state(10, 3.72, [contact("TS1", 346, 327.2, cpa=129, tcpa=48.9)])
        current = state(30, 1.89, [contact("TS1", 220, 280.0, cpa=112, tcpa=39.7)])

        narrative = render_with_history([old, current])

        self.assertIn("CURRENT BRIDGE STATE", narrative)
        self.assertIn("RECENT TRAFFIC DEVELOPMENTS (last 20.0 seconds)", narrative)
        self.assertIn("Own ship has slowed from 7.23 to 3.67 knots.", narrative)
        self.assertIn(
            "TS1 range decreased from 346 metres to 220 metres.",
            narrative,
        )
        self.assertIn("TS1 moved from the port bow sector to the port beam sector.", narrative)
        self.assertIn(
            "TS1 projected CPA decreased from 129 metres to 112 metres.",
            narrative,
        )
        self.assertIn("TS1 TCPA decreased from 48.9 seconds to 39.7 seconds.", narrative)
        self.assertNotIn("collision risk", narrative.lower())

    def test_omits_automated_traffic_assessment_from_bridge_fields(self):
        assessed = contact("TS1", 220, 5, cpa=80, tcpa=40)
        assessed.update({
            "encounter_type": "head_on",
            "risk": "high",
            "own_role": "give_way",
            "in_sight": True,
            "applicable_rules": [7, 8, 14],
            "standing_rules": [2, 5, 6, 7, 11],
            "active_encounter_rules": [8, 14],
        })

        narrative = render_with_history([state(10, 2, [assessed])])

        self.assertNotIn("AUTOMATED TRAFFIC ASSESSMENT", narrative)
        self.assertNotIn("head-on encounter", narrative)
        self.assertNotIn("risk", narrative.lower())
        self.assertNotIn("give-way", narrative)
        self.assertIn(
            "Projected CPA is 80 metres with TCPA 40.0 seconds.",
            narrative,
        )

    def test_renders_active_temporary_course_alteration(self):
        bridge_state = {
            "moos_time": 12.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0, "y": -495, "heading": 351, "speed": 3.39},
            "target_speed": 20,
            "movement_status": "underway",
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": -20},
            "temporary_waypoint": {"x": -76.95, "y": -275.81},
            "mission_objective": {"x": 0, "y": 500},
            "contacts": [],
        }

        compact = compact_state(bridge_state)
        narrative = render_with_history([compact])

        self.assertIn(
            "CURRENT MANEUVER STATE\n\n"
            "A temporary course-change waypoint is currently active.\n"
            "Commanded alteration: 20 degrees to port.\n"
            "The temporary waypoint is an intermediate helm target; "
            "it is not the mission destination.",
            narrative,
        )
        self.assertEqual(
            compact["current_maneuver"]["temporary_waypoint"],
            {"x": -77.0, "y": -275.8},
        )
        self.assertNotIn("Choose", narrative)
        self.assertNotIn("Do not", narrative)
        self.assertNotIn("Stop only", narrative)
        self.assertNotIn("ACTION MEANINGS", narrative)

    def test_course_hold_target_never_replaces_fixed_mission_destination(self):
        compact = compact_state({
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0, "y": 0, "heading": 30, "speed": 4},
            "mission_objective": {"x": 100, "y": 500},
            "next_waypoint": {"x": 50000, "y": 86603},
            "temporary_waypoint": {"x": 50000, "y": 86603},
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 30},
            "contacts": [],
        })

        self.assertEqual(compact["mission"], {
            "x": 100.0,
            "y": 500.0,
            "distance_m": 509.9,
            "bearing_deg": 11.3,
            "heading_error_deg": -18.7,
        })
        narrative = render_with_history([compact])
        self.assertIn("The mission destination is", narrative)
        self.assertNotIn("46.760", narrative)

    def test_temporary_maneuver_has_no_rejoin_assessment(self):
        compact = compact_state({
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0, "y": 0, "heading": 30, "speed": 5},
            "mission_objective": {"x": 0, "y": 1000},
            "next_waypoint": {"x": 0, "y": 1000},
            "temporary_waypoint": {"x": 50000, "y": 86603},
            "action_underway": True,
            "current_action": {"action": "alter_course", "degrees": 30},
            "contacts": [{
                "name": "CROSSING",
                "nav": {"x": 100, "y": 100, "heading": 270, "speed": 5},
                "required_cpa_m": 150,
                "projection_status": "no_future_cpa",
            }],
        })

        self.assertNotIn("saved_route_geometry", compact)
        self.assertNotIn("rejoin_route_assessment", compact)
        narrative = render_with_history([compact])
        self.assertNotIn("PROJECTED GEOMETRY ON THE SAVED ROUTE", narrative)
        self.assertNotIn("rejoin", narrative.lower())
        self.assertNotIn("course hold", narrative.lower())
        self.assertIn("temporary course-change waypoint", narrative)

    def test_renders_normal_cruise_target_and_reduced_speed_recovery(self):
        bridge_state = {
            "moos_time": 12.0,
            "vehicle": "LLM_SHIP",
            "nav": {"x": 0, "y": 0, "heading": 0, "speed": 0.46},
            "target_speed": 2.0,
            "recovery_speed": 20.0,
            "movement_status": "underway",
            "mission_objective": {"x": 0, "y": 500},
            "contacts": [],
        }

        narrative = render_with_history([compact_state(bridge_state)])

        self.assertIn("Target speed is 3.89 knots.", narrative)
        self.assertIn("Normal cruise target is 38.88 knots.", narrative)
        self.assertIn("REDUCED-SPEED STATE", narrative)
        self.assertNotIn("restore normal cruise with set_speed", narrative)

    def test_projection_transition_appearance_disappearance_and_staleness(self):
        old = state(0, 2, [contact("GONE", 20, 0), contact("TS", 50, 2, age=1)])
        current = state(
            10,
            2,
            [contact("TS", 55, 3, projection="no_future_cpa", age=7), contact("NEW", 80, 5)],
        )

        lines = temporal_developments([old, current])

        self.assertIn("Contact NEW appeared.", lines)
        self.assertIn("Contact GONE disappeared.", lines)
        self.assertIn(
            "Based on the last-known TS track, estimated projection changed "
            "from a valid future CPA to no future CPA.",
            lines,
        )
        self.assertIn("TS observation age increased from 1.0 to 7.0 seconds.", lines)

    def test_stale_contact_trends_are_explicitly_estimated(self):
        old = state(0, 2, [contact("TS", 100, 10, age=1)])
        current = state(10, 2, [contact("TS", 90, 30, age=20)])
        current["contacts"][0]["track_quality"] = "stale"

        lines = temporal_developments([old, current])

        self.assertIn(
            "Based on the last-known TS track, estimated range decreased "
            "from 100 metres to 90 metres.",
            lines,
        )
        self.assertTrue(any(
            "moved from" in line and "sector" in line
            for line in lines
        ))
        self.assertFalse(any(line.startswith("TS range") for line in lines))

    def test_suppresses_history_without_timestamps(self):
        old = state(None, 4, [])
        current = state(None, 2, [])
        self.assertNotIn("RECENT TRAFFIC DEVELOPMENTS", render_with_history([old, current]))

    def test_appearance_uses_previous_state_not_oldest_state(self):
        oldest = state(0, 2, [])
        previous = state(10, 2, [contact("TS", 50, 2)])
        current = state(20, 2, [contact("TS", 45, 3)])

        lines = temporal_developments([oldest, previous, current])

        self.assertNotIn("Contact TS appeared.", lines)

    def test_heading_and_within_sector_bearing_noise_are_suppressed(self):
        old = state(0, 2, [contact("TS", 50, 2)])
        current = state(10, 2, [contact("TS", 50, 2.1)])
        old["own_ship"]["heading"] = 48.4
        current["own_ship"]["heading"] = 48.1
        old["contacts"][0]["heading"] = 50.9
        current["contacts"][0]["heading"] = 51.0

        lines = temporal_developments([old, current])

        self.assertFalse(any("heading changed" in line for line in lines))
        self.assertFalse(any("relative bearing" in line for line in lines))

    def test_navigation_progress_is_separate_from_traffic(self):
        old = state(0, 2, [], x=0)
        current = state(10, 2, [], x=10)
        narrative = render_with_history([old, current])
        self.assertIn("NAVIGATION PROGRESS\n\nMission distance decreased", narrative)
        self.assertNotIn("RECENT TRAFFIC DEVELOPMENTS", narrative)

    def test_effectively_zero_speed_and_mission_changes_are_suppressed(self):
        old = state(0, 3.00, [contact("TS", 50, 2)], x=0)
        current = state(10, 2.99, [contact("TS", 50, 2)], x=0.4)
        current["contacts"][0]["speed"] = 2.99

        narrative = render_with_history([old, current])

        self.assertNotIn("Own ship has slowed", narrative)
        self.assertNotIn("TS speed decreased", narrative)
        self.assertNotIn("NAVIGATION PROGRESS", narrative)

    def test_heading_changes_use_wraparound(self):
        old = state(0, 2, [contact("TS", 50, 2)])
        current = state(10, 2, [contact("TS", 50, 2)])
        old["contacts"][0]["heading"] = 359
        current["contacts"][0]["heading"] = 1
        self.assertIn(
            "TS heading changed from 359.0 to 1.0 degrees (2.0 degrees to starboard).",
            temporal_developments([old, current]),
        )

    def test_sequence_resets_at_source_cycle_time_and_large_gap(self):
        prior_row = {"source_file": "a", "cycle_id": 2}
        row = {"source_file": "a", "cycle_id": 3}
        self.assertTrue(continues_sequence(prior_row, row, state(10, 1, []), state(20, 1, []), 120))
        self.assertFalse(continues_sequence(prior_row, {**row, "source_file": "b"}, state(10, 1, []), state(20, 1, []), 120))
        self.assertFalse(continues_sequence(prior_row, {**row, "cycle_id": 1}, state(10, 1, []), state(20, 1, []), 120))
        self.assertFalse(continues_sequence(prior_row, row, state(10, 1, []), state(200, 1, []), 120))

    def test_extractor_keeps_only_state_level_clock(self):
        compact = compact_state({
            "moos_time": 12.345,
            "nav": {},
            "contacts": [{"name": "TS", "moos_time": 11, "nav": {}}],
        })
        self.assertEqual(compact["observed_at_s"], 12.35)
        self.assertNotIn("moos_time", compact["contacts"][0])

    def test_extractor_preserves_all_speeds_in_metres_per_second(self):
        compact = compact_state({
            "nav": {"speed": 4.25},
            "target_speed": 17,
            "contacts": [{
                "name": "TS",
                "nav": {"speed": 3},
                "closing_speed": 1.5,
            }],
        })

        self.assertEqual(compact["own_ship"]["speed"], 4.25)
        self.assertEqual(compact["own_ship"]["target_speed"], 17.0)
        self.assertEqual(compact["contacts"][0]["speed"], 3.0)
        self.assertEqual(compact["contacts"][0]["closing_speed"], 1.5)

    def test_extractor_keeps_facts_and_strips_colregs_assessment(self):
        compact = compact_state({
            "moos_time": 12.0,
            "nav": {},
            "contacts": [{
                "name": "TS",
                "nav": {"x": 10, "y": 20},
                "encounter_type": "crossing",
                "risk": "medium",
                "relative_bearing": 45,
            }],
            "colregs_context": {
                "own_vessel_type": "power_driven",
                "encounters": [{
                    "contact": "TS",
                    "contact_vessel_type": "power_driven",
                    "in_sight": True,
                    "encounter_type": "crossing",
                    "risk": "medium",
                    "own_role": "give_way",
                    "applicable_rules": [7, 8, 15, 16],
                }],
                "assumptions": {"visibility_condition": "clear"},
            },
        })

        self.assertEqual(compact["contacts"][0]["x"], 10.0)
        self.assertNotIn("encounter_type", compact["contacts"][0])
        self.assertNotIn("risk", compact["contacts"][0])
        self.assertNotIn("own_role", compact["contacts"][0])
        self.assertNotIn("standing_rules", compact["contacts"][0])
        self.assertNotIn("active_encounter_rules", compact["contacts"][0])
        self.assertEqual(compact["conditions"]["visibility_condition"], "clear")

    def test_rule13_relationship_persists_until_past_and_clear(self):
        latch = {}
        initial = {
            "nav": {"x": 0, "y": 0, "heading": 0},
            "contacts": [{
                "name": "TS",
                "nav": {"x": 0, "y": 100, "heading": 0},
                "range": 100,
                "relative_bearing": 0,
                "closing_speed": 1,
                "encounter_type": "we_are_overtaking_contact",
            }],
        }
        slowing = {
            "nav": {"x": 0, "y": 0, "heading": 0},
            "contacts": [{
                "name": "TS",
                "nav": {"x": 0, "y": 120, "heading": 0},
                "range": 120,
                "relative_bearing": 0,
                "closing_speed": -1,
                "encounter_type": "diverging",
            }],
        }
        clear = {
            "nav": {"x": 0, "y": 0, "heading": 0},
            "contacts": [{
                "name": "TS",
                "nav": {"x": 0, "y": -200, "heading": 0},
                "range": 200,
                "relative_bearing": 180,
                "closing_speed": -1,
                "encounter_type": "diverging",
            }],
        }

        apply_encounter_persistence(initial, latch)
        persisted = apply_encounter_persistence(slowing, latch)
        released = apply_encounter_persistence(clear, latch)

        self.assertEqual(
            persisted["contacts"][0]["encounter_type"],
            "we_are_overtaking_contact",
        )
        self.assertEqual(released["contacts"][0]["encounter_type"], "diverging")
        self.assertEqual(latch, {})

    def test_active_head_on_survives_maneuver_geometry_until_risk_resolves(self):
        latch = {}
        head_on = {
            "nav": {"x": 0, "y": 0, "heading": 0},
            "contacts": [{
                "name": "TS",
                "nav": {"x": 0, "y": 300, "heading": 180},
                "range": 300,
                "relative_bearing": 0,
                "closing_speed": 5,
                "projection_status": "valid",
                "encounter_type": "head_on",
                "risk": "critical",
                "age_s": 0,
            }],
        }
        maneuver_geometry = copy.deepcopy(head_on)
        maneuver_geometry["contacts"][0].update({
            "encounter_type": "crossing",
            "relative_bearing": 315,
        })
        resolved = copy.deepcopy(maneuver_geometry)
        resolved["contacts"][0].update({
            "encounter_type": "diverging",
            "risk": "low",
            "closing_speed": -1,
            "projection_status": "no_future_cpa",
        })

        apply_encounter_persistence(head_on, latch)
        persisted = apply_encounter_persistence(maneuver_geometry, latch)
        cleared = apply_encounter_persistence(resolved, latch)

        self.assertEqual(persisted["contacts"][0]["encounter_type"], "head_on")
        self.assertEqual(persisted["contacts"][0]["_active_encounter"], "head_on")
        self.assertEqual(cleared["contacts"][0]["encounter_type"], "diverging")

    def test_colregs_vessel_type_persists_when_context_drops_stale_contact(self):
        active = {}
        types = {}
        fresh = {
            "nav": {},
            "contacts": [{"name": "TS", "vessel_type": "ship", "nav": {}}],
            "colregs_context": {"encounters": [{
                "contact": "TS", "contact_vessel_type": "power_driven"
            }]},
        }
        stale = {
            "nav": {},
            "contacts": [{"name": "TS", "nav": {}, "age_s": 10}],
            "colregs_context": {"encounters": []},
        }

        apply_encounter_persistence(fresh, active, types)
        persisted = apply_encounter_persistence(stale, active, types)

        self.assertEqual(
            persisted["contacts"][0]["_colregs_vessel_type"],
            "power_driven",
        )

    def test_impossible_fresh_jump_starts_a_new_track_generation(self):
        active = {"TS": "we_are_overtaking_contact"}
        tracks = {}
        stale = {
            "moos_time": 100,
            "nav": {"x": 0, "y": 0, "heading": 0},
            "contacts": [{
                "name": "TS",
                "nav": {"x": 458.1, "y": 62.2, "speed": 3},
                "range": 164,
                "relative_bearing": 110.5,
                "closing_speed": -1.22,
                "projection_status": "no_future_cpa",
                "encounter_type": "diverging",
                "risk": "low",
                "age_s": 140.2,
            }],
        }
        reacquired = copy.deepcopy(stale)
        reacquired["moos_time"] = 120
        reacquired["contacts"][0].update({
            "nav": {"x": 874.8, "y": 403.1, "speed": 0.46},
            "range": 478,
            "relative_bearing": 39.2,
            "closing_speed": 2.84,
            "projection_status": "valid",
            "encounter_type": "diverging",
            "age_s": 0.06,
        })

        apply_encounter_persistence(stale, active, track_states=tracks)
        reset = apply_encounter_persistence(reacquired, active, track_states=tracks)

        current = reset["contacts"][0]
        self.assertTrue(current["_track_reacquired"])
        self.assertEqual(current["_track_generation"], 1)
        self.assertNotIn("_active_encounter", current)
        self.assertEqual(active, {})

        old_compact = compact_state(apply_encounter_persistence(
            stale, {"TS": "we_are_overtaking_contact"}, track_states={}
        ))
        new_compact = compact_state(reset)
        lines = temporal_developments([old_compact, new_compact])
        self.assertEqual(lines, ["TS was reacquired with a fresh track."])

    def test_stale_track_retains_relationship_but_marks_risk_uncertain(self):
        raw = {
            "moos_time": 100,
            "nav": {"x": 0, "y": 0, "heading": 0},
            "contacts": [{
                "name": "TS",
                "nav": {"x": 100, "y": 100, "heading": 0, "speed": 3},
                "range": 140,
                "relative_bearing": 20,
                "closing_speed": 1,
                "projection_status": "valid",
                "cpa_distance": 50,
                "tcpa_s": 30,
                "encounter_type": "we_are_overtaking_contact",
                "risk": "medium",
                "age_s": 78.3,
            }],
        }
        persisted = apply_encounter_persistence(raw, {})
        compact = compact_state(persisted)
        tracked = compact["contacts"][0]

        self.assertEqual(tracked["track_quality"], "unreliable")
        self.assertNotIn("risk", tracked)
        self.assertNotIn("last_computed_risk", tracked)
        narrative = render_with_history([compact])
        self.assertIn("Last computed CPA was 50 metres", narrative)
        self.assertNotIn("risk", narrative.lower())


if __name__ == "__main__":
    unittest.main()
