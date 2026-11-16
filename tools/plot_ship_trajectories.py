#!/usr/bin/env python3
"""Export thesis figures of observed Imazu tracks from audit/telemetry JSONL logs."""

from __future__ import annotations

import argparse
import configparser
import csv
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re


DEFAULT_RUNS = [
    ("MOOS-IvP", Path("logs/evaluations/imazu/moos_ivp_colregs_baseline")),
    ("Qwen3-8B + COLREG RAG", Path("logs/evaluations/imazu/qwen3_8b_colregs_rag")),
    ("Qwen3-8B fine-tuned", Path("logs/evaluations/imazu/20261005T103227Z_finetuned")),
]
COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#332288"]


@dataclass
class Track:
    label: str
    path: Path
    own: list = field(default_factory=list)
    contacts: dict = field(default_factory=dict)
    destination: tuple | None = None
    mission_start: tuple | None = None
    mission_bounds_source: str | None = None
    outcome: str = "Observed track"


def position(nav):
    x, y = nav.get("x"), nav.get("y")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
        return (float(x), float(y))
    return None


def load_track(path: Path, label: str) -> Track:
    track = Track(label, path)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from error
        if row.get("event") == "mission_complete":
            track.outcome = "Mission complete"
        elif row.get("event") == "mission_failed":
            track.outcome = "Mission failed"
        elif row.get("event") == "bridge_snapshot":
            state = row.get("state", {})
            if state.get("ready") is False:
                continue
            own = position(state.get("nav", {}))
            if own is None:
                continue
            timestamp = state.get("moos_time", row.get("timestamp"))
            track.own.append((timestamp, *own))
            # Missing contact samples create gaps rather than invented tracks.
            contacts = {str(c["name"]): position(c.get("nav", {}))
                        for c in state.get("contacts", []) if c.get("name") and c.get("ready") is not False}
            for name in contacts.keys() | track.contacts.keys():
                track.contacts.setdefault(name, [(p[0], math.nan, math.nan) for p in track.own[:-1]])
                xy = contacts.get(name) or (math.nan, math.nan)
                track.contacts[name].append((timestamp, *xy))
            destination = position(state.get("mission_objective") or {})
            if destination:
                track.destination = destination
            if state.get("mission_failed"):
                track.outcome = "Mission failed"
            elif state.get("mission_complete"):
                track.outcome = "Mission complete"
    if len(track.own) < 2:
        raise ValueError(f"{path}: fewer than two valid own-ship observations")
    scenario = path.parent / "scenario.ini"
    if scenario.is_file():
        config = configparser.ConfigParser()
        config.read(scenario)
        section = "vehicle:LLM_SHIP"
        if config.has_section(section):
            def scenario_point(key):
                fields = dict(part.strip().split("=", 1)
                              for part in config.get(section, key).split(","))
                return position({key: float(fields[key]) for key in ("x", "y")})
            track.mission_start = scenario_point("start")
            if track.destination is None:
                track.destination = scenario_point("waypoint")
            track.mission_bounds_source = str(scenario.resolve())
    return track


def find_log(run: Path, case: str):
    if run.is_file():
        return run
    for name in ("audit.jsonl", "telemetry.jsonl"):
        path = run / case / name
        if path.is_file():
            return path
    return None


def terminal_segment(values, scale, bounds):
    """Last visible nonzero segment, without bridging missing reports."""
    xmin, xmax, ymin, ymax = bounds
    for start, end in reversed(list(zip(values, values[1:]))):
        if not all(math.isfinite(v) for v in (*start[1:], *end[1:])):
            continue
        x, y = (v / scale for v in start[1:])
        ex, ey = (v / scale for v in end[1:])
        dx, dy = ex - x, ey - y
        if math.hypot(dx, dy) < 1e-9:
            continue
        lo, hi = 0.0, 1.0
        for direction, distance in ((-dx, x - xmin), (dx, xmax - x),
                                    (-dy, y - ymin), (dy, ymax - y)):
            if direction == 0:
                if distance < 0:
                    break
                continue
            ratio = distance / direction
            if direction < 0:
                lo = max(lo, ratio)
            else:
                hi = min(hi, ratio)
            if lo > hi:
                break
        else:
            if hi > lo:
                return ((x + lo * dx, y + lo * dy), (x + hi * dx, y + hi * dy))
    return None


