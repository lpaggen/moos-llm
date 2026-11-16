"""Direct Officer-of-the-Watch decision agent used by base and tuned models."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents.schemas.captain import CaptainDecision
from agents.skill_loader import load_skill
from agents.teacher_lookahead import build_teacher_candidate_lookahead
from agents.colregs.paper_state_machine import PaperColregsStateMachine
from bridge.observations import (
    apply_encounter_persistence,
    compact_state,
)
from bridge.narratives import (
    BOW_CROSSING_M,
    CAUTION_CPA_M,
    CRITICAL_CPA_M,
    EMERGENCY_TCPA_S,
    INTERVENTION_TCPA_S,
    MIN_SAFE_CPA_M,
    PLANNING_TCPA_S,
    render_with_history,
)
from tools.config_value import get

from experiments.qwen8b_colregs_rag.oow import SYSTEM_PROMPT as COLREG_RAG_SYSTEM_PROMPT
from experiments.qwen8b_colregs_rag.retrieval import ColregsRetriever, navigation_queries
from experiments.colregs_decision_tree.selection import DecisionTreeGuidance


TEACHER_NAVIGATION_PROMPT = load_skill("captain_teacher")
SYSTEM_PROMPT = load_skill("oow_decision")
SFT_ABLATION_SYSTEM_PROMPT = load_skill("oow_sft_ablation")
SFT_DIRECTIONAL_SYSTEM_PROMPT = load_skill("oow_sft_directional")
BASE_RUNTIME_CONTRACT = """

## Direct OOW runtime contract

You are making the executable decision directly; there is no separate captain
between your response and the deterministic command validator. Follow the
allowed actions supplied with the current briefing. When stopped or paused,
resume when clear and use skip only while a specific observed hazard makes
resuming unsafe. Return exactly one JSON object with action, degrees, speed,
reason, and auxiliary_commands. Ground the reason in observed facts. The reason
must concisely cite every COLREG Rule that directly applies to the controlling
encounter and chosen action (for example, "Rules 13 and 16"). Do not omit a
directly applicable rule, add merely general rules, or invent a rule citation.
/no_think
"""
# Keep the direct OOW prompt independent from the Captain-critic skill.  The
# latter has a different input/output contract and must never leak into SFT or
# direct OOW inference.  The trailing newline preserves the prompt used by the
# audited base-narrative baseline (SHA-256 44cbd145...).
BASE_SYSTEM_PROMPT = load_skill("oow_base_narrative") + "\n"
VHF_SYSTEM_PROMPT = load_skill("oow_vhf")
TEACHER_SYSTEM_PROMPT = TEACHER_NAVIGATION_PROMPT + BASE_RUNTIME_CONTRACT
ERIKSEN_RUNTIME_CONTRACT = """

## Deterministic Eriksen COLREG classification

The current briefing includes a deterministic COLREG assessment produced from
the same observed vessel kinematics by the persistent Eriksen et al. (2020)
state-machine classifier. Use its persistent encounter state, current
geometrical state, own-ship role, applicable Rules and concise rule summaries
as authoritative classification evidence under the classifier's stated
assumptions. The persistent state may lag the instantaneous geometrical state
because transition thresholds and hysteresis suppress unstable switching.

The assessment lists the classifier's complete applicable Rule set for each
contact. This includes the standing obligations in Rules 2, 5, 6, and 7 as well
as encounter-specific Rules. Treat the reported own-ship role and complete Rule
set as authoritative for the classified encounter, and cite that complete set
in the decision reason. In this constrained profile, those standing Rules are
part of the applicable set rather than unsupported general additions.

This classifier does not prescribe a maneuver. You remain responsible for
selecting the safest, COLREG-compliant action, considering every reported
contact, mission progress, CPA/TCPA and how the situation is evolving.
"""
RAW_STATE_RUNTIME_CONTRACT = """

## Raw-state input format

