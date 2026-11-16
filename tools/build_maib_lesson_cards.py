#!/usr/bin/env python3
"""Extract auditable MAIB lesson cards with the OpenAI Batch API.

The default commands are local-only. ``prepare`` writes a Batch API request
file and provenance manifest; only ``submit`` sends data or incurs API cost.
``validate`` accepts cards only when every cited quotation can be found on the
claimed page in the local MAIB text index.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


DEFAULT_INDEX = Path("accidents/index.jsonl")
DEFAULT_WORK_DIR = Path("accidents/cards")
DEFAULT_MODEL = "gpt-6.1-terra"
PRICING_AS_OF = "2026-11-02"

# USD per million tokens. Batch is 50% of Standard for these models as of the
# date above. CLI rate overrides keep estimates usable when prices change.
MODEL_PRICES: dict[str, dict[str, tuple[float, float]]] = {
    "gpt-6-luna": {"standard": (0.10, 0.50), "batch": (0.05, 0.25)},
    "gpt-6.1-terra": {"standard": (1.00, 5.00), "batch": (0.50, 2.50)},
    "gpt-6.1-sol": {"standard": (2.00, 10.00), "batch": (1.00, 5.00)},
}

# Keep this deliberately narrow. The API rejects an unsupported value for the
# entire Batch request rather than silently choosing a compatible effort.
MODEL_REASONING_EFFORTS: dict[str, set[str]] = {
    "gpt-6-luna": {"none", "low", "medium", "high", "xhigh"},
    "gpt-6.1-terra": {"none", "low", "medium", "high", "xhigh"},
    "gpt-6.1-sol": {"low", "medium", "high", "xhigh"},
}
MODEL_DEFAULT_REASONING_EFFORT: dict[str, str] = {
    "gpt-6-luna": "none",
    "gpt-6.1-terra": "none",
    "gpt-6.1-sol": "low",
}

ANALYSIS_MARKERS = (
    "analysis", "conclusion", "safety issues", "causal factor",
    "contributory factor", "the investigation found", "the maib found",
    "lessons learned", "actions taken", "recommendation",
)
NAVIGATION_MARKERS = (
    "collision avoidance", "risk of collision", "close quarters", "closest point",
    " cpa ", " tcpa ", "crossing vessel", "head-on", "head on", "overtaking",
    "give-way", "give way", "stand-on", "stand on", "altered course",
    "course alteration", "bridge team", "watchkeeper", "lookout",
)

SYSTEM_PROMPT = """You extract source-grounded collision-avoidance lessons from a published marine accident investigation report.

Return zero to three lesson cards. Return zero cards if the supplied passages do not explicitly support a relevant investigator finding.

