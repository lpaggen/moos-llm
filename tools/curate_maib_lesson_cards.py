#!/usr/bin/env python3
"""Semantically curate extracted MAIB cards for the Captain decision interface.

Preparation is local-only. It groups cards by source report so the model can compare
potentially redundant lessons, then writes an OpenAI Batch input and provenance
manifest. Validation preserves the source extraction and writes separate retained,
review, and excluded artifacts with a decision record for every input card.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from tools.build_maib_lesson_cards import (
        MODEL_PRICES,
        approximate_tokens,
        read_jsonl,
        resolve_reasoning_effort,
        response_content,
        write_jsonl,
    )
except ModuleNotFoundError:
    from build_maib_lesson_cards import (
        MODEL_PRICES,
        approximate_tokens,
        read_jsonl,
        resolve_reasoning_effort,
        response_content,
        write_jsonl,
    )


DEFAULT_CARDS = Path(
    "accidents/cards_decision_style_report_level/cards.machine_validated.jsonl"
)
DEFAULT_WORK_DIR = Path("accidents/cards_decision_style_curated")


SYSTEM_PROMPT = """You are curating source-grounded MAIB lesson cards for a runtime Captain critic that evaluates an ongoing PRE-COLLISION navigation decision.

The runtime can observe measured vessel motion, relative bearing, range, closing speed, CPA/TCPA, trends, track quality and uncertainty, visibility, multiple contacts, current manoeuvre or control state, and an OOW proposal. It does not independently observe crew alcohol use, fatigue, competence, training history, maintenance quality, organisational policy, or post-collision damage unless those facts are explicitly present in a future runtime input.

Review every supplied card independently, while comparing cards from the same report for semantic duplication.

Decision relevance:
- direct: the finding can directly inform detection or appraisal of collision risk, interpretation of the available navigation evidence, or evaluation of an OOW's current pre-collision action proposal using the runtime inputs above.
- adjacent: a defensible maritime lesson, but mainly about staffing, training, equipment maintenance, organisational policy, passage preparation, tug/tow/berthing operations, emergency preparedness, or another matter that the current runtime usually cannot observe or act on.
- out_of_scope: post-collision response, rescue, damage control, unrelated occupational or engineering safety, or material that does not meaningfully inform this navigation decision interface.

Grounding quality:
- strong: the exact quotations explicitly support the investigator finding and preserve uncertainty or causal modality.
- partial: the quotations support only part of a composite finding or the lesson adds a material claim not established by them.
- weak: the finding or lesson is generic, speculative beyond the quotations, or principally a quoted rule rather than investigator reasoning.

Transfer quality:
- safe: operational implication is cautious, conditional, non-prescriptive, and respects the non-transfer conditions.
- review: potentially useful but broader, more prescriptive, or less conditional than the source warrants.
- reject: unsafe or materially unsupported transfer, prescribed manoeuvre, or accident-outcome analogy presented as prediction.

Set duplicate_of only when an earlier supplied card expresses materially the same investigator finding, causal mechanism, and operational lesson. Shared topic alone is not duplication. Use the exact earlier card_id or null.

