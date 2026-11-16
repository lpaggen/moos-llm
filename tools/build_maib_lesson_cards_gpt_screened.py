#!/usr/bin/env python3
"""Build MAIB lesson cards from complete report-level sets of indexed passages.

The Chapter 4 path is ``prepare-generate`` with ``accidents/index.jsonl`` as its
input.  It contains no lexical relevance score and no fixed passage-count limit:
every indexed passage from a report is supplied in that report's extraction
request.  The older screening commands remain available for provenance of
development runs but are not part of the frozen experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from tools.build_maib_lesson_cards import (
        DEFAULT_INDEX,
        DEFAULT_MODEL,
        LessonCardCandidate,
        approximate_tokens,
        cost_summary,
        group_reports,
        normalized,
        read_jsonl,
        resolve_prices,
        resolve_reasoning_effort,
        response_content,
        write_jsonl,
    )
except ModuleNotFoundError:
    # Support direct execution as ``python tools/<script>.py`` as well as
    # module execution from the repository root.
    from build_maib_lesson_cards import (
        DEFAULT_INDEX,
        DEFAULT_MODEL,
        LessonCardCandidate,
        approximate_tokens,
        cost_summary,
        group_reports,
        normalized,
        read_jsonl,
        resolve_prices,
        resolve_reasoning_effort,
        response_content,
        write_jsonl,
    )


SCREEN_SYSTEM_PROMPT = """You screen passages from one published marine accident investigation report for a later source-grounded lesson-card extraction stage.

Review every supplied passage. Retain a passage when it contains evidence that may support an investigator finding or transferable lesson about collision avoidance, lookout, situational awareness, bridge-team coordination, risk assessment, delayed or ineffective action, vessel control, or a causal limitation relevant to those subjects.

Retain supporting factual context when it is needed to interpret investigator analysis. Exclude administrative boilerplate, contents pages, generic vessel particulars, unrelated injuries or equipment details, raw chronology with no decision or causal relevance, and quoted regulations that are not connected to the investigation's reasoning.

Prefer recall when uncertain: retain a passage if excluding it could remove material needed to interpret a relevant finding. Do not generate lesson cards in this stage. Select only supplied chunk identifiers and give a short evidence-based reason for each retained passage.
"""


class SelectedPassage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=10, max_length=400)


class PassageScreenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_has_relevant_evidence: bool
    assessment: str = Field(min_length=10, max_length=800)
    selected_passages: list[SelectedPassage]


class AllLessonCardsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[LessonCardCandidate]


GENERATION_SYSTEM_PROMPT = """You extract all distinct source-grounded collision-avoidance lessons supported by the supplied passages from a published marine accident investigation report.

The downstream decision-input style includes measured vessel motion, relative bearing, range, closing speed, CPA/TCPA, trends, track quality, visibility, multiple contacts, current control state, and an OOW proposal. Extract a supported lesson when it could inform this class of decision in some compatible future state. Do not require it to apply to one particular example encounter.

Return an empty cards array if the supplied passages do not explicitly support a relevant investigator finding.

