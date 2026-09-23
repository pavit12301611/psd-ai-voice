"""Text-to-speech output (espeak-ng via pyttsx3) with a worker queue."""

from __future__ import annotations

import queue
import threading
from typing import Callable

from assistant.logger import get_logger

log = get_logger("speaker")

_DONE = object()


class Speaker:
    """Thread-safe TTS queue. speak() never blocks the caller."""

    def __init__(self, rate: int = 170, volume: float = 1.0,
                 voice: str | None = None, mute: bool = False,
                 on_state: Callable[[bool], None] | None = None):
        self.rate = rate
        self.volume = volume
        self.voice = voice
        self.mute = mute
        self.on_state = on_state
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._engine = None
        self._ready = threading.Event()
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate)
            engine.setProperty("volume", self.volume)
            if self.voice:
                for v in engine.getProperty("voices") or []:
                    if self.voice.lower() in (v.id or "").lower() or \
                       self.voice.lower() in (v.name or "").lower():
                        engine.setProperty("voice", v.id)
                        break
            self._engine = engine
            log.info("tts engine ready (pyttsx3/espeak)")
        except Exception as exc:  # noqa: BLE001
            log.warning("pyttsx3 unavailable (%s); speech will be logged only", exc)
            self._engine = None
        self._ready.set()

        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is _DONE:
                break
            self._say_blocking(str(item))

    def _say_blocking(self, text: str) -> None:
        text = " ".join((text or "").split())
        if not text:
            return
        if self.mute:
            log.info("[muted TTS] %s", text)
            return
        if self.on_state:
            try:
                self.on_state(True)
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._engine is not None:
                self._engine.say(text)
                self._engine.runAndWait()
            else:
                log.info("[TTS unavailable] %s", text)
        except Exception as exc:  # noqa: BLE001
            log.warning("tts failed: %s", exc)
            # engine can die after suspend/resume — try one re-init
            try:
                import pyttsx3
                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", self.rate)
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:  # noqa: BLE001
                self._engine = None
        finally:
            if self.on_state:
                try:
                    self.on_state(False)
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------
    def say(self, text: str) -> None:
        if text:
            self._queue.put(text)

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(_DONE)
        if self._thread:
            self._thread.join(timeout=5)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until the queue is drained (best effort)."""
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.empty():
                # small grace period — an item may be mid-utterance
                time.sleep(0.15)
                if self._queue.empty():
                    return True
            time.sleep(0.1)
        return False


def earcon(kind: str = "listen") -> None:
    """Short synthesized feedback beep (best effort, non-fatal)."""
    try:
        import numpy as np
        import sounddevice as sd

        sr = 16000
        if kind == "listen":
            freqs, duration = (660, 880), 0.14
        elif kind == "stop":
            freqs, duration = (440, 330), 0.16
        elif kind == "think":
            freqs, duration = (520,), 0.1
        else:
            freqs, duration = (600,), 0.08
        total = int(sr * duration)
        t = np.linspace(0, duration, total, False)
        wave = np.zeros(total, dtype=np.float32)
        seg = total // len(freqs)
        for i, freq in enumerate(freqs):
            chunk = slice(i * seg, (i + 1) * seg if i < len(freqs) - 1 else total)
            wave[chunk] = 0.22 * np.sin(2 * np.pi * freq * t[chunk]).astype(np.float32)
        # simple fade to avoid clicks
        fade = int(sr * 0.01)
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
        sd.play(wave, sr)
    except Exception:  # noqa: BLE001 — feedback sound is optional
        pass
