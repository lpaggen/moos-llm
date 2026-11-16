#!/usr/bin/env python3
"""Prepare, run, validate, and score a blinded GPT relevance judgment batch."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.build_maib_lesson_cards import (  # noqa: E402
    MODEL_PRICES,
    approximate_tokens,
    response_content,
)
from tools.evaluate_maib_state_retrieval import (  # noqa: E402
    DEFAULT_CARDS,
    DEFAULT_OUTPUT,
    calculate_metrics,
    markdown_results,
    paired_differences,
    read_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_LABELS = DEFAULT_OUTPUT / "relevance_labels.csv"
DEFAULT_WORK_DIR = DEFAULT_OUTPUT / "gpt_judge"
DEFAULT_MODEL = "gpt-6.1-sol"

SYSTEM_PROMPT = """You are judging whether historical MAIB investigation lesson cards are relevant to an observed navigation state.

This is a retrieval evaluation, not a navigation-decision task. Judge each supplied state-card pair independently. Do not prescribe a manoeuvre and do not compare cards against one another.

Use exactly this relevance rubric:
- 2 (relevant): the investigator finding addresses a mechanism demonstrably present in the supplied navigation state, and the lesson's applicability conditions are satisfied by supplied facts.
- 1 (partially relevant): the finding or lesson addresses a related mechanism, but an important circumstance differs or is unknown.
- 0 (irrelevant): the card does not provide applicable reasoning for the supplied encounter.

The threshold for label 2 is strict. Treat every circumstance absent from the navigation state as unknown. Do not infer fatigue, distraction, equipment use or failure, watchkeeping practice, communication, traffic constraints, environmental effects, vessel capability, or human intent unless explicitly supplied. Shared words such as collision, crossing, overtaking, CPA, or close pass do not by themselves establish relevance. Apply each non-transfer condition. Source quotations establish what the historical investigation found; they do not establish that the same mechanism exists in the current state.

Return one assessment for every annotation_id in exactly the supplied order. Give a concise reason grounded in the supplied state and the card's applicability conditions. You are not given, and must not infer, which retrieval method selected a card."""


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotation_id: str = Field(min_length=4, max_length=16)
    relevance_label: Literal[0, 1, 2]
    reason: str = Field(min_length=20, max_length=600)


class JudgmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[Judgment] = Field(min_length=1, max_length=12)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_label_sheet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    required = {
        "annotation_id",
        "state_id",
        "navigation_state",
        "card_finding",
        "transferable_lesson",
        "non_transfer_conditions",
        "supporting_quotations",
        "relevance_label",
        "reviewer_notes",
    }
    if required - set(fields):
        raise ValueError(f"missing label fields: {sorted(required - set(fields))}")
    if not rows:
        raise ValueError(f"empty label sheet: {path}")
    identifiers = [str(row["annotation_id"]) for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("annotation IDs are not unique")
    return fields, rows


def compact_candidate(row: dict[str, str]) -> dict[str, Any]:
    return {
        "annotation_id": row["annotation_id"],
        "investigator_finding": row["card_finding"],
        "transferable_lesson": row["transferable_lesson"],
        "non_transfer_conditions": json.loads(row["non_transfer_conditions"]),
        "supporting_quotations": json.loads(row["supporting_quotations"]),
    }


def strict_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "maib_relevance_judgments",
            "strict": True,
            "schema": JudgmentResponse.model_json_schema(),
        },
    }


