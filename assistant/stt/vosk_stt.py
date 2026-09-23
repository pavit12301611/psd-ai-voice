"""Offline speech recognition with Vosk."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

from assistant.config import Config
from assistant.logger import get_logger
from assistant.stt import STTEngine
from assistant.types import Transcript

log = get_logger("stt.vosk")


class VoskSTT(STTEngine):
    name = "vosk"

    def __init__(self, cfg: Config):
        import vosk  # noqa: F401 — fail fast if not installed

        self._vosk = vosk
        model_path = cfg.path_of("stt.model_path")
        if not model_path.exists():
            raise FileNotFoundError(
                f"Vosk model not found at {model_path}. Run ./run.sh --setup to download it."
            )
        log.info("loading vosk model %s", model_path)
        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(model_path))
        self.sample_rate = int(cfg.get("audio.sample_rate", 16000))
        self._ready = True
        log.info("vosk ready")

    def transcribe(self, frames: np.ndarray) -> Transcript:
        if frames is None or len(frames) == 0:
            return Transcript("", 0.0)

        # Accept float32 [-1,1] or int16.
        if frames.dtype != np.int16:
            if frames.dtype == np.float32 or frames.dtype == np.float64:
                pcm = np.clip(frames, -1.0, 1.0)
                pcm = (pcm * 32767.0).astype(np.int16)
            else:
                pcm = frames.astype(np.int16)
        else:
            pcm = frames

        recognizer = self._vosk.KaldiRecognizer(self._model, self.sample_rate)
        recognizer.AcceptWaveform(pcm.tobytes())
        result = json.loads(recognizer.FinalResult())
        text = (result.get("text") or "").strip()
        conf = float(result.get("conf") or 0.0)
        # Vosk 'conf' is often 0.0 for short utterances — treat missing as neutral.
        if conf == 0.0 and text:
            conf = 0.7
        return Transcript(text=text, confidence=conf)


class VoskFileSTT(STTEngine):
    """Debug helper: transcribe a WAV file."""

    def __init__(self, cfg: Config):
        self.inner = VoskSTT(cfg)

    def transcribe_path(self, path: Path) -> Transcript:
        with wave.open(str(path), "rb") as wf:
            data = wf.readframes(wf.getnframes())
        arr = np.frombuffer(data, dtype=np.int16)
        return self.inner.transcribe(arr)
