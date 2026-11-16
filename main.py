from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

from openai import OpenAI, OpenAIError

from agents.agent_captain import AgentCaptain
from agents.agent_oow import AgentOOW
from agents.agent_vhf import AgentVHF
from bridge.bridge import get_bridge_json
from bridge.state import BridgeStateStore
from bridge.bridge import send_command, set_simulation_paused
from tools.config_value import get
from rag.maib import MaibRetriever


BRIDGE_HOST = get("hosts", "bridge")
BRIDGE_PORT = get("ports", "bridge")
VLLM_HOST = get("hosts", "vllm")
VLLM_PORT = get("ports", "vllm")
MODEL = get("models", "default_local")
API_KEY = get("llm", "api_key")
OPENAI_API = os.environ.get("MOOS_LLM_OPENAI_API", "off").strip().lower() in {
    "1", "true", "yes", "on"
}
LOG_DIR = Path(get("paths", "runtime_logs"))
AUDIT_LOG = Path(os.environ.get("MOOS_LLM_AUDIT_LOG", LOG_DIR / "agent_audit.jsonl"))
NEAR_COLLISION_AUDITED = False
ACTIVE_VHF_REPORTS: list[dict[str, Any]] = []
DEFAULT_VHF_CONTEXT_CYCLES = 3
LOCKSTEP_LOGICAL_MOOS_TIME: float | None = None


# This is the sole bridge-state reader used by the agent orchestration path.
BRIDGE_STATE = BridgeStateStore(
    lambda: get_bridge_json(BRIDGE_HOST, BRIDGE_PORT)
)


