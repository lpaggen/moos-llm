"""Convert retrieval traces plus trusted rule judgments into reranker examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_no}: expected object")
                yield value


def relevant_rules(row: dict) -> set[int]:
    labels = row.get("colregs_labels") or {}
    rules = {int(value) for value in labels.get("standing_rules", [])}
    rules.update(int(value) for value in labels.get("relevant_encounter_rules", []))
    return rules


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument(
        "--judgments",
        required=True,
        type=Path,
        help="Trusted JSONL keyed by id containing canonical colregs_labels",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    judgments = {str(row["id"]): relevant_rules(row) for row in rows(args.judgments)}
    output = []
    for trace in rows(args.traces):
        rid = str(trace.get("id"))
        if rid not in judgments:
            continue
        positives = judgments[rid]
        for rank, candidate in enumerate(trace.get("candidates") or [], 1):
            rule = int(candidate["rule"])
            output.append({
                "id": rid,
                "query": trace["query"],
                "document_id": candidate["chunk_id"],
                "document": f"Rule {rule} — {candidate['title']}\n{candidate['text']}",
                "label": int(rule in positives),
                "rule": rule,
                "first_stage_rank": rank,
                "first_stage_score": candidate["retrieval_score"],
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    positives = sum(row["label"] for row in output)
    print(json.dumps({
        "examples": len(output),
        "positives": positives,
        "negatives": len(output) - positives,
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
