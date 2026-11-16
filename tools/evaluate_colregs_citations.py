#!/usr/bin/env python3
"""Evaluate decision-rule citations against the per-cycle Eriksen oracle."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


RULE_NUMBER = r"\d+(?:\s*\([a-z0-9]+\))*"
RULE_CLAUSE = re.compile(
    rf"\b(?:COLREG(?:s)?\s*)?Rules?\s*({RULE_NUMBER}"
    rf"(?:\s*,\s*(?:(?:and|&)\s+)?{RULE_NUMBER}"
    rf"|\s+(?:and|&)\s+{RULE_NUMBER})*)",
    re.IGNORECASE,
)
PERSISTENT_RULES = frozenset({2, 5, 6, 7})


def extract_rule_citations(reason: str) -> set[int]:
    """Extract rule numbers only from explicit Rule/COLREG citation clauses."""
    rules: set[int] = set()
    for match in RULE_CLAUSE.finditer(reason or ""):
        rules.update(int(value) for value in re.findall(r"\d+", match.group(1)))
    return rules


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc


def expected_rules(classification: dict[str, Any]) -> tuple[str | None, set[int]]:
    """Return the primary contact and its complete Eriksen rule set.

    ``applicable_rules`` is deliberately used without filtering, then unioned
    with standing/persistent Rules 2, 5, 6, and 7. The explicit union matters
    on clear-water cycles, where the oracle has no contact record.
    """
    primary = classification.get("primary_contact")
    contacts = classification.get("contacts") or []
    selected = next(
        (contact for contact in contacts if contact.get("contact") == primary),
        contacts[0] if len(contacts) == 1 else None,
    )
    if not isinstance(selected, dict):
        return primary, set(PERSISTENT_RULES)
    return primary or selected.get("contact"), PERSISTENT_RULES | {
        int(rule) for rule in selected.get("applicable_rules") or []
    }


# Backwards-compatible private name for callers predating the frozen contract.
_expected_rules = expected_rules


def evaluate_audit(path: Path, case: int | None = None) -> list[dict[str, Any]]:
    oracles: dict[Any, dict[str, Any]] = {}
    decisions: dict[Any, dict[str, Any]] = {}
    for row in _rows(path):
        cycle = row.get("cycle_id")
        if row.get("event") == "oow_colregs_oracle":
            oracles[cycle] = row.get("classification") or {}
        elif row.get("event") == "oow_decision":
            decisions[cycle] = row.get("decision") or {}

    records = []
    for cycle in sorted(decisions, key=lambda value: (value is None, value)):
        decision = decisions[cycle]
        oracle = oracles.get(cycle)
        if oracle is None:
            records.append({
                "case": case,
                "cycle_id": cycle,
                "status": "missing_oracle",
                "action": decision.get("action"),
                "reason": decision.get("reason") or "",
            })
            continue
        primary, expected = expected_rules(oracle)
        cited = extract_rule_citations(str(decision.get("reason") or ""))
        correct = cited & expected
        missing = expected - cited
        extra = cited - expected
        precision = len(correct) / len(cited) if cited else (1.0 if not expected else 0.0)
        recall = len(correct) / len(expected) if expected else (1.0 if not cited else 0.0)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        records.append({
            "case": case,
            "cycle_id": cycle,
            "status": "evaluated",
            "primary_contact": primary,
            "oracle_state": next((
                contact.get("state")
                for contact in oracle.get("contacts") or []
                if contact.get("contact") == primary
            ), None),
            "action": decision.get("action"),
            "reason": decision.get("reason") or "",
            "expected_rules": sorted(expected),
            "cited_rules": sorted(cited),
            "correct_rules": sorted(correct),
            "missing_rules": sorted(missing),
            "unsupported_rules": sorted(extra),
            "citation_present": bool(cited),
            "exact_match": cited == expected,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        })
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [record for record in records if record["status"] == "evaluated"]
    expected_total = sum(len(record["expected_rules"]) for record in evaluated)
    cited_total = sum(len(record["cited_rules"]) for record in evaluated)
    correct_total = sum(len(record["correct_rules"]) for record in evaluated)
    micro_precision = correct_total / cited_total if cited_total else 0.0
    micro_recall = correct_total / expected_total if expected_total else 0.0
    micro_f1 = (
        2 * micro_precision * micro_recall / (micro_precision + micro_recall)
        if micro_precision + micro_recall else 0.0
    )
    missing = Counter(
        rule for record in evaluated for rule in record["missing_rules"]
    )
    unsupported = Counter(
        rule for record in evaluated for rule in record["unsupported_rules"]
    )
    return {
        "decisions": len(records),
        "evaluated_decisions": len(evaluated),
        "missing_oracle_decisions": len(records) - len(evaluated),
        "citation_presence_rate": (
            sum(record["citation_present"] for record in evaluated) / len(evaluated)
            if evaluated else None
        ),
        "exact_match_rate": (
            sum(record["exact_match"] for record in evaluated) / len(evaluated)
            if evaluated else None
        ),
        "macro_precision": (
            sum(record["precision"] for record in evaluated) / len(evaluated)
            if evaluated else None
        ),
        "macro_recall": (
            sum(record["recall"] for record in evaluated) / len(evaluated)
            if evaluated else None
        ),
        "macro_f1": (
            sum(record["f1"] for record in evaluated) / len(evaluated)
            if evaluated else None
        ),
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "micro_f1": micro_f1,
        "most_missing_rules": dict(missing.most_common()),
        "most_unsupported_rules": dict(unsupported.most_common()),
        "reference": (
            "Per-cycle applicable_rules for the Eriksen primary contact from "
            "oow_colregs_oracle, union persistent Rules 2, 5, 6, and 7 on "
            "every cycle."
        ),
    }


def campaign_audits(path: Path) -> list[tuple[int | None, Path]]:
    if path.is_file():
        return [(None, path)]
    audits = []
    for audit in sorted(path.glob("case_*/audit.jsonl")):
        match = re.fullmatch(r"case_(\d+)", audit.parent.name)
        audits.append((int(match.group(1)) if match else None, audit))
    return audits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Campaign directory or one audit.jsonl")
    parser.add_argument("--output", type=Path, help="Output directory; defaults to RUN/citation_audit")
    args = parser.parse_args()
    audits = campaign_audits(args.run)
    if not audits:
        parser.error(f"{args.run}: no audit logs found")
    records = [
        record
        for case, audit in audits
        for record in evaluate_audit(audit, case)
    ]
    report = {"summary": summarize(records), "decisions": records}
    output = args.output or (args.run / "citation_audit" if args.run.is_dir() else args.run.parent / "citation_audit")
    output.mkdir(parents=True, exist_ok=True)
    (output / "colregs_citations.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    fields = [
        "case", "cycle_id", "status", "primary_contact", "oracle_state", "action",
        "expected_rules", "cited_rules", "missing_rules", "unsupported_rules",
        "citation_present", "exact_match", "precision", "recall", "f1", "reason",
    ]
    with (output / "colregs_citations.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({
                key: json.dumps(value) if isinstance(value, list) else value
                for key, value in record.items()
            })
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
