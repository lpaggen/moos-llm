#!/usr/bin/env python3
"""Prepare and score a blinded Imazu state-to-MAIB-card retrieval evaluation.

The preparation stage is entirely offline. It selects the first retrieval-
eligible state in each archived Imazu case, reconstructs and verifies the live
query, ranks the final retained lesson-card corpus four ways, and exports a
deduplicated CSV for human relevance annotation. The score stage accepts only
complete labels in {0, 1, 2} and reports Precision@2, Hit@2, and graded nDCG@2.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import random
import shutil
import sys
from typing import Any, Iterable, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.maib import (  # noqa: E402
    MaibRetriever,
    natural_query_from_state,
    retrieval_preferences_from_state,
)
from tools.build_maib_lesson_cards import card_retrieval_text  # noqa: E402


DEFAULT_LOG_ROOT = Path(
    "logs/evaluations/imazu/"
    "maib_lesson_cards_captain_review_prompt_fixed_full_warp5"
)
DEFAULT_CARDS = Path(
    "accidents/cards_decision_style_curated/cards.retained.jsonl"
)
DEFAULT_OUTPUT = Path("reports/maib_state_retrieval_benchmark")
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
METHODS = ("bm25", "bge_dense", "rrf", "rrf_preferences")
METHOD_TITLES = {
    "bm25": "BM25",
    "bge_dense": "BGE dense",
    "rrf": "BM25 + BGE (RRF)",
    "rrf_preferences": "RRF + encounter preferences",
}
LABEL_FIELDS = (
    "annotation_id",
    "state_id",
    "navigation_state",
    "card_finding",
    "transferable_lesson",
    "non_transfer_conditions",
    "supporting_quotations",
    "relevance_label",
    "reviewer_notes",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            records.append(value)
    return records


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cached_model_revision(model_name: str) -> str:
    reference = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / ("models--" + model_name.replace("/", "--"))
        / "refs"
        / "main"
    )
    if not reference.exists():
        raise FileNotFoundError(
            f"offline evaluation requires a cached main revision: {reference}"
        )
    revision = reference.read_text(encoding="utf-8").strip()
    if not revision:
        raise ValueError(f"empty cached model revision: {reference}")
    return revision


def number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def relevant_contacts(
    bridge_state: dict[str, Any], clearance_m: float
) -> list[dict[str, Any]]:
    """Mirror AgentCaptain.navigation_relevant_contacts without policy labels."""
    selected = []
    for contact in bridge_state.get("contacts", []):
        if not isinstance(contact, dict):
            continue
        if (
            number(contact.get("closing_speed"), 0.0) > 0.0
            and number(contact.get("tcpa_s"), -1.0) >= 0.0
            and number(contact.get("cpa_distance"), math.inf) < clearance_m
        ):
            selected.append(contact)
    return selected


def event_for_cycle(
    events: Sequence[dict[str, Any]], event_name: str, cycle_id: Any
) -> dict[str, Any]:
    matches = [
        event
        for event in events
        if event.get("event") == event_name and event.get("cycle_id") == cycle_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {event_name!r} event for cycle {cycle_id}, "
            f"found {len(matches)}"
        )
    return matches[0]


def select_states(log_root: Path) -> list[dict[str, Any]]:
    """Select and verify the first retrieval-eligible cycle in each case."""
    states: list[dict[str, Any]] = []
    for scenario in range(1, 23):
        audit_path = log_root / f"case_{scenario:02d}" / "audit.jsonl"
        if not audit_path.exists():
            raise FileNotFoundError(f"missing Imazu audit: {audit_path}")
        events = read_jsonl(audit_path)
        retrievals = [
            event
            for event in events
            if event.get("event") == "maib_retrieval"
            and (event.get("routing") or {}).get("method")
            == "numeric_observations_only"
        ]
        if not retrievals:
            raise ValueError(f"case {scenario:02d} has no retrieval-eligible state")
        retrieval = retrievals[0]
        cycle_id = retrieval.get("cycle_id")
        snapshot = event_for_cycle(events, "bridge_snapshot", cycle_id)
        oow = event_for_cycle(events, "oow_decision", cycle_id)
        bridge_state = snapshot.get("state")
        proposal = oow.get("decision")
        if not isinstance(bridge_state, dict) or not isinstance(proposal, dict):
            raise ValueError(f"case {scenario:02d} cycle {cycle_id} lacks state/proposal")

        decision_state = {"oow_proposal": proposal}
        reconstructed_query = natural_query_from_state(decision_state, bridge_state)
        logged_query = retrieval.get("query")
        if reconstructed_query != logged_query:
            raise ValueError(
                f"case {scenario:02d} cycle {cycle_id}: reconstructed query "
                "does not equal the archived runtime query"
            )

        routing = retrieval.get("routing") or {}
        clearance_m = number(routing.get("clearance_m"), math.nan)
        if not math.isfinite(clearance_m):
            raise ValueError(f"case {scenario:02d}: invalid retrieval clearance")
        contacts = relevant_contacts(bridge_state, clearance_m)
        preferences = {
            key: sorted(values)
            for key, values in retrieval_preferences_from_state(
                bridge_state, contacts
            ).items()
        }
        if preferences != routing.get("metadata_preferences"):
            raise ValueError(
                f"case {scenario:02d} cycle {cycle_id}: reconstructed "
                "preferences do not equal the archived runtime preferences"
            )
        if len(contacts) != len(routing.get("contacts") or []):
            raise ValueError(
                f"case {scenario:02d} cycle {cycle_id}: reconstructed contact "
                "gate does not equal the archived runtime gate"
            )

        states.append(
            {
                "scenario_id": f"imazu-{scenario:02d}",
                "cycle_id": cycle_id,
                "timestamp": retrieval.get("timestamp"),
                "selection_rule": "first archived numeric_observations_only retrieval",
                "audit_path": str(audit_path),
                "query": reconstructed_query,
                "oow_proposal": proposal,
                "retrieval_clearance_m": clearance_m,
                "relevant_contacts": contacts,
                "metadata_preferences": preferences,
                "bridge_state": bridge_state,
            }
        )
    if len(states) != 22:
        raise AssertionError(f"expected 22 selected states, got {len(states)}")
    return states


def export_card_index(cards_path: Path, index_path: Path) -> list[dict[str, Any]]:
    """Render the retained 503-card set in the existing runtime schema."""
    cards = read_jsonl(cards_path)
    if len(cards) != 503:
        raise ValueError(f"expected final corpus of 503 cards, got {len(cards)}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, card in enumerate(cards, start=1):
        identifier = str(card.get("card_id") or "")
        if not identifier or identifier in seen:
            raise ValueError(
                f"missing or duplicate card_id at {cards_path}:{line_number}"
            )
        seen.add(identifier)
        pages = sorted({int(page) for page in card.get("pages", [])})
        records.append(
            {
                "chunk_id": identifier,
                "report_title": str(card.get("report_title") or "Untitled MAIB report"),
                "source_file": str(card.get("source_file") or ""),
                "page": pages[0] if pages else None,
                "pages": pages,
                "source_type": "published_investigation_lesson_card",
                "review_status": str(card.get("review_status") or ""),
                "encounter_types": list(card.get("encounter_types") or []),
                "roles": list(card.get("roles") or []),
                "encounter_phases": list(card.get("encounter_phases") or []),
                "causal_mechanisms": list(card.get("causal_mechanisms") or []),
                "text": card_retrieval_text(card),
            }
        )
    write_jsonl(index_path, records)
    return records


def embed_card_index(
    records: Sequence[dict[str, Any]],
    output_path: Path,
    embedding_model: str,
    batch_size: int,
) -> np.ndarray:
    """Embed using the same passage wrapper and normalisation as embed_maib.py."""
    from sentence_transformers import SentenceTransformer

    passages = [
        f"Report: {doc.get('report_title', '')}. Page: {doc.get('page', '')}. "
        f"Passage: {doc['text']}"
        for doc in records
    ]
    model = SentenceTransformer(
        embedding_model,
        device="cpu",
        local_files_only=True,
    )
    matrix = model.encode(
        passages,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).astype(np.float32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(output_path, matrix)
    return matrix


@dataclass(frozen=True)
class RankedCard:
    card_id: str
    source_file: str
    score: float
    bm25_score: float
    dense_score: float | None
    bm25_rank: int | None
    dense_rank: int | None

    def as_dict(self, rank: int) -> dict[str, Any]:
        return {
            "rank": rank,
            "card_id": self.card_id,
            "source_file": self.source_file,
            "score": round(self.score, 8),
            "bm25_score": round(self.bm25_score, 6),
            "dense_score": (
                None if self.dense_score is None else round(self.dense_score, 6)
            ),
            "bm25_rank": self.bm25_rank,
            "dense_rank": self.dense_rank,
        }


def select_diverse(
    retriever: MaibRetriever,
    scored: Sequence[tuple[float, int]],
    bm25: Sequence[float],
    dense: Sequence[float] | None,
    bm25_rank: dict[int, int],
    dense_rank: dict[int, int],
    limit: int = 2,
) -> list[RankedCard]:
    selected: list[RankedCard] = []
    seen_sources: set[str] = set()
    for score, index in scored:
        document = retriever.documents[index]
        source = str(document.get("source_file") or "")
        if source in seen_sources:
            continue
        selected.append(
            RankedCard(
                card_id=str(document.get("chunk_id")),
                source_file=source,
                score=float(score),
                bm25_score=float(bm25[index]),
                dense_score=None if dense is None else float(dense[index]),
                bm25_rank=bm25_rank.get(index),
                dense_rank=dense_rank.get(index),
            )
        )
        seen_sources.add(source)
        if len(selected) == limit:
            break
    if len(selected) != limit:
        raise ValueError(f"retrieval returned {len(selected)} results, expected {limit}")
    return selected


def rank_query(
    retriever: MaibRetriever,
    query: str,
    preferences: dict[str, set[str]],
    *,
    min_score: float,
) -> dict[str, list[RankedCard]]:
    """Run four ablations using the existing retriever's scoring primitives."""
    if retriever.embeddings is None or retriever.embedding_model is None:
        raise ValueError("dense embeddings and local embedding model are required")
    bm25 = retriever._bm25_scores(query)
    bm25_order = sorted(
        range(len(bm25)),
        key=lambda index: (
            -bm25[index],
            str(retriever.documents[index].get("chunk_id", "")),
        ),
    )
    bm25_rank = {
        index: rank for rank, index in enumerate(bm25_order[:50], start=1)
    }

    vector = retriever.embedding_model.encode(
        "Represent this sentence for searching relevant passages: " + query,
        normalize_embeddings=True,
    )
    dense = retriever.embeddings @ vector
    dense_order = sorted(
        range(len(dense)),
        key=lambda index: (
            -float(dense[index]) * retriever._evidence_weight(index),
            index,
        ),
    )
    dense_rank = {
        index: rank for rank, index in enumerate(dense_order[:50], start=1)
    }

    lexical_scored = [
        (float(bm25[index]), index)
        for index in bm25_order[:50]
        if bm25[index] >= min_score
    ]
    dense_scored = [
        (float(dense[index]) * retriever._evidence_weight(index), index)
        for index in dense_order[:50]
    ]
    candidate_indexes = set(bm25_rank) | set(dense_rank)
    fused_scored: list[tuple[float, int]] = []
    for index in candidate_indexes:
        if bm25[index] < min_score and index not in dense_rank:
            continue
        fused = (
            1.0 / (60 + bm25_rank[index]) if index in bm25_rank else 0.0
        ) + (
            1.0 / (60 + dense_rank[index]) if index in dense_rank else 0.0
        )
        fused *= retriever._evidence_weight(index)
        fused_scored.append((fused, index))

    fused_scored.sort(
        key=lambda item: retriever._ranking_key(item[0], item[1], None)
    )
    preferred_scored = list(fused_scored)
    preferred_scored.sort(
        key=lambda item: retriever._ranking_key(
            item[0], item[1], preferences
        )
    )

    result = {
        "bm25": select_diverse(
            retriever, lexical_scored, bm25, None, bm25_rank, {}, 2
        ),
        "bge_dense": select_diverse(
            retriever, dense_scored, bm25, dense, bm25_rank, dense_rank, 2
        ),
        "rrf": select_diverse(
            retriever, fused_scored, bm25, dense, bm25_rank, dense_rank, 2
        ),
        "rrf_preferences": select_diverse(
            retriever,
            preferred_scored,
            bm25,
            dense,
            bm25_rank,
            dense_rank,
            2,
        ),
    }

    # Guard the ablation implementation against drift from public runtime paths.
    lexical_runtime = MaibRetriever(
        retriever.index_path, top_k=2, min_score=min_score
    ).retrieve(query)
    hybrid_runtime = retriever.retrieve(query)
    preferred_runtime = retriever.retrieve(
        query, metadata_preferences=preferences
    )
    expected = {
        "bm25": [item.chunk_id for item in lexical_runtime],
        "rrf": [item.chunk_id for item in hybrid_runtime],
        "rrf_preferences": [item.chunk_id for item in preferred_runtime],
    }
    for method, runtime_ids in expected.items():
        ablation_ids = [item.card_id for item in result[method]]
        if ablation_ids != runtime_ids:
            raise AssertionError(
                f"{method} ablation drifted from MaibRetriever: "
                f"{ablation_ids} != {runtime_ids}"
            )
    return result


