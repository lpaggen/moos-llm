"""CLI for the vanilla Qwen3-8B COLREG-RAG baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openai import OpenAI

from .oow import RagOOW
from .retrieval import ColregsRetriever


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "agents/colregs/colregs.json"
DEFAULT_GUIDANCE = ROOT / "experiments/qwen8b_colregs_rag/colregs_ship_guidance.json"


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def narrative_by_id(path: Path, requested: str) -> str:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if str(row.get("id")) == requested:
                value = row.get("narrative") or row.get("input")
                if isinstance(value, str):
                    return value
    raise KeyError(f"id {requested!r} not found in {path}")


def make_retriever(args: argparse.Namespace) -> ColregsRetriever:
    return ColregsRetriever(
        args.corpus,
        candidate_k=args.candidate_k,
        top_k=args.top_k,
        guidance=args.guidance,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--guidance", type=Path, default=DEFAULT_GUIDANCE)
    parser.add_argument("--candidate-k", type=int, default=12)
    parser.add_argument("--top-k", type=int, default=6)
    sub = parser.add_subparsers(dest="command", required=True)

    retrieve = sub.add_parser("retrieve")
    retrieve.add_argument("--query", required=True)

    decide = sub.add_parser("decide")
    decide.add_argument("--input", required=True, type=Path)
    decide.add_argument("--id", required=True)
    decide.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    decide.add_argument("--api-key", default="local-vllm")
    decide.add_argument("--model", default="Qwen/Qwen3-8B")
    decide.add_argument("--cruise-speed", type=float, required=True)
    decide.add_argument("--allowed-actions", nargs="+", default=[
        "maintain_course", "alter_course", "set_speed", "stop", "rejoin_route"
    ])
    decide.add_argument("--log", type=Path)
    args = parser.parse_args()

    retriever = make_retriever(args)
    if args.command == "retrieve":
        candidates, selected = retriever.retrieve(args.query)
        print(json.dumps({
            "query": args.query,
            "candidates": [item.as_dict() for item in candidates],
            "selected": [item.as_dict() for item in selected],
        }, ensure_ascii=False, indent=2))
        return

    narrative = narrative_by_id(args.input, args.id)
    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    decision, trace = RagOOW(client, retriever, args.model).decide(
        narrative, args.allowed_actions, cruise_speed=args.cruise_speed
    )
    trace["id"] = args.id
    if args.log:
        append_jsonl(args.log, trace)
    print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
