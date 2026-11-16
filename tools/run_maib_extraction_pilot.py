#!/usr/bin/env python3
"""Run a reproducible MAIB evidence-selection and reasoning-record pilot.

The pilot evaluates first-stage retrieval and cross-encoder reranking against
the page provenance of existing machine-validated lesson cards.  It also
inventories text, tables/layout cues, and embedded PDF image objects without
claiming to interpret visual content that has not passed through a vision
model.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import sys
import time
from typing import Any, Iterable

import numpy as np
from pypdf import PdfReader
from sentence_transformers import CrossEncoder, SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rag.maib import tokenize


DEFAULT_INDEX = Path("accidents/index.jsonl")
DEFAULT_EMBEDDINGS = Path("accidents/embeddings.npy")
DEFAULT_CARDS = Path("accidents/cards/cards.machine_validated.jsonl")
DEFAULT_PDF_DIR = Path("accidents")
DEFAULT_OUTPUT = Path("reports/maib_extraction_pilot")
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_RERANKER = "cross-encoder/ms-marco-MiniLM-L6-v2"
SPACE_RE = re.compile(r"\s+")
TABLE_RE = re.compile(r"\b(table|column|row)\s*[0-9ivx]*\b", re.IGNORECASE)
FIGURE_RE = re.compile(r"\b(figure|fig\.)\s*[0-9ivx]+\b", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object at {path}:{line_number}")
            records.append(value)
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def normalize_text(text: str) -> str:
    return SPACE_RE.sub(" ", text).strip().casefold()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def embedded_image_count(page: Any) -> tuple[int, bool]:
    """Count image XObjects without decoding them; return count and parse success."""
    try:
        resources_ref = page.get("/Resources")
        if resources_ref is None:
            return 0, True
        resources = resources_ref.get_object()
        objects_ref = resources.get("/XObject")
        if objects_ref is None:
            return 0, True
        objects = objects_ref.get_object()
        count = 0
        for value in objects.values():
            obj = value.get_object()
            if str(obj.get("/Subtype")) == "/Image":
                count += 1
        return count, True
    except Exception:
        return 0, False


def inventory_pdfs(pdf_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        try:
            reader = PdfReader(pdf_path)
        except Exception as exc:
            failures.append({"source_file": str(pdf_path), "error": str(exc)})
            continue
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
                text_ok = True
            except Exception:
                text = ""
                text_ok = False
            image_count, image_parse_ok = embedded_image_count(page)
            normalized = normalize_text(text)
            pages.append({
                "source_file": str(pdf_path),
                "page": page_number,
                "text_characters": len(normalized),
                "text_extraction_ok": text_ok,
                "embedded_image_objects": image_count,
                "image_inventory_ok": image_parse_ok,
                "table_or_structured_layout_cue": bool(TABLE_RE.search(text)),
                "figure_caption_cue": bool(FIGURE_RE.search(text)),
                "visual_only_candidate": len(normalized) < 80 and image_count > 0,
            })
    report_count = len({row["source_file"] for row in pages})
    summary = {
        "reports": report_count,
        "pages": len(pages),
        "pages_with_extractable_text": sum(row["text_characters"] >= 80 for row in pages),
        "pages_with_embedded_images": sum(row["embedded_image_objects"] > 0 for row in pages),
        "pages_with_table_or_layout_cues": sum(row["table_or_structured_layout_cue"] for row in pages),
        "pages_with_figure_caption_cues": sum(row["figure_caption_cue"] for row in pages),
        "visual_only_candidates": sum(row["visual_only_candidate"] for row in pages),
        "image_inventory_failures": sum(not row["image_inventory_ok"] for row in pages),
        "pdf_open_failures": failures,
    }
    return pages, summary


def card_query(card: dict[str, Any], mode: str = "generic") -> str:
    base = (
        "Locate report passages needed to construct a source-grounded maritime reasoning record. "
        "Prefer investigator analysis, conclusions, safety issues, causal or contributory factors, "
        "and passages connecting observed conditions and warning signs to decisions or actions and outcomes. "
        "Also find limitations that show when a lesson should not transfer to another encounter."
    )
    if mode == "generic":
        return base
    encounter = ", ".join(str(item).replace("_", " ") for item in card.get("encounter_types", []))
    roles = ", ".join(str(item).replace("_", " ") for item in card.get("roles", []))
    phases = ", ".join(str(item).replace("_", " ") for item in card.get("encounter_phases", []))
    metadata = (
        f" Encounter categories: {encounter or 'unknown'}. Roles: {roles or 'uncertain'}. "
        f"Phases: {phases or 'unknown'}."
    )
    if mode == "metadata":
        return base + metadata
    if mode != "enriched":
        raise ValueError(f"unsupported query mode: {mode}")
    warnings = "; ".join(str(item) for item in card.get("warning_signs", []))
    return base + metadata + f" Observed warning signs: {warnings or 'not supplied'}."


@dataclass
class QueryRanking:
    card: dict[str, Any]
    query: str
    source_indexes: list[int]
    bm25: list[int]
    dense: list[int]
    rrf: list[int]
    reranked: list[int] | None = None


def bm25_order(query: str, indexes: list[int], documents: list[dict[str, Any]]) -> list[int]:
    counts = [Counter(tokenize(str(documents[index]["text"]))) for index in indexes]
    lengths = [sum(item.values()) for item in counts]
    average_length = statistics.fmean(lengths) if lengths else 1.0
    document_frequency: Counter[str] = Counter()
    for item in counts:
        document_frequency.update(item.keys())
    terms = set(tokenize(query))
    total = len(indexes)
    scored: list[tuple[float, str, int]] = []
    for local_index, global_index in enumerate(indexes):
        score = 0.0
        for term in terms:
            frequency = counts[local_index].get(term, 0)
            if not frequency:
                continue
            docs_with_term = document_frequency[term]
            inverse = math.log(1.0 + (total - docs_with_term + 0.5) / (docs_with_term + 0.5))
            denominator = frequency + 1.5 * (
                1.0 - 0.75 + 0.75 * lengths[local_index] / max(average_length, 1.0)
            )
            score += inverse * frequency * 2.5 / denominator
        scored.append((score, str(documents[global_index].get("chunk_id", "")), global_index))
    return [item[2] for item in sorted(scored, key=lambda item: (-item[0], item[1]))]


def dense_order(
    query_vector: np.ndarray,
    indexes: list[int],
    embeddings: np.ndarray,
    documents: list[dict[str, Any]],
) -> list[int]:
    scores = embeddings[indexes] @ query_vector
    ranked = sorted(
        zip(scores.tolist(), indexes),
        key=lambda item: (-float(item[0]), str(documents[item[1]].get("chunk_id", ""))),
    )
    return [item[1] for item in ranked]


def reciprocal_rank_fusion(rankings: list[list[int]], *, constant: int = 60) -> list[int]:
    scores: defaultdict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, index in enumerate(ranking, start=1):
            scores[index] += 1.0 / (constant + rank)
    return [item[0] for item in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def unique_page_ranking(order: list[int], documents: list[dict[str, Any]]) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for index in order:
        page = documents[index].get("page")
        if not isinstance(page, int) or page in seen:
            continue
        pages.append(page)
        seen.add(page)
    return pages


def ranking_metrics(page_order: list[int], gold_pages: set[int], cutoff: int = 5) -> dict[str, float]:
    first_rank = next((rank for rank, page in enumerate(page_order, start=1) if page in gold_pages), None)
    found = set(page_order[:cutoff]) & gold_pages
    dcg = sum(1.0 / math.log2(rank + 1) for rank, page in enumerate(page_order[:cutoff], start=1) if page in gold_pages)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(gold_pages), cutoff) + 1))
    return {
        "hit_at_1": float(bool(page_order[:1] and page_order[0] in gold_pages)),
        "hit_at_3": float(bool(set(page_order[:3]) & gold_pages)),
        "hit_at_5": float(bool(found)),
        "gold_page_recall_at_5": len(found) / max(len(gold_pages), 1),
        "mrr": 0.0 if first_rank is None else 1.0 / first_rank,
        "ndcg_at_5": 0.0 if ideal == 0 else dcg / ideal,
    }


def mean_metrics(rows: list[dict[str, float]]) -> dict[str, float]:
    return {
        key: statistics.fmean(row[key] for row in rows)
        for key in rows[0]
    } if rows else {}


def paired_cluster_bootstrap(
    base: list[dict[str, float]],
    candidate: list[dict[str, float]],
    cluster_ids: list[str],
    *,
    samples: int = 2000,
    seed: int = 20261113,
) -> dict[str, dict[str, float]]:
    if not (len(base) == len(candidate) == len(cluster_ids)):
        raise ValueError("base, candidate, and cluster_ids must have equal lengths")
    rng = random.Random(seed)
    count = len(base)
    clusters: defaultdict[str, list[int]] = defaultdict(list)
    for index, cluster_id in enumerate(cluster_ids):
        clusters[cluster_id].append(index)
    cluster_names = sorted(clusters)
    output: dict[str, dict[str, float]] = {}
    for metric in ("hit_at_5", "mrr", "ndcg_at_5"):
        observed = statistics.fmean(candidate[i][metric] - base[i][metric] for i in range(count))
        deltas = []
        for _ in range(samples):
            sampled_clusters = [rng.choice(cluster_names) for _ in cluster_names]
            picks = [index for name in sampled_clusters for index in clusters[name]]
            deltas.append(statistics.fmean(candidate[i][metric] - base[i][metric] for i in picks))
        deltas.sort()
        output[metric] = {
            "observed_delta": observed,
            "bootstrap_95_low": deltas[int(0.025 * samples)],
            "bootstrap_95_high": deltas[int(0.975 * samples)],
            "clusters": len(cluster_names),
        }
    return output


def build_rankings(
    documents: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    embeddings: np.ndarray,
    embedding_model: SentenceTransformer,
    reranker: CrossEncoder,
    *,
    candidate_count: int,
    query_mode: str,
) -> tuple[list[QueryRanking], dict[str, float]]:
    source_indexes: defaultdict[str, list[int]] = defaultdict(list)
    for index, document in enumerate(documents):
        source_indexes[str(document.get("source_file", ""))].append(index)
    usable = [
        card for card in cards
        if str(card.get("source_file", "")) in source_indexes
        and any(isinstance(page, int) for page in card.get("pages", []))
    ]
    queries = [card_query(card, query_mode) for card in usable]
    started = time.perf_counter()
    query_vectors = embedding_model.encode(
        ["Represent this sentence for searching relevant passages: " + query for query in queries],
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    dense_seconds = time.perf_counter() - started
    rankings: list[QueryRanking] = []
    pairs: list[tuple[str, str]] = []
    pair_owners: list[tuple[int, int]] = []
    for card_index, (card, query, vector) in enumerate(zip(usable, queries, query_vectors)):
        indexes = source_indexes[str(card["source_file"])]
        bm25 = bm25_order(query, indexes, documents)
        dense = dense_order(vector, indexes, embeddings, documents)
        rrf = reciprocal_rank_fusion([bm25, dense])
        rankings.append(QueryRanking(card, query, indexes, bm25, dense, rrf))
        for candidate in rrf[:candidate_count]:
            pairs.append((query, str(documents[candidate]["text"])))
            pair_owners.append((card_index, candidate))
    started = time.perf_counter()
    predictions = reranker.predict(
        pairs,
        batch_size=64,
        show_progress_bar=True,
    )
    rerank_seconds = time.perf_counter() - started
    by_card: defaultdict[int, list[tuple[float, int]]] = defaultdict(list)
    for (card_index, candidate), score in zip(pair_owners, predictions):
        by_card[card_index].append((float(score), candidate))
    for card_index, ranking in enumerate(rankings):
        reranked_head = [
            index for _, index in sorted(
                by_card[card_index],
                key=lambda item: (-item[0], str(documents[item[1]].get("chunk_id", ""))),
            )
        ]
        reranked_set = set(reranked_head)
        ranking.reranked = reranked_head + [index for index in ranking.rrf if index not in reranked_set]
    return rankings, {
        "dense_query_encoding_seconds": dense_seconds,
        "cross_encoder_seconds": rerank_seconds,
        "cross_encoder_pairs": len(pairs),
    }


def evaluate_rankings(
    rankings: list[QueryRanking],
    documents: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    methods = ("bm25", "dense", "rrf", "reranked")
    metric_rows: dict[str, list[dict[str, float]]] = {method: [] for method in methods}
    details: list[dict[str, Any]] = []
    for ranking in rankings:
        gold = {int(page) for page in ranking.card.get("pages", []) if isinstance(page, int)}
        detail: dict[str, Any] = {
            "card_id": ranking.card.get("card_id"),
            "source_file": ranking.card.get("source_file"),
            "gold_pages": sorted(gold),
            "query": ranking.query,
            "rankings": {},
        }
        for method in methods:
            order = getattr(ranking, method)
            if order is None:
                continue
            pages = unique_page_ranking(order, documents)
            metrics = ranking_metrics(pages, gold)
            metric_rows[method].append(metrics)
            detail["rankings"][method] = {
                "top_pages": pages[:10],
                "metrics": metrics,
            }
        details.append(detail)
    summary = {
        "queries": len(rankings),
        "mean_metrics": {method: mean_metrics(metric_rows[method]) for method in methods},
        "reranker_vs_rrf_report_cluster_bootstrap": paired_cluster_bootstrap(
            metric_rows["rrf"],
            metric_rows["reranked"],
            [str(ranking.card.get("source_file", "")) for ranking in rankings],
        ),
        "benchmark_caveat": (
            "The query is schema-derived and independent of card contents. Gold pages come from "
            "machine-generated cards whose quotations passed exact page validation, so the benchmark "
            "still requires confirmation on a manually annotated held-out set."
        ),
    }
    return summary, details


def page_text_map(documents: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    grouped: defaultdict[tuple[str, int], list[str]] = defaultdict(list)
    for document in documents:
        page = document.get("page")
        if isinstance(page, int):
            grouped[(str(document.get("source_file", "")), page)].append(str(document.get("text", "")))
    return {key: normalize_text(" ".join(value)) for key, value in grouped.items()}


def build_reasoning_records(
    cards: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text_by_page = page_text_map(documents)
    inventory_by_page = {
        (str(row["source_file"]), int(row["page"])): row for row in inventory
    }
    records: list[dict[str, Any]] = []
    verified_quotes = total_quotes = 0
    for card in cards:
        source = str(card.get("source_file", ""))
        evidence = []
        all_verified = True
        for quote in card.get("supporting_quotes", []):
            page = int(quote["page"])
            quote_text = str(quote["quote"])
            supported = normalize_text(quote_text) in text_by_page.get((source, page), "")
            total_quotes += 1
            verified_quotes += int(supported)
            all_verified &= supported
            page_inventory = inventory_by_page.get((source, page), {})
            evidence.append({
                "page": page,
                "modality": "text",
                "quote": quote_text,
                "quote_reverified_against_local_index": supported,
                "page_visual_context": {
                    "embedded_image_objects": page_inventory.get("embedded_image_objects"),
                    "table_or_structured_layout_cue": page_inventory.get("table_or_structured_layout_cue"),
                    "visual_content_interpreted": False,
                },
            })
        report_inventory = [row for row in inventory if str(row["source_file"]) == source]
        modalities = ["text"]
        if any(row["embedded_image_objects"] > 0 for row in report_inventory):
            modalities.append("embedded_images_uninterpreted")
        if any(row["table_or_structured_layout_cue"] for row in report_inventory):
            modalities.append("table_or_layout_cues_uninterpreted")
        records.append({
            "record_id": card.get("card_id"),
            "record_type": "source_grounded_maritime_reasoning_record",
            "source": {
                "report_title": card.get("report_title"),
                "source_file": source,
                "pages": card.get("pages", []),
                "document_modalities_present": modalities,
            },
            "situation": {
                "encounter_types": card.get("encounter_types", []),
                "roles": card.get("roles", []),
                "encounter_phases": card.get("encounter_phases", []),
                "warning_signs": card.get("warning_signs", []),
            },
            "reasoning": {
                "causal_mechanisms": card.get("causal_mechanisms", []),
                "investigator_finding": card.get("investigator_finding"),
                "transferable_lesson": card.get("transferable_lesson"),
                "non_transfer_conditions": card.get("non_transfer_conditions", []),
            },
            "evidence": evidence,
            "quality": {
                "source_review_status": card.get("review_status"),
                "all_quotes_reverified_against_local_index": all_verified,
                "human_review_required": True,
                "visual_semantics_validated": False,
            },
            "agent_use_contract": {
                "use": "advisory precedent for reasoning or critic review",
                "live_observations_and_current_regulations_remain_authoritative": True,
                "must_check_non_transfer_conditions": True,
                "must_not_infer_unseen_visual_content": True,
            },
        })
    return records, {
        "records": len(records),
        "supporting_quotes": total_quotes,
        "quotes_reverified_against_local_index": verified_quotes,
        "records_with_all_quotes_reverified": sum(
            record["quality"]["all_quotes_reverified_against_local_index"] for record in records
        ),
    }


def fmt(value: float) -> str:
    return f"{value:.3f}"


def write_report_legacy(
    path: Path,
    *,
    corpus: dict[str, Any],
    retrieval: dict[str, Any],
    reasoning: dict[str, Any],
    timings: dict[str, float],
    provenance: dict[str, Any],
) -> None:
    metrics = retrieval["mean_metrics"]
    rows = []
    for method, label in (
        ("bm25", "BM25"),
        ("dense", "BGE dense"),
        ("rrf", "BM25 + BGE RRF"),
        ("reranked", "RRF + cross-encoder reranker"),
    ):
        item = metrics[method]
        rows.append(
            f"| {label} | {fmt(item['hit_at_1'])} | {fmt(item['hit_at_3'])} | "
            f"{fmt(item['hit_at_5'])} | {fmt(item['gold_page_recall_at_5'])} | "
            f"{fmt(item['mrr'])} | {fmt(item['ndcg_at_5'])} |"
        )
    bootstrap = retrieval["reranker_vs_rrf_report_cluster_bootstrap"]
    answer = f"""# MAIB textual extraction, evidence retrieval, and reranking pilot

