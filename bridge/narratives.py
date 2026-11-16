#!/usr/bin/env python3
"""Render compact navigation observations as deterministic bridge narratives."""
import argparse
import json
import math
import os
from pathlib import Path

from tools.config_value import get

METERS_PER_SECOND_TO_KNOTS = 1.9438444924406
CAUTION_CPA_M = float(
    os.environ.get("MOOS_LLM_CAUTION_CPA_M")
    or get("ship", "CAUTION_CPA_M", fallback="750.0")
)
MIN_SAFE_CPA_M = float(
    os.environ.get("MOOS_LLM_MIN_SAFE_CPA_M")
    or get("ship", "MIN_SAFE_CPA_M", fallback="500.0")
)
BOW_CROSSING_M = float(os.environ.get("MOOS_LLM_BOW_CROSSING_M") or MIN_SAFE_CPA_M)
CRITICAL_CPA_M = float(
    os.environ.get("MOOS_LLM_CRITICAL_CPA_M")
    or get("ship", "CRITICAL_CPA_M", fallback="500.0")
)
PLANNING_TCPA_S = 600.0
INTERVENTION_TCPA_S = 300.0
EMERGENCY_TCPA_S = 120.0


def fmt1(x):
    return f"{float(x):.1f}"


def fmt2(x):
    return f"{float(x):.2f}"


def fmt0(x):
    return str(int(round(float(x))))


def fmt_meters(meters):
    return fmt0(meters)


def fmt_knots(speed_mps):
    return f"{float(speed_mps) * METERS_PER_SECOND_TO_KNOTS:.2f}"


def relative_motion_text(closing_speed):
    speed = float(closing_speed)
    if abs(speed) < 0.03:
        return "The range is nearly steady"
    if speed > 0.0:
        return f"The vessels are closing at {fmt_knots(speed)} knots"
    return f"The vessels are opening at {fmt_knots(abs(speed))} knots"


def relative_motion_change(subject, start, end):
    old_speed = float(start)
    new_speed = float(end)
    if old_speed >= 0.0 and new_speed >= 0.0:
        return (
            f"{subject} closing rate changed from {fmt_knots(old_speed)} to "
            f"{fmt_knots(new_speed)} knots."
        )
    if old_speed <= 0.0 and new_speed <= 0.0:
        return (
            f"{subject} opening rate changed from {fmt_knots(abs(old_speed))} to "
            f"{fmt_knots(abs(new_speed))} knots."
        )
    old_motion = "closing" if old_speed > 0.0 else "opening"
    new_motion = "closing" if new_speed > 0.0 else "opening"
    return (
        f"{subject} changed from {old_motion} at {fmt_knots(abs(old_speed))} knots "
        f"to {new_motion} at {fmt_knots(abs(new_speed))} knots."
    )


def bearing_sector(relative_bearing_deg):
    if relative_bearing_deg is None:
        return None
    bearing = float(relative_bearing_deg) % 360.0
    if bearing < 22.5 or bearing >= 337.5:
        return "ahead"
    if bearing < 67.5:
        return "starboard bow"
    if bearing < 112.5:
        return "starboard beam"
    if bearing < 157.5:
        return "starboard quarter"
    if bearing < 202.5:
        return "astern"
    if bearing < 247.5:
        return "port quarter"
    if bearing < 292.5:
        return "port beam"
    return "port bow"


def render_projection(contact):
    status = contact.get("projection_status")
    quality = contact.get("track_quality", "fresh")

    if status == "valid":
        cpa = contact.get("cpa_distance_m")
        tcpa = contact.get("tcpa_s")
        if cpa is not None and tcpa is not None:
            if quality == "fresh":
                return f"Projected CPA is {fmt_meters(cpa)} metres with TCPA {fmt1(tcpa)} seconds."
            return (
                f"Last computed CPA was {fmt_meters(cpa)} metres with TCPA {fmt1(tcpa)} seconds; "
                "the current projection is uncertain."
            )
        return ""

    if status == "beyond_planning_horizon":
        cpa = contact.get("cpa_distance_m")
        tcpa = contact.get("tcpa_s")
        if cpa is not None and tcpa is not None:
            return (
                f"Projected CPA is {fmt_meters(cpa)} metres in {fmt1(tcpa)} seconds. "
                f"This is beyond the {fmt0(PLANNING_TCPA_S)}-second planning window."
            )
        return "The projected closest approach is beyond the planning window."

    if status == "no_future_cpa":
        if quality != "fresh":
            return (
                "The last observation produced no future CPA; "
                "the current projection is uncertain."
            )
        return "The vessels are not approaching a future CPA on their current courses and speeds."

    # Unknown / unavailable projection states are simply omitted.
    return ""


