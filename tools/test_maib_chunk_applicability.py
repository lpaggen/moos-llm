#!/usr/bin/env python3
"""Test whether MAIB chunks retrieved in recent audits are useful to live states.

The experiment deliberately evaluates one audit state and one retrieved chunk
per API request.  GPT must first decide whether the single chunk contains a
source-grounded lesson that transfers to explicit facts in the live narrative.
Only then may it extract a lesson card.  Local validation rejects invented
quotes, invented live facts, and cards returned for non-applicable passages.

This script never modifies the source audits or the production MAIB indexes.
Each run writes a new, auditable result directory.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
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
        resolve_reasoning_effort,
        write_jsonl,
    )
except ModuleNotFoundError:
    from build_maib_lesson_cards import (
        LessonCardCandidate,
        MODEL_PRICES,
        approximate_tokens,
        normalized,
        resolve_reasoning_effort,
        write_jsonl,
    )


DEFAULT_AUDIT_ROOT = Path(
    "logs/evaluations/imazu/maib_rag_captain_critic_lockstep_30s_warp20"
)

# These are real, completed cycles from the latest evidence-bearing MAIB run.
# Together they cover head-on, crossing, overtaking, one-contact, multi-contact,
# monitoring, risk-developing, and action-required states.  Expected answers
# are intentionally not encoded: the purpose is to measure GPT's judgment.
DEFAULT_SAMPLES: tuple[tuple[str, int], ...] = (
    ("case_01", 44),
    ("case_02", 47),
    ("case_13", 55),
    ("case_16", 81),
    ("case_20", 74),
)

SYSTEM_PROMPT = """You are evaluating whether one passage from a published marine-accident investigation can be used meaningfully as advisory evidence for one live navigation decision state.

Apply a strict evidence standard:
1. Judge only the supplied passage. Do not use outside knowledge or another part of the report.
2. A passage is meaningful only when it contains an investigator finding, causal analysis, conclusion, or explicit safety issue AND an analogous mechanism is supported by explicit facts in the live narrative.
3. Shared words, vessel types, small CPA, or a broadly similar accident topic are not enough.
4. Do not infer unobserved distraction, lookout failure, radar use, fatigue, visibility, intentions, vessel limitations, or bridge-team behaviour.
5. Treat submarine-only sensing, ranging, doctrine, and manoeuvres as materially different unless the passage states a genuinely transferable causal mechanism that is also explicit in the live state.
6. Live geometry is authoritative. The passage cannot establish COLREG roles, encounter geometry, or a manoeuvre for the live state.
7. Do not copy an accident manoeuvre as a recommendation and do not predict the live outcome from the accident outcome.
8. Cite exact, verbatim excerpts from both the live narrative and the source passage. In quotation fields, return only the copied text: do not add surrounding quotation marks unless those marks occur in the source itself. Do not repair OCR or silently paraphrase quotations.
9. If the passage cannot be used meaningfully for this state, set can_be_used_meaningfully to false, use_in_decision_prompt to false, decision_usefulness to none, and lesson_card to null.
10. If it can be used meaningfully, extract exactly one lesson card supported by this passage alone. Its source quotations must be exact substrings of the passage and its page must equal the supplied source page. State the material differences and non-transfer conditions explicitly.

This is an evidence-applicability test, not a request to make or imitate a helm decision.
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
DecisionUsefulness = Literal["material", "supporting", "none"]


class ApplicabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=128)
    source_passage_type: PassageType
    source_contains_investigator_reasoning: bool
    can_be_used_meaningfully: bool
    confidence: float = Field(ge=0.0, le=1.0)
    source_assessment: str = Field(min_length=20, max_length=1200)
    narrative_evidence: list[str] = Field(max_length=5)
    analogous_facts: list[str] = Field(max_length=6)
    material_differences: list[str] = Field(min_length=1, max_length=8)
    decision_usefulness: DecisionUsefulness
    decision_impact: str = Field(min_length=4, max_length=1000)
    use_in_decision_prompt: bool
    lesson_card: LessonCardCandidate | None


def read_audit_cycle(path: Path, cycle_id: int) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"audit does not exist: {path}")
    events: dict[str, dict[str, Any]] = {}
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
    required = {"oow_prompt_input", "oow_decision", "maib_retrieval", "captain_decision"}
    missing = sorted(required - set(events))
    if missing:
        raise ValueError(f"{path} cycle {cycle_id} is missing events: {missing}")
    return events


