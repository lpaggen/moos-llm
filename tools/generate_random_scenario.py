#!/usr/bin/env python3
"""Generate reproducible, spatially diverse random multi-ship scenarios."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path


KINDS = (
    "distant",
    "diverging",
    "parallel",
    "crossing",
    "head_on",
    "we_are_overtaking_contact",
    "contact_overtaking_us",
    "stationary",
)
KIND_WEIGHTS = (0.18, 0.12, 0.12, 0.20, 0.12, 0.09, 0.09, 0.08)
DEFAULT_SCALE_M = 6_000.0
OWN_ROUTE_LEGS = 5
CONTACT_ROUTE_LEGS = 25
GUARANTEED_ENCOUNTERS = 12
OWN_SHIP_SPEED_KNOTS = 12.0
CONTACT_TYPES = (
    "ship",
    "sailing",
    "fishing",
    "not_under_command",
    "restricted_maneuverability",
    "constrained_by_draft",
)
CONTACT_TYPE_WEIGHTS = (0.55, 0.12, 0.12, 0.06, 0.08, 0.07)
TYPE_SPEED_RANGES_KNOTS = {
    "ship": (6.0, 20.0),
    "sailing": (3.0, 10.0),
    "fishing": (2.0, 8.0),
    "not_under_command": (0.2, 1.0),
    "restricted_maneuverability": (1.0, 6.0),
    "constrained_by_draft": (6.0, 14.0),
}
CLASSIC_KINDS = (
    "head_on",
    "crossing_starboard",
    "crossing_port",
    "we_are_overtaking_contact",
    "contact_overtaking_us",
)
CURRICULUM_VARIANTS = (
    ("canonical", 0.50),
    ("perturbed", 0.25),
    ("multi_contact", 0.15),
    ("recovery", 0.10),
)
CURRICULUM_CPA_BANDS_M = {
    "critical": (50.0, 200.0),
    "caution": (200.0, 500.0),
    "safe": (500.0, 800.0),
}


def _disk_point(rng: random.Random, radius: float) -> tuple[float, float]:
    distance = radius * math.sqrt(rng.random())
    angle = rng.uniform(0.0, 2.0 * math.pi)
    return distance * math.cos(angle), distance * math.sin(angle)


def _opposite_perimeter_objective(
    rng: random.Random,
    origin: tuple[float, float],
    radius: float,
) -> tuple[float, float]:
    if math.hypot(*origin) < radius * 0.05:
        origin_angle = rng.uniform(0.0, 2.0 * math.pi)
    else:
        origin_angle = math.atan2(origin[1], origin[0])
    angle = origin_angle + math.pi + rng.uniform(-math.pi / 3.0, math.pi / 3.0)
    objective_radius = rng.uniform(radius * 0.82, radius * 0.97)
    return objective_radius * math.cos(angle), objective_radius * math.sin(angle)


def _bearing(start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _point_along(
    point: tuple[float, float], heading: float, distance: float
) -> tuple[float, float]:
    radians = math.radians(heading)
    return point[0] + distance * math.sin(radians), point[1] + distance * math.cos(radians)


def _perimeter_objective_along_heading(
    point: tuple[float, float], heading: float, radius: float
) -> tuple[float, float]:
    radians = math.radians(heading)
    dx, dy = math.sin(radians), math.cos(radians)
    dot = point[0] * dx + point[1] * dy
    distance = -dot + math.sqrt(max(0.0, dot * dot + radius * radius - math.hypot(*point) ** 2))
    return point[0] + distance * dx, point[1] + distance * dy


def _timed_encounter_route(
    rng: random.Random,
    own_start: tuple[float, float],
    own_heading: float,
    own_speed_knots: float,
    contact_speed_knots: float,
    kind: str,
    side: str | None,
    radius: float,
    own_distance: float,
) -> tuple[tuple[float, float], tuple[float, float], float, tuple[float, float], float]:
    own_speed = own_speed_knots * 0.514444
    contact_speed = contact_speed_knots * 0.514444
    for attempt in range(200):
        feasible_distance = max(1100.0, own_distance * (0.985 ** attempt))
        encounter = _point_along(own_start, own_heading, feasible_distance)
        if kind == "head_on":
            heading = (own_heading + 180.0 + rng.uniform(-4.0, 4.0)) % 360.0
        else:
            direction = 1.0 if side == "starboard" else -1.0
            heading = (
                own_heading + direction * rng.uniform(65.0, 115.0)
            ) % 360.0
        eta = feasible_distance / own_speed
        start = _point_along(encounter, heading, -(contact_speed * eta))
        if math.hypot(*start) > radius:
            continue
        goal = _perimeter_objective_along_heading(
            encounter, heading, rng.uniform(radius * 0.82, radius * 0.97)
        )
        return start, goal, heading, encounter, feasible_distance
    raise RuntimeError("could not sample a timed encounter inside the scenario disk")


def _route(rng: random.Random, radius: float, minimum_length: float) -> tuple[tuple[float, float], tuple[float, float]]:
    for _ in range(200):
        start = _disk_point(rng, radius)
        goal = _opposite_perimeter_objective(rng, start, radius)
        if math.dist(start, goal) >= minimum_length:
            return start, goal
    raise RuntimeError("could not sample a sufficiently long route")


def _fmt_point(point: tuple[float, float]) -> str:
    return f"x={point[0]:.3f}, y={point[1]:.3f}"


def _fmt_start(point: tuple[float, float], heading: float) -> str:
    return f"{_fmt_point(point)}, heading={heading:.3f}"


def _continuing_route(
    rng: random.Random,
    first_goal: tuple[float, float],
    radius: float,
    minimum_length: float,
    legs: int,
) -> list[tuple[float, float]]:
    goals = [first_goal]
    while len(goals) < legs:
        for _ in range(200):
            candidate = _opposite_perimeter_objective(rng, goals[-1], radius)
            if math.dist(goals[-1], candidate) >= minimum_length:
                goals.append(candidate)
                break
        else:
            raise RuntimeError("could not sample a continuing route")
    return goals


def _fmt_route(goals: list[tuple[float, float]]) -> str:
    return "; ".join(_fmt_point(goal) for goal in goals)


def generate_classic(
    seed: int,
    contacts: int = 1,
    scale_m: float = 3_000.0,
    *,
    encounter_kinds: list[str] | None = None,
    cpa_offsets_m: list[float] | None = None,
    encounter_distances_m: list[float] | None = None,
    contact_speeds_knots: list[float] | None = None,
) -> tuple[str, dict]:
    """Generate randomized classic encounters along one circular-domain route."""
    if not 1 <= contacts <= 10:
        raise ValueError("classic profile requires 1 through 10 contacts")
    if scale_m <= 0:
        raise ValueError("scale_m must be positive")
    rng = random.Random(seed)
    own_speed_knots = OWN_SHIP_SPEED_KNOTS
    own_speed = own_speed_knots * 0.514444
    route_angle = rng.uniform(0.0, 2.0 * math.pi)
    route_radius = rng.uniform(scale_m * 0.82, scale_m * 0.9)
    own_start = (
        route_radius * math.cos(route_angle),
        route_radius * math.sin(route_angle),
    )
    own_goal = (-own_start[0], -own_start[1])
    own_heading = _bearing(own_start, own_goal)
    sections = [
        "[DEFAULT]",
        "type = ship",
        "color = white",
        "avoid = false",
        "",
        "[vehicle:LLM_SHIP]",
        "color = yellow",
        "avoid = true",
        f"speed = {own_speed:.4f}",
        f"max_speed = {own_speed:.4f}",
        f"start = {_fmt_start(own_start, own_heading)}",
        f"waypoint = {_fmt_point(own_goal)}",
        f"loiter = {_fmt_point(own_goal)}",
    ]
    metadata_contacts = []
    route_length = math.dist(own_start, own_goal)
    for index in range(1, contacts + 1):
        kind = encounter_kinds[index - 1] if encounter_kinds else rng.choice(CLASSIC_KINDS)
        if encounter_distances_m:
            encounter_distance = encounter_distances_m[index - 1]
        else:
            progress = (index + 0.5) / (contacts + 1.0)
            nominal_distance = route_length * (0.16 + 0.68 * progress)
            encounter_distance = nominal_distance + rng.uniform(-120.0, 120.0)

        for attempt in range(500):
            feasible_distance = max(scale_m * 0.18, encounter_distance * (0.99 ** attempt))
            encounter = _point_along(own_start, own_heading, feasible_distance)
            eta = feasible_distance / own_speed
            if kind == "head_on":
                contact_heading = (own_heading + 180.0 + rng.uniform(-3.0, 3.0)) % 360.0
                contact_speed_knots = rng.uniform(9.0, 15.0)
                cpa_offset = rng.uniform(-80.0, 80.0)
            elif kind == "crossing_starboard":
                contact_heading = (own_heading + rng.uniform(70.0, 110.0)) % 360.0
                contact_speed_knots = rng.uniform(8.0, 16.0)
                cpa_offset = rng.uniform(-180.0, 180.0)
            elif kind == "crossing_port":
                contact_heading = (own_heading - rng.uniform(70.0, 110.0)) % 360.0
                contact_speed_knots = rng.uniform(8.0, 16.0)
                cpa_offset = rng.uniform(-180.0, 180.0)
            elif kind == "we_are_overtaking_contact":
                contact_heading = (own_heading + rng.uniform(-2.0, 2.0)) % 360.0
                contact_speed_knots = rng.uniform(6.0, 9.5)
                cpa_offset = rng.uniform(-100.0, 100.0)
            else:
                contact_heading = (own_heading + rng.uniform(-2.0, 2.0)) % 360.0
                contact_speed_knots = rng.uniform(14.0, 18.0)
                cpa_offset = rng.uniform(-100.0, 100.0)

            if contact_speeds_knots:
                contact_speed_knots = contact_speeds_knots[index - 1]
            if cpa_offsets_m:
                cpa_offset = cpa_offsets_m[index - 1]

            contact_speed = contact_speed_knots * 0.514444
            contact_track_point = _point_along(
                encounter, (contact_heading + 90.0) % 360.0, cpa_offset
            )
            contact_start = _point_along(
                contact_track_point, contact_heading, -(contact_speed * eta)
            )
            if math.hypot(*contact_start) <= scale_m * 0.97:
                encounter_distance = feasible_distance
                break
        else:
            raise RuntimeError("could not sample a classic encounter inside the scenario disk")

        contact_goal = _perimeter_objective_along_heading(
            contact_track_point, contact_heading, scale_m * 0.92
        )
        name = f"RANDOM_TS{index}"
        sections.extend([
            "",
            f"[vehicle:{name}]",
            f"speed = {contact_speed:.4f}",
            f"max_speed = {contact_speed:.4f}",
            f"start = {_fmt_start(contact_start, contact_heading)}",
            f"waypoint = {_fmt_point(contact_goal)}",
            f"loiter = {_fmt_point(contact_goal)}",
        ])
        metadata_contacts.append({
            "name": name,
            "kind": kind,
            "vessel_type": "ship",
            "speed": round(contact_speed_knots, 4),
            "start": [round(value, 3) for value in contact_start],
            "goal": [round(value, 3) for value in contact_goal],
            "heading": round(contact_heading, 3),
            "designed_cpa_offset_m": round(abs(cpa_offset), 3),
            "encounter_eta_s": round(eta, 3),
        })
    metadata = {
        "generator_version": 7,
        "profile": "classic",
        "seed": seed,
        "contact_count": contacts,
        "scale_m": scale_m,
        "own_ship": {
            "speed": own_speed_knots,
            "start": [round(value, 3) for value in own_start],
            "goal": [round(value, 3) for value in own_goal],
            "heading": round(own_heading, 3),
        },
        "contacts": metadata_contacts,
    }
    return "\n".join(sections) + "\n", metadata


def _weighted_choice(rng: random.Random, choices: tuple[tuple[str, float], ...]) -> str:
    value = rng.random()
    cumulative = 0.0
    for name, weight in choices:
        cumulative += weight
        if value < cumulative:
            return name
    return choices[-1][0]


def generate_curriculum(seed: int, scale_m: float = 3_000.0) -> tuple[str, dict]:
    """Generate controlled, randomized training encounters distinct from Imazu."""
    rng = random.Random(seed ^ 0xC011E6)
    variant = _weighted_choice(rng, CURRICULUM_VARIANTS)
    if variant in {"canonical", "perturbed"}:
        contacts = 1
    elif variant == "multi_contact":
        contacts = rng.randint(2, 3)
    else:
        contacts = 2

    kinds = [rng.choice(CLASSIC_KINDS) for _ in range(contacts)]
    if variant == "multi_contact" and len(set(kinds)) == 1:
        kinds[-1] = CLASSIC_KINDS[(CLASSIC_KINDS.index(kinds[-1]) + 1) % len(CLASSIC_KINDS)]

    if variant == "canonical":
        band_names = ["caution"]
    elif variant == "perturbed":
        band_names = [rng.choice(("critical", "caution", "safe"))]
    elif variant == "multi_contact":
        controlling = rng.randrange(contacts)
        band_names = ["safe"] * contacts
        band_names[controlling] = rng.choice(("critical", "caution"))
    else:
        band_names = [rng.choice(("critical", "caution")), "safe"]

    offsets = []
    for band_name in band_names:
        low, high = CURRICULUM_CPA_BANDS_M[band_name]
        offsets.append(rng.choice((-1.0, 1.0)) * rng.uniform(low, high))

    scenario, metadata = generate_classic(
        seed,
        contacts,
        scale_m,
        encounter_kinds=kinds,
        cpa_offsets_m=offsets,
    )
    metadata.update({
        "generator_version": 8,
        "profile": "curriculum",
        "curriculum_variant": variant,
        "cpa_bands": band_names,
        "independent_of_imazu": True,
    })
    for contact, band_name in zip(metadata["contacts"], band_names):
        contact["designed_cpa_band"] = band_name
    return scenario, metadata


def generate_targeted(seed: int, scale_m: float = 3_000.0) -> tuple[str, dict]:
    """Generate candidates for rare, defensible stop and port-turn decisions."""
    rng = random.Random(seed ^ 0x57A9E7)
    encounter_distance = scale_m * 0.67
    if seed % 2 == 0:
        variant = "stop_crossing"
        kinds = ["crossing_starboard", "crossing_port"]
        speeds = [10.0, 10.0]
        clearance = rng.uniform(40.0, 90.0)
        offsets = [clearance, -clearance]
        target_action = "stop"
        design_intent = (
            "Two contacts approach the same future conflict point from opposite crossing "
            "directions, making a course alteration toward either side unattractive and "
            "taking all way off potentially defensible. The teacher remains free to choose "
            "another action."
        )
    else:
        variant = "port_overtaking"
        kinds = ["we_are_overtaking_contact", "contact_overtaking_us"]
        speeds = [8.0, 12.75]
        inner_offset = rng.uniform(60.0, 110.0)
        offsets = [inner_offset, inner_offset + rng.uniform(180.0, 260.0)]
        target_action = "alter_course_port"
        design_intent = (
            "Own ship overtakes a slower vessel on its starboard side while a faster vessel "
            "simultaneously overtakes farther to starboard. A starboard alteration would move "
            "toward the second contact, making a substantial port alteration away from both "
            "contacts defensible under Rules 8 and 13. The teacher remains free to choose "
            "another action."
        )

    scenario, metadata = generate_classic(
        seed,
        contacts=2,
        scale_m=scale_m,
        encounter_kinds=kinds,
        cpa_offsets_m=offsets,
        encounter_distances_m=[encounter_distance, encounter_distance],
        contact_speeds_knots=speeds,
    )
    metadata.update({
        "generator_version": 9,
        "profile": "targeted",
        "targeted_variant": variant,
        "target_action": target_action,
        "design_intent": design_intent,
        "teacher_label_forced": False,
        "independent_of_imazu": True,
    })
    return scenario, metadata


def generate(seed: int, contacts: int, scale_m: float = DEFAULT_SCALE_M) -> tuple[str, dict]:
    if contacts < 1:
        raise ValueError("contacts must be positive")
    if scale_m <= 0:
        raise ValueError("scale_m must be positive")

    rng = random.Random(seed)
    own_start, own_goal = _route(rng, scale_m * 0.92, scale_m * 0.65)
    own_speed_knots = OWN_SHIP_SPEED_KNOTS
    own_heading = _bearing(own_start, own_goal)
    own_first_leg_length = math.dist(own_start, own_goal)
    own_goals = _continuing_route(
        rng, own_goal, scale_m * 0.92, scale_m * 0.35, OWN_ROUTE_LEGS
    )
    guaranteed_count = min(GUARANTEED_ENCOUNTERS, contacts // 2)
    selected_kinds = rng.choices(
        ("crossing", "head_on"), weights=(0.7, 0.3), k=guaranteed_count
    )
    selected_kinds.extend(
        rng.choices(KINDS, weights=KIND_WEIGHTS, k=contacts - guaranteed_count)
    )

    sections = [
        "[DEFAULT]",
        "type = ship",
        "color = white",
        "avoid = false",
        "",
        "[vehicle:LLM_SHIP]",
        "color = yellow",
        "avoid = true",
        f"speed = {own_speed_knots * 0.514444:.4f}",
        f"max_speed = {own_speed_knots * 0.514444:.4f}",
        f"start = {_fmt_start(own_start, own_heading)}",
        f"waypoints = {_fmt_route(own_goals)}",
        f"loiter = {_fmt_point(own_goals[-1])}",
    ]
    metadata_contacts = []

    for index, kind in enumerate(selected_kinds, 1):
        if kind == "contact_overtaking_us":
            vessel_type = rng.choice(("ship", "constrained_by_draft"))
        elif kind == "stationary":
            vessel_type = rng.choice(("not_under_command", "restricted_maneuverability"))
        else:
            vessel_type = rng.choices(
                CONTACT_TYPES, weights=CONTACT_TYPE_WEIGHTS, k=1
            )[0]
        side = None
        if kind == "crossing":
            side = rng.choice(("port", "starboard"))
        if kind == "we_are_overtaking_contact":
            type_minimum, type_maximum = TYPE_SPEED_RANGES_KNOTS[vessel_type]
            speed_knots = rng.uniform(
                type_minimum, min(type_maximum, own_speed_knots * 0.85)
            )
        elif kind == "contact_overtaking_us":
            speed_knots = rng.uniform(own_speed_knots * 1.15, own_speed_knots * 1.55)
        elif kind == "stationary":
            speed_knots = 0.2
        else:
            speed_knots = rng.uniform(*TYPE_SPEED_RANGES_KNOTS[vessel_type])

        engineered = index <= guaranteed_count
        encounter_point = None
        encounter_distance = None
        minimum_length = scale_m * (0.18 if kind == "stationary" else 0.4)
        if engineered:
            progress = (index - 0.5) / guaranteed_count
            encounter_distance = min(
                own_first_leg_length * (0.14 + 0.68 * progress),
                own_first_leg_length - 900.0,
            )
            encounter_distance = max(1100.0, encounter_distance)
            if kind == "head_on":
                speed_ratio = speed_knots / own_speed_knots
                encounter_distance = min(
                    encounter_distance,
                    own_first_leg_length * 0.88 / (1.0 + speed_ratio),
                )
            start, goal, heading, encounter_point, encounter_distance = _timed_encounter_route(
                rng,
                own_start,
                own_heading,
                own_speed_knots,
                speed_knots,
                kind,
                side,
                scale_m * 0.97,
                encounter_distance,
            )
        else:
            start, goal = _route(rng, scale_m * 0.97, minimum_length)
            heading = _bearing(start, goal)
        if kind == "stationary":
            goal = start
            heading = rng.uniform(0.0, 360.0)
        goals = _continuing_route(
            rng, goal, scale_m * 0.97, scale_m * 0.3, CONTACT_ROUTE_LEGS
        )

        name = f"RANDOM_TS{index}"
        sections.extend([
            "",
            f"[vehicle:{name}]",
            f"type = {vessel_type}",
            f"speed = {speed_knots * 0.514444:.4f}",
            f"max_speed = {speed_knots * 0.514444:.4f}",
            f"start = {_fmt_start(start, heading)}",
            f"waypoints = {_fmt_route(goals)}",
            f"loiter = {_fmt_point(goals[-1])}",
        ])
        metadata_contacts.append({
            "name": name,
            "kind": kind,
            "vessel_type": vessel_type,
            "side": side,
            "speed": round(speed_knots, 4),
            "start": [round(value, 3) for value in start],
            "goal": [round(value, 3) for value in goal],
            "goals": [
                [round(value, 3) for value in objective]
                for objective in goals
            ],
            "heading": round(heading, 3),
            "engineered_encounter": engineered,
            "encounter_point": (
                [round(value, 3) for value in encounter_point]
                if encounter_point is not None else None
            ),
            "encounter_distance_along_own_route_m": (
                round(encounter_distance, 3)
                if encounter_distance is not None else None
            ),
        })

    metadata = {
        "generator_version": 6,
        "seed": seed,
        "contact_count": contacts,
        "scale_m": scale_m,
        "position_sampling": "uniform_area_disk",
        "objective_sampling": "opposite_perimeter_annulus",
        "guaranteed_encounters": guaranteed_count,
        "own_ship": {
            "speed": round(own_speed_knots, 4),
            "start": [round(value, 3) for value in own_start],
            "goal": [round(value, 3) for value in own_goal],
            "goals": [
                [round(value, 3) for value in objective]
                for objective in own_goals
            ],
            "heading": round(own_heading, 3),
        },
        "contacts": metadata_contacts,
    }
    return "\n".join(sections) + "\n", metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--contacts", type=int)
    parser.add_argument("--min-contacts", type=int, default=1)
    parser.add_argument("--max-contacts", type=int, default=50)
    parser.add_argument("--scale-m", type=float, default=DEFAULT_SCALE_M)
    parser.add_argument(
        "--profile",
        choices=("stress", "classic", "curriculum", "targeted"),
        default="stress",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.contacts is None:
        if not 1 <= args.min_contacts <= args.max_contacts:
            parser.error("require 1 <= min-contacts <= max-contacts")
        contacts = random.Random(args.seed ^ 0x5F3759DF).randint(
            args.min_contacts, args.max_contacts
        )
    else:
        contacts = args.contacts
    if args.profile == "curriculum":
        scenario, metadata = generate_curriculum(args.seed, args.scale_m)
    elif args.profile == "targeted":
        scenario, metadata = generate_targeted(args.seed, args.scale_m)
    elif args.profile == "classic":
        scenario, metadata = generate_classic(args.seed, contacts, args.scale_m)
    else:
        scenario, metadata = generate(args.seed, contacts, args.scale_m)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(scenario, encoding="utf-8")
    args.output.with_suffix(".json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