def draw_terminal_arrow(ax, values, scale, color, linewidth=1, size=10):
    segment = terminal_segment(values, scale, (*ax.get_xlim(), *ax.get_ylim()))
    if segment is None:
        return
    start, end = segment
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    annotation = ax.annotate("", xy=end, xytext=(-size * dx / length, -size * dy / length),
                            textcoords="offset points", annotation_clip=False,
                            arrowprops={"arrowstyle": "-|>", "color": color,
                                        "lw": linewidth, "shrinkA": 0, "shrinkB": 0}, zorder=8)
    annotation.arrow_patch.set_clip_path(ax.patch)


def mission_bounds(tracks, scale):
    """Mission square plus 1 NM on each side; detours never change the extent."""
    track = next((t for t in tracks if t.mission_start and t.destination), None)
    if track is None:
        track = next((t for t in tracks if t.own and t.destination), None)
    if track is None:
        raise ValueError("mission square requires a start position and destination")
    start = track.mission_start or track.own[0][1:]
    end = track.destination
    cx, cy = ((start[i] + end[i]) / (2 * scale) for i in (0, 1))
    side = max(abs(end[0] - start[0]), abs(end[1] - start[1])) / scale
    if side <= 0:
        raise ValueError("mission start and destination must differ")
    half = side / 2 + 1852.0 / scale  # One nautical mile beyond each mission edge.
    return (cx - half, cx + half, cy - half, cy + half)


def draw_atlas_track(ax, track, scale, color, contacts):
    """Draw observed tracks with one direction arrow at each visible end."""
    if contacts:
        for name, values in sorted(track.contacts.items()):
            ax.plot([p[1] / scale for p in values], [p[2] / scale for p in values],
                    color="#657B89", linestyle="--", linewidth=.6, alpha=.65)
            draw_terminal_arrow(ax, values, scale, "#657B89", linewidth=.8)
            first = next((p for p in values if math.isfinite(p[1]) and math.isfinite(p[2])), None)
            if first:
                ax.annotate(name.replace("IMAZU_", ""), (first[1] / scale, first[2] / scale),
                            xytext=(3, 3), textcoords="offset points", fontsize=5.5, color="#516674")
    x, y = [p[1] / scale for p in track.own], [p[2] / scale for p in track.own]
    ax.plot(x, y, color=color, linewidth=1.2, zorder=3)
    ax.scatter(x[0], y[0], marker="o", s=13, facecolor="white", edgecolor=color, zorder=5)
    draw_terminal_arrow(ax, track.own, scale, color)
    if track.destination:
        ax.scatter(*(v / scale for v in track.destination), marker="*", s=42,
                   color="#263F50", edgecolor="white", linewidth=.4, zorder=6)


def architecture_cases(run, label, fallbacks=()):
    """Retain all 22 slots and record the actual source of every usable track."""
    cases = []
    for number in range(1, 23):
        case = f"case_{number:02d}"
        track, error = None, "No recorded track"
        for source in (run, *fallbacks):
            path = find_log(source, case)
            if path is None:
                continue
            try:
                track = load_track(path, label)
            except ValueError as exc:
                error = str(exc)
                continue
            break
        cases.append({"case": case, "track": track, "missing_reason": None if track else error})
    return cases


