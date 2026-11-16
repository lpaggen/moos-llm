#!/usr/bin/env python3
"""Inject deterministic VHF transcripts when scenario geometry meets a trigger."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.bridge import DEFAULT_HOST, DEFAULT_PORT, get_bridge_json


VHF_REQUESTED_ACTIONS = {
    "none",
    "maintain_course",
    "alter_course",
    "rejoin_route",
    "set_speed",
    "stop",
    "resume",
    "proceed",
    "skip",
}
VHF_TURN_DIRECTIONS = {"none", "port", "starboard"}


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("schema_version") != 1:
        raise ValueError("VHF manifest schema_version must be 1")
    if not str(manifest.get("scenario_id") or "").strip():
        raise ValueError("VHF manifest requires scenario_id")
    events = manifest.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("VHF manifest requires at least one event")
    seen: set[str] = set()
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id or event_id in seen:
            raise ValueError("every VHF event requires a unique event_id")
        seen.add(event_id)
        if not str(event.get("transcript") or "").strip():
            raise ValueError(f"{event_id}: transcript cannot be empty")
        expected = event.get("expected_interpretation") or {}
        requested_action = expected.get("requested_action")
        if requested_action is not None and requested_action not in VHF_REQUESTED_ACTIONS:
            raise ValueError(
                f"{event_id}: expected requested_action must use the current "
                f"categorical action schema; got {requested_action!r}"
            )
        requested_direction = expected.get("requested_turn_direction")
        if (
            requested_direction is not None
            and requested_direction not in VHF_TURN_DIRECTIONS
        ):
            raise ValueError(
                f"{event_id}: invalid requested_turn_direction "
                f"{requested_direction!r}"
            )
        if requested_action == "alter_course" and requested_direction not in {
            "port",
            "starboard",
        }:
            raise ValueError(
                f"{event_id}: alter_course expectations require "
                "requested_turn_direction port or starboard"
            )
        if (
            requested_action is not None
            and requested_action != "alter_course"
            and requested_direction not in {None, "none"}
        ):
            raise ValueError(
                f"{event_id}: only alter_course may expect a turn direction"
            )
        channel = event.get("channel")
        if not isinstance(channel, int) or not 1 <= channel <= 88:
            raise ValueError(f"{event_id}: channel must be an integer from 1 to 88")
        trigger = event.get("trigger")
        if not isinstance(trigger, dict) or not trigger:
            raise ValueError(f"{event_id}: trigger must be a non-empty object")
        known = {
            "contact",
            "range_lte_m",
            "contact_y_lte_m",
            "contact_y_gte_m",
            "own_y_gte_m",
            "elapsed_sim_s_gte",
        }
        unknown = set(trigger) - known
        if unknown:
            raise ValueError(f"{event_id}: unknown trigger keys: {sorted(unknown)}")
        contact_keys = {"range_lte_m", "contact_y_lte_m", "contact_y_gte_m"}
        if contact_keys.intersection(trigger) and not trigger.get("contact"):
            raise ValueError(
                f"{event_id}: contact-derived trigger conditions require contact"
            )
        for key in (
            "range_lte_m",
            "contact_y_lte_m",
            "contact_y_gte_m",
            "own_y_gte_m",
            "elapsed_sim_s_gte",
        ):
            if key in trigger and not math.isfinite(float(trigger[key])):
                raise ValueError(f"{event_id}: {key} must be finite")
    return manifest


def trigger_matches(
    trigger: dict[str, Any],
    state: dict[str, Any],
    initial_sim_time: float,
) -> bool:
    nav = state.get("nav") or {}
    if "own_y_gte_m" in trigger:
        if nav.get("y") is None or float(nav["y"]) < float(trigger["own_y_gte_m"]):
            return False
    if "elapsed_sim_s_gte" in trigger:
        current = state.get("moos_time")
        if current is None or float(current) - initial_sim_time < float(trigger["elapsed_sim_s_gte"]):
            return False
    contact = None
    if any(
        key in trigger
        for key in ("range_lte_m", "contact_y_lte_m", "contact_y_gte_m")
    ):
        wanted = str(trigger["contact"]).casefold()
        contact = next(
            (
                item for item in state.get("contacts", [])
                if str(item.get("name") or "").casefold() == wanted
            ),
            None,
        )
        if contact is None:
            return False
    if "range_lte_m" in trigger:
        if contact.get("range") is None:
            return False
        if float(contact["range"]) > float(trigger["range_lte_m"]):
            return False
    contact_nav = (contact or {}).get("nav") or {}
    if "contact_y_lte_m" in trigger:
        if contact_nav.get("y") is None:
            return False
        if float(contact_nav["y"]) > float(trigger["contact_y_lte_m"]):
            return False
    if "contact_y_gte_m" in trigger:
        if contact_nav.get("y") is None:
            return False
        if float(contact_nav["y"]) < float(trigger["contact_y_gte_m"]):
            return False
    return True


def emit_event(
    *,
    inbox: Path,
    scenario_id: str,
    event: dict[str, Any],
    state: dict[str, Any],
) -> Path:
    payload = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "event_id": event["event_id"],
        "channel": event["channel"],
        "language": "en",
        "transcript": event["transcript"],
        "injected_at_sim_s": state.get("moos_time"),
        "trigger": event["trigger"],
    }
    stable = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    suffix = hashlib.sha256(stable).hexdigest()[:12]
    destination = inbox / f"{scenario_id.lower()}_{event['event_id']}_{suffix}.json"
    temporary = destination.with_suffix(".json.tmp")
    inbox.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.check:
        print(
            f"Valid VHF manifest {manifest['scenario_id']}: "
            f"{len(manifest['events'])} event(s)"
        )
        return 0
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")

    pending = {event["event_id"]: event for event in manifest["events"]}
    initial_sim_time: float | None = None
    while pending:
        try:
            state = get_bridge_json(args.host, args.port)
        except (OSError, RuntimeError, ValueError):
            time.sleep(args.poll_seconds)
            continue
        if not state.get("ready", True):
            time.sleep(args.poll_seconds)
            continue
        current_sim_time = state.get("moos_time")
        if initial_sim_time is None and current_sim_time is not None:
            initial_sim_time = float(current_sim_time)
        if initial_sim_time is None:
            time.sleep(args.poll_seconds)
            continue
        for event_id, event in list(pending.items()):
            if trigger_matches(event["trigger"], state, initial_sim_time):
                path = emit_event(
                    inbox=args.inbox,
                    scenario_id=manifest["scenario_id"],
                    event=event,
                    state=state,
                )
                print(
                    json.dumps({
                        "event": "vhf_injected",
                        "scenario_id": manifest["scenario_id"],
                        "event_id": event_id,
                        "path": str(path),
                        "moos_time": state.get("moos_time"),
                    }),
                    flush=True,
                )
                pending.pop(event_id)
        if state.get("mission_complete") or state.get("mission_failed"):
            break
        time.sleep(args.poll_seconds)
    if pending:
        print(f"Unsent VHF events: {', '.join(sorted(pending))}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