def render_contact(contact, stale_age_threshold):
    name = contact.get("name", "UNKNOWN_CONTACT")
    identity = f"- {name}"
    vessel_type = contact.get("colregs_vessel_type") or contact.get("vessel_type")
    if vessel_type:
        identity += f" ({str(vessel_type).replace('_', '-')})"
    x = contact.get("x")
    y = contact.get("y")
    if x is not None and y is not None:
        identity += f" at ({fmt_meters(x)}, {fmt_meters(y)}) metres"

    parts = [identity + "."]

    range_m = contact.get("range_m")
    sector = contact.get("sector") or bearing_sector(contact.get("relative_bearing_deg"))
    if range_m is not None:
        location = f"It is {fmt_meters(range_m)} metres away"
        if sector:
            if sector in {"ahead", "astern"}:
                location += f", {sector} of own ship"
            else:
                location += f" on own ship's {sector}"
        parts.append(location + ".")
    elif sector:
        parts.append(f"It is on the {sector}.")

    heading = contact.get("heading")
    speed = contact.get("speed")
    closing = contact.get("closing_speed")

    motion = []
    if heading is not None:
        motion.append(f"Heading {fmt1(heading)} degrees")
    if speed is not None:
        motion.append(f"speed {fmt_knots(speed)} knots")
    if motion:
        parts.append(", ".join(motion) + ".")
    if closing is not None:
        parts.append(relative_motion_text(closing) + ".")

    projection = render_projection(contact)
    if projection:
        parts.append(projection)

    intersection = contact.get("track_intersection")
    if isinstance(intersection, dict):
        ix = intersection.get("x")
        iy = intersection.get("y")
        own_eta = intersection.get("own_eta_s")
        contact_eta = intersection.get("contact_eta_s")
        if None not in (ix, iy, own_eta, contact_eta):
            parts.append(
                f"The forward course lines intersect at ({fmt_meters(ix)}, "
                f"{fmt_meters(iy)}) metres; own-ship ETA is "
                f"{fmt1(own_eta)} seconds and contact ETA is "
                f"{fmt1(contact_eta)} seconds."
            )

    age = contact.get("age_s")
    if age is not None and float(age) > stale_age_threshold:
        parts.append(f"Observation age is {fmt1(age)} seconds.")
    quality = contact.get("track_quality")
    if quality == "stale":
        parts.append("Track quality is stale.")
    elif quality == "unreliable":
        parts.append("Track quality is unreliable.")

    return " ".join(parts)


