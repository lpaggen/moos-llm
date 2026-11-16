#!/usr/bin/env python3
"""Generate and score held-out OOW decisions from a Qwen3 PEFT adapter."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_ADAPTER = "models/qwen3_8b_oow_colregs_qlora"
DEFAULT_BASE_MODEL = "Qwen/Qwen3-8B"
DEFAULT_TEST = "sft/output/prepared/chat/test_natural.jsonl"
DEFAULT_OUTPUT = "sft/output/evaluation"
ACTIONS = (
    "skip",
    "maintain_course",
    "alter_course",
    "set_speed",
    "stop",
    "resume",
    "rejoin_route",
)
REFERENCE_MARKER = "\n\nCOLREG REFERENCE MATERIAL:"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as destination:
        for row in rows:
            destination.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )


def strict_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def expected_label(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["completion"][0]["content"])


def without_colreg_references(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose user prompt contains no retrieved COLREG text."""
    transformed = json.loads(json.dumps(row))
    user_text = transformed["prompt"][-1]["content"]
    marker_start = user_text.index(REFERENCE_MARKER)
    transformed["prompt"][-1]["content"] = user_text[:marker_start].rstrip()
    transformed["reference_mode"] = "none"
    return transformed


def action_constraints(row: dict[str, Any]) -> dict[str, Any]:
    user_text = row["prompt"][-1]["content"]
    try:
        payload = json.loads(user_text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("allowed_actions"), list):
        return {"allowed_actions": payload["allowed_actions"]}
    marker = "\n\nPERMITTED ACTIONS\n"
    if marker in user_text:
        start = user_text.index(marker) + len(marker)
        constraints, _ = json.JSONDecoder().raw_decode(user_text[start:].lstrip())
        return constraints
    # Narrative-only ablations intentionally omit the JSON constraints wrapper.
    # Their system message exposes the fixed executable action vocabulary instead.
    return {"allowed_actions": list(ACTIONS)}


def cited_rule_ids(text: Any) -> set[int]:
    if not isinstance(text, str):
        return set()
    ids: set[int] = set()
    for match in re.finditer(r"\bRules?\s+((?:\d{1,2}(?:\s*,\s*|\s+and\s+)?)+)", text):
        ids.update(int(number) for number in re.findall(r"\d{1,2}", match.group(1)))
    return ids


