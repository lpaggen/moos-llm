"""Interpret VHF transcripts without performing bridge I/O."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from itertools import chain
from pathlib import Path
from typing import Any
import asyncio
from uuid import uuid4
from datetime import datetime, timezone
from pydantic import ValidationError

from .agent_whisper import WhisperModel
from .skill_loader import load_skill
from .schemas.vhf import AudioSource, TranscriptionRecord, VHFAdvisory, VHFEvent, VHFInterpretation
from bridge.state import BridgeSnapshot
from tools.config_value import get


SYSTEM_PROMPT = load_skill("vhf")
DEFAULT_MINIMUM_CONFIDENCE = float(get("vhf", "minimum_confidence"))
DEFAULT_INPUT_DIR = Path(
    os.environ.get("MOOS_LLM_VHF_INPUT_DIR") or get("vhf", "input_dir")
)
DEFAULT_PROCESSED_DIR = Path(
    os.environ.get("MOOS_LLM_VHF_PROCESSED_DIR") or get("vhf", "processed_dir")
)
DEFAULT_FAILED_DIR = Path(
    os.environ.get("MOOS_LLM_VHF_FAILED_DIR")
    or get("vhf", "failed_dir", fallback="audio/vhf_failed")
)
DEFAULT_DEVICE = get("vhf", "device")
DEFAULT_COMPUTE_TYPE = get("vhf", "compute_type")
DEFAULT_WHISPER_MODEL = get("models", "whisper")


class AgentVHF:
    """Convert a transcript into a structured advisory using an LLM client."""

    def __init__(
        self,
        client: Any,
        model: str,
        *,
        own_ship: str | None = None,
        captain_queue: asyncio.Queue
    ) -> None:
        self.client = client
        self.model = model
        self.own_ship = own_ship
        self.captain_queue = captain_queue
        self.minimum_confidence = DEFAULT_MINIMUM_CONFIDENCE
        self.input_dir = DEFAULT_INPUT_DIR
        self.processed_dir = DEFAULT_PROCESSED_DIR
        self.failed_dir = DEFAULT_FAILED_DIR
        self.last_failures: list[dict[str, str]] = []
        self.whisper = WhisperModel(
            DEFAULT_WHISPER_MODEL,
            device=DEFAULT_DEVICE,
            compute_type=DEFAULT_COMPUTE_TYPE,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_name(path: Path) -> str:
        return path.name

    def interpret(
        self,
        transcript: str,
        *,
        bridge_snapshot: BridgeSnapshot,
        channel: int | None = None,
        known_vessels: list[str] | None = None,
        known_locations: list[str] | None = None,
        language: str | None = None,
        currently_paused: bool = False,
    ) -> VHFInterpretation:
        transcript = transcript.strip()
        if not transcript:
            raise ValueError("transcript cannot be empty")

        # this block requests a copy of the state and takes what the agent needs only
        bridge_state = bridge_snapshot.state_for_agent()
        contacts = bridge_state.get("contacts", [])
        bridge_vessels = [
            str(contact["name"])
            for contact in contacts
            if isinstance(contact, dict) and contact.get("name")
        ]

        payload = {
            "transcript": transcript,
            "receiver_metadata": {
                "channel": channel,
                "detected_language": language,
            },
            "operational_context": {
                "bridge_state_version": bridge_snapshot.version,
                "own_ship": self.own_ship or bridge_state.get("vehicle"),
                "known_vessels": known_vessels or bridge_vessels,
                "known_locations": known_locations or [],
                "currently_paused": bridge_state.get(
                    "paused", currently_paused
                ),
            },
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        last_error: Exception | None = None
        last_content: str | None = None
        response_schema = VHFInterpretation.model_json_schema()
        # Structured generation must emit the complete interpretation rather
        # than silently inheriting Pydantic defaults for omitted operational
        # fields. Nullable values remain valid, but every key is explicit.
        response_schema["required"] = list(response_schema["properties"])
        for attempt in range(2):
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=512,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "vhf_interpretation",
                        "strict": True,
                        "schema": response_schema,
                    },
                },
            )
            content = response.choices[0].message.content
            last_content = content
            try:
                if not content:
                    raise RuntimeError("VHF interpreter returned no content")
                return VHFInterpretation.model_validate_json(content)
            except (ValidationError, RuntimeError) as exc:
                last_error = exc
                if attempt == 0:
                    messages.extend([
                        {"role": "assistant", "content": content or ""},
                        {
                            "role": "user",
                            "content": (
                                "The interpretation violated the VHF contract: "
                                f"{exc}. Re-read the literal call structure and "
                                "return one corrected JSON object."
                            ),
                        },
                    ])
        raise RuntimeError(
            "VHF interpreter returned invalid output twice "
            f"({last_error}); last_content={last_content!r}"
        ) from last_error

    async def relay_to_captain(
        self,
        interpretation: VHFInterpretation,
        *,
        currently_paused: bool,
        message_id: str | None = None,
        source: dict[str, Any] | None = None,
        transcription: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Relay interpreted evidence; never execute it as a direct command."""
        action = interpretation.recommended_action
        actionable = interpretation.actionable(self.minimum_confidence)
        if (action == "stop" and currently_paused) or (
            action == "resume" and not currently_paused
        ):
            actionable = False

        advisory = VHFAdvisory(
            message_id=uuid4(),
            speaker=interpretation.speaker,
            addressee=interpretation.addressee,
            addressed_to_own_ship=interpretation.addressed_to_own_ship,
            source_type=interpretation.source_type,
            identity_status=interpretation.identity_status,
            message=interpretation.normalized_message,
            intent=interpretation.intent,
            requested_action=interpretation.requested_action,
            requested_turn_direction=interpretation.requested_turn_direction,
            requested_turn_degrees=interpretation.requested_turn_degrees,
            requested_speed_knots=interpretation.requested_speed_knots,
            requested_course_degrees=interpretation.requested_course_degrees,
            directive_lifetime=interpretation.directive_lifetime,
            directive_end_condition=interpretation.directive_end_condition,
            reported_facts=interpretation.reported_facts,
            recommended_action=action,
            confidence=interpretation.confidence,
            clarification_required=interpretation.clarification_required,
        )

        # The queue crosses into JSON prompt construction and audit logging.
        # Convert UUID/datetime-bearing Pydantic values at this boundary so a
        # valid radio message can never crash the navigation cycle.
        report = {
            "message_id": message_id,
            "source": source,
            "transcription": transcription,
            "interpretation": interpretation.model_dump(mode="json"),
            "advisory": advisory.model_dump(mode="json"),
            "actionable": actionable,
        }
        await self.captain_queue.put({"source": "vhf", "report": report})
        return report["advisory"]

    async def process_directory(
        self,
        *,
        bridge_snapshot: BridgeSnapshot,
    ) -> list[VHFEvent]:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)
        self.last_failures = []

        events: list[VHFEvent] = []
        inputs = sorted(chain(self.input_dir.glob("*.wav"), self.input_dir.glob("*.json")))
        for input_path in inputs:
            input_kind = "audio" if input_path.suffix.lower() == ".wav" else "scenario_text"
            audio_sha256 = self._sha256(input_path)
            message_id = uuid4()
            source = AudioSource(
                device=DEFAULT_DEVICE if input_kind == "audio" else "deterministic_injector",
                channel=None,
                audio_file=str(input_path),
                audio_sha256=audio_sha256,
                input_kind=input_kind,
            )
            succeeded = False
            try:
                scenario_payload: dict[str, Any] = {}
                if input_kind == "audio":
                    transcription = self.whisper.transcribe(input_path)
                    transcription_record = TranscriptionRecord(
                        text=transcription.text,
                        language=transcription.language,
                        language_probability=transcription.language_probability,
                        model=DEFAULT_WHISPER_MODEL,
                    )
                else:
                    scenario_payload = json.loads(input_path.read_text(encoding="utf-8"))
                    if scenario_payload.get("schema_version") != 1:
                        raise ValueError("scenario VHF input schema_version must be 1")
                    transcript = str(scenario_payload.get("transcript") or "").strip()
                    if not transcript:
                        raise ValueError("scenario VHF transcript cannot be empty")
                    source = source.model_copy(update={
                        "channel": scenario_payload.get("channel"),
                        "scenario_id": scenario_payload.get("scenario_id"),
                        "scenario_event_id": scenario_payload.get("event_id"),
                        "injected_at_sim_s": scenario_payload.get("injected_at_sim_s"),
                    })
                    transcription_record = TranscriptionRecord(
                        text=transcript,
                        language=str(scenario_payload.get("language") or "en"),
                        language_probability=1.0,
                        model="scenario-exact-text",
                    )
                interpretation = self.interpret(
                    transcription_record.text,
                    bridge_snapshot=bridge_snapshot,
                    channel=source.channel,
                    language=transcription_record.language,
                )
                event = VHFEvent(
                    message_id=message_id,
                    received_at=self._now(),
                    source=source,
                    transcription=transcription_record,
                    interpretation=interpretation,
                )
                events.append(event)
                await self.relay_to_captain(
                    interpretation,
                    currently_paused=bool(
                        bridge_snapshot.state_for_agent().get("paused", False)
                    ),
                    message_id=str(message_id),
                    source=source.model_dump(mode="json"),
                    transcription=transcription_record.model_dump(mode="json"),
                )
                succeeded = True
            except Exception as exc:
                self.last_failures.append({
                    "audio_file": str(input_path),
                    "audio_sha256": audio_sha256,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            finally:
                destination_dir = self.processed_dir if succeeded else self.failed_dir
                destination = destination_dir / input_path.name
                if input_path.exists():
                    shutil.move(str(input_path), str(destination))

        return events
