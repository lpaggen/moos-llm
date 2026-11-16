#!/usr/bin/env python3
"""Build a page-aware JSONL index from local MAIB PDF reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader


def chunks(text: str, size: int, overlap: int):
    words = re.sub(r"\s+", " ", text).strip().split()
    step = size - overlap
    for start in range(0, len(words), step):
        chunk = words[start : start + size]
        if len(chunk) >= 80:
            yield " ".join(chunk)
        if start + size >= len(words):
            break


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--chunk-words", type=int, default=400)
    parser.add_argument("--overlap-words", type=int, default=60)
    args = parser.parse_args()
    if args.chunk_words <= 80 or not 0 <= args.overlap_words < args.chunk_words:
        parser.error("chunk size must exceed 80 words and overlap must be smaller")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pdf_count = chunk_count = skipped = 0
    with args.output.open("w", encoding="utf-8") as output:
        for pdf_path in sorted(args.input_dir.glob("*.pdf")):
            try:
                reader = PdfReader(pdf_path)
                metadata_title = reader.metadata.title if reader.metadata else None
                title = str(metadata_title or pdf_path.stem).strip()
                wrote_pdf = False
                for page_number, page in enumerate(reader.pages, start=1):
                    for ordinal, text in enumerate(
                        chunks(page.extract_text() or "", args.chunk_words, args.overlap_words),
                        start=1,
                    ):
                        digest = hashlib.sha256(
                            f"{pdf_path.name}:{page_number}:{ordinal}:{text}".encode()
                        ).hexdigest()[:12]
                        output.write(json.dumps({
                            "chunk_id": f"maib-{digest}",
                            "report_title": title,
                            "source_file": str(pdf_path),
                            "page": page_number,
                            "text": text,
                        }, ensure_ascii=False) + "\n")
                        chunk_count += 1
                        wrote_pdf = True
                pdf_count += int(wrote_pdf)
                skipped += int(not wrote_pdf)
            except Exception as exc:
                skipped += 1
                print(f"warning: skipped {pdf_path}: {exc}")
    print(f"Indexed {pdf_count} PDFs into {chunk_count} chunks; skipped {skipped}; output={args.output}")


if __name__ == "__main__":
    main()