Do not reward polished wording. Judge source support and fit to the stated runtime interface. Return one assessment for every supplied card, in the same order, without adding or omitting identifiers."""


class CardAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(min_length=1, max_length=128)
    decision_relevance: Literal["direct", "adjacent", "out_of_scope"]
    grounding_quality: Literal["strong", "partial", "weak"]
    transfer_quality: Literal["safe", "review", "reject"]
    duplicate_of: str | None
    reason: str = Field(min_length=20, max_length=500)


class CurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[CardAssessment] = Field(min_length=1)


def group_cards(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    reports: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identifiers: set[str] = set()
    for card in cards:
        identifier = str(card.get("card_id") or "")
        source = str(card.get("source_file") or "")
        if not identifier or not source:
            raise ValueError("every card requires card_id and source_file")
        if identifier in identifiers:
            raise ValueError(f"duplicate card_id in input: {identifier}")
        identifiers.add(identifier)
        reports[source].append(card)
    return dict(sorted(reports.items()))


def compact_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": card["card_id"],
        "pages": card["pages"],
        "encounter_phases": card["encounter_phases"],
        "warning_signs": card["warning_signs"],
        "investigator_finding": card["investigator_finding"],
        "transferable_lesson": card["transferable_lesson"],
        "non_transfer_conditions": card["non_transfer_conditions"],
        "supporting_quotes": card["supporting_quotes"],
    }


def request_record(
    source: str,
    cards: list[dict[str, Any]],
    model: str,
    effort: str | None,
    max_output_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    user_payload = {
        "source_file": source,
        "instruction": "Assess every card. Preserve order. duplicate_of may reference only an earlier card in this array.",
        "cards": [compact_card(card) for card in cards],
    }
    schema = CurationResponse.model_json_schema(mode="validation")
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "maib_card_curation",
                "strict": True,
                "schema": schema,
            },
        },
        "max_completion_tokens": max_output_tokens,
    }
    if effort is not None:
        body["reasoning_effort"] = effort
    custom_id = f"maib-curate-{Path(source).stem}"
    request = {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": body,
    }
    manifest = {
        "custom_id": custom_id,
        "source_file": source,
        "cards": cards,
    }
    return request, manifest


def prepare(args: argparse.Namespace) -> None:
    if args.work_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing directory: {args.work_dir}")
    cards = read_jsonl(args.cards)
    reports = group_cards(cards)
    effort = resolve_reasoning_effort(args.model, args.reasoning_effort)
    requests: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    estimated_input_tokens = 0
    for source, report_cards in reports.items():
        request, manifest = request_record(
            source, report_cards, args.model, effort, args.max_output_tokens
        )
        requests.append(request)
        manifests.append(manifest)
        estimated_input_tokens += approximate_tokens(json.dumps(request["body"]))
    args.work_dir.mkdir(parents=True)
    write_jsonl(args.work_dir / "batch_input.jsonl", requests)
    write_jsonl(args.work_dir / "manifest.jsonl", manifests)
    rates = MODEL_PRICES.get(args.model, {}).get("batch")
    maximum_output_tokens = len(requests) * args.max_output_tokens
    summary: dict[str, Any] = {
        "model": args.model,
        "requests": len(requests),
        "source_cards": len(cards),
        "source_reports": len(reports),
        "estimated_input_tokens": estimated_input_tokens,
        "maximum_output_tokens": maximum_output_tokens,
        "pricing_mode": "batch",
    }
    if rates:
        summary.update({
            "estimated_input_cost_usd": estimated_input_tokens / 1_000_000 * rates[0],
            "maximum_output_cost_usd": maximum_output_tokens / 1_000_000 * rates[1],
            "maximum_total_cost_usd": (
                estimated_input_tokens / 1_000_000 * rates[0]
                + maximum_output_tokens / 1_000_000 * rates[1]
            ),
        })
    (args.work_dir / "cost_estimate.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    print("curation Batch prepared; no API request was submitted")


def disposition(assessment: CardAssessment) -> str:
    if assessment.duplicate_of is not None:
        return "exclude"
    if (
        assessment.decision_relevance == "direct"
        and assessment.grounding_quality == "strong"
        and assessment.transfer_quality == "safe"
    ):
        return "retain"
    if (
        assessment.decision_relevance == "out_of_scope"
        or assessment.grounding_quality == "weak"
        or assessment.transfer_quality == "reject"
    ):
        return "exclude"
    return "review"


def normalized_pages(card: dict[str, Any]) -> tuple[dict[str, Any], list[int]]:
    quote_pages = sorted({int(item["page"]) for item in card["supporting_quotes"]})
    declared = {int(value) for value in card["pages"]}
    removed = sorted(declared - set(quote_pages))
    result = dict(card)
    result["pages"] = quote_pages
    return result, removed


def validate(args: argparse.Namespace) -> None:
    manifests = {
        item["custom_id"]: item
        for item in read_jsonl(args.work_dir / "manifest.jsonl")
    }
    output_path = getattr(args, "batch_output", None) or (args.work_dir / "batch_output.jsonl")
    outputs = read_jsonl(output_path)
    seen_outputs: set[str] = set()
    ignored_outputs: list[str] = []
    retained: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for output in outputs:
        identifier = str(output.get("custom_id") or "")
        manifest = manifests.get(identifier)
        if manifest is None:
            if getattr(args, "allow_unrelated_output", False):
                ignored_outputs.append(identifier)
                continue
            failures.append({"custom_id": identifier, "error": "missing manifest"})
            continue
        if identifier in seen_outputs:
            failures.append({"custom_id": identifier, "error": "duplicate Batch response"})
            continue
        seen_outputs.add(identifier)
        cards = manifest["cards"]
        expected_ids = [card["card_id"] for card in cards]
        try:
            parsed = CurationResponse.model_validate_json(response_content(output))
            returned_ids = [item.card_id for item in parsed.assessments]
            if returned_ids != expected_ids:
                raise ValueError("assessment identifiers or order differ from manifest")
            earlier: set[str] = set()
            for card, assessment in zip(cards, parsed.assessments):
                if assessment.duplicate_of is not None:
                    if assessment.duplicate_of not in earlier:
                        raise ValueError(
                            f"{assessment.card_id} duplicate_of must name an earlier card"
                        )
                earlier.add(assessment.card_id)
                cleaned, removed_pages = normalized_pages(card)
                action = disposition(assessment)
                decision = {
                    **assessment.model_dump(),
                    "source_file": manifest["source_file"],
                    "disposition": action,
                    "removed_unquoted_pages": removed_pages,
                }
                decisions.append(decision)
                if action == "retain":
                    retained.append(cleaned)
                elif action == "review":
                    review.append({"card": cleaned, "curation": decision})
                else:
                    excluded.append({"card": cleaned, "curation": decision})
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            failures.append({
                "custom_id": identifier,
                "source_file": manifest["source_file"],
                "error": str(exc),
            })

    for identifier, manifest in manifests.items():
        if identifier not in seen_outputs:
            failures.append({
                "custom_id": identifier,
                "source_file": manifest["source_file"],
                "error": "missing Batch response",
            })

    write_jsonl(args.work_dir / "curation_decisions.jsonl", decisions)
    write_jsonl(args.work_dir / "cards.retained.jsonl", retained)
    write_jsonl(args.work_dir / "cards.review.jsonl", review)
    write_jsonl(args.work_dir / "cards.excluded.jsonl", excluded)
    write_jsonl(args.work_dir / "curation_failures.jsonl", failures)
    summary = {
        "source_reports": len(manifests),
        "source_cards": sum(len(item["cards"]) for item in manifests.values()),
        "assessed_cards": len(decisions),
        "retained_cards": len(retained),
        "review_cards": len(review),
        "excluded_cards": len(excluded),
        "semantic_duplicates_excluded": sum(
            item["duplicate_of"] is not None for item in decisions
        ),
        "cards_with_unquoted_pages_removed": sum(
            bool(item["removed_unquoted_pages"]) for item in decisions
        ),
        "decision_relevance": dict(Counter(
            item["decision_relevance"] for item in decisions
        )),
        "grounding_quality": dict(Counter(
            item["grounding_quality"] for item in decisions
        )),
        "transfer_quality": dict(Counter(
            item["transfer_quality"] for item in decisions
        )),
        "failed_report_responses": len(failures),
        "batch_output_path": str(output_path),
        "ignored_unrelated_responses": ignored_outputs,
    }
    (args.work_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    if failures:
        raise RuntimeError(
            f"{len(failures)} report response(s) failed; inspect curation_failures.jsonl"
        )


def audit(args: argparse.Namespace) -> None:
    manifests = read_jsonl(args.work_dir / "manifest.jsonl")
    source_cards = [card for item in manifests for card in item["cards"]]
    decisions = read_jsonl(args.work_dir / "curation_decisions.jsonl")
    retained = read_jsonl(args.work_dir / "cards.retained.jsonl")
    review = read_jsonl(args.work_dir / "cards.review.jsonl")
    excluded = read_jsonl(args.work_dir / "cards.excluded.jsonl")
    failures = read_jsonl(args.work_dir / "curation_failures.jsonl")
    expected_ids = {item["card_id"] for item in source_cards}
    decision_ids = [item["card_id"] for item in decisions]
    retained_ids = [item["card_id"] for item in retained]
    review_ids = [item["card"]["card_id"] for item in review]
    excluded_ids = [item["card"]["card_id"] for item in excluded]
    partition_ids = retained_ids + review_ids + excluded_ids
    problems: list[str] = []
    if failures:
        problems.append(f"{len(failures)} failed report responses")
    if len(decision_ids) != len(set(decision_ids)):
        problems.append("duplicate card identifiers in decisions")
    if set(decision_ids) != expected_ids:
        problems.append("decision identifiers do not cover the source cards exactly")
    if len(partition_ids) != len(set(partition_ids)):
        problems.append("card identifiers overlap across disposition outputs")
    if set(partition_ids) != expected_ids:
        problems.append("disposition outputs do not partition the source cards exactly")
    for card in retained:
        quote_pages = {int(item["page"]) for item in card["supporting_quotes"]}
        if {int(value) for value in card["pages"]} != quote_pages:
            problems.append(f"retained card has an unquoted page: {card['card_id']}")
    summary = {
        "source_cards": len(source_cards),
        "decisions": len(decisions),
        "retained": len(retained),
        "review": len(review),
        "excluded": len(excluded),
        "failed_report_responses": len(failures),
        "problems": problems,
        "audit_passed": not problems,
    }
    (args.work_dir / "audit.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    if problems:
        raise RuntimeError("curated corpus audit failed")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="command", required=True)

    prepare_parser = subs.add_parser("prepare")
    prepare_parser.add_argument("--cards", type=Path, default=DEFAULT_CARDS)
    prepare_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    prepare_parser.add_argument("--model", default="gpt-6.1-sol")
    prepare_parser.add_argument("--reasoning-effort", default="low")
    prepare_parser.add_argument("--max-output-tokens", type=int, default=8000)
    prepare_parser.set_defaults(function=prepare)

    validate_parser = subs.add_parser("validate")
    validate_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    validate_parser.add_argument("--batch-output", type=Path)
    validate_parser.add_argument("--allow-unrelated-output", action="store_true")
    validate_parser.set_defaults(function=validate)

    audit_parser = subs.add_parser("audit")
    audit_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    audit_parser.set_defaults(function=audit)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        args.function(args)
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