For this experiment, the user message contains a processed numeric `state`
object instead of a natural-language briefing. Positions, ranges, CPA and
mission distances are in metres. Speeds and closing speeds are in metres per
second. Headings and relative bearings are in degrees. TCPA, observation age
and timestamps are in seconds. `projection_status` states whether CPA/TCPA are
valid, beyond the planning horizon, or have no future closest approach.
Use only these supplied values; do not assume a prose briefing is present.
"""
DEFAULT_MODEL = get("models", "default_local")
DEFAULT_MAX_OUTPUT_TOKENS = int(get("llm", "officer_max_output_tokens"))
DEFAULT_HISTORY_SIZE = int(get("oow", "history_size", fallback="3"))
DEFAULT_MAX_HISTORY_GAP_S = float(get("oow", "max_history_gap_s", fallback="120"))
DEFAULT_STALE_AGE_S = float(get("lookout", "stale_after_s"))
DEFAULT_TEACHER_TRIGGER_RANGE_M = 1500.0
COURSE_INTENT_DEGREES = 30.0
LARGE_COURSE_INTENT_DEGREES = 45.0


def _render_eriksen_assessment(oracle: dict[str, Any]) -> str:
    """Render only the classifier conclusions useful to a small decision model."""
    def relationship(contact: dict[str, Any]) -> str:
        state = contact.get("state")
        name = contact.get("contact") or "the contact"
        role = contact.get("own_role")
        if state == "OT":
            if role == "give_way":
                return (
                    f"Own ship is overtaking {name}. Own ship must keep clear until "
                    f"it is finally past and clear"
                )
            return f"{name} is overtaking own ship. Own ship should stand on"
        if state == "HO":
            return (
                f"Own ship and {name} are meeting head-on. Both vessels should alter "
                f"course to starboard"
            )
        if state == "GW":
            return (
                f"{name} is crossing from starboard. Own ship is the give-way vessel "
                f"and should avoid crossing ahead"
            )
        if state == "SO":
            return (
                f"{name} is crossing from port. Own ship is the stand-on vessel and "
                f"should initially keep course and speed"
            )
        if state == "EM":
            return f"There is an immediate close-quarters danger with {name}"
        return f"No active COLREG encounter exists with {name}"

    lines = ["COLREG ASSESSMENT"]
    contacts = oracle.get("contacts") or []
    active_contacts = [contact for contact in contacts if contact.get("state") != "SF"]
    primary = oracle.get("primary_contact") if active_contacts else None
    if primary:
        lines.extend(["", f"Most important contact: {primary}."])
    if not contacts:
        lines.extend(["", "No contacts are available for classification."])
    for contact in contacts:
        trend = contact.get("evolution_since_previous_snapshot") or {}
        trend_text = {
            "first_observation": "This is the first observation of the contact.",
            "closing": "The range is decreasing.",
            "opening": "The range is increasing.",
            "approximately_steady": "The range is approximately steady.",
        }.get(trend.get("assessment"), "The recent range trend is unavailable.")
        lines.extend(["", f"- {relationship(contact)}. {trend_text}"])
        state_name = contact.get("state_name") or "unknown"
        geometrical_state = contact.get("geometrical_state") or "unknown"
        lines.append(
            "  Classifier details: "
            f"persistent state {contact.get('state') or 'unknown'} ({state_name}); "
            f"current geometrical state {geometrical_state}; "
            f"own-ship role {contact.get('own_role') or 'unknown'}."
        )
        for rule, summary in (
            contact.get("applicable_rule_summaries") or {}
        ).items():
            lines.append(f"  Rule {rule}: {summary}")
    return "\n".join(lines)


def _openai_compatible_schema(value: Any) -> Any:
    """Translate Pydantic unions to the JSON Schema subset OpenAI accepts."""
    if isinstance(value, dict):
        translated = {
            key: _openai_compatible_schema(item)
            for key, item in value.items()
            if key != "discriminator"
        }
        if "oneOf" in translated:
            translated["anyOf"] = translated.pop("oneOf")
        return translated
    if isinstance(value, list):
        return [_openai_compatible_schema(item) for item in value]
    return value


def _canonicalize_unused_decision_fields(payload: Any) -> dict[str, Any]:
    """Zero numeric fields that have no meaning for the selected action."""
    if not isinstance(payload, dict):
        raise ValueError("OOW decision must be a JSON object")
    canonical = dict(payload)
    action = canonical.get("action")
    if action != "alter_course":
        canonical["degrees"] = 0.0
    if action != "set_speed":
        canonical["speed"] = 0.0
    return canonical


def _resolve_directional_decision_fields(payload: Any) -> dict[str, Any]:
    """Convert categorical helm output to the signed internal command format."""
    if not isinstance(payload, dict):
        raise ValueError("OOW decision must be a JSON object")
    canonical = dict(payload)
    action = canonical.get("action")
    direction = canonical.pop("turn_direction", None)
    magnitude = canonical.pop("turn_degrees", None)
    if not isinstance(magnitude, (int, float)):
        raise ValueError("turn_degrees must be numeric")
    magnitude = float(magnitude)
    if action == "alter_course":
        if direction not in {"port", "starboard"}:
            raise ValueError("alter_course requires turn_direction port or starboard")
        if not 0.0 < magnitude <= 60.0:
            raise ValueError("alter_course requires turn_degrees in (0, 60]")
        canonical["degrees"] = magnitude if direction == "starboard" else -magnitude
    else:
        if direction != "none" or magnitude != 0.0:
            raise ValueError(
                "non-alter_course actions require turn_direction none and turn_degrees 0"
            )
        canonical["degrees"] = 0.0
    return canonical


class AgentOOW:
    """Render deterministic bridge evidence and choose one executable action."""

    def __init__(
        self,
        *,
        client: Any,
        model: str | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        history_size: int = DEFAULT_HISTORY_SIZE,
        max_history_gap_s: float = DEFAULT_MAX_HISTORY_GAP_S,
        stale_age_s: float = DEFAULT_STALE_AGE_S,
    ) -> None:
        self.client = client
        self.model = model or os.environ.get("MOOS_LLM_OOW_MODEL", DEFAULT_MODEL)
        self.mode = os.environ.get("MOOS_LLM_OOW_MODE", "base").strip().lower()
        if self.mode not in {"base", "finetuned"}:
            raise ValueError("MOOS_LLM_OOW_MODE must be 'base' or 'finetuned'")
        self.finetuned_prompt_profile = os.environ.get(
            "MOOS_LLM_FINETUNED_PROMPT_PROFILE", "compact"
        ).strip().lower()
        if self.finetuned_prompt_profile not in {
            "compact",
            "base-narrative",
            "narrative-only",
            "narrative-directional",
        }:
            raise ValueError(
                "MOOS_LLM_FINETUNED_PROMPT_PROFILE must be 'compact', "
                "'base-narrative', 'narrative-only', or 'narrative-directional'"
            )
        self.max_output_tokens = max_output_tokens
        self.history_size = max(1, history_size)
        self.max_history_gap_s = max_history_gap_s
        self.stale_age_s = stale_age_s
        self.reasoning_enabled = os.environ.get(
            "MOOS_LLM_REASONING", "off"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.openai_api = os.environ.get(
            "MOOS_LLM_OPENAI_API", "off"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.openai_prompt_profile = os.environ.get(
            "MOOS_LLM_OPENAI_PROMPT_PROFILE", "teacher"
        ).strip().lower()
        if self.openai_prompt_profile not in {"teacher", "runtime-base"}:
            raise ValueError(
                "MOOS_LLM_OPENAI_PROMPT_PROFILE must be 'teacher' or 'runtime-base'"
            )
        self.input_format = os.environ.get(
            "MOOS_LLM_OOW_INPUT_FORMAT", "narrative"
        ).strip().lower()
        if self.input_format not in {"narrative", "raw-state"}:
            raise ValueError(
                "MOOS_LLM_OOW_INPUT_FORMAT must be 'narrative' or 'raw-state'"
            )
        self.openai_reasoning_effort = os.environ.get(
            "MOOS_LLM_OPENAI_REASONING_EFFORT", "low"
        ).strip().lower()
        self.last_response_metadata: dict[str, Any] | None = None
        self.last_model_decision: dict[str, Any] | None = None
        self.teacher_trigger_range_m = float(os.environ.get(
            "MOOS_LLM_TEACHER_TRIGGER_RANGE_M", DEFAULT_TEACHER_TRIGGER_RANGE_M
        ))
        if self.teacher_trigger_range_m <= 0.0:
            raise ValueError("MOOS_LLM_TEACHER_TRIGGER_RANGE_M must be positive")
        self.encounter_only = os.environ.get(
            "MOOS_LLM_ENCOUNTER_ONLY", "off"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.teacher_lookahead_enabled = os.environ.get(
            "MOOS_LLM_TEACHER_LOOKAHEAD", "on"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.teacher_lookahead_horizon_s = float(os.environ.get(
            "MOOS_LLM_TEACHER_LOOKAHEAD_HORIZON_S", "600"
        ))
        self.teacher_lookahead_timestep_s = float(os.environ.get(
            "MOOS_LLM_TEACHER_LOOKAHEAD_TIMESTEP_S", "1"
        ))
        self.teacher_lookahead_margin_m = float(os.environ.get(
            "MOOS_LLM_TEACHER_LOOKAHEAD_MARGIN_M", "50"
        ))
        self.maneuver_distance_m = float(os.environ.get(
            "MOOS_LLM_MANEUVER_DISTANCE_M", get("ship", "DEFAULT_MANEUVER_DISTANCE_M")
        ))
        self.paper_colregs = PaperColregsStateMachine()
        self.last_paper_colregs: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.active_encounters: dict[str, str] = {}
        self.vessel_types: dict[str, str] = {}
        self.vhf_benchmark_enabled = os.environ.get(
            "MOOS_LLM_VHF_BENCHMARK", "off"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if self.mode == "base" or self.finetuned_prompt_profile == "base-narrative":
            self.system_prompt = BASE_SYSTEM_PROMPT
        elif self.finetuned_prompt_profile == "narrative-only":
            self.system_prompt = SFT_ABLATION_SYSTEM_PROMPT
        elif self.finetuned_prompt_profile == "narrative-directional":
            self.system_prompt = SFT_DIRECTIONAL_SYSTEM_PROMPT
        else:
            self.system_prompt = SYSTEM_PROMPT
        if self.input_format == "raw-state":
            if self.mode != "base" or self.openai_api:
                raise ValueError("raw-state input is supported only by the local base OOW")
            self.system_prompt = self.system_prompt.replace(
                "The deterministic briefing uses metres for positions and distances, knots for\n"
                "speeds, and seconds for times.",
                "The processed numeric state uses the units defined in the raw-state input contract.",
            ).replace(
                "allowed actions supplied with the current briefing",
                "allowed actions supplied with the current input",
            )
            self.system_prompt += RAW_STATE_RUNTIME_CONTRACT
        if self.vhf_benchmark_enabled:
            no_think = "/no_think" in self.system_prompt
            self.system_prompt = self.system_prompt.replace("/no_think", "").rstrip()
            self.system_prompt = f"{self.system_prompt}\n\n{VHF_SYSTEM_PROMPT}"
            if no_think:
                self.system_prompt += "\n/no_think"
        self.eriksen_prompt_enabled = (
            self.mode == "base"
            and os.environ.get("MOOS_LLM_ERIKSEN_PROMPT", "off").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if self.eriksen_prompt_enabled:
            self.system_prompt += ERIKSEN_RUNTIME_CONTRACT
        self.colregs_retriever: ColregsRetriever | None = None
        self.last_colregs_retrieval: dict[str, Any] | None = None
        self.colregs_tree: DecisionTreeGuidance | None = None
        tree_enabled = os.environ.get("MOOS_LLM_COLREG_TREE", "off").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if os.environ.get("MOOS_LLM_COLREG_RAG", "off").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            if tree_enabled:
                raise ValueError("Choose COLREG RAG or decision tree, not both")
            corpus = os.environ.get("MOOS_LLM_COLREG_CORPUS", "agents/colregs/colregs.json")
            self.colregs_retriever = ColregsRetriever(
                Path(corpus),
                candidate_k=int(os.environ.get("MOOS_LLM_COLREG_CANDIDATE_K", "12")),
                top_k=int(os.environ.get("MOOS_LLM_COLREG_TOP_K", "6")),
            )
            self.system_prompt = (
                f"{self.system_prompt}\n\n## COLREG retrieval instructions\n"
                f"{COLREG_RAG_SYSTEM_PROMPT}"
            )
        if tree_enabled:
            self.colregs_tree = DecisionTreeGuidance()
            tree_prompt = COLREG_RAG_SYSTEM_PROMPT.replace(
                "retrieved verbatim COLREG provisions", "selected simplified COLREG guidance"
            ).replace("retrieved text", "selected guidance")
            self.system_prompt += f"\n\n## COLREG decision-tree instructions\n{tree_prompt}"
        if self.reasoning_enabled:
            self.system_prompt = self.system_prompt.replace("/no_think", "").rstrip()
            self.system_prompt += "\n\n/think"
        if self.openai_api:
            if self.mode == "base" and self.openai_prompt_profile == "teacher":
                trigger_text = f"{self.teacher_trigger_range_m:,.0f}"
                clearance_text = f"{MIN_SAFE_CPA_M:,.0f}"
                self.system_prompt = TEACHER_SYSTEM_PROMPT.replace(
                    "1,500", trigger_text
                ).rstrip()
                # Keep the teacher's mental model aligned with the actual
                # bridge command. Random campaigns use 500 m; Imazu uses one
                # nautical mile through the navigation profile.
                maneuver_text = f"{self.maneuver_distance_m:,.0f} metres"
                self.system_prompt = self.system_prompt.replace(
                    "1 nautical mile (1,852 metres)", maneuver_text
                ).replace(
                    "temporary 1 NM path", f"temporary {maneuver_text} path"
                ).replace(
                    "active 1-NM waypoint", f"active {maneuver_text} waypoint"
                )
                self.system_prompt += f"""