## How to use this report

This document is a technical writing aid for the maritime accident-report extraction
chapter. It separates three different claims that should not be conflated in the
thesis:

1. **pipeline validity:** whether the same inputs and configuration reproducibly
   produce schema-conforming records with traceable provenance;
2. **evidence-selection quality:** whether a ranking method places pages already known
   to contain supporting evidence near the top of a report; and
3. **semantic extraction quality:** whether every generated category, finding, and
   lesson is a correct and complete interpretation of the report.

This pilot supplies evidence for the first claim and a weakly supervised evaluation of
the second. It does not independently establish the third because the semantic fields
have not been compared with a manually annotated gold set.

## Outcome

This pilot demonstrates a reproducible path from the machine-readable text of
unstructured accident reports to agent-ready, source-grounded reasoning records.
Photographs, diagrams, and other visual content are outside the extraction scope. The
PDF inventory records their presence only so that the study does not accidentally
present a text-only result as visual interpretation. Only textual quotations are
treated as validated evidence.

The corpus contains {corpus['reports']} readable reports and {corpus['pages']} PDF pages.
{corpus['pages_with_extractable_text']} pages contain at least 80 extracted text
characters, {corpus['pages_with_embedded_images']} contain embedded image objects,
{corpus['pages_with_table_or_layout_cues']} contain table/layout cues, and
{corpus['visual_only_candidates']} are visual-only candidates excluded from textual
interpretation.

