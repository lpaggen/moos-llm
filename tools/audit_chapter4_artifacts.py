#!/usr/bin/env python3
"""Reproduce the Chapter 4 artifact counts without modifying source artifacts.

The default audit covers JSON/JSONL artifacts and frozen simulation scores.  Pass
``--include-pdf-pages`` to reopen every local PDF and reproduce the page counts;
that optional check is slower and may expose non-fatal pypdf repair warnings.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
from pathlib import Path
import re
import statistics
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def read_json(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def read_jsonl(path: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def usage(rows: list[dict[str, Any]], prefix: str | None = None) -> dict[str, int]:
    selected = rows
    if prefix is not None:
        selected = [row for row in rows if str(row.get("custom_id", "")).startswith(prefix)]
    values = [row["response"]["body"].get("usage", {}) for row in selected]
    return {
        "responses": len(selected),
        "prompt_tokens": sum(int(value.get("prompt_tokens") or 0) for value in values),
        "completion_tokens": sum(int(value.get("completion_tokens") or 0) for value in values),
        "total_tokens": sum(int(value.get("total_tokens") or 0) for value in values),
    }


def card_summary(cards: list[dict[str, Any]]) -> dict[str, int]:
    report_pages = {
        (str(card["source_file"]), int(quote["page"]))
        for card in cards
        for quote in card.get("supporting_quotes", [])
    }
    return {
        "cards": len(cards),
        "reports": len({str(card["source_file"]) for card in cards}),
        "quotes": sum(len(card.get("supporting_quotes", [])) for card in cards),
        "report_pages": len(report_pages),
    }


def unquoted_pages(cards: list[dict[str, Any]]) -> tuple[int, int]:
    affected = claims = 0
    for card in cards:
        quote_pages = {int(quote["page"]) for quote in card["supporting_quotes"]}
        extras = {int(page) for page in card["pages"]} - quote_pages
        affected += bool(extras)
        claims += len(extras)
    return affected, claims


def optional_pdf_summary(indexed_pages: set[tuple[str, int]]) -> dict[str, Any]:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    rows: list[tuple[str, int, int, int]] = []
    failures: list[str] = []
    pdfs = sorted((ROOT / "accidents").glob("*.pdf"))
    for path in pdfs:
        relative = str(path.relative_to(ROOT))
        try:
            reader = PdfReader(path)
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                words = len(re.sub(r"\s+", " ", text).strip().split())
                rows.append((relative, page_number, len(text.strip()), words))
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append(f"{relative}: {exc}")
    character_eligible = [row for row in rows if row[2] >= 80]
    eligible_without_chunk = [
        row for row in character_eligible if (row[0], row[1]) not in indexed_pages
    ]
    return {
        "pdfs": len(pdfs),
        "pages": len(rows),
        "pages_at_least_80_characters": len(character_eligible),
        "pages_with_chunks": len(indexed_pages),
        "character_eligible_without_chunk": len(eligible_without_chunk),
        "all_character_eligible_omissions_under_80_words": all(
            row[3] < 80 for row in eligible_without_chunk
        ),
        "open_or_page_failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-pdf-pages", action="store_true")
    args = parser.parse_args()

    index = read_jsonl("accidents/index.jsonl")
    indexed_pages = {(str(row["source_file"]), int(row["page"])) for row in index}
    word_counts = [len(str(row["text"]).split()) for row in index]
    chunks_per_report = Counter(str(row["source_file"]) for row in index)

    initial_cards = read_jsonl(
        "accidents/cards_decision_style_report_level/cards.machine_validated.jsonl"
    )
    extraction_manifest = read_jsonl(
        "accidents/cards_decision_style_report_level/manifest.jsonl"
    )
    manifest_chunk_ids = [
        str(chunk["chunk_id"])
        for report in extraction_manifest
        for chunk in report["selected_chunks"]
    ]
    initial_rejections = read_jsonl(
        "accidents/cards_decision_style_report_level/cards.rejected.jsonl"
    )
    retry_cards = read_jsonl(
        "accidents/cards_decision_style_report_level_retry_pdf127/cards.machine_validated.jsonl"
    )
    retry_rejections = read_jsonl(
        "accidents/cards_decision_style_report_level_retry_pdf127/cards.rejected.jsonl"
    )
    curation_manifest = read_jsonl("accidents/cards_decision_style_curated/manifest.jsonl")
    curation_source_cards = [card for group in curation_manifest for card in group["cards"]]
    decisions = read_jsonl("accidents/cards_decision_style_curated/curation_decisions.jsonl")
    retained = read_jsonl("accidents/cards_decision_style_curated/cards.retained.jsonl")
    review = read_jsonl("accidents/cards_decision_style_curated/cards.review.jsonl")
    excluded = read_jsonl("accidents/cards_decision_style_curated/cards.excluded.jsonl")

    initial_output = read_jsonl("accidents/cards_decision_style_report_level/batch_output.jsonl")
    combined_output = read_jsonl(
        "reports/maib_extraction_pilot/batch_6af9cb13553081908595ed7f9b27fd6d_output.jsonl"
    )

    affected, extra_claims = unquoted_pages(curation_source_cards)
    result: dict[str, Any] = {
        "index": {
            "reports": len(chunks_per_report),
            "chunks": len(index),
            "unique_chunk_ids": len({str(row["chunk_id"]) for row in index}),
            "pages_with_chunks": len(indexed_pages),
            "mean_chunk_words": statistics.fmean(word_counts),
            "median_chunk_words": statistics.median(word_counts),
            "median_chunks_per_report": statistics.median(chunks_per_report.values()),
            "chunk_range_per_report": [min(chunks_per_report.values()), max(chunks_per_report.values())],
        },
        "extraction_manifest": {
            "reports": len(extraction_manifest),
            "chunk_references": len(manifest_chunk_ids),
            "unique_chunk_ids": len(set(manifest_chunk_ids)),
            "matches_index_ids": set(manifest_chunk_ids)
            == {str(row["chunk_id"]) for row in index},
        },
        "initial_extraction": {
            **card_summary(initial_cards),
            "candidate_rejections": sum("ordinal" in row for row in initial_rejections),
            "report_response_failures": sum("ordinal" not in row for row in initial_rejections),
            "raw_candidates": len(initial_cards) + sum("ordinal" in row for row in initial_rejections),
            "cards_with_unquoted_pages": unquoted_pages(initial_cards)[0],
            "usage": usage(initial_output),
        },
        "retry": {
            **card_summary(retry_cards),
            "candidate_rejections": sum("ordinal" in row for row in retry_rejections),
            "raw_candidates": len(retry_cards) + sum("ordinal" in row for row in retry_rejections),
            "usage": usage(combined_output, "maib-card-"),
        },
        "curation": {
            "source_reports": len(curation_manifest),
            "source_cards": len(curation_source_cards),
            "decisions": len(decisions),
            "retained": len(retained),
            "review": len(review),
            "excluded": len(excluded),
            "partition_unique_ids": len({
                *[row["card_id"] for row in retained],
                *[row["card"]["card_id"] for row in review],
                *[row["card"]["card_id"] for row in excluded],
            }),
            "pre_correction_cards_with_unquoted_pages": affected,
            "pre_correction_unquoted_page_claims": extra_claims,
            "semantic_duplicates_labelled": sum(row.get("duplicate_of") is not None for row in decisions),
            "usage": usage(combined_output, "maib-curate-"),
        },
        "retained_corpus": {
            **card_summary(retained),
            "cards_with_unquoted_pages": unquoted_pages(retained)[0],
        },
        "retrieval": read_json("reports/maib_extraction_pilot/retrieval_metrics.json"),
        "simulation": {
            "raw_passage_pilot": read_json(
                "reports/imazu_frozen_v1/maib_rag_independent_model_only_warp5/scores.json"
            )["summary"],
            "lesson_card_pilot": read_json(
                "reports/imazu_frozen_v1/maib_lesson_cards_captain_review_prompt_fixed_full_warp5/scores.json"
            )["summary"],
            "lockstep_base": read_json(
                "logs/evaluations/imazu/base_narrative_lockstep_30s_warp20/scores_v2/scores.json"
            )["summary"],
            "lockstep_raw_maib_critic": read_json(
                "logs/evaluations/imazu/maib_rag_captain_critic_lockstep_30s_warp20/scores_v2/scores.json"
            )["summary"],
        },
    }
    if args.include_pdf_pages:
        result["pdfs"] = optional_pdf_summary(indexed_pages)

    expected = {
        "reports": 107,
        "chunks": 2373,
        "pages_with_chunks": 1622,
        "manifest_reports": 107,
        "manifest_chunk_references": 2373,
        "manifest_unique_chunk_ids": 2373,
        "manifest_matches_index_ids": True,
        "initial_cards": 998,
        "initial_rejections": 17,
        "initial_cards_with_unquoted_pages": 0,
        "retry_cards": 2,
        "retry_rejections": 1,
        "curation_cards": 998,
        "retained": 503,
        "review": 462,
        "excluded": 33,
        "retained_quotes": 1008,
        "retained_reports": 101,
        "retained_cards_with_unquoted_pages": 0,
    }
    actual = {
        "reports": result["index"]["reports"],
        "chunks": result["index"]["chunks"],
        "pages_with_chunks": result["index"]["pages_with_chunks"],
        "manifest_reports": result["extraction_manifest"]["reports"],
        "manifest_chunk_references": result["extraction_manifest"]["chunk_references"],
        "manifest_unique_chunk_ids": result["extraction_manifest"]["unique_chunk_ids"],
        "manifest_matches_index_ids": result["extraction_manifest"]["matches_index_ids"],
        "initial_cards": result["initial_extraction"]["cards"],
        "initial_rejections": result["initial_extraction"]["candidate_rejections"],
        "initial_cards_with_unquoted_pages": result["initial_extraction"]["cards_with_unquoted_pages"],
        "retry_cards": result["retry"]["cards"],
        "retry_rejections": result["retry"]["candidate_rejections"],
        "curation_cards": result["curation"]["source_cards"],
        "retained": result["curation"]["retained"],
        "review": result["curation"]["review"],
        "excluded": result["curation"]["excluded"],
        "retained_quotes": result["retained_corpus"]["quotes"],
        "retained_reports": result["retained_corpus"]["reports"],
        "retained_cards_with_unquoted_pages": result["retained_corpus"]["cards_with_unquoted_pages"],
    }
    mismatches = {key: {"expected": expected[key], "actual": value} for key, value in actual.items() if value != expected[key]}
    result["acceptance"] = {"passed": not mismatches, "mismatches": mismatches}
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