def build_requests(
    labels_path: Path,
    model: str,
    max_completion_tokens: int,
    reasoning_effort: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    _, rows = read_label_sheet(labels_path)
    by_state: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_state[str(row["state_id"])].append(row)
    requests: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    estimated_input_tokens = 0
    response_format = strict_response_format()
    for state_id, state_rows in by_state.items():
        queries = {row["navigation_state"] for row in state_rows}
        if len(queries) != 1:
            raise ValueError(f"state {state_id} contains inconsistent navigation text")
        payload = {
            "state_id": state_id,
            "navigation_state": state_rows[0]["navigation_state"],
            "candidate_cards": [compact_candidate(row) for row in state_rows],
        }
        user_text = json.dumps(payload, ensure_ascii=False, indent=2)
        custom_id = f"maib-relevance-{state_id.lower()}"
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "response_format": response_format,
            "max_completion_tokens": max_completion_tokens,
            "reasoning_effort": reasoning_effort,
        }
        requests.append(
            {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body,
            }
        )
        manifests.append(
            {
                "custom_id": custom_id,
                "state_id": state_id,
                "annotation_ids": [row["annotation_id"] for row in state_rows],
                "rows": state_rows,
            }
        )
        estimated_input_tokens += approximate_tokens(
            SYSTEM_PROMPT + user_text + json.dumps(response_format)
        )
    if len(requests) != 22 or sum(len(item["rows"]) for item in manifests) != 137:
        raise ValueError(
            f"expected 22 state requests and 137 pairs, got "
            f"{len(requests)} and {sum(len(item['rows']) for item in manifests)}"
        )
    return requests, manifests, estimated_input_tokens


def command_prepare(args: argparse.Namespace) -> None:
    requests, manifests, input_tokens = build_requests(
        args.labels,
        args.model,
        args.max_completion_tokens,
        args.reasoning_effort,
    )
    args.work_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.work_dir / "batch_input.jsonl", requests)
    write_jsonl(args.work_dir / "manifest.jsonl", manifests)
    rates = MODEL_PRICES.get(args.model, {}).get("batch")
    expected_output_tokens = 137 * 100
    estimate: dict[str, Any] = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "judge_type": "language_model",
        "human_ground_truth": False,
        "method_blinded": True,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "requests": len(requests),
        "state_card_pairs": 137,
        "estimated_input_tokens": input_tokens,
        "expected_output_tokens": expected_output_tokens,
        "maximum_completion_tokens_per_request": args.max_completion_tokens,
        "labels_path": str(args.labels),
        "labels_sha256": sha256(args.labels),
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "script_sha256": sha256(Path(__file__)),
    }
    if rates:
        input_rate, output_rate = rates
        estimate.update(
            {
                "batch_input_rate_per_million_usd": input_rate,
                "batch_output_rate_per_million_usd": output_rate,
                "expected_cost_usd": input_tokens / 1_000_000 * input_rate
                + expected_output_tokens / 1_000_000 * output_rate,
                "maximum_cost_usd": input_tokens / 1_000_000 * input_rate
                + len(requests)
                * args.max_completion_tokens
                / 1_000_000
                * output_rate,
            }
        )
    write_json(args.work_dir / "prepare_summary.json", estimate)
    print(json.dumps(estimate, indent=2))


def openai_client():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set; export it or place it in the ignored .env file"
        )
    from openai import OpenAI

    return OpenAI()


def validate_prepared(path: Path) -> None:
    records = read_jsonl(path)
    if len(records) != 22:
        raise ValueError(f"expected 22 prepared requests, got {len(records)}")
    identifiers: set[str] = set()
    for record in records:
        identifier = str(record.get("custom_id") or "")
        if not identifier or identifier in identifiers:
            raise ValueError("missing or duplicate custom_id")
        identifiers.add(identifier)
        body = record.get("body") or {}
        schema = body.get("response_format", {}).get("json_schema", {})
        if schema.get("strict") is not True:
            raise ValueError(f"strict JSON schema missing for {identifier}")
        serialized = json.dumps(record).lower()
        for forbidden in ("method_ranks", "bm25", "bge_dense", "rrf_preferences"):
            if forbidden in serialized:
                raise ValueError(f"request {identifier} leaks method: {forbidden}")


