#!/usr/bin/env python3
"""Compare decision timing and prompt size across Imazu campaigns."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


GUIDANCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments/qwen8b_colregs_rag/colregs_ship_guidance.json"
)


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse_timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def load_guidance(path: Path = GUIDANCE_PATH) -> dict[int, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    if isinstance(raw, list):
        records = ((record["rule"], record.get("ship_guidance")) for record in raw)
    else:
        if "rules" in raw:
            raw = raw["rules"]
        records = raw.items()
    for key, value in records:
        lines = value.get("guidance", value) if isinstance(value, dict) else value
        if isinstance(lines, str):
            lines = [lines]
        result[int(key)] = [str(line) for line in lines]
    return result


def render_retrieval(selected: list[dict[str, Any]], guidance: dict[int, list[str]]) -> str:
    blocks = []
    for item in sorted(selected, key=lambda value: int(value["rule"])):
        rule = int(item["rule"])
        title = str(item.get("title") or "")
        chunk_id = str(item.get("chunk_id") or f"colreg-{rule}")
        if rule in guidance:
            body = "\n".join(f"- {line}" for line in guidance[rule])
            blocks.append(
                f"[{chunk_id}] Rule {rule} — {title}\n"
                f"OPERATIONAL SUMMARY\n{body}"
            )
        else:
            blocks.append(
                f"[{chunk_id}] Rule {rule} — {title}\n{str(item.get('text') or '')}"
            )
    return "\n\n".join(blocks)


def assembled_user_prompt(
    prompt: dict[str, Any], retrieval: dict[str, Any] | None,
    guidance: dict[int, list[str]],
) -> str:
    user_prompt = str(prompt.get("user_prompt") or "")
    if retrieval is None:
        return user_prompt
    context = render_retrieval(retrieval.get("selected") or [], guidance)
    allowed = (prompt.get("constraints") or {}).get("allowed_actions") or []
    return (
        f"BRIDGE BRIEFING\n{user_prompt.strip()}\n\n"
        f"RETRIEVED COLREG PROVISIONS\n{context}\n\n"
        f"ALLOWED ACTIONS\n{json.dumps(allowed)}"
    )


def text_metrics(text: str) -> dict[str, int]:
    characters = len(text)
    return {
        "characters": characters,
        "utf8_bytes": len(text.encode("utf-8")),
        "words": len(text.split()),
        "estimated_tokens": math.ceil(characters / 4),
    }


def campaign_metrics(run: Path, guidance: dict[int, list[str]]) -> dict[str, Any]:
    prompt_metrics: dict[str, list[float]] = {
        "characters": [], "utf8_bytes": [], "words": [], "estimated_tokens": [],
    }
    system_characters: list[float] = []
    user_characters: list[float] = []
    inter_decision_s: list[float] = []
    decision_latency_s: list[float] = []
    oow_duration_s: list[float] = []
    cases = 0
    rag_cycles = 0
    model = None

    for audit_path in sorted(run.glob("case_*/audit.jsonl")):
        rows = read_rows(audit_path)
        prompts = {
            row.get("cycle_id"): row
            for row in rows
            if row.get("event") == "oow_prompt_input"
        }
        retrievals = {
            row.get("cycle_id"): row
            for row in rows
            if row.get("event") == "oow_colregs_retrieval"
        }
        decisions = [row for row in rows if row.get("event") == "oow_decision"]
        performances = [row for row in rows if row.get("event") == "cycle_performance"]
        if not prompts and not decisions:
            continue
        cases += 1
        decision_times = [parse_timestamp(row["timestamp"]) for row in decisions]
        inter_decision_s.extend(
            after - before for before, after in zip(decision_times, decision_times[1:])
        )
        for decision in decisions:
            prompt = prompts.get(decision.get("cycle_id"))
            if prompt is None:
                continue
            model = model or prompt.get("model")
            retrieval = retrievals.get(decision.get("cycle_id"))
            rag_cycles += int(retrieval is not None)
            system_prompt = str(prompt.get("system_prompt") or "")
            user_prompt = assembled_user_prompt(prompt, retrieval, guidance)
            full_prompt = system_prompt + "\n\n" + user_prompt
            metrics = text_metrics(full_prompt)
            for key, value in metrics.items():
                prompt_metrics[key].append(float(value))
            system_characters.append(float(len(system_prompt)))
            user_characters.append(float(len(user_prompt)))
            decision_latency_s.append(
                parse_timestamp(decision["timestamp"]) - parse_timestamp(prompt["timestamp"])
            )
        oow_duration_s.extend(
            float(row["oow_duration_s"])
            for row in performances
            if row.get("oow_duration_s") is not None
        )

    return {
        "run": str(run),
        "model": model,
        "cases": cases,
        "prompts": len(prompt_metrics["characters"]),
        "rag_augmented_prompts": rag_cycles,
        "prompt_size": {key: summary(values) for key, values in prompt_metrics.items()},
        "system_prompt_characters": summary(system_characters),
        "user_prompt_characters": summary(user_characters),
        "inter_decision_seconds": summary(inter_decision_s),
        "prompt_to_decision_seconds": summary(decision_latency_s),
        "logged_oow_duration_seconds": summary(oow_duration_s),
    }


def reduction(reference: float | None, candidate: float | None) -> float | None:
    if reference is None or candidate is None or reference == 0:
        return None
    return 100.0 * (reference - candidate) / reference


def comparison(
    finetuned: dict[str, Any], baselines: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {}
    for name, baseline in baselines.items():
        prompt_reduction = {}
        for metric in ("characters", "utf8_bytes", "words", "estimated_tokens"):
            prompt_reduction[metric] = {
                statistic: reduction(
                    baseline["prompt_size"][metric][statistic],
                    finetuned["prompt_size"][metric][statistic],
                )
                for statistic in ("mean", "median")
            }
        result[name] = {
            "prompt_reduction_percent": prompt_reduction,
            "system_prompt_character_reduction_percent": {
                statistic: reduction(
                    baseline["system_prompt_characters"][statistic],
                    finetuned["system_prompt_characters"][statistic],
                )
                for statistic in ("mean", "median")
            },
            "decision_latency_reduction_percent": {
                statistic: reduction(
                    baseline["prompt_to_decision_seconds"][statistic],
                    finetuned["prompt_to_decision_seconds"][statistic],
                )
                for statistic in ("mean", "median")
            },
        }
    return result


def named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, path = value.split("=", 1)
    if not name or not path:
        raise argparse.ArgumentTypeError("expected non-empty NAME=PATH")
    return name, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finetuned", type=Path, required=True)
    parser.add_argument("--baseline", type=named_path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    guidance = load_guidance()
    finetuned = campaign_metrics(args.finetuned, guidance)
    baselines = {name: campaign_metrics(path, guidance) for name, path in args.baseline}
    report = {
        "method": {
            "prompt": "system prompt plus actual user prompt; logged RAG selections are reconstructed",
            "estimated_tokens": "ceil(Unicode character count / 4); not tokenizer-exact",
            "inter_decision": "wall-clock difference between consecutive oow_decision events within each case",
            "decision_latency": "wall-clock difference from oow_prompt_input to oow_decision",
            "reduction": "100 * (baseline - finetuned) / baseline; negative values indicate growth",
            "retry_limitation": "invalid-response retry prompts are not recorded and are excluded",
        },
        "finetuned": finetuned,
        "baselines": baselines,
        "comparisons": comparison(finetuned, baselines),
    }
    output = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