## Teacher-data clearance contract

The complete mission is eligible for supervised training only if it reaches the
destination and its audited minimum observed centre-to-centre separation never
falls below {clearance_text} metres. Treat {clearance_text} metres as a hard
lower bound throughout the mission, not merely as an emergency threshold or a
desirable projected CPA. Act early enough to retain a practical margin above
that bound, account for every tracked contact, and verify from later observations
that the chosen action is effective. A predicted CPA above the bound does not
excuse allowing the observed separation to fall below it. Never claim that the
bound was met unless the supplied observations support that claim.
"""
                if self.teacher_lookahead_enabled:
                    self.system_prompt += """

## Privileged candidate lookahead

The user payload contains `teacher_candidate_lookahead`, an approximate
comparison of executable candidate actions across every tracked contact. Use it
to reject candidates projected below the hard minimum, prefer a practical safety
margin, and avoid solving one encounter by creating another. It assumes targets
maintain constant course and speed and approximates own-ship dynamics, so combine
it with the observed trends, uncertainty, and authoritative COLREG role rather
than treating it as ground truth. You may choose a different allowed action when
the complete evidence justifies it; this table never executes or overrides a
command. The student will not receive this table: never mention the table,
lookahead, candidate projections, or other privileged evidence in `reason`.
"""
            if self.openai_prompt_profile == "teacher":
                self.system_prompt = self.system_prompt.replace("/no_think", "").replace(
                    "/think", ""
                ).rstrip()

    def decision_trigger(self, transformed: dict[str, Any]) -> dict[str, Any]:
        """Determine whether a decision is operationally useful.

        Runtime encounter gating is opt-in.  It changes only whether inference
        is called; it never supplies the Eriksen classification to the model or
        overrides a model action.
        """
        teacher_gate = self.openai_api and self.mode == "base"
        if not teacher_gate and not self.encounter_only:
            return {"call_model": True, "reason": "non_teacher_mode"}
        if transformed.get("vhf_communications"):
            return {"call_model": True, "reason": "active_vhf_communication"}
        own_ship = transformed.get("state", {}).get("own_ship", {})
        if own_ship.get("stopped") or own_ship.get("paused") or str(
            own_ship.get("movement_status") or ""
        ).lower() in {"paused", "stalled", "stopped"}:
            return {"call_model": True, "reason": "stopped_or_paused"}

        oracle = self.last_paper_colregs or {}
        active = [
            contact for contact in oracle.get("contacts", [])
            if contact.get("state") != "SF"
        ]
        if active:
            return {
                "call_model": True,
                "reason": "active_colregs_encounter",
                "contacts": [contact.get("contact") for contact in active],
            }

        nearby = [
            contact for contact in oracle.get("contacts", [])
            if contact.get("range_m") is not None
            and float(contact["range_m"]) <= self.teacher_trigger_range_m
        ]
        if nearby:
            return {
                "call_model": True,
                "reason": "contact_within_trigger_range",
                "trigger_range_m": self.teacher_trigger_range_m,
                "contacts": [contact.get("contact") for contact in nearby],
            }
        return {
            "call_model": False,
            "reason": "no_relevant_contact",
            "trigger_range_m": self.teacher_trigger_range_m,
            "nearest_contact_m": min(
                (float(contact["range_m"]) for contact in oracle.get("contacts", [])
                 if contact.get("range_m") is not None),
                default=None,
            ),
        }

    def deterministic_input(
        self,
        bridge_state: dict[str, Any],
        vhf_advisories: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Apply the same deterministic transform used to prepare OOW SFT data."""
        relevant_state = bridge_state
        # Only the legacy privileged teacher profile narrows the scene.  The
        # runtime-base teacher is a data generator for the local runtime, so
        # its rendered prompt must retain exactly the same contacts.
        if (
            self.openai_api
            and self.mode == "base"
            and self.openai_prompt_profile == "teacher"
        ):
            relevant_state = dict(bridge_state)
            relevant_state["contacts"] = [
                contact
                for contact in bridge_state.get("contacts", [])
                if contact.get("range") is not None
                and float(contact["range"]) <= self.teacher_trigger_range_m
            ]
        self.last_paper_colregs = self.paper_colregs.classify(relevant_state)
        persistent = apply_encounter_persistence(
            relevant_state,
            self.active_encounters,
            self.vessel_types,
        )
        state = compact_state(persistent)
        observed_at = state.get("observed_at_s")
        previous_at = self.history[-1].get("observed_at_s") if self.history else None
        if (
            previous_at is not None
            and observed_at is not None
            and (
                float(observed_at) <= float(previous_at)
                or float(observed_at) - float(previous_at) > self.max_history_gap_s
            )
        ):
            self.history.clear()
        self.history.append(state)
        self.history = self.history[-self.history_size :]

        own_ship = state.get("own_ship", {})
        movement_status = str(own_ship.get("movement_status") or "").lower()
        stopped = bool(
            own_ship.get("stopped")
            or own_ship.get("paused")
            or movement_status in {"paused", "stalled", "stopped"}
            or own_ship.get("speed") == 0.0
        )
        action_underway = bool(state.get("current_maneuver"))
        enriched = dict(state)
        enriched["allowed_actions"] = [
            "maintain_course",
            "alter_course",
            "set_speed",
            "stop",
            "resume",
            "skip",
        ]
        if stopped:
            enriched["allowed_actions"] = ["resume", "skip"]
        current_maneuver = state.get("current_maneuver") or {}
        if not stopped and current_maneuver.get("action") == "alter_course":
            enriched["allowed_actions"].insert(2, "rejoin_route")
        narrative = render_with_history(self.history, self.stale_age_s)
        vhf_communications: list[dict[str, Any]] = []
        if self.vhf_benchmark_enabled:
            for report in (vhf_advisories or [])[-8:]:
                advisory = report.get("advisory") if isinstance(report, dict) else None
                if not isinstance(advisory, dict):
                    continue
                vhf_communications.append({
                    "message_id": advisory.get("message_id"),
                    "channel": (report.get("source") or {}).get("channel"),
                    "scenario_id": (report.get("source") or {}).get("scenario_id"),
                    "scenario_event_id": (report.get("source") or {}).get(
                        "scenario_event_id"
                    ),
                    "speaker": advisory.get("speaker"),
                    "addressee": advisory.get("addressee"),
                    "addressed_to_own_ship": advisory.get("addressed_to_own_ship"),
                    "source_type": advisory.get("source_type"),
                    "identity_status": advisory.get("identity_status"),
                    "intent": advisory.get("intent"),
                    "message": advisory.get("message"),
                    "requested_action": advisory.get("requested_action"),
                    "requested_turn_direction": advisory.get(
                        "requested_turn_direction", "none"
                    ),
                    "requested_turn_degrees": advisory.get("requested_turn_degrees"),
                    "requested_speed_knots": advisory.get("requested_speed_knots"),
                    "requested_course_degrees": advisory.get(
                        "requested_course_degrees"
                    ),
                    "directive_lifetime": advisory.get(
                        "directive_lifetime", "transient"
                    ),
                    "directive_end_condition": advisory.get(
                        "directive_end_condition"
                    ),
                    "reported_facts": advisory.get("reported_facts", []),
                    "confidence": advisory.get("confidence"),
                    "clarification_required": advisory.get("clarification_required"),
                    "age_cycles": report.get("age_cycles", 0),
                    "context_status": report.get("context_status", "newly_received"),
                })
        if vhf_communications:
            recent_calls = []
            active_instructions = []
            for item in vhf_communications:
                requested_turn = item.get("requested_turn_direction") or "none"
                if item.get("requested_turn_degrees") is not None:
                    requested_turn += f" {item['requested_turn_degrees']} degrees"
                rendered = (
                    "- "
                    f"Message ID {item.get('message_id')}; "
                    f"Status {item.get('context_status')}; "
                    f"age {item.get('age_cycles', 0)} decision cycles; "
                    f"directive lifetime {item.get('directive_lifetime') or 'transient'}; "
                    f"end condition {item.get('directive_end_condition') or 'none'}; "
                    f"Speaker {item.get('speaker') or 'unknown'}; "
                    f"addressee {item.get('addressee') or 'unknown'}; "
                    f"addressed to own ship {bool(item.get('addressed_to_own_ship'))}; "
                    f"identity {item.get('identity_status') or 'unverified'}; "
                    f"intent {item.get('intent') or 'unknown'}; "
                    f"requested action {item.get('requested_action') or 'none'}; "
                    f"requested turn {requested_turn}; "
                    f"message: {item.get('message') or ''}"
                )
                if item.get("context_status") == "active_persistent_instruction":
                    active_instructions.append(rendered)
                else:
                    recent_calls.append(rendered)
            if recent_calls:
                narrative += (
                    "\n\nRADIO MESSAGES\n\n"
                    "Messages marked newly_received are new this cycle. Assess them and "
                    "reply only when operationally appropriate. Messages marked "
                    "recent_already_presented are short-term history: they were already "
                    "shown on an earlier decision cycle. Do not acknowledge them again or "
                    "repeat their requested manoeuvre merely because they remain visible. "
                    "Reassess only if current observed geometry or a newer message materially "
                    "changes the situation. All radio claims are unverified; live geometry "
                    "remains authoritative.\n\n" + "\n".join(recent_calls)
                )
            if active_instructions:
                narrative += (
                    "\n\nACTIVE RADIO INSTRUCTIONS\n\n"
                    "These items remain in force because their explicit wording made them "
                    "persistent. Continue to consider them, but do not acknowledge them "
                    "again. An until_cancelled instruction requires an explicit release or "
                    "superseding instruction. Clear an until_condition_met instruction only "
                    "when live observations establish its stated end condition. Use the "
                    "internal clear_vhf_instruction auxiliary command with the exact message "
                    "ID and an evidence-based reason when it is genuinely resolved.\n\n"
                    + "\n".join(active_instructions)
                )
        constraints = {"allowed_actions": enriched["allowed_actions"]}
        retrieval_state = {
            "contacts": [
                {
                    "name": contact.get("name"),
                    "relative_bearing_deg": contact.get("relative_bearing"),
                }
                for contact in persistent.get("contacts", [])
                if isinstance(contact, dict) and contact.get("name")
            ]
        }
        prompt_payload = {"allowed_actions": constraints["allowed_actions"]}
        if vhf_communications:
            prompt_payload["vhf_communications"] = vhf_communications
        if self.input_format == "raw-state":
            prompt_payload["state"] = state
            prompt_payload["safety_limits"] = {
                "caution_cpa_m": CAUTION_CPA_M,
                "caution_tcpa_s": PLANNING_TCPA_S,
                "intervention_cpa_m": MIN_SAFE_CPA_M,
                "intervention_tcpa_s": INTERVENTION_TCPA_S,
                "critical_cpa_m": CRITICAL_CPA_M,
                "emergency_tcpa_s": EMERGENCY_TCPA_S,
                "bow_crossing_clearance_m": BOW_CROSSING_M,
            }
            if len(self.history) > 1:
                prompt_payload["previous_states"] = self.history[:-1]
        else:
            prompt_payload["briefing"] = narrative
        action_review = None
        reduced_speed = bool(
            own_ship.get("target_speed") is not None
            and own_ship.get("recovery_speed") is not None
            and float(own_ship["target_speed"]) < float(own_ship["recovery_speed"])
        )
        if stopped:
            action_review = {
                "active_action": "stop",
                "current_speed": own_ship.get("speed"),
                "target_speed": own_ship.get("target_speed"),
                "instruction": (
                    "OWN SHIP IS STOPPED OR PAUSED. It is not maintaining an "
                    "underway course. Choose resume only when the observed traffic "
                    "makes getting underway safe; otherwise choose skip and identify "
                    "the specific hazard requiring own ship to remain stopped."
                ),
            }
        elif current_maneuver.get("action") == "alter_course":
            action_review = {
                "active_action": (
                    "alter_course_and_set_speed" if reduced_speed else "alter_course"
                ),
                "degrees": current_maneuver.get("degrees"),
                "current_target_speed": own_ship.get("target_speed"),
                "normal_speed": own_ship.get("recovery_speed"),
                "instruction": (
                    "Review whether the existing course alteration is still necessary "
                    "and effective. Name the specific current contact and geometry that "
                    "still require it. If no such contact exists and returning is safe, "
                    "choose rejoin_route now. Compare own heading with the objective "
                    "bearing; do not continue a detour on a vague claim of avoidance. "
                    "Any new alter_course command is relative to the current measured "
                        "heading and replaces the active 1-NM waypoint, so repeating the same "
                    "signed alteration compounds the diversion rather than holding the "
                    "previous course. State the resulting heading and reject additional "
                    "objective error unless a named current hazard makes it necessary. "
                    + (
                        "Independently review the reduced speed. Course and speed controls "
                        "are separate: choosing set_speed does not cancel the active course "
                        "alteration. Name the specific current contact whose present CPA/TCPA "
                        "still require the low target and explain how that target improves "
                        "the geometry. If none exists, choose set_speed at normal speed now, "
                        "even if the course alteration should continue. Do not use "
                        "maintain_course merely to preserve both stale controls."
                        if reduced_speed else
                        "Replace the maneuver only if a current hazard requires a different "
                        "course alteration."
                    )
                ),
            }
        elif reduced_speed:
            action_review = {
                "active_action": "set_speed",
                "current_target_speed": own_ship.get("target_speed"),
                "normal_speed": own_ship.get("recovery_speed"),
                "instruction": (
                    "Review the reduced speed independently. Name the specific current "
                    "contact whose present CPA/TCPA still require this target and explain "
                    "how remaining slow improves that encounter. A historical encounter, "
                    "generic nearby traffic, or an unsupported statement that avoidance "
                    "must continue is not enough. If the causative hazard has passed or "
                    "the low speed no longer materially improves separation, choose "
                    "set_speed at normal speed now. Choose maintain_course only when a "
                    "named current hazard positively justifies retaining the low target."
                ),
            }
        if action_review is not None:
            prompt_payload["active_action_review"] = action_review
        if self.eriksen_prompt_enabled:
            narrative = f"{narrative}\n\n{_render_eriksen_assessment(self.last_paper_colregs)}"
            prompt_payload["briefing"] = narrative
        if (
            self.openai_api
            and self.mode == "base"
            and self.openai_prompt_profile == "teacher"
        ):
            prompt_payload["state"] = state
            prompt_payload["deterministic_colregs_oracle"] = self.last_paper_colregs
            if self.teacher_lookahead_enabled:
                prompt_payload["teacher_candidate_lookahead"] = (
                    build_teacher_candidate_lookahead(
                        state,
                        horizon_s=self.teacher_lookahead_horizon_s,
                        timestep_s=self.teacher_lookahead_timestep_s,
                        maneuver_distance_m=self.maneuver_distance_m,
                        minimum_separation_m=MIN_SAFE_CPA_M,
                        critical_separation_m=CRITICAL_CPA_M,
                        margin_m=self.teacher_lookahead_margin_m,
                    )
                )
        if self.mode == "finetuned" and self.finetuned_prompt_profile in {
            "narrative-only",
            "narrative-directional",
        }:
            # Prompt-ablation profile: the rendered narrative is the complete
            # user message.  Do not leak the JSON wrapper or action-review hints
            # used by the full-prompt control condition.
            user_prompt = narrative
        else:
            user_prompt = json.dumps(
                prompt_payload, separators=(",", ":"), ensure_ascii=False
            )

        result = {
            "state": enriched,
            "retrieval_state": retrieval_state,
            "narrative": narrative,
            "constraints": constraints,
            "user_prompt": user_prompt,
            "vhf_communications": vhf_communications,
        }
        if self.colregs_tree is not None:
            result["colregs_selection"] = self.colregs_tree.select(
                persistent, stale_after_s=self.stale_age_s
            )
        return result

    def decide(self, transformed: dict[str, Any]) -> CaptainDecision:
        user_prompt = transformed["user_prompt"]
        self.last_colregs_retrieval = None
        if self.colregs_tree is not None:
            selection = transformed["colregs_selection"]
            user_prompt = (
                f"BRIDGE BRIEFING\n{transformed['user_prompt'].strip()}\n\n"
                "DECISION-TREE COLREG GUIDANCE\n"
                f"{self.colregs_tree.prompt_context(selection)}\n\n"
                f"ALLOWED ACTIONS\n{json.dumps(transformed['constraints']['allowed_actions'])}"
            )
            self.last_colregs_retrieval = dict(selection)
        if self.colregs_retriever is not None:
            retrieval_queries = navigation_queries(
                transformed["narrative"], transformed.get("retrieval_state")
            )
            if hasattr(self.colregs_retriever, "retrieve_many"):
                candidates, selected = self.colregs_retriever.retrieve_many(retrieval_queries)
            else:  # Compatibility with lightweight retriever test doubles.
                candidates, selected = self.colregs_retriever.retrieve(
                    "\n\n---\n\n".join(retrieval_queries)
                )
            user_prompt = (
                f"BRIDGE BRIEFING\n{transformed['user_prompt'].strip()}\n\n"
                "RETRIEVED COLREG PROVISIONS\n"
                f"{self.colregs_retriever.prompt_context(selected)}\n\n"
                f"ALLOWED ACTIONS\n{json.dumps(transformed['constraints']['allowed_actions'])}"
            )
            self.last_colregs_retrieval = {
                "query": retrieval_queries[0] if len(retrieval_queries) == 1 else None,
                "queries": retrieval_queries,
                "reranker": self.colregs_retriever.reranker.name,
                "candidates": [item.as_dict() for item in candidates],
                "selected": [item.as_dict() for item in selected],
            }
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        decision_schema = deepcopy(CaptainDecision.model_json_schema())
        decision_schema["properties"]["action"]["enum"] = list(
            transformed["constraints"]["allowed_actions"]
        )
        directional_output = (
            self.mode == "finetuned"
            and self.finetuned_prompt_profile == "narrative-directional"
        )
        if directional_output:
            decision_schema["properties"].pop("degrees")
            decision_schema["properties"]["turn_direction"] = {
                "type": "string",
                "enum": ["none", "port", "starboard"],
            }
            decision_schema["properties"]["turn_degrees"] = {
                "type": "number",
                "minimum": 0.0,
                "maximum": 60.0,
            }
            decision_schema["required"] = [
                field for field in decision_schema["required"] if field != "degrees"
            ] + ["turn_direction", "turn_degrees"]
        if self.colregs_retriever is not None or self.colregs_tree is not None:
            decision_schema["properties"]["auxiliary_commands"]["maxItems"] = 0
            decision_schema["properties"]["degrees"] = {
                "type": "string",
                "enum": ["none", "port", "starboard", "large_port", "large_starboard"],
                "description": (
                    "Use port or starboard for a normal alteration, large_port or "
                    "large_starboard for a substantial 45-degree alteration, and none for "
                    "every other action. Never output a numeric course alteration."
                ),
            }
            decision_schema["properties"]["speed"] = {
                "type": "string",
                "enum": ["none", "slow", "normal", "fast"],
                "description": (
                    "Use slow, normal, or fast only with set_speed; use none for every other action. "
                    "Never output a numeric speed."
                ),
            }
        if self.openai_api:
            decision_schema = _openai_compatible_schema(decision_schema)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "oow_decision",
                "strict": True,
                "schema": decision_schema,
            },
        }
        last_error: Exception | None = None
        last_content: str | None = None
        self.last_response_metadata = None
        self.last_model_decision = None
        final_messages = messages
        if self.reasoning_enabled and not self.openai_api:
            # A strict JSON schema constrains the first generated token to JSON,
            # which prevents Qwen3 from emitting its <think> block. Perform a
            # verified, unconstrained deliberation first and use a separate
            # schema-constrained call only to serialize the final action.
            deliberation = None
            reasoning_trace = ""
            reasoning_body = ""
            reasoning_complete = False
            for _reasoning_attempt in range(2):
                deliberation = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_tokens=self.max_output_tokens,
                    messages=messages,
                )
                deliberation_message = deliberation.choices[0].message
                reasoning_trace = deliberation_message.content or ""
                separate_reasoning = getattr(
                    deliberation_message, "reasoning_content", None
                )
                if separate_reasoning and "<think>" not in reasoning_trace:
                    reasoning_trace = (
                        f"<think>{separate_reasoning}</think>\n{reasoning_trace}"
                    )
                think_match = re.search(
                    r"<think>\s*(.*?)(?:\s*</think>|\Z)",
                    reasoning_trace,
                    flags=re.DOTALL,
                )
                reasoning_body = (
                    think_match.group(1).strip() if think_match is not None else ""
                )
                reasoning_complete = "</think>" in reasoning_trace
                if len(reasoning_body) >= 20:
                    break
            if len(reasoning_body) < 20:
                raise RuntimeError(
                    "reasoning mode was requested but Qwen returned no substantive "
                    "<think> deliberation in two attempts"
                )
            assert deliberation is not None
            usage = getattr(deliberation, "usage", None)
            self.last_response_metadata = {
                "reasoning_enabled": True,
                "reasoning_verified": True,
                "reasoning_trace": reasoning_trace,
                "reasoning_characters": len(reasoning_body),
                "reasoning_complete": reasoning_complete,
                "reasoning_attempts": _reasoning_attempt + 1,
                "reasoning_finish_reason": getattr(
                    deliberation.choices[0], "finish_reason", None
                ),
                "deliberation_usage": (
                    usage.model_dump() if hasattr(usage, "model_dump") else usage
                ),
            }
            finalization_trace = (
                reasoning_trace
                if reasoning_complete
                else f"{reasoning_trace.rstrip()}\n</think>"
            )
            final_messages = [
                {
                    "role": "system",
                    "content": self.system_prompt.replace("/think", "/no_think"),
                },
                messages[1],
                {"role": "assistant", "content": finalization_trace},
                {
                    "role": "user",
                    "content": (
                        "Using the completed analysis above, return only the final "
                        "JSON decision matching the required schema. Do not deliberate "
                        "again."
                    ),
                },
            ]
        for _attempt in range(2):
            if self.openai_api:
                response = self.client.responses.create(
                    model=self.model,
                    instructions=self.system_prompt,
                    input=messages[1:],
                    max_output_tokens=self.max_output_tokens,
                    reasoning={"effort": self.openai_reasoning_effort},
                    text={"format": response_format["json_schema"] | {"type": "json_schema"}},
                )
                content = response.output_text
                usage = getattr(response, "usage", None)
                self.last_response_metadata = {
                    "response_id": getattr(response, "id", None),
                    "model": getattr(response, "model", self.model),
                    "usage": usage.model_dump() if hasattr(usage, "model_dump") else usage,
                }
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_tokens=self.max_output_tokens,
                    messages=final_messages,
                    response_format=response_format,
                )
                content = response.choices[0].message.content
                if self.last_response_metadata is not None:
                    usage = getattr(response, "usage", None)
                    self.last_response_metadata["finalization_usage"] = (
                        usage.model_dump() if hasattr(usage, "model_dump") else usage
                    )
            last_content = content
            try:
                if not content:
                    raise RuntimeError("OOW returned no decision")
                if self.colregs_retriever is None and self.colregs_tree is None:
                    raw_payload = json.loads(content)
                    self.last_model_decision = dict(raw_payload)
                    payload = (
                        _resolve_directional_decision_fields(raw_payload)
                        if directional_output
                        else raw_payload
                    )
                    payload = _canonicalize_unused_decision_fields(payload)
                    return CaptainDecision.model_validate(payload)

                payload = json.loads(content)
                intent = payload.get("speed")
                course_intent = payload.get("degrees")
                action = payload.get("action")
                course_intents = {"port", "starboard", "large_port", "large_starboard"}
                if action == "alter_course" and course_intent not in course_intents:
                    raise ValueError(
                        "alter_course requires a normal or large port/starboard intent"
                    )
                if action != "alter_course" and course_intent != "none":
                    raise ValueError("non-alter_course action requires degrees='none'")
                if action == "set_speed" and intent not in {"slow", "normal", "fast"}:
                    raise ValueError("set_speed requires speed='slow', 'normal', or 'fast'")
                if action != "set_speed" and intent != "none":
                    raise ValueError("non-set_speed action requires speed='none'")
                own = transformed.get("state", {}).get("own_ship", {})
                cruise = float(own.get("recovery_speed") or own.get("target_speed") or 0.0)
                if action == "set_speed" and cruise <= 0.0:
                    raise ValueError("no configured cruise speed is available")
                numeric_speed = 0.0
                if action == "set_speed":
                    resolved_intent = intent
                    numeric_speed = (
                        cruise
                        if resolved_intent in {"normal", "fast"}
                        else max(0.3, cruise * 0.7)
                    )
                else:
                    resolved_intent = intent
                numeric_degrees = 0.0
                if action == "alter_course":
                    magnitude = (
                        LARGE_COURSE_INTENT_DEGREES
                        if str(course_intent).startswith("large_")
                        else COURSE_INTENT_DEGREES
                    )
                    numeric_degrees = (
                        magnitude if str(course_intent).endswith("starboard") else -magnitude
                    )
                payload["degrees"] = numeric_degrees
                payload["speed"] = numeric_speed
                decision = CaptainDecision.model_validate(payload)
                if self.last_colregs_retrieval is not None:
                    self.last_colregs_retrieval["speed_intent"] = intent
                    self.last_colregs_retrieval["resolved_speed_intent"] = resolved_intent
                    self.last_colregs_retrieval["resolved_speed"] = numeric_speed
                    self.last_colregs_retrieval["course_intent"] = course_intent
                    self.last_colregs_retrieval["resolved_degrees"] = numeric_degrees
                return decision
            except (ValidationError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
                last_error = exc
                if content:
                    final_messages.append({
                        "role": "user",
                        "content": (
                            f"Your previous response was invalid: {content!r}; error: {exc}. "
                            "Return a corrected JSON object obeying the constraints exactly."
                        ),
                    })
        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
        raise RuntimeError(
            f"OOW returned invalid JSON twice ({detail}); last_content={last_content!r}"
        ) from last_error
