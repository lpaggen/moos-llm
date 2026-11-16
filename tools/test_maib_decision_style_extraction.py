#!/usr/bin/env python3
"""Extract MAIB lessons relevant to the style of recent navigation inputs.

Recent audit narratives are examples of the information and decision interface
the runtime provides.  They are not cases to which a source passage must already
apply.  Each unique MAIB passage is screened independently for any grounded
lesson that could inform this class of decision in a compatible future state.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from tools.build_maib_lesson_cards import (
        LessonCardCandidate,
        MODEL_PRICES,
        approximate_tokens,
        normalized,
        response_content,
        resolve_reasoning_effort,
        write_jsonl,
    )
except ModuleNotFoundError:
    from build_maib_lesson_cards import (
        LessonCardCandidate,
        MODEL_PRICES,
        approximate_tokens,
        normalized,
        response_content,
        resolve_reasoning_effort,
        write_jsonl,
    )


DEFAULT_AUDIT_ROOT = Path(
    "logs/evaluations/imazu/maib_rag_captain_critic_lockstep_30s_warp20"
)
DEFAULT_SAMPLES: tuple[tuple[str, int], ...] = (
    ("case_01", 44),
    ("case_02", 47),
    ("case_13", 55),
    ("case_16", 81),
    ("case_20", 74),
)

SYSTEM_PROMPT = """You extract source-grounded marine-accident lessons for a navigation decision-support system.

The supplied audit states are examples of the STYLE OF RUNTIME INPUT: measured vessel motion, relative bearing, range, closing speed, CPA/TCPA, trends, track quality, visibility, multiple contacts, current control state, and an OOW proposal. They are not target cases that the source passage must match. Do not ask whether the source applies to any one example state.

For the single supplied MAIB passage, determine whether it contains any investigator finding, causal analysis, conclusion, or explicit safety issue that could meaningfully inform this class of navigation decision in SOME compatible future state.

Rules:
1. Use the audit examples only to understand the decision-input format and kinds of decisions. They are not evidence for a lesson card.
2. Use only the MAIB passage to extract findings and lessons.
3. Do not reject a passage merely because its mechanism is absent from the example states. Instead state which live facts would need to be present before using the lesson.
4. Reject passages that contain only administrative background, crew particulars, analytical method, raw chronology, figures, or generic regulations without an investigator finding or causal conclusion.
5. Extract every distinct, defensible lesson supported by the passage; do not impose an arbitrary target number.
6. Every card must contain at least one exact quotation from the supplied passage and cite only its supplied page.
7. In quotation fields, copy only the exact text. Do not add surrounding quotation marks unless they occur in the passage. Do not repair OCR.
8. Separate source finding from runtime use. For each card, state the explicit live evidence that would be required before applying it and the inferences that remain prohibited.
9. Never infer COLREG roles, geometry, equipment use, distraction, visibility, intentions, or measurement error unless a future runtime state explicitly supports them.
10. Do not copy an accident manoeuvre, prescribe a heading or speed, or treat an accident outcome as a prediction.
11. Submarine findings may transfer only at the level of an explicitly supported causal mechanism, such as range overestimation or contact misclassification. Submarine-specific doctrine and sensing procedures are not surface-vessel recommendations.