def render_state(state, stale_age_threshold=5.0):
    own = state.get("own_ship") or {}
    mission = state.get("mission") or {}
    contacts = state.get("contacts") or []

    own_name = own.get("name", "Own ship")
    status = own.get("movement_status")

    sentence = own_name
    if status:
        sentence += f" is {status}"

    x = own.get("x")
    y = own.get("y")
    if x is not None and y is not None:
        sentence += f" at ({fmt_meters(x)}, {fmt_meters(y)}) metres"

    heading = own.get("heading")
    if heading is not None:
        sentence += f", heading {fmt1(heading)} degrees"

    speed = own.get("speed")
    if speed is not None:
        sentence += f", speed {fmt_knots(speed)} knots"

    sentence += "."

    target_speed = own.get("target_speed")
    recovery_speed = own.get("recovery_speed")
    if target_speed is not None and recovery_speed is not None and float(target_speed) == float(recovery_speed):
        sentence += f" Target and normal cruise speed are {fmt_knots(target_speed)} knots."
    elif target_speed is not None:
        sentence += f" Target speed is {fmt_knots(target_speed)} knots."
    if recovery_speed is not None and (
        target_speed is None or float(target_speed) != float(recovery_speed)
    ):
        sentence += f" Normal cruise target is {fmt_knots(recovery_speed)} knots."

    blocks = [sentence]

    own_type = own.get("colregs_vessel_type") or own.get("vessel_type")
    if own_type:
        vessel_type = str(own_type).replace("_", "-")
        blocks.append(f"Own ship is a {vessel_type} vessel.")

    mx = mission.get("x")
    my = mission.get("y")
    if mx is not None and my is not None:
        objective_text = f"The mission destination is ({fmt_meters(mx)}, {fmt_meters(my)}) metres."
        distance = mission.get("distance_m")
        heading_error = mission.get("heading_error_deg")
        if distance is not None:
            objective_text += f" It is {fmt_meters(distance)} metres away."
        if heading_error is not None:
            error = float(heading_error)
            objective_sector = bearing_sector(error)
            objective_text += f" It lies {objective_sector} of own ship."
        blocks.append(objective_text)

    blocks.append(
        "SAFETY LIMITS\n\n"
        f"A contact enters the caution area when CPA is below {fmt_meters(CAUTION_CPA_M)} "
        f"metres and TCPA is within {fmt0(PLANNING_TCPA_S)} seconds. Plan early action "
        f"before CPA falls below {fmt_meters(MIN_SAFE_CPA_M)} metres with TCPA within "
        f"{fmt0(INTERVENTION_TCPA_S)} seconds. A CPA below "
        f"{fmt_meters(CRITICAL_CPA_M)} metres is critical, especially when TCPA is "
        f"within {fmt0(EMERGENCY_TCPA_S)} seconds. Keep at least "
        f"{fmt_meters(BOW_CROSSING_M)} metres clear when crossing ahead or meeting head-on."
    )

    if (
        status == "underway"
        and target_speed is not None
        and recovery_speed is not None
        and float(target_speed) < float(recovery_speed)
    ):
        blocks.append(
            "REDUCED-SPEED STATE\n\n"
            f"The current target of {fmt_knots(target_speed)} knots is below the "
            f"normal cruise target of {fmt_knots(recovery_speed)} knots."
        )

    if (
        own.get("stopped")
        or own.get("paused")
        or status in {"paused", "stalled", "stopped"}
        or (speed is not None and float(speed) == 0.0)
    ):
        blocks.append(
            "STOPPED VESSEL STATE\n\n"
            "Own ship is not making way."
        )

    maneuver = state.get("current_maneuver") or {}
    if maneuver.get("action") == "alter_course":
        maneuver_lines = ["A temporary course-change waypoint is currently active."]
        degrees = maneuver.get("degrees")
        if degrees is not None and float(degrees) != 0.0:
            direction = "starboard" if float(degrees) > 0.0 else "port"
            maneuver_lines.append(
                f"Commanded alteration: {fmt0(abs(float(degrees)))} degrees to {direction}."
            )
        if maneuver.get("temporary_waypoint_active"):
            maneuver_lines.append(
                "The temporary waypoint is an intermediate helm target; "
                "it is not the mission destination."
            )
        blocks.append("CURRENT MANEUVER STATE\n\n" + "\n".join(maneuver_lines))

    if contacts:
        contact_lines = "\n".join(
            render_contact(c, stale_age_threshold) for c in contacts
        )
        blocks.append("CONTACTS\n\n" + contact_lines)
    else:
        blocks.append("CONTACTS\n\nNo contacts are currently tracked.")

    conditions = state.get("conditions") or {}
    condition_parts = []
    if conditions.get("visibility_condition"):
        condition_parts.append(
            "visibility is " + str(conditions["visibility_condition"]).replace("_", " ")
        )
    if conditions.get("visibility_range_m") is not None:
        condition_parts.append(
            "assessed visibility range is "
            f"{fmt_meters(conditions['visibility_range_m'])} metres"
        )
    if conditions.get("narrow_channel") is not None:
        condition_parts.append(
            "narrow-channel context is "
            + ("present" if conditions["narrow_channel"] else "not indicated")
        )
    if conditions.get("traffic_separation_scheme") is not None:
        condition_parts.append(
            "traffic-separation context is "
            + ("present" if conditions["traffic_separation_scheme"] else "not indicated")
        )
    if condition_parts:
        condition_text = ". ".join(
            part[0].upper() + part[1:] for part in condition_parts
        )
        blocks.append("OPERATING CONDITIONS\n\n" + condition_text + ".")

    return "\n\n".join(blocks)