def render_architectures(runs, fallback_runs, output, unit, contacts, dpi, per_page, columns, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator

    if any(run.is_file() for _, run in runs):
        raise ValueError("architecture layout requires run directories, not individual logs")
    scale = 1852.0 if unit == "nm" else 1000.0
    unit_label = "NM" if unit == "nm" else "km"
    collections = [(label, architecture_cases(run, label, fallback_runs.get(label, ())))
                   for label, run in runs]
    # Identical bounds for the same case across architectures.
    bounds = []
    for i in range(22):
        available = [cases[i]["track"] for _, cases in collections if cases[i]["track"]]
        bounds.append(mission_bounds(available, scale) if available else None)
    plt.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42, "ps.fonttype": 42})
    output.mkdir(parents=True, exist_ok=True)
    handles = [
        Line2D([], [], color="#0072B2", lw=1.2, label="Own ship"),
        Line2D([], [], marker="o", markerfacecolor="white", color="#0072B2", ls="", label="First observation"),
        Line2D([], [], marker=">", color="#0072B2", ls="", label="Terminal direction"),
        Line2D([], [], marker="*", color="#263F50", ls="", label="Destination"),
    ]
    if contacts:
        handles.extend([
            Line2D([], [], color="#657B89", ls="--", label="Observed traffic"),
        ])
    stems = set()
    for label, cases in collections:
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        if not slug or slug in stems:
            raise ValueError("architecture labels must have unique, nonempty filename forms")
        stems.add(slug)
        stem = output / f"{slug}_all_22"
        pages = math.ceil(22 / per_page)
        records = []
        with PdfPages(stem.with_suffix(".pdf")) as pdf:
            for page in range(pages):
                selected = cases[page * per_page:(page + 1) * per_page]
                rows = math.ceil(min(per_page, 22) / columns)
                # A4 portrait pages for paginated panels; larger A3 overview.
                size = (11.69, 16.54) if per_page > 12 else (8.27, 11.69)
                fig, axes = plt.subplots(rows, columns, figsize=size, squeeze=False)
                for index, ax in enumerate(axes.flat):
                    if index >= len(selected):
                        ax.set_visible(False)
                        continue
                    item = selected[index]
                    case = item["case"]
                    track = item["track"]
                    case_number = int(case.split("_")[-1])
                    ax.set_facecolor("#E6F3FA")
                    if track is None:
                        ax.set_xticks([])
                        ax.set_yticks([])
                        ax.text(.5, .5, "No recorded track", transform=ax.transAxes,
                                ha="center", va="center", color="#516674", fontsize=8)
                        outcome = "Unavailable"
                    else:
                        ax.set_aspect("equal", adjustable="box")
                        box = bounds[case_number - 1]
                        ax.set_xlim(box[:2])
                        ax.set_ylim(box[2:])
                        ax.grid(color="#BDD7E5", linewidth=.4, alpha=.8)
                        ax.set_axisbelow(True)
                        ax.tick_params(labelsize=6, length=2, pad=2)
                        ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
                        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
                        draw_atlas_track(ax, track, scale, "#0072B2", contacts)
                        outcome = track.outcome
                    for spine in ax.spines.values():
                        spine.set_color("#90ADBE")
                    ax.set_title(f"Case {case_number:02d} · {outcome}", fontsize=8, pad=6)
                    records.append({
                        "case": case, "page": page + 1,
                        "log": str(track.path.resolve()) if track else None,
                        "outcome": outcome, "missing_reason": item["missing_reason"],
                        "own_ship_samples": len(track.own) if track else 0,
                        "bounds": bounds[case_number - 1],
                        "mission_start_m": (track.mission_start or list(track.own[0][1:])) if track else None,
                        "mission_destination_m": track.destination if track else None,
                        "mission_bounds_source": (track.mission_bounds_source or "First recorded own-ship position") if track else None,
                    })
                fig.suptitle(title or f"{label} · Imazu observed trajectories", fontsize=14, y=.975)
                fig.text(.5, .949, f"Cases {int(selected[0]['case'][-2:]):02d}–{int(selected[-1]['case'][-2:]):02d}"
                         f"  ·  Page {page + 1}/{pages}  ·  Local simulation coordinates", ha="center",
                         fontsize=8, color="#516674")
                fig.supxlabel(f"East ({unit_label})", fontsize=9, y=.087)
                fig.supylabel(f"North ({unit_label})", fontsize=9, x=.018)
                fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False,
                           fontsize=7, bbox_to_anchor=(.5, .032))
                fig.text(.5, .013, "Mission bounds + 1 NM per side. Arrows show terminal track direction; off-map tracks are clipped.",
                         ha="center", fontsize=6, color="#516674")
                fig.subplots_adjust(left=.09, right=.97, top=.91, bottom=.12, hspace=.35, wspace=.32)
                pdf.savefig(fig, facecolor="white")
                page_stem = stem.parent / f"{stem.name}_page_{page + 1:02d}"
                for extension in ("png", "svg"):
                    fig.savefig(page_stem.with_suffix(f".{extension}"), dpi=dpi, facecolor="white")
                plt.close(fig)
        with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["case", "architecture", "vessel", "observed_at", "x_m", "y_m"])
            for item in cases:
                track = item["track"]
                if track:
                    for vessel, values in [("own_ship", track.own), *sorted(track.contacts.items())]:
                        writer.writerows((item["case"], label, vessel, time, x, y)
                                         for time, x, y in values if math.isfinite(x) and math.isfinite(y))
        stem.with_suffix(".json").write_text(json.dumps({
            "architecture": label, "unit": unit, "pages": pages, "cases_per_page": per_page,
            "columns": columns, "cases": records,
            "note": "Observed tracks only; no smoothing or unobserved route extension. Missing cases retain their slots.",
            "bounds_note": "Square bounds from mission start and destination plus 1 NM on each side, identical across architectures. Tracks outside are clipped in the figure; CSV retains all coordinates.",
            "arrow_note": "One arrow per vessel at its last visible nonzero segment; tracks ending outside the bounds get an arrow at their last visible exit. No snapshot markers.",
        }, indent=2) + "\n")
        print(f"{stem.with_suffix('.pdf')} ({sum(item['track'] is not None for item in cases)}/22 recorded cases)")


