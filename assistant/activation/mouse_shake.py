"""Cursor-shake activation.

Backends
--------
* pynput — X11 (and XWayland) sessions; uses the Python input library.
* evdev  — native Wayland sessions on Fedora; reads /dev/input directly.
           Requires the user to be in the `input` group (run.sh offers this).

Both feed the same direction-reversal detector.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from assistant.logger import get_logger

log = get_logger("shake")


class ShakeDetector:
    """Counts direction reversals of cursor travel inside a rolling window."""

    def __init__(
        self,
        direction_changes: int = 4,
        window_seconds: float = 0.7,
        min_travel: float = 100.0,
        cooldown_seconds: float = 1.5,
        on_shake: Callable[[], None] | None = None,
    ):
        self.direction_changes = max(2, int(direction_changes))
        self.window_seconds = window_seconds
        self.min_travel = min_travel
        self.cooldown = cooldown_seconds
        self.on_shake = on_shake

        self._events: list[tuple[float, float, int]] = []  # (t, delta, sign)
        self._last_fired = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def feed(self, dx: float, dy: float) -> None:
        """Feed one mouse movement (dx, dy)."""
        # Track horizontal primarily; use dominant axis so diagonal wiggles count.
        delta = abs(dx)
        sign = 1 if dx > 0 else (-1 if dx < 0 else 0)
        if abs(dy) > abs(dx):
            delta = abs(dy)
            sign = 1 if dy > 0 else (-1 if dy < 0 else 0)
        if sign == 0 or delta < 0.5:
            return

        now = time.monotonic()
        with self._lock:
            self._events.append((now, delta, sign))
            cutoff = now - self.window_seconds
            while self._events and self._events[0][0] < cutoff:
                self._events.pop(0)

            if len(self._events) < self.direction_changes:
                return
            travel = sum(e[1] for e in self._events)
            if travel < self.min_travel:
                return
            # count sign flips over the window
            signs = [e[2] for e in self._events]
            flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
            if flips + 1 >= self.direction_changes:
                if now - self._last_fired >= self.cooldown:
                    self._last_fired = now
                    self._events.clear()
                    callback = self.on_shake
                else:
                    return
                if callback:
                    threading.Thread(
                        target=self._safe_call, args=(callback,), daemon=True
                    ).start()

    @staticmethod
    def _safe_call(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:  # noqa: BLE001
            log.exception("shake callback failed")


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def pick_backend(preferred: str = "auto") -> str:
    if preferred in ("pynput", "evdev"):
        return preferred
    # Wayland detection
    import os

    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or \
       os.environ.get("WAYLAND_DISPLAY"):
        if _evdev_available():
            return "evdev"
    return "pynput"


def _evdev_available() -> bool:
    try:
        import evdev  # noqa: F401
        from pathlib import Path

        return any(Path("/dev/input").glob("event*"))
    except Exception:  # noqa: BLE001
        return False


class EvdevInputMonitor:
    """Reads mouse (and optionally keyboard) events on Wayland/native."""

    def __init__(self, detector: ShakeDetector,
                 hotkey_keys: list[str] | None = None,
                 on_hotkey: Callable[[], None] | None = None):
        import evdev
        from evdev import ecodes

        self._evdev = evdev
        self._ecodes = ecodes
        self.detector = detector
        self.hotkey_keys = [k.lower() for k in (hotkey_keys or [])]
        self.on_hotkey = on_hotkey
        self._pressed: set[int] = set()
        self._hotkey_codes: set[int] = set()
        for name in self.hotkey_keys:
            code = getattr(ecodes, f"KEY_{name.upper()}", None)
            if code is None and name == "ctrl":
                code = ecodes.KEY_LEFTCTRL
            if code is not None:
                self._hotkey_codes.add(code)
        self._mice: list = []
        self._keyboards: list = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _discover(self) -> None:
        from pathlib import Path

        mice, keyboards = [], []
        for path in sorted(Path("/dev/input").glob("event*")):
            try:
                dev = self._evdev.InputDevice(str(path))
            except (PermissionError, OSError):
                continue
            caps = dev.capabilities()
            rel = caps.get(self._evdev.ecodes.EV_REL, [])
            keys = caps.get(self._evdev.ecodes.EV_KEY, [])
            if self._evdev.ecodes.REL_X in rel and self._evdev.ecodes.BTN_LEFT in keys:
                mice.append(dev)
            elif self._hotkey_codes and any(k in keys for k in self._hotkey_codes):
                keyboards.append(dev)
            else:
                dev.close()
        self._mice, self._keyboards = mice, keyboards
        log.info("evdev: %d mouse device(s), %d keyboard device(s)",
                 len(mice), len(keyboards))
        if not mice:
            log.warning(
                "no mouse devices readable — add your user to the 'input' group "
                "(run.sh can do this) or use the HUD Talk button"
            )

    def start(self) -> None:
        self._discover()
        self._thread = threading.Thread(target=self._run, name="evdev", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from select import select

        devices = self._mice + self._keyboards
        if not devices:
            log.error("evdev monitor: no devices — shake activation unavailable")
            return
        while not self._stop.is_set():
            r, _, _ = select(devices, [], [], 0.5)
            for dev in r:
                try:
                    for event in dev.read():
                        self._handle(dev, event)
                except (OSError, BlockingIOError):
                    continue

    def _handle(self, dev, event) -> None:  # noqa: ANN001
        et = self._evdev.ecodes
        if dev in self._mice:
            if event.type == et.EV_REL:
                if event.code == et.REL_X:
                    self.detector.feed(event.value, 0)
                elif event.code == et.REL_Y:
                    self.detector.feed(0, event.value)
        elif event.type == et.EV_KEY:
            code = event.value  # 1=press 0=release
            if event.code in self._hotkey_codes:
                if code == 1:
                    self._pressed.add(event.code)
                    if self._hotkey_codes and self._hotkey_codes <= self._pressed:
                        if self.on_hotkey:
                            try:
                                self.on_hotkey()
                            except Exception:  # noqa: BLE001
                                log.exception("hotkey callback failed")
                elif code == 0:
                    self._pressed.discard(event.code)

    def stop(self) -> None:
        self._stop.set()
        for dev in self._mice + self._keyboards:
            try:
                dev.close()
            except OSError:
                pass


class PynputInputMonitor:
    """Mouse shake (+ optional keyboard hotkey) via pynput on X11."""

    def __init__(self, detector: ShakeDetector,
                 hotkey_keys: list[str] | None = None,
                 on_hotkey: Callable[[], None] | None = None):
        self.detector = detector
        self.hotkey_keys = hotkey_keys or []
        self.on_hotkey = on_hotkey
        self._mouse_listener = None
        self._hotkey_listener = None

    def start(self) -> None:
        from pynput import mouse

        self._mouse_listener = mouse.Listener(on_move=self._on_move)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()
        log.info("pynput mouse monitor started")

        if self.hotkey_keys and self.on_hotkey:
            try:
                from pynput import keyboard

                combo = {
                    getattr(keyboard.Key, k, None) or getattr(keyboard.KeyCode, k, None)
                    for k in self.hotkey_keys
                }
                combo.discard(None)
                # normalise aliases like ctrl → Key.ctrl
                normalised = set()
                for k in self.hotkey_keys:
                    for candidate in (k, k.replace("ctrl", "ctrl")):
                        attr = {
                            "ctrl": "ctrl", "alt": "alt", "shift": "shift",
                            "cmd": "cmd", "super": "cmd", "v": None,
                        }.get(candidate)
                        if attr:
                            normalised.add(getattr(keyboard.Key, attr, None))
                        else:
                            normalised.add(keyboard.KeyCode.from_char(candidate))
                normalised.discard(None)

                pressed: set = set()

                def on_press(key):
                    pressed.add(key)
                    if normalised and normalised <= pressed:
                        try:
                            self.on_hotkey()
                        except Exception:  # noqa: BLE001
                            log.exception("hotkey failed")

                def on_release(key):
                    pressed.discard(key)

                self._hotkey_listener = keyboard.Listener(
                    on_press=on_press, on_release=on_release
                )
                self._hotkey_listener.daemon = True
                self._hotkey_listener.start()
            except Exception as exc:  # noqa: BLE001
                log.warning("hotkey listener failed: %s", exc)

    def _on_move(self, x, y, dx, dy) -> None:  # noqa: ANN001
        self.detector.feed(dx, dy)

    def stop(self) -> None:
        for listener in (self._mouse_listener, self._hotkey_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:  # noqa: BLE001
                    pass


def start_input_monitor(cfg, detector: ShakeDetector,
                        on_hotkey: Callable[[], None] | None = None):
    """Create + start the right monitor for this session. Returns monitor."""
    if not cfg.get("activation.shake.enabled", True) and \
            not cfg.get("activation.hotkey.enabled", True):
        log.info("input monitors disabled in config")
        return None

    hotkey_keys = cfg.get("activation.hotkey.keys", ["ctrl", "alt", "v"]) \
        if cfg.get("activation.hotkey.enabled", True) else []
    backend = pick_backend(cfg.get("activation.shake.backend", "auto"))
    log.info("input backend: %s", backend)

    if backend == "evdev":
        try:
            monitor = EvdevInputMonitor(detector, hotkey_keys, on_hotkey)
            monitor.start()
            return monitor
        except Exception as exc:  # noqa: BLE001
            log.warning("evdev backend failed (%s); trying pynput", exc)

    try:
        monitor = PynputInputMonitor(detector, hotkey_keys, on_hotkey)
        monitor.start()
        return monitor
    except Exception as exc:  # noqa: BLE001
        log.warning("pynput backend failed: %s", exc)
        log.info("hint: use the HUD Talk button to activate listening")
        return None
