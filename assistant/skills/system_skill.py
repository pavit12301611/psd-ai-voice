"""System control — volume, brightness, screenshot, lock, suspend."""

from __future__ import annotations

import glob
import shutil
import subprocess
from pathlib import Path

from assistant.logger import get_logger
from assistant.skills.base import Skill
from assistant.types import NLUResult, Reply

log = get_logger("system")


def _run(cmd: list[str] | str, timeout: float = 8.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, shell=isinstance(cmd, str), capture_output=True,
            text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, str(exc)


_INTENTS = {
    "volume_up", "volume_down", "volume_set", "mute",
    "brightness_up", "brightness_down", "screenshot", "lock_screen",
}


class SystemSkill(Skill):
    name = "system"

    def can_handle(self, nlu: NLUResult) -> float:
        return 0.95 if nlu.intent in _INTENTS else 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        intent = nlu.intent
        if intent == "volume_up":
            return self._volume("+5%")
        if intent == "volume_down":
            return self._volume("-5%")
        if intent == "volume_set":
            pct = nlu.entities.get("percent")
            if pct is None:
                return Reply(speak="How much volume? Say a number like 40 percent.")
            return self._volume(f"{pct}%")
        if intent == "mute":
            return self._mute(nlu)
        if intent == "brightness_up":
            return self._brightness("+10%")
        if intent == "brightness_down":
            return self._brightness("-10%")
        if intent == "screenshot":
            return self._screenshot()
        if intent == "lock_screen":
            return self._lock_or_sleep(nlu)
        return None

    # ------------------------------------------------------------------
    def _volume(self, delta: str) -> Reply:
        if shutil.which("pactl"):
            code, out = _run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", delta]
            )
            if code == 0:
                _, level = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
                pretty = level.split("/")[-1].strip() if "/" in level else level
                verb = "up" if delta.startswith("+") else (
                    "down" if delta.startswith("-") else f"to {delta}")
                return Reply(
                    speak=f"Volume {verb}.",
                    display=f"🔊 Volume {delta} — {pretty or 'ok'}",
                )
        if shutil.which("wpctl"):
            # PipeWire native fallback: wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+
            factor = delta.replace("%", "")
            code, out = _run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{factor}"]
                if not delta.endswith("%")
                else ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@",
                      f"{'+' if delta.startswith('+') else ''}{factor}"]
            )
            if code == 0:
                return Reply(speak="Volume adjusted.", display=f"🔊 wpctl {delta}")
        return Reply(
            speak="I couldn't reach the audio controls on this system.",
            display="⚠️ Neither pactl nor wpctl worked.",
        )

    def _mute(self, nlu: NLUResult) -> Reply:
        raw = nlu.normalized
        unmute = "unmute" in raw
        state = "unmuted" if unmute else "muted"
        if shutil.which("pactl"):
            code, _ = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@",
                            "0" if unmute else "1"])
            if code == 0:
                return Reply(speak=f"Sound {state}.",
                             display=f"🔊 {state.capitalize()}")
        if shutil.which("wpctl"):
            code, _ = _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@",
                            "0" if unmute else "1"])
            if code == 0:
                return Reply(speak=f"Sound {state}.",
                             display=f"🔊 {state.capitalize()}")
        return Reply(speak="Audio mute control isn't available right now.",
                     display="⚠️ Mute failed.")

    def _brightness(self, delta: str) -> Reply:
        if shutil.which("brightnessctl"):
            code, out = _run(["brightnessctl", "set", delta])
            if code == 0:
                return Reply(speak="Brightness adjusted.",
                             display=f"💡 brightnessctl set {delta}")
        # sysfs fallback (needs write permission — usually the `video` group)
        devices = sorted(glob.glob("/sys/class/backlight/*"))
        for device in devices:
            try:
                max_path = Path(device) / "max_brightness"
                cur_path = Path(device) / "brightness"
                maximum = int(max_path.read_text().strip())
                current = int(cur_path.read_text().strip())
                step = int(maximum * 0.1)
                new = max(step, min(maximum, current + (step if delta.startswith("+") else -step)))
                cur_path.write_text(str(new))
                return Reply(
                    speak="Brightness adjusted.",
                    display=f"💡 {Path(device).name}: {current} → {new}",
                )
            except (OSError, ValueError) as exc:
                log.debug("backlight %s failed: %s", device, exc)
        return Reply(
            speak="I couldn't change brightness — install brightnessctl for that.",
            display="⚠️ Brightness control unavailable (brightnessctl missing).",
        )

    def _screenshot(self) -> Reply:
        target_dir = Path.home() / "Pictures" / "Screenshots"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"screenshot-{__import__('time').strftime('%Y%m%d-%H%M%S')}.png"

        attempts: list[tuple[list[str], Path]] = []
        if shutil.which("gnome-screenshot"):
            attempts.append((["gnome-screenshot", "-f", str(target)], target))
        if shutil.which("spectacle"):
            attempts.append((["spectacle", "-b", "-n", "-o", str(target)], target))
        if shutil.which("grim"):
            attempts.append((["grim", str(target)], target))
        if shutil.which("gnome-screenshot") is None and shutil.which("flameshot"):
            attempts.append((["flameshot", "full", "-p", str(target.parent)], target))

        for cmd, expected in attempts:
            code, out = _run(cmd, timeout=15)
            if code == 0 and (expected.exists() or any(expected_dir_files(target_dir))):
                found = expected if expected.exists() else max(
                    expected_dir_files(target_dir), key=lambda p: p.stat().st_mtime
                )
                return Reply(
                    speak="Screenshot taken.",
                    display=f"📸 Saved: {found}",
                    data={"path": str(found)},
                )

        # GNOME Shell D-Bus fallback (older GNOMEs) / xdg portal last resort
        code, out = _run([
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Shell.Screenshot",
            "--object-path", "/org/gnome/Shell/Screenshot",
            "--method", "org.gnome.Shell.Screenshot.Screenshot",
            "false", "true", str(target),
        ], timeout=15)
        if code == 0 and target.exists():
            return Reply(speak="Screenshot taken.", display=f"📸 Saved: {target}")

        return Reply(
            speak="Screenshot failed — no screenshot tool found. "
                  "Install gnome-screenshot or flameshot.",
            display="⚠️ No working screenshot tool.",
        )

    def _lock_or_sleep(self, nlu: NLUResult) -> Reply:
        text = nlu.normalized
        want_sleep = any(w in text for w in ("sleep", "suspend"))
        if want_sleep:
            for cmd in (["systemctl", "suspend"], ["loginctl", "suspend"]):
                if shutil.which(cmd[0]):
                    code, _ = _run(cmd)
                    if code == 0:
                        return Reply(speak="Suspending now. See you soon.",
                                     display="🌙 System suspending")
        else:
            for cmd in (
                ["loginctl", "lock-session"],
                ["gnome-screensaver-command", "-l"],
            ):
                if shutil.which(cmd[0]):
                    code, _ = _run(cmd)
                    if code == 0:
                        return Reply(speak="Screen locked.",
                                     display="🔒 Session locked")
        return Reply(speak="I couldn't lock the screen.",
                     display="⚠️ lock-session failed")


def expected_dir_files(directory: Path) -> list[Path]:
    try:
        return [p for p in directory.iterdir() if p.suffix == ".png"]
    except OSError:
        return []