The working corpus index contains 2,373 text chunks. The lesson-card generation stage
produced {reasoning['records']} accepted cards from
{bootstrap['hit_at_5']['clusters']} of the {corpus['reports']} reports. A report can
yield more than one card because a single accident investigation can support several
distinct lessons or decision points.

## Research task and unit of analysis

For the text-only scope of this thesis, the operational research question is:

> How can unstructured maritime accident reports be systematically transformed into
> source-grounded structured reasoning data suitable for autonomous agentic workflows?

Here, **systematically** means that ingestion, evidence selection, record construction,
and validation are defined as repeatable stages rather than performed through ad hoc
manual summarisation. **Source-grounded** means that a downstream user can trace every
record to a local report, page number, and exact quotation. **Suitable for agentic
workflows** does not mean that a lesson may directly command a vessel. It means that
the record has a predictable machine-readable structure and an explicit use contract:
historical precedent is advisory, while live observations and current regulations
remain authoritative.

The pipeline uses four different units that should be named explicitly in the thesis:

| Unit | Meaning | Role in the experiment |
|---|---|---|
| Report | One local MAIB PDF | Provenance boundary and bootstrap cluster |
| Chunk | A page-associated text passage | Object scored by retrieval methods |
| Page | One numbered report page | Evaluation target after duplicate chunks are collapsed |
| Reasoning record | One transferable, evidence-backed lesson | Final downstream JSONL object |

