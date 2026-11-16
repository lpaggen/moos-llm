import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.analyze_baseline_logs import (
    arrival_on_polyline,
    case_log_paths,
    event_log_path,
    timed_bridge_states,
)


class BaselineArrivalTests(unittest.TestCase):
    def test_crossing_between_distant_samples(self):
        own = [dict(time=0, x=-100, y=0), dict(time=25, x=100, y=0)]
        self.assertEqual(arrival_on_polyline(own, (0, 0), 20), (10, 80))

    def test_nearby_segment_can_miss_goal(self):
        own = [dict(time=0, x=-100, y=21), dict(time=25, x=100, y=21)]
        self.assertIsNone(arrival_on_polyline(own, (0, 0), 20))

    def test_stationary_segments_do_not_divide_by_zero(self):
        own = [dict(time=0, x=-100, y=0), dict(time=25, x=-100, y=0),
               dict(time=50, x=100, y=0)]
        self.assertEqual(arrival_on_polyline(own, (0, 0), 20), (35, 80))


class BaselineLogDiscoveryTests(unittest.TestCase):
    def test_prefers_current_audit_log(self):
        with TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case_01"
            case_dir.mkdir()
            (case_dir / "audit.jsonl").touch()
            (case_dir / "telemetry.jsonl").touch()
            self.assertEqual(event_log_path(case_dir), case_dir / "audit.jsonl")

    def test_discovers_both_log_formats_in_case_order(self):
        with TemporaryDirectory() as directory:
            run = Path(directory)
            for case, name in ((10, "audit.jsonl"), (2, "telemetry.jsonl")):
                case_dir = run / f"case_{case:02d}"
                case_dir.mkdir()
                (case_dir / name).touch()
            self.assertEqual(
                case_log_paths(run),
                [run / "case_02" / "telemetry.jsonl", run / "case_10" / "audit.jsonl"],
            )


class LockstepTimelineTests(unittest.TestCase):
    def test_decision_pause_is_removed_at_configured_warp(self):
        events = [
            {"event": "bridge_snapshot", "state": {"moos_time": 100.0}},
            {"event": "simulation_resumed_after_decision",
             "paused_wall_duration_s": 2.0},
            {"event": "bridge_snapshot", "state": {"moos_time": 170.0}},
        ]

        rows = timed_bridge_states(
            events, exclude_decision_pauses=True, time_warp=20.0,
        )
        adjusted = [state["moos_time"] - pause for _, state, pause in rows]

        self.assertEqual(adjusted, [100.0, 130.0])

    def test_raw_timeline_remains_available_for_frozen_v1(self):
        events = [
            {"event": "bridge_snapshot", "state": {"moos_time": 100.0}},
            {"event": "simulation_resumed_after_decision",
             "paused_wall_duration_s": 2.0},
            {"event": "bridge_snapshot", "state": {"moos_time": 170.0}},
        ]

        rows = timed_bridge_states(events, time_warp=20.0)

        self.assertEqual([pause for _, _, pause in rows], [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
