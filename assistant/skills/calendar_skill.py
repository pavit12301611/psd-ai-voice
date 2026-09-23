"""Calendar skill — "add this to my calendar", list events, open GNOME Calendar."""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import rrule as dateutil_rrule  # noqa: F401  (kept for clarity)
from dateutil import tz as dateutil_tz  # noqa: F401
import parsedatetime

from assistant.logger import get_logger
from assistant.skills.base import Skill
from assistant.types import NLUResult, Reply

log = get_logger("calendar")

_FILLER_WORDS = re.compile(
    r"\b(to|my|the|on|in|at|a|an|add|schedule|create|put|book|set|up|"
    r"please|calendar|event|meeting|appointment|reminder|remind|me|for|"
    r"diary|agenda|this|that|it)\b",
    re.I,
)

# Date keywords stripped from the title (require real content — no empty matches).
_DAY_NAMES = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = ("january|february|march|april|may|june|july|august|"
           "september|october|november|december")
_DATE_PHRASE = re.compile(
    rf"\b(?:"
    rf"today|tonight|tomorrow|day after tomorrow|"
    rf"next\s+(?:{_DAY_NAMES}|week|weekend|month|year)|"
    rf"this\s+(?:morning|afternoon|evening|week|weekend|{_DAY_NAMES})|"
    rf"{_DAY_NAMES}|"
    rf"{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,\s*\d{{4}})?|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}(?:\s+\d{{4}})?"
    rf")\b",
    re.I,
)
_TIME_PHRASE = re.compile(
    r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)"
    r"|\b(?:at\s+)?\d{1,2}:\d{2}\b"
    r"|\b(?:noon|midnight)\b"
    r"|\b(?:at\s+)?(?:this\s+)?(?:morning|afternoon|evening)\b",
    re.I,
)