def command_submit(args: argparse.Namespace) -> None:
    path = args.work_dir / "batch_input.jsonl"
    validate_prepared(path)
    client = openai_client()
    with path.open("rb") as stream:
        uploaded = client.files.create(file=stream, purpose="batch")
    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={"purpose": "maib_retrieval_relevance_judgment"},
    )
    state = {
        "batch_id": batch.id,
        "input_file_id": uploaded.id,
        "status": batch.status,
        "submitted_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.work_dir / "batch_job.json", state)
    print(json.dumps(state, indent=2))


def batch_id(work_dir: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    path = work_dir / "batch_job.json"
    if not path.exists():
        raise FileNotFoundError("run submit or provide --batch-id")
    return str(json.loads(path.read_text())["batch_id"])


def command_status(args: argparse.Namespace) -> None:
    batch = openai_client().batches.retrieve(batch_id(args.work_dir, args.batch_id))
    print(batch.model_dump_json(indent=2))


def command_download(args: argparse.Namespace) -> None:
    client = openai_client()
    batch = client.batches.retrieve(batch_id(args.work_dir, args.batch_id))
    if not batch.output_file_id:
        raise RuntimeError(f"batch has no output file; status={batch.status}")
    output = args.work_dir / "batch_output.jsonl"
    client.files.content(batch.output_file_id).write_to_file(output)
    if batch.error_file_id:
        client.files.content(batch.error_file_id).write_to_file(
            args.work_dir / "batch_errors.jsonl"
        )
    print(f"wrote {output}")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def command_validate(args: argparse.Namespace) -> None:
    manifests = {
        item["custom_id"]: item
        for item in read_jsonl(args.work_dir / "manifest.jsonl")
    }
    outputs = read_jsonl(args.work_dir / "batch_output.jsonl")
    output_by_id: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    judgments: list[dict[str, Any]] = []
    usage = Counter()
    fingerprints = Counter()
    response_models = Counter()
    for output in outputs:
        identifier = str(output.get("custom_id") or "")
        if identifier in output_by_id:
            failures.append({"custom_id": identifier, "error": "duplicate response"})
            continue
        output_by_id[identifier] = output
        manifest = manifests.get(identifier)
        if manifest is None:
            failures.append({"custom_id": identifier, "error": "unknown response"})
            continue
        try:
            parsed = JudgmentResponse.model_validate_json(response_content(output))
            returned = [item.annotation_id for item in parsed.assessments]
            if returned != manifest["annotation_ids"]:
                raise ValueError("annotation IDs or order differ from manifest")
            for item in parsed.assessments:
                judgments.append(
                    {
                        **item.model_dump(),
                        "state_id": manifest["state_id"],
                        "custom_id": identifier,
                        "judge_model_requested": args.model,
                    }
                )
            body = output.get("response", {}).get("body", {})
            response_models[str(body.get("model"))] += 1
            fingerprints[str(body.get("system_fingerprint"))] += 1
            response_usage = body.get("usage") or {}
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[field] += int(response_usage.get(field) or 0)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            failures.append({"custom_id": identifier, "error": str(exc)})
    for identifier in manifests:
        if identifier not in output_by_id:
            failures.append({"custom_id": identifier, "error": "missing response"})
    write_jsonl(args.work_dir / "model_judgments.jsonl", judgments)
    write_jsonl(args.work_dir / "validation_failures.jsonl", failures)
    if failures:
        raise RuntimeError(f"{len(failures)} validation failures")
    if len(judgments) != 137:
        raise RuntimeError(f"expected 137 judgments, got {len(judgments)}")

    fields, rows = read_label_sheet(args.labels)
    by_annotation = {item["annotation_id"]: item for item in judgments}
    for row in rows:
        judgment = by_annotation[row["annotation_id"]]
        row["relevance_label"] = str(judgment["relevance_label"])
        row["reviewer_notes"] = (
            f"Automated {args.model} judgment: {judgment['reason']}"
        )
    model_labels = args.work_dir / "model_relevance_labels.csv"
    write_csv(model_labels, fields, rows)

    rankings = read_jsonl(DEFAULT_OUTPUT / "rankings.jsonl")
    key_rows = read_jsonl(DEFAULT_OUTPUT / "private_pool_key.jsonl")
    numeric_labels = {
        item["annotation_id"]: int(item["relevance_label"])
        for item in judgments
    }
    summary, per_state = calculate_metrics(rankings, key_rows, numeric_labels)
    paired = paired_differences(per_state)
    cards = read_jsonl(DEFAULT_CARDS)
    write_jsonl(args.work_dir / "per_scenario.jsonl", per_state)
    rates = MODEL_PRICES.get(args.model, {}).get("batch")
    actual_cost = None
    if rates:
        actual_cost = usage["prompt_tokens"] / 1_000_000 * rates[0] + usage[
            "completion_tokens"
        ] / 1_000_000 * rates[1]
    result = {
        "judge_type": "language_model",
        "human_ground_truth": False,
        "model_requested": args.model,
        "response_models": dict(response_models),
        "system_fingerprints": dict(fingerprints),
        "judgments": len(judgments),
        "label_distribution": dict(
            sorted(Counter(item["relevance_label"] for item in judgments).items())
        ),
        "usage": dict(usage),
        "estimated_batch_cost_usd": actual_cost,
        "methods": summary,
        "paired_differences": paired,
        "raw_output_sha256": sha256(args.work_dir / "batch_output.jsonl"),
        "model_labels_sha256": sha256(model_labels),
    }
    write_json(args.work_dir / "summary.json", result)
    disclosure = """# Model-judged navigation-state retrieval results

**Important:** relevance was assigned by an automated, method-blind language-model judge, not by a human maritime expert. These measurements evaluate agreement with the stated model rubric; they are not independent ground truth.

"""
    rendered = markdown_results(
        summary, per_state, paired, rankings, cards, key_rows, numeric_labels
    )
    rendered = rendered.replace(
        "# Navigation-state lesson-card retrieval results\n\n", "", 1
    )
    (args.work_dir / "RESULTS_MODEL_JUDGED.md").write_text(
        disclosure + rendered, encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


def command_audit(args: argparse.Namespace) -> None:
    judgments = read_jsonl(args.work_dir / "model_judgments.jsonl")
    failures = read_jsonl(args.work_dir / "validation_failures.jsonl")
    identifiers = [item.get("annotation_id") for item in judgments]
    problems = []
    if failures:
        problems.append(f"{len(failures)} response validation failures")
    if len(judgments) != 137 or len(set(identifiers)) != 137:
        problems.append("judgments do not cover 137 unique annotation IDs")
    if any(item.get("relevance_label") not in {0, 1, 2} for item in judgments):
        problems.append("judgment outside 0/1/2")
    result = {
        "ok": not problems,
        "judgments": len(judgments),
        "failures": len(failures),
        "problems": problems,
    }
    print(json.dumps(result, indent=2))
    if problems:
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="command", required=True)
    prepare = subs.add_parser("prepare")
    prepare.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    prepare.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    prepare.add_argument("--model", default=DEFAULT_MODEL)
    prepare.add_argument("--reasoning-effort", default="low")
    prepare.add_argument("--max-completion-tokens", type=int, default=5000)
    prepare.set_defaults(function=command_prepare)
    submit = subs.add_parser("submit")
    submit.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    submit.set_defaults(function=command_submit)
    for name, function in (("status", command_status), ("download", command_download)):
        command = subs.add_parser(name)
        command.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
        command.add_argument("--batch-id")
        command.set_defaults(function=function)
    validate = subs.add_parser("validate")
    validate.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    validate.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    validate.add_argument("--model", default=DEFAULT_MODEL)
    validate.set_defaults(function=command_validate)
    audit = subs.add_parser("audit")
    audit.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    audit.set_defaults(function=command_audit)
    return result


def main() -> None:
    args = parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