def angular_delta(start, end):
    """Return the shortest signed turn from start to end in degrees."""
    return (float(end) - float(start) + 180.0) % 360.0 - 180.0


def mission_distance(state):
    own = state.get("own_ship") or {}
    mission = state.get("mission") or {}
    values = (own.get("x"), own.get("y"), mission.get("x"), mission.get("y"))
    if any(value is None for value in values):
        return None
    return math.hypot(float(values[2]) - float(values[0]), float(values[3]) - float(values[1]))


def contact_map(state):
    return {
        contact["name"]: contact
        for contact in state.get("contacts") or []
        if isinstance(contact, dict) and contact.get("name")
    }


def changed(a, b, tolerance=1e-9):
    return a is not None and b is not None and abs(float(b) - float(a)) > tolerance


def changed_by_at_least(a, b, minimum):
    return a is not None and b is not None and abs(float(b) - float(a)) >= minimum


def render_numeric_change(subject, label, start, end, unit, decimals=1):
    direction = "increased" if float(end) > float(start) else "decreased"
    if unit == "m":
        formatter = fmt_meters
        unit = "metres"
    else:
        formatter = fmt0 if decimals == 0 else (fmt2 if decimals == 2 else fmt1)
    suffix = f" {unit}" if unit else ""
    return (
        f"{subject} {label} {direction} from {formatter(start)}{suffix} "
        f"to {formatter(end)}{suffix}."
    )


def temporal_developments(states, stale_age_threshold=5.0, heading_change_threshold=1.5):
    """Render factual endpoint changes across a chronological state window."""
    if len(states) < 2:
        return []

    oldest, current = states[0], states[-1]
    lines = []
    old_own = oldest.get("own_ship") or {}
    new_own = current.get("own_ship") or {}

    if (changed(old_own.get("heading"), new_own.get("heading")) and
            abs(angular_delta(old_own["heading"], new_own["heading"])) >= heading_change_threshold):
        turn = angular_delta(old_own["heading"], new_own["heading"])
        direction = "starboard" if turn > 0 else "port"
        lines.append(
            f"Own ship heading changed from {fmt1(old_own['heading'])} to "
            f"{fmt1(new_own['heading'])} degrees ({fmt1(abs(turn))} degrees to {direction})."
        )
    if changed_by_at_least(old_own.get("speed"), new_own.get("speed"), 0.1):
        verb = "sped up" if float(new_own["speed"]) > float(old_own["speed"]) else "slowed"
        lines.append(
            f"Own ship has {verb} from {fmt_knots(old_own['speed'])} to "
            f"{fmt_knots(new_own['speed'])} knots."
        )

    old_status = old_own.get("movement_status")
    new_status = new_own.get("movement_status")
    if old_status and new_status and old_status != new_status:
        lines.append(f"Own ship movement status changed from {old_status} to {new_status}.")

    old_contacts = contact_map(oldest)
    new_contacts = contact_map(current)
    # Appearance/disappearance is an event between the immediately preceding
    # observation and now. Trends below still span oldest -> current.
    previous_contacts = contact_map(states[-2])
    for name in sorted(new_contacts.keys() - previous_contacts.keys()):
        lines.append(f"Contact {name} appeared.")
    for name in sorted(previous_contacts.keys() - new_contacts.keys()):
        lines.append(f"Contact {name} disappeared.")

    for name in sorted(old_contacts.keys() & new_contacts.keys()):
        old = old_contacts[name]
        new = new_contacts[name]
        if (
            new.get("track_reacquired")
            or old.get("track_generation", 0) != new.get("track_generation", 0)
        ):
            lines.append(f"{name} was reacquired with a fresh track.")
            continue
        quality = new.get("track_quality")
        estimated = quality in {"stale", "unreliable"} or (
            quality is None
            and new.get("age_s") is not None
            and float(new["age_s"]) > stale_age_threshold
        )
        trend_subject = (
            f"Based on the last-known {name} track, estimated"
            if estimated else name
        )
        if changed(old.get("range_m"), new.get("range_m")):
            lines.append(render_numeric_change(trend_subject, "range", old["range_m"], new["range_m"], "m", 0))
        old_sector = old.get("sector") or bearing_sector(old.get("relative_bearing_deg"))
        new_sector = new.get("sector") or bearing_sector(new.get("relative_bearing_deg"))
        if old_sector and new_sector and old_sector != new_sector:
            lines.append(
                f"{trend_subject} moved from the {old_sector} sector to the "
                f"{new_sector} sector."
            )
        if changed(old.get("closing_speed"), new.get("closing_speed")):
            lines.append(relative_motion_change(
                trend_subject, old["closing_speed"], new["closing_speed"]
            ))
        if (changed(old.get("heading"), new.get("heading")) and
                abs(angular_delta(old["heading"], new["heading"])) >= heading_change_threshold):
            turn = angular_delta(old["heading"], new["heading"])
            direction = "starboard" if turn > 0 else "port"
            lines.append(
                f"{trend_subject} heading changed from {fmt1(old['heading'])} to {fmt1(new['heading'])} "
                f"degrees ({fmt1(abs(turn))} degrees to {direction})."
            )
        if changed_by_at_least(old.get("speed"), new.get("speed"), 0.1):
            direction = "increased" if float(new["speed"]) > float(old["speed"]) else "decreased"
            lines.append(
                f"{trend_subject} speed {direction} from {fmt_knots(old['speed'])} "
                f"knots to {fmt_knots(new['speed'])} knots."
            )

        old_projection = old.get("projection_status")
        new_projection = new.get("projection_status")
        if old_projection == "valid" and new_projection == "no_future_cpa":
            if estimated:
                lines.append(f"{trend_subject} projection changed from a valid future CPA to no future CPA.")
            else:
                lines.append(f"{name} changed from a valid future CPA to no future CPA.")
        elif old_projection == "no_future_cpa" and new_projection == "valid":
            if estimated:
                lines.append(f"{trend_subject} projection changed from no future CPA to a valid future CPA.")
            else:
                lines.append(f"{name} changed from no future CPA to a valid future CPA.")
        elif old_projection == new_projection == "valid":
            if changed(old.get("cpa_distance_m"), new.get("cpa_distance_m")):
                lines.append(render_numeric_change(trend_subject, "projected CPA", old["cpa_distance_m"], new["cpa_distance_m"], "m", 0))
            if changed(old.get("tcpa_s"), new.get("tcpa_s")):
                lines.append(render_numeric_change(trend_subject, "TCPA", old["tcpa_s"], new["tcpa_s"], "seconds", 1))

        old_age = old.get("age_s")
        new_age = new.get("age_s")
        if (old_age is not None and new_age is not None and
                float(old_age) <= stale_age_threshold < float(new_age)):
            lines.append(
                f"{name} observation age increased from "
                f"{fmt1(old_age)} to {fmt1(new_age)} seconds."
            )

    return lines


