"""Transparent two-stage retrieval over the local verbatim COLREG corpus."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol, Sequence


TOKEN_RE = re.compile(r"[a-z0-9]+")
DEFAULT_GUIDANCE = (
    Path(__file__).resolve().parents[2]
    / "experiments/qwen8b_colregs_rag/colregs_ship_guidance.json"
)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _first_match(pattern: str, text: str, default: str | None = None) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else default


def _metres(nautical_miles: str) -> str:
    return str(int(round(float(nautical_miles) * 1852.0)))


def _encounter_terms(
    own_heading: str | None,
    contact_heading: str | None,
    contact_speed_knots: str | None,
    relative_bearing: str | None,
    closing_knots: str | None,
    has_future_cpa: bool,
) -> list[str]:
    """Derive lexical encounter terms from facts printed in the narrative."""
    values = (
        own_heading,
        contact_heading,
        contact_speed_knots,
        relative_bearing,
        closing_knots,
    )
    if any(value is None for value in values):
        return []
    own_hdg, contact_hdg, contact_speed, bearing, closing = map(float, values)
    bearing %= 360.0
    heading_difference = abs((contact_hdg - own_hdg + 180.0) % 360.0 - 180.0)
    ahead = bearing <= 15.0 or bearing >= 345.0
    if contact_speed <= 0.39:
        return ["stationary contact"]
    if closing > 0.0 and ahead and heading_difference >= 150.0:
        return ["head-on encounter"]
    if closing > 0.0 and heading_difference <= 30.0:
        contact_astern = 112.5 < bearing < 247.5
        reciprocal_bearing = (own_hdg + bearing + 180.0 - contact_hdg) % 360.0
        own_astern = 112.5 < reciprocal_bearing < 247.5
        if contact_astern:
            return ["contact overtaking own ship", "own ship stand-on vessel"]
        if own_astern:
            return ["own ship overtaking contact", "own ship give-way vessel"]
        return ["parallel encounter"]
    if closing > 0.0 and has_future_cpa:
        role = "give-way vessel" if 0.0 < bearing < 112.5 else "stand-on vessel"
        return ["crossing encounter", f"own ship {role}"]
    return ["diverging encounter"]


def _state_encounter_terms(contact: dict[str, Any]) -> list[str]:
    encounter = str(contact.get("encounter_type") or "").strip().lower()
    if encounter == "we_are_overtaking_contact":
        return ["established overtaking encounter remains active",
                "own ship overtaking contact", "own ship give-way vessel"]
    if encounter == "contact_overtaking_us":
        return ["established overtaking encounter remains active",
                "contact overtaking own ship", "own ship stand-on vessel"]
    if encounter == "head_on":
        return ["head-on encounter"]
    if encounter == "crossing":
        try:
            bearing = float(contact.get("relative_bearing_deg")) % 360.0
        except (TypeError, ValueError):
            return ["crossing encounter"]
        role = "give-way vessel" if 0.0 < bearing < 112.5 else "stand-on vessel"
        return ["crossing encounter", f"own ship {role}"]
    if encounter in {"parallel", "diverging", "stationary_contact"}:
        return [encounter.replace("_", " ") + " encounter"]
    return []


def navigation_queries(
    narrative: str, state: dict[str, Any] | None = None
) -> list[str]:
    """Extract one compact, observation-only COLREG query per contact.

    The bridge narrative remains the source of truth.  This removes mission and
    policy prose that is useful to the OOW but harmful to lexical retrieval.
    """
    own_type = _first_match(
        r"^Own-ship vessel type is ([^.]+)\.", narrative, "power driven"
    )
    own_status = _first_match(
        r"^.+? is (underway|stopped|paused|stalled)\b", narrative, "underway"
    )
    own_heading = _first_match(
        r"^.+?\bheading ([+-]?[\d.]+) degrees", narrative
    )
    visibility = _first_match(r"\bvisibility is ([^;.]+)", narrative, "clear")

    maneuver = "none"
    maneuver_block = re.search(
        r"CURRENT MANEUVER STATE\s+(.+?)(?=\n\n[A-Z][A-Z ]+\n|\n\nTracked contacts:|\Z)",
        narrative,
        re.IGNORECASE | re.DOTALL,
    )
    if maneuver_block:
        direction = _first_match(
            r"Commanded alteration:\s*([^.]+)", maneuver_block.group(1)
        )
        maneuver = direction or "alter course"

    shared = [
        "OWN SHIP",
        str(own_type).replace("_", "-").lower(),
        str(own_status).lower(),
        f"{str(visibility).lower()} visibility",
    ]
    contacts_by_name = {
        str(item.get("name")): item
        for item in (state or {}).get("contacts", [])
        if isinstance(item, dict) and item.get("name")
    }
    # Rendered contact observations contain a colon; temporal event bullets
    # such as "Contact X appeared" deliberately do not.
    contact_lines = re.findall(r"^- Contact .+?:.*$", narrative, re.MULTILINE)
    queries: list[str] = []
    for contact_line in contact_lines:
        name = _first_match(r"^- Contact (.+?)(?:\s*\(|:)", contact_line, "contact")
        vessel_type = _first_match(r"^- Contact [^(]+\(([^)]+)\)", contact_line, "power driven")
        sector = _first_match(
            r"nautical miles,\s*([^(.]+?)\s*\(relative bearing", contact_line
        )
        bearing = _first_match(r"relative bearing ([+-]?[\d.]+) degrees", contact_line)
        range_nm = _first_match(r"range ([\d.]+) nautical miles", contact_line)
        closing_knots = _first_match(r"closing speed ([+-]?[\d.]+) knots", contact_line)
        contact_heading = _first_match(r"Heading ([+-]?[\d.]+) degrees", contact_line)
        contact_speed = _first_match(r"\bspeed ([+-]?[\d.]+) knots", contact_line)
        cpa_nm = _first_match(r"(?:Projected|computed) CPA (?:is|was) ([\d.]+) nautical miles", contact_line)
        tcpa = _first_match(r"TCPA ([+-]?[\d.]+) seconds", contact_line)

        contact = ["CONTACT", str(name), str(vessel_type).replace("_", "-").lower()]
        if sector:
            contact.append(sector.lower())
        if bearing is not None:
            contact.append(f"relative bearing: {bearing} degrees")
        if range_nm is not None:
            contact.append(f"range: {_metres(range_nm)} m")
        if closing_knots is not None:
            contact.append("closing" if float(closing_knots) > 0 else "opening")
        if cpa_nm is not None:
            contact.append(f"CPA: {_metres(cpa_nm)} m")
        if tcpa is not None:
            contact.append(f"TCPA: {int(round(float(tcpa)))} s")
        state_contact = contacts_by_name.get(str(name), {})
        state_terms = _state_encounter_terms(state_contact)
        contact.extend(
            state_terms or _encounter_terms(
                own_heading,
                contact_heading,
                contact_speed,
                bearing,
                closing_knots,
                tcpa is not None,
            )
        )
        risk = str(state_contact.get("risk") or "").lower()
        if risk in {"medium", "high", "critical"}:
            contact.append("collision risk")
        elif risk:
            contact.append("no collision risk")

        bearing_change = _first_match(
            rf"^-\s*{re.escape(str(name))} relative bearing changed .*? by ([\d.]+) degrees\.",
            narrative,
        )
        if bearing_change is not None and float(bearing_change) <= 5.0:
            contact.append("bearing nearly steady")
        queries.append("\n".join(shared + [""] + contact + ["", "CURRENT MANEUVER", maneuver]))

    if queries:
        return queries
    return [
        "\n".join(
            shared
            + ["", "CONTACT", "no contacts", "", "CURRENT MANEUVER", maneuver,
               "safe speed", "lookout", "risk of collision"]
        )
    ]


def navigation_query(narrative: str, state: dict[str, Any] | None = None) -> str:
    """Backward-compatible combined form of the compact retrieval queries."""
    return "\n\n---\n\n".join(navigation_queries(narrative, state))


@dataclass(frozen=True)
class RuleChunk:
    chunk_id: str
    rule: int
    title: str
    text: str


@dataclass(frozen=True)
class RetrievedRule:
    chunk_id: str
    rule: int
    title: str
    text: str
    retrieval_score: float
    reranker_score: float | None = None
    selected: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


class Reranker(Protocol):
    """Interface to preserve while replacing heuristics with a neural model."""

    name: str

    def rerank(
        self, query: str, candidates: Sequence[RetrievedRule]
    ) -> list[RetrievedRule]: ...


class IdentityReranker:
    name = "identity"

    def rerank(self, query: str, candidates: Sequence[RetrievedRule]) -> list[RetrievedRule]:
        del query
        return list(candidates)


def load_rule_chunks(path: Path) -> list[RuleChunk]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    chunks: list[RuleChunk] = []
    for part in corpus.get("parts", []):
        groups = [part.get("rules", [])]
        groups.extend(section.get("rules", []) for section in part.get("sections", []))
        for rules in groups:
            for rule in rules:
                number = int(rule["number"])
                title = str(rule.get("title") or "")
                text = str(rule.get("text_verbatim") or rule.get("text") or "").strip()
                if text:
                    chunks.append(RuleChunk(f"colreg-{number}", number, title, text))
    if not chunks:
        raise ValueError(f"no COLREG rules found in {path}")
    return sorted(chunks, key=lambda chunk: chunk.rule)


def load_ship_guidance(path: Path) -> dict[int, tuple[str, ...]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"COLREG ship guidance must be a JSON array: {path}")
    guidance: dict[int, tuple[str, ...]] = {}
    for record in records:
        if not isinstance(record, dict) or "rule" not in record:
            raise ValueError(f"invalid COLREG ship guidance record in {path}")
        rule = int(record["rule"])
        lines = record.get("ship_guidance")
        if rule in guidance:
            raise ValueError(f"duplicate Rule {rule} guidance in {path}")
        if not isinstance(lines, list) or not lines or not all(
            isinstance(line, str) and line.strip() for line in lines
        ):
            raise ValueError(f"Rule {rule} has invalid ship_guidance in {path}")
        guidance[rule] = tuple(line.strip() for line in lines)
    return guidance


class ColregsRetriever:
    """BM25 candidate retrieval followed by a swappable reranker."""

    def __init__(
        self,
        corpus: Path,
        *,
        candidate_k: int = 12,
        top_k: int = 6,
        reranker: Reranker | None = None,
        guidance: Path | None = DEFAULT_GUIDANCE,
    ) -> None:
        if not 1 <= top_k <= candidate_k:
            raise ValueError("require 1 <= top_k <= candidate_k")
        self.chunks = load_rule_chunks(corpus)
        self.candidate_k = candidate_k
        self.top_k = top_k
        self.reranker = reranker or IdentityReranker()
        self.guidance = load_ship_guidance(guidance) if guidance is not None else {}
        self._tokens = [tokenize(f"rule {c.rule} {c.title} {c.text}") for c in self.chunks]
        self._avgdl = sum(map(len, self._tokens)) / len(self._tokens)
        self._df: dict[str, int] = {}
        for tokens in self._tokens:
            for token in set(tokens):
                self._df[token] = self._df.get(token, 0) + 1

    def _score(self, query_tokens: Sequence[str], index: int) -> float:
        tokens = self._tokens[index]
        frequencies: dict[str, int] = {}
        for token in tokens:
            frequencies[token] = frequencies.get(token, 0) + 1
        score = 0.0
        n = len(self.chunks)
        for term in set(query_tokens):
            tf = frequencies.get(term, 0)
            if not tf:
                continue
            df = self._df.get(term, 0)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            denom = tf + 1.5 * (1.0 - 0.75 + 0.75 * len(tokens) / self._avgdl)
            score += idf * tf * 2.5 / denom
        return score

    @staticmethod
    def _rule_priors(query: str) -> dict[int, float]:
        """Small, inspectable domain priors for linked-rule recall.

        These do not classify the encounter. They only ensure that when the
        query itself uses a legal/navigation concept, its neighboring duties
        enter the candidate pool for the reranker and OOW to assess.
        """
        q = query.lower()
        priors: dict[int, float] = {}

        def add(rules: Sequence[int], value: float) -> None:
            for rule in rules:
                priors[rule] = priors.get(rule, 0.0) + value

        if "contact" in q and "no contacts" not in q:
            add((2, 5, 6, 7, 11), 5.0)

        if "crossing encounter" in q:
            add((15,), 16.0)
            add((8,), 12.0)
            for rule in (13, 14):
                priors[rule] = priors.get(rule, 0.0) - 20.0
        elif not any(term in q for term in ("parallel encounter", "diverging encounter", "overtaking")) and (
            "crossing" in q or "starboard side" in q or "starboard bow" in q
        ):
            add((15, 16, 17), 16.0)
        if "own ship give-way vessel" in q:
            add((16,), 12.0)
            priors[17] = priors.get(17, 0.0) - 20.0
        if "own ship stand-on vessel" in q:
            add((17,), 12.0)
            priors[16] = priors.get(16, 0.0) - 20.0
        if "head-on" in q or "head on" in q:
            add((14,), 7.0)
            add((8,), 12.0)
        if "overtaking" in q or "overtaken" in q:
            add((13,), 7.0)
        if "own ship overtaking contact" in q:
            add((8, 13, 16), 12.0)
        if "overtaking encounter" in q:
            for rule in (14, 15):
                priors[rule] = priors.get(rule, 0.0) - 20.0
        if "parallel encounter" in q or "diverging encounter" in q:
            for rule in range(12, 18):
                priors[rule] = priors.get(rule, 0.0) - 20.0
        if "collision risk" in q or "risk of collision" in q or "cpa" in q or "tcpa" in q:
            add((7, 8), 8.0)
        if "no collision risk" in q:
            add((2, 5, 6, 7, 11), 12.0)
            priors[8] = priors.get(8, 0.0) - 20.0
        if "safe speed" in q or "set speed" in q or "slow" in q or "stop" in q:
            add((6, 8), 4.0)
        if "restricted visibility" in q or "fog" in q:
            add((19,), 8.0)
        if "clear visibility" in q:
            priors[19] = priors.get(19, 0.0) - 20.0
            for rule in (21, 22, 23, 35):
                priors[rule] = priors.get(rule, 0.0) - 20.0
        if "no contacts" in q:
            add((2, 5, 6, 7), 8.0)
            for rule in (*range(12, 20), 22, 34):
                priors[rule] = priors.get(rule, 0.0) - 20.0
        if not any(term in q for term in ("horn", "whistle", "maneuvering signal", "warning signal")):
            priors[34] = priors.get(34, 0.0) - 20.0
        if "traffic separation" not in q:
            priors[10] = priors.get(10, 0.0) - 20.0
        if q.count("power driven") >= 2 or q.count("power-driven") >= 2:
            priors[3] = priors.get(3, 0.0) - 12.0
            priors[18] = priors.get(18, 0.0) - 12.0
        return priors

    def retrieve(self, query: str) -> tuple[list[RetrievedRule], list[RetrievedRule]]:
        query_tokens = tokenize(query)
        priors = self._rule_priors(query)
        ranked = sorted(
            (
                RetrievedRule(
                    c.chunk_id,
                    c.rule,
                    c.title,
                    c.text,
                    self._score(query_tokens, i) + priors.get(c.rule, 0.0),
                )
                for i, c in enumerate(self.chunks)
            ),
            key=lambda item: (-item.retrieval_score, item.rule),
        )[: self.candidate_k]
        reranked = self.reranker.rerank(query, ranked)
        # The authoritative corpus determines ranking. The maintained guidance
        # set determines which results are actionable enough to show the OOW.
        selection_pool = (
            [item for item in reranked if item.rule in self.guidance]
            if self.guidance else reranked
        )
        if "no contacts" in query.lower():
            selection_pool = [item for item in reranked if item.rule in {2, 5, 6, 7, 8}]
        elif (
            "no collision risk" in query.lower()
            and "overtaking encounter" not in query.lower()
        ):
            selection_pool = [item for item in reranked if item.rule in {2, 5, 6, 7, 11}]
        selected_ids = {item.chunk_id for item in selection_pool[: self.top_k]}
        candidates = [replace(item, selected=item.chunk_id in selected_ids) for item in ranked]
        selected_by_id = {item.chunk_id: item for item in selection_pool[: self.top_k]}
        selected = [replace(selected_by_id[cid], selected=True) for cid in selected_ids]
        selected.sort(
            key=lambda item: (
                -(item.reranker_score if item.reranker_score is not None else item.retrieval_score),
                item.rule,
            )
        )
        return candidates, selected

    def retrieve_many(
        self, queries: Sequence[str]
    ) -> tuple[list[RetrievedRule], list[RetrievedRule]]:
        """Retrieve per encounter and merge rules, retaining the best score."""
        candidate_by_id: dict[str, RetrievedRule] = {}
        selected_by_id: dict[str, RetrievedRule] = {}
        for query in queries:
            candidates, selected = self.retrieve(query)
            for destination, items in (
                (candidate_by_id, candidates), (selected_by_id, selected)
            ):
                for item in items:
                    previous = destination.get(item.chunk_id)
                    item_score = item.reranker_score if item.reranker_score is not None else item.retrieval_score
                    previous_score = (
                        previous.reranker_score
                        if previous is not None and previous.reranker_score is not None
                        else previous.retrieval_score if previous is not None else float("-inf")
                    )
                    if item_score > previous_score:
                        destination[item.chunk_id] = item
        selected_ids = set(selected_by_id)
        candidates = [
            replace(item, selected=item.chunk_id in selected_ids)
            for item in candidate_by_id.values()
        ]
        candidates.sort(key=lambda item: (-item.retrieval_score, item.rule))
        selected = [replace(item, selected=True) for item in selected_by_id.values()]
        selected.sort(key=lambda item: item.rule)
        return candidates, selected

    def prompt_context(self, items: Sequence[RetrievedRule]) -> str:
        """Render selected authoritative rule IDs as concise ship guidance."""
        blocks = []
        for item in items:
            guidance = self.guidance.get(item.rule)
            if guidance:
                body = "\n".join(f"- {line}" for line in guidance)
                blocks.append(
                    f"[{item.chunk_id}] Rule {item.rule} — {item.title}\n"
                    "OPERATIONAL SUMMARY\n"
                    f"{body}"
                )
            else:
                # Corpus rules without a maintained summary remain available;
                # never silently drop an authoritative retrieval result.
                blocks.append(
                    f"[{item.chunk_id}] Rule {item.rule} — {item.title}\n{item.text}"
                )
        return "\n\n".join(blocks)
