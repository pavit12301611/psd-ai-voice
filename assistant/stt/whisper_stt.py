"""Optional faster-whisper backend (better accuracy, heavier install)."""

from __future__ import annotations

import numpy as np

from assistant.config import Config
from assistant.logger import get_logger
from assistant.stt import STTEngine
from assistant.types import Transcript

log = get_logger("stt.whisper")


class WhisperSTT(STTEngine):
    name = "whisper"

    def __init__(self, cfg: Config):
        from faster_whisper import WhisperModel  # fail fast

        size = cfg.get("stt.whisper_size", "base")
        log.info("loading faster-whisper model '%s'", size)
        self._model = WhisperModel(size, device="cpu", compute_type="int8")
        self.sample_rate = int(cfg.get("audio.sample_rate", 16000))

    def transcribe(self, frames: np.ndarray) -> Transcript:
        if frames is None or len(frames) == 0:
            return Transcript("", 0.0)
        audio = frames.astype(np.float32)
        if np.issubdtype(frames.dtype, np.integer):
            audio = audio / 32768.0
        segments, info = self._model.transcribe(
            audio, beam_size=1, language="en", vad_filter=False
        )
        parts = [seg.text.strip() for seg in segments]
        text = " ".join(p for p in parts if p).strip()
        conf = float(getattr(info, "language_probability", 0.8))
        return Transcript(text=text, confidence=conf or 0.7)
