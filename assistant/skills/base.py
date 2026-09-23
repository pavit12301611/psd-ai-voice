"""Skill base class."""

from __future__ import annotations

from typing import Any

from assistant.types import NLUResult, Reply


class Skill:
    name: str = "base"

    def __init__(self, cfg: Any, app: Any | None = None):
        self.cfg = cfg
        self.app = app

    def can_handle(self, nlu: NLUResult) -> float:
        """Return >0 confidence if this skill should run."""
        raise NotImplementedError

    def handle(self, nlu: NLUResult) -> Reply | None:
        raise NotImplementedError
