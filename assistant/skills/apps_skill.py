"""App launcher skill — 'open firefox', 'launch GNOME Calendar', ..."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from assistant.logger import get_logger
from assistant.skills.base import Skill
from assistant.types import NLUResult, Reply

log = get_logger("apps")

_DESKTOP_DIRS = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path(os.path.expanduser("~/.local/share/applications")),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path(os.path.expanduser("~/.local/share/flatpak/exports/share/applications")),
]

# Very common aliases → desktop-ish search keys.
_ALIASES = {
    "files": "nautilus",
    "file manager": "nautilus",
    "browser": "firefox",
    "web browser": "firefox",
    "terminal": "org.gnome.Terminal",
    "console": "org.gnome.Console",
    "text editor": "gedit",
    "calculator": "gnome-calculator",
    "calendar": "gnome-calendar",
    "settings": "gnome-control-center",
    "preferences": "gnome-control-center",
    "software center": "gnome-software",
    "app store": "gnome-software",
    "music": "rhythmbox",
    "videos": "totem",
    "camera": "cheese",
    "screenshots": "gnome-screenshot",
    "trash": "nautilus",
}


@dataclass
class DesktopEntry:
    path: Path
    app_id: str
    name: str
    generic: str
    keywords: str
    exec_line: str
    no_display: bool
    try_exec: str


def _parse_desktop(path: Path) -> DesktopEntry | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if "[Desktop Entry]" not in text:
        return None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith(("[", "#")):
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    if fields.get("Type") not in (None, "Application"):
        return None
    return DesktopEntry(
        path=path,
        app_id=path.stem,
        name=fields.get("Name", ""),
        generic=fields.get("GenericName", ""),
        keywords=fields.get("Keywords", "").replace(";", " "),
        exec_line=fields.get("Exec", ""),
        no_display=fields.get("NoDisplay", "").lower() in ("true", "yes"),
        try_exec=fields.get("TryExec", ""),
    )


class AppsSkill(Skill):
    name = "apps"

    def __init__(self, cfg, app=None):
        super().__init__(cfg, app)
        self._cache: list[DesktopEntry] | None = None

    # -- discovery -----------------------------------------------------
    def _entries(self) -> list[DesktopEntry]:
        if self._cache is not None:
            return self._cache
        entries: list[DesktopEntry] = []
        seen: set[str] = set()
        for directory in _DESKTOP_DIRS:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.desktop")):
                entry = _parse_desktop(path)
                if entry and entry.app_id not in seen:
                    seen.add(entry.app_id)
                    entries.append(entry)
        self._cache = entries
        log.info("discovered %d desktop entries", len(entries))
        return entries

    def rescan(self) -> None:
        self._cache = None

    # -- matching ------------------------------------------------------
    def _score(self, entry: DesktopEntry, query: str) -> float:
        q = query.lower().strip()
        if not q:
            return 0.0
        candidates = [
            (entry.name.lower(), 1.0),
            (entry.generic.lower(), 0.92),
            (entry.app_id.lower().replace("-", " ").replace(".", " "), 0.9),
            (entry.keywords.lower().replace(";", " "), 0.85),
            (entry.app_id.lower(), 0.9),
        ]
        best = 0.0
        for candidate, weight in candidates:
            if not candidate:
                continue
            if q == candidate:
                return 1.0 * weight + 0.1
            if q in candidate or candidate in q:
                best = max(best, (0.78 + 0.2 * SequenceMatcher(None, q, candidate).ratio()) * weight)
                continue
            ratio = SequenceMatcher(None, q, candidate).ratio()
            best = max(best, ratio * weight)
        return best

    def best_match(self, query: str) -> tuple[DesktopEntry | None, float, list[str]]:
        resolved = _ALIASES.get(query.lower(), query)
        scored = sorted(
            ((self._score(entry, resolved), entry) for entry in self._entries()
             if not entry.no_display or resolved in entry.app_id),
            key=lambda pair: pair[0],
            reverse=True,
        )
        suggestions = [e.name for s, e in scored[:3] if s > 0.3 and e.name]
        top = scored[0] if scored else (0.0, None)
        if top[0] >= 0.72:
            return top[1], top[0], suggestions
        return None, top[0], suggestions

    # -- intent ----------------------------------------------------------
    def can_handle(self, nlu: NLUResult) -> float:
        if nlu.intent == "open_app" and nlu.entities.get("app"):
            return 0.97
        return 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        query = (nlu.entities.get("app") or "").strip()
        if not query:
            return None
        entry, score, suggestions = self.best_match(query)
        if entry is None:
            hint = f" Did you mean {', '.join(suggestions)}?" if suggestions else ""
            # Not found locally → let the agent figure it out (maybe flatpak install).
            if self.app is not None:
                return self.app.escalate_how_to(f"open or install the app '{query}' on Fedora")
            return Reply(
                speak=f"I couldn't find {query} on this computer.{hint}",
                display=f"❌ No match for “{query}”.{hint}",
            )
        if self._launch(entry):
            return Reply(
                speak=f"Opening {entry.name}.",
                display=f"🚀 Launched **{entry.name}** ({entry.app_id})\n"
                        f"   score={score:.2f}",
                data={"app": entry.name},
            )
        return Reply(
            speak=f"{entry.name} is installed but failed to launch.",
            display=f"⚠️ Launch failed for {entry.app_id}: {entry.exec_line}",
        )

    def _launch(self, entry: DesktopEntry) -> bool:
        # gio launch works under GNOME on both X11 and Wayland.
        if shutil.which("gio"):
            try:
                proc = subprocess.run(
                    ["gio", "launch", str(entry.path)],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    return True
                log.warning("gio launch failed: %s", proc.stderr.strip())
            except (subprocess.SubprocessError, OSError) as exc:
                log.warning("gio launch error: %s", exc)
        if shutil.which("gtk-launch"):
            try:
                subprocess.Popen(
                    ["gtk-launch", entry.app_id],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            except OSError as exc:
                log.warning("gtk-launch error: %s", exc)
        if entry.exec_line:
            try:
                cmd = re.sub(r"%[fFuUdDkKcCiIm]", "", entry.exec_line).strip()
                subprocess.Popen(
                    cmd, shell=True, start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            except OSError as exc:
                log.warning("exec fallback error: %s", exc)
        return False
