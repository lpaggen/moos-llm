#!/usr/bin/env python3
"""Compare lexical and hybrid MAIB retrieval on fixed diagnostic queries."""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rag.maib import MaibRetriever


QUERIES = (
    "Overtaking vessel approaching from astern, own ship is stand-on, slowly closing with wide predicted separation; is intervention required?",
    "Crossing vessel on the starboard bow, own ship is give-way, rapidly closing with close CPA and near-term TCPA.",
    "Head-on encounter in restricted visibility, rapidly closing with critical CPA and immediate TCPA.",
    "Contact is opening after closest approach with no future CPA; preserve predictable course and speed when collision risk is not established.",
)


def run(label: str, retriever: MaibRetriever) -> None:
    elapsed = []
    print(f"\n{label}")
    for query in QUERIES:
        started = time.perf_counter()
        results = retriever.retrieve(query)
        elapsed.append(time.perf_counter() - started)
        print(f"QUERY: {query}")
        for result in results:
            print(
                f"  {result.source_file} p.{result.page} {result.chunk_id} "
                f"bm25={result.bm25_score:.3f} dense={result.dense_score} "
                f"ranks={result.bm25_rank}/{result.dense_rank}"
            )
    print(f"median_query_ms={statistics.median(elapsed) * 1000:.1f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="accidents/index.jsonl")
    parser.add_argument("--embeddings", default="accidents/embeddings.npy")
    parser.add_argument("--reranker")
    args = parser.parse_args()
    started = time.perf_counter()
    lexical = MaibRetriever(args.index, top_k=2, min_score=5.0)
    print(f"lexical_load_s={time.perf_counter() - started:.2f}")
    run("LEXICAL", lexical)
    started = time.perf_counter()
    hybrid = MaibRetriever(
        args.index,
        top_k=2,
        min_score=5.0,
        embeddings_path=args.embeddings,
        reranker_model=args.reranker,
    )
    print(f"hybrid_load_s={time.perf_counter() - started:.2f}")
    run("HYBRID", hybrid)


if __name__ == "__main__":
    main()
