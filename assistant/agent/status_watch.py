"""Watches Agent Mode's status file and announces state transitions by voice."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from assistant.logger import get_logger

log = get_logger("status.watch")

ANNOUNCE = {
    # state: (template when starting, template when finishing / done)
    "working": "Agent mode update: I'm now working on {task}.",
    "done": "Agent mode update: I'm done with {task}.",
    "blocked": "Agent mode is blocked on {task} and needs your input.",
    "error": "Agent mode hit a problem on {task}.",
    "idle": "Agent mode is idle again.",
}


class StatusWatcher:
    def __init__(self, status_file: Path, announce: Callable[[str, str], None],
                 poll_seconds: float = 3.0):
        self.status_file = status_file
        self.announce = announce  # (speak, display)
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_state: str | None = None
        self._last_task: str | None = None
        self._last_spoken = 0.0

    def _read(self) -> dict:
        if self.status_file.exists():
            try:
                return json.loads(self.status_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"state": "idle", "task": "", "detail": ""}

    def _loop(self) -> None:
        # Prime without announcing history.
        self._last_state = self._read().get("state")
        self._last_task = self._read().get("task")
        while not self._stop.wait(self.poll_seconds):
            try:
                status = self._read()
            except Exception:  # noqa: BLE001
                continue
            state = status.get("state", "idle")
            task = (status.get("task") or "").strip() or "your project"
            changed = state != self._last_state or task != self._last_task
            # Also re-announce "done" rows whose updated_at is fresh but state
            # was already 'done' on first read (assistant started later).
            if not changed:
                continue
            if state == self._last_state and state != "working":
                # task-only change for non-working states: only announce working/done
                self._last_task = task
                continue

            template = ANNOUNCE.get(state)
            now = time.time()
            if template and now - self._last_spoken > 2.0:
                speak = template.format(task=task)
                detail = (status.get("detail") or "").strip()
                if detail and state == "working":
                    display = f"🤖 Agent working: **{task}**\n   {detail}"
                elif state == "done":
                    display = f"✅ Agent finished: **{task}**"
                    if detail:
                        display += f"\n   {detail}"
                else:
                    display = f"🤖 Agent status: {state}\n   Task: {task}"
                log.info("announce: %s", speak)
                self.announce(speak, display)
                self._last_spoken = now

            self._last_state = state
            self._last_task = task

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="status-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
