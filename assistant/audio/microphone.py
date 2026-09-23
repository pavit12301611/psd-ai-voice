"""Microphone capture + energy-based voice activity detection."""

from __future__ import annotations

import queue
import threading
from typing import Callable

import numpy as np

from assistant.logger import get_logger

log = get_logger("mic")


class Microphone:
    """Captures int16/float32 frames from the default (or chosen) input."""

    def __init__(self, sample_rate: int = 16000, device: int | str | None = None,
                 blocksize: int = 1024):
        import sounddevice as sd  # fail fast

        self._sd = sd
        self.sample_rate = sample_rate
        self.device = device
        self.blocksize = blocksize
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream: "sd.InputStream | None" = None
        self._stop = threading.Event()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("mic status: %s", status)
        try:
            self.frames.put_nowait(indata.copy())
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            self.frames.put_nowait(indata.copy())

    def start(self) -> None:
        self._stream = self._sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.blocksize,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()
        log.info("microphone started (rate=%d, device=%s)",
                 self.sample_rate, self.device or "default")

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None


def frame_energy(frame: np.ndarray) -> float:
    """RMS energy of a frame (works for int16 or float)."""
    data = frame.astype(np.float32)
    if np.issubdtype(frame.dtype, np.integer):
        data = data / 32768.0
    return float(np.sqrt(np.mean(np.square(data))) * 10000)


class EnergyVAD:
    """Simple energy threshold VAD with hangover."""

    def __init__(self, threshold: float = 400, sample_rate: int = 16000,
                 silence_seconds: float = 1.2, min_speech_seconds: float = 0.35,
                 max_seconds: float = 12.0):
        # energy computed on [-1,1]*10000 scale — threshold tuned for ~int16/100
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.silence_frames_needed = max(1, int(silence_seconds * sample_rate / 1024))
        self.min_speech_frames = max(1, int(min_speech_seconds * sample_rate / 1024))
        self.max_frames = max(1, int(max_seconds * sample_rate / 1024))
        self.reset()

    def reset(self) -> None:
        self._voiced_seen = False
        self._silence_run = 0
        self._buffer: list[np.ndarray] = []
        self._frame_count = 0

    def feed(self, frame: np.ndarray) -> tuple[str, np.ndarray | None]:
        """Returns (state, utterance) where state ∈ listening|speech|done."""
        energy = frame_energy(frame)
        self._frame_count += 1

        if energy >= self.threshold:
            self._voiced_seen = True
            self._silence_run = 0
            self._buffer.append(frame)
        elif self._voiced_seen:
            self._silence_run += 1
            self._buffer.append(frame)
            if self._silence_run >= self.silence_frames_needed:
                return self._finish()
        else:
            # pure silence before speech — keep listening, don't bloat buffer
            pass

        if self._frame_count >= self.max_frames:
            if self._voiced_seen:
                return self._finish()
            self.reset()

        if self._voiced_seen:
            return "speech", None
        return "listening", None

    def _finish(self) -> tuple[str, np.ndarray | None]:
        utterance = None
        if len(self._buffer) >= self.min_speech_frames:
            utterance = np.concatenate(self._buffer, axis=0).reshape(-1)
        self.reset()
        return "done", utterance
