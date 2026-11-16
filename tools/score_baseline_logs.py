#!/usr/bin/env python3
"""Frozen Imazu scoring with explicit telemetry reconstruction policies.

Contract version ``imazu-frozen-v1`` adds citation agreement as a 0.10 axis by
splitting the former 0.20 physical-compliance allocation evenly. Observed
heading/speed changes proxy an unavailable command stream. Contact geometry is
interpolated only inside each contact's recorded time span.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import configparser
import csv
import json
import math
from pathlib import Path
import statistics

try:
    from tools.analyze_baseline_logs import (analyze, case_log_paths, load_events,
                                             point, run_time_warp,
                                             timed_bridge_states)
    from tools.evaluate_colregs_citations import expected_rules, extract_rule_citations
    from tools.evaluate_run import (_canonical_findings, compliance_axis,
                                    heading_delta, interp_xy,
                                    manoeuvre_axis_from_decisions)
except ModuleNotFoundError:
    from analyze_baseline_logs import (analyze, case_log_paths, load_events, point,
                                       run_time_warp, timed_bridge_states)
    from evaluate_colregs_citations import expected_rules, extract_rule_citations
    from evaluate_run import (_canonical_findings, compliance_axis,
                              heading_delta, interp_xy,
                              manoeuvre_axis_from_decisions)


SCORING_CONTRACT_VERSION = "imazu-frozen-v1"
PAUSE_ADJUSTED_CONTRACT_VERSION = "imazu-lockstep-v2"
FROZEN_WEIGHTS = {
    "safety": 0.35,
    "compliance": 0.10,
    "citation": 0.10,
    "temporal": 0.10,
    "spatial": 0.10,
    "manoeuvre": 0.10,
    "smoothness": 0.15,
}


def citation_axis(events):
    """Score explicit rule citations against each cycle's Eriksen rule set.

    The score is macro F1 over oracle cycles. In Captain-critic runs, the
    Captain's final proposed decision is evaluated because that is the decision
    sent for validation/execution; otherwise the OOW decision is evaluated.
    Missing final decisions and missing citations receive zero whenever the
    oracle expects rules. Empty prediction against an empty oracle set scores 1.
    """
    oracles = {
        row.get("cycle_id"): row.get("classification") or {}
        for row in events
        if row.get("event") == "oow_colregs_oracle"
        and row.get("cycle_id") is not None
    }
    captain_mode = any(row.get("event") == "captain_decision" for row in events)
    decision_event = "captain_decision" if captain_mode else "oow_decision"
    decisions = {
        row.get("cycle_id"): row.get("decision") or {}
        for row in events
        if row.get("event") == decision_event
        and row.get("cycle_id") is not None
    }
    records = []
    for cycle in sorted(oracles):
        primary, expected = expected_rules(oracles[cycle])
        decision = decisions.get(cycle)
        cited = (
            extract_rule_citations(str(decision.get("reason") or ""))
            if decision is not None else set()
        )
        correct = cited & expected
        precision = len(correct) / len(cited) if cited else (1.0 if not expected else 0.0)
        recall = len(correct) / len(expected) if expected else (1.0 if not cited else 0.0)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        records.append({
            "cycle_id": cycle,
            "primary_contact": primary,
            "decision_present": decision is not None,
            "expected_rules": sorted(expected),
            "cited_rules": sorted(cited),
            "correct_rules": sorted(correct),
            "missing_rules": sorted(expected - cited),
            "unsupported_rules": sorted(cited - expected),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        })
    score_value = statistics.mean(record["f1"] for record in records) if records else 0.0
    return {
        "score": score_value,
        "decision_source": decision_event,
        "oracle_cycles": len(records),
        "decision_cycles": sum(record["decision_present"] for record in records),
        "missing_decision_cycles": sum(not record["decision_present"] for record in records),
        "macro_precision": (
            statistics.mean(record["precision"] for record in records) if records else 0.0
        ),
        "macro_recall": (
            statistics.mean(record["recall"] for record in records) if records else 0.0
        ),
        "macro_f1": score_value,
        "records": records,
    }


def velocity(row):
    angle = math.radians(row["heading"])
    return row["speed"] * math.sin(angle), row["speed"] * math.cos(angle)


def relative_bearing(own, contact):
    return (math.degrees(math.atan2(contact["x"] - own["x"], contact["y"] - own["y"])) - own["heading"]) % 360


def wrong_side_at_closest(own, contact, kind):
    """Whether realized closest-approach geometry is on the prohibited side."""
    bearing = relative_bearing(own, contact)
    if kind == "head_on":
        return 0 < bearing < 180
    forward = (
        (contact["x"] - own["x"]) * math.sin(math.radians(own["heading"]))
        + (contact["y"] - own["y"]) * math.cos(math.radians(own["heading"]))
    )
    # In a starboard crossing give-way encounter, passing astern leaves the
    # stand-on contact ahead of own ship at the crossing. Contact astern means
    # own ship crossed ahead, which is the prohibited realization.
    return forward < 0


def encounter(own, contact):
    bearing = relative_bearing(own, contact)
    difference = abs(heading_delta(contact["heading"], own["heading"]))
    if contact["speed"] <= 0.2:
        return "stationary", "give_way"
    if (bearing <= 15 or bearing >= 345) and difference >= 150:
        return "head_on", "give_way"
    if difference <= 30:
        if 112.5 < relative_bearing(contact, own) < 247.5:
            return "overtaking", "give_way"
        if 112.5 < bearing < 247.5:
            return "overtaking", "stand_on"
    return "crossing", "give_way" if 0 < bearing < 180 else "stand_on"


def contact_at(track, time):
    if not track[0]["time"] <= time <= track[-1]["time"]:
        return None
    i = bisect_right([row["time"] for row in track], time) - 1
    a = track[i]
    result = dict(a)
    result["x"], result["y"] = interp_xy(track, time)
    if i < len(track) - 1:
        b = track[i + 1]
        fraction = (time - a["time"]) / (b["time"] - a["time"])
        result["heading"] = (a["heading"] + fraction * heading_delta(b["heading"], a["heading"])) % 360
        result["speed"] = a["speed"] + fraction * (b["speed"] - a["speed"])
    return result


def ground_truth(own, contact, safe_distance):
    ovx, ovy = velocity(own)
    cvx, cvy = velocity(contact)
    dx, dy = contact["x"] - own["x"], contact["y"] - own["y"]
    vx, vy = cvx - ovx, cvy - ovy
    v2 = vx * vx + vy * vy
    tcpa = -(dx * vx + dy * vy) / v2 if v2 > 1e-10 else 0.0
    cpa = math.hypot(dx + vx * tcpa, dy + vy * tcpa)
    # Explicit reconstruction policy: two safe-distance lengths at relative
    # speed, with a 60-second floor. The source simulator's helper is absent.
    horizon = max(60.0, 2 * safe_distance / max(math.sqrt(v2), 0.1))
    band = "passed" if tcpa < 0 else "safe" if cpa >= safe_distance else "acute" if tcpa <= horizon else "early"
    kind, role = encounter(own, contact)
    return dict(cpa=cpa, tcpa=tcpa, band=band, kind=kind, role=role,
                relative_bearing=relative_bearing(own, contact), risk_horizon_s=horizon)


def inferred_action(a, b, heading_deadband=1.0, speed_deadband=0.1):
    change = heading_delta(b["heading"], a["heading"])
    speed_change = b["speed"] - a["speed"]
    if abs(change) >= heading_deadband:
        return "turn_right" if change > 0 else "turn_left", abs(change)
    if abs(speed_change) >= speed_deadband:
        return "speed_up" if speed_change > 0 else "slow_down", 0.0
    return "hold_course", 0.0


def action_from_decision(decision, current_speed=0.0):
    """Map an audited executable decision onto the retrospective action classes."""
    action = decision.get("action")
    if action == "alter_course":
        degrees = float(decision.get("degrees") or 0.0)
        return ("turn_right" if degrees > 0 else "turn_left"), abs(degrees)
    if action == "set_speed":
        requested = float(decision.get("speed") or 0.0)
        return ("speed_up" if requested > current_speed else "slow_down"), 0.0
    if action == "stop":
        return "slow_down", 0.0
    if action == "resume":
        return "speed_up", 0.0
    # maintain_course, skip, and rejoin_route do not themselves specify a new
    # relative collision-avoidance turn. Any resulting route-controller helm
    # motion must not be misattributed to the model as a port/starboard order.
    return "hold_course", 0.0


def action_serves_give_way(action, truth):
    """Whether an inferred control can reasonably serve this give-way encounter."""
    if truth["role"] != "give_way" or truth["band"] not in {"acute", "early"}:
        return False
    if action == "slow_down":
        return True
    if action == "turn_right":
        return True
    # An overtaking vessel may pass on either side when the manoeuvre is safe.
    return action == "turn_left" and truth["kind"] == "overtaking"


def compliance_findings_for_step(
    action, degrees, truths, step, *, active_avoidance=False
):
    """Attribute retrospective findings across all simultaneous encounters."""
    findings = []
    if degrees > 120:
        findings.append(dict(code="C_degrees_over_limit", step=step,
                             inferred_action=action, ground_truth=None))
    active = [truth for truth in truths if truth["band"] in {"acute", "early"}]
    acute = [truth for truth in active if truth["band"] == "acute"]
    justified = [truth for truth in active if action_serves_give_way(action, truth)]
    # Rule 17(b) requires the stand-on vessel to act once collision cannot be
    # avoided by the give-way vessel alone. A safe starboard alteration or
    # speed reduction for one acute stand-on encounter can therefore justify
    # deviation relative to another, earlier stand-on contact.
    justified.extend(
        truth for truth in acute
        if truth["role"] == "stand_on"
        and action in {"turn_right", "slow_down"}
    )
    justified.extend(
        truth for truth in truths
        if truth.get("recorded_requires_action")
        and action in {"turn_right", "slow_down"}
    )
    if action == "turn_left":
        wrong = [truth for truth in active
                 if truth["role"] == "give_way"
                 and truth["kind"] in {"head_on", "crossing"}]
        if wrong:
            decisive = min(wrong, key=lambda truth: truth["cpa"])
            findings.append(dict(code="B_wrong_direction", step=step,
                                 inferred_action=action, ground_truth=decisive))
    if action == "hold_course" and acute and not active_avoidance:
        decisive = min(acute, key=lambda truth: truth["cpa"])
        findings.append(dict(code="D_no_action_when_required", step=step,
                             inferred_action=action, ground_truth=decisive))
    stand_on_early = [truth for truth in active
                      if truth["role"] == "stand_on" and truth["band"] == "early"]
    if action != "hold_course" and stand_on_early and not justified:
        decisive = min(stand_on_early, key=lambda truth: truth["cpa"])
        findings.append(dict(code="B_17c", step=step,
                             inferred_action=action, ground_truth=decisive))
    if action == "turn_left":
        port_contacts = [truth for truth in active
                         if truth["kind"] in {"head_on", "crossing"}
                         and truth["relative_bearing"] > 180]
        if port_contacts:
            decisive = min(port_contacts, key=lambda truth: truth["cpa"])
            findings.append(dict(code="P_port_toward_contact", step=step,
                                 inferred_action=action, ground_truth=decisive))
    return findings


def score(case_dir, safe_distance=500.0, heading_deadband=1.0, speed_deadband=0.1,
          *, exclude_decision_pauses=False):
    # Preserve every frozen-v1 geometry and motion axis.  Lock-step v2 changes
    # only the defective temporal denominator so the contract has one explicit
    # independent-variable change.
    result = analyze(case_dir, safe_distance)
    temporal_result = (
        analyze(case_dir, safe_distance, exclude_decision_pauses=True)
        if exclude_decision_pauses else result
    )
    _, events = load_events(case_dir)
    timed_states = timed_bridge_states(
        events,
        exclude_decision_pauses=False,
        time_warp=run_time_warp(case_dir),
    )
    states = [state for _, state, _ in timed_states]
    origin = states[0]["moos_time"] - timed_states[0][2]
    own = []
    for event, state, pause_sim_s in timed_states:
        roles = {
            str(encounter.get("contact")): str(encounter.get("own_role"))
            for encounter in state.get("colregs_context", {}).get("encounters", [])
            if isinstance(encounter, dict) and encounter.get("contact")
        }
        recorded_contacts = {
            str(contact.get("name")): {
                "role": roles.get(str(contact.get("name"))),
                "risk": contact.get("risk"),
                "cpa": contact.get("cpa_distance"),
                "tcpa": contact.get("tcpa_s"),
                "closing_speed": contact.get("closing_speed"),
            }
            for contact in state.get("contacts", [])
            if isinstance(contact, dict) and contact.get("name")
        }
        own.append(dict(
            time=state["moos_time"] - pause_sim_s - origin,
            cycle_id=event.get("cycle_id"),
            active_avoidance=bool(
                state.get("action_underway")
                or state.get("temporary_waypoint")
                or (state.get("current_action") or {}).get("action") == "alter_course"
            ),
            recorded_contacts=recorded_contacts,
            **state["nav"],
        ))
    explicit_decisions = {
        row.get("cycle_id"): row["decision"]
        for row in events
        if row.get("event") == "captain_validated"
        and isinstance(row.get("decision"), dict)
        and row.get("cycle_id") is not None
    }
    used_explicit_actions = bool(explicit_decisions)
    tracks = {}
    for _, state, pause_sim_s in timed_states:
        for contact in state.get("contacts", []):
            if contact.get("ready") is False or not contact.get("nav"):
                continue
            time = contact["moos_time"] - pause_sim_s - origin
            tracks.setdefault(contact["name"], {})[time] = dict(time=time, **contact["nav"])
    tracks = {name: sorted(rows.values(), key=lambda row: row["time"]) for name, rows in tracks.items()}
    actions, findings = [], []
    for step, (a, b) in enumerate(zip(own, own[1:]), 1):
        explicit = explicit_decisions.get(a.get("cycle_id"))
        if explicit is not None:
            action, degrees = action_from_decision(explicit, a["speed"])
        else:
            action, degrees = inferred_action(a, b, heading_deadband, speed_deadband)
        actions.append(action)
        truth = []
        for name, track in tracks.items():
            contact = contact_at(track, a["time"])
            if contact is None:
                continue
            assessment = ground_truth(a, contact, safe_distance)
            assessment["contact"] = name
            recorded = a.get("recorded_contacts", {}).get(name, {})
            recorded_tcpa = recorded.get("tcpa")
            assessment["recorded_role"] = recorded.get("role")
            assessment["recorded_risk"] = recorded.get("risk")
            assessment["recorded_requires_action"] = bool(
                recorded.get("risk") == "critical"
                and recorded_tcpa is not None
                and 0.0 <= float(recorded_tcpa) <= 300.0
                and float(recorded.get("closing_speed") or 0.0) > 0.0
            )
            truth.append(assessment)
        findings.extend(compliance_findings_for_step(
            action,
            degrees,
            truth,
            step,
            active_avoidance=bool(a.get("active_avoidance")),
        ))
    config = configparser.ConfigParser()
    config.read(case_dir / "scenario.ini")
    own_section = config["vehicle:LLM_SHIP"]
    initial_own = dict(zip(("x", "y"), point(own_section["start"])))
    initial_own.update(heading=float(dict(part.strip().split("=", 1) for part in own_section["start"].split(","))["heading"]), speed=float(own_section["speed"]))
    wrong_side_details = []
    for name, track in tracks.items():
        section = config[f"vehicle:{name}"]
        start_fields = dict(part.strip().split("=", 1) for part in section["start"].split(","))
        initial_contact = {key: float(start_fields[key]) for key in ("x", "y", "heading")}
        initial_contact["speed"] = float(section["speed"])
        kind, role = encounter(initial_own, initial_contact)
        if kind != "head_on" and not (kind == "crossing" and role == "give_way"):
            continue
        lo, hi = max(own[0]["time"], track[0]["time"]), min(own[-1]["time"], track[-1]["time"])
        closest = None
        for index in range(max(0, math.ceil(hi - lo)) + 1):
            time = min(lo + index, hi)
            a, c = contact_at(own, time), contact_at(track, time)
            if a is None or c is None:
                continue
            distance = math.hypot(c["x"] - a["x"], c["y"] - a["y"])
            if closest is None or distance < closest[0]:
                closest = distance, time, a, c
        if closest:
            distance, time, a, c = closest
            bearing = relative_bearing(a, c)
            wrong = wrong_side_at_closest(a, c, kind)
            if wrong:
                wrong_side_details.append(dict(contact=name, encounter=kind, time_s=time, range_m=distance, relative_bearing=bearing))
    if wrong_side_details:
        findings.append(dict(code="P_wrong_side_pass", contacts=wrong_side_details))
    overlaps = [r["minimum_interpolated_range_m"] for r in result["contact_overlap_ranges"].values() if r["minimum_interpolated_range_m"] is not None]
    # Retain the smaller of the high-rate latched observation and reconstructed
    # overlaps so coarse interpolation cannot erase an observed near miss.
    minimum = min(overlaps + [result["minimum_logged_range_m"]])
    collision = minimum < 15
    if not collision and minimum < safe_distance:
        findings.append(dict(code="cpa_violation"))
    findings = _canonical_findings(findings)
    compliance = compliance_axis(findings, collision)
    citation = citation_axis(events)
    manoeuvre = manoeuvre_axis_from_decisions(actions, len(tracks))
    est = result["interpolated_goal_crossing_estimate"]
    temporal_est = temporal_result["interpolated_goal_crossing_estimate"]
    # The helm's terminal completion event is authoritative when coarse
    # polyline reconstruction misses a capture/slip event (cases 13 and 14).
    arrived = result["mission_complete"] or est["arrived"]
    temporal, spatial = temporal_est["temporal_score"], est["spatial_score"]
    arrival_source = "interpolated_goal_crossing" if est["arrived"] else "not_arrived"
    if arrived and not est["arrived"]:
        straight = math.dist(point(own_section["start"]), point(own_section["waypoint"]))
        temporal = min(
            1.0,
            straight
            / float(own_section["speed"])
            / temporal_result["recording_duration_s"],
        )
        spatial = min(1.0, straight / result["full_recorded_path_m"])
        arrival_source = "logged_mission_complete"
    axes = dict(safety=min(1.0, minimum / safe_distance), compliance=compliance["score"],
                citation=citation["score"],
                temporal=temporal, spatial=spatial, manoeuvre=manoeuvre["manoeuvre_score"],
                smoothness=result["smoothness"]["smoothness_score"])
    if collision:
        composite, verdict = 0.0, "FAIL -- collision occurred"
    elif not arrived:
        composite = min(0.2, sum(FROZEN_WEIGHTS[key] * axes[key] for key in ("compliance", "citation", "manoeuvre", "smoothness")))
        verdict = "FAIL -- did not reach the goal"
    else:
        composite = sum(FROZEN_WEIGHTS[key] * axes[key] for key in FROZEN_WEIGHTS)
        verdict = "PASS_WITH_CPA_VIOLATION" if minimum < safe_distance else "PASS"
    return dict(case=result["case"], composite_score=composite, verdict=verdict, axes=axes,
                min_cpa_m=minimum, arrival_source=arrival_source,
                manoeuvre_details=manoeuvre, compliance_breakdown=compliance["breakdown"],
                citation_details=citation,
                findings=findings, inferred_action_counts=dict(Counter(actions)),
                explanation_compliance=None,
                action_source=("audited captain_validated decisions"
                               if used_explicit_actions else
                               "heading/speed reconstruction"),
                source=result["source"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--safe-distance", type=float, default=500.0)
    parser.add_argument("--heading-deadband", type=float, default=1.0)
    parser.add_argument("--speed-deadband", type=float, default=0.1)
    parser.add_argument(
        "--exclude-decision-pauses",
        action="store_true",
        help=("Use the lock-step zero-latency timeline by subtracting logged "
              "decision pauses multiplied by the run's time warp"),
    )
    args = parser.parse_args()
    cases = [score(path.parent, args.safe_distance, args.heading_deadband, args.speed_deadband,
                   exclude_decision_pauses=args.exclude_decision_pauses)
             for path in case_log_paths(args.run)]
    if not cases:
        parser.error(f"{args.run}: no case_*/audit.jsonl or telemetry.jsonl logs found")
    completed = [r for r in cases if r["verdict"].startswith("PASS")]
    summary = dict(cases=len(cases), mean_composite_score=statistics.mean(r["composite_score"] for r in cases),
                   mean_completed_score=(statistics.mean(r["composite_score"] for r in completed)
                                         if completed else None),
                   verdicts=dict(Counter(r["verdict"] for r in cases)),
                   mean_axes={key: statistics.mean(r["axes"][key] for r in cases) for key in FROZEN_WEIGHTS},
                   weights=FROZEN_WEIGHTS,
                   scoring_contract_version=(PAUSE_ADJUSTED_CONTRACT_VERSION
                                             if args.exclude_decision_pauses
                                             else SCORING_CONTRACT_VERSION),
                   safe_distance_m=args.safe_distance,
                   heading_deadband_deg=args.heading_deadband, speed_deadband_mps=args.speed_deadband,
                   score_kind="frozen retrospective physical audit plus Eriksen citation-set agreement",
                   policies=["No explanation deductions; explanations do not enter composite",
                             "One inferred action per observed interval; heading changes take priority over speed changes",
                             "Geometry risk horizon=max(60s,2*safe_distance/relative_speed); source helper unavailable",
                             "COLREG head-on bearing 15deg, reciprocal heading tolerance 30deg; overtaking abaft 112.5deg",
                             "Passing-side classification uses scenario initial geometry and realized closest approach",
                             "Contact positions use native timestamps; no extrapolation outside recorded spans",
                             "Clearance=min(latched logged range, one-second interpolated contact-overlap ranges)",
                             "Arrival uses interpolated 20m goal crossing, falling back to logged mission completion",
                             "Citation axis is macro per-cycle F1 against Eriksen applicable_rules union persistent Rules 2, 5, 6, and 7 on every cycle",
                             "Citation source is captain_decision in Captain-critic runs and oow_decision otherwise; absent oracle coverage scores zero",
                             "Initial unrecorded travel omitted, matching recorded-run elapsed time and path convention",
                             "All 22 cases equally weighted, including capped non-arrivals"])
    if args.exclude_decision_pauses:
        summary["policies"].insert(
            0,
            "Temporal efficiency alone excludes lock-step decision pauses using logged wall pause duration multiplied by manifest time warp; all other axes retain frozen-v1 reconstruction",
        )
    args.output.mkdir(parents=True, exist_ok=True)
    summary["policies"].insert(
        0,
        "Prefer audited captain_validated decisions; infer heading/speed actions only when absent",
    )
    (args.output / "scores.json").write_text(json.dumps(dict(summary=summary, cases=cases), indent=2, allow_nan=False) + "\n")
    with (args.output / "scores.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case", "composite_score", "verdict", "min_cpa_m", *FROZEN_WEIGHTS])
        for row in cases:
            writer.writerow([row["case"], row["composite_score"], row["verdict"], row["min_cpa_m"], *[row["axes"][key] for key in FROZEN_WEIGHTS]])
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
