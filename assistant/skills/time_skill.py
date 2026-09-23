"""Time, date, and timer skill."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from assistant.logger import get_logger
from assistant.skills.base import Skill
from assistant.types import NLUResult, Reply

log = get_logger("time")

_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}


class TimeSkill(Skill):
    name = "time"

    def __init__(self, cfg, app=None):
        super().__init__(cfg, app)
        self._timers: dict[str, threading.Timer] = {}

    def can_handle(self, nlu: NLUResult) -> float:
        if nlu.intent in ("time_query", "date_query", "timer_set"):
            return 0.96
        return 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        if nlu.intent == "time_query":
            now = datetime.now()
            return Reply(
                speak=f"It's {now.strftime('%I:%M %p').lstrip('0').replace(' 0', ' ')}.",
                display=f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S')}",
            )
        if nlu.intent == "date_query":
            now = datetime.now()
            return Reply(
                speak=f"Today is {now.strftime('%A, %B %d, %Y')}.",
                display=f"📆 {now.strftime('%A, %B %d, %Y')}",
            )
        if nlu.intent == "timer_set":
            return self._set_timer(nlu)
        return None

    def _set_timer(self, nlu: NLUResult) -> Reply:
        value = nlu.entities.get("duration_value")
        unit = nlu.entities.get("duration_unit")
        if not value or not unit:
            return Reply(
                speak="How long should the timer be? Say: set a timer for 5 minutes.",
            )
        seconds = int(value) * _UNIT_SECONDS.get(unit, 60)
        seconds = max(1, min(seconds, 6 * 3600))
        label = f"{value} {unit}{'s' if value != 1 else ''}"

        def _fire() -> None:
            log.info("timer fired (%s)", label)
            if self.app:
                self.app.announce(
                    speak=f"Your {label} timer is done.",
                    display=f"⏰ Timer done: {label}",
                )
            self._timers.pop(label, None)

        timer = threading.Timer(seconds, _fire)
        timer.daemon = True
        timer.start()
        self._timers[label] = timer
        ends = datetime.now() + timedelta(seconds=seconds)
        return Reply(
            speak=f"Timer set for {label}. I'll ping you at "
                  f"{ends.strftime('%I:%M %p').lstrip('0').replace(' 0', ' ')}.",
            display=f"⏰ Timer: {label} (ends {ends.strftime('%H:%M:%S')})",
            data={"seconds": seconds},
        )
