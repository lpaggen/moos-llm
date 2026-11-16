"""Inspectable lexical, semantic, and optional reranked MAIB retrieval."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower().replace("head_on", "head on"))


def query_from_state(state: dict[str, Any]) -> str:
    """Flatten categorical bridge/watch facts into retrieval terms."""
    terms: list[str] = []

    def visit(key: str, value: Any) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_key, child_value)
        elif isinstance(value, list):
            for item in value:
                visit(key, item)
        elif isinstance(value, str) and value:
            terms.extend((key.replace("_", " "), value.replace("_", " ")))

    visit("situation", state)
    return " ".join(terms)


def natural_query_from_state(
    decision_state: dict[str, Any],
    bridge_state: dict[str, Any],
) -> str:
    """Describe measured encounter geometry without consuming policy labels."""
    contacts = [c for c in bridge_state.get("contacts", []) if isinstance(c, dict)]
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    contacts.sort(key=lambda c: (risk_order.get(str(c.get("risk")), 4), c.get("range", 1e20)))
    descriptions = []
    for contact in contacts[:3]:
        nav = contact.get("nav") if isinstance(contact.get("nav"), dict) else {}
        cpa = contact.get("cpa_distance")
        tcpa = contact.get("tcpa_s")
        closing = float(contact.get("closing_speed") or 0.0)
        motion = "opening" if closing <= 0 else "slowly closing" if closing < 1 else "rapidly closing"
        cpa_text = "unavailable" if cpa is None else f"{float(cpa):.1f} metres"
        tcpa_text = "unavailable" if tcpa is None else f"{float(tcpa):.1f} seconds"
        descriptions.append(
            f"a {contact.get('vessel_type') or 'vessel'} at relative bearing "
            f"{contact.get('relative_bearing', 'unknown')} degrees, heading "
            f"{nav.get('heading', 'unknown')} degrees at {nav.get('speed', 'unknown')} knots, "
            f"{motion}, with projected CPA {cpa_text} and TCPA {tcpa_text}"
        )
    conditions = bridge_state.get("conditions") if isinstance(bridge_state.get("conditions"), dict) else {}
    visibility = conditions.get("visibility_condition", "not supplied")
    control = bridge_state.get("movement_status", "unknown")
    situation = "; ".join(descriptions) or "no navigation-relevant contacts"
    proposal = decision_state.get("oow_proposal")
    proposal_text = ""
    if isinstance(proposal, dict):
        proposal_text = (
            f" The OOW proposal under review is {proposal.get('action', 'unknown')}"
            f" with course change {float(proposal.get('degrees') or 0.0):.1f} degrees"
            f" and target speed {float(proposal.get('speed') or 0.0):.1f} knots."
        )
    return (
        "Find MAIB investigation analysis or conclusions about a comparable surface-vessel encounter. "
        f"Conditions are {visibility}. Own ship is {control}. The traffic situation is {situation}. "
        "The decision question is whether tactical intervention is required, while preserving predictable "
        "course and speed when collision risk is not established."
        f"{proposal_text} Prefer investigator findings over quoted regulations."
    )


def retrieval_preferences_from_state(
    bridge_state: dict[str, Any],
    contacts: Iterable[dict[str, Any]],
) -> dict[str, set[str]]:
    """Build hidden retrieval preferences solely from numeric observations."""
    own_nav = bridge_state.get("nav") if isinstance(bridge_state.get("nav"), dict) else {}
    own_heading = float(own_nav.get("heading") or 0.0)
    own_x = own_nav.get("x")
    own_y = own_nav.get("y")
    encounters: set[str] = set()
    phases: set[str] = set()
    for contact in contacts:
        nav = contact.get("nav") if isinstance(contact.get("nav"), dict) else {}
        speed = float(nav.get("speed") or 0.0)
        heading = float(nav.get("heading") or 0.0)
        heading_difference = abs(((heading - own_heading + 180.0) % 360.0) - 180.0)
        relative_bearing = contact.get("relative_bearing")
        relative_bearing = float(relative_bearing) if relative_bearing is not None else None
        ahead = relative_bearing is not None and (relative_bearing <= 15.0 or relative_bearing >= 345.0)
        if speed <= 0.2:
            encounters.add("stationary_contact")
        elif ahead and heading_difference >= 150.0:
            encounters.add("head_on")
        elif heading_difference <= 30.0:
            contact_x = nav.get("x")
            contact_y = nav.get("y")
            contact_relative_bearing = None
            if None not in (own_x, own_y, contact_x, contact_y):
                dx = float(own_x) - float(contact_x)
                dy = float(own_y) - float(contact_y)
                bearing = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
                contact_relative_bearing = (bearing - heading + 360.0) % 360.0
            own_sees_contact_astern = (
                relative_bearing is not None and 112.5 < relative_bearing < 247.5
            )
            contact_sees_own_astern = (
                contact_relative_bearing is not None
                and 112.5 < contact_relative_bearing < 247.5
            )
            encounters.add(
                "overtaking"
                if own_sees_contact_astern or contact_sees_own_astern
                else "unknown"
            )
        elif contact.get("tcpa_s") is not None:
            encounters.add("crossing")
        else:
            encounters.add("unknown")
        tcpa = contact.get("tcpa_s")
        if tcpa is not None and float(tcpa) <= 120.0:
            phases.add("emergency")
        elif tcpa is not None and float(tcpa) <= 300.0:
            phases.add("action_required")
        elif tcpa is not None and float(tcpa) <= 600.0:
            phases.add("risk_developing")
        else:
            phases.add("monitoring")
    return {
        "encounter_types": encounters,
        "roles": set(),
        "encounter_phases": phases,
    }


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    report_title: str
    source_file: str
    page: int | None
    text: str
    score: float
    source_type: str = ""
    review_status: str = ""
    encounter_types: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    encounter_phases: tuple[str, ...] = ()
    bm25_score: float | None = None
    dense_score: float | None = None
    bm25_rank: int | None = None
    dense_rank: int | None = None
    reranker_score: float | None = None
    reranker_rank: int | None = None
    fused_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "report_title": self.report_title,
            "source_file": self.source_file,
            "page": self.page,
            "text": self.text,
            "score": round(self.score, 6),
            "source_type": self.source_type,
            "review_status": self.review_status,
            "encounter_types": list(self.encounter_types),
            "roles": list(self.roles),
            "encounter_phases": list(self.encounter_phases),
            "bm25_score": None if self.bm25_score is None else round(self.bm25_score, 6),
            "dense_score": None if self.dense_score is None else round(self.dense_score, 6),
            "bm25_rank": self.bm25_rank,
            "dense_rank": self.dense_rank,
            "reranker_score": None if self.reranker_score is None else round(self.reranker_score, 6),
            "reranker_rank": self.reranker_rank,
            "fused_score": None if self.fused_score is None else round(self.fused_score, 8),
        }


class MaibRetriever:
    """Load a JSONL corpus once and rank chunks with auditable hybrid retrieval."""

    def __init__(
        self,
        index_path: str | Path,
        *,
        top_k: int = 2,
        min_score: float = 0.0,
        embeddings_path: str | Path | None = None,
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        reranker_model: str | None = None,
    ) -> None:
        self.index_path = Path(index_path)
        self.top_k = max(0, top_k)
        self.min_score = max(0.0, min_score)
        self.embedding_model_name = embedding_model
        self.documents = self._load(self.index_path)
        self.term_counts = [Counter(tokenize(doc["text"])) for doc in self.documents]
        self.lengths = [sum(counts.values()) for counts in self.term_counts]
        self.average_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.document_frequency: Counter[str] = Counter()
        for counts in self.term_counts:
            self.document_frequency.update(counts.keys())
        self.embeddings = None
        self.embedding_model = None
        self.reranker = None
        if embeddings_path and Path(embeddings_path).exists():
            import numpy as np
            from sentence_transformers import SentenceTransformer
            matrix = np.load(embeddings_path)
            if len(matrix) != len(self.documents):
                raise ValueError("MAIB embedding count does not match the text index")
            self.embeddings = matrix
            # Runtime simulations must never depend on Hugging Face availability.
            self.embedding_model = SentenceTransformer(
                embedding_model,
                device="cpu",
                local_files_only=True,
            )
        if reranker_model:
            from sentence_transformers import CrossEncoder
            self.reranker = CrossEncoder(
                reranker_model,
                device="cpu",
                local_files_only=True,
            )

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        documents: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record.get("text"), str) or not record["text"].strip():
                    raise ValueError(f"invalid RAG chunk at {path}:{line_number}")
                documents.append(record)
        return documents

    def _bm25_scores(self, query: str) -> list[float]:
        if not self.documents or not query.strip():
            return []
        query_terms = set(tokenize(query))
        total = len(self.documents)
        k1, b = 1.5, 0.75
        scores = []
        for index, counts in enumerate(self.term_counts):
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                docs_with_term = self.document_frequency[term]
                idf = math.log(1.0 + (total - docs_with_term + 0.5) / (docs_with_term + 0.5))
                denominator = frequency + k1 * (1.0 - b + b * self.lengths[index] / max(self.average_length, 1.0))
                score += idf * frequency * (k1 + 1.0) / denominator
            score *= self._evidence_weight(index)
            scores.append(score)
        return scores

    def _evidence_weight(self, index: int) -> float:
        """Prefer investigators' reasoning to pages that merely reproduce rules."""
        text = str(self.documents[index]["text"]).lower()
        rule_count = sum(
            marker in text
            for marker in ("rule 5", "rule 6", "rule 7", "rule 8", "rule 13", "rule 15", "rule 16", "rule 17", "rule 19")
        )
        analytical = any(
            marker in text
            for marker in (
                "analysis", "conclusion", "safety issues", "contributory factor",
                "causal factor", "the investigation found", "the maib found",
            )
        )
        if rule_count >= 2 and not analytical:
            return 0.25
        if analytical:
            return 1.12
        return 1.0

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        metadata_preferences: dict[str, set[str]] | None = None,
    ) -> list[RetrievalResult]:
        limit = self.top_k if top_k is None else max(0, top_k)
        if not self.documents or not query.strip() or limit == 0:
            return []
        bm25 = self._bm25_scores(query)
        bm25_order = sorted(range(len(bm25)), key=lambda i: (-bm25[i], str(self.documents[i].get("chunk_id", ""))))
        bm25_rank = {index: rank for rank, index in enumerate(bm25_order[:50], start=1)}
        dense_scores = None
        dense_rank: dict[int, int] = {}
        if self.embeddings is not None and self.embedding_model is not None:
            vector = self.embedding_model.encode(
                "Represent this sentence for searching relevant passages: " + query,
                normalize_embeddings=True,
            )
            dense_scores = self.embeddings @ vector
            dense_order = sorted(
                range(len(dense_scores)),
                key=lambda i: (-float(dense_scores[i]) * self._evidence_weight(i), i),
            )
            dense_rank = {index: rank for rank, index in enumerate(dense_order[:50], start=1)}
        candidate_indexes = set(bm25_rank) | set(dense_rank)
        scored: list[tuple[float, int]] = []
        for index in candidate_indexes:
            if bm25[index] < self.min_score and index not in dense_rank:
                continue
            fused = (1.0 / (60 + bm25_rank[index]) if index in bm25_rank else 0.0)
            fused += (1.0 / (60 + dense_rank[index]) if index in dense_rank else 0.0)
            if dense_scores is not None:
                fused *= self._evidence_weight(index)
            scored.append((fused if dense_scores is not None else bm25[index], index))
        scored.sort(
            key=lambda item: self._ranking_key(
                item[0], item[1], metadata_preferences
            )
        )
        reranker_scores: dict[int, float] = {}
        reranker_rank: dict[int, int] = {}
        if self.reranker is not None and scored:
            rerank_candidates = [index for _, index in scored[:20]]
            predictions = self.reranker.predict(
                [(query, str(self.documents[index]["text"])) for index in rerank_candidates],
                show_progress_bar=False,
            )
            reranker_scores = {
                index: float(value)
                for index, value in zip(rerank_candidates, predictions)
            }
            ordered = sorted(
                rerank_candidates,
                key=lambda i: (-reranker_scores[i], str(self.documents[i].get("chunk_id", ""))),
            )
            reranker_rank = {index: rank for rank, index in enumerate(ordered, start=1)}
            scored = [
                (score + (1.0 / (60 + reranker_rank[index]) if index in reranker_rank else 0.0), index)
                for score, index in scored
            ]
            scored.sort(
                key=lambda item: self._ranking_key(
                    item[0], item[1], metadata_preferences
                )
            )
        # Diversity matters more than adjacent pages from the same casualty.
        selected: list[RetrievalResult] = []
        seen_sources: set[str] = set()
        for score, index in scored:
            result = self._result(
                index,
                score,
                bm25_score=bm25[index],
                dense_score=None if dense_scores is None else float(dense_scores[index]),
                bm25_rank=bm25_rank.get(index),
                dense_rank=dense_rank.get(index),
                reranker_score=reranker_scores.get(index),
                reranker_rank=reranker_rank.get(index),
                fused_score=score if dense_scores is not None else None,
            )
            if result.source_file in seen_sources:
                continue
            selected.append(result)
            seen_sources.add(result.source_file)
            if len(selected) >= limit:
                break
        return selected

    def _ranking_key(
        self,
        score: float,
        index: int,
        preferences: dict[str, set[str]] | None,
    ) -> tuple[Any, ...]:
        """Rank lesson cards categorically, then by hybrid retrieval score."""
        doc = self.documents[index]
        if not preferences or doc.get("source_type") != "published_investigation_lesson_card":
            return (0, 0, 0, -score, str(doc.get("chunk_id", "")))

        def quality(field: str, fallback: str | None = None) -> int:
            wanted = preferences.get(field) or set()
            actual = {str(item) for item in doc.get(field, [])}
            if not wanted:
                return 0
            if actual & wanted:
                return 2
            if fallback and fallback in actual:
                return 1
            return 0

        encounter_quality = quality("encounter_types", "unknown")
        role_quality = quality("roles", "uncertain")
        phase_quality = quality("encounter_phases")
        return (
            -encounter_quality,
            -role_quality,
            -phase_quality,
            -score,
            str(doc.get("chunk_id", "")),
        )

    def _result(self, index: int, score: float, **ranking) -> RetrievalResult:
        doc = self.documents[index]
        return RetrievalResult(
            chunk_id=str(doc.get("chunk_id", index)),
            report_title=str(doc.get("report_title", "Untitled MAIB report")),
            source_file=str(doc.get("source_file", "")),
            page=doc.get("page") if isinstance(doc.get("page"), int) else None,
            text=str(doc["text"]),
            score=score,
            source_type=str(doc.get("source_type", "")),
            review_status=str(doc.get("review_status", "")),
            encounter_types=tuple(str(item) for item in doc.get("encounter_types", [])),
            roles=tuple(str(item) for item in doc.get("roles", [])),
            encounter_phases=tuple(str(item) for item in doc.get("encounter_phases", [])),
            **ranking,
        )

    @staticmethod
    def prompt_context(results: Iterable[RetrievalResult]) -> str:
        entries = []
        for item in results:
            location = f", page {item.page}" if item.page is not None else ""
            entries.append(f"[{item.chunk_id}] {item.report_title}{location}\n{item.text}")
        return "\n\n".join(entries)
