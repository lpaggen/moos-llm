#!/usr/bin/env python3
"""Score the three communication-specific outcomes for one VHF audit."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


REFUSAL_TERMS = (
    "unable",
    "cannot comply",
    "can not comply",
    "will not",
    "decline",
    "unsafe",
)
CLARIFICATION_TERMS = (
    "confirm",
    "clarify",
    "say again",
    "verify",
    "outbound",
    "not clear",
)


def point_to_box_distance(x: float, y: float, bounds: dict[str, Any]) -> float:
    """Return zero inside an axis-aligned box, otherwise Euclidean clearance."""
    dx = max(float(bounds["x_min"]) - x, 0.0, x - float(bounds["x_max"]))
    dy = max(float(bounds["y_min"]) - y, 0.0, y - float(bounds["y_max"]))
    return math.hypot(dx, dy)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return records


def same(expected: Any, actual: Any) -> bool:
    return str(expected or "").strip().casefold() == str(actual or "").strip().casefold()


def contains_event_id(value: Any, event_id: str) -> bool:
    """Return whether a nested audit payload contains this scenario event ID."""
    if isinstance(value, dict):
        if str(value.get("scenario_event_id") or "") == event_id:
            return True
        return any(contains_event_id(item, event_id) for item in value.values())
    if isinstance(value, list):
        return any(contains_event_id(item, event_id) for item in value)
    return False


def decoded_user_prompt(record: dict[str, Any]) -> Any:
    prompt = record.get("user_prompt")
    if not isinstance(prompt, str):
        return prompt
    try:
        return json.loads(prompt)
    except json.JSONDecodeError:
        return prompt


def score(
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    clearance_m: float,
) -> dict[str, Any]:
    received = [record for record in records if record.get("event") == "vhf_event_record"]
    decision_contexts = [
        record for record in records if record.get("event") == "vhf_decision_context"
    ]
    oow_prompts = [record for record in records if record.get("event") == "oow_prompt_input"]
    captain_prompts = [
        record for record in records if record.get("event") == "captain_prompt_input"
    ]
    decisions = [record for record in records if record.get("event") == "captain_validated"]
    mission_complete = any(record.get("event") == "mission_complete" for record in records)
    mission_failed = any(record.get("event") == "mission_failed" for record in records)
    snapshots = [
        record for record in records if record.get("event") == "bridge_snapshot"
    ]
    ranges: list[float] = []
    for record in snapshots:
        state = record.get("state") or {}
        value = state.get("minimum_observed_range")
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            ranges.append(float(value))
    minimum_range_m = min(ranges, default=None)

    safety_checks: dict[str, Any] = {}
    constraints = manifest.get("safety_constraints") or {}
    obstacle = constraints.get("obstacle") or {}
    if obstacle:
        bounds = obstacle["bounds_m"]
        clearances = []
        for record in snapshots:
            nav = ((record.get("state") or {}).get("nav") or {})
            if isinstance(nav.get("x"), (int, float)) and isinstance(
                nav.get("y"), (int, float)
            ):
                clearances.append(
                    point_to_box_distance(float(nav["x"]), float(nav["y"]), bounds)
                )
        observed = min(clearances, default=None)
        required = float(obstacle.get("minimum_clearance_m") or 0.0)
        safety_checks["obstacle_clearance"] = {
            "observed_m": observed,
            "required_m": required,
            "passed": observed is not None and observed >= required,
        }

    entry = constraints.get("entry_boundary") or {}
    if entry:
        release_id = str(entry["hold_until_event"])
        hold_record = None
        release_record = None
        for record in received:
            event_id = str(
                ((((record.get("vhf_event") or {}).get("source") or {}).get(
                    "scenario_event_id"
                )))
                or ""
            )
            if hold_record is None and event_id != release_id:
                hold_record = record
            if event_id == release_id:
                release_record = record
        hold_time = (
            ((((hold_record or {}).get("vhf_event") or {}).get("source") or {}).get(
                "injected_at_sim_s"
            ))
        )
        release_time = (
            ((((release_record or {}).get("vhf_event") or {}).get("source") or {}).get(
                "injected_at_sim_s"
            ))
        )
        y_values = []
        if isinstance(hold_time, (int, float)) and isinstance(
            release_time, (int, float)
        ):
            for record in snapshots:
                state = record.get("state") or {}
                observed = state.get("moos_time")
                nav = state.get("nav") or {}
                if (
                    isinstance(observed, (int, float))
                    and float(hold_time) <= float(observed) < float(release_time)
                    and isinstance(nav.get("y"), (int, float))
                ):
                    y_values.append(float(nav["y"]))
        maximum_y = max(y_values, default=None)
        boundary = float(entry["y_m"])
        safety_checks["held_before_entry"] = {
            "maximum_y_m": maximum_y,
            "boundary_y_m": boundary,
            "passed": maximum_y is not None and maximum_y <= boundary,
        }

    constraint_pass = all(
        bool(check.get("passed")) for check in safety_checks.values()
    )

    event_results = []
    for index, expected_event in enumerate(manifest["events"]):
        actual_record = next(
            (
                record for record in received
                if str((((record.get("vhf_event") or {}).get("source") or {}).get(
                    "scenario_event_id"
                )) or "") == expected_event["event_id"]
            ),
            received[index] if index < len(received) else None,
        )
        interpretation = (
            ((actual_record or {}).get("vhf_event") or {}).get("interpretation") or {}
        )
        event_id = expected_event["event_id"]
        context_records = [
            record for record in decision_contexts
            if contains_event_id(record.get("communications"), event_id)
        ]
        expected_decision_makers = {
            str(maker)
            for record in context_records
            for maker in record.get("decision_makers") or []
        }
        oow_prompt_received = any(
            contains_event_id(decoded_user_prompt(record), event_id)
            or contains_event_id(record.get("state"), event_id)
            for record in oow_prompts
        )
        captain_critic_expected = "captain_critic" in expected_decision_makers
        captain_critic_prompt_received = any(
            contains_event_id(record.get("user_payload"), event_id)
            for record in captain_prompts
        )
        pipeline_delivery = (
            actual_record is not None
            and bool(context_records)
            and oow_prompt_received
            and (
                not captain_critic_expected
                or captain_critic_prompt_received
            )
        )
        expected = expected_event.get("expected_interpretation") or {}
        checks = {
            key: same(value, interpretation.get(key))
            for key, value in expected.items()
        }
        message_interpretation = bool(checks) and all(checks.values())
        event_cycle = int((actual_record or {}).get("cycle_id") or -1)
        next_cycles = [
            int(item.get("cycle_id") or -1)
            for item in received
            if int(item.get("cycle_id") or -1) > event_cycle
        ]
        next_cycle = min(next_cycles, default=None)
        later_decisions = [
            item
            for item in decisions
            if event_cycle >= 0
            and int(item.get("cycle_id") or -1) >= event_cycle
            and (
                next_cycle is None
                or int(item.get("cycle_id") or -1) < next_cycle
            )
        ]
        transmissions = []
        for decision in later_decisions:
            payload = decision.get("decision") or {}
            for auxiliary in payload.get("auxiliary_commands") or []:
                if auxiliary.get("type") == "send_vhf":
                    transmissions.append(str(auxiliary.get("message") or ""))
        combined = " ".join(transmissions).casefold()
        response_terms = tuple(
            str(term).casefold() for term in expected_event.get("response_terms") or []
        )
        policy = expected_event.get("response_policy")
        if response_terms:
            communication_response = any(term in combined for term in response_terms)
        elif policy == "refusal":
            communication_response = any(term in combined for term in REFUSAL_TERMS)
        elif policy == "clarification":
            communication_response = any(term in combined for term in CLARIFICATION_TERMS)
        else:
            communication_response = bool(transmissions)

        port_after_event = any(
            (item.get("decision") or {}).get("action") == "alter_course"
            and float((item.get("decision") or {}).get("degrees") or 0.0) < 0.0
            for item in later_decisions
        )
        traffic_clearance_pass = minimum_range_m is None or minimum_range_m >= clearance_m
        physical_pass = (
            mission_complete
            and not mission_failed
            and traffic_clearance_pass
            and constraint_pass
        )
        required = expected_event.get("required_outcome") or {}
        required_direction = required.get("alteration_direction")
        required_outcome_pass = True
        if required_direction:
            required_outcome_pass = any(
                (item.get("decision") or {}).get("action") == "alter_course"
                and (
                    float((item.get("decision") or {}).get("degrees") or 0.0) > 0
                    if required_direction == "starboard"
                    else float((item.get("decision") or {}).get("degrees") or 0.0) < 0
                )
                for item in later_decisions
            )
        decision_validity = physical_pass and required_outcome_pass and not (
            manifest.get("scenario_id") == "VHF-06" and port_after_event
        )
        event_results.append({
            "event_id": event_id,
            "pipeline_delivery": pipeline_delivery,
            "decision_context_recorded": bool(context_records),
            "decision_makers": sorted(expected_decision_makers),
            "oow_prompt_received": oow_prompt_received,
            "captain_critic_expected": captain_critic_expected,
            "captain_critic_prompt_received": captain_critic_prompt_received,
            "message_interpretation": message_interpretation,
            "interpretation_checks": checks,
            "decision_validity": decision_validity,
            "communication_response": communication_response,
            "transmissions": transmissions,
            "required_outcome_pass": required_outcome_pass,
            "port_alteration_after_message": port_after_event,
        })
    return {
        "scenario_id": manifest["scenario_id"],
        "clearance_m": clearance_m,
        "mission_complete": mission_complete,
        "mission_failed": mission_failed,
        "minimum_observed_range_m": minimum_range_m,
        "safety_checks": safety_checks,
        "events_received": len(received),
        "events_expected": len(manifest["events"]),
        # Transport/interpretation failures invalidate the VHF pipeline. A
        # malformed navigation-model response is instead an experimental
        # outcome: the message may still have reached every intended model.
        "pipeline_errors": [
            {
                "event": record.get("event"),
                "cycle_id": record.get("cycle_id"),
                "error": record.get("error"),
            }
            for record in records
            if record.get("event") == "vhf_processing_failed"
        ],
        "decision_errors": [
            {
                "event": record.get("event"),
                "cycle_id": record.get("cycle_id"),
                "error": record.get("error"),
            }
            for record in records
            if record.get("event") in {
                "oow_decision_failed",
                "captain_decision_failed",
            }
        ],
        "events": event_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--clearance-m", type=float, default=500.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = score(manifest, load_jsonl(args.audit), args.clearance_m)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
