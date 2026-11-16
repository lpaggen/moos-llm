#!/usr/bin/env python3
"""Audit scenario, encounter, action, and balancing distributions in an SFT set."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from extract_oow_pairs import extract_pairs
from build_teacher_dataset import audit_quality


FILES = {
    "train_natural": "train_natural.jsonl",
    "train_balanced": "train_balanced.jsonl",
    "validation": "validation_natural.jsonl",
    "test": "test_natural.jsonl",
    "all_natural": "paired_all.jsonl",
}
STATE_ORDER = ["SF", "OT", "HO", "GW", "SO", "EM", "none"]
STATE_NAMES = {
    "SF": "safe",
    "OT": "overtaking",
    "HO": "head-on",
    "GW": "give-way",
    "SO": "stand-on",
    "EM": "emergency",
    "none": "no classified contact",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def parse_completion(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["completion"][0]["content"])


def parse_user_prompt(event: dict[str, Any]) -> dict[str, Any] | None:
    value = event.get("user_prompt")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def load_audit(path: Path) -> dict[str, Any]:
    prompts: dict[int, dict[str, Any]] = {}
    max_contacts = 0
    # prepare_teacher_campaigns.py defines cycle_id as the 1-based ordinal of
    # validated prompt/decision pairs, not the runtime cycle number. Reuse the
    # same extractor so this audit cannot silently join a row to the wrong event.
    # Use its raw form because chat_format intentionally removes the privileged
    # oracle before constructing student-oriented records.
    for index, pair in enumerate(
        extract_pairs(path, validated_only=True, chat_format=False), start=1
    ):
        content = (pair.get("input") or {}).get("user_prompt")
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        prompts[index] = payload
        state = payload.get("state")
        if isinstance(state, dict):
            max_contacts = max(max_contacts, len(state.get("contacts") or []))
    scenario_path = path.with_suffix(".scenario.json")
    scenario = (
        json.loads(scenario_path.read_text(encoding="utf-8"))
        if scenario_path.is_file()
        else {}
    )
    configured_contacts = scenario.get("contacts") or []
    configured_count = scenario.get("contact_count")
    if not isinstance(configured_count, int):
        configured_count = len(configured_contacts) if configured_contacts else max_contacts
    return {
        "prompts": prompts,
        "max_contacts": max_contacts,
        "scenario": scenario,
        "configured_contacts": configured_contacts,
        "configured_count": configured_count,
    }


def primary_oracle_contact(oracle: dict[str, Any]) -> dict[str, Any] | None:
    contacts = oracle.get("contacts") or []
    if not isinstance(contacts, list) or not contacts:
        return None
    name = oracle.get("primary_contact")
    return next((item for item in contacts if item.get("contact") == name), contacts[0])


def campaign_name(path: Path) -> str:
    return path.parent.name


def enrich(
    rows: Iterable[dict[str, Any]], audits: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        source = str(row["source_file"])
        cycle = int(row["cycle_id"])
        payload = audits[source]["prompts"].get(cycle)
        if payload is None:
            raise ValueError(f"no oow_prompt_input for {source} cycle {cycle}")
        state = payload.get("state") or {}
        contacts = state.get("contacts") or []
        oracle = payload.get("deterministic_colregs_oracle") or {}
        oracle_contacts = oracle.get("contacts") or []
        primary = primary_oracle_contact(oracle)
        decision = parse_completion(row)
        scenario = audits[source]["scenario"]
        configured_contacts = audits[source]["configured_contacts"]
        enriched.append(
            {
                "source": source,
                "campaign": campaign_name(Path(source)),
                "cycle": cycle,
                "contacts": len(contacts),
                "mission_max_contacts": audits[source]["max_contacts"],
                "configured_contacts": audits[source]["configured_count"],
                "scenario_kinds": [str(item.get("kind") or "unknown") for item in configured_contacts],
                "scenario_bands": [str(item.get("designed_cpa_band") or "unknown") for item in configured_contacts],
                "scenario_profile": str(scenario.get("profile") or "unknown"),
                "scenario_variant": str(scenario.get("curriculum_variant") or "unknown"),
                "primary_state": str(primary.get("state")) if primary else "none",
                "primary_role": str(primary.get("own_role")) if primary else "none",
                "geometry": str(primary.get("geometrical_state")) if primary else "none",
                "all_states": [str(item.get("state") or "none") for item in oracle_contacts],
                "action": str(decision.get("action")),
                "direction": str(decision.get("turn_direction") or "none"),
                "lookahead": isinstance(payload.get("teacher_candidate_lookahead"), dict),
                "active_review": isinstance(payload.get("active_action_review"), dict),
                "token_count": row.get("token_count"),
            }
        )
    return enriched


def pct(count: int, total: int) -> str:
    return f"{count} ({100.0 * count / total:.1f}%)" if total else "0 (0.0%)"


def counter_table(
    title: str,
    categories: list[str],
    datasets: list[tuple[str, list[dict[str, Any]]]],
    field: str,
    labels: dict[str, str] | None = None,
) -> list[str]:
    counters = [(name, Counter(str(row[field]) for row in rows), len(rows)) for name, rows in datasets]
    lines = [f"## {title}", "", "| Category | " + " | ".join(name for name, _, _ in counters) + " |", "|---|" + "|".join("---:" for _ in counters) + "|"]
    for category in categories:
        label = labels.get(category, category) if labels else category
        lines.append("| " + label + " | " + " | ".join(pct(counts[category], total) for _, counts, total in counters) + " |")
    lines.append("")
    return lines


def summary_values(values: list[int]) -> str:
    if not values:
        return "n/a"
    return f"{min(values)} / {statistics.median(values):.1f} / {statistics.mean(values):.1f} / {max(values)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("sft/output/privileged_teacher_narrative_directional_v7"))
    parser.add_argument("--output", type=Path, default=Path("reports/SFT_DATA_DISTRIBUTION.md"))
    parser.add_argument("--run-config", type=Path, default=Path("models/qwen3_8b_oow_privileged_luna_narrative_directional_v7/run_config.json"))
    parser.add_argument("--campaign-manifest", type=Path, default=Path("sft/output/teacher_campaigns_luna_expanded_v5/manifest.json"))
    parser.add_argument("--prompt-match-manifest", type=Path, default=Path("sft/output/privileged_teacher_base_narrative_v6_clean/manifest.json"))
    args = parser.parse_args()

    manifest = json.loads((args.dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = {name: read_jsonl(args.dataset_dir / filename) for name, filename in FILES.items()}
    sources = sorted({str(row["source_file"]) for row in rows["all_natural"]})
    audits = {source: load_audit(Path(source)) for source in sources}
    data = {name: enrich(split_rows, audits) for name, split_rows in rows.items()}

    # Reconstruct the one actual v7 epoch from the persisted trainer configuration
    # and the deterministic retain-all sampler in sft/train_qwen3_oow.py.
    run_config = json.loads(args.run_config.read_text(encoding="utf-8"))
    campaign_manifest = json.loads(args.campaign_manifest.read_text(encoding="utf-8"))
    prompt_match_manifest = json.loads(args.prompt_match_manifest.read_text(encoding="utf-8"))
    sampling = run_config.get("sampling") or {}
    unique_train = data["train_natural"]
    maintain_indices = [
        index for index, row in enumerate(unique_train)
        if row["action"] == "maintain_course"
    ]
    maneuver_indices = [
        index for index, row in enumerate(unique_train)
        if row["action"] != "maintain_course"
    ]
    target_maintain = int(sampling["epoch_maintain_samples"])
    target_maneuver = int(sampling["epoch_nonmaintain_samples"])
    rng = random.Random(int(run_config["seed"]))
    epoch_indices = list(range(len(unique_train)))
    epoch_indices.extend(
        rng.choices(maintain_indices, k=target_maintain - len(maintain_indices))
    )
    epoch_indices.extend(
        rng.choices(maneuver_indices, k=target_maneuver - len(maneuver_indices))
    )
    rng.shuffle(epoch_indices)
    data["actual_train_epoch"] = [unique_train[index] for index in epoch_indices]
    if len(data["actual_train_epoch"]) != int(sampling["epoch_samples"]):
        raise ValueError("reconstructed epoch length differs from run_config")

    campaign_audits = sorted(
        path
        for directory in campaign_manifest["inputs"]
        for path in Path(directory).glob("seed_*.jsonl")
    )
    mission_quality = [audit_quality(path, 500.0) for path in campaign_audits]
    quality_reasons = Counter(reason for _, reason, _ in mission_quality)
    geometric_pass = sum(
        minimum is not None and minimum >= 500.0
        for _, _, minimum in mission_quality
    )
    completed = quality_reasons["accepted"] + quality_reasons["minimum_range"]
    prompt_matched_rows = sum(
        int(split["rows"])
        for name, split in prompt_match_manifest["splits"].items()
        if name != "train_balanced"
    )

    missing = [row for row in data["all_natural"] if row["primary_state"] not in STATE_ORDER]
    if missing:
        raise ValueError(f"unexpected oracle states: {sorted({row['primary_state'] for row in missing})}")

    lines = [
        "# SFT data distribution and rebalancing audit",
        "",
        f"Dataset: `{args.dataset_dir}`.",
        "",
        "The tables below are generated by joining every SFT example to the exact validated prompt/decision pair in its source teacher audit using `(source_file, decision ordinal)`, matching the dataset builder's definition of `cycle_id`. Encounter labels are deterministic Eriksen/COLREG oracle labels visible to the privileged teacher; they are not present in the short narrative student input.",
        "",
        "## Dataset overview",
        "",
        "| Split | Rows | Missions | Rows per mission (min / median / mean / max) | Token count (min / median / mean / max) |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("train_natural", "actual_train_epoch", "train_balanced", "validation", "test", "all_natural"):
        split = data[name]
        per_mission = Counter(row["source"] for row in split)
        tokens = [int(row["token_count"]) for row in split if isinstance(row["token_count"], int)]
        lines.append(f"| {name.replace('_', ' ')} | {len(split):,} | {len(per_mission):,} | {summary_values(list(per_mission.values()))} | {summary_values(tokens)} |")
    lines += [
        "",
        "The natural corpus contains 7,343 accepted decisions from 330 missions. **The completed v7 run trained on all 5,865 unique natural-training rows.** Its retain-all sampler added 3,797 replacement draws from the 1,034 non-maintain rows, producing 9,662 one-epoch exposures: 4,831 maintain and 4,831 non-maintain. The 2,068-row downsampled file exists but was not used by this run. Validation and test remained natural.",
        "",
        "## Mission acceptance and row-filtering funnel",
        "",
        "The pure 500 m statistic and the final acceptance statistic differ because a mission can preserve 500 m but fail to complete.",
        "",
        "| Mission outcome | Missions | Fraction of all examined |",
        "|---|---:|---:|",
        f"| Examined | {len(mission_quality):,} | 100.0% |",
        f"| Minimum observed separation at least 500 m, irrespective of completion | {geometric_pass:,} | {100.0 * geometric_pass / len(mission_quality):.1f}% |",
        f"| Completed | {completed:,} | {100.0 * completed / len(mission_quality):.1f}% |",
        f"| **Accepted: completed and at least 500 m** | **{quality_reasons['accepted']:,}** | **{100.0 * quality_reasons['accepted'] / len(mission_quality):.1f}%** |",
        f"| Rejected for minimum separation below 500 m | {quality_reasons['minimum_range']:,} | {100.0 * quality_reasons['minimum_range'] / len(mission_quality):.1f}% |",
        f"| Mission failed | {quality_reasons['mission_failed']:,} | {100.0 * quality_reasons['mission_failed'] / len(mission_quality):.1f}% |",
        f"| Unfinished | {quality_reasons['unfinished']:,} | {100.0 * quality_reasons['unfinished'] / len(mission_quality):.1f}% |",
        "",
        f"Among the {completed:,} completed missions, {quality_reasons['accepted']:,} ({100.0 * quality_reasons['accepted'] / completed:.1f}%) met the 500 m requirement. Eleven failed or unfinished missions also remained above 500 m, which is why the geometry-only count is 341 rather than 330.",
        "",
        "| Decision-row stage | Rows | Retained from previous stage | Retained from initial accepted-mission rows |",
        "|---|---:|---:|---:|",
        f"| Validated decisions from the 330 accepted missions | {campaign_manifest['all']['rows']:,} | 100.0% | 100.0% |",
        f"| Prompt-matched, student-valid rows | {prompt_matched_rows:,} | {100.0 * prompt_matched_rows / campaign_manifest['all']['rows']:.1f}% | {100.0 * prompt_matched_rows / campaign_manifest['all']['rows']:.1f}% |",
        f"| Final v7 natural rows after the 208-row action-quality gate | {len(data['all_natural']):,} | {100.0 * len(data['all_natural']) / prompt_matched_rows:.1f}% | {100.0 * len(data['all_natural']) / campaign_manifest['all']['rows']:.1f}% |",
        f"| Unique rows assigned to training | {len(data['train_natural']):,} | split, not filtering | {100.0 * len(data['train_natural']) / campaign_manifest['all']['rows']:.1f}% |",
        f"| Actual one-epoch training exposures after resampling | {len(data['actual_train_epoch']):,} | 165.0% of unique train | not a unique-row retention rate |",
        "",
        "## Scenario size: target ships per mission",
        "",
        "A mission's target-ship count is read from its immutable `.scenario.json` generator record. Total vessels equals own ship plus target ships.",
        "",
        "| Target ships | Total vessels | All accepted missions | Actual training missions | Natural rows | Actual epoch exposures |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    all_mission_size = {source: audits[source]["configured_count"] for source in sources}
    natural_train_sources = {row["source"] for row in data["train_natural"]}
    actual_train_sources = {row["source"] for row in data["actual_train_epoch"]}
    natural_size_rows = Counter(row["configured_contacts"] for row in data["all_natural"])
    actual_size_rows = Counter(row["configured_contacts"] for row in data["actual_train_epoch"])
    for count in sorted(set(all_mission_size.values())):
        all_missions = sum(value == count for value in all_mission_size.values())
        actual_missions = sum(all_mission_size[source] == count for source in actual_train_sources)
        lines.append(f"| {count} | {count + 1} | {pct(all_missions, len(all_mission_size))} | {pct(actual_missions, len(actual_train_sources))} | {pct(natural_size_rows[count], len(data['all_natural']))} | {pct(actual_size_rows[count], len(data['actual_train_epoch']))} |")
    lines.append("")

    lines += [
        "## Configured scenario archetypes",
        "",
        "These labels come from the random-scenario generator files, not from post-hoc model reasoning. Multi-ship missions contribute one target-ship instance per configured contact, and one mission may therefore appear in several archetype rows.",
        "",
        "| Generator encounter kind | Configured target ships | Missions containing kind | Actual-training missions containing kind |",
        "|---|---:|---:|---:|",
    ]
    configured_kind_counts: Counter[str] = Counter()
    kind_missions: defaultdict[str, set[str]] = defaultdict(set)
    for source in sources:
        for kind in [str(item.get("kind") or "unknown") for item in audits[source]["configured_contacts"]]:
            configured_kind_counts[kind] += 1
            kind_missions[kind].add(source)
    total_configured_contacts = sum(configured_kind_counts.values())
    for kind, count in sorted(configured_kind_counts.items(), key=lambda item: (-item[1], item[0])):
        actual_count = len(kind_missions[kind] & actual_train_sources)
        lines.append(f"| `{kind}` | {pct(count, total_configured_contacts)} | {pct(len(kind_missions[kind]), len(sources))} | {pct(actual_count, len(actual_train_sources))} |")
    lines += [
        "",
        "### Designed CPA band per configured target ship",
        "",
        "| Designed band | Configured target ships | Missions containing band |",
        "|---|---:|---:|",
    ]
    configured_band_counts: Counter[str] = Counter()
    band_missions: defaultdict[str, set[str]] = defaultdict(set)
    for source in sources:
        for band in [str(item.get("designed_cpa_band") or "unknown") for item in audits[source]["configured_contacts"]]:
            configured_band_counts[band] += 1
            band_missions[band].add(source)
    for band, count in sorted(configured_band_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| `{band}` | {pct(count, total_configured_contacts)} | {pct(len(band_missions[band]), len(sources))} |")
    lines += [
        "",
        "### Scenario-generation variants",
        "",
        "| Variant | Missions | Natural rows | Actual epoch exposures |",
        "|---|---:|---:|---:|",
    ]
    variant_missions: defaultdict[str, set[str]] = defaultdict(set)
    variant_natural: Counter[str] = Counter()
    variant_actual: Counter[str] = Counter()
    for row in data["all_natural"]:
        variant_missions[row["scenario_variant"]].add(row["source"])
        variant_natural[row["scenario_variant"]] += 1
    for row in data["actual_train_epoch"]:
        variant_actual[row["scenario_variant"]] += 1
    for variant in sorted(variant_missions):
        lines.append(f"| `{variant}` | {pct(len(variant_missions[variant]), len(sources))} | {pct(variant_natural[variant], len(data['all_natural']))} | {pct(variant_actual[variant], len(data['actual_train_epoch']))} |")
    lines.append("")

    contact_categories = [str(value) for value in sorted({row["contacts"] for row in data["all_natural"]})]
    lines += counter_table(
        "Contacts visible per decision",
        contact_categories,
        [("Unique train", data["train_natural"]), ("Actual epoch", data["actual_train_epoch"]), ("Validation", data["validation"]), ("Test", data["test"])],
        "contacts",
    )

    lines += counter_table(
        "Primary persistent encounter classification per decision",
        STATE_ORDER,
        [("Unique train", data["train_natural"]), ("Actual epoch", data["actual_train_epoch"]), ("Validation", data["validation"]), ("Test", data["test"])],
        "primary_state",
        {key: f"{key}: {value}" for key, value in STATE_NAMES.items()},
    )

    roles = sorted({row["primary_role"] for row in data["all_natural"]})
    lines += counter_table(
        "Own-ship role for the primary contact",
        roles,
        [("Unique train", data["train_natural"]), ("Actual epoch", data["actual_train_epoch"]), ("Validation", data["validation"]), ("Test", data["test"])],
        "primary_role",
    )

    geometries = sorted({row["geometry"] for row in data["all_natural"]})
    lines += counter_table(
        "Instantaneous geometry of the primary contact",
        geometries,
        [("Unique train", data["train_natural"]), ("Actual epoch", data["actual_train_epoch"]), ("Validation", data["validation"]), ("Test", data["test"])],
        "geometry",
    )

    lines += [
        "## Mission-level encounter coverage",
        "",
        "A mission may enter more than one persistent state, so these rows are multi-label and do not sum to 330 missions.",
        "",
        "| Persistent state observed at least once | Missions |",
        "|---|---:|",
    ]
    mission_states: defaultdict[str, set[str]] = defaultdict(set)
    for row in data["all_natural"]:
        mission_states[row["source"]].update(row["all_states"] or ["none"])
    for state in STATE_ORDER:
        count = sum(state in states for states in mission_states.values())
        lines.append(f"| {state}: {STATE_NAMES[state]} | {pct(count, len(mission_states))} |")
    lines.append("")

    actions = sorted({row["action"] for row in data["train_natural"]})
    natural_actions = Counter(row["action"] for row in data["train_natural"])
    epoch_actions = Counter(row["action"] for row in data["actual_train_epoch"])
    downsampled_actions = Counter(row["action"] for row in data["train_balanced"])
    lines += [
        "## Actual action exposure and the unused downsampled alternative",
        "",
        "| Action | Unique training rows | Actual one-epoch exposures | Prepared downsampled file (unused) | Exposure multiplier |",
        "|---|---:|---:|---:|---:|",
    ]
    for action in actions:
        before = natural_actions[action]
        exposed = epoch_actions[action]
        downsampled = downsampled_actions[action]
        lines.append(f"| {action} | {pct(before, len(data['train_natural']))} | {pct(exposed, len(data['actual_train_epoch']))} | {pct(downsampled, len(data['train_balanced']))} | {exposed / before:.2f}x |")
    lines.append(f"| **Total** | **{len(data['train_natural']):,}** | **{len(data['actual_train_epoch']):,}** | **{len(data['train_balanced']):,}** | **{len(data['actual_train_epoch']) / len(data['train_natural']):.2f}x** |")
    lines += ["", "The actual sampler retained every unique row once, then drew 3,797 additional non-maintain rows uniformly with replacement. It balanced the binary maintain/non-maintain exposure at 50/50 while preserving the relative action mixture only approximately within the resampled non-maintain class. The separately prepared 2,068-row file used maintain downsampling, but v7 did not train on it.", ""]

    direction_counts = Counter(row["direction"] for row in data["actual_train_epoch"] if row["action"] == "alter_course")
    lines += [
        "### Alter-course direction in the actual training epoch",
        "",
        "| Direction | Rows |",
        "|---|---:|",
    ]
    for direction in sorted(direction_counts):
        lines.append(f"| {direction} | {pct(direction_counts[direction], sum(direction_counts.values()))} |")
    lines.append("")

    lines += [
        "## Privileged evidence availability",
        "",
        "These fields were available to the teacher and quality gate, but removed from the final student prompt.",
        "",
        "| Split | Candidate-action lookahead present | Active-action review present |",
        "|---|---:|---:|",
    ]
    for name in ("train_natural", "actual_train_epoch", "validation", "test"):
        split = data[name]
        lines.append(f"| {name.replace('_', ' ')} | {pct(sum(row['lookahead'] for row in split), len(split))} | {pct(sum(row['active_review'] for row in split), len(split))} |")
    lines.append("")

    campaign_missions: defaultdict[str, set[str]] = defaultdict(set)
    campaign_rows: Counter[str] = Counter()
    campaign_actual: Counter[str] = Counter()
    for row in data["all_natural"]:
        campaign_missions[row["campaign"]].add(row["source"])
        campaign_rows[row["campaign"]] += 1
    for row in data["actual_train_epoch"]:
        campaign_actual[row["campaign"]] += 1
    lines += [
        "## Source campaign distribution",
        "",
        "| Campaign | Accepted missions | Natural rows | Actual epoch exposures |",
        "|---|---:|---:|---:|",
    ]
    for campaign in sorted(campaign_missions):
        lines.append(f"| `{campaign}` | {len(campaign_missions[campaign])} | {campaign_rows[campaign]:,} | {campaign_actual[campaign]:,} |")
    lines.append("")

    gate = manifest.get("action_quality_gate") or {}
    lines += [
        "## Filtering and leakage controls",
        "",
        f"- Source missions were required to maintain at least **{manifest.get('source_minimum_range_m', 500):g} m** minimum separation.",
        f"- The action-quality gate rejected **{int(gate.get('rejected_rows') or 0):,} rows** after mission acceptance.",
    ]
    for reason, count in sorted((gate.get("reasons") or {}).items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"  - `{reason}`: {count:,}")
    lines += [
        "- Training, validation, and test are split by source mission, with zero mission overlap.",
        "- Prompt-hash deduplication was performed before the final quality/directional transformations.",
        "- The final student input contains only the short OOW system instruction and deterministic bridge narrative. Numeric state, oracle state/role, candidate lookahead, active-action review, and teacher-only instructions are absent.",
        "- Signed course labels were losslessly converted to categorical `port`/`starboard` plus a positive magnitude.",
        "",
        "## Interpretation cautions",
        "",
        "- Rows are temporally correlated observations from missions, not independent scenarios. Report both row counts and mission counts.",
        "- Actual balancing is exposure-level oversampling of the combined non-maintain class, not deletion of natural training rows and not independent balancing of every action.",
        "- `SF` means the persistent classifier's entry criteria were not active; it does not mean no vessel was visible or that no prudent action was needed.",
        "- `geometrical_state` is instantaneous geometry, whereas `state` is the persistent state-machine classification. They should not be presented as interchangeable labels.",
        "- The actual v7 run retained all 262 natural-training missions. One mission is absent only from the unused downsampled alternative.",
        "",
        "## Reproduction",
        "",
        "```bash",
        f"python3 tools/analyze_sft_distribution.py --dataset-dir {args.dataset_dir} --run-config {args.run_config} --output {args.output}",
        "```",
        "",
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