def render(tracks, case, output, layout, unit, contacts, dpi, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    scale = 1852.0 if unit == "nm" else 1000.0
    unit_label = "NM" if unit == "nm" else "km"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.titlesize": 11, "pdf.fonttype": 42, "ps.fonttype": 42})
    panels = len(tracks) if layout == "panels" else 1
    fig, axes = plt.subplots(1, panels, figsize=(4.2 * panels, 6.3), squeeze=False)
    bounds = mission_bounds(tracks, scale)
    for ax in axes.flat:
        ax.set_facecolor("#E6F3FA")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(bounds[:2])
        ax.set_ylim(bounds[2:])
        ax.grid(color="#BDD7E5", linewidth=.5, alpha=.8)
        ax.set_axisbelow(True)
        ax.set_xlabel(f"East ({unit_label})")
        for spine in ax.spines.values():
            spine.set_color("#90ADBE")
        ax.annotate("N", xy=(.93, .94), xytext=(.93, .84), xycoords="axes fraction",
                    ha="center", va="center", color="#385466",
                    arrowprops={"arrowstyle": "-|>", "color": "#385466"})
    axes[0, 0].set_ylabel(f"North ({unit_label})")
    for index, track in enumerate(tracks):
        ax = axes[0, index if layout == "panels" else 0]
        color = COLORS[index % len(COLORS)]
        if contacts:
            for name, values in sorted(track.contacts.items()):
                ax.plot([p[1] / scale for p in values], [p[2] / scale for p in values],
                        color="#657B89", linestyle="--", linewidth=1, alpha=.6)
                draw_terminal_arrow(ax, values, scale, "#657B89", linewidth=1.2, size=14)
                valid = [p for p in values if math.isfinite(p[1]) and math.isfinite(p[2])]
                if valid and (layout == "panels" or index == 0):
                    ax.annotate(name.replace("IMAZU_", ""),
                                (valid[0][1] / scale, valid[0][2] / scale),
                                xytext=(5, 5), textcoords="offset points", fontsize=8, color="#516674")
        x = [p[1] / scale for p in track.own]
        y = [p[2] / scale for p in track.own]
        ax.plot(x, y, color=color, linewidth=2, label=track.label, zorder=3)
        ax.scatter(x[0], y[0], marker="o", s=28, facecolor="white", edgecolor=color, zorder=5)
        draw_terminal_arrow(ax, track.own, scale, color, linewidth=1.8, size=14)
        if track.destination:
            ax.scatter(*(v / scale for v in track.destination), marker="*", s=100,
                       color="#263F50", edgecolor="white", linewidth=.6, zorder=6)
        if layout == "panels":
            ax.set_title(track.label, pad=12)
            ax.text(.03, .97, track.outcome, transform=ax.transAxes, va="top", fontsize=8, color="#385466")
    handles = [Line2D([], [], color=COLORS[i % len(COLORS)], lw=2, label=t.label)
               for i, t in enumerate(tracks)] if layout == "overlay" else []
    handles += [Line2D([], [], marker="o", color="#385466", markerfacecolor="white", ls="", label="First observation"),
                Line2D([], [], marker=">", color="#385466", ls="", label="Terminal direction"),
                Line2D([], [], marker="*", color="#263F50", ls="", label="Mission destination")]
    if contacts:
        handles.append(Line2D([], [], color="#657B89", ls="--", label="Observed traffic"))
    fig.suptitle(title or f"Imazu case {int(case.split('_')[-1]):02d} · observed trajectories", y=.98, fontsize=14)
    fig.legend(handles=handles, loc="lower center", ncol=min(4, len(handles)), frameon=False, fontsize=8)
    fig.subplots_adjust(left=.08, right=.97, top=.9, bottom=.16, wspace=.25)
    output.mkdir(parents=True, exist_ok=True)
    stem = output / f"{case}_{layout}"
    for extension in ("pdf", "svg", "png"):
        fig.savefig(stem.with_suffix(f".{extension}"), dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["model", "vessel", "observed_at", "x_m", "y_m"])
        for track in tracks:
            for vessel, values in [("own_ship", track.own), *sorted(track.contacts.items())]:
                writer.writerows((track.label, vessel, time, x, y) for time, x, y in values if math.isfinite(x) and math.isfinite(y))
    stem.with_suffix(".json").write_text(json.dumps({"case": case, "unit": unit, "layout": layout,
        "note": "Local simulation coordinates; straight segments connect logged observations. Square mission bounds plus 1 NM per side clip tracks outside the viewing area; CSV retains all coordinates. No smoothing, inferred coastlines, or unobserved route extension.",
        "bounds": bounds,
        "arrow_note": "One direction arrow per vessel at its last visible nonzero segment. No snapshot markers.",
        "runs": [{"label": t.label, "log": str(t.path.resolve()), "own_ship_samples": len(t.own),
                  "outcome": t.outcome} for t in tracks]}, indent=2) + "\n")
    print(stem.with_suffix(".pdf"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", metavar="LABEL=PATH", help="Repeat for model directories or individual JSONL logs")
    parser.add_argument("--case", default="15", help="Case number or 'all'")
    parser.add_argument("--layout", choices=("panels", "overlay", "architecture"), default="panels")
    parser.add_argument("--fallback-run", action="append", metavar="LABEL=PATH",
                        help="Explicit fallback source for missing cases in an architecture")
    parser.add_argument("--cases-per-page", type=int, default=12, help="Architecture layout: panels per page (1–22)")
    parser.add_argument("--columns", type=int, default=3, help="Architecture layout: grid columns")
    parser.add_argument("--output-dir", type=Path, default=Path("figures/trajectories"))
    parser.add_argument("--unit", choices=("nm", "km"), default="nm")
    parser.add_argument("--no-contacts", action="store_true")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--title")
    args = parser.parse_args()
    runs = DEFAULT_RUNS
    if args.run:
        runs = []
        for item in args.run:
            label, separator, path = item.partition("=")
            if not separator or not label or not path:
                parser.error("--run requires LABEL=PATH")
            runs.append((label, Path(path)))
    if args.layout == "architecture":
        if not 1 <= args.cases_per_page <= 22 or not 1 <= args.columns <= args.cases_per_page:
            parser.error("require 1 <= columns <= cases-per-page <= 22")
        fallbacks = {}
        for item in args.fallback_run or []:
            label, separator, path = item.partition("=")
            if not separator or label not in {label for label, _ in runs} or not path:
                parser.error("--fallback-run requires an existing architecture LABEL=PATH")
            fallbacks.setdefault(label, []).append(Path(path))
        render_architectures(runs, fallbacks, args.output_dir, args.unit, not args.no_contacts,
                             args.dpi, args.cases_per_page, args.columns, args.title)
        return
    if args.fallback_run:
        parser.error("--fallback-run is only supported with --layout architecture")
    if args.case == "all":
        cases = sorted({p.name for _, run in runs for p in run.glob("case_*") if p.is_dir()})
    elif re.fullmatch(r"(?:case_)?\d+", args.case):
        cases = [f"case_{int(args.case.replace('case_', '')):02d}"]
    else:
        parser.error("--case requires a number or 'all'")
    if not cases:
        parser.error("no case directories found")
    for case in cases:
        tracks = []
        for label, run in runs:
            path = find_log(run, case)
            if path is None:
                print(f"Skipping {label}: no log for {case}")
                continue
            tracks.append(load_track(path, label))
        if not tracks:
            parser.error(f"no usable logs for {case}")
        render(tracks, case, args.output_dir, args.layout, args.unit,
               not args.no_contacts, args.dpi, args.title)


if __name__ == "__main__":
    main()