This is lesson extraction for a decision-input class, not evaluation of a particular live decision.
"""

PassageType = Literal[
    "investigator_analysis",
    "conclusions_or_safety_issues",
    "background_or_method",
    "chronology",
    "quoted_rules_or_guidance",
    "mixed",
    "unclear",
]
InputFeature = Literal[
    "relative_bearing",
    "range_or_closing_speed",
    "cpa_tcpa",
    "traffic_trend",
    "track_quality_or_uncertainty",
    "visibility",
    "multi_contact_context",
    "current_manoeuvre_or_control",
    "oow_proposal",
    "vessel_identity_or_type",
    "other",
]


class DecisionStyleCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card: LessonCardCandidate
    relevant_input_features: list[InputFeature] = Field(min_length=1)
    potential_decision_use: str = Field(min_length=20, max_length=1000)
    required_live_evidence: list[str] = Field(min_length=1, max_length=8)
    prohibited_inferences: list[str] = Field(min_length=1, max_length=8)


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=128)
    source_passage_type: PassageType
    source_contains_investigator_reasoning: bool
    contains_extractable_lessons: bool
    confidence: float = Field(ge=0.0, le=1.0)
    source_assessment: str = Field(min_length=20, max_length=1200)
    lessons: list[DecisionStyleCard]


def read_cycle(path: Path, cycle_id: int) -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    if not path.exists():
        raise FileNotFoundError(f"audit does not exist: {path}")
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            if record.get("cycle_id") == cycle_id:
                events[str(record.get("event"))] = record
    required = {"oow_prompt_input", "oow_decision", "maib_retrieval"}
    missing = sorted(required - set(events))
    if missing:
        raise ValueError(f"{path} cycle {cycle_id} is missing events: {missing}")
    return events


def collect_material(
    audit_root: Path, samples: list[tuple[str, int]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    examples: list[dict[str, Any]] = []
    chunks: dict[str, dict[str, Any]] = {}
    for case_name, cycle_id in samples:
        audit_path = audit_root / case_name / "audit.jsonl"
        events = read_cycle(audit_path, cycle_id)
        prompt = events["oow_prompt_input"]
        retrieval = events["maib_retrieval"]
        reference = {
            "audit_path": str(audit_path),
            "case": case_name,
            "cycle_id": cycle_id,
        }
        examples.append({
            "audit_reference": reference,
            "rendered_narrative": prompt.get("narrative"),
            "structured_state": prompt.get("state"),
            "oow_proposal": events["oow_decision"].get("decision"),
        })
        for rank, item in enumerate(retrieval.get("results") or [], start=1):
            chunk_id = str(item.get("chunk_id") or "")
            if not chunk_id:
                continue
            if chunk_id not in chunks:
                chunks[chunk_id] = {
                    "chunk_id": chunk_id,
                    "report_title": item.get("report_title"),
                    "source_file": item.get("source_file"),
                    "page": int(item.get("page") or 0),
                    "text": str(item.get("text") or ""),
                    "audit_retrieval_occurrences": [],
                }
            chunks[chunk_id]["audit_retrieval_occurrences"].append({
                **reference,
                "retrieval_rank": rank,
                "retrieval_score": item.get("score"),
            })
    return examples, list(chunks.values())


def read_index(path: Path) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if not path.exists():
        raise FileNotFoundError(f"chunk index does not exist: {path}")
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            required = {"chunk_id", "report_title", "source_file", "page", "text"}
            missing = sorted(required - set(item))
            if missing:
                raise ValueError(f"{path}:{line_number} is missing fields: {missing}")
            chunks.append({
                "chunk_id": str(item["chunk_id"]),
                "report_title": item["report_title"],
                "source_file": item["source_file"],
                "page": int(item["page"]),
                "text": str(item["text"]),
                "audit_retrieval_occurrences": [],
            })
    identifiers = [item["chunk_id"] for item in chunks]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"chunk index contains duplicate chunk_id values: {path}")
    return chunks


def prompt_payload(
    examples: list[dict[str, Any]], chunk: dict[str, Any]
) -> dict[str, Any]:
    return {
        "task": (
            "Extract every lesson in SOURCE_PASSAGE that could be relevant to "
            "the demonstrated navigation-decision input style in a compatible "
            "future state. Do not require applicability to a particular example."
        ),
        "target_decision_input_examples": examples,
        "source_passage": chunk,
    }


def request_body(
    examples: list[dict[str, Any]], chunk: dict[str, Any], model: str,
    effort: str | None, max_output_tokens: int,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    prompt_payload(examples, chunk), ensure_ascii=False, indent=2
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "maib_decision_style_lesson_extraction",
                "strict": True,
                "schema": ExtractionResponse.model_json_schema(mode="validation"),
            },
        },
        "max_completion_tokens": max_output_tokens,
    }
    if effort is not None:
        body["reasoning_effort"] = effort
    return body


def exact_substring(needle: str, source: str) -> bool:
    return bool(needle.strip()) and normalized(needle) in normalized(source)


OUTER_QUOTES = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))


def canonical_quote(value: str, source: str) -> str:
    candidate = value.strip()
    if exact_substring(candidate, source):
        return candidate
    for opening, closing in OUTER_QUOTES:
        if candidate.startswith(opening) and candidate.endswith(closing):
            inner = candidate[len(opening): len(candidate) - len(closing)].strip()
            if exact_substring(inner, source):
                return inner
    return candidate


def validate_response(
    response: ExtractionResponse, chunk: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if response.chunk_id != chunk["chunk_id"]:
        errors.append("response chunk_id does not match source passage")
    if response.contains_extractable_lessons:
        if not response.source_contains_investigator_reasoning:
            errors.append("extractable response is not investigator reasoning")
        if response.source_passage_type in {
            "background_or_method", "chronology", "quoted_rules_or_guidance", "unclear"
        }:
            errors.append("passage type cannot support extracted lessons")
        if not response.lessons:
            errors.append("extractable response contains no lessons")
    elif response.lessons:
        errors.append("non-extractable response contains lesson cards")

    fingerprints: set[str] = set()
    for lesson in response.lessons:
        if set(lesson.card.pages) != {chunk["page"]}:
            errors.append("lesson card cites a page other than the supplied page")
        for quote in lesson.card.supporting_quotes:
            quote.quote = canonical_quote(quote.quote, chunk["text"])
            if quote.page != chunk["page"]:
                errors.append("supporting quote cites a different page")
            if not exact_substring(quote.quote, chunk["text"]):
                errors.append(f"source quotation is not verbatim: {quote.quote!r}")
        fingerprint = normalized(lesson.card.investigator_finding).casefold()
        if fingerprint in fingerprints:
            errors.append("duplicate investigator finding within one response")
        fingerprints.add(fingerprint)
    return errors


def validate_key(value: str | None) -> str:
    key = (value or "").strip()
    if len(key) < 20 or key in {"...", "<key>", "<api-key>"} or "your-key" in key.lower():
        raise ValueError("OPENAI_API_KEY is missing or contains a placeholder")
    return key


def output_directory(base: Path) -> Path:
    return base / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_new(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing output directory: {path}")
    path.mkdir(parents=True)


def cost_estimate(
    requests: list[dict[str, Any]], model: str, pricing_mode: str = "standard"
) -> dict[str, Any]:
    input_tokens = sum(approximate_tokens(json.dumps(item)) for item in requests)
    maximum_output = sum(int(item["max_completion_tokens"]) for item in requests)
    rates = MODEL_PRICES.get(model, {}).get(pricing_mode)
    result: dict[str, Any] = {
        "requests": len(requests),
        "estimated_input_tokens": input_tokens,
        "maximum_output_tokens": maximum_output,
        "model": model,
        "pricing_mode": pricing_mode,
    }
    if rates:
        result.update({
            "estimated_input_cost_usd": input_tokens / 1_000_000 * rates[0],
            "maximum_output_cost_usd": maximum_output / 1_000_000 * rates[1],
            "maximum_total_cost_usd": (
                input_tokens / 1_000_000 * rates[0]
                + maximum_output / 1_000_000 * rates[1]
            ),
        })
    return result


def usage(completion: Any) -> dict[str, Any]:
    value = getattr(completion, "usage", None)
    return value.model_dump() if value is not None and hasattr(value, "model_dump") else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSONL file does not exist: {path}")
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            records.append(value)
    return records


def batch_record(chunk: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return {
        "custom_id": f"decision-style-{chunk['chunk_id']}",
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": body,
    }


def write_extracted_cards(
    output_dir: Path, results: list[dict[str, Any]]
) -> tuple[int, int]:
    accepted: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for result in results:
        chunk = result["chunk"]
        for ordinal, lesson in enumerate(result["extraction"]["lessons"], start=1):
            card = lesson["card"]
            fingerprint_input = (
                f"{chunk['source_file']}\n"
                f"{normalized(card['investigator_finding']).casefold()}"
            )
            fingerprint = hashlib.sha256(fingerprint_input.encode()).hexdigest()[:16]
            record = {
                "card_id": f"maib-style-lesson-{fingerprint}",
                "source_chunk_id": chunk["chunk_id"],
                "source_file": chunk["source_file"],
                "report_title": chunk["report_title"],
                "source_page": chunk["page"],
                "source_type": "published_investigation_lesson_card",
                "review_status": "machine_validated_needs_human_review",
                **card,
                "relevant_input_features": lesson["relevant_input_features"],
                "potential_decision_use": lesson["potential_decision_use"],
                "required_live_evidence": lesson["required_live_evidence"],
                "prohibited_inferences": lesson["prohibited_inferences"],
            }
            existing = seen.get(fingerprint_input)
            if existing is not None:
                duplicates.append({
                    "duplicate_of": existing,
                    "source_chunk_id": chunk["chunk_id"],
                    "ordinal": ordinal,
                    "card": record,
                })
            else:
                seen[fingerprint_input] = record["card_id"]
                accepted.append(record)
    write_jsonl(output_dir / "cards.machine_validated.jsonl", accepted)
    write_jsonl(output_dir / "cards.exact_duplicates.jsonl", duplicates)
    return len(accepted), len(duplicates)


def finalize_outputs(
    output_dir: Path,
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    model: str,
    audit_examples: int,
    total_passages: int,
    mode: str,
) -> dict[str, Any]:
    write_jsonl(output_dir / "results.jsonl", results)
    write_jsonl(output_dir / "failures.jsonl", failures)
    write_jsonl(output_dir / "attempts.jsonl", attempts)
    unique_cards, exact_duplicates = write_extracted_cards(output_dir, results)
    summary = {
        "model": model,
        "mode": mode,
        "audit_examples": audit_examples,
        "unique_passages": total_passages,
        "passages_validated": len(results),
        "passages_failed": len(failures),
        "passages_with_lessons": sum(
            bool(item["extraction"]["contains_extractable_lessons"])
            for item in results
        ),
        "lesson_cards_before_exact_deduplication": sum(
            len(item["extraction"]["lessons"]) for item in results
        ),
        "machine_validated_unique_cards": unique_cards,
        "exact_duplicate_cards": exact_duplicates,
        "output_dir": str(output_dir),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_report(output_dir / "REPORT.md", results, failures)
    return summary


def prepare_batch(
    output_dir: Path,
    examples: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    model: str,
) -> None:
    ensure_new(output_dir)
    records = [batch_record(chunk, body) for chunk, body in zip(chunks, requests)]
    manifests = [
        {"custom_id": record["custom_id"], "chunk": chunk}
        for record, chunk in zip(records, chunks)
    ]
    write_jsonl(output_dir / "batch_input.jsonl", records)
    write_jsonl(output_dir / "manifest.jsonl", manifests)
    (output_dir / "audit_examples.json").write_text(
        json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    estimate = cost_estimate(requests, model, pricing_mode="batch")
    (output_dir / "cost_estimate.json").write_text(json.dumps(estimate, indent=2) + "\n")
    print(json.dumps({
        "output_dir": str(output_dir),
        "audit_examples": len(examples),
        "all_chunks": len(chunks),
        **estimate,
    }, indent=2))
    print("batch prepared; no API request was submitted")


def write_report(path: Path, results: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    cards = [
        lesson
        for result in results
        for lesson in result["extraction"]["lessons"]
    ]
    type_counts = Counter(result["extraction"]["source_passage_type"] for result in results)
    lines = [
        "# MAIB decision-style extraction test",
        "",
        f"Passages validated: **{len(results)}**  ",
        f"Passages with extractable lessons: **{sum(bool(r['extraction']['contains_extractable_lessons']) for r in results)}**  ",
        f"Lesson cards extracted: **{len(cards)}**  ",
        f"Failures: **{len(failures)}**",
        "",
        "## Passage classifications",
        "",
        "| Type | Count |",
        "|---|---:|",
        *(f"| {name} | {count} |" for name, count in sorted(type_counts.items())),
        "",
        "## Extracted lessons",
        "",
        "| Chunk | Finding | Transferable lesson | Potential runtime use |",
        "|---|---|---|---|",
    ]
    for result in results:
        for lesson in result["extraction"]["lessons"]:
            card = lesson["card"]
            cells = [
                result["chunk"]["chunk_id"],
                card["investigator_finding"],
                card["transferable_lesson"],
                lesson["potential_decision_use"],
            ]
            lines.append("| " + " | ".join(str(x).replace("|", "\\|") for x in cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_batch(output_dir: Path, model: str) -> None:
    manifests = {
        item["custom_id"]: item["chunk"]
        for item in read_jsonl(output_dir / "manifest.jsonl")
    }
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    seen_outputs: set[str] = set()
    for record in read_jsonl(output_dir / "batch_output.jsonl"):
        identifier = str(record.get("custom_id") or "")
        chunk = manifests.get(identifier)
        if chunk is None:
            failures.append({"custom_id": identifier, "error": "missing manifest"})
            continue
        seen_outputs.add(identifier)
        content: str | None = None
        try:
            content = response_content(record)
            parsed = ExtractionResponse.model_validate_json(content)
            errors = validate_response(parsed, chunk)
            if errors:
                raise ValueError("; ".join(errors))
            body = record.get("response", {}).get("body", {})
            results.append({
                "chunk": chunk,
                "extraction": parsed.model_dump(),
                "api": {
                    "response_id": body.get("id"),
                    "model": body.get("model", model),
                    "usage": body.get("usage") or {},
                    "batch_custom_id": identifier,
                },
            })
            attempts.append({
                "chunk_id": chunk["chunk_id"],
                "batch_custom_id": identifier,
                "response_id": body.get("id"),
                "raw_content": content,
                "validation": "accepted",
            })
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures.append({
                "chunk_id": chunk["chunk_id"],
                "batch_custom_id": identifier,
                "error": error,
            })
            attempts.append({
                "chunk_id": chunk["chunk_id"],
                "batch_custom_id": identifier,
                "raw_content": content,
                "validation": "rejected",
                "error": error,
            })
    for identifier, chunk in manifests.items():
        if identifier not in seen_outputs:
            failures.append({
                "chunk_id": chunk["chunk_id"],
                "batch_custom_id": identifier,
                "error": "batch output missing for prepared request",
            })
    examples = json.loads((output_dir / "audit_examples.json").read_text())
    summary = finalize_outputs(
        output_dir, results, failures, attempts, model,
        len(examples), len(manifests), "batch_all_chunks",
    )
    print(json.dumps(summary, indent=2))
    if failures:
        raise RuntimeError(
            f"{len(failures)} passage(s) failed validation; inspect "
            f"{output_dir / 'failures.jsonl'}"
        )


def run(args: argparse.Namespace) -> None:
    examples, audit_chunks = collect_material(args.audit_root, list(DEFAULT_SAMPLES))
    chunks = read_index(args.index) if args.all_chunks else audit_chunks
    effort = resolve_reasoning_effort(args.model, args.reasoning_effort)
    requests = [
        request_body(examples, chunk, args.model, effort, args.max_output_tokens)
        for chunk in chunks
    ]
    out = args.output_dir or output_directory(args.output_base)
    if args.batch_prepare:
        prepare_batch(out, examples, chunks, requests, args.model)
        return
    if args.all_chunks:
        raise ValueError(
            "--all-chunks must be used with --batch-prepare; sending the full "
            "corpus synchronously is intentionally disabled"
        )
    ensure_new(out)
    prepared = [
        {"chunk": chunk, "request_body": body}
        for chunk, body in zip(chunks, requests)
    ]
    write_jsonl(out / "prepared_requests.jsonl", prepared)
    estimate = cost_estimate(requests, args.model)
    (out / "cost_estimate.json").write_text(json.dumps(estimate, indent=2) + "\n")
    print(json.dumps({"output_dir": str(out), "audit_examples": len(examples), "unique_chunks": len(chunks), **estimate}, indent=2))
    if args.dry_run:
        print("dry run complete; no API requests were sent")
        return

    from openai import OpenAI
    client = OpenAI(api_key=validate_key(os.environ.get("OPENAI_API_KEY")))
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for index, (chunk, body) in enumerate(zip(chunks, requests), start=1):
        print(f"[{index}/{len(chunks)}] {chunk['chunk_id']}", flush=True)
        last_error = ""
        for attempt in range(1, args.attempts + 1):
            completion: Any | None = None
            content: str | None = None
            try:
                completion = client.chat.completions.create(**body)
                content = completion.choices[0].message.content
                if not content:
                    raise ValueError("API response contained no content")
                parsed = ExtractionResponse.model_validate_json(content)
                errors = validate_response(parsed, chunk)
                if errors:
                    raise ValueError("; ".join(errors))
                attempts.append({
                    "chunk_id": chunk["chunk_id"], "attempt": attempt,
                    "response_id": getattr(completion, "id", None),
                    "raw_content": content, "validation": "accepted",
                })
                results.append({
                    "chunk": chunk,
                    "extraction": parsed.model_dump(),
                    "api": {
                        "response_id": getattr(completion, "id", None),
                        "model": getattr(completion, "model", args.model),
                        "usage": usage(completion),
                        "attempt": attempt,
                    },
                })
                last_error = ""
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                attempts.append({
                    "chunk_id": chunk["chunk_id"], "attempt": attempt,
                    "response_id": getattr(completion, "id", None) if completion else None,
                    "raw_content": content, "validation": "rejected", "error": last_error,
                })
                if attempt < args.attempts:
                    time.sleep(min(2 ** attempt, 8))
        if last_error:
            failures.append({
                "chunk_id": chunk["chunk_id"], "error": last_error,
                "attempts": args.attempts,
            })

    summary = finalize_outputs(
        out, results, failures, attempts, args.model,
        len(examples), len(chunks), "direct_test",
    )
    print(json.dumps(summary, indent=2))
    if failures:
        raise RuntimeError(f"{len(failures)} passage(s) failed; inspect {out / 'failures.jsonl'}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--audit-root", type=Path, default=DEFAULT_AUDIT_ROOT)
    result.add_argument("--index", type=Path, default=Path("accidents/index.jsonl"))
    result.add_argument("--model", default="gpt-6.1-sol")
    result.add_argument("--reasoning-effort", default="low")
    result.add_argument("--max-output-tokens", type=int, default=5000)
    result.add_argument("--attempts", type=int, default=2)
    result.add_argument(
        "--output-base", type=Path,
        default=Path("reports/maib_decision_style_extraction"),
    )
    result.add_argument("--output-dir", type=Path)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--all-chunks", action="store_true")
    result.add_argument("--batch-prepare", action="store_true")
    result.add_argument("--validate-batch", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.validate_batch:
            if args.output_dir is None:
                raise ValueError("--validate-batch requires --output-dir")
            validate_batch(args.output_dir, args.model)
        else:
            run(args)
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
