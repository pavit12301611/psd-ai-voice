"""Shared datatypes used across the assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Transcript:
    """Result of a speech-to-text pass."""

    text: str
    confidence: float = 0.0


@dataclass
class NLUResult:
    """Parsed intent + entities for one utterance."""

    intent: str
    confidence: float
    raw: str
    normalized: str
    entities: dict[str, Any] = field(default_factory=dict)


@dataclass
class Reply:
    """What the assistant should say out loud and show on the HUD."""

    speak: str
    display: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    # When True the utterance could not be handled locally and was sent to Agent Mode.
    escalated: bool = False

    def shown(self) -> str:
        return self.display if self.display is not None else self.speak