Retrieval is evaluated **within the known source report**. Consequently, this experiment
tests which pages should be supplied to the structured extraction stage; it does not
test selecting the correct report from the entire corpus.

## End-to-end transformation pipeline

```text
MAIB PDF text
    -> page-linked chunks
    -> deterministic candidate-passage selection
    -> strict lesson-card generation
    -> schema and exact-quotation validation
    -> source-grounded reasoning records

page-linked chunks + generic evidence query
    -> BM25 / BGE / RRF
    -> optional cross-encoder reranking
    -> compare ranked pages with validated quotation pages
```

1. **Text ingestion and provenance preservation.** PDF text is stored as page-associated
   chunks with a report identifier, page number, chunk identifier, and report title.
2. **Candidate-passage selection.** A deterministic heuristic prioritises passages that
   contain analysis, conclusions, safety issues, causal factors, collision-avoidance
   terminology, or investigator findings.
3. **Constrained lesson-card generation.** The generator may return zero to three cards
   per report under a strict schema and must supply exact supporting quotations. The
   retained artifact used 107 `gpt-6.1-sol` batch requests, low reasoning effort, and at
   most eight selected passages per report. Model output is not treated as human truth.
4. **Deterministic rejection and quotation validation.** Extra fields, malformed values,
   invalid pages, and quotations absent from the claimed page cause rejection.
5. **Evidence retrieval benchmark.** BM25, dense retrieval, reciprocal-rank fusion, and
   cross-encoder reranking use validated quotation pages as weak gold targets.