Rules:
- Use only the supplied report passages.
- Every finding and lesson must be supported by at least one exact quotation copied from a supplied page.
- Keep each quotation short and preserve its wording exactly.
- Prefer investigator analysis, conclusions, safety issues, and causal factors.
- Do not invent COLREG roles, rule numbers, geometry, or recommended manoeuvres.
- Do not recommend a particular heading, speed, or course alteration.
- Do not treat the accident outcome as proof that another encounter will end the same way.
- A transferable lesson must be a concise operational implication of the cited investigator finding, not a new factual claim.
- State when the analogy would not transfer.
"""


Encounter = Literal[
    "crossing", "head_on", "overtaking", "stationary_contact", "multi_contact",
    "restricted_visibility", "unknown",
]
Role = Literal["give_way", "stand_on", "overtaking_vessel", "uncertain", "not_applicable"]
Phase = Literal["monitoring", "risk_developing", "action_required", "emergency", "recovery"]
Mechanism = Literal[
    "delayed_action", "ineffective_action", "poor_monitoring",
    "rigid_stand_on_interpretation", "role_confusion", "repeated_course_changes",
    "inadequate_cpa_assessment", "poor_team_coordination", "distraction", "other",
]


class SupportingQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    quote: str = Field(min_length=8, max_length=700)


class LessonCardCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[int] = Field(min_length=1, max_length=4)
    encounter_types: list[Encounter] = Field(min_length=1, max_length=4)
    roles: list[Role] = Field(min_length=1, max_length=4)
    encounter_phases: list[Phase] = Field(min_length=1, max_length=5)
    warning_signs: list[str] = Field(min_length=1, max_length=6)
    causal_mechanisms: list[Mechanism] = Field(min_length=1, max_length=5)
    investigator_finding: str = Field(min_length=20, max_length=800)
    transferable_lesson: str = Field(min_length=20, max_length=600)
    non_transfer_conditions: list[str] = Field(min_length=1, max_length=5)
    supporting_quotes: list[SupportingQuote] = Field(min_length=1, max_length=3)


class LessonCardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[LessonCardCandidate] = Field(max_length=3)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def group_reports(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    reports: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        source = str(record.get("source_file") or "").strip()
        text = str(record.get("text") or "").strip()
        if source and text:
            reports[source].append(record)
    for chunks in reports.values():
        chunks.sort(key=lambda item: (int(item.get("page") or 0), str(item.get("chunk_id") or "")))
    return dict(sorted(reports.items()))


def passage_score(text: str) -> int:
    haystack = f" {text.lower()} "
    score = 0
    score += 5 * sum(marker in haystack for marker in ANALYSIS_MARKERS)
    score += 3 * sum(marker in haystack for marker in NAVIGATION_MARKERS)
    score += min(haystack.count("collision"), 3)
    return score


def select_passages(chunks: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    """Select analytical/navigation passages and retain deterministic ordering."""
    ranked = sorted(
        chunks,
        key=lambda item: (
            -passage_score(str(item.get("text") or "")),
            int(item.get("page") or 0),
            str(item.get("chunk_id") or ""),
        ),
    )
    selected = [item for item in ranked if passage_score(str(item.get("text") or "")) > 0][:maximum]
    if not selected:
        selected = ranked[: min(maximum, 2)]
    return sorted(selected, key=lambda item: (int(item.get("page") or 0), str(item.get("chunk_id") or "")))


def report_payload(source: str, chunks: list[dict[str, Any]], max_passages: int) -> tuple[str, dict[str, Any]]:
    selected = select_passages(chunks, max_passages)
    title = str(chunks[0].get("report_title") or Path(source).stem)
    sections = [
        f"REPORT TITLE: {title}",
        f"LOCAL SOURCE IDENTIFIER: {source}",
        "Only the passages below are available. Output an empty cards array when they do not support a collision-avoidance lesson.",
    ]
    for item in selected:
        sections.append(
            f"--- PAGE {int(item.get('page') or 0)} / CHUNK {item.get('chunk_id')} ---\n"
            f"{str(item.get('text') or '').strip()}"
        )
    manifest = {
        "source_file": source,
        "report_title": title,
        "selected_chunks": [
            {
                "chunk_id": item.get("chunk_id"),
                "page": int(item.get("page") or 0),
                "text": str(item.get("text") or ""),
                "selection_score": passage_score(str(item.get("text") or "")),
            }
            for item in selected
        ],
    }
    return "\n\n".join(sections), manifest


def approximate_tokens(text: str) -> int:
    """Conservative English-text estimate; actual billing comes from API usage."""
    return math.ceil(len(text.encode("utf-8")) / 3.7)


def custom_id(source: str) -> str:
    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
    return f"maib-card-{digest}"


def resolve_reasoning_effort(model: str, requested: str) -> str | None:
    if requested == "omit":
        return None
    effort = MODEL_DEFAULT_REASONING_EFFORT.get(model) if requested == "auto" else requested
    if effort is None:
        raise ValueError(
            f"no automatic reasoning effort is known for {model!r}; use --reasoning-effort omit or an explicit value"
        )
    supported = MODEL_REASONING_EFFORTS.get(model)
    if supported is not None and effort not in supported:
        raise ValueError(
            f"{model} does not support reasoning_effort={effort!r}; supported values: {sorted(supported)}"
        )
    return effort


def request_record(
    identifier: str,
    model: str,
    user_text: str,
    max_output_tokens: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    schema = LessonCardResponse.model_json_schema(mode="validation")
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "maib_lesson_cards",
                "strict": True,
                "schema": schema,
            },
        },
        "max_completion_tokens": max_output_tokens,
    }
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return {
        "custom_id": identifier,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": body,
    }


def build_requests(
    index: Path,
    model: str,
    max_passages: int,
    max_output_tokens: int,
    reasoning_effort: str = "auto",
    report_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    reports = group_reports(read_jsonl(index))
    if report_limit is not None:
        reports = dict(list(reports.items())[:report_limit])
    requests: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    input_tokens = 0
    resolved_effort = resolve_reasoning_effort(model, reasoning_effort)
    for source, chunks in reports.items():
        user_text, manifest = report_payload(source, chunks, max_passages)
        identifier = custom_id(source)
        request = request_record(identifier, model, user_text, max_output_tokens, resolved_effort)
        input_tokens += approximate_tokens(SYSTEM_PROMPT + user_text + json.dumps(request["body"]["response_format"]))
        manifest["custom_id"] = identifier
        manifests.append(manifest)
        requests.append(request)
    return requests, manifests, input_tokens


def resolve_prices(model: str, mode: str, input_rate: float | None, output_rate: float | None) -> tuple[float, float]:
    if input_rate is not None and output_rate is not None:
        return input_rate, output_rate
    known = MODEL_PRICES.get(model, {}).get(mode)
    if known is None:
        raise ValueError(
            f"no built-in {mode} price for {model!r}; provide both --input-rate and --output-rate"
        )
    return known


def cost_summary(
    request_count: int,
    input_tokens: int,
    max_output_tokens: int,
    input_rate: float,
    output_rate: float,
) -> dict[str, float | int]:
    expected_output_tokens = request_count * min(max_output_tokens, 550)
    maximum_output_tokens = request_count * max_output_tokens
    input_cost = input_tokens / 1_000_000 * input_rate
    return {
        "requests": request_count,
        "estimated_input_tokens": input_tokens,
        "expected_output_tokens": expected_output_tokens,
        "maximum_output_tokens": maximum_output_tokens,
        "estimated_input_cost_usd": input_cost,
        "expected_total_cost_usd": input_cost + expected_output_tokens / 1_000_000 * output_rate,
        "maximum_total_cost_usd": input_cost + maximum_output_tokens / 1_000_000 * output_rate,
    }


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def prepare(args: argparse.Namespace) -> None:
    requests, manifests, input_tokens = build_requests(
        args.index,
        args.model,
        args.max_passages,
        args.max_output_tokens,
        args.reasoning_effort,
        args.report_limit,
    )
    input_rate, output_rate = resolve_prices(
        args.model, args.pricing_mode, args.input_rate, args.output_rate
    )
    summary = cost_summary(
        len(requests), input_tokens, args.max_output_tokens, input_rate, output_rate
    )
    summary.update({
        "model": args.model,
        "pricing_mode": args.pricing_mode,
        "pricing_as_of": PRICING_AS_OF,
        "input_rate_per_million": input_rate,
        "output_rate_per_million": output_rate,
        "reasoning_effort": resolve_reasoning_effort(args.model, args.reasoning_effort),
        "token_estimate_method": "UTF-8 bytes / 3.7; actual API usage may differ",
    })
    print(json.dumps(summary, indent=2))
    if args.command == "prepare":
        write_jsonl(args.work_dir / "batch_input.jsonl", requests)
        write_jsonl(args.work_dir / "manifest.jsonl", manifests)
        (args.work_dir / "cost_estimate.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.work_dir / 'batch_input.jsonl'}")
        print(f"wrote {args.work_dir / 'manifest.jsonl'}")


def openai_client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI
    return OpenAI()


def submit(args: argparse.Namespace) -> None:
    batch_input = args.work_dir / "batch_input.jsonl"
    if not batch_input.exists():
        raise FileNotFoundError(f"run prepare first: {batch_input} does not exist")
    validate_prepared_requests(batch_input)
    client = openai_client()
    with batch_input.open("rb") as stream:
        uploaded = client.files.create(file=stream, purpose="batch")
    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={"purpose": "maib_lesson_card_extraction"},
    )
    state = {"batch_id": batch.id, "input_file_id": uploaded.id, "status": batch.status}
    (args.work_dir / "batch_job.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(state, indent=2))


def validate_prepared_requests(path: Path) -> None:
    """Fail locally on known model/parameter incompatibilities before upload."""
    records = read_jsonl(path)
    if not records:
        raise ValueError(f"prepared batch is empty: {path}")
    identifiers: set[str] = set()
    for line_number, record in enumerate(records, start=1):
        identifier = str(record.get("custom_id") or "")
        if not identifier or identifier in identifiers:
            raise ValueError(f"missing or duplicate custom_id at {path}:{line_number}")
        identifiers.add(identifier)
        body = record.get("body")
        if not isinstance(body, dict):
            raise ValueError(f"missing request body at {path}:{line_number}")
        model = str(body.get("model") or "")
        effort = body.get("reasoning_effort")
        if effort is not None:
            supported = MODEL_REASONING_EFFORTS.get(model)
            if supported is not None and effort not in supported:
                raise ValueError(
                    f"invalid reasoning_effort={effort!r} for {model} at {path}:{line_number}; "
                    f"supported values: {sorted(supported)}. Run prepare again."
                )
        schema = body.get("response_format", {}).get("json_schema", {})
        if schema.get("strict") is not True:
            raise ValueError(f"strict structured output missing at {path}:{line_number}")


def batch_id_from_args(args: argparse.Namespace) -> str:
    if args.batch_id:
        return args.batch_id
    state_path = args.work_dir / "batch_job.json"
    if not state_path.exists():
        raise FileNotFoundError("provide --batch-id or run submit first")
    return str(json.loads(state_path.read_text(encoding="utf-8"))["batch_id"])


def status(args: argparse.Namespace) -> None:
    batch = openai_client().batches.retrieve(batch_id_from_args(args))
    print(batch.model_dump_json(indent=2))


def download(args: argparse.Namespace) -> None:
    client = openai_client()
    batch = client.batches.retrieve(batch_id_from_args(args))
    if not batch.output_file_id:
        raise RuntimeError(f"batch has no output file; current status={batch.status}")
    output = args.work_dir / "batch_output.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    client.files.content(batch.output_file_id).write_to_file(output)
    if batch.error_file_id:
        client.files.content(batch.error_file_id).write_to_file(args.work_dir / "batch_errors.jsonl")
    print(f"wrote {output}")


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return re.sub(r"\s+", " ", text).strip()


def quote_is_supported(quote: SupportingQuote, manifest: dict[str, Any]) -> bool:
    page_text = " ".join(
        str(chunk.get("text") or "")
        for chunk in manifest.get("selected_chunks", [])
        if int(chunk.get("page") or 0) == quote.page
    )
    return bool(page_text) and normalized(quote.quote) in normalized(page_text)


def response_content(record: dict[str, Any]) -> str:
    if record.get("error"):
        raise ValueError(f"batch request failed: {record['error']}")
    body = record.get("response", {}).get("body", {})
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("batch response contains no choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("batch response contains no message content")
    return content


def validate(args: argparse.Namespace) -> None:
    manifests = {item["custom_id"]: item for item in read_jsonl(args.work_dir / "manifest.jsonl")}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_cards: set[str] = set()
    for result in read_jsonl(args.work_dir / "batch_output.jsonl"):
        identifier = str(result.get("custom_id") or "")
        manifest = manifests.get(identifier)
        if manifest is None:
            rejected.append({"custom_id": identifier, "error": "missing manifest"})
            continue
        try:
            parsed = LessonCardResponse.model_validate_json(response_content(result))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            rejected.append({"custom_id": identifier, "error": str(exc)})
            continue
        selected_pages = {int(chunk["page"]) for chunk in manifest["selected_chunks"]}
        for ordinal, candidate in enumerate(parsed.cards, start=1):
            problems: list[str] = []
            if not set(candidate.pages).issubset(selected_pages):
                problems.append("card cites a page that was not supplied")
            unsupported = [quote for quote in candidate.supporting_quotes if not quote_is_supported(quote, manifest)]
            if unsupported:
                problems.append(f"{len(unsupported)} supporting quote(s) not found verbatim on the claimed page")
            fingerprint = hashlib.sha256(
                f"{manifest['source_file']}\n{normalized(candidate.investigator_finding)}".encode()
            ).hexdigest()[:16]
            card_id = f"maib-lesson-{fingerprint}"
            if card_id in seen_cards:
                problems.append("duplicate card")
            record = {
                "card_id": card_id,
                "report_title": manifest["report_title"],
                "source_file": manifest["source_file"],
                "source_type": "published_investigation",
                "review_status": "machine_validated_needs_human_review",
                **candidate.model_dump(),
            }
            if problems:
                rejected.append({"custom_id": identifier, "ordinal": ordinal, "problems": problems, "candidate": record})
            else:
                accepted.append(record)
                seen_cards.add(card_id)
    write_jsonl(args.work_dir / "cards.machine_validated.jsonl", accepted)
    write_jsonl(args.work_dir / "cards.rejected.jsonl", rejected)
    print(json.dumps({"accepted": len(accepted), "rejected": len(rejected)}, indent=2))
    print("Accepted cards still require human review before use in an evaluation corpus.")


def card_retrieval_text(card: dict[str, Any]) -> str:
    """Render a validated lesson card as one self-contained RAG document."""
    lines = [
        "MAIB INVESTIGATION LESSON CARD",
        f"Encounter types: {', '.join(card['encounter_types'])}",
        f"Vessel roles: {', '.join(card['roles'])}",
        f"Encounter phases: {', '.join(card['encounter_phases'])}",
        f"Causal mechanisms: {', '.join(card['causal_mechanisms'])}",
        "Warning signs:",
        *(f"- {item}" for item in card["warning_signs"]),
        f"Investigator finding: {card['investigator_finding']}",
        f"Transferable lesson: {card['transferable_lesson']}",
        "Do not transfer this analogy when:",
        *(f"- {item}" for item in card["non_transfer_conditions"]),
        "Source quotations:",
        *(f"- Page {item['page']}: {item['quote']}" for item in card["supporting_quotes"]),
    ]
    return "\n".join(lines)


def export_index(args: argparse.Namespace) -> None:
    """Convert machine-validated cards into the runtime retriever schema."""
    cards = read_jsonl(args.cards)
    if not cards:
        raise ValueError(f"validated card file is empty: {args.cards}")
    records: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for line_number, card in enumerate(cards, start=1):
        identifier = str(card.get("card_id") or "").strip()
        if not identifier or identifier in identifiers:
            raise ValueError(
                f"missing or duplicate card_id at {args.cards}:{line_number}"
            )
        if card.get("review_status") != "machine_validated_needs_human_review":
            raise ValueError(
                f"card is not machine validated at {args.cards}:{line_number}"
            )
        # Revalidate the controlled card fields before publishing a runtime
        # index, even when the input came from this tool's validate command.
        candidate = LessonCardCandidate.model_validate({
            key: card[key]
            for key in LessonCardCandidate.model_fields
        })
        identifiers.add(identifier)
        pages = sorted(set(candidate.pages))
        records.append({
            "chunk_id": identifier,
            "report_title": str(card.get("report_title") or "Untitled MAIB report"),
            "source_file": str(card.get("source_file") or ""),
            "page": pages[0] if pages else None,
            "pages": pages,
            "source_type": "published_investigation_lesson_card",
            "review_status": card["review_status"],
            "encounter_types": candidate.encounter_types,
            "roles": candidate.roles,
            "encounter_phases": candidate.encounter_phases,
            "causal_mechanisms": candidate.causal_mechanisms,
            "text": card_retrieval_text(card),
        })
    write_jsonl(args.output, records)
    print(json.dumps({
        "cards": len(records),
        "output": str(args.output),
        "unique_sources": len({item["source_file"] for item in records}),
    }, indent=2))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)

    for name in ("estimate", "prepare"):
        sub = subparsers.add_parser(name, help=f"{name} extraction requests without calling the API")
        sub.add_argument("--index", type=Path, default=DEFAULT_INDEX)
        sub.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
        sub.add_argument("--model", default=DEFAULT_MODEL)
        sub.add_argument("--max-passages", type=int, default=8)
        sub.add_argument("--max-output-tokens", type=int, default=1600)
        sub.add_argument(
            "--reasoning-effort",
            choices=("auto", "omit", "none", "low", "medium", "high", "xhigh"),
            default="auto",
            help="auto selects a model-compatible value (Sol uses low)",
        )
        sub.add_argument("--report-limit", type=int)
        sub.add_argument("--pricing-mode", choices=("standard", "batch"), default="batch")
        sub.add_argument("--input-rate", type=float, help="USD per million input tokens")
        sub.add_argument("--output-rate", type=float, help="USD per million output tokens")
        sub.set_defaults(function=prepare)

    submit_parser = subparsers.add_parser("submit", help="upload the prepared file and create a billable batch")
    submit_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    submit_parser.set_defaults(function=submit)

    for name, function in (("status", status), ("download", download)):
        sub = subparsers.add_parser(name)
        sub.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
        sub.add_argument("--batch-id")
        sub.set_defaults(function=function)

    validate_parser = subparsers.add_parser("validate", help="validate downloaded cards and source quotations")
    validate_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    validate_parser.set_defaults(function=validate)

    export_parser = subparsers.add_parser(
        "export-index",
        help="convert machine-validated cards into the runtime RAG index",
    )
    export_parser.add_argument(
        "--cards",
        type=Path,
        default=DEFAULT_WORK_DIR / "cards.machine_validated.jsonl",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_WORK_DIR / "index.jsonl",
    )
    export_parser.set_defaults(function=export_index)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        args.function(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