def prepare_rankings(
    states: Sequence[dict[str, Any]],
    retriever: MaibRetriever,
    min_score: float,
) -> list[dict[str, Any]]:
    rankings: list[dict[str, Any]] = []
    for state in states:
        preferences = {
            key: set(values)
            for key, values in state["metadata_preferences"].items()
        }
        methods = rank_query(
            retriever,
            state["query"],
            preferences,
            min_score=min_score,
        )
        rankings.append(
            {
                "scenario_id": state["scenario_id"],
                "cycle_id": state["cycle_id"],
                "query": state["query"],
                "metadata_preferences": state["metadata_preferences"],
                "methods": {
                    method: [
                        item.as_dict(rank)
                        for rank, item in enumerate(methods[method], start=1)
                    ]
                    for method in METHODS
                },
            }
        )
    return rankings


def anonymized_pool(
    states: Sequence[dict[str, Any]],
    rankings: Sequence[dict[str, Any]],
    cards: Sequence[dict[str, Any]],
    seed: int,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Pool/deduplicate candidates and create a method-blind annotation CSV."""
    cards_by_id = {str(card["card_id"]): card for card in cards}
    states_by_scenario = {str(state["scenario_id"]): state for state in states}
    rng = random.Random(seed)

    scenario_order = sorted(states_by_scenario)
    rng.shuffle(scenario_order)
    state_ids = {
        scenario: f"S{ordinal:03d}"
        for ordinal, scenario in enumerate(scenario_order, start=1)
    }

    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    for state_ranking in rankings:
        scenario = str(state_ranking["scenario_id"])
        for method in METHODS:
            for result in state_ranking["methods"][method]:
                card_id = str(result["card_id"])
                key = (scenario, card_id)
                entry = pooled.setdefault(
                    key,
                    {
                        "scenario_id": scenario,
                        "state_id": state_ids[scenario],
                        "card_id": card_id,
                        "method_ranks": {},
                    },
                )
                entry["method_ranks"][method] = int(result["rank"])

    pool_order = sorted(pooled)
    rng.shuffle(pool_order)
    sheet_rows: list[dict[str, str]] = []
    key_rows: list[dict[str, Any]] = []
    for ordinal, key in enumerate(pool_order, start=1):
        pooled_entry = pooled[key]
        scenario = pooled_entry["scenario_id"]
        card_id = pooled_entry["card_id"]
        state = states_by_scenario[scenario]
        card = cards_by_id.get(card_id)
        if card is None:
            raise ValueError(f"ranked card absent from retained corpus: {card_id}")
        annotation_id = f"P{ordinal:03d}"
        sheet_rows.append(
            {
                "annotation_id": annotation_id,
                "state_id": pooled_entry["state_id"],
                "navigation_state": str(state["query"]),
                "card_finding": str(card.get("investigator_finding") or ""),
                "transferable_lesson": str(card.get("transferable_lesson") or ""),
                "non_transfer_conditions": json.dumps(
                    card.get("non_transfer_conditions") or [], ensure_ascii=False
                ),
                "supporting_quotations": json.dumps(
                    card.get("supporting_quotes") or [], ensure_ascii=False
                ),
                "relevance_label": "",
                "reviewer_notes": "",
            }
        )
        key_rows.append(
            {
                "annotation_id": annotation_id,
                "state_id": pooled_entry["state_id"],
                "scenario_id": scenario,
                "card_id": card_id,
                "method_ranks": pooled_entry["method_ranks"],
            }
        )
    return sheet_rows, key_rows


def write_label_sheet(path: Path, rows: Sequence[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LABEL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_labels(path: Path) -> dict[str, int]:
    labels: dict[str, int] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing_fields = set(LABEL_FIELDS) - set(reader.fieldnames or [])
        if missing_fields:
            raise ValueError(
                f"label sheet is missing columns: {sorted(missing_fields)}"
            )
        for line_number, row in enumerate(reader, start=2):
            annotation_id = str(row.get("annotation_id") or "").strip()
            if not annotation_id or annotation_id in labels:
                raise ValueError(
                    f"missing or duplicate annotation_id at {path}:{line_number}"
                )
            raw_label = str(row.get("relevance_label") or "").strip()
            if raw_label not in {"0", "1", "2"}:
                raise ValueError(
                    f"label at {path}:{line_number} must be exactly 0, 1, or 2"
                )
            labels[annotation_id] = int(raw_label)
    return labels


def dcg(labels: Sequence[int]) -> float:
    return sum(
        (2**label - 1) / math.log2(rank + 1)
        for rank, label in enumerate(labels, start=1)
    )


def calculate_metrics(
    rankings: Sequence[dict[str, Any]],
    key_rows: Sequence[dict[str, Any]],
    labels_by_annotation: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    annotation_by_pair = {
        (str(row["scenario_id"]), str(row["card_id"])): str(
            row["annotation_id"]
        )
        for row in key_rows
    }
    if set(labels_by_annotation) != {
        str(row["annotation_id"]) for row in key_rows
    }:
        raise ValueError("label IDs do not exactly match the private pool key")

    pooled_labels: dict[str, list[int]] = {}
    for row in key_rows:
        pooled_labels.setdefault(str(row["scenario_id"]), []).append(
            labels_by_annotation[str(row["annotation_id"])]
        )

    per_state: list[dict[str, Any]] = []
    for state_ranking in rankings:
        scenario = str(state_ranking["scenario_id"])
        ideal = sorted(pooled_labels[scenario], reverse=True)[:2]
        ideal_dcg = dcg(ideal)
        for method in METHODS:
            method_labels = [
                labels_by_annotation[
                    annotation_by_pair[(scenario, str(result["card_id"]))]
                ]
                for result in state_ranking["methods"][method]
            ]
            per_state.append(
                {
                    "scenario_id": scenario,
                    "method": method,
                    "labels": method_labels,
                    "precision_at_2": sum(
                        label == 2 for label in method_labels
                    )
                    / 2.0,
                    "hit_at_2": float(any(label == 2 for label in method_labels)),
                    "ndcg_at_2": (
                        dcg(method_labels) / ideal_dcg if ideal_dcg > 0 else 0.0
                    ),
                }
            )

    summary: list[dict[str, Any]] = []
    for method in METHODS:
        rows = [row for row in per_state if row["method"] == method]
        if len(rows) != 22:
            raise AssertionError(f"expected 22 metric rows for {method}")
        summary.append(
            {
                "method": method,
                "method_title": METHOD_TITLES[method],
                "states": len(rows),
                "precision_at_2": sum(
                    row["precision_at_2"] for row in rows
                )
                / len(rows),
                "hit_at_2": sum(row["hit_at_2"] for row in rows) / len(rows),
                "ndcg_at_2": sum(row["ndcg_at_2"] for row in rows) / len(rows),
            }
        )
    return summary, per_state


def paired_differences(
    per_state: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Calculate method-B minus method-A differences paired by scenario."""
    indexed = {
        (str(row["scenario_id"]), str(row["method"])): row
        for row in per_state
    }
    scenarios = sorted({str(row["scenario_id"]) for row in per_state})
    comparisons: list[dict[str, Any]] = []
    metrics = ("precision_at_2", "hit_at_2", "ndcg_at_2")
    for left_index, method_a in enumerate(METHODS):
        for method_b in METHODS[left_index + 1 :]:
            deltas = {
                metric: [
                    float(indexed[(scenario, method_b)][metric])
                    - float(indexed[(scenario, method_a)][metric])
                    for scenario in scenarios
                ]
                for metric in metrics
            }
            ndcg = deltas["ndcg_at_2"]
            comparisons.append(
                {
                    "method_a": method_a,
                    "method_b": method_b,
                    "direction": "method_b_minus_method_a",
                    "states": len(scenarios),
                    **{
                        f"mean_delta_{metric}": sum(values) / len(values)
                        for metric, values in deltas.items()
                    },
                    "ndcg_wins_b": sum(value > 1e-12 for value in ndcg),
                    "ndcg_ties": sum(abs(value) <= 1e-12 for value in ndcg),
                    "ndcg_wins_a": sum(value < -1e-12 for value in ndcg),
                }
            )
    return comparisons


def markdown_results(
    summary: Sequence[dict[str, Any]],
    per_state: Sequence[dict[str, Any]],
    paired: Sequence[dict[str, Any]],
    rankings: Sequence[dict[str, Any]],
    cards: Sequence[dict[str, Any]],
    key_rows: Sequence[dict[str, Any]],
    labels: dict[str, int],
) -> str:
    lines = [
        "# Navigation-state lesson-card retrieval results",
        "",
        "## Summary",
        "",
        "| Retrieval method | Precision@2 | Hit@2 | nDCG@2 |",
        "|---|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['method_title']} | {row['precision_at_2']:.3f} | "
            f"{row['hit_at_2']:.3f} | {row['ndcg_at_2']:.3f} |"
        )
    lines.extend(
        [
            "",
            "Precision@2 and Hit@2 treat only label 2 as relevant. nDCG@2 "
            "uses gains 0, 1, and 3 for labels 0, 1, and 2 respectively; "
            "the ideal ranking is calculated from the pooled judged cards "
            "for that state. The pool is not an exhaustive relevance set, so "
            "corpus-wide recall is not reported.",
            "",
            "## Paired method differences",
            "",
            "Each delta is method B minus method A, calculated within each "
            "state and then averaged over the 22 states.",
            "",
            "| Method A | Method B | Δ Precision@2 | Δ Hit@2 | Δ nDCG@2 | nDCG wins/ties/losses for B |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in paired:
        lines.append(
            f"| {METHOD_TITLES[row['method_a']]} | "
            f"{METHOD_TITLES[row['method_b']]} | "
            f"{row['mean_delta_precision_at_2']:+.3f} | "
            f"{row['mean_delta_hit_at_2']:+.3f} | "
            f"{row['mean_delta_ndcg_at_2']:+.3f} | "
            f"{row['ndcg_wins_b']}/{row['ndcg_ties']}/{row['ndcg_wins_a']} |"
        )
    lines.extend(
        [
            "",
            "## Per-scenario results",
            "",
            "| Scenario | Method | Labels at ranks 1–2 | Precision@2 | Hit@2 | nDCG@2 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(per_state, key=lambda item: (item["scenario_id"], METHODS.index(item["method"]))):
        lines.append(
            f"| {row['scenario_id']} | {METHOD_TITLES[row['method']]} | "
            f"{row['labels']} | {row['precision_at_2']:.3f} | "
            f"{row['hit_at_2']:.3f} | {row['ndcg_at_2']:.3f} |"
        )

    cards_by_id = {str(card["card_id"]): card for card in cards}
    annotations = {
        (str(row["scenario_id"]), str(row["card_id"])): str(row["annotation_id"])
        for row in key_rows
    }
    rankings_by_scenario = {
        str(row["scenario_id"]): row for row in rankings
    }
    lines.extend(["", "## Representative successes and failures", ""])
    for method in METHODS:
        rows = [row for row in per_state if row["method"] == method]
        success = max(
            rows,
            key=lambda row: (
                row["ndcg_at_2"], row["hit_at_2"], row["scenario_id"]
            ),
        )
        failure = min(
            rows,
            key=lambda row: (
                row["ndcg_at_2"], row["hit_at_2"], row["scenario_id"]
            ),
        )
        lines.append(f"### {METHOD_TITLES[method]}")
        lines.append("")
        for label, example in (("Success", success), ("Failure", failure)):
            scenario = str(example["scenario_id"])
            results = rankings_by_scenario[scenario]["methods"][method]
            descriptions = []
            for result in results:
                card_id = str(result["card_id"])
                relevance = labels[annotations[(scenario, card_id)]]
                finding = str(cards_by_id[card_id]["investigator_finding"])
                descriptions.append(
                    f"rank {result['rank']} label {relevance}: {finding}"
                )
            lines.append(
                f"- **{label} ({scenario}, nDCG@2 "
                f"{example['ndcg_at_2']:.3f}):** " + " / ".join(descriptions)
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def annotation_guide(pool_size: int, seed: int) -> str:
    return f"""# Human relevance annotation guide

This sheet contains {pool_size} unique state–card pairs pooled from four retrieval methods. The rows are deduplicated and deterministically shuffled with seed {seed}. The sheet does not identify the retrieval method, rank, MAIB report, scenario number, or card identifier. Do not open `private_pool_key.jsonl` until annotation is complete.

Enter exactly one value in `relevance_label` for every row:

- **2 — Relevant:** the investigator finding addresses a mechanism present in the navigation state, and the lesson's applicability conditions are satisfied.
- **1 — Partially relevant:** the finding or lesson addresses a related mechanism, but an important circumstance differs or is unknown.
- **0 — Irrelevant:** the card does not provide applicable reasoning for this encounter.

Judge transfer to the observed state, not shared words such as “collision”, “crossing”, or “overtaking”. Treat unobserved circumstances as unknown; do not invent them. Use `reviewer_notes` for difficult or borderline decisions. Do not alter the other columns or reorder/delete rows.

After labelling, run:

```bash
.venv/bin/python tools/evaluate_maib_state_retrieval.py score \\
  --output-dir reports/maib_state_retrieval_benchmark \\
  --labels reports/maib_state_retrieval_benchmark/relevance_labels.csv
```
"""


def protocol_readme(pool_size: int) -> str:
    return f"""# Imazu navigation-state lesson-card retrieval benchmark

This offline benchmark uses the final 503-card retained corpus and one archived retrieval-eligible state from each of the 22 Imazu scenarios. The deterministic state-selection rule is the first `numeric_observations_only` MAIB retrieval in each case of the frozen warp-5 lesson-card campaign. Each selected query and its hidden encounter preferences were reconstructed from the archived bridge snapshot and OOW decision and required to equal the logged runtime values exactly.

Four top-two configurations are compared with unchanged retrieval parameters:

1. BM25 (`k1=1.5`, `b=0.75`, evidence weighting, minimum score 5.0);
2. BGE dense retrieval (`BAAI/bge-small-en-v1.5`, existing query prefix, normalised vectors, evidence weighting);
3. BM25+BGE reciprocal-rank fusion (top-50 channel pools, `1/(60+r)`, evidence weighting);
4. the same RRF ranking with the existing categorical encounter-type and phase preferences.

All four apply the runtime source-diversity rule of at most one card per MAIB report and return two cards. No reranker is enabled. The union contains {pool_size} unique state–card pairs (maximum 176).

`relevance_labels.csv` is the only file intended for the blinded annotator. `private_pool_key.jsonl` and `rankings.jsonl` reveal methods and must remain hidden until labels are final. No language model is used for relevance grading. Once all rows contain 0, 1, or 2, the `score` command writes `summary.json`, `per_scenario.jsonl`, and `RESULTS.md`.

The benchmark reports Precision@2, Hit@2, and nDCG@2. Only label 2 counts as relevant for Precision and Hit. nDCG uses graded gains `2^label - 1`. Because judgments cover the pooled top-two candidates rather than all 503 cards, the benchmark does not report corpus-wide recall.
"""


def command_prepare(args: argparse.Namespace) -> None:
    output_dir: Path = args.output_dir
    if output_dir.exists():
        if not args.force:
            raise FileExistsError(
                f"output directory exists; use --force to replace: {output_dir}"
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    cards = read_jsonl(args.cards)
    index_path = output_dir / "final_503_cards.index.jsonl"
    index_records = export_card_index(args.cards, index_path)
    embeddings_path = output_dir / "final_503_cards.embeddings.npy"
    embed_card_index(
        index_records,
        embeddings_path,
        args.embedding_model,
        args.batch_size,
    )
    states = select_states(args.log_root)
    write_jsonl(output_dir / "selected_states.jsonl", states)

    retriever = MaibRetriever(
        index_path,
        top_k=2,
        min_score=args.min_score,
        embeddings_path=embeddings_path,
        embedding_model=args.embedding_model,
    )
    rankings = prepare_rankings(states, retriever, args.min_score)
    write_jsonl(output_dir / "rankings.jsonl", rankings)
    sheet_rows, key_rows = anonymized_pool(
        states, rankings, cards, args.anonymization_seed
    )
    write_label_sheet(output_dir / "relevance_labels.csv", sheet_rows)
    write_jsonl(output_dir / "private_pool_key.jsonl", key_rows)
    (output_dir / "ANNOTATION_GUIDE.md").write_text(
        annotation_guide(len(sheet_rows), args.anonymization_seed),
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        protocol_readme(len(sheet_rows)), encoding="utf-8"
    )
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "language_model_grading": False,
        "state_selection": "first retrieval event with routing.method=numeric_observations_only per case",
        "scenarios": 22,
        "cards": len(cards),
        "pooled_state_card_pairs": len(sheet_rows),
        "methods": list(METHODS),
        "top_k": 2,
        "min_score": args.min_score,
        "embedding_model": args.embedding_model,
        "embedding_model_revision": cached_model_revision(args.embedding_model),
        "anonymization_seed": args.anonymization_seed,
        "environment": {
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "sentence_transformers": version("sentence-transformers"),
            "transformers": version("transformers"),
            "torch": version("torch"),
        },
        "sources": {
            "log_root": str(args.log_root),
            "cards": str(args.cards),
            "cards_sha256": sha256(args.cards),
            "audit_sha256": {
                state["scenario_id"]: sha256(Path(state["audit_path"]))
                for state in states
            },
            "retriever_source_sha256": sha256(Path("rag/maib.py")),
            "evaluation_script_sha256": sha256(Path(__file__)),
        },
        "artifacts": {
            "index": str(index_path),
            "index_sha256": sha256(index_path),
            "embeddings": str(embeddings_path),
            "embeddings_sha256": sha256(embeddings_path),
            "selected_states_sha256": sha256(output_dir / "selected_states.jsonl"),
            "rankings_sha256": sha256(output_dir / "rankings.jsonl"),
            "label_sheet_sha256_before_annotation": sha256(
                output_dir / "relevance_labels.csv"
            ),
            "private_pool_key_sha256": sha256(
                output_dir / "private_pool_key.jsonl"
            ),
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


def command_score(args: argparse.Namespace) -> None:
    output_dir: Path = args.output_dir
    cards = read_jsonl(args.cards)
    rankings = read_jsonl(output_dir / "rankings.jsonl")
    key_rows = read_jsonl(output_dir / "private_pool_key.jsonl")
    labels = read_labels(args.labels)
    summary, per_state = calculate_metrics(rankings, key_rows, labels)
    paired = paired_differences(per_state)
    write_json(
        output_dir / "summary.json",
        {
            "labels": len(labels),
            "methods": summary,
            "paired_differences": paired,
            "definitions": {
                "precision_at_2": "fraction of two results with label 2",
                "hit_at_2": "one when either result has label 2, otherwise zero",
                "ndcg_at_2": "graded gains 2^label-1; IDCG from the pooled judged candidates for the state",
            },
            "recall_reported": False,
        },
    )
    write_jsonl(output_dir / "per_scenario.jsonl", per_state)
    (output_dir / "RESULTS.md").write_text(
        markdown_results(
            summary, per_state, paired, rankings, cards, key_rows, labels
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


def command_audit(args: argparse.Namespace) -> None:
    output_dir: Path = args.output_dir
    manifest = json.loads((output_dir / "manifest.json").read_text())
    states = read_jsonl(output_dir / "selected_states.jsonl")
    rankings = read_jsonl(output_dir / "rankings.jsonl")
    key_rows = read_jsonl(output_dir / "private_pool_key.jsonl")
    with (output_dir / "relevance_labels.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        sheet_rows = list(csv.DictReader(stream))
    problems: list[str] = []
    if len(states) != 22:
        problems.append(f"selected states: expected 22, got {len(states)}")
    if len(rankings) != 22:
        problems.append(f"rankings: expected 22, got {len(rankings)}")
    for row in rankings:
        if tuple(row.get("methods", {}).keys()) != METHODS:
            problems.append(f"method set/order differs for {row.get('scenario_id')}")
        for method in METHODS:
            if len(row.get("methods", {}).get(method, [])) != 2:
                problems.append(
                    f"{row.get('scenario_id')} {method}: expected two results"
                )
    if len(key_rows) != len(sheet_rows):
        problems.append("private key and label sheet row counts differ")
    if len(sheet_rows) != manifest.get("pooled_state_card_pairs"):
        problems.append("manifest pool size differs from label sheet")
    forbidden_columns = {"method", "rank", "card_id", "scenario_id", "source_file"}
    exposed = forbidden_columns & set(sheet_rows[0] if sheet_rows else {})
    if exposed:
        problems.append(f"label sheet exposes blinded columns: {sorted(exposed)}")
    sheet_ids = {row.get("annotation_id") for row in sheet_rows}
    key_ids = {row.get("annotation_id") for row in key_rows}
    if sheet_ids != key_ids or len(sheet_ids) != len(sheet_rows):
        problems.append("annotation IDs are missing, duplicated, or mismatched")
    pair_count = len(
        {(row.get("scenario_id"), row.get("card_id")) for row in key_rows}
    )
    if pair_count != len(key_rows):
        problems.append("pooled state-card pairs are not deduplicated")
    result = {
        "ok": not problems,
        "states": len(states),
        "rankings": len(rankings),
        "pooled_pairs": len(key_rows),
        "problems": problems,
    }
    print(json.dumps(result, indent=2))
    if problems:
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="build the offline rankings and blinded label sheet"
    )
    prepare.add_argument("--log-root", type=Path, default=DEFAULT_LOG_ROOT)
    prepare.add_argument("--cards", type=Path, default=DEFAULT_CARDS)
    prepare.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    prepare.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    prepare.add_argument("--batch-size", type=int, default=32)
    prepare.add_argument("--min-score", type=float, default=5.0)
    prepare.add_argument("--anonymization-seed", type=int, default=20261116)
    prepare.add_argument("--force", action="store_true")
    prepare.set_defaults(function=command_prepare)

    score = subparsers.add_parser(
        "score", help="validate complete human labels and calculate metrics"
    )
    score.add_argument("--cards", type=Path, default=DEFAULT_CARDS)
    score.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    score.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_OUTPUT / "relevance_labels.csv",
    )
    score.set_defaults(function=command_score)

    audit = subparsers.add_parser(
        "audit", help="check the prepared artifact structure and blinding"
    )
    audit.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    audit.set_defaults(function=command_audit)
    return result


def main() -> None:
    args = parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
