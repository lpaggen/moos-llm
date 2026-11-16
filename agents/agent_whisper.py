"""Small, side-effect-free WAV transcription component."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any
import wave


@dataclass(frozen=True)
class Transcription:
    """The normalized result returned by Whisper."""

    text: str
    language: str | None = None
    language_probability: float | None = None

    def to_dict(self) -> dict[str, str | float | None]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class WhisperModel:
    """Transcribe existing WAV files without recording or publishing anything."""

    def __init__(
        self,
        model_name: str = "base",
        *,
        device: str = "auto",
        compute_type: str = "default",
        transcriber: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._transcriber = transcriber

    def _get_transcriber(self) -> Any:
        if self._transcriber is None:
            try:
                from faster_whisper import WhisperModel as FasterWhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "WAV transcription requires the optional 'faster-whisper' "
                    "dependency"
                ) from exc

            self._transcriber = FasterWhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._transcriber

    @staticmethod
    def _validate_wav(audio_path: str | Path) -> Path:
        path = Path(audio_path)
        if path.suffix.lower() != ".wav":
            raise ValueError(f"WhisperModel only accepts .wav files: {path}")
        if not path.is_file():
            raise FileNotFoundError(f"WAV file does not exist: {path}")

        try:
            with wave.open(str(path), "rb") as wav_file:
                if wav_file.getnframes() == 0:
                    raise ValueError(f"WAV file contains no audio frames: {path}")
        except (EOFError, wave.Error) as exc:
            raise ValueError(f"invalid WAV file: {path}") from exc
        return path

    def transcribe(self, audio_path: str | Path) -> Transcription:
        """Return a structured transcription object for one WAV file."""
        path = self._validate_wav(audio_path)
        segments, info = self._get_transcriber().transcribe(
            str(path),
            task="transcribe",
            vad_filter=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise ValueError(f"no speech detected in WAV file: {path}")

        return Transcription(
            text=text,
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
        )

    def transcribe_text(self, audio_path: str | Path) -> str:
        """Return only the transcript string."""
        return self.transcribe(audio_path).text

    def transcribe_json(self, audio_path: str | Path) -> str:
        """Return the transcription serialized as JSON."""
        return self.transcribe(audio_path).to_json()
