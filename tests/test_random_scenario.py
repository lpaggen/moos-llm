import configparser
from collections import Counter
import math
from pathlib import Path
import tempfile
import unittest

from mission.render_fleet import load_fleet
from tools.generate_random_scenario import (
    CONTACT_ROUTE_LEGS,
    CONTACT_TYPES,
    GUARANTEED_ENCOUNTERS,
    KINDS,
    OWN_ROUTE_LEGS,
    generate,
    generate_classic,
    generate_curriculum,
    generate_targeted,
)


class RandomScenarioTests(unittest.TestCase):
    def test_targeted_profile_alternates_unforced_rare_action_candidates(self):
        _, stop = generate_targeted(50000)
        _, port = generate_targeted(50001)

        self.assertEqual(stop["profile"], "targeted")
        self.assertEqual(stop["targeted_variant"], "stop_crossing")
        self.assertEqual(stop["target_action"], "stop")
        self.assertEqual(
            [contact["kind"] for contact in stop["contacts"]],
            ["crossing_starboard", "crossing_port"],
        )
        self.assertEqual(port["targeted_variant"], "port_overtaking")
        self.assertEqual(port["target_action"], "alter_course_port")
        self.assertEqual(
            [contact["kind"] for contact in port["contacts"]],
            ["we_are_overtaking_contact", "contact_overtaking_us"],
        )
        self.assertEqual(stop["contact_count"], 2)
        self.assertEqual(port["contact_count"], 2)
        self.assertFalse(stop["teacher_label_forced"])
        self.assertFalse(port["teacher_label_forced"])
        self.assertTrue(stop["independent_of_imazu"])
        self.assertTrue(port["independent_of_imazu"])

    def test_curriculum_profile_is_reproducible_and_limited_to_three_contacts(self):
        first, metadata = generate_curriculum(42)
        second, repeated = generate_curriculum(42)
        self.assertEqual(first, second)
        self.assertEqual(metadata, repeated)
        self.assertEqual(metadata["profile"], "curriculum")
        self.assertIn(metadata["curriculum_variant"], {
            "canonical", "perturbed", "multi_contact", "recovery"
        })
        self.assertLessEqual(metadata["contact_count"], 3)
        self.assertTrue(metadata["independent_of_imazu"])

    def test_curriculum_distribution_covers_variants_and_cpa_bands(self):
        metadata = [generate_curriculum(seed)[1] for seed in range(200)]
        self.assertEqual({row["curriculum_variant"] for row in metadata}, {
            "canonical", "perturbed", "multi_contact", "recovery"
        })
        self.assertEqual({band for row in metadata for band in row["cpa_bands"]}, {
            "critical", "caution", "safe"
        })

    def test_classic_profile_has_one_reproducible_textbook_encounter(self):
        first, metadata = generate_classic(42, 1, 3000.0)
        second, repeated = generate_classic(42, 1, 3000.0)

        self.assertEqual(first, second)
        self.assertEqual(metadata, repeated)
        self.assertEqual(metadata["profile"], "classic")
        self.assertEqual(metadata["contact_count"], 1)
        self.assertIn(metadata["contacts"][0]["kind"], {
            "head_on", "crossing_starboard", "crossing_port",
            "we_are_overtaking_contact", "contact_overtaking_us",
        })

    def test_classic_profile_covers_all_encounter_kinds(self):
        kinds = {generate_classic(seed)[1]["contacts"][0]["kind"] for seed in range(100)}
        self.assertEqual(kinds, {
            "head_on", "crossing_starboard", "crossing_port",
            "we_are_overtaking_contact", "contact_overtaking_us",
        })

    def test_classic_profile_supports_ten_targets(self):
        scenario, metadata = generate_classic(50000, 10, 3000.0)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(scenario)

        vehicles = [name for name in parser.sections() if name.startswith("vehicle:")]
        self.assertEqual(len(vehicles), 11)
        self.assertEqual(metadata["contact_count"], 10)

    def test_seed_is_reproducible_and_own_ship_is_first(self):
        first, first_metadata = generate(42, 4)
        second, second_metadata = generate(42, 4)

        self.assertEqual(first, second)
        self.assertEqual(first_metadata, second_metadata)
        self.assertLess(first.index("[vehicle:LLM_SHIP]"), first.index("[vehicle:RANDOM_TS1]"))

    def test_generated_fleet_has_requested_contacts(self):
        scenario, metadata = generate(7, 5)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(scenario)

        vehicles = [name for name in parser.sections() if name.startswith("vehicle:")]
        self.assertEqual(len(vehicles), 6)
        self.assertEqual(metadata["contact_count"], 5)
        self.assertEqual(metadata["generator_version"], 6)
        self.assertEqual(metadata["objective_sampling"], "opposite_perimeter_annulus")
        self.assertEqual(metadata["own_ship"]["speed"], 12.0)
        self.assertEqual(len(metadata["own_ship"]["goals"]), OWN_ROUTE_LEGS)
        self.assertTrue(all(
            len(contact["goals"]) == CONTACT_ROUTE_LEGS
            for contact in metadata["contacts"]
        ))

    def test_many_seeds_cover_semantic_kinds_and_crossing_sides(self):
        contacts = [
            contact
            for seed in range(200)
            for contact in generate(seed, 6)[1]["contacts"]
        ]
        counts = Counter(contact["kind"] for contact in contacts)
        self.assertEqual(set(counts), set(KINDS))
        self.assertGreaterEqual(min(counts.values()), 50)
        self.assertEqual(
            {c["side"] for c in contacts if c["kind"] == "crossing"},
            {"port", "starboard"},
        )

    def test_overtaking_directions_have_correct_relative_speeds(self):
        for seed in range(100):
            _, metadata = generate(seed, 6)
            own_speed = metadata["own_ship"]["speed"]
            for contact in metadata["contacts"]:
                if contact["kind"] == "we_are_overtaking_contact":
                    self.assertLess(contact["speed"], own_speed)
                elif contact["kind"] == "contact_overtaking_us":
                    self.assertGreater(contact["speed"], own_speed)

    def test_repeated_encounter_kinds_are_possible(self):
        repeated = False
        for seed in range(30):
            kinds = [c["kind"] for c in generate(seed, 6)[1]["contacts"]]
            repeated = repeated or len(kinds) != len(set(kinds))
        self.assertTrue(repeated)

    def test_many_seeds_cover_contact_vessel_types(self):
        vessel_types = {
            contact["vessel_type"]
            for seed in range(200)
            for contact in generate(seed, 6)[1]["contacts"]
        }
        self.assertEqual(vessel_types, set(CONTACT_TYPES))

    def test_contact_speeds_follow_type_ranges_except_encounter_constraints(self):
        for seed in range(100):
            _, metadata = generate(seed, 6)
            for contact in metadata["contacts"]:
                self.assertGreater(contact["speed"], 0.0)
                if contact["kind"] == "contact_overtaking_us":
                    self.assertGreater(contact["speed"], 12.0)
                elif contact["kind"] == "we_are_overtaking_contact":
                    self.assertLess(contact["speed"], 12.0)

    def test_positions_are_inside_disk_and_not_only_on_circumference(self):
        scale = 6000.0
        radii = []
        for seed in range(30):
            _, metadata = generate(seed, 6, scale)
            points = [metadata["own_ship"]["start"]]
            points.extend(contact["start"] for contact in metadata["contacts"])
            radii.extend((x * x + y * y) ** 0.5 for x, y in points)
        self.assertLessEqual(max(radii), scale)
        self.assertLess(min(radii), scale * 0.25)
        self.assertGreater(max(radii) - min(radii), scale * 0.6)

    def test_nonstationary_objectives_use_outer_annulus(self):
        scale = 6000.0
        _, metadata = generate(42, 50, scale)
        minimum_radius = scale * 0.97 * 0.82
        for contact in metadata["contacts"]:
            if contact["kind"] == "stationary":
                continue
            for x, y in contact["goals"]:
                self.assertGreaterEqual(math.hypot(x, y), minimum_radius - 0.01)

    def test_large_campaign_guarantees_near_term_encounters(self):
        _, metadata = generate(50000, 50, 6000.0)
        engineered = [
            contact for contact in metadata["contacts"]
            if contact["engineered_encounter"]
        ]
        own_start = metadata["own_ship"]["start"]
        self.assertEqual(len(engineered), GUARANTEED_ENCOUNTERS)
        self.assertEqual(metadata["guaranteed_encounters"], GUARANTEED_ENCOUNTERS)
        self.assertTrue(all(
            contact["kind"] in {"crossing", "head_on"}
            for contact in engineered
        ))
        encounter_distances = [
            contact["encounter_distance_along_own_route_m"]
            for contact in engineered
        ]
        self.assertGreaterEqual(min(encounter_distances), 1000.0)
        self.assertGreater(max(encounter_distances) - min(encounter_distances), 2000.0)
        self.assertGreater(
            sum(math.dist(own_start, contact["start"]) > 1500.0 for contact in engineered),
            len(engineered) // 2,
        )

    def test_generated_scenario_is_accepted_by_fleet_renderer(self):
        scenario, _ = generate(42, 6)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "random.ini"
            path.write_text(scenario, encoding="utf-8")
            rows = load_fleet(path, amount=None, fast=False)
        self.assertEqual(len(rows["names"]), 7)
        self.assertEqual(rows["routes"][0].count(":"), OWN_ROUTE_LEGS - 1)
        self.assertTrue(all(
            route.count(":") == CONTACT_ROUTE_LEGS - 1
            for route in rows["routes"][1:]
        ))