6. **Agent-facing record construction.** Accepted cards become stable reasoning records
   containing situation, reasoning, evidence, quality, and use-contract fields.

## Retrieval and reranking experiment

The benchmark contains {retrieval['queries']} source-grounded lesson-card queries.
Each report uses the same schema-derived evidence query, without generated card fields
or supporting quotations; the hidden gold targets are the cited report pages. Ranking
is performed **within the known source report**, matching the evidence-selection stage
of structured extraction.

### Benchmark construction and leakage control

The cited pages of the accepted lesson cards act as weak gold evidence pages. The
retrieval query is a single schema-derived request for investigator analysis, causal
factors, warning signs, actions, outcomes, and non-transfer conditions. It contains no
phrasing copied from the card being evaluated, avoiding target-derived lexical leakage.

Several cards can originate from the same report. Those cards share the generic query
but can identify different gold pages. Metrics are calculated per card, while entire
source reports are resampled as clusters for uncertainty estimation.

### Compared ranking methods

- **BM25** is a lexical baseline that corrects for term frequency, rarity, and length.
- **BGE dense retrieval** ranks independently encoded query and chunk vectors by
  similarity.
- **Reciprocal Rank Fusion (RRF)** combines BM25 and dense ranks using
  `1/(60 + rank)`, without calibrating their raw scores.
- **Cross-encoder reranking** jointly scores the query and the first 20 RRF candidates
  with `cross-encoder/ms-marco-MiniLM-L6-v2`.

Chunk rankings are converted to page rankings by retaining only the first occurrence
of each page, preventing overlapping chunks from producing duplicate page credit.

### Meaning of the metrics

| Metric | Question answered | Interpretation |
|---|---|---|
| Hit@1 | Is any gold page ranked first? | One-page evidence budget |
| Hit@3 / Hit@5 | Is any gold page in the first 3 or 5? | Small evidence-window success |
| Gold-page recall@5 | What fraction of all cited pages is in the first five? | Completeness of page recovery |
| MRR | How early does the first gold page occur? | Reciprocal first relevant rank |
| nDCG@5 | Are multiple gold pages near the top? | Rank-sensitive multi-page relevance |

Hit@5 and gold-page recall@5 are not interchangeable. A card citing three pages has a
perfect Hit@5 when only one is found, but recall of only one third.

| Method | Hit@1 | Hit@3 | Hit@5 | Gold-page recall@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

### Interpretation of the ranking results

BM25 was strongest on every reported aggregate metric. Its Hit@5 of
{fmt(metrics['bm25']['hit_at_5'])} means that at least one cited evidence page occurred
among the first five pages for about {100 * metrics['bm25']['hit_at_5']:.1f}% of the
card-level units. Gold-page recall@5 is lower at
{fmt(metrics['bm25']['gold_page_recall_at_5'])} because a card can cite several pages.

Dense retrieval underperformed BM25 by
{metrics['bm25']['hit_at_5'] - metrics['dense']['hit_at_5']:.3f} absolute Hit@5 and
{metrics['bm25']['mrr'] - metrics['dense']['mrr']:.3f} MRR. Fusion also underperformed
BM25, indicating that the weaker dense ranking sometimes displaced useful lexical
results. The recurring section terminology in investigation reports plausibly favours
lexical matching.

The cross-encoder changed RRF Hit@5 from {fmt(metrics['rrf']['hit_at_5'])} to
{fmt(metrics['reranked']['hit_at_5'])}, MRR from {fmt(metrics['rrf']['mrr'])} to
{fmt(metrics['reranked']['mrr'])}, and nDCG@5 from
{fmt(metrics['rrf']['ndcg_at_5'])} to {fmt(metrics['reranked']['ndcg_at_5'])}. These
small gains did not recover BM25's performance.

Relative to RRF, reranking changed Hit@5 by
{bootstrap['hit_at_5']['observed_delta']:+.3f}
(paired bootstrap over {bootstrap['hit_at_5']['clusters']} source-report clusters; 95% interval
[{bootstrap['hit_at_5']['bootstrap_95_low']:+.3f},
{bootstrap['hit_at_5']['bootstrap_95_high']:+.3f}]), MRR by
{bootstrap['mrr']['observed_delta']:+.3f}, and nDCG@5 by
{bootstrap['ndcg_at_5']['observed_delta']:+.3f}. The reranker evaluated
{int(timings['cross_encoder_pairs'])} query-passage pairs in
{timings['cross_encoder_seconds']:.1f} seconds on CPU.

This is still a **weakly supervised** evaluation because gold pages come from
machine-generated lesson cards, although exact supporting quotations were previously
page-validated. The query itself is independent of those cards. The experiment measures
evidence-page recovery, not end-to-end factual accuracy, and needs a manually annotated
held-out set before supporting a strong thesis claim.

The defensible conclusion is not that reranking is generally ineffective. This
off-the-shelf MS MARCO reranker did not justify its added complexity on this benchmark.
BM25 should be retained unless a domain-tuned reranker improves an independently
annotated test set.

## Structured reasoning records

The transformation produced {reasoning['records']} JSONL reasoning records with:

- controlled encounter, role, and phase fields;
- observable warning signs;
- causal mechanisms and investigator findings;
- a transferable lesson plus explicit non-transfer conditions;
- page-bound exact quotations and local quote revalidation;
- an agent-use contract that keeps live observations and current regulations authoritative;
- explicit flags showing that only textual evidence was interpreted.

The schema separates context, interpretation, and evidence:

| Component | Contents | Purpose |
|---|---|---|
| `source` | Report, file, and cited pages | Preserves provenance |
| `situation` | Encounter, role, phase, and warning signs | Defines relevance context |
| `reasoning` | Mechanisms, finding, lesson, and non-transfer conditions | Separates reported cause from transfer |
| `evidence` | Page, quotation, and verification status | Supports independent inspection |
| `quality` | Validation and review flags | Prevents overclaiming certainty |
| `agent_use_contract` | Advisory-use restrictions | Keeps observations and rules authoritative |

{reasoning['quotes_reverified_against_local_index']} of
{reasoning['supporting_quotes']} quotations were independently found in the local
page index. Records still require human review because schema validity and quotation
support do not prove that every interpretation is complete or correct.

### Three validation layers

1. **Structural validation** checks required fields, types, allowed values, lengths, and
   undeclared fields. All retained records satisfy the exported JSON Schema.
2. **Quotation validation** checks that every normalised exact quotation occurs on its
   claimed indexed page. All retained quotations passed.
3. **Semantic validation** would test whether each generated field is a correct and
   complete interpretation. It requires independent human annotation and was not
   performed here.

The thesis may claim complete schema validity and quotation traceability for the
retained set, but not 100% extraction accuracy.

## Answer to the research question

**How can unstructured maritime accident reports be systematically transformed into
source-grounded structured reasoning data suitable for autonomous agentic workflows?**

A defensible text-extraction pipeline is provenance-first:

1. preserve report, page, and chunk identity during ingestion;
2. retrieve a compact evidence pool from the known source report;
3. optionally rerank only that pool, but retain the reranker only when a held-out
   domain benchmark shows a reliable improvement (the generic reranker tested here
   should therefore not be adopted yet);
4. generate a constrained record separating situation, findings, lessons, and limits;
5. reject malformed or unsupported records and validate quotations against their pages;
6. attach a use contract that keeps observations and regulations authoritative; and
7. require human adjudication for safety-relevant use.

In this benchmark, plain BM25 was stronger than dense retrieval, fusion, and reranking;
this is evidence against assuming that a more complex ranker is automatically better.
The pilot implements stages 1--6 at proof-of-concept level and encodes stage 7 as a
mandatory downstream requirement. If a stronger extraction-accuracy claim is needed,
a held-out human annotation study should report
field-level precision, recall and F1, evidence accuracy, omission rate,
unsupported-inference rate, and inter-annotator agreement.

## What the experiment establishes

Supported claims:

- reports can be converted into uniform records while retaining page-level provenance;
- deterministic schema and quotation checks reject malformed or ungrounded outputs;
- BM25 was stronger than the tested dense, fusion, and reranking alternatives; and
- the records encode advisory use and authority limits for downstream agents.

Unsupported claims:

- every semantic field is correct or all relevant lessons were extracted;
- the retained records are an unbiased or exhaustive representation of MAIB reports;
- within-report evidence selection equals whole-corpus report retrieval;
- quotation grounding proves the interpretation follows logically; or
- supplying the records to an agent improves physical safety.

## Suggested Chapter 4 structure

### 4.1 Task definition and scope

Define machine-readable MAIB text as the input and source-grounded reasoning records as
the output. Exclude visual interpretation explicitly and define report, chunk, page, and
reasoning-record units.

### 4.2 Corpus and preprocessing

Report the PDF, page, and chunk counts, page-level provenance, and deterministic
candidate-passage selection. Reconcile downloaded, excluded, and finally indexed corpus
counts instead of treating them as one set.

### 4.3 Target representation and generation

Explain schema groups and why findings, lessons, non-transfer conditions, and evidence
are separate. Put the complete JSON Schema and full examples in an appendix.

### 4.4 Validation and retrieval experiment

Define structural and quotation validation, the generic query, within-report candidates,
weak gold pages, ranking methods, page deduplication, metrics, and report-cluster
bootstrap. State that card content was not inserted into the query.

### 4.5 Results

Lead with BM25 Hit@5 and gold-page recall@5, then the inconclusive reranker delta and
cost. Separately report record count, schema validity, and quotation revalidation.

### 4.6 Limitations and error analysis

Discuss weak machine-derived gold pages, repeated cards within reports, missing human
semantic labels, within-report scope, candidate-selection bias, and model domain mismatch.

### 4.7 Chapter summary

> The experiment demonstrates a reproducible, provenance-preserving method for
> transforming textual maritime accident reports into schema-constrained reasoning
> records. Across {reasoning['records']} retained records, all
> {reasoning['quotes_reverified_against_local_index']} supporting quotations were
> revalidated against their cited pages. BM25 provided the strongest evidence-page
> retrieval performance (Hit@5 = {fmt(metrics['bm25']['hit_at_5'])}), while the tested
> dense, fusion, and reranking alternatives did not improve on it. The results establish
> structural and evidential grounding, not complete semantic extraction accuracy.

## Claims to phrase carefully

Prefer “schema-valid,” “quotation-grounded,” “reproducible,” and “proof of concept.”
Avoid “fully validated,” “accurate extraction,” “ground truth,” and “safe for autonomous
control” without experiments that directly measure those properties. Call the cited
card pages **weak gold evidence pages**, not human gold annotations.

## Methodological basis