if OPENAI_API:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
else:
    client = OpenAI(
        base_url=f"http://{VLLM_HOST}:{VLLM_PORT}/v1",
        api_key=API_KEY,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _jsonable(value.model_dump())
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def audit(event_name: str, **payload: Any) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": _now_iso(),
        "event": event_name,
        **{key: _jsonable(value) for key, value in payload.items()},
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[audit] {event_name}", flush=True)


def _lockstep_settings() -> tuple[bool, float, str, list[int]]:
    """Resolve simulation lockstep settings supplied by the supervisor."""
    enabled = os.environ.get(
        "MOOS_LLM_FREEZE_DURING_DECISION", "off"
    ).strip().lower() in {"1", "true", "yes", "on"}
    interval_s = float(os.environ.get(
        "MOOS_LLM_DECISION_INTERVAL_S", get("runtime", "poll_seconds")
    ))
    if interval_s <= 0.0:
        raise ValueError("MOOS_LLM_DECISION_INTERVAL_S must be positive")
    host = os.environ.get("MOOS_LLM_MOOS_HOST", get("hosts", "moos")).strip()
    ports_text = os.environ.get("MOOS_LLM_VEHICLE_PORTS", "").strip()
    ports = [int(item) for item in ports_text.split(",") if item.strip()]
    if enabled and not ports:
        raise ValueError(
            "MOOS_LLM_VEHICLE_PORTS is required when decision lockstep is enabled"
        )
    return enabled, interval_s, host, ports


def _post_simulation_pause(host: str, ports: list[int], paused: bool) -> None:
    """Pause or resume patched uSimMarine dynamics on every vessel.

    The project requires a uSimMarineV22 build whose pause timer subtracts
    millisecond-precision wall time multiplied by ``GetMOOSTimeWarp()``.
    That makes this physical lockstep valid above warp 1 as well.
    """
    del host, ports  # Fleet fan-out is configured in the MOOS broker network.
    try:
        set_simulation_paused(paused, BRIDGE_HOST, BRIDGE_PORT)
    except OSError as exc:
        raise RuntimeError(
            f"failed to {'pause' if paused else 'resume'} fleet simulation: {exc}"
        ) from exc


def _recent_vhf_reports(
    reports: list[dict[str, Any]], current_cycle: int
) -> list[dict[str, Any]]:
    """Return recent messages plus any explicitly persistent instructions.

    A report is shown on its arrival cycle and the following two cycles by
    default.  Later appearances are explicitly marked as history so the model
    does not interpret the same transmission as newly received each time.
    """
    context_cycles = int(os.environ.get(
        "MOOS_LLM_VHF_CONTEXT_CYCLES", str(DEFAULT_VHF_CONTEXT_CYCLES)
    ))
    if context_cycles < 1:
        raise ValueError("MOOS_LLM_VHF_CONTEXT_CYCLES must be at least 1")

    recent: list[dict[str, Any]] = []
    for report in reports:
        received_cycle = int(report.get("received_cycle", current_cycle))
        age_cycles = max(0, current_cycle - received_cycle)
        advisory = report.get("advisory") or {}
        persistent = (
            advisory.get("directive_lifetime", "transient") != "transient"
            and bool(advisory.get("addressed_to_own_ship"))
        )
        if age_cycles >= context_cycles and not persistent:
            continue
        recent.append({
            **report,
            "received_cycle": received_cycle,
            "age_cycles": age_cycles,
            "context_status": (
                "newly_received"
                if age_cycles == 0
                else (
                    "recent_already_presented"
                    if age_cycles < context_cycles
                    else "active_persistent_instruction"
                )
            ),
        })
    return recent


def _apply_vhf_instruction_clearances(
    reports: list[dict[str, Any]],
    auxiliary_commands: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply validated model lifecycle decisions without sending bridge commands."""
    clear_commands = [
        command for command in auxiliary_commands
        if command.get("type") == "clear_vhf_instruction"
    ]
    if not clear_commands:
        return reports, [], []

    by_id = {
        str((report.get("advisory") or {}).get("message_id")): report
        for report in reports
    }
    cleared: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    clear_ids: set[str] = set()
    for command in clear_commands:
        message_id = str(command.get("message_id"))
        report = by_id.get(message_id)
        advisory = (report or {}).get("advisory") or {}
        if report is None:
            rejected.append({**command, "error": "message_id is not active"})
            continue
        if advisory.get("directive_lifetime", "transient") == "transient":
            rejected.append({**command, "error": "message is not persistent"})
            continue
        clear_ids.add(message_id)
        cleared.append({
            **command,
            "directive_lifetime": advisory.get("directive_lifetime"),
            "directive_end_condition": advisory.get("directive_end_condition"),
        })

    retained = [
        report for report in reports
        if str((report.get("advisory") or {}).get("message_id")) not in clear_ids
    ]
    return retained, cleared, rejected


def build_agents() -> tuple[AgentCaptain, AgentOOW, AgentVHF]:
    validator_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    configured_rag = get("maib_rag", "enabled").strip().lower() in {"1", "true", "yes", "on"}
    rag_override = os.environ.get("MOOS_LLM_RAG", "config").strip().lower()
    if rag_override not in {"config", "on", "off"}:
        raise ValueError("MOOS_LLM_RAG must be 'config', 'on', or 'off'")
    rag_enabled = configured_rag if rag_override == "config" else rag_override == "on"
    configured_captain_rag = get(
        "maib_rag", "captain_decision_enabled", fallback="true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    captain_rag_override = os.environ.get("MOOS_LLM_CAPTAIN_RAG", "config").strip().lower()
    if captain_rag_override not in {"config", "on", "off"}:
        raise ValueError("MOOS_LLM_CAPTAIN_RAG must be 'config', 'on', or 'off'")
    captain_rag_enabled = (
        configured_captain_rag
        if captain_rag_override == "config"
        else captain_rag_override == "on"
    )
    retriever = None
    if rag_enabled:
        index_path = Path(get("maib_rag", "index_path"))
        embeddings_value = get("maib_rag", "embeddings_path").strip()
        embeddings_path = Path(embeddings_value) if embeddings_value else None
        if not index_path.is_file() or index_path.stat().st_size == 0:
            raise RuntimeError(
                f"MAIB RAG is enabled but its index is missing or empty: {index_path}"
            )
        if embeddings_path is not None and (
            not embeddings_path.is_file() or embeddings_path.stat().st_size == 0
        ):
            raise RuntimeError(
                "MAIB RAG embeddings are configured but missing or empty: "
                f"{embeddings_path}"
            )
        retriever = MaibRetriever(
            index_path,
            top_k=int(get("maib_rag", "top_k")),
            min_score=float(get("maib_rag", "min_score")),
            embeddings_path=embeddings_path,
            embedding_model=get("maib_rag", "embedding_model"),
            reranker_model=get("maib_rag", "reranker_model", fallback="").strip() or None,
        )
    captain = AgentCaptain(
        client=client,
        bridge_host=BRIDGE_HOST,
        bridge_port=BRIDGE_PORT,
        model=MODEL,
        validator_queue=validator_queue,
        maib_retriever=retriever,
        rag_decision_enabled=rag_enabled and captain_rag_enabled,
    )
    oow = AgentOOW(
        client=client,
    )
    vhf = AgentVHF(
        client=client,
        model=MODEL,
        captain_queue=captain.queue,
    )
    return captain, oow, vhf


async def process_cycle(
    captain: AgentCaptain,
    oow: AgentOOW,
    vhf: AgentVHF,
) -> bool:
    global NEAR_COLLISION_AUDITED, ACTIVE_VHF_REPORTS, LOCKSTEP_LOGICAL_MOOS_TIME
    cycle_started = monotonic()
    retrieval_duration_s = 0.0
    captain_duration_s = 0.0
    command_revalidation_duration_s = 0.0
    bridge_snapshot = BRIDGE_STATE.refresh()
    print(f"[cycle] bridge snapshot {bridge_snapshot.version}", flush=True)
    audit(
        "bridge_snapshot",
        cycle_id=bridge_snapshot.version,
        received_at=bridge_snapshot.received_at,
        state=bridge_snapshot.state_for_agent(),
    )

    bridge_state = bridge_snapshot.state_for_agent()
    if os.environ.get(
        "MOOS_LLM_FREEZE_DURING_DECISION", "off"
    ).strip().lower() in {"1", "true", "yes", "on"}:
        raw_moos_time = float(bridge_state.get("moos_time", 0.0))
        interval_s = float(os.environ.get(
            "MOOS_LLM_DECISION_INTERVAL_S", get("runtime", "poll_seconds")
        ))
        if LOCKSTEP_LOGICAL_MOOS_TIME is None:
            LOCKSTEP_LOGICAL_MOOS_TIME = raw_moos_time
        else:
            LOCKSTEP_LOGICAL_MOOS_TIME += interval_s
        bridge_state["raw_moos_time"] = raw_moos_time
        bridge_state["moos_time"] = LOCKSTEP_LOGICAL_MOOS_TIME
    if bridge_state.get("near_collision") is True and not NEAR_COLLISION_AUDITED:
        audit(
            "near_collision",
            cycle_id=bridge_snapshot.version,
            source="bridge",
            contact=bridge_state.get("minimum_range_contact"),
            minimum_observed_range_m=bridge_state.get("minimum_observed_range"),
            nav=bridge_state.get("nav"),
        )
        NEAR_COLLISION_AUDITED = True
    if bridge_state.get("mission_failed") is True:
        audit(
            "mission_failed",
            cycle_id=bridge_snapshot.version,
            source="bridge",
            reason=bridge_state.get("mission_failure_reason"),
            contact=bridge_state.get("minimum_range_contact"),
            minimum_observed_range_m=bridge_state.get("minimum_observed_range"),
            mission_objective=bridge_state.get("mission_objective"),
            nav=bridge_state.get("nav"),
        )
        return True
    if bridge_state.get("mission_complete") is True:
        audit(
            "mission_complete",
            cycle_id=bridge_snapshot.version,
            source="bridge",
            mission_objective=bridge_state.get("mission_objective"),
            nav=bridge_state.get("nav"),
        )
        return True
    max_cycles = int(os.environ.get("MOOS_LLM_MAX_CYCLES", "0") or 0)
    if max_cycles > 0 and bridge_snapshot.version >= max_cycles:
        audit(
            "cycle_limit_reached",
            cycle_id=bridge_snapshot.version,
            source="runner",
            max_cycles=max_cycles,
            mission_objective=bridge_state.get("mission_objective"),
            nav=bridge_state.get("nav"),
            minimum_observed_range_m=bridge_state.get("minimum_observed_range"),
        )
        return True
    oow_duration_s = 0.0

    vhf_events = await vhf.process_directory(bridge_snapshot=bridge_snapshot)
    for failure in getattr(vhf, "last_failures", []):
        audit(
            "vhf_processing_failed",
            cycle_id=bridge_snapshot.version,
            source="vhf",
            **failure,
        )
    for event in vhf_events:
        audit(
            "vhf_event_record",
            cycle_id=bridge_snapshot.version,
            source="vhf",
            vhf_event=event,
        )
        print(f"[vhf] event {event.message_id}", flush=True)
    audit(
        "vhf_scan_complete",
        cycle_id=bridge_snapshot.version,
        inbox=str(vhf.input_dir),
        processed=str(vhf.processed_dir),
        wav_count=len(vhf_events),
    )

    while not captain.queue.empty():
        report = await captain.queue.get()
        audit(
            "captain_queue_item",
            cycle_id=bridge_snapshot.version,
            source=report.get("source"),
            data=report.get("report"),
        )
        source = report.get("source")
        if source == "vhf":
            # VHF reports are observations/advisories only. They never issue
            # navigation commands directly and never bypass the OOW.
            vhf_report = report.get("report", {})
            audit(
                "vhf_to_captain",
                cycle_id=bridge_snapshot.version,
                report=vhf_report,
            )
            ACTIVE_VHF_REPORTS.append({
                **vhf_report,
                "received_cycle": bridge_snapshot.version,
            })
            continue

    ACTIVE_VHF_REPORTS = _recent_vhf_reports(
        ACTIVE_VHF_REPORTS, bridge_snapshot.version
    )
    transformed = oow.deterministic_input(
        bridge_state,
        vhf_advisories=ACTIVE_VHF_REPORTS,
    )
    if transformed.get("vhf_communications"):
        audit(
            "vhf_decision_context",
            cycle_id=bridge_snapshot.version,
            source="decision_pipeline",
            decision_makers=(
                ["oow", "captain_critic"]
                if getattr(captain, "rag_decision_enabled", False)
                and captain.maib_retriever is not None
                else ["oow"]
            ),
            communications=transformed["vhf_communications"],
        )
    paper_colregs = getattr(oow, "last_paper_colregs", None)
    if paper_colregs is not None:
        audit(
            "oow_colregs_oracle",
            cycle_id=bridge_snapshot.version,
            source="eriksen_2020_state_machine",
            classification=paper_colregs,
        )
    decision_trigger = (
        oow.decision_trigger(transformed)
        if hasattr(oow, "decision_trigger")
        else {"call_model": True, "reason": "trigger_not_supported"}
    )
    if not decision_trigger["call_model"]:
        audit(
            "oow_model_call_skipped",
            cycle_id=bridge_snapshot.version,
            source="decision_trigger",
            model=oow.model,
            **decision_trigger,
        )
        audit(
            "cycle_performance",
            cycle_id=bridge_snapshot.version,
            rag_enabled=captain.maib_retriever is not None,
            oow_model=getattr(oow, "model", None),
            oow_duration_s=0.0,
            lookout_duration_s=0.0,
            retrieval_duration_s=retrieval_duration_s,
            captain_duration_s=0.0,
            command_revalidation_duration_s=0.0,
            total_duration_s=monotonic() - cycle_started,
            model_call_skipped=True,
        )
        return False
    audit(
        "oow_prompt_input",
        cycle_id=bridge_snapshot.version,
        source="oow",
        model=oow.model,
        system_prompt=oow.system_prompt,
        narrative=transformed["narrative"],
        user_prompt=transformed.get("user_prompt"),
        constraints=transformed["constraints"],
        state=transformed["state"],
    )
    oow_started = monotonic()
    try:
        decision = oow.decide(transformed)
    except RuntimeError as exc:
        oow_duration_s = monotonic() - oow_started
        if oow.last_colregs_retrieval is not None:
            audit(
                "oow_colregs_retrieval",
                cycle_id=bridge_snapshot.version,
                source="oow",
                **oow.last_colregs_retrieval,
            )
        audit(
            "oow_decision_failed",
            cycle_id=bridge_snapshot.version,
            source="oow",
            model=oow.model,
            error=str(exc),
        )
        audit(
            "cycle_performance",
            cycle_id=bridge_snapshot.version,
            rag_enabled=captain.maib_retriever is not None,
            oow_model=getattr(oow, "model", None),
            oow_duration_s=oow_duration_s,
            lookout_duration_s=0.0,
            retrieval_duration_s=retrieval_duration_s,
            captain_duration_s=0.0,
            command_revalidation_duration_s=0.0,
            total_duration_s=monotonic() - cycle_started,
            decision_failed=True,
        )
        return False
    oow_duration_s = monotonic() - oow_started
    if getattr(oow, "last_response_metadata", None) is not None:
        audit(
            "oow_api_response",
            cycle_id=bridge_snapshot.version,
            source="oow",
            **oow.last_response_metadata,
        )
    if oow.last_colregs_retrieval is not None:
        audit(
            "oow_colregs_retrieval",
            cycle_id=bridge_snapshot.version,
            source="oow",
            **oow.last_colregs_retrieval,
        )
    audit(
        "oow_decision",
        cycle_id=bridge_snapshot.version,
        source="oow",
        model=oow.model,
        model_decision=getattr(oow, "last_model_decision", None),
        decision=decision,
    )
    # The captain prompt is serialized as JSON.  Dump in JSON mode here so
    # typed schema values (notably UUID message IDs used by VHF lifecycle
    # commands) cannot leak into the handoff as non-serializable objects.
    decision_payload = decision.model_dump(mode="json")
    decision_source = "oow"

    # In the MAIB-RAG condition the captain reviews the OOW proposal against
    # observation-only bridge geometry and retrieved investigation lessons.
    # The captain's approved or replacement control is the final command.
    if (
        getattr(captain, "rag_decision_enabled", False)
        and captain.maib_retriever is not None
    ):
        captain_state = {
            "priority": "navigation_decision",
            "task": (
                "Independently verify the OOW proposal against the live bridge "
                "observations. Approve it unless a specific, material, observation-"
                "supported safety defect requires a different control. Retrieved "
                "accident-investigation evidence is advisory."
            ),
            "oow_proposal": decision_payload,
            "vhf_communications": transformed.get("vhf_communications", []),
        }
        retrieval_started = monotonic()
        retrieval_query, retrieved = captain.retrieve_evidence(
            captain_state,
            bridge_snapshot,
        )
        retrieval_duration_s = monotonic() - retrieval_started
        audit(
            "maib_retrieval",
            cycle_id=bridge_snapshot.version,
            source="captain",
            query=retrieval_query,
            routing=getattr(captain, "last_retrieval_routing", None),
            results=[item.as_dict() for item in retrieved],
            retrieval_duration_s=retrieval_duration_s,
        )
        captain_started = monotonic()
        try:
            captain_decision = captain.decide(
                captain_state,
                bridge_snapshot,
                retrieved_evidence=retrieved,
            )
        except (RuntimeError, ValueError, OpenAIError) as exc:
            captain_duration_s = monotonic() - captain_started
            audit(
                "captain_decision_failed",
                cycle_id=bridge_snapshot.version,
                source="captain_maib_rag",
                model=captain.model,
                error=str(exc),
                prompt_budget=getattr(captain, "last_prompt_budget", None),
            )
            audit(
                "cycle_performance",
                cycle_id=bridge_snapshot.version,
                rag_enabled=True,
                captain_rag_decision_enabled=True,
                oow_model=getattr(oow, "model", None),
                oow_duration_s=oow_duration_s,
                lookout_duration_s=0.0,
                retrieval_duration_s=retrieval_duration_s,
                captain_duration_s=captain_duration_s,
                command_revalidation_duration_s=command_revalidation_duration_s,
                total_duration_s=monotonic() - cycle_started,
                decision_failed=True,
            )
            return False
        captain_duration_s = monotonic() - captain_started
        audit(
            "captain_prompt_input",
            cycle_id=bridge_snapshot.version,
            source="captain_maib_rag",
            model=captain.model,
            system_prompt=captain.system_prompt,
            user_payload=captain.last_prompt_payload,
            prompt_budget=getattr(captain, "last_prompt_budget", None),
        )
        audit(
            "captain_decision",
            cycle_id=bridge_snapshot.version,
            source="captain_maib_rag",
            model=captain.model,
            decision=captain_decision,
            course_intent=getattr(captain, "last_course_intent", None),
            navigation_assessment=getattr(
                captain, "last_navigation_assessment", None
            ),
            evidence_assessment=getattr(captain, "last_evidence_assessment", None),
        )
        decision_payload = captain_decision.model_dump(mode="json")
        audit(
            "captain_oow_comparison",
            cycle_id=bridge_snapshot.version,
            source="captain_rag_review",
            oow_action=decision.action,
            captain_action=captain_decision.action,
            actions_agree=decision.action == captain_decision.action,
        )
        decision_source = "captain_maib_rag"
    try:
        validated = captain.validate_decision(decision_payload, bridge_state)
    except ValueError as exc:
        # Invalid output is never rewritten into another navigation decision.
        # Record it and let the next OOW cycle reassess the live state.
        audit(
            "captain_decision_rejected"
            if decision_source == "captain_maib_rag"
            else "oow_decision_rejected",
            cycle_id=bridge_snapshot.version,
            source=decision_source,
            decision=decision_payload,
            error=str(exc),
        )
        validated = None

    if validated is None:
        audit(
            "cycle_performance",
            cycle_id=bridge_snapshot.version,
            rag_enabled=captain.maib_retriever is not None,
            captain_rag_decision_enabled=getattr(captain, "rag_decision_enabled", False),
            oow_model=getattr(oow, "model", None),
            oow_duration_s=oow_duration_s,
            lookout_duration_s=0.0,
            retrieval_duration_s=retrieval_duration_s,
            captain_duration_s=captain_duration_s,
            command_revalidation_duration_s=0.0,
            total_duration_s=monotonic() - cycle_started,
            decision_rejected=True,
        )
        return False

    lifecycle_commands = [
        command for command in validated.get("auxiliary_commands", [])
        if command.get("type") == "clear_vhf_instruction"
    ]
    ACTIVE_VHF_REPORTS, cleared_instructions, rejected_clearances = (
        _apply_vhf_instruction_clearances(ACTIVE_VHF_REPORTS, lifecycle_commands)
    )
    for clearance in cleared_instructions:
        audit(
            "vhf_instruction_cleared",
            cycle_id=bridge_snapshot.version,
            source=decision_source,
            **clearance,
        )
    for clearance in rejected_clearances:
        audit(
            "vhf_instruction_clearance_rejected",
            cycle_id=bridge_snapshot.version,
            source=decision_source,
            **clearance,
        )
    # Lifecycle commands are consumed by the orchestration layer and must
    # never be forwarded to the bridge communication-command interface.
    validated["auxiliary_commands"] = [
        command for command in validated.get("auxiliary_commands", [])
        if command.get("type") != "clear_vhf_instruction"
    ]

    audit(
        "captain_validated",
        cycle_id=bridge_snapshot.version,
        source=decision_source,
        decision=validated,
    )

    command_started = monotonic()
    command = captain.command_from_decision(
        validated,
        bridge_state,
    )
    captain_duration_s += monotonic() - command_started

    audit(
        "command_from_decision",
        cycle_id=bridge_snapshot.version,
        source=decision_source,
        command=command,
    )
    if command is not None:
        send_command(command, BRIDGE_HOST, BRIDGE_PORT)

    audit(
        "cycle_performance",
        cycle_id=bridge_snapshot.version,
        rag_enabled=captain.maib_retriever is not None,
        captain_rag_decision_enabled=getattr(captain, "rag_decision_enabled", False),
        oow_model=getattr(oow, "model", None),
        oow_duration_s=oow_duration_s,
        lookout_duration_s=0.0,
        retrieval_duration_s=retrieval_duration_s,
        captain_duration_s=captain_duration_s,
        command_revalidation_duration_s=command_revalidation_duration_s,
        total_duration_s=monotonic() - cycle_started,
    )
    return False


async def main() -> None:
    captain, oow, vhf = build_agents()
    lockstep_enabled, decision_interval_s, moos_host, vehicle_ports = (
        _lockstep_settings()
    )
    print(
        f"[startup] bridge={BRIDGE_HOST}:{BRIDGE_PORT} "
        f"vllm={VLLM_HOST}:{VLLM_PORT} "
        f"vhf_inbox={vhf.input_dir} vhf_processed={vhf.processed_dir}",
        flush=True,
    )
    audit(
        "startup",
        rag_enabled=captain.maib_retriever is not None,
        captain_rag_decision_enabled=getattr(captain, "rag_decision_enabled", False),
        colregs_rag_enabled=oow.colregs_retriever is not None,
        colregs_tree_enabled=getattr(oow, "colregs_tree", None) is not None,
        reasoning_enabled=oow.reasoning_enabled,
        oow_mode=oow.mode,
        openai_api=OPENAI_API,
        openai_prompt_profile=getattr(oow, "openai_prompt_profile", None),
        navigation_profile=os.environ.get("MOOS_LLM_NAVIGATION_PROFILE", "default"),
        oow_model=oow.model,
        bridge_host=BRIDGE_HOST,
        bridge_port=BRIDGE_PORT,
        vllm_host=VLLM_HOST,
        vllm_port=VLLM_PORT,
        vhf_input_dir=str(vhf.input_dir),
        vhf_processed_dir=str(vhf.processed_dir),
        freeze_during_decision=lockstep_enabled,
        decision_interval_simulated_s=decision_interval_s,
        lockstep_vehicle_ports=vehicle_ports,
        encounter_only=getattr(oow, "encounter_only", False),
        encounter_trigger_range_m=getattr(oow, "teacher_trigger_range_m", None),
    )
    time_warp = max(1.0, float(os.environ.get("MOOS_LLM_TIME_WARP", "1")))
    poll_seconds = decision_interval_s / time_warp
    if lockstep_enabled:
        # Lockstep missions start uSimMarine paused so mission rendering,
        # deployment, bridge startup, and model startup cannot consume any of
        # the encounter. Begin the first requested navigation interval only
        # after the bridge has connected to every vessel.
        await asyncio.to_thread(
            _post_simulation_pause, moos_host, vehicle_ports, False
        )
        await asyncio.sleep(poll_seconds)
    while True:
        pause_started = 0.0
        if lockstep_enabled:
            await asyncio.to_thread(
                _post_simulation_pause, moos_host, vehicle_ports, True
            )
            pause_started = monotonic()
            audit(
                "simulation_paused_for_decision",
                moos_host=moos_host,
                vehicle_ports=vehicle_ports,
                decision_interval_simulated_s=decision_interval_s,
            )
        next_pause_deadline = 0.0
        try:
            terminal = await process_cycle(captain, oow, vhf)
        finally:
            if lockstep_enabled:
                paused_wall_duration_s = monotonic() - pause_started
                await asyncio.to_thread(
                    _post_simulation_pause, moos_host, vehicle_ports, False
                )
                # Start the navigation interval only after the bridge confirms
                # that resume was posted to every vehicle simulator.
                next_pause_deadline = monotonic() + poll_seconds
                audit(
                    "simulation_resumed_after_decision",
                    moos_host=moos_host,
                    vehicle_ports=vehicle_ports,
                    paused_wall_duration_s=paused_wall_duration_s,
                    next_decision_after_simulated_s=decision_interval_s,
                )
        if terminal:
            if os.environ.get("MOOS_LLM_EXIT_ON_COMPLETE", "").lower() in {
                "1", "true", "yes", "on",
            }:
                print("[mission] terminal state reached; exiting batch run.", flush=True)
                return
            print(
                "[mission] terminal state reached; inference stopped. "
                "Press Ctrl-C to stop the simulator.",
                flush=True,
            )
            await asyncio.Event().wait()
        if lockstep_enabled:
            await asyncio.sleep(max(0.0, next_pause_deadline - monotonic()))
        else:
            await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
