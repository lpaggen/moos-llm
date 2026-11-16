#!/usr/bin/env python3
"""Export recorded OOW inputs and decisions as input/output JSONL pairs."""

import argparse
import json
import sys
from pathlib import Path


def _is_rare_stop_or_port(decision):
    return decision.get("action") == "stop" or (
        decision.get("action") == "alter_course"
        and isinstance(decision.get("degrees"), (int, float))
        and decision["degrees"] < 0
    )


def extract_pairs(
    path,
    validated_only=False,
    chat_format=False,
    rare_stop_or_port=False,
):
    pending = {}
    decisions = {}
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"Skipping invalid JSON: {path}:{number}", file=sys.stderr)
                continue
            event = row.get("event")
            cycle = row.get("cycle_id")
            if event == "startup":
                pending.clear()
            elif event == "oow_prompt_input" and cycle is not None:
                pending[cycle] = {
                    key: row[key]
                    for key in ("system_prompt", "user_prompt", "narrative", "constraints", "state")
                    if key in row
                }
            elif event == "oow_decision_failed":
                pending.pop(cycle, None)
            elif event == "oow_decision" and cycle in pending:
                prompt = pending.pop(cycle)
                if isinstance(row.get("decision"), dict):
                    pair = {"input": prompt, "output": row["decision"]}
                    if rare_stop_or_port and not _is_rare_stop_or_port(row["decision"]):
                        continue
                    if validated_only:
                        decisions[cycle] = pair
                    else:
                        yield _format_pair(pair, chat_format)
            elif event == "oow_decision_rejected":
                decisions.pop(cycle, None)
            elif event == "captain_validated" and cycle in decisions:
                yield _format_pair(decisions.pop(cycle), chat_format)


def _format_pair(pair, chat_format):
    if not chat_format:
        return pair
    user_prompt = pair["input"]["user_prompt"]
    try:
        payload = json.loads(user_prompt)
        payload.pop("deterministic_colregs_oracle", None)
        user_prompt = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "Choose one safe executable navigation action from the supplied "
                    "bridge state and allowed actions. Return only the required JSON."
                ),
            },
            {"role": "user", "content": user_prompt},
            {
                "role": "assistant",
                "content": json.dumps(pair["output"], separators=(",", ":")),
            },
        ]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path,
                        help="Audit JSONL files or directories to search for audit.jsonl")
    parser.add_argument("--output", "-o", required=True, type=Path)
    parser.add_argument("--validated-only", action="store_true",
                        help="Export only decisions accepted by the deterministic validator")
    parser.add_argument("--chat-format", action="store_true",
                        help="Write compact chat fine-tuning JSONL without the teacher prompt")
    parser.add_argument(
        "--rare-stop-or-port",
        action="store_true",
        help="Export only stop decisions and alter_course decisions with negative degrees",
    )
    args = parser.parse_args()
    paths = set()
    for path in args.inputs:
        if not path.exists():
            parser.error(f"input does not exist: {path}")
        if path.is_dir():
            paths.update(p.resolve() for p in path.rglob("audit.jsonl"))
        else:
            paths.add(path.resolve())
    if not paths:
        parser.error("no audit files found")
    if args.output.resolve() in paths:
        parser.error("output must not overwrite an input audit")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("w", encoding="utf-8") as output:
        for path in sorted(paths):
            for pair in extract_pairs(
                path,
                args.validated_only,
                args.chat_format,
                args.rare_stop_or_port,
            ):
                output.write(json.dumps(pair, ensure_ascii=False) + "\n")
                count += 1
    print(f"Wrote {count} OOW pairs from {len(paths)} audits to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
