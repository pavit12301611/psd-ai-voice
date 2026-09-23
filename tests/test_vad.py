"""VAD + end-to-end --once style pipeline (no hardware)."""

import numpy as np

from assistant.audio.microphone import EnergyVAD, frame_energy


def _frame(energy_scale: float, n: int = 1024) -> np.ndarray:
    """Build an int16 frame with approximate RMS ≈ energy_scale on VAD scale."""
    # frame_energy = rms(int16/32768 * 10000) → amplitude = scale * 32768 / 10000
    amplitude = energy_scale * 32768 / 10000
    t = np.arange(n)
    return (amplitude * np.sin(2 * np.pi * 0.05 * t)).astype(np.int16)


def test_frame_energy_silence_near_zero():
    assert frame_energy(np.zeros(1024, dtype=np.int16)) < 1


def test_frame_energy_loud():
    loud = _frame(1000)
    assert frame_energy(loud) > 500


def test_vad_collects_utterance():
    vad = EnergyVAD(threshold=400, silence_seconds=0.05, min_speech_seconds=0.0,
                    max_seconds=5)
    states = []
    utterance = None
    # silence
    for _ in range(5):
        state, u = vad.feed(np.zeros(1024, dtype=np.int16))
        states.append(state)
        assert u is None
    # speech
    for _ in range(10):
        state, u = vad.feed(_frame(800))
        states.append(state)
        assert u is None
    # trailing silence → done
    for _ in range(10):
        state, u = vad.feed(np.zeros(1024, dtype=np.int16))
        if state == "done":
            utterance = u
            break
    assert utterance is not None
    assert len(utterance) >= 1024
    # after finish, VAD resets to listening
    state, _ = vad.feed(np.zeros(1024, dtype=np.int16))
    assert state == "listening"


def test_vad_ignores_short_blips():
    vad = EnergyVAD(threshold=400, silence_seconds=0.02,
                    min_speech_seconds=1.0, max_seconds=5)
    # one loud frame then silence — shorter than min_speech
    vad.feed(_frame(900))
    result = None
    for _ in range(20):
        state, u = vad.feed(np.zeros(1024, dtype=np.int16))
        if state == "done":
            result = (state, u)
            break
    # either not done yet or done with None utterance (too short)
    if result is not None:
        assert result[1] is None


def test_vad_max_length_forces_done():
    vad = EnergyVAD(threshold=400, silence_seconds=100,
                    min_speech_seconds=0.0, max_seconds=0.02)
    utterance = None
    for _ in range(10):
        state, u = vad.feed(_frame(900))
        if state == "done":
            utterance = u
            break
    assert utterance is not None
