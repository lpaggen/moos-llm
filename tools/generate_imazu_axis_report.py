#!/usr/bin/env python3
"""Generate a scenario-level report from the frozen Imazu v1 score files."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "reports" / "imazu_frozen_v1"
OUT_MD = ROOT / "reports" / "IMAZU_AXIS_SCORES_BY_SCENARIO.md"
OUT_CSV = ROOT / "reports" / "IMAZU_AXIS_SCORES_BY_SCENARIO.csv"

CONFIGS = [
    ("base_narrative_combined", "Base narrative", "Narrative"),
    ("base_raw_state_combined", "Base raw state", "Raw"),
    ("base_eriksen_all_rules_matched_warp5", "Base + Eriksen/all rules", "Eriksen"),
    ("colregs_rag_matched_warp5", "COLREG summary RAG", "COLREG RAG"),
    (
        "maib_lesson_cards_captain_review_prompt_fixed_full_warp5",
        "Narrative OOW + Captain critic",
        "Critic",
    ),
    (
        "maib_rag_independent_model_only_warp5",
        "Independent MAIB-RAG Captain",
        "MAIB ind.",
    ),
    (
        "privileged_luna_narrative_directional_v7_full_warp5",
        "Directional narrative SFT v7",
        "SFT v7",
    ),
    ("moos_ivp_colregs_baseline_12nm", "MOOS-IvP (warp 50)", "MOOS-IvP"),
]

AXES = [
    ("safety", "Safety", 0.35),
    ("compliance", "Compliance", 0.10),
    ("citation", "Citation", 0.10),
    ("temporal", "Temporal", 0.10),
    ("spatial", "Spatial", 0.10),
    ("manoeuvre", "Manoeuvre", 0.10),
    ("smoothness", "Smoothness", 0.15),
]


def load() -> tuple[dict[str, list[dict[str, str]]], dict[str, dict]]:
    rows_by_config: dict[str, list[dict[str, str]]] = {}
    summaries: dict[str, dict] = {}
    for slug, _, _ in CONFIGS:
        csv_path = FROZEN / slug / "scores.csv"
        json_path = FROZEN / slug / "scores.json"
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        cases = [int(row["case"]) for row in rows]
        if cases != list(range(1, 23)):
            raise ValueError(f"{slug}: expected cases 1..22, got {cases}")
        with json_path.open(encoding="utf-8") as handle:
            summary = json.load(handle)["summary"]
        for key, _, _ in AXES:
            calculated = sum(float(row[key]) for row in rows) / len(rows)
            recorded = float(summary["mean_axes"][key])
            if abs(calculated - recorded) > 1e-12:
                raise ValueError(f"{slug}: mean mismatch for {key}")
        rows_by_config[slug] = rows
        summaries[slug] = summary
    return rows_by_config, summaries


def marker(verdict: str) -> str:
    if "collision" in verdict:
        return " C"
    if "did not reach" in verdict:
        return " N"
    return ""


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---:"] * len(headers)) + "|")
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def main() -> None:
    rows_by_config, summaries = load()
    short_names = [short for _, _, short in CONFIGS]

    md = [
        "# Imazu frozen-v1 axis scores by scenario",
        "",
        "These values are copied from the frozen `scores.csv` artefacts; they are not a new rescoring. All axes and the composite are on a 0--1 scale, where higher is better. The frozen composite weights are safety 0.35, compliance 0.10, citation 0.10, temporal 0.10, spatial 0.10, manoeuvre 0.10, and smoothness 0.15. `C` marks a collision (composite forced to 0), and `N` marks a non-arrival (composite capped at 0.20).",
        "",
        "Superseded SFT v0, v1, and v4 runs are excluded; SFT v7 is the retained fine-tuned condition. MOOS-IvP was evaluated at warp 50; the other listed evaluations use their documented warp-5 configurations.",
        "",
        "## Configuration labels",
        "",
        "| Short label | Configuration | Frozen directory |",
        "|---|---|---|",
    ]
    for slug, label, short in CONFIGS:
        md.append(f"| {short} | {label} | `{slug}` |")

    md.extend(["", "## Mean scores across 22 scenarios", ""])
    mean_headers = ["Configuration", "Composite"] + [name for _, name, _ in AXES]
    mean_rows = []
    for slug, _, short in CONFIGS:
        summary = summaries[slug]
        mean_rows.append(
            [short, f'{float(summary["mean_composite_score"]):.3f}']
            + [f'{float(summary["mean_axes"][key]):.3f}' for key, _, _ in AXES]
        )
    md.extend(table(mean_headers, mean_rows))

    sections = [("composite_score", "Composite score", None)] + [
        (key, f"{name} axis", weight) for key, name, weight in AXES
    ]
    for field, title, weight in sections:
        md.extend(["", f"## {title} by scenario", ""])
        if weight is not None:
            md.extend([f"Composite weight: {weight:.2f}.", ""])
        scenario_rows = []
        for case_idx in range(22):
            values = []
            for slug, _, _ in CONFIGS:
                row = rows_by_config[slug][case_idx]
                suffix = marker(row["verdict"]) if field == "composite_score" else ""
                values.append(f'{float(row[field]):.3f}{suffix}')
            scenario_rows.append([str(case_idx + 1)] + values)
        mean_values = []
        for slug, _, _ in CONFIGS:
            if field == "composite_score":
                value = float(summaries[slug]["mean_composite_score"])
            else:
                value = float(summaries[slug]["mean_axes"][field])
            mean_values.append(f"**{value:.3f}**")
        scenario_rows.append(["**Mean**"] + mean_values)
        md.extend(table(["Scenario"] + short_names, scenario_rows))

    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")

    fields = [
        "configuration",
        "short_label",
        "case",
        "verdict",
        "min_cpa_m",
        "composite_score",
    ] + [key for key, _, _ in AXES]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for slug, label, short in CONFIGS:
            for row in rows_by_config[slug]:
                writer.writerow(
                    {
                        "configuration": label,
                        "short_label": short,
                        **{field: row[field] for field in fields[2:]},
                    }
                )

    print(f"Wrote {OUT_MD.relative_to(ROOT)}")
    print(f"Wrote {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