class CalendarSkill(Skill):
    name = "calendar"

    def __init__(self, cfg, app=None):
        super().__init__(cfg, app)
        self.db_path: Path = cfg.path_of("calendar.db")
        self.ics_dir: Path = cfg.path_of("calendar.ics_dir")
        self.open_with: str = cfg.get("calendar.open_with", "gnome-calendar")
        self._lock = threading.Lock()
        self._init_db()

    # -- db ----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_utc TEXT NOT NULL,
                created TEXT NOT NULL
            )"""
        )
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            pass

    # -- intents -----------------------------------------------------
    def can_handle(self, nlu: NLUResult) -> float:
        if nlu.intent in ("calendar_add", "calendar_list", "calendar_open"):
            return 0.95
        # "remind me ..." without explicit calendar words
        if re.match(r"^(?:please\s+)?remind me\b", nlu.normalized):
            return 0.9
        return 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        if nlu.intent == "calendar_add" or nlu.normalized.startswith(("remind me", "please remind me")):
            return self._add(nlu)
        if nlu.intent == "calendar_list":
            return self._list()
        if nlu.intent == "calendar_open":
            return self._open()
        return None

    # -- actions -----------------------------------------------------
    def _parse_when(self, text: str) -> datetime | None:
        cal = parsedatetime.Calendar()
        now = datetime.now().astimezone()
        # parsedatetime returns struct_time in local naive form
        struct, status = cal.parse(text, now)
        if status == 0:
            return None
        when = datetime(*struct[:6])
        # If only a time was given (no date components), parsedatetime keeps
        # today's date — that's fine. Reject "long ago" defaults.
        if when.year < 2000:
            return None
        # Attach local timezone.
        return when.astimezone() if when.tzinfo else when.replace(
            tzinfo=now.tzinfo
        )

    def _extract_parts(self, utterance: str) -> tuple[str, datetime | None]:
        # parsedatetime understands the whole sentence best
        # ("... tomorrow at 3 pm ..." even with filler words around it).
        when = self._parse_when(utterance)

        # Title = utterance minus datetime phrases minus command filler.
        title = utterance
        title = _TIME_PHRASE.sub(" ", title)
        title = _DATE_PHRASE.sub(" ", title)
        title = re.sub(
            r"^(?:please\s+)?(?:add|schedule|create|put|book|set up|remind me(?: to)?)\s+",
            "", title, flags=re.I,
        )
        title = re.sub(
            r"\b(?:to|on|in|at|for|by)?\s*(?:my|the)?\s*calendar\b", " ",
            title, flags=re.I,
        )
        title = re.sub(
            r"^(?:an?\s+)?(?:event|meeting|appointment|reminder)\s+(?:for|about|with)\s+",
            "", title, flags=re.I,
        )
        # strip leftover glue words, keep meaningful nouns
        title = re.sub(
            r"\b(?:please|to|on|in|at|for|by|my|the|a|an|a\.m\.|p\.m\.)\b", " ",
            title, flags=re.I,
        )
        title = re.sub(r"\s+", " ", title).strip(" ,-.")

        return title or "Untitled event", when

    def _add(self, nlu: NLUResult) -> Reply:
        source = nlu.entities.get("event_text") or nlu.raw
        title, when = self._extract_parts(source)

        if when is None:
            return Reply(
                speak="I can add that — tell me when. For example: tomorrow at 3 pm.",
                display="Couldn't find a date/time. Say e.g. "
                        "\"add project sync tomorrow at 3 pm to my calendar\".",
            )

        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (title, start_utc, created) VALUES (?,?,?)",
                (title, when.isoformat(), datetime.now().astimezone().isoformat()),
            )

        ics_path = self._export_ics()
        local_str = when.strftime("%A %B %d at %I:%M %p").lstrip("0")
        spoken_when = self._spoken_when(when)
        display = (
            f"📅 Added **{title}**\n"
            f"   {when.strftime('%a %b %d, %Y %H:%M')}\n"
            f"   ICS: {ics_path}"
        )
        return Reply(
            speak=f"Done — {title}, {spoken_when}. It's on your calendar.",
            display=display,
            data={"title": title, "start": when.isoformat(), "when_spoken": spoken_when,
                  "local": local_str},
        )

    @staticmethod
    def _spoken_when(when: datetime) -> str:
        now = datetime.now().astimezone()
        delta_days = (when.date() - now.date()).days
        day = {
            0: "today",
            1: "tomorrow",
            2: "the day after tomorrow",
        }.get(delta_days)
        if day is None:
            day = "on " + when.strftime("%A").lstrip("0")
            if when.year != now.year:
                day += when.strftime(", %B %d %Y")
            else:
                day += when.strftime(" %B %d")
        else:
            if when.year != now.year:
                day += when.strftime(", %B %d %Y")
        time_part = when.strftime("%I:%M %p").lstrip("0").replace(" 0", " ")
        return f"{day} at {time_part}"

    def _list(self) -> Reply:
        now = datetime.now().astimezone()
        soon = now + timedelta(days=14)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT title, start_utc FROM events WHERE start_utc >= ? "
                "ORDER BY start_utc LIMIT 10",
                (now.isoformat(),),
            ).fetchall()
        if not rows:
            return Reply(
                speak="You have nothing on your calendar for the next two weeks.",
                display="📭 No upcoming events (next 14 days).",
            )
        lines = ["🗓️ Upcoming events:"]
        spoken_parts = []
        for title, start_iso in rows:
            try:
                when = datetime.fromisoformat(start_iso)
            except ValueError:
                continue
            lines.append(f"  • {when.strftime('%a %b %d %H:%M')} — {title}")
            spoken_parts.append(f"{title}, {self._spoken_when(when)}")
        speak = "Here's what's coming up: " + "; ".join(spoken_parts[:5]) + "."
        return Reply(speak=speak, display="\n".join(lines))

    def _open(self) -> Reply:
        import shutil
        import subprocess

        if shutil.which(self.open_with) or shutil.which("gnome-calendar"):
            binary = self.open_with if shutil.which(self.open_with) else "gnome-calendar"
            try:
                subprocess.Popen(
                    [binary], start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return Reply(speak="Opening your calendar now.",
                             display=f"🗓️ Launched {binary}")
            except OSError as exc:
                log.warning("failed to open calendar: %s", exc)
        # Fallback: open the ICS directory in Files
        ics = self._export_ics()
        try:
            subprocess.Popen(
                ["xdg-open", str(ics.parent)], start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
        return Reply(
            speak="GNOME Calendar isn't installed, so I opened your calendar files instead.",
            display=f"🗓️ Calendar file: {ics}",
        )

    def _export_ics(self) -> Path:
        """Write all events to a single standards-compliant .ics file."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, start_utc FROM events ORDER BY start_utc"
            ).fetchall()
        self.ics_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//PSD Voice//Assistant//EN",
            "CALSCALE:GREGORIAN",
        ]
        for event_id, title, start_iso in rows:
            try:
                when = datetime.fromisoformat(start_iso)
            except ValueError:
                continue
            end = when + timedelta(hours=1)
            stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
            lines += [
                "BEGIN:VEVENT",
                f"UID:psd-voice-{event_id}@localhost",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{when.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{title}",
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        path = self.ics_dir / "assistant.ics"
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return path