def navigation_progress(states):
    if len(states) < 2:
        return []
    old_distance = mission_distance(states[0])
    new_distance = mission_distance(states[-1])
    if not changed_by_at_least(old_distance, new_distance, 1.0):
        return []
    return [
        render_numeric_change(
            "Mission distance", "", old_distance, new_distance, "m", 0
        ).replace("  ", " ")
    ]


def render_with_history(states, stale_age_threshold=5.0, heading_change_threshold=1.5):
    current = states[-1]
    narrative = "CURRENT BRIDGE STATE\n\n" + render_state(current, stale_age_threshold)
    start = states[0].get("observed_at_s")
    end = current.get("observed_at_s")
    # Temporal claims without a real elapsed interval are intentionally
    # suppressed; cycle numbers and file order are not clocks.
    if start is None or end is None or float(end) <= float(start):
        return narrative
    developments = temporal_developments(
        states, stale_age_threshold, heading_change_threshold
    )
    progress = navigation_progress(states)
    if not developments and not progress:
        return narrative

    blocks = [narrative]
    if developments:
        heading = (
            "RECENT TRAFFIC DEVELOPMENTS "
            f"(last {fmt1(float(end) - float(start))} seconds)"
        )
        blocks.append(heading + "\n\n" + "\n".join(f"- {line}" for line in developments))
    if progress:
        blocks.append("NAVIGATION PROGRESS\n\n" + "\n".join(progress))
    return "\n\n".join(blocks)


def extract_state(row):
    if isinstance(row.get("state"), dict):
        return row["state"]

    if isinstance(row.get("bridge_state"), dict):
        # This script is intended for the compact narration input.
        # Accepting bridge_state here makes failure messages friendlier,
        # but callers should normally use bridge.observations first.
        raise ValueError(
            "Row contains bridge_state rather than compact state. "
            "Run bridge.observations first."
        )

    raise ValueError("Row has no 'state' object")


