"""Shared pytest fixtures."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_runtime(tmp_path: Path) -> Path:
    """Creates a isolated runtime tree (knowledge, calendar, agent dirs)."""
    for sub in ("agent_inbox", "agent_outbox", "calendar", "models"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture()
def cfg(tmp_runtime: Path):
    from assistant.config import Config

    data = {
        "audio": {"sample_rate": 16000, "vad_energy_threshold": 400,
                  "silence_seconds": 1.2, "max_utterance_seconds": 12,
                  "min_speech_seconds": 0.35},
        "stt": {"engine": "vosk",
                "model_path": str(tmp_runtime / "models" / "vosk" / "missing")},
        "tts": {"rate": 170, "volume": 1.0, "voice": None, "mute": True},
        "activation": {
            "shake": {"enabled": True, "backend": "auto",
                      "direction_changes": 3, "window_seconds": 1.0,
                      "min_travel_px": 18, "cooldown_seconds": 0.8,
                      "min_event_px": 0.8},
            "hotkey": {"enabled": True, "keys": ["ctrl", "alt", "v"]},
            "always_listening": False,
            "session_timeout": 20,
        },
        "orb": {"enabled": False, "size": 132, "opacity": 0.97,
                "follow_omega": 14.0, "follow_zeta": 0.85},
        "agent": {
            "host": "127.0.0.1",
            "port": 8765,
            "inbox_dir": str(tmp_runtime / "agent_inbox"),
            "outbox_dir": str(tmp_runtime / "agent_outbox"),
            "status_file": str(tmp_runtime / "agent_status.json"),
            "answer_timeout": 5,
            "llm": {"enabled": False},
        },
        "knowledge": {
            "seed_file": str(ROOT / "config" / "knowledge.seed.json"),
            "path": str(tmp_runtime / "knowledge.json"),
        },
        "calendar": {
            "db": str(tmp_runtime / "calendar.db"),
            "ics_dir": str(tmp_runtime / "calendar"),
            "open_with": "gnome-calendar",
        },
        "notes": {"path": str(tmp_runtime / "notes.json")},
        "logging": {"level": "WARNING",
                    "file": str(tmp_runtime / "assistant.log")},
    }
    return Config(data)


@pytest.fixture()
def knowledge(cfg):
    from assistant.brain.knowledge import KnowledgeBase

    return KnowledgeBase(cfg.path_of("knowledge.path"),
                         cfg.path_of("knowledge.seed_file"))