def build_pairs(
    audit_root: Path, samples: list[tuple[str, int]], max_pairs: int | None = None
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for case_name, cycle_id in samples:
        audit_path = audit_root / case_name / "audit.jsonl"
        events = read_audit_cycle(audit_path, cycle_id)
        prompt = events["oow_prompt_input"]
        retrieval = events["maib_retrieval"]
        prior_captain = events["captain_decision"]
        results = retrieval.get("results") or []
        if not results:
            raise ValueError(f"{case_name} cycle {cycle_id} has no retrieved chunks")
        for rank, chunk in enumerate(results, start=1):
            chunk_id = str(chunk.get("chunk_id") or "")
            pair_id = f"{case_name}-cycle_{cycle_id}-{chunk_id}"
            pairs.append({
                "pair_id": pair_id,
                "audit_reference": {
                    "audit_path": str(audit_path),
                    "case": case_name,
                    "cycle_id": cycle_id,
                    "retrieval_rank": rank,
                },
                "narrative": str(prompt.get("narrative") or ""),
                "structured_state": prompt.get("state") or {},
                "oow_proposal": events["oow_decision"].get("decision") or {},
                "retrieval_context": {
                    "query": retrieval.get("query"),
                    "routing": retrieval.get("routing"),
                },
                "source_chunk": {
                    "chunk_id": chunk_id,
                    "report_title": chunk.get("report_title"),
                    "source_file": chunk.get("source_file"),
                    "page": int(chunk.get("page") or 0),
                    "text": str(chunk.get("text") or ""),
                    "retrieval_score": chunk.get("score"),
                },
                # Retained for post-hoc comparison, never included in the GPT prompt.
                "prior_audit_outcome": {
                    "captain_decision": prior_captain.get("decision"),
                    "evidence_assessment": prior_captain.get("evidence_assessment"),
                },
            })
    if max_pairs is not None:
        pairs = pairs[:max_pairs]
    return pairs


def user_prompt(pair: dict[str, Any]) -> str:
    payload = {
        "task": (
            "Determine whether SOURCE_CHUNK can be used meaningfully as advisory "
            "investigation evidence for LIVE_AUDIT_STATE. If and only if it can, "
            "extract one source-grounded lesson card."
        ),
        "live_audit_state": {
            "audit_reference": pair["audit_reference"],
            "rendered_narrative": pair["narrative"],
            "structured_state": pair["structured_state"],
            "oow_proposal_under_review": pair["oow_proposal"],
        },
        "retrieval_context": pair["retrieval_context"],
        "source_chunk": pair["source_chunk"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def request_body(
    pair: dict[str, Any], model: str, reasoning_effort: str | None,
    max_output_tokens: int,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(pair)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "maib_chunk_live_state_applicability",
                "strict": True,
                "schema": ApplicabilityResponse.model_json_schema(mode="validation"),
            },
        },
        "max_completion_tokens": max_output_tokens,
    }
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return body


def exact_substring(needle: str, haystack: str) -> bool:
    return bool(needle.strip()) and normalized(needle) in normalized(haystack)


OUTER_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
)


def canonical_exact_quote(value: str, source: str) -> str:
    """Remove only an added pair of outer quotation marks.

    The returned value must itself occur verbatim after the same conservative
    whitespace/Unicode normalization used for PDF quotation validation.
    """
    candidate = value.strip()
    if exact_substring(candidate, source):
        return candidate
    for opening, closing in OUTER_QUOTE_PAIRS:
        if candidate.startswith(opening) and candidate.endswith(closing):
            inner = candidate[len(opening): len(candidate) - len(closing)].strip()
            if exact_substring(inner, source):
                return inner
    return candidate


def canonicalize_grounding_quotes(
    response: ApplicabilityResponse, pair: dict[str, Any]
) -> ApplicabilityResponse:
    """Canonicalize harmless quote wrappers before strict grounding checks."""
    response.narrative_evidence = [
        canonical_exact_quote(value, pair["narrative"])
        for value in response.narrative_evidence
    ]
    if response.lesson_card is not None:
        source = pair["source_chunk"]["text"]
        for item in response.lesson_card.supporting_quotes:
            item.quote = canonical_exact_quote(item.quote, source)
    return response