def continues_sequence(previous_row, row, previous_state, state, max_gap_s):
    previous_source = previous_row.get("source_file")
    source = row.get("source_file")
    if previous_source is not None and source is not None and previous_source != source:
        return False

    previous_cycle = previous_row.get("cycle_id")
    cycle = row.get("cycle_id")
    if previous_cycle is not None and cycle is not None and float(cycle) <= float(previous_cycle):
        return False

    previous_time = previous_state.get("observed_at_s")
    observed_time = state.get("observed_at_s")
    if previous_time is None or observed_time is None:
        return False
    gap = float(observed_time) - float(previous_time)
    if gap <= 0 or gap > max_gap_s:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Deterministically render compact MOOS navigation states as bridge narratives."
    )
    parser.add_argument("input", help="Input JSONL, e.g. moos_narration_input.jsonl")
    parser.add_argument("output", help="Output JSONL containing state + narrative")
    parser.add_argument(
        "--sft-output",
        help="Optional JSONL of {input, output} pairs for seq2seq/SFT training",
    )
    parser.add_argument(
        "--text-output",
        help="Optional plain-text file containing narratives separated by dividers",
    )
    parser.add_argument(
        "--stale-age-threshold",
        type=float,
        default=5.0,
        help="Only verbalize contact age when age_s exceeds this many seconds (default: 5)",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=3,
        help="Number of chronological states used for temporal developments (default: 3)",
    )
    parser.add_argument(
        "--max-window-gap",
        type=float,
        default=120.0,
        help="Reset history when consecutive observations are farther apart (default: 120 seconds)",
    )
    parser.add_argument(
        "--heading-change-threshold",
        type=float,
        default=1.5,
        help="Minimum absolute heading change to verbalize (default: 1.5 degrees)",
    )
    args = parser.parse_args()
    if args.window_size < 1:
        parser.error("--window-size must be at least 1")
    if args.max_window_gap <= 0:
        parser.error("--max-window-gap must be positive")
    if args.heading_change_threshold < 0:
        parser.error("--heading-change-threshold cannot be negative")

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sft_path = Path(args.sft_output) if args.sft_output else None
    if sft_path:
        sft_path.parent.mkdir(parents=True, exist_ok=True)

    text_path = Path(args.text_output) if args.text_output else None
    if text_path:
        text_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    history = []
    previous_row = None
    previous_state = None

    with input_path.open("r", encoding="utf-8") as src, \
         output_path.open("w", encoding="utf-8") as dst, \
         (sft_path.open("w", encoding="utf-8") if sft_path else open("/dev/null", "w")) as sft, \
         (text_path.open("w", encoding="utf-8") if text_path else open("/dev/null", "w")) as txt:

        for lineno, line in enumerate(src, start=1):
            if not line.strip():
                continue

            row = json.loads(line)
            state = extract_state(row)
            if previous_row is not None and not continues_sequence(
                previous_row, row, previous_state, state, args.max_window_gap
            ):
                history = []
            history.append(state)
            history = history[-args.window_size:]
            narrative = render_with_history(
                history,
                args.stale_age_threshold,
                args.heading_change_threshold,
            )

            out_row = {
                "id": row.get("id"),
                "cycle_id": row.get("cycle_id"),
                "source_file": row.get("source_file"),
                "state": state,
                "narrative": narrative,
            }
            dst.write(json.dumps(out_row, ensure_ascii=False) + "\n")

            if sft_path:
                sft_row = {
                    "id": row.get("id"),
                    "cycle_id": row.get("cycle_id"),
                    "input": json.dumps(
                        state,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "output": narrative,
                }
                sft.write(json.dumps(sft_row, ensure_ascii=False) + "\n")

            if text_path:
                if count:
                    txt.write("\n" + "=" * 80 + "\n")
                txt.write(narrative + "\n")

            count += 1
            previous_row = row
            previous_state = state

    print(f"Rendered: {count}")
    print(f"Output:   {output_path}")
    if sft_path:
        print(f"SFT:      {sft_path}")
    if text_path:
        print(f"Text:     {text_path}")


if __name__ == "__main__":
    main()
