"""Agent-status skill — 'are you working?', 'project status', 'done yet?'."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from assistant.skills.base import Skill
from assistant.types import NLUResult, Reply

_STATE_PHRASES = {
    "idle": "Agent Mode is idle — nothing queued right now.",
    "working": "Agent Mode is working",
    "done": "Agent Mode finished the last task",
    "blocked": "Agent Mode is blocked and needs your input",
    "error": "Agent Mode hit an error on the last task",
}


class StatusSkill(Skill):
    name = "status"

    def __init__(self, cfg, app=None):
        super().__init__(cfg, app)
        self.status_file: Path = cfg.path_of("agent.status_file")

    def _read(self) -> dict:
        if self.status_file.exists():
            try:
                return json.loads(self.status_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"state": "idle", "task": "", "detail": "", "updated_at": None}

    def can_handle(self, nlu: NLUResult) -> float:
        return 0.97 if nlu.intent == "status_check" else 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        status = self._read()
        state = status.get("state", "idle")
        task = (status.get("task") or "").strip()
        detail = (status.get("detail") or "").strip()
        updated = status.get("updated_at")

        base = _STATE_PHRASES.get(state, f"Agent Mode state: {state}")
        if state in ("working", "done", "blocked", "error") and task:
            speak = f"{base}: {task}."
        else:
            speak = base
        if detail and state != "idle":
            speak += f" {detail}" if len(speak + detail) < 180 else ""

        lines = [f"🤖 Agent Mode: **{state}**"]
        if task:
            lines.append(f"   Task: {task}")
        if detail:
            lines.append(f"   {detail}")
        if updated:
            lines.append(f"   Updated: {updated}")
        if state == "working" and updated:
            try:
                age = datetime.now().astimezone() - datetime.fromisoformat(updated)
                lines.append(f"   Running for {int(age.total_seconds() // 60)} min")
            except ValueError:
                pass

        return Reply(speak=speak, display="\n".join(lines), data=status)
