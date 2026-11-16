#!/usr/bin/env python3
"""Inspect MAIB retrieval results without running the simulator."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rag.maib import MaibRetriever


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--index", default="accidents/index.jsonl")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--embeddings", help="Optional .npy matrix for hybrid BM25/dense retrieval")
    parser.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    args = parser.parse_args()
    retriever = MaibRetriever(
        args.index,
        top_k=args.top_k,
        embeddings_path=args.embeddings,
        embedding_model=args.model,
    )
    for result in retriever.retrieve(args.query):
        print(
            f"{result.chunk_id} score={result.score:.3f} "
            f"bm25={result.bm25_score:.3f} dense={result.dense_score} "
            f"bm25_rank={result.bm25_rank} dense_rank={result.dense_rank} "
            f"file={result.source_file} page={result.page}\n"
            f"{result.report_title}\n{result.text}\n"
        )


if __name__ == "__main__":
    main()
