"""Quick notes / memory pads."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from assistant.logger import get_logger
from assistant.skills.base import Skill
from assistant.types import NLUResult, Reply

log = get_logger("notes")


class NotesSkill(Skill):
    name = "notes"

    def __init__(self, cfg, app=None):
        super().__init__(cfg, app)
        self.path: Path = cfg.path_of("notes.path")
        self._notes = self._load()

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("notes unreadable: %s", exc)
        return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._notes, indent=2), encoding="utf-8")

    def can_handle(self, nlu: NLUResult) -> float:
        if nlu.intent in ("note_add", "note_read", "note_clear"):
            return 0.95
        return 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        if nlu.intent == "note_add":
            import re
            text = nlu.raw.strip()
            text = re.sub(r"^(?:add|take|make)\s+(?:a\s+)?(?:quick\s+)?note\s*(?:that\s+|about\s+|:)?",
                          "", text, flags=re.I)
            text = re.sub(r"^note[:,]\s*", "", text, flags=re.I).strip()
            if not text:
                return Reply(speak="What should the note say?")
            self._notes.append({
                "text": text,
                "at": datetime.now().astimezone().isoformat(),
            })
            self._save()
            return Reply(
                speak=f"Noted: {text}",
                display=f"📝 Note added: {text}",
            )
        if nlu.intent == "note_read":
            if not self._notes:
                return Reply(speak="You don't have any notes yet.",
                             display="📝 No notes.")
            lines = ["📝 Your notes:"] + [
                f"  {i+1}. {n['text']}" for i, n in enumerate(self._notes[-15:])
            ]
            spoken = "; ".join(n["text"] for n in self._notes[-5:])
            return Reply(speak=f"Your recent notes: {spoken}.",
                         display="\n".join(lines))
        if nlu.intent == "note_clear":
            count = len(self._notes)
            self._notes = []
            self._save()
            return Reply(speak=f"Cleared {count} note{'s' if count != 1 else ''}.",
                         display=f"🗑️ Cleared {count} notes.")
        return None