def applicable_rule_ids(row: dict[str, Any]) -> set[int]:
    user_text = row["prompt"][-1]["content"]
    ids = {2, 5, 6, 7}
    try:
        payload = json.loads(user_text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        user_text = str(payload.get("briefing") or "")
    for label in ("Standing navigation rules", "Active encounter rules"):
        for match in re.finditer(rf"{label}: ([0-9, ]+)\.", user_text):
            ids.update(int(number) for number in re.findall(r"\d{1,2}", match.group(1)))
    return ids


def validate_prediction(
    prediction: dict[str, Any] | None, row: dict[str, Any]
) -> tuple[bool, list[str]]:
    if prediction is None:
        return False, ["invalid_json"]
    errors: list[str] = []
    directional = (
        "turn_direction" in expected_label(row)
        if row.get("completion")
        else "turn_direction" in prediction
    )
    required = (
        {"action", "turn_direction", "turn_degrees", "speed", "reason", "auxiliary_commands"}
        if directional
        else {"action", "degrees", "speed", "reason", "auxiliary_commands"}
    )
    if set(prediction) != required:
        errors.append("schema_fields")
    action = prediction.get("action")
    constraints = action_constraints(row)
    allowed = constraints.get("allowed_actions", [])
    candidates = constraints.get("candidate_actions", [])
    if action not in allowed:
        errors.append("action_not_allowed")
    candidate = next(
        (item for item in candidates if item.get("action") == action), None
    )
    if candidates and candidate is None:
        errors.append("candidate_missing")
    try:
        speed = float(prediction.get("speed"))
        if directional:
            direction = prediction.get("turn_direction")
            magnitude = float(prediction.get("turn_degrees"))
            degrees = (
                magnitude if direction == "starboard"
                else -magnitude if direction == "port"
                else 0.0
            )
        else:
            direction = None
            magnitude = abs(float(prediction.get("degrees")))
            degrees = float(prediction.get("degrees"))
    except (TypeError, ValueError):
        errors.append("numeric_type")
        degrees = speed = magnitude = math.nan
        direction = None
    if action == "alter_course":
        degree_range = candidate.get("degrees_range", {}) if candidate else {}
        minimum = float(degree_range.get("min", -60.0))
        maximum = float(degree_range.get("max", 60.0))
        if not math.isfinite(degrees) or degrees == 0.0 or not minimum <= degrees <= maximum:
            errors.append("degrees_outside_candidate")
        if directional and direction not in {"port", "starboard"}:
            errors.append("turn_direction")
    else:
        if math.isfinite(degrees) and degrees != 0.0:
            errors.append("irrelevant_degrees")
        if directional and (direction != "none" or magnitude != 0.0):
            errors.append("irrelevant_turn_fields")
    if action == "set_speed":
        if candidate is not None and candidate.get("speed") is not None:
            expected = float(candidate["speed"])
            if not math.isfinite(speed) or not math.isclose(speed, expected, abs_tol=1e-6):
                errors.append("speed_mismatch")
        elif not math.isfinite(speed) or speed <= 0.0:
            errors.append("speed_outside_candidate")
    elif math.isfinite(speed) and speed != 0.0:
        errors.append("irrelevant_speed")
    if not isinstance(prediction.get("reason"), str) or not prediction.get("reason", "").strip():
        errors.append("empty_reason")
    auxiliaries = prediction.get("auxiliary_commands")
    if not isinstance(auxiliaries, list) or len(auxiliaries) > 2:
        errors.append("auxiliary_schema")
    elif isinstance(auxiliaries, list):
        types = [item.get("type") for item in auxiliaries if isinstance(item, dict)]
        if len(types) != len(auxiliaries) or len(types) != len(set(types)):
            errors.append("auxiliary_schema")
        for auxiliary in auxiliaries:
            if not isinstance(auxiliary, dict):
                continue
            if auxiliary.get("type") == "sound_horn":
                signal = auxiliary.get("signal")
                if signal == "altering_course_to_starboard" and not (
                    action == "alter_course" and degrees > 0
                ):
                    errors.append("horn_direction")
                if signal == "altering_course_to_port" and not (
                    action == "alter_course" and degrees < 0
                ):
                    errors.append("horn_direction")
    return not errors, errors


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    gold_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    true_positive: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    exact_actions = 0
    exact_decisions = 0
    strict_valid = 0
    constraint_valid = 0
    unsupported_citations = 0
    unsupported_citations_excluding_rule_8 = 0
    cited_examples = 0
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    by_mode: dict[str, Counter[str]] = defaultdict(Counter)

    for item in rows:
        row = item["example"]
        gold = item["gold"]
        prediction = item.get("prediction")
        gold_action = str(gold.get("action"))
        predicted_action = (
            str(prediction.get("action")) if isinstance(prediction, dict) else "<invalid>"
        )
        gold_counts[gold_action] += 1
        predicted_counts[predicted_action] += 1
        confusion[gold_action][predicted_action] += 1
        mode = str(row.get("reference_mode"))
        by_mode[mode]["total"] += 1
        if prediction is not None:
            strict_valid += 1
            by_mode[mode]["valid_json"] += 1
        valid, validation_errors = validate_prediction(prediction, row)
        errors.update(validation_errors)
        if valid:
            constraint_valid += 1
            by_mode[mode]["constraint_valid"] += 1
        if predicted_action == gold_action:
            exact_actions += 1
            true_positive[gold_action] += 1
            by_mode[mode]["action_correct"] += 1
        if prediction == gold:
            exact_decisions += 1
        if isinstance(prediction, dict):
            citations = cited_rule_ids(prediction.get("reason"))
            if citations:
                cited_examples += 1
                applicable = applicable_rule_ids(row)
                unsupported = citations - applicable
                if unsupported:
                    unsupported_citations += 1
                if unsupported - {8}:
                    unsupported_citations_excluding_rule_8 += 1

    per_action: dict[str, Any] = {}
    f1_values = []
    for action in ACTIONS:
        support = gold_counts[action]
        if not support:
            continue
        precision = safe_div(true_positive[action], predicted_counts[action])
        recall = safe_div(true_positive[action], support)
        f1 = safe_div(2 * precision * recall, precision + recall)
        f1_values.append(f1)
        per_action[action] = {
            "support": support,
            "predicted": predicted_counts[action],
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    return {
        "examples": total,
        "strict_json_rate": safe_div(strict_valid, total),
        "constraint_valid_rate": safe_div(constraint_valid, total),
        "exact_action_accuracy": safe_div(exact_actions, total),
        "macro_f1": safe_div(sum(f1_values), len(f1_values)),
        "exact_full_decision_rate": safe_div(exact_decisions, total),
        "validation_errors": dict(errors),
        "per_action": per_action,
        "confusion_matrix": {key: dict(value) for key, value in confusion.items()},
        "by_reference_mode": {
            mode: {
                "examples": values["total"],
                "json_rate": safe_div(values["valid_json"], values["total"]),
                "constraint_valid_rate": safe_div(
                    values["constraint_valid"], values["total"]
                ),
                "action_accuracy": safe_div(values["action_correct"], values["total"]),
            }
            for mode, values in by_mode.items()
        },
        "citation_audit": {
            "examples_with_rule_citations": cited_examples,
            "examples_citing_rules_not_listed_as_applicable": unsupported_citations,
            "examples_citing_nonlisted_rules_other_than_rule_8": (
                unsupported_citations_excluding_rule_8
            ),
            "note": (
                "Rule 8 is often mentioned negatively (for example, that no Rule 8 "
                "action is required), so nonlisted Rule 8 mentions are not automatically errors."
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default=DEFAULT_ADAPTER)
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument(
        "--base-model-only",
        action="store_true",
        help="Evaluate the untouched base model without loading the PEFT adapter.",
    )
    parser.add_argument("--test-file", default=DEFAULT_TEST)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--reference-condition",
        choices=("original", "none"),
        default="original",
        help=(
            "Reference treatment applied to every test example. 'none' removes "
            "all retrieved COLREG text while preserving the scenario and constraints."
        ),
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="score an existing predictions.jsonl without loading a model",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    predictions_path = output_dir / "predictions.jsonl"
    metrics_path = output_dir / "metrics.json"
    test_rows = read_jsonl(Path(args.test_file))
    if args.reference_condition == "none":
        test_rows = [without_colreg_references(row) for row in test_rows]
    if args.limit is not None:
        test_rows = test_rows[: args.limit]

    existing = read_jsonl(predictions_path) if predictions_path.exists() else []
    if args.score_only:
        metrics = score(existing)
        print(json.dumps(metrics, indent=2))
        metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        return
    if existing and not args.resume:
        raise SystemExit(
            f"{predictions_path} already exists; use --resume or choose another output"
        )
    completed_ids = {row["example_id"] for row in existing}
    pending = [row for row in test_rows if row["example_id"] not in completed_ids]

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for generation evaluation")
    adapter_path = Path(args.adapter)
    if args.base_model_only:
        base_model = args.base_model
    else:
        adapter_config = json.loads(
            (adapter_path / "adapter_config.json").read_text(encoding="utf-8")
        )
        base_model = adapter_config["base_model_name_or_path"]
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(adapter_path, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype,
        ),
        torch_dtype=dtype,
        device_map={"": 0},
    )
    if not args.base_model_only:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()

    for offset in range(0, len(pending), args.batch_size):
        batch = pending[offset : offset + args.batch_size]
        texts = [
            tokenizer.apply_chat_template(
                row["prompt"],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            for row in batch
        ]
        encoded = tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=4096,
        ).to(model.device)
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        prompt_length = encoded["input_ids"].shape[1]
        outputs = tokenizer.batch_decode(
            generated[:, prompt_length:], skip_special_tokens=True
        )
        results = []
        for row, text in zip(batch, outputs):
            results.append(
                {
                    "example_id": row["example_id"],
                    "example": row,
                    "gold": expected_label(row),
                    "raw_output": text,
                    "prediction": strict_json(text),
                }
            )
        append_jsonl(predictions_path, results)
        done = len(completed_ids) + min(offset + len(batch), len(pending))
        print(f"Generated {done}/{len(test_rows)}", flush=True)

    all_predictions = read_jsonl(predictions_path)
    selected_ids = {row["example_id"] for row in test_rows}
    all_predictions = [
        row for row in all_predictions if row["example_id"] in selected_ids
    ]
    metrics = score(all_predictions)
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
