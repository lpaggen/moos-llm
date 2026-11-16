#!/usr/bin/env python3
"""Validate fleet.ini and render the line-oriented files used by launch.sh."""

from __future__ import annotations

import argparse
import configparser
import math
import os
from pathlib import Path
import re
import tempfile


NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
OUTPUTS = {
    "names": "vnames.txt",
    "types": "vtypes.txt",
    "avoids": "vavoids.txt",
    "colors": "vcolors.txt",
    "speeds": "vspeeds.txt",
    "max_speeds": "vmaxspeeds.txt",
    "starts": "vpositions.txt",
    "waypoints": "vwaypoints.txt",
    "routes": "vroutes.txt",
    "loiters": "vloiterpos.txt",
}


def finite_number(value: str, context: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{context}: expected a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{context}: value must be finite")
    return number


def parse_position(value: str, keys: tuple[str, ...], context: str) -> str:
    fields: dict[str, float] = {}
    for item in value.split(","):
        if "=" not in item:
            raise ValueError(f"{context}: expected key=value fields")
        key, raw = (part.strip() for part in item.split("=", 1))
        if key not in keys:
            raise ValueError(f"{context}: unexpected field {key!r}")
        if key in fields:
            raise ValueError(f"{context}: duplicate field {key!r}")
        fields[key] = finite_number(raw, f"{context}.{key}")

    missing = [key for key in keys if key not in fields]
    if missing:
        raise ValueError(f"{context}: missing {', '.join(missing)}")
    return ",".join(f"{key}={fields[key]:g}" for key in keys)


def parse_waypoint_list(value: str, context: str) -> list[tuple[str, str]]:
    waypoints: list[tuple[str, str]] = []
    for raw_item in value.split(";"):
        item = raw_item.strip()
        if not item:
            continue
        parsed = parse_position(item, ("x", "y"), context)
        fields = dict(part.split("=", 1) for part in parsed.split(","))
        waypoints.append((fields["x"], fields["y"]))

    if not waypoints:
        raise ValueError(f"{context}: expected at least one waypoint")
    return waypoints


def load_fleet(config_path: Path, amount: int | None, fast: bool) -> dict[str, list[str]]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with config_path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error) as exc:
        raise ValueError(f"cannot read {config_path}: {exc}") from exc

    sections = [section for section in parser.sections() if section.startswith("vehicle:")]
    unknown = [section for section in parser.sections() if not section.startswith("vehicle:")]
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(unknown)}")

    enabled: list[str] = []
    for section in sections:
        try:
            is_enabled = parser.getboolean(section, "enabled", fallback=True)
        except ValueError as exc:
            raise ValueError(f"[{section}] enabled must be true or false") from exc
        if is_enabled:
            enabled.append(section)

    if not enabled:
        raise ValueError("fleet contains no enabled vehicles")
    if amount is None:
        amount = len(enabled)
    if amount < 1 or amount > len(enabled):
        raise ValueError(f"requested {amount} vehicle(s), but fleet has {len(enabled)} enabled")

    rows = {key: [] for key in OUTPUTS}
    seen_names: set[str] = set()
    for section in enabled[:amount]:
        name = section.removeprefix("vehicle:").strip()
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"[{section}] name must match {NAME_RE.pattern}")
        if name.casefold() in seen_names:
            raise ValueError(f"duplicate vehicle name: {name}")
        seen_names.add(name.casefold())

        vehicle_type = parser.get(section, "type", fallback="ship").strip()
        color = parser.get(section, "color", fallback="white").strip()
        if not TOKEN_RE.fullmatch(vehicle_type):
            raise ValueError(f"[{section}] invalid type {vehicle_type!r}")
        if not TOKEN_RE.fullmatch(color):
            raise ValueError(f"[{section}] invalid color {color!r}")

        speed = finite_number(parser.get(section, "speed", fallback="0.6"), f"[{section}] speed")
        max_speed = finite_number(parser.get(section, "max_speed", fallback="2.0"), f"[{section}] max_speed")
        if speed <= 0 or max_speed <= 0 or speed > max_speed:
            raise ValueError(f"[{section}] require 0 < speed <= max_speed")
        try:
            avoid = parser.getboolean(section, "avoid", fallback=True)
        except ValueError as exc:
            raise ValueError(f"[{section}] avoid must be true or false") from exc

        start_key = "fast_start" if fast and parser.has_option(section, "fast_start") else "start"
        waypoint_key = "fast_waypoints" if fast and parser.has_option(section, "fast_waypoints") else "waypoints"
        if not parser.has_option(section, waypoint_key):
            waypoint_key = "fast_waypoint" if fast and parser.has_option(section, "fast_waypoint") else "waypoint"
        loiter_key = "fast_loiter" if fast and parser.has_option(section, "fast_loiter") else "loiter"
        for required in (start_key, waypoint_key, loiter_key):
            if not parser.has_option(section, required):
                raise ValueError(f"[{section}] missing {required}")

        rows["names"].append(name)
        rows["types"].append(vehicle_type)
        rows["avoids"].append("true" if avoid else "false")
        rows["colors"].append(color)
        rows["speeds"].append(f"{speed:g}")
        rows["max_speeds"].append(f"{max_speed:g}")
        rows["starts"].append(parse_position(parser.get(section, start_key), ("x", "y", "heading"), f"[{section}] {start_key}"))
        raw_waypoints = parser.get(section, waypoint_key)
        if ";" in raw_waypoints:
            route = parse_waypoint_list(raw_waypoints, f"[{section}] {waypoint_key}")
        else:
            parsed = parse_position(raw_waypoints, ("x", "y"), f"[{section}] {waypoint_key}")
            fields = dict(part.split("=", 1) for part in parsed.split(","))
            route = [(fields["x"], fields["y"])]
        rows["waypoints"].append(f"x={route[0][0]},y={route[0][1]}")
        rows["routes"].append(":".join(f"{x},{y}" for x, y in route))
        rows["loiters"].append(parse_position(parser.get(section, loiter_key), ("x", "y"), f"[{section}] {loiter_key}"))

    return rows


def write_outputs(rows: dict[str, list[str]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, filename in OUTPUTS.items():
        fd, temporary = tempfile.mkstemp(prefix=f".{filename}.", dir=output_dir, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("\n".join(rows[key]) + "\n")
            os.replace(temporary, output_dir / filename)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def main() -> int:
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--config", type=Path, required=True)
    arg_parser.add_argument("--output-dir", type=Path, required=True)
    arg_parser.add_argument("--amt", type=int)
    arg_parser.add_argument("--fast", action="store_true")
    args = arg_parser.parse_args()

    try:
        rows = load_fleet(args.config, args.amt, args.fast)
        write_outputs(rows, args.output_dir)
    except ValueError as exc:
        arg_parser.error(str(exc))
    print(f"Rendered {len(rows['names'])} vehicle(s) from {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