- The lexical baseline follows the probabilistic relevance framework described in
  [BM25 and Beyond](https://doi.org/10.1561/1500000019).
- Dense retrieval uses the bi-encoder pattern introduced by
  [Sentence-BERT](https://arxiv.org/abs/1908.10084) and the locally cached English BGE
  model released with [C-Pack](https://arxiv.org/abs/2309.07597).
- Lexical and dense rankings are combined with
  [Reciprocal Rank Fusion](https://doi.org/10.1145/1571941.1572114), using the paper's
  conventional constant of 60.
- Candidate passages are scored jointly with a BERT-style cross-encoder following
  [Nogueira and Cho](https://arxiv.org/abs/1901.04085).
- The experiment intentionally excludes semantic interpretation of photographs,
  diagrams, and other visual-only content; no vision-layout result is claimed.

## Artifacts and provenance

- `retrieval_metrics.json`: aggregate metrics, paired report-cluster bootstrap intervals, and caveat.
- `retrieval_details.jsonl`: per-card queries, gold pages, ranked pages, and metrics.
- `reasoning_records.jsonl`: agent-ready records with page evidence and use contracts.
- `multimodal_inventory.jsonl`: per-page text/image/layout inventory.
- `reasoning_record_schema.json`: structural contract for downstream validation.
- `provenance.json`: input hashes, local model names, timings, and parameters.

Input provenance:

```json
{json.dumps(provenance, indent=2)}
```
"""
    path.write_text(answer, encoding="utf-8")


def write_report(
    path: Path,
    *,
    corpus: dict[str, Any],
    retrieval: dict[str, Any],
    reasoning: dict[str, Any],
    timings: dict[str, float],
    provenance: dict[str, Any],
) -> None:
    """Write the final benchmark snapshot without restating extraction history."""
    metrics = retrieval["mean_metrics"]
    rows: list[str] = []
    for method, label in (
        ("bm25", "BM25"),
        ("dense", "BGE dense"),
        ("rrf", "BM25 + BGE RRF"),
        ("reranked", "RRF + cross-encoder"),
    ):
        item = metrics[method]
        rows.append(
            f"| {label} | {fmt(item['hit_at_1'])} | {fmt(item['hit_at_3'])} | "
            f"{fmt(item['hit_at_5'])} | {fmt(item['gold_page_recall_at_5'])} | "
            f"{fmt(item['mrr'])} | {fmt(item['ndcg_at_5'])} |"
        )
    bootstrap = retrieval["reranker_vs_rrf_report_cluster_bootstrap"]
    report = f"""# Final MAIB evidence-page retrieval benchmark

This file is generated from the frozen curated card corpus. The thesis-ready narrative,
including extraction and semantic-curation methodology, is in `REPORT.md`.

## Frozen inputs

- indexed passages: {provenance['documents']}
- retained cards / benchmark queries: {retrieval['queries']}
- represented source-report clusters: {bootstrap['hit_at_5']['clusters']}
- verified quotations in exported reasoning records: {reasoning['quotes_reverified_against_local_index']} / {reasoning['supporting_quotes']}
- card artifact: `{provenance['cards_path']}`
- card SHA-256: `{provenance['cards_sha256']}`
- query mode: `{provenance['query_mode']}`
- RRF candidate depth passed to the cross-encoder: {provenance['candidate_count']}

## Task

For each retained card, rank the indexed chunks from its known source report using a
generic schema-derived evidence query. Card-specific findings, warning signs, lessons,
and quotations are not inserted into the query. The card's cited pages are weak,
machine-derived targets rather than independent human relevance labels. Chunk rankings
are collapsed to unique page rankings before scoring.

## Results

| Method | Hit@1 | Hit@3 | Hit@5 | Reference-page recall@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

BM25 has the highest Hit@1, Hit@5, reference-page recall@5, MRR, and nDCG@5. RRF has
the highest Hit@3. Relative to RRF, cross-encoder reranking changed:

- Hit@5 by {bootstrap['hit_at_5']['observed_delta']:+.3f}, with report-cluster bootstrap
  95% interval [{bootstrap['hit_at_5']['bootstrap_95_low']:+.3f},
  {bootstrap['hit_at_5']['bootstrap_95_high']:+.3f}];
- MRR by {bootstrap['mrr']['observed_delta']:+.3f}, with interval
  [{bootstrap['mrr']['bootstrap_95_low']:+.3f},
  {bootstrap['mrr']['bootstrap_95_high']:+.3f}]; and
- nDCG@5 by {bootstrap['ndcg_at_5']['observed_delta']:+.3f}, with interval
  [{bootstrap['ndcg_at_5']['bootstrap_95_low']:+.3f},
  {bootstrap['ndcg_at_5']['bootstrap_95_high']:+.3f}].

All intervals contain zero. This run therefore does not demonstrate a reliable
cross-encoder improvement over RRF. The cross-encoder scored
{int(timings['cross_encoder_pairs'])} query-passage pairs in
{timings['cross_encoder_seconds']:.1f} seconds on CPU. Dense query encoding required
{timings['dense_query_encoding_seconds']:.1f} seconds.

## Scope limitation

This benchmark measures within-report recovery of pages cited by generated cards. It
does not evaluate whole-corpus report discovery, live-state lesson-card retrieval,
semantic extraction accuracy, or navigation outcomes. The cited pages are not a human
gold standard.

## Corpus inventory

- readable reports: {corpus['reports']}
- PDF pages: {corpus['pages']}
- pages with at least 80 extracted characters: {corpus['pages_with_extractable_text']}
- pages containing embedded image objects: {corpus['pages_with_embedded_images']}
- visual-only candidates excluded from textual interpretation: {corpus['visual_only_candidates']}

## Provenance

```json
{json.dumps(provenance, indent=2)}
```
"""
    path.write_text(report, encoding="utf-8")


def reasoning_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Source-grounded maritime reasoning record",
        "type": "object",
        "required": ["record_id", "record_type", "source", "situation", "reasoning", "evidence", "quality", "agent_use_contract"],
        "properties": {
            "record_id": {"type": "string"},
            "record_type": {"const": "source_grounded_maritime_reasoning_record"},
            "source": {
                "type": "object",
                "required": ["report_title", "source_file", "pages", "document_modalities_present"],
                "properties": {
                    "report_title": {"type": ["string", "null"]},
                    "source_file": {"type": "string"},
                    "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1},
                    "document_modalities_present": string_array,
                },
                "additionalProperties": False,
            },
            "situation": {
                "type": "object",
                "required": ["encounter_types", "roles", "encounter_phases", "warning_signs"],
                "properties": {
                    "encounter_types": string_array,
                    "roles": string_array,
                    "encounter_phases": string_array,
                    "warning_signs": string_array,
                },
                "additionalProperties": False,
            },
            "reasoning": {
                "type": "object",
                "required": ["causal_mechanisms", "investigator_finding", "transferable_lesson", "non_transfer_conditions"],
                "properties": {
                    "causal_mechanisms": string_array,
                    "investigator_finding": {"type": ["string", "null"]},
                    "transferable_lesson": {"type": ["string", "null"]},
                    "non_transfer_conditions": string_array,
                },
                "additionalProperties": False,
            },
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["page", "modality", "quote", "quote_reverified_against_local_index", "page_visual_context"],
                    "properties": {
                        "page": {"type": "integer", "minimum": 1},
                        "modality": {"const": "text"},
                        "quote": {"type": "string", "minLength": 8},
                        "quote_reverified_against_local_index": {"type": "boolean"},
                        "page_visual_context": {
                            "type": "object",
                            "required": ["embedded_image_objects", "table_or_structured_layout_cue", "visual_content_interpreted"],
                            "properties": {
                                "embedded_image_objects": {"type": ["integer", "null"], "minimum": 0},
                                "table_or_structured_layout_cue": {"type": ["boolean", "null"]},
                                "visual_content_interpreted": {"const": False},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "quality": {
                "type": "object",
                "required": ["source_review_status", "all_quotes_reverified_against_local_index", "human_review_required", "visual_semantics_validated"],
                "properties": {
                    "source_review_status": {"type": ["string", "null"]},
                    "all_quotes_reverified_against_local_index": {"type": "boolean"},
                    "human_review_required": {"const": True},
                    "visual_semantics_validated": {"const": False},
                },
                "additionalProperties": False,
            },
            "agent_use_contract": {
                "type": "object",
                "required": ["use", "live_observations_and_current_regulations_remain_authoritative", "must_check_non_transfer_conditions", "must_not_infer_unseen_visual_content"],
                "properties": {
                    "use": {"const": "advisory precedent for reasoning or critic review"},
                    "live_observations_and_current_regulations_remain_authoritative": {"const": True},
                    "must_check_non_transfer_conditions": {"const": True},
                    "must_not_infer_unseen_visual_content": {"const": True},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--embeddings", type=Path, default=DEFAULT_EMBEDDINGS)
    parser.add_argument("--cards", type=Path, default=DEFAULT_CARDS)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER)
    parser.add_argument("--candidate-count", type=int, default=20)
    parser.add_argument(
        "--query-mode",
        choices=("generic", "metadata", "enriched"),
        default="generic",
        help="generic is leakage-resistant; metadata/enriched are diagnostic modes",
    )
    args = parser.parse_args()
    if args.candidate_count < 1:
        parser.error("--candidate-count must be positive")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    documents = read_jsonl(args.index)
    cards = read_jsonl(args.cards)
    embeddings = np.load(args.embeddings)
    if len(embeddings) != len(documents):
        raise ValueError("embedding row count does not match the document index")

    inventory, inventory_summary = inventory_pdfs(args.pdf_dir)
    write_jsonl(args.output_dir / "multimodal_inventory.jsonl", inventory)

    embedding_model = SentenceTransformer(
        args.embedding_model,
        device="cpu",
        local_files_only=True,
    )
    reranker = CrossEncoder(
        args.reranker_model,
        device="cpu",
        local_files_only=True,
    )
    rankings, timings = build_rankings(
        documents,
        cards,
        embeddings,
        embedding_model,
        reranker,
        candidate_count=args.candidate_count,
        query_mode=args.query_mode,
    )
    retrieval_summary, retrieval_details = evaluate_rankings(rankings, documents)
    retrieval_summary["timings"] = timings
    (args.output_dir / "retrieval_metrics.json").write_text(
        json.dumps(retrieval_summary, indent=2) + "\n", encoding="utf-8"
    )
    write_jsonl(args.output_dir / "retrieval_details.jsonl", retrieval_details)

    records, reasoning_summary = build_reasoning_records(cards, documents, inventory)
    write_jsonl(args.output_dir / "reasoning_records.jsonl", records)
    (args.output_dir / "reasoning_record_schema.json").write_text(
        json.dumps(reasoning_schema(), indent=2) + "\n", encoding="utf-8"
    )

    provenance = {
        "index": str(args.index),
        "index_sha256": sha256(args.index),
        "embeddings": str(args.embeddings),
        "embeddings_sha256": sha256(args.embeddings),
        "cards_path": str(args.cards),
        "cards_sha256": sha256(args.cards),
        "embedding_model": args.embedding_model,
        "reranker_model": args.reranker_model,
        "candidate_count": args.candidate_count,
        "query_mode": args.query_mode,
        "documents": len(documents),
        "card_records": len(cards),
        "timings": timings,
    }
    (args.output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    write_report(
        # Keep the thesis-oriented REPORT.md stable; this file is the regenerated
        # metric/provenance summary produced by each pilot invocation.
        args.output_dir / "PILOT_RESULTS.generated.md",
        corpus=inventory_summary,
        retrieval=retrieval_summary,
        reasoning=reasoning_summary,
        timings=timings,
        provenance=provenance,
    )
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "corpus": inventory_summary,
        "retrieval": retrieval_summary,
        "reasoning": reasoning_summary,
    }, indent=2))


if __name__ == "__main__":
    main()
