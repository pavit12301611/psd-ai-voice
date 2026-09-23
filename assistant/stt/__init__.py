"""Speech-to-text engines (offline by default)."""

from __future__ import annotations

from assistant.config import Config
from assistant.logger import get_logger
from assistant.types import Transcript

log = get_logger("stt")


class STTEngine:
    """Interface: transcribe float32/int16 16 kHz mono frames → Transcript."""

    name = "base"

    def transcribe(self, frames) -> Transcript:  # np.ndarray
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - trivial
        pass


class NullSTT(STTEngine):
    """Used when no model is available — returns empty transcripts."""

    name = "null"

    def transcribe(self, frames) -> Transcript:
        return Transcript(text="", confidence=0.0)


def build_stt(cfg: Config) -> STTEngine:
    engine = (cfg.get("stt.engine", "vosk") or "vosk").lower()
    if engine == "vosk":
        try:
            from assistant.stt.vosk_stt import VoskSTT

            return VoskSTT(cfg)
        except Exception as exc:  # noqa: BLE001
            log.warning("vosk unavailable (%s); falling back to whisper", exc)
            engine = "whisper"
    if engine == "whisper":
        try:
            from assistant.stt.whisper_stt import WhisperSTT

            return WhisperSTT(cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("whisper unavailable: %s", exc)
            return NullSTT()
    return NullSTT()
