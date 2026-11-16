"""Captain agent: receive officer reports, select an action, and issue it."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from functools import lru_cache
from typing import Any
import asyncio

from openai import OpenAI
from transformers import AutoTokenizer

from bridge.state import BridgeSnapshot
from bridge.observations import compact_state
from .teacher_lookahead import build_teacher_candidate_lookahead
from .schemas.captain import CaptainDecision, CaptainRagDecision
from commands.all_commands import NAVIGATION_ACTIONS
from .skill_loader import load_skill
from tools.config_value import get
from .schemas.report import AgentReport
from rag.maib import (
    MaibRetriever,
    natural_query_from_state,
    retrieval_preferences_from_state,
)


DECISION_SCHEMA = CaptainDecision.model_json_schema(mode="validation")
SYSTEM_PROMPT: str = load_skill("captain")
DEFAULT_STALE_AFTER_S = float(get("lookout", "stale_after_s"))
DEFAULT_MAX_OUTPUT_TOKENS = int(get("lookout", "max_output_tokens"))
DEFAULT_MAX_CONTEXT_TOKENS = int(get("llm", "max_model_len"))
DEFAULT_MODEL: str = get("models", "default_local")
BRIDGE_HOST = get("hosts", "bridge")
BRIDGE_PORT = get("ports", "bridge")
AGENT_LLM_MODEL = get("models", "default_local")
MANEUVER_DISTANCE_M = float(
    os.environ.get("MOOS_LLM_MANEUVER_DISTANCE_M")
    or get("ship", "DEFAULT_MANEUVER_DISTANCE_M", fallback="200.0")
)
RETRIEVAL_CLEARANCE_M = float(
    os.environ.get("MOOS_LLM_MIN_SAFE_CPA_M")
    or get("ship", "MIN_SAFE_CPA_M", fallback="150.0")
)
CRITICAL_CPA_M = float(
    os.environ.get("MOOS_LLM_CRITICAL_CPA_M")
    or get("ship", "CRITICAL_CPA_M", fallback="150.0")
)
CAPTAIN_LOOKAHEAD_HORIZON_S = float(
    os.environ.get("MOOS_LLM_CAPTAIN_LOOKAHEAD_HORIZON_S", "600")
)
CAPTAIN_LOOKAHEAD_TIMESTEP_S = float(
    os.environ.get("MOOS_LLM_CAPTAIN_LOOKAHEAD_TIMESTEP_S", "5")
)
# Reserve room for chat-template differences and structured-output bookkeeping.
# This is deliberately additional to the model's configured output allowance.
CAPTAIN_PROMPT_SAFETY_MARGIN_TOKENS = 512
CAPTAIN_EVIDENCE_MAX_TOKENS = 512

CAPTAIN_FORBIDDEN_STATE_KEYS = {
    "_active_encounter",
    "_colregs_vessel_type",
    "colregs_context",
    "collision",
    "encounter_type",
    "mission_failed",
    "mission_failure_reason",
    "moos_colregs",
    "near_collision",
    "projection_status",
    "required_cpa_m",
    "risk",
    "sector",
}


@lru_cache(maxsize=4)
def _local_tokenizer(model: str):
    """Load the exact local tokenizer without ever reaching the network."""
    return AutoTokenizer.from_pretrained(model, local_files_only=True)


class AgentCaptain:
    def __init__(
        self,
        *,
        client: OpenAI,
        bridge_host: str = BRIDGE_HOST,
        bridge_port: int = BRIDGE_PORT,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        model: str = AGENT_LLM_MODEL,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        validator_queue: asyncio.Queue,
        maib_retriever: MaibRetriever | None = None,
        rag_decision_enabled: bool = False,
    ) -> None:
        self.client = client
        self.bridge_host = bridge_host
        self.bridge_port = bridge_port
        self.stale_after_s = stale_after_s
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_context_tokens = max_context_tokens
        if self.max_output_tokens <= 0 or self.max_context_tokens <= 0:
            raise ValueError("Captain context and output token limits must be positive")
        if (
            self.max_output_tokens + CAPTAIN_PROMPT_SAFETY_MARGIN_TOKENS
            >= self.max_context_tokens
        ):
            raise ValueError("Captain output allowance leaves no safe prompt budget")
        self.system_prompt = SYSTEM_PROMPT.replace(
            "the configured temporary-waypoint distance",
            f"the configured {MANEUVER_DISTANCE_M:.0f}-metre temporary-waypoint distance",
        )
        self.queue: asyncio.Queue[AgentReport] = asyncio.Queue()  # use to share info between captain and other agents
        self.validator_queue = validator_queue
        self.maib_retriever = maib_retriever
        self.rag_decision_enabled = bool(rag_decision_enabled and maib_retriever is not None)
        self.last_retrieval: list[dict[str, Any]] = []
        self.last_retrieval_query = ""
        self.last_retrieval_routing: dict[str, Any] = {}
        self.last_prompt_payload: dict[str, Any] | None = None
        self.last_evidence_assessment: dict[str, str] | None = None
        self.last_navigation_assessment: dict[str, str] | None = None
        self.last_course_intent = "none"
        self.last_prompt_budget: dict[str, Any] | None = None


    def send_decision_to_validator(self):
        self.validator_queue.put(...)


    @staticmethod
    def number(value: Any, default: float = 0.0) -> float:
        """Convert a value to a finite float or return a default."""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default

        if result != result:
            return default

        if result in (float("inf"), float("-inf")):
            return default

        return result


    def get_current_speed(self, state: dict[str, Any]) -> float:
        own_ship = state.get("own_ship", {})
        return max(0.0, self.number(own_ship.get("speed"), 0.0))

    def is_paused(self, state: dict[str, Any]) -> bool:
        control_state = state.get("control", state.get("control_state", {}))
        own_ship = state.get("own_ship", {})
        return bool(
            state.get("paused", control_state.get("paused", own_ship.get("paused", False)))
        )


    def get_allowed_actions(self, state: dict[str, Any]) -> set[str]:
        """Expose every supported navigation action without policy gating."""
        actions = set(NAVIGATION_ACTIONS)
        maneuver = state.get("current_maneuver") or state.get("current_action") or {}
        if not (
            isinstance(maneuver, dict)
            and maneuver.get("action") == "alter_course"
        ):
            actions.discard("rejoin_route")
        return actions

    def navigation_relevant_contacts(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Gate retrieval using CPA/TCPA observations, never policy labels."""
        relevant = []
        for contact in state.get("contacts", []):
            if not isinstance(contact, dict):
                continue
            tcpa = self.number(contact.get("tcpa_s"), -1.0)
            closing = self.number(contact.get("closing_speed"), 0.0)
            cpa = self.number(contact.get("cpa_distance"), float("inf"))
            if (
                closing > 0.0
                and tcpa >= 0.0
                and cpa < RETRIEVAL_CLEARANCE_M
            ):
                relevant.append(contact)
        return relevant

    @classmethod
    def observations_only_state(cls, state: dict[str, Any]) -> dict[str, Any]:
        """Strip every upstream encounter/rule/risk verdict from captain input."""
        cleaned = deepcopy(state)

        def strip(value: Any) -> None:
            if isinstance(value, dict):
                for key in list(value):
                    if key in CAPTAIN_FORBIDDEN_STATE_KEYS:
                        value.pop(key, None)
                    else:
                        strip(value[key])
            elif isinstance(value, list):
                for item in value:
                    strip(item)

        strip(cleaned)
        cls.assert_observations_only(cleaned)
        return cleaned

    @staticmethod
    def assert_observations_only(state: dict[str, Any]) -> None:
        """Fail closed if a forbidden deterministic label reaches the prompt."""
        def visit(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else key
                    if key in CAPTAIN_FORBIDDEN_STATE_KEYS:
                        raise RuntimeError(
                            f"captain observations-only invariant violated at {child_path}"
                        )
                    visit(child, child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{path}[{index}]")

        visit(state, "")

    @staticmethod
    def compact_candidate_consequences(table: dict[str, Any]) -> dict[str, Any]:
        """Keep projection results needed for review, not simulator diagnostics."""
        compact_candidates = []
        for candidate in table.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            compact = {
                key: candidate[key]
                for key in (
                    "action",
                    "degrees",
                    "speed_mps",
                    "projected_minimum_separation_m",
                    "classification",
                    "all_contacts_at_or_above_minimum",
                    "worst_contact",
                )
                if key in candidate
            }
            compact["contacts"] = [
                {
                    key: contact[key]
                    for key in (
                        "contact",
                        "minimum_separation_m",
                        "time_to_minimum_s",
                        "final_trend",
                        "classification",
                    )
                    if key in contact
                }
                for contact in candidate.get("contacts") or []
                if isinstance(contact, dict)
            ]
            compact_candidates.append(compact)
        assumptions = table.get("assumptions") or {}
        return {
            "purpose": table.get("purpose"),
            "assumptions": {
                key: assumptions[key]
                for key in (
                    "target_motion",
                    "own_ship_model",
                    "horizon_s",
                    "timestep_s",
                    "temporary_maneuver_distance_m",
                )
                if key in assumptions
            },
            "thresholds": table.get("thresholds") or {},
            "warning": table.get("warning"),
            "candidates": compact_candidates,
        }

    def _truncate_to_tokens(self, text: str, limit: int) -> tuple[str, bool]:
        if not text:
            return "", False
        try:
            tokenizer = _local_tokenizer(self.model)
            tokens = tokenizer.encode(text, add_special_tokens=False)
            if len(tokens) <= limit:
                return text, False
            return tokenizer.decode(tokens[:limit]).rstrip() + "\n[excerpt truncated]", True
        except Exception:
            # Conservative offline fallback for an unavailable tokenizer.
            character_limit = max(0, limit * 2)
            if len(text) <= character_limit:
                return text, False
            return text[:character_limit].rstrip() + "\n[excerpt truncated]", True

    def _compact_evidence_context(self, retrieved: list[Any]) -> tuple[str, bool]:
        """Share the evidence allowance across results so every hit is visible."""
        if not retrieved:
            return "", False
        # Reserve roughly 64 tokens per result for its chunk ID, report title,
        # page label, separators, and truncation marker.
        allowance = max(
            64,
            (CAPTAIN_EVIDENCE_MAX_TOKENS - 64 * len(retrieved)) // len(retrieved),
        )
        entries = []
        any_truncated = False
        for item in retrieved:
            location = f", page {item.page}" if item.page is not None else ""
            header = f"[{item.chunk_id}] {item.report_title}{location}"
            text, truncated = self._truncate_to_tokens(item.text, allowance)
            any_truncated = any_truncated or truncated
            entries.append(f"{header}\n{text}")
        return "\n\n".join(entries), any_truncated

    def _prompt_token_count(self, messages: list[dict[str, str]]) -> int:
        try:
            tokenizer = _local_tokenizer(self.model)
            encoded = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
            )
            if hasattr(encoded, "input_ids"):
                encoded = encoded.input_ids
            elif isinstance(encoded, dict):
                encoded = encoded["input_ids"]
            return len(encoded)
        except Exception:
            # Two UTF-8 characters per token is intentionally conservative for
            # JSON containing punctuation, identifiers, and decimal numbers.
            return sum((len(item.get("content") or "") + 1) // 2 for item in messages) + 32

    def _messages_within_budget(
        self, payload: dict[str, Any]
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        """Serialize and bound a Captain request before it reaches vLLM."""
        payload = deepcopy(payload)
        evidence = payload.get("maib_case_evidence")
        evidence_truncated = bool(
            isinstance(evidence, dict) and evidence.get("truncated_for_prompt")
        )
        if isinstance(evidence, dict):
            excerpts, combined_truncated = self._truncate_to_tokens(
                str(evidence.get("excerpts") or ""),
                CAPTAIN_EVIDENCE_MAX_TOKENS,
            )
            evidence_truncated = evidence_truncated or combined_truncated
            evidence["excerpts"] = excerpts

        def serialize() -> list[dict[str, str]]:
            return [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ]

        budget = (
            self.max_context_tokens
            - self.max_output_tokens
            - CAPTAIN_PROMPT_SAFETY_MARGIN_TOKENS
        )
        messages = serialize()
        initial_tokens = self._prompt_token_count(messages)
        final_tokens = initial_tokens

        # The normal compact contract should fit. If unusual multi-contact or
        # VHF state still exceeds it, remove optional accident prose rather than
        # live observations, the OOW proposal, or consequence results.
        if final_tokens > budget and isinstance(evidence, dict) and evidence.get("excerpts"):
            evidence["excerpts"] = "[omitted: prompt budget; live geometry remains authoritative]"
            evidence_truncated = True
            messages = serialize()
            final_tokens = self._prompt_token_count(messages)

        if final_tokens > budget:
            raise RuntimeError(
                "Captain prompt cannot fit safely after optional evidence compaction: "
                f"{final_tokens} input tokens > {budget} token budget "
                f"({self.max_context_tokens} context - {self.max_output_tokens} output "
                f"- {CAPTAIN_PROMPT_SAFETY_MARGIN_TOKENS} safety margin)"
            )

        budget_record = {
            "context_limit_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens,
            "safety_margin_tokens": CAPTAIN_PROMPT_SAFETY_MARGIN_TOKENS,
            "input_budget_tokens": budget,
            "initial_input_tokens": initial_tokens,
            "final_input_tokens": final_tokens,
            "remaining_input_tokens": budget - final_tokens,
            "evidence_truncated": evidence_truncated,
        }
        return messages, budget_record

    def build_prompt_payload(
        self,
        decision_state: dict[str, Any],
        bridge_snapshot: BridgeSnapshot,
        retrieved_evidence=None,
    ) -> dict[str, Any]:
        """Build the exact observation-only payload used by the critic model.

        Keeping serialization separate from inference lets offline teacher-data
        preparation reuse the runtime contract without calling a model or
        duplicating prompt-construction policy.
        """
        observed_state = bridge_snapshot.state_for_agent()
        proposal = decision_state.get("oow_proposal")
        proposal_candidate = None
        if isinstance(proposal, dict):
            proposal_candidate = {
                "action": proposal.get("action"),
                "degrees": float(proposal.get("degrees") or 0.0),
                "speed_mps": float(proposal.get("speed") or 0.0),
                "label": "OOW proposal",
            }
        consequence_table = build_teacher_candidate_lookahead(
            compact_state(observed_state),
            additional_candidates=[proposal_candidate] if proposal_candidate else None,
            horizon_s=CAPTAIN_LOOKAHEAD_HORIZON_S,
            timestep_s=CAPTAIN_LOOKAHEAD_TIMESTEP_S,
            maneuver_distance_m=MANEUVER_DISTANCE_M,
            minimum_separation_m=RETRIEVAL_CLEARANCE_M,
            critical_separation_m=CRITICAL_CPA_M,
            margin_m=50.0,
        )
        consequence_table["purpose"] = (
            "advisory runtime consequence comparison; never an executable override"
        )
        consequence_table["warning"] = (
            "Approximate constant-target-motion projection. Use it to compare the OOW "
            "proposal with executable alternatives, but also check COLREG obligations, "
            "observed trends, track quality, and uncertainty."
        )
        compact_observed_state = compact_state(observed_state)
        control_summary = self.observations_only_state(
            bridge_snapshot.captain_state_for_agent(
                str(decision_state.get("priority") or "")
            )
        )
        # own_ship and mission duplicate compact_observed_state. Retain only
        # the control-specific fields that are not present in that record.
        control_summary.pop("own_ship", None)
        control_summary.pop("mission", None)
        captain_state = {
            **decision_state,
            # The captain receives measured/derived geometry but no upstream
            # encounter, role, rule, risk, or recommended-action verdict.
            "bridge_state": self.observations_only_state(
                compact_observed_state
            ),
            "bridge_control_summary": control_summary,
            "candidate_action_consequences": self.compact_candidate_consequences(
                consequence_table
            ),
        }
        if retrieved_evidence is None:
            _, retrieved = self.retrieve_evidence(decision_state, bridge_snapshot)
        else:
            retrieved = retrieved_evidence
        self.last_retrieval = [item.as_dict() for item in retrieved]
        if retrieved:
            evidence_context, evidence_truncated = self._compact_evidence_context(
                retrieved
            )
            captain_state["maib_case_evidence"] = {
                "status": "advisory published-investigation reasoning",
                "instructions": (
                    "Live measured geometry is authoritative. Infer COLREG obligations yourself. "
                    "Use the excerpts to identify an analogous causal mechanism or investigator lesson, "
                    "then test whether it actually transfers to the live geometry. Do not copy an accident "
                    "action, infer missing facts, or treat an accident outcome as a prediction. Cite a chunk "
                    "ID only if its published reasoning materially affects the decision reason."
                ),
                "excerpts": evidence_context,
                "truncated_for_prompt": evidence_truncated,
            }
        else:
            captain_state["maib_case_evidence"] = {
                "status": "not retrieved for this cycle",
                "instructions": (
                    "Review the OOW proposal against the live bridge observations "
                    "without accident-case evidence."
                ),
                "excerpts": "",
            }
        captain_state["decision_contract"] = {
            "review": (
                "Independently verify the supplied OOW proposal against the measured "
                "bridge state. Do not search for a disagreement. Approval is the "
                "correct result whenever no material safety defect is established. "
                "Your selected control is the final command."
            ),
            "verdict": (
                "Use approve and reproduce the exact proposed control when the proposal "
                "is safe and reasonable, even if another action might also be reasonable. "
                "Use reject only for a specific observation-supported error that materially "
                "affects safety and requires a different control. A stylistic preference, "
                "uncertain alternative, or desire to be critical is not grounds to reject."
            ),
            "numeric_checks": (
                "Perform literal comparisons: TCPA is within a horizon only when it is "
                "non-negative and less than or equal to that horizon; a larger TCPA is "
                "outside it. Where an envelope requires both CPA and TCPA conditions, "
                "CPA alone does not activate it. Negative closing speed means opening, "
                "and unavailable TCPA does not mean an approaching CPA."
            ),
            "emergency_check": (
                "Stand-on status is not a reason to reject necessary avoiding action when "
                "the give-way vessel is not resolving a close encounter. At short TCPA or "
                "dangerously small CPA, judge the proposed action by whether it improves "
                "separation; do not preserve role formalism at the expense of collision "
                "avoidance."
            ),
            "course_intent": (
                "Select exactly one control variant. Argument-free actions contain only "
                "action. alter_course also requires an explicit port_N or starboard_N "
                "course_intent. set_speed also requires a positive absolute speed."
            ),
            "evidence_assessment": (
                "State analogous live facts, material differences, and the transferable "
                "lesson. Use 'none' when no evidence was retrieved or it does not transfer."
            ),
            "navigation_assessment": (
                "Name the controlling contact (or 'none'), state own role and encounter "
                "phase, and verify in geometry_check that the chosen direction improves "
                "separation from every material contact."
            ),
            "consequence_assessment": (
                f"Treat {RETRIEVAL_CLEARANCE_M:.0f} metres as the hard minimum projected "
                "separation. Compare the OOW proposal with candidate_action_consequences. "
                "Reject a proposal projected below the hard minimum when a feasible "
                "candidate remains at or above it. If every candidate breaches the "
                "minimum, select the action with the strongest defensible separation "
                "while respecting COLREG obligations and explain the residual risk. "
                "Do not claim the approximate projection guarantees the outcome."
            ),
            "approval_wording": (
                "For an approval, oow_critique should state 'No material defect identified' "
                "and briefly confirm the decisive geometry. It is valid to approve without "
                "inventing a criticism."
            ),
        }
        self.last_prompt_payload = captain_state
        return captain_state

    def decide(
        self,
        decision_state: dict[str, Any],
        bridge_snapshot: BridgeSnapshot,
        retrieved_evidence=None,
    ) -> CaptainDecision:
        """Ask the captain LLM to select the tactical action."""

        captain_state = self.build_prompt_payload(
            decision_state,
            bridge_snapshot,
            retrieved_evidence=retrieved_evidence,
        )

        messages, self.last_prompt_budget = self._messages_within_budget(captain_state)
        # Store the exact compacted payload sent to the model for auditability.
        captain_state = json.loads(messages[1]["content"])
        self.last_prompt_payload = captain_state
        structured = None
        last_error: Exception | None = None
        for attempt in range(2):
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=self.max_output_tokens,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "captain_decision",
                        "strict": True,
                        "schema": CaptainRagDecision.model_json_schema(),
                    },
                },
            )
            content = response.choices[0].message.content
            if not content:
                last_error = RuntimeError("Captain returned no decision")
            else:
                try:
                    candidate = CaptainRagDecision.model_validate_json(content)
                    self.validate_rag_control_against_state(
                        candidate,
                        captain_state["bridge_state"],
                        decision_state.get("oow_proposal"),
                    )
                    structured = candidate
                    break
                except (ValueError, RuntimeError) as exc:
                    last_error = exc
            if attempt == 0:
                messages.extend([
                    {"role": "assistant", "content": content or ""},
                    {
                        "role": "user",
                        "content": (
                            "Your decision violated the control contract: "
                            f"{last_error}. Return a corrected JSON decision. "
                            "Do not change or invent bridge observations."
                        ),
                    },
                ])
        if structured is None:
            raise ValueError(f"Captain returned invalid decisions twice: {last_error}")
        self.last_course_intent = getattr(structured.control, "course_intent", "none")
        self.last_navigation_assessment = {
            "oow_verdict": structured.oow_verdict,
            "oow_critique": structured.oow_critique,
            "controlling_contact": structured.controlling_contact,
            "own_role": structured.own_role,
            "encounter_phase": structured.encounter_phase,
            "geometry_check": structured.geometry_check,
        }
        self.last_evidence_assessment = {
            "analogy": structured.evidence_analogy,
            "difference": structured.evidence_difference,
            "lesson": structured.transferable_lesson,
        }
        return structured.to_navigation_decision()

    @staticmethod
    def validate_rag_control_against_state(
        decision: CaptainRagDecision,
        state: dict[str, Any],
        oow_proposal: dict[str, Any] | None = None,
    ) -> None:
        """Validate model intent against active control without choosing an action."""
        current = state.get("current_action") or state.get("current_maneuver")
        active = bool(
            isinstance(current, dict)
            and current.get("action") == "alter_course"
            and (
                state.get("action_underway")
                or "temporary_waypoint_active" in current
            )
        )
        if decision.control.action == "rejoin_route" and not active:
            raise ValueError("rejoin_route requires an active maneuver")
        if active and current.get("action") == "alter_course":
            selected = decision.control
            if selected.action == "alter_course":
                direction, magnitude = selected.course_intent.split("_", 1)
                degrees = float(magnitude) * (-1.0 if direction == "port" else 1.0)
                if degrees == float(current.get("degrees") or 0.0):
                    raise ValueError(
                        "cannot reissue the same active course alteration; use "
                        "maintain_course to continue it"
                    )
        if not isinstance(oow_proposal, dict):
            raise ValueError("captain review requires an OOW proposal")
        selected = decision.to_navigation_decision()
        same_control = (
            selected.action == oow_proposal.get("action")
            and abs(selected.degrees - float(oow_proposal.get("degrees") or 0.0)) < 1e-9
            and abs(selected.speed - float(oow_proposal.get("speed") or 0.0)) < 1e-9
        )
        if decision.oow_verdict == "approve" and not same_control:
            raise ValueError("oow_verdict=approve requires the exact proposed control")
        if decision.oow_verdict == "reject" and same_control:
            raise ValueError("oow_verdict=reject requires a different final control")

    def retrieve_evidence(
        self,
        decision_state: dict[str, Any],
        bridge_snapshot: BridgeSnapshot,
    ):
        bridge_state = bridge_snapshot.state_for_agent()
        captain_state = {
            **decision_state,
            "bridge_state": bridge_snapshot.captain_state_for_agent(
                str(decision_state.get("priority") or "")
            ),
        }
        relevant_contacts = self.navigation_relevant_contacts(bridge_state)
        if not relevant_contacts:
            query = "not requested: no navigation-relevant closing contact"
            self.last_retrieval_query = query
            self.last_retrieval = []
            self.last_retrieval_routing = {
                "method": "numeric_cpa_tcpa_gate",
                "clearance_m": RETRIEVAL_CLEARANCE_M,
                "contacts": [],
                "visible_to_captain": False,
            }
            return query, []
        query = natural_query_from_state(decision_state, bridge_state)
        preferences = retrieval_preferences_from_state(
            bridge_state, relevant_contacts
        )
        self.last_retrieval_routing = {
            "method": "numeric_observations_only",
            "clearance_m": RETRIEVAL_CLEARANCE_M,
            "contacts": [
                {
                    "name": contact.get("name"),
                    "closing_speed": contact.get("closing_speed"),
                    "cpa_distance": contact.get("cpa_distance"),
                    "tcpa_s": contact.get("tcpa_s"),
                    "relative_bearing": contact.get("relative_bearing"),
                    "heading": (contact.get("nav") or {}).get("heading"),
                    "speed": (contact.get("nav") or {}).get("speed"),
                }
                for contact in relevant_contacts
            ],
            "metadata_preferences": {
                key: sorted(value) for key, value in preferences.items()
            },
            "visible_to_captain": False,
        }
        results = (
            self.maib_retriever.retrieve(
                query,
                metadata_preferences=preferences,
            )
            if self.maib_retriever is not None
            else []
        )
        self.last_retrieval_query = query
        self.last_retrieval = [item.as_dict() for item in results]
        return query, results

    def validate_decision(
        self,
        decision: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        action = decision.get("action")

        if action not in NAVIGATION_ACTIONS:
            raise ValueError(f"Unsupported action: {action!r}")

        degrees = self.number(decision.get("degrees"), 0.0)
        distance_m = MANEUVER_DISTANCE_M if action == "alter_course" else 0.0
        speed = self.number(decision.get("speed"), 0.0)
        reason = str(decision.get("reason", "")).strip()

        if not reason:
            reason = "No reason provided."

        if action == "alter_course":
            if degrees == 0.0:
                raise ValueError("alter_course requires a non-zero course change")
            if speed < 0.0:
                raise ValueError("alter_course speed cannot be negative")

        elif action == "set_speed":
            if speed <= 0:
                raise ValueError("set_speed requires a positive target speed")

        else:
            if degrees != 0.0 or speed != 0.0:
                raise ValueError(
                    f"{action} requires degrees=0 and speed=0; "
                    "the agent decision was not modified"
                )

        return {
            "action": action,
            "reason": reason,
            "degrees": degrees,
            "distance_m": distance_m,
            "speed": speed,
            "auxiliary_commands": decision.get("auxiliary_commands", []),
        }

    def command_from_decision(
        self,
        decision: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        action = decision["action"]
        auxiliary_commands = decision.get("auxiliary_commands", [])

        def with_auxiliary(command: dict[str, Any] | None) -> dict[str, Any] | None:
            if not auxiliary_commands:
                return command
            commands = ([] if command is None else [command]) + [
                {
                    "type": "command",
                    "class": "communication",
                    "action": auxiliary["type"],
                    **{key: value for key, value in auxiliary.items() if key != "type"},
                }
                for auxiliary in auxiliary_commands
            ]
            return {"type": "commands", "commands": commands}
        control_state = state.get("control", state.get("control_state", {}))

        paused = self.is_paused(state)
        current_speed = self.get_current_speed(state)

        # Emergency stop can interrupt another maneuver.
        if action == "stop":
            if paused:
                return with_auxiliary(None)

            return with_auxiliary({
                "type": "command",
                "class": "mode",
                "action": "stop",
            })

        if action == "skip":
            return None

        if action == "resume":
            # Resume also recovers an unexpected stall. A vessel can have no
            # active speed objective while LLM_STOP/paused remains false.
            if not paused and current_speed > 0.2:
                return with_auxiliary(None)

            return with_auxiliary({
                "type": "command",
                "class": "mode",
                "action": "resume",
            })

        if action == "maintain_course":
            return with_auxiliary(None)

        if action == "rejoin_route":
            return with_auxiliary({
                "type": "command",
                "class": "maneuver",
                "action": "rejoin_route",
            })

        if action == "alter_course":
            threats = [
                contact for contact in state.get("contacts", [])
                if isinstance(contact, dict)
                and contact.get("risk") in {"critical", "high", "medium"}
                and self.number(contact.get("closing_speed"), 0.0) > 0.0
            ]
            designated = min(
                threats,
                key=lambda contact: self.number(
                    contact.get("tcpa_s"), float("inf")
                ),
                default={},
            )
            return with_auxiliary({
                "type": "command",
                "class": "maneuver",
                "action": "alter_course",
                "contact": designated.get("name") or "",
                "degrees": self.number(decision.get("degrees"), 0.0),
                "distance_m": self.number(
                    decision.get("distance_m"), MANEUVER_DISTANCE_M
                ),
                # alter_course changes route geometry only.  Feeding measured
                # speed back into the route update can lock in transient
                # deceleration and eventually stall the vessel.
                "speed": 0.0,
            })

        if action == "set_speed":
            requested_speed = self.number(decision.get("speed"), 0.0)

            return with_auxiliary({
                "type": "command",
                "class": "speed",
                "action": "set_speed",
                "speed": requested_speed,
            })

        # Unknown navigation actions issue no helm command, but auxiliary
        # commands are still independently dispatchable.
        return with_auxiliary(None)