def validate_applicability(
    response: ApplicabilityResponse, pair: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    chunk = pair["source_chunk"]
    if response.chunk_id != chunk["chunk_id"]:
        errors.append("response chunk_id does not match the supplied chunk")

    for quote in response.narrative_evidence:
        if not exact_substring(quote, pair["narrative"]):
            errors.append(f"narrative quotation is not verbatim: {quote!r}")

    if response.can_be_used_meaningfully:
        if not response.source_contains_investigator_reasoning:
            errors.append("meaningful passage is not marked as investigator reasoning")
        if response.source_passage_type in {
            "background_or_method", "chronology", "quoted_rules_or_guidance", "unclear"
        }:
            errors.append("passage type cannot support a meaningful lesson card")
        if not response.narrative_evidence:
            errors.append("meaningful assessment has no verbatim live-narrative evidence")
        if not response.analogous_facts:
            errors.append("meaningful assessment has no analogous facts")
        if response.decision_usefulness == "none":
            errors.append("meaningful assessment has decision_usefulness=none")
        if not response.use_in_decision_prompt:
            errors.append("meaningful assessment is excluded from the decision prompt")
        if response.lesson_card is None:
            errors.append("meaningful assessment has no lesson card")
        else:
            if set(response.lesson_card.pages) != {chunk["page"]}:
                errors.append("lesson card cites a page other than the supplied chunk page")
            for item in response.lesson_card.supporting_quotes:
                if item.page != chunk["page"]:
                    errors.append("supporting quote cites a different page")
                if not exact_substring(item.quote, chunk["text"]):
                    errors.append(f"source quotation is not verbatim: {item.quote!r}")
    else:
        if response.lesson_card is not None:
            errors.append("non-meaningful assessment returned a lesson card")
        if response.use_in_decision_prompt:
            errors.append("non-meaningful assessment requests prompt inclusion")
        if response.decision_usefulness != "none":
            errors.append("non-meaningful assessment has non-none decision usefulness")
    return errors


def parse_sample(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"(case_\d+):(\d+)", value.strip())
    if not match:
        raise argparse.ArgumentTypeError("sample must have form case_01:44")
    return match.group(1), int(match.group(2))


def validate_key_shape(value: str | None) -> str:
    key = (value or "").strip()
    lowered = key.lower()
    if (
        len(key) < 20
        or "your-key" in lowered
        or "your_key" in lowered
        or "actual-key" in lowered
        or key in {"...", "<key>", "<api-key>"}
    ):
        raise ValueError("OPENAI_API_KEY is missing or contains an example placeholder")
    return key


def unique_output_dir(base: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return base / stamp


def ensure_new_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(
            f"refusing to overwrite existing output directory: {path}"
        )
    path.mkdir(parents=True)


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


def pair_from_prepared(record: dict[str, Any]) -> dict[str, Any]:
    body = record.get("request_body") or {}
    messages = body.get("messages") or []
    if len(messages) < 2:
        raise ValueError(f"prepared request {record.get('pair_id')} has no user message")
    payload = json.loads(messages[1]["content"])
    live = payload["live_audit_state"]
    return {
        "pair_id": record["pair_id"],
        "audit_reference": record["audit_reference"],
        "narrative": live["rendered_narrative"],
        "structured_state": live["structured_state"],
        "oow_proposal": live["oow_proposal_under_review"],
        "retrieval_context": payload["retrieval_context"],
        "source_chunk": payload["source_chunk"],
        "prior_audit_outcome": record.get("prior_audit_outcome") or {},
    }


def load_retry_run(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prepared = read_jsonl(path / "prepared_requests.jsonl")
    prior_results = read_jsonl(path / "results.jsonl")
    failures = read_jsonl(path / "failures.jsonl")
    failed_ids = {str(item.get("pair_id") or "") for item in failures}
    if not failed_ids:
        raise ValueError(f"retry source contains no failed pairs: {path}")
    pairs = [pair_from_prepared(item) for item in prepared if item.get("pair_id") in failed_ids]
    found = {pair["pair_id"] for pair in pairs}
    missing = sorted(failed_ids - found)
    if missing:
        raise ValueError(f"retry source is missing prepared requests for: {missing}")
    return pairs, prior_results


def usage_dict(completion: Any) -> dict[str, Any]:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def estimated_cost(requests: list[dict[str, Any]], model: str) -> dict[str, Any]:
    estimated_input_tokens = sum(approximate_tokens(json.dumps(r)) for r in requests)
    maximum_output_tokens = sum(int(r.get("max_completion_tokens") or 0) for r in requests)
    rates = MODEL_PRICES.get(model, {}).get("standard")
    result: dict[str, Any] = {
        "requests": len(requests),
        "estimated_input_tokens": estimated_input_tokens,
        "maximum_output_tokens": maximum_output_tokens,
        "pricing_mode": "standard_direct_api",
        "model": model,
    }
    if rates:
        result.update({
            "input_rate_per_million": rates[0],
            "output_rate_per_million": rates[1],
            "estimated_input_cost_usd": estimated_input_tokens / 1_000_000 * rates[0],
            "maximum_output_cost_usd": maximum_output_tokens / 1_000_000 * rates[1],
            "maximum_total_cost_usd": (
                estimated_input_tokens / 1_000_000 * rates[0]
                + maximum_output_tokens / 1_000_000 * rates[1]
            ),
        })
    else:
        result["pricing_note"] = "unknown model rate; no cost estimate calculated"
    return result


def write_report(
    path: Path, results: list[dict[str, Any]], failures: list[dict[str, Any]],
    model: str,
) -> None:
    meaningful = [r for r in results if r["assessment"]["can_be_used_meaningfully"]]
    by_type = Counter(r["assessment"]["source_passage_type"] for r in results)
    lines = [
        "# GPT audit-state/chunk applicability test",
        "",
        f"Model: `{model}`",
        "",
        f"Validated judgments: **{len(results)}**  ",
        f"Meaningfully applicable: **{len(meaningful)}**  ",
        f"Rejected as not meaningful: **{len(results) - len(meaningful)}**  ",
        f"Failed API or local validation: **{len(failures)}**",
        "",
        "## Passage-type judgments",
        "",
        "| Passage type | Count |",
        "|---|---:|",
        *(f"| {key} | {value} |" for key, value in sorted(by_type.items())),
        "",
        "## Pair-level results",
        "",
        "| Audit pair | Meaningful | Usefulness | Confidence | Extracted lesson |",
        "|---|---|---|---:|---|",
    ]
    for record in results:
        assessment = record["assessment"]
        card = assessment.get("lesson_card")
        lesson = str(card.get("transferable_lesson") if card else "—").replace("|", "\\|")
        lines.append(
            f"| {record['pair_id']} | {assessment['can_be_used_meaningfully']} | "
            f"{assessment['decision_usefulness']} | {assessment['confidence']:.2f} | {lesson} |"
        )
    lines.extend([
        "",
        "The prior captain evidence verdict is retained in `results.jsonl` for comparison but was not shown to GPT.",
        "Every accepted card passed exact page and quotation checks against the supplied chunk.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    prior_results: list[dict[str, Any]] = []
    if args.retry_run is not None:
        if args.sample or args.max_pairs is not None:
            raise ValueError("--retry-run cannot be combined with --sample or --max-pairs")
        pairs, prior_results = load_retry_run(args.retry_run)
    else:
        samples = args.sample or list(DEFAULT_SAMPLES)
        pairs = build_pairs(args.audit_root, samples, args.max_pairs)
    effort = resolve_reasoning_effort(args.model, args.reasoning_effort)
    bodies = [request_body(pair, args.model, effort, args.max_output_tokens) for pair in pairs]

    output_dir = args.output_dir or unique_output_dir(args.output_base)
    ensure_new_directory(output_dir)
    prepared = [
        {
            "pair_id": pair["pair_id"],
            "audit_reference": pair["audit_reference"],
            "source_chunk": pair["source_chunk"],
            "prior_audit_outcome": pair["prior_audit_outcome"],
            "request_body": body,
        }
        for pair, body in zip(pairs, bodies)
    ]
    write_jsonl(output_dir / "prepared_requests.jsonl", prepared)
    estimate = estimated_cost(bodies, args.model)
    (output_dir / "cost_estimate.json").write_text(
        json.dumps(estimate, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(output_dir), **estimate}, indent=2))

    if args.dry_run:
        print("dry run complete; no API requests were sent")
        return

    key = validate_key_shape(os.environ.get("OPENAI_API_KEY"))
    from openai import OpenAI

    client = OpenAI(api_key=key)
    results: list[dict[str, Any]] = list(prior_results)
    failures: list[dict[str, Any]] = []
    attempts_log: list[dict[str, Any]] = []
    for index, (pair, body) in enumerate(zip(pairs, bodies), start=1):
        print(f"[{index}/{len(pairs)}] {pair['pair_id']}", flush=True)
        last_error = ""
        for attempt in range(1, args.attempts + 1):
            completion: Any | None = None
            content: str | None = None
            try:
                completion = client.chat.completions.create(**body)
                content = completion.choices[0].message.content
                if not content:
                    raise ValueError("API response contained no message content")
                parsed = ApplicabilityResponse.model_validate_json(content)
                parsed = canonicalize_grounding_quotes(parsed, pair)
                validation_errors = validate_applicability(parsed, pair)
                if validation_errors:
                    raise ValueError("; ".join(validation_errors))
                attempts_log.append({
                    "pair_id": pair["pair_id"],
                    "attempt": attempt,
                    "response_id": getattr(completion, "id", None),
                    "raw_content": content,
                    "validation": "accepted",
                })
                results.append({
                    "pair_id": pair["pair_id"],
                    "audit_reference": pair["audit_reference"],
                    "source_chunk": pair["source_chunk"],
                    "prior_audit_outcome": pair["prior_audit_outcome"],
                    "assessment": parsed.model_dump(),
                    "api": {
                        "response_id": getattr(completion, "id", None),
                        "model": getattr(completion, "model", args.model),
                        "usage": usage_dict(completion),
                        "attempt": attempt,
                    },
                })
                last_error = ""
                break
            except Exception as exc:  # API and schema errors are recorded per pair.
                last_error = f"{type(exc).__name__}: {exc}"
                attempts_log.append({
                    "pair_id": pair["pair_id"],
                    "attempt": attempt,
                    "response_id": (
                        getattr(completion, "id", None)
                        if completion is not None else None
                    ),
                    "raw_content": content,
                    "validation": "rejected",
                    "error": last_error,
                })
                if attempt < args.attempts:
                    time.sleep(min(2 ** attempt, 8))
        if last_error:
            failures.append({
                "pair_id": pair["pair_id"],
                "audit_reference": pair["audit_reference"],
                "error": last_error,
                "attempts": args.attempts,
            })

    write_jsonl(output_dir / "results.jsonl", results)
    write_jsonl(output_dir / "failures.jsonl", failures)
    write_jsonl(output_dir / "attempts.jsonl", attempts_log)
    summary = {
        "model": args.model,
        "pairs_in_complete_result": len(results) + len(failures),
        "pairs_retried": len(pairs) if args.retry_run is not None else 0,
        "pairs_prepared": len(pairs),
        "pairs_validated": len(results),
        "pairs_failed": len(failures),
        "meaningful": sum(
            bool(r["assessment"]["can_be_used_meaningfully"]) for r in results
        ),
        "not_meaningful": sum(
            not bool(r["assessment"]["can_be_used_meaningfully"]) for r in results
        ),
        "output_dir": str(output_dir),
        "retry_source": str(args.retry_run) if args.retry_run is not None else None,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    write_report(output_dir / "REPORT.md", results, failures, args.model)
    print(json.dumps(summary, indent=2))
    if failures:
        raise RuntimeError(
            f"{len(failures)} pair(s) failed; inspect {output_dir / 'failures.jsonl'}"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--audit-root", type=Path, default=DEFAULT_AUDIT_ROOT)
    result.add_argument(
        "--sample", action="append", type=parse_sample,
        help="audit cycle to test, e.g. case_01:44; repeat for multiple cycles",
    )
    result.add_argument("--max-pairs", type=int)
    result.add_argument("--model", default="gpt-6.1-sol")
    result.add_argument("--reasoning-effort", default="low")
    result.add_argument("--max-output-tokens", type=int, default=5000)
    result.add_argument("--attempts", type=int, default=2)
    result.add_argument(
        "--retry-run", type=Path,
        help=(
            "reuse valid results from a previous run and call the API only for "
            "the pairs listed in that run's failures.jsonl"
        ),
    )
    result.add_argument(
        "--output-base", type=Path,
        default=Path("reports/maib_chunk_applicability"),
    )
    result.add_argument("--output-dir", type=Path)
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    if args.max_pairs is not None and args.max_pairs < 1:
        parser().error("--max-pairs must be positive")
    if args.attempts < 1:
        parser().error("--attempts must be positive")
    try:
        run(args)
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
