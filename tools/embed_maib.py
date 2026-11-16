#!/usr/bin/env python3
"""Precompute normalized dense embeddings for the MAIB JSONL index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    documents = [json.loads(line) for line in args.index.read_text().splitlines() if line.strip()]
    passages = [
        f"Report: {doc.get('report_title', '')}. Page: {doc.get('page', '')}. Passage: {doc['text']}"
        for doc in documents
    ]
    model = SentenceTransformer(args.model, device="cpu")
    matrix = model.encode(
        passages,
        batch_size=args.batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.output, matrix.astype(np.float32))
    print(f"Wrote {matrix.shape} embeddings to {args.output}")


if __name__ == "__main__":
    main()
