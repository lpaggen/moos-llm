#!/usr/bin/env python3
"""Plot three-stage evolutions for narrative cases that passed below 500 m."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


CASES = {
    4: {
        "contact": "IMAZU_TS1",
        "cycles": (95, 120, 128),
        "clearance_m": 301.223,
    },
    12: {
        "contact": "IMAZU_TS2",
        "cycles": (21, 40, 46),
        "clearance_m": 459.277,
    },
    13: {
        "contact": "IMAZU_TS3",
        "cycles": (95, 114, 119),
        "clearance_m": 296.141,
    },
}

STAGES = ("Planning window", "Critical approach", "Closest passing")
RUN = Path("logs/evaluations/imazu/base_narrative_matched_warp5_rerun")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number}: invalid JSON") from error
    return rows


def snapshots(rows: list[dict]) -> dict[int, dict]:
    return {
        int(row["cycle_id"]): row["state"]
        for row in rows
        if row.get("event") == "bridge_snapshot" and row.get("state", {}).get("ready") is not False
    }


def decisions(rows: list[dict]) -> dict[int, dict]:
    return {
        int(row["cycle_id"]): row["decision"]
        for row in rows
        if row.get("event") == "captain_validated"
    }


def contact(state: dict, name: str) -> dict | None:
    return next((item for item in state.get("contacts", []) if item.get("name") == name), None)


def point(nav: dict) -> tuple[float, float]:
    return float(nav["x"]), float(nav["y"])


def action_label(decision: dict | None) -> str:
    if not decision:
        return "unavailable"
    action = str(decision.get("action", "unknown")).replace("_", " ")
    degrees = float(decision.get("degrees") or 0.0)
    if action == "alter course":
        return f"alter +{degrees:g}°"
    return action


def metric(item: dict, key: str):
    value = item.get(key)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def heading_arrow(ax, x: float, y: float, heading: float, color: str, length: float = 170.0):
    radians = math.radians(heading)
    dx, dy = length * math.sin(radians), length * math.cos(radians)
    ax.annotate(
        "",
        xy=(x + dx, y + dy),
        xytext=(x, y),
        arrowprops={"arrowstyle": "-|>", "lw": 1.5, "color": color, "mutation_scale": 11},
        zorder=8,
    )


def recent_track(states: dict[int, dict], cycle: int, vessel: str | None, origin: tuple[float, float]):
    result = []
    for current in sorted(key for key in states if cycle - 14 <= key <= cycle):
        state = states[current]
        if vessel is None:
            nav = state.get("nav", {})
        else:
            item = contact(state, vessel)
            if item is None:
                continue
            nav = item.get("nav", {})
        if not all(isinstance(nav.get(key), (int, float)) for key in ("x", "y")):
            continue
        result.append((float(nav["x"]) - origin[0], float(nav["y"]) - origin[1]))
    return result


def draw_panel(ax, states, decision_map, case_number, contact_name, cycle, stage, clearance_m):
    from matplotlib.patches import Circle

    state = states[cycle]
    own = state["nav"]
    target = contact(state, contact_name)
    if target is None:
        raise ValueError(f"case {case_number}, cycle {cycle}: {contact_name} unavailable")
    origin = point(own)
    target_x, target_y = point(target["nav"])
    target_x -= origin[0]
    target_y -= origin[1]

    own_track = recent_track(states, cycle, None, origin)
    target_track = recent_track(states, cycle, contact_name, origin)
    if own_track:
        ax.plot(*zip(*own_track), color="#0072B2", lw=1.8, alpha=.8, zorder=4)
    if target_track:
        ax.plot(*zip(*target_track), color="#D55E00", lw=1.6, ls="--", alpha=.8, zorder=3)

    ax.add_patch(Circle((0, 0), 500, facecolor="#F4A6A6", edgecolor="#B2182B",
                        lw=1.0, ls="--", alpha=.18, zorder=1))
    ax.scatter(0, 0, s=55, color="#0072B2", edgecolor="white", lw=.7, zorder=7)
    ax.scatter(target_x, target_y, s=55, color="#D55E00", edgecolor="white", lw=.7, zorder=7)
    heading_arrow(ax, 0, 0, float(own.get("heading", 0.0)), "#0072B2")
    heading_arrow(ax, target_x, target_y, float(target.get("nav", {}).get("heading", 0.0)), "#D55E00")
    ax.annotate("Own ship", (0, 0), xytext=(7, -15), textcoords="offset points",
                fontsize=7.5, color="#005A8D")
    ax.annotate(contact_name.replace("IMAZU_", ""), (target_x, target_y),
                xytext=(7, 6), textcoords="offset points", fontsize=7.5, color="#A44300")

    for item in state.get("contacts", []):
        if item.get("name") == contact_name or not item.get("nav"):
            continue
        x, y = point(item["nav"])
        x, y = x - origin[0], y - origin[1]
        if abs(x) <= 2300 and abs(y) <= 2300:
            ax.scatter(x, y, s=23, color="#7A8B94", alpha=.65, zorder=5)
            ax.annotate(item["name"].replace("IMAZU_", ""), (x, y), xytext=(4, 4),
                        textcoords="offset points", fontsize=6.5, color="#637681")

    range_m = metric(target, "range")
    cpa_m = metric(target, "cpa_distance")
    tcpa_s = metric(target, "tcpa_s")
    metrics = [f"range {range_m:.0f} m" if range_m is not None else "range unavailable"]
    metrics.append(f"CPA {cpa_m:.0f} m" if cpa_m is not None else "CPA passed")
    metrics.append(f"TCPA {tcpa_s:.0f} s" if tcpa_s is not None else "TCPA passed")
    previous = action_label(decision_map.get(cycle - 1))
    current = action_label(decision_map.get(cycle))

    ax.set_title(f"{stage}\ncycle {cycle} · " + " · ".join(metrics), fontsize=9, pad=7)
    ax.text(.03, .03, f"OOW: {previous} → {current}", transform=ax.transAxes,
            fontsize=7.2, color="#263F50", va="bottom",
            bbox={"boxstyle": "round,pad=.25", "facecolor": "white", "edgecolor": "#B5C7D1", "alpha": .9})
    ax.text(.97, .97, f"realized minimum\n{clearance_m:.1f} m", transform=ax.transAxes,
            ha="right", va="top", fontsize=7.2, color="#8B1A1A")
    ax.set_xlim(-2300, 2300)
    ax.set_ylim(-2300, 2300)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(color="#C9DCE6", lw=.45, alpha=.85)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=7)
    ax.set_facecolor("#F6FBFD")
    for spine in ax.spines.values():
        spine.set_color("#9BB6C5")


def render(output: Path, dpi: int):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, axes = plt.subplots(3, 3, figsize=(15.2, 14.2), sharex=True, sharey=True)
    sources = []
    for row_index, (case_number, spec) in enumerate(CASES.items()):
        path = RUN / f"case_{case_number:02d}" / "audit.jsonl"
        rows = load_jsonl(path)
        states = snapshots(rows)
        decision_map = decisions(rows)
        sources.append(str(path.resolve()))
        for column_index, (cycle, stage) in enumerate(zip(spec["cycles"], STAGES)):
            draw_panel(axes[row_index, column_index], states, decision_map, case_number,
                       spec["contact"], cycle, stage, spec["clearance_m"])
        axes[row_index, 0].set_ylabel(f"Case {case_number:02d}\nNorth offset (m)", fontsize=9)

    for ax in axes[-1, :]:
        ax.set_xlabel("East offset from own ship (m)", fontsize=9)
    handles = [
        Line2D([], [], color="#0072B2", marker="o", label="Own ship and recent track"),
        Line2D([], [], color="#D55E00", marker="o", ls="--", label="Controlling contact and recent track"),
        Patch(facecolor="#F4A6A6", edgecolor="#B2182B", alpha=.35, label="500 m safety threshold"),
    ]
    fig.suptitle("Base-narrative OOW: evolution of sub-500 m encounters", fontsize=15, y=.982)
    fig.text(.5, .958, "Recorded positions in a north-up frame centred on own ship at each snapshot",
             ha="center", fontsize=9, color="#516674")
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=9,
               bbox_to_anchor=(.5, .018))
    fig.subplots_adjust(left=.075, right=.985, top=.925, bottom=.075, wspace=.12, hspace=.2)

    output.parent.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf", "svg"):
        fig.savefig(output.with_suffix(f".{extension}"), dpi=dpi, facecolor="white")
    plt.close(fig)
    output.with_suffix(".json").write_text(json.dumps({
        "figure": "Narrative sub-500 m encounter evolution",
        "frame": "North-up, translated to own-ship position at each snapshot; no rotation",
        "trail": "Last 14 recorded bridge snapshots through the displayed cycle",
        "safe_distance_m": 500,
        "cases": CASES,
        "sources": sources,
        "note": "All positions, headings, CPA/TCPA values, and actions come from audit logs. No trajectory smoothing or unobserved-state interpolation.",
    }, indent=2) + "\n", encoding="utf-8")
    print(output.with_suffix(".png"))

    for case_number, spec in CASES.items():
        path = RUN / f"case_{case_number:02d}" / "audit.jsonl"
        rows = load_jsonl(path)
        states = snapshots(rows)
        decision_map = decisions(rows)
        case_fig, case_axes = plt.subplots(1, 3, figsize=(15.2, 5.25), sharex=True, sharey=True)
        for axis, cycle, stage in zip(case_axes, spec["cycles"], STAGES):
            draw_panel(axis, states, decision_map, case_number, spec["contact"],
                       cycle, stage, spec["clearance_m"])
            axis.set_xlabel("East offset from own ship (m)", fontsize=9)
        case_axes[0].set_ylabel("North offset from own ship (m)", fontsize=9)
        case_fig.suptitle(
            f"Base-narrative OOW · Imazu case {case_number:02d} · sub-500 m encounter evolution",
            fontsize=14, y=.985,
        )
        case_fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False,
                        fontsize=9, bbox_to_anchor=(.5, .018))
        case_fig.subplots_adjust(left=.06, right=.99, top=.86, bottom=.17, wspace=.12)
        case_output = output.parent / f"{output.name}_case_{case_number:02d}"
        for extension in ("png", "pdf", "svg"):
            case_fig.savefig(case_output.with_suffix(f".{extension}"), dpi=dpi, facecolor="white")
        plt.close(case_fig)
        print(case_output.with_suffix(".png"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("reports/figures/imazu_narrative_threshold_evolution"))
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()
    render(args.output, args.dpi)


if __name__ == "__main__":
    main()
