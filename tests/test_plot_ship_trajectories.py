import json
import math
from pathlib import Path
import tempfile
import unittest

from tools.plot_ship_trajectories import Track, terminal_segment, load_track, position, mission_bounds, architecture_cases


class TrajectoryTests(unittest.TestCase):
    def test_mission_square_ignores_far_off_route_observations(self):
        track = Track("Model", Path("audit.jsonl"))
        track.mission_start = (0, -5556)
        track.destination = (0, 5556)
        track.own = [(0, 0, -5553), (1, 1000000, 0)]
        self.assertEqual(mission_bounds([track], 1852), (-4, 4, -4, 4))

    def test_missing_cases_keep_all_22_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = architecture_cases(Path(directory), "Model")
        self.assertEqual(len(cases), 22)
        self.assertEqual(cases[0]["case"], "case_01")
        self.assertEqual(cases[-1]["case"], "case_22")
        self.assertTrue(all(item["track"] is None for item in cases))

    def test_extracts_observed_positions_and_contact_gaps(self):
        def snapshot(x, contacts, complete=False):
            return {"event": "bridge_snapshot", "state": {
                "ready": True, "moos_time": x + 10,
                "nav": {"x": x, "y": 1852}, "contacts": contacts,
                "mission_objective": {"x": 0, "y": 5556}, "mission_complete": complete}}
        rows = [snapshot(0, [{"name": "TS1", "nav": {"x": 2, "y": 3}}]),
                {"event": "oow_prompt_input", "state": {"nav": {"x": 999, "y": 999}}},
                snapshot(1, []), snapshot(2, [{"name": "TS1", "nav": {"x": 4, "y": 5}}], True)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.jsonl"
            path.write_text("\n".join(map(json.dumps, rows)))
            track = load_track(path, "Model")
        self.assertEqual([p[1] for p in track.own], [0, 1, 2])
        self.assertTrue(math.isnan(track.contacts["TS1"][1][1]))
        self.assertEqual(track.destination, (0, 5556))
        self.assertEqual(track.outcome, "Mission complete")

    def test_rejects_nonfinite_coordinates(self):
        self.assertIsNone(position({"x": math.inf, "y": 0}))
        self.assertIsNone(position({"x": "1", "y": 0}))
        self.assertEqual(position({"x": 0, "y": 0}), (0, 0))

    def test_terminal_arrow_clips_exit_and_ignores_stationary_samples(self):
        values = [(0, 0, 0), (1, 2, 0), (2, 10, 0), (3, 10, 0)]
        self.assertEqual(terminal_segment(values, 1, (-4, 4, -4, 4)),
                         ((2, 0), (4, 0)))

    def test_terminal_arrow_does_not_bridge_missing_contact_reports(self):
        values = [(0, 0, 0), (1, math.nan, math.nan), (2, 1, 1)]
        self.assertIsNone(terminal_segment(values, 1, (-4, 4, -4, 4)))

    def test_late_contact_is_aligned_with_own_ship(self):
        rows = [{"event": "bridge_snapshot", "state": {"moos_time": i,
                 "nav": {"x": i, "y": 0}, "contacts": [] if i < 2 else
                 [{"name": "TS1", "nav": {"x": 100, "y": 5}}]}} for i in range(4)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text("\n".join(map(json.dumps, rows)))
            track = load_track(path, "Model")
        self.assertEqual(len(track.contacts["TS1"]), len(track.own))
        self.assertTrue(math.isnan(track.contacts["TS1"][0][1]))
        self.assertEqual(track.contacts["TS1"][2], (2, 100, 5))


if __name__ == "__main__":
    unittest.main()