Rules:
- Use only the supplied report passages.
- Return every distinct supported lesson; do not impose an arbitrary card-count target.
- Every finding and lesson must be supported by at least one exact quotation copied from a supplied page.
- Keep each quotation short and preserve its wording exactly.
- Prefer investigator analysis, conclusions, safety issues, and causal factors.
- Do not invent COLREG roles, rule numbers, geometry, or recommended manoeuvres.
- Do not recommend a particular heading, speed, or course alteration.
- Do not treat the accident outcome as proof that another encounter will end the same way.
- A transferable lesson must be a concise operational implication of the cited investigator finding, not a new factual claim.
- State when the analogy would not transfer.
"""


def custom_id(prefix: str, source: str) -> str:
    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def request_with_schema(
    identifier: str,
    model: str,
    system_prompt: str,
    user_text: str,
    schema_name: str,
    schema: dict[str, Any],
    max_output_tokens: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
        "max_completion_tokens": max_output_tokens,
    }
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return {"custom_id": identifier, "method": "POST", "url": "/v1/chat/completions", "body": body}


def passage_text(source: str, chunks: list[dict[str, Any]]) -> str:
    title = str(chunks[0].get("report_title") or Path(source).stem)
    sections = [
        f"REPORT TITLE: {title}",
        f"LOCAL SOURCE IDENTIFIER: {source}",
        f"PASSAGES TO SCREEN: {len(chunks)}",
    ]
    for item in chunks:
        sections.append(
            f"--- PAGE {int(item.get('page') or 0)} / CHUNK {item.get('chunk_id')} ---\n"
            f"{str(item.get('text') or '').strip()}"
        )
    return "\n\n".join(sections)


def write_cost(
    work_dir: Path,
    requests: list[dict[str, Any]],
    input_tokens: int,
    model: str,
    max_output_tokens: int,
    pricing_mode: str,
    input_rate: float | None,
    output_rate: float | None,
    method_label: str,
) -> None:
    rates = resolve_prices(model, pricing_mode, input_rate, output_rate)
    summary = cost_summary(len(requests), input_tokens, max_output_tokens, *rates)
    summary.update({
        "model": model,
        "pricing_mode": pricing_mode,
        "input_rate_per_million": rates[0],
        "output_rate_per_million": rates[1],
        "selection_method": method_label,
    })
    (work_dir / "cost_estimate.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


def prepare_screen(args: argparse.Namespace) -> None:
    reports = group_reports(read_jsonl(args.index))
    if args.report_limit is not None:
        reports = dict(list(reports.items())[: args.report_limit])
    effort = resolve_reasoning_effort(args.model, args.reasoning_effort)
    schema = PassageScreenResponse.model_json_schema(mode="validation")
    requests: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    input_tokens = 0
    for source, chunks in reports.items():
        user_text = passage_text(source, chunks)
        identifier = custom_id("maib-screen", source)
        request = request_with_schema(
            identifier, args.model, SCREEN_SYSTEM_PROMPT, user_text,
            "maib_passage_screen", schema, args.max_output_tokens, effort,
        )
        requests.append(request)
        manifests.append({
            "custom_id": identifier,
            "source_file": source,
            "report_title": str(chunks[0].get("report_title") or Path(source).stem),
            "all_chunks": chunks,
        })
        input_tokens += approximate_tokens(
            SCREEN_SYSTEM_PROMPT + user_text + json.dumps(request["body"]["response_format"])
        )
    args.work_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.work_dir / "batch_input.jsonl", requests)
    write_jsonl(args.work_dir / "manifest.jsonl", manifests)
    write_cost(
        args.work_dir, requests, input_tokens, args.model, args.max_output_tokens,
        args.pricing_mode, args.input_rate, args.output_rate,
        "GPT screening of every indexed passage; no lexical score or passage-count cap",
    )


def validate_screen(args: argparse.Namespace) -> None:
    manifests = {row["custom_id"]: row for row in read_jsonl(args.work_dir / "manifest.jsonl")}
    selected_index: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for output in read_jsonl(args.work_dir / "batch_output.jsonl"):
        identifier = str(output.get("custom_id") or "")
        manifest = manifests.get(identifier)
        if manifest is None:
            failures.append({"custom_id": identifier, "error": "missing manifest"})
            continue
        try:
            parsed = PassageScreenResponse.model_validate_json(response_content(output))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            failures.append({"custom_id": identifier, "error": str(exc)})
            continue
        chunks = {str(row.get("chunk_id")): row for row in manifest["all_chunks"]}
        selected_ids = [item.chunk_id for item in parsed.selected_passages]
        unknown = sorted(set(selected_ids) - set(chunks))
        duplicates = sorted({value for value in selected_ids if selected_ids.count(value) > 1})
        if unknown or duplicates:
            failures.append({
                "custom_id": identifier,
                "error": "invalid selected chunk identifiers",
                "unknown": unknown,
                "duplicates": duplicates,
            })
            continue
        if parsed.report_has_relevant_evidence != bool(selected_ids):
            failures.append({"custom_id": identifier, "error": "evidence flag disagrees with selection"})
            continue
        reasons = {item.chunk_id: item.reason for item in parsed.selected_passages}
        selected_set = set(selected_ids)
        selected_index.extend(chunks[value] for value in selected_ids)
        audits.append({
            "custom_id": identifier,
            "source_file": manifest["source_file"],
            "report_title": manifest["report_title"],
            "report_has_relevant_evidence": parsed.report_has_relevant_evidence,
            "assessment": parsed.assessment,
            "input_chunk_count": len(chunks),
            "selected_chunk_count": len(selected_ids),
            "selected_chunks": [
                {"chunk_id": value, "page": int(chunks[value].get("page") or 0), "reason": reasons[value]}
                for value in selected_ids
            ],
            "rejected_chunk_ids": sorted(set(chunks) - selected_set),
        })
    write_jsonl(args.output_index, selected_index)
    write_jsonl(args.output_audit, audits)
    write_jsonl(args.work_dir / "screening_failures.jsonl", failures)
    print(json.dumps({
        "reports_validated": len(audits),
        "reports_failed": len(failures),
        "selected_passages": len(selected_index),
        "output_index": str(args.output_index),
    }, indent=2))
    if failures:
        raise ValueError("screening contains failed report responses; do not continue to generation")


def generation_payload(source: str, chunks: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    title = str(chunks[0].get("report_title") or Path(source).stem)
    sections = [
        f"REPORT TITLE: {title}",
        f"LOCAL SOURCE IDENTIFIER: {source}",
        "The passages below are the supplied indexed text for this report. Screen them semantically and extract only supported lessons.",
    ]
    for item in chunks:
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
                "selection_source": "supplied_report_passage",
            }
            for item in chunks
        ],
    }
    return "\n\n".join(sections), manifest


def prepare_generate(args: argparse.Namespace) -> None:
    reports = group_reports(read_jsonl(args.screened_index))
    if args.source_file:
        reports = {
            source: chunks for source, chunks in reports.items()
            if source == args.source_file
        }
        if not reports:
            raise ValueError(
                f"source file {args.source_file!r} is absent from {args.screened_index}"
            )
    effort = resolve_reasoning_effort(args.model, args.reasoning_effort)
    schema = AllLessonCardsResponse.model_json_schema(mode="validation")
    requests: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    input_tokens = 0
    for source, chunks in reports.items():
        user_text, manifest = generation_payload(source, chunks)
        identifier = custom_id("maib-card", source)
        request = request_with_schema(
            identifier, args.model, GENERATION_SYSTEM_PROMPT, user_text,
            "maib_lesson_cards", schema, args.max_output_tokens, effort,
        )
        manifest["custom_id"] = identifier
        requests.append(request)
        manifests.append(manifest)
        input_tokens += approximate_tokens(
            GENERATION_SYSTEM_PROMPT + user_text + json.dumps(request["body"]["response_format"])
        )
    args.work_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.work_dir / "batch_input.jsonl", requests)
    write_jsonl(args.work_dir / "manifest.jsonl", manifests)
    write_cost(
        args.work_dir, requests, input_tokens, args.model, args.max_output_tokens,
        args.pricing_mode, args.input_rate, args.output_rate,
        "GPT extraction from every indexed passage grouped by report; no lexical score or passage-count cap",
    )


def quote_supported(quote: Any, manifest: dict[str, Any]) -> bool:
    text = " ".join(
        str(chunk.get("text") or "")
        for chunk in manifest["selected_chunks"]
        if int(chunk.get("page") or 0) == quote.page
    )
    return bool(text) and normalized(quote.quote) in normalized(text)


def validate_generate(args: argparse.Namespace) -> None:
    manifests = {row["custom_id"]: row for row in read_jsonl(args.work_dir / "manifest.jsonl")}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    output_path = getattr(args, "batch_output", None) or (args.work_dir / "batch_output.jsonl")
    all_outputs = read_jsonl(output_path)
    ignored_output_ids: list[str] = []
    if getattr(args, "allow_unrelated_output", False):
        outputs = []
        for output in all_outputs:
            identifier = str(output.get("custom_id") or "")
            if identifier in manifests:
                outputs.append(output)
            else:
                ignored_output_ids.append(identifier)
    else:
        outputs = all_outputs
    response_ids: list[str] = []
    raw_candidates = 0
    prompt_tokens = 0
    completion_tokens = 0
    for output in outputs:
        identifier = str(output.get("custom_id") or "")
        response_ids.append(identifier)
        manifest = manifests.get(identifier)
        if manifest is None:
            rejected.append({"custom_id": identifier, "error": "missing manifest"})
            continue
        usage = ((output.get("response") or {}).get("body") or {}).get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            parsed = AllLessonCardsResponse.model_validate_json(response_content(output))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            choice = (((output.get("response") or {}).get("body") or {}).get("choices") or [{}])[0]
            rejected.append({
                "custom_id": identifier,
                "source_file": manifest["source_file"],
                "error": str(exc),
                "finish_reason": choice.get("finish_reason"),
            })
            continue
        raw_candidates += len(parsed.cards)
        pages = {int(row["page"]) for row in manifest["selected_chunks"]}
        for ordinal, candidate in enumerate(parsed.cards, start=1):
            problems: list[str] = []
            if not set(candidate.pages).issubset(pages):
                problems.append("card cites a page not supplied after GPT screening")
            unsupported = [q for q in candidate.supporting_quotes if not quote_supported(q, manifest)]
            if unsupported:
                problems.append(f"{len(unsupported)} quotation(s) not found on claimed page")
            fingerprint = hashlib.sha256(
                f"{manifest['source_file']}\n{normalized(candidate.investigator_finding)}".encode()
            ).hexdigest()[:16]
            card_id = f"maib-lesson-{fingerprint}"
            if card_id in seen:
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
                # A generated page can describe useful context without providing a
                # quotation.  The exported provenance must be narrower: every page
                # it declares is therefore derived from a quotation that has just
                # passed the exact, page-bounded source check above.
                record["pages"] = sorted({quote["page"] for quote in record["supporting_quotes"]})
                accepted.append(record)
                seen.add(card_id)
    write_jsonl(args.work_dir / "cards.machine_validated.jsonl", accepted)
    write_jsonl(args.work_dir / "cards.rejected.jsonl", rejected)
    manifest_ids = set(manifests)
    duplicate_response_ids = sorted({value for value in response_ids if response_ids.count(value) > 1})
    missing_response_ids = sorted(manifest_ids - set(response_ids))
    unknown_response_ids = sorted(set(response_ids) - manifest_ids)
    response_failures = [row for row in rejected if "ordinal" not in row]
    candidate_rejections = [row for row in rejected if "ordinal" in row]
    reports_with_cards = len({row["source_file"] for row in accepted})
    reports_with_empty_card_arrays = len(manifests) - reports_with_cards - len(response_failures)
    http_200_responses = sum(
        int(((row.get("response") or {}).get("status_code") or 0) == 200)
        for row in outputs
    )
    length_limited = [row for row in response_failures if row.get("finish_reason") == "length"]
    cost_path = args.work_dir / "cost_estimate.json"
    cost = json.loads(cost_path.read_text(encoding="utf-8")) if cost_path.exists() else {}
    input_rate = cost.get("input_rate_per_million")
    output_rate = cost.get("output_rate_per_million")
    implied_cost = None
    if input_rate is not None and output_rate is not None:
        implied_cost = (
            prompt_tokens * float(input_rate) / 1_000_000
            + completion_tokens * float(output_rate) / 1_000_000
        )
    summary = {
        "prepared_reports": len(manifests),
        "batch_responses": len(outputs),
        "unique_batch_responses": len(set(response_ids)),
        "http_200_responses": http_200_responses,
        "batch_error_responses": sum(int(row.get("error") is not None) for row in outputs),
        "duplicate_response_ids": duplicate_response_ids,
        "missing_response_ids": missing_response_ids,
        "unknown_response_ids": unknown_response_ids,
        "batch_output_path": str(output_path),
        "ignored_unrelated_response_ids": ignored_output_ids,
        "parseable_report_responses": len(outputs) - len(response_failures),
        "response_failures": len(response_failures),
        "length_limited_report_responses": len(length_limited),
        "length_limited_source_files": [row.get("source_file") for row in length_limited],
        "reports_with_accepted_cards": reports_with_cards,
        "reports_with_empty_card_arrays": reports_with_empty_card_arrays,
        "raw_candidates": raw_candidates,
        "machine_validated_cards": len(accepted),
        "rejected_candidates": len(candidate_rejections),
        "accepted_supporting_quotes": sum(len(row["supporting_quotes"]) for row in accepted),
        "cards_with_unquoted_declared_pages": sum(
            bool(set(row["pages"]) - {quote["page"] for quote in row["supporting_quotes"]})
            for row in accepted
        ),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "configured_batch_input_rate_usd_per_million": input_rate,
        "configured_batch_output_rate_usd_per_million": output_rate,
        "implied_cost_usd": implied_cost,
        "status": (
            "invalid_response_coverage"
            if duplicate_response_ids or missing_response_ids or unknown_response_ids
            else "complete_with_report_failures"
            if response_failures
            else "complete"
        ),
    }
    (args.work_dir / "validation_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def add_common_prepare(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--model", default=DEFAULT_MODEL)
    sub.add_argument("--max-output-tokens", type=int, default=12000)
    sub.add_argument("--reasoning-effort", default="auto")
    sub.add_argument("--pricing-mode", choices=("standard", "batch"), default="batch")
    sub.add_argument("--input-rate", type=float)
    sub.add_argument("--output-rate", type=float)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="command", required=True)

    screen = subs.add_parser("prepare-screen")
    screen.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    screen.add_argument("--work-dir", type=Path, default=Path("accidents/cards_gpt_screened/screening"))
    screen.add_argument("--report-limit", type=int)
    add_common_prepare(screen)
    screen.set_defaults(function=prepare_screen)

    validate_s = subs.add_parser("validate-screen")
    validate_s.add_argument("--work-dir", type=Path, default=Path("accidents/cards_gpt_screened/screening"))
    validate_s.add_argument("--output-index", type=Path, default=Path("accidents/cards_gpt_screened/screened_index.jsonl"))
    validate_s.add_argument("--output-audit", type=Path, default=Path("accidents/cards_gpt_screened/screening_audit.jsonl"))
    validate_s.set_defaults(function=validate_screen)

    generate = subs.add_parser("prepare-generate")
    generate.add_argument(
        "--index", "--screened-index", dest="screened_index", type=Path,
        default=Path("accidents/index.jsonl"),
        help="complete page-bounded JSONL index (the old --screened-index name remains an alias)",
    )
    generate.add_argument("--work-dir", type=Path, default=Path("accidents/cards_gpt_screened/generation"))
    generate.add_argument("--source-file", help="prepare only one exact source_file value")
    add_common_prepare(generate)
    generate.set_defaults(function=prepare_generate)

    validate_g = subs.add_parser("validate-generate")
    validate_g.add_argument("--work-dir", type=Path, default=Path("accidents/cards_gpt_screened/generation"))
    validate_g.add_argument("--batch-output", type=Path)
    validate_g.add_argument("--allow-unrelated-output", action="store_true")
    validate_g.set_defaults(function=validate_generate)
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
