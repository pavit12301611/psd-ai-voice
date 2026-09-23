"""Google-Gemini-style AI orb — the ONLY window: a small circle, zero text.

Design goals (per the brief):
  * small circular, frameless, fully transparent window
  * flowing "Google AI" gradient (blue → violet → rose → aqua), no text ever
  * spring-follows the cursor with a soft organic lag (it feels alive)
  * completely click-through — it never blocks the app underneath
  * every state is expressed purely through motion / colour / shape:

        READY      slow breathing, soft flowing gradient, orbiting sparkle
        LISTENING  grows, fast flow, ripple rings, waveform edge wobble
        THINKING   swirling vortex + comet dot
        SPEAKING   rhythmic pulses, as if the orb itself is talking
        ANSWER/POP one bright burst when an Agent answer lands

  Runs on GTK3 + cairo via XWayland so free positioning works on GNOME
  Wayland.  All gi/GTK imports are lazy so this module stays importable
  (and unit-testable) without a display server.
"""

from __future__ import annotations

import math
import os
import time
from typing import Callable

from assistant.logger import get_logger

log = get_logger("orb")

# ---------------------------------------------------------------------------
# Pure maths (no GTK) — unit-testable
# ---------------------------------------------------------------------------

# Google's AI / Gemini-ish palette (sRGB 0-1), wraps around seamlessly.
GOOGLE_AI_PALETTE: list[tuple[float, float, float]] = [
    (0.259, 0.522, 0.957),  # #4285F4 Google blue
    (0.612, 0.447, 0.796),  # #9B72CB violet
    (0.851, 0.396, 0.439),  # #D96570 rose
    (0.961, 0.608, 0.357),  # #F59B5B warm
    (0.141, 0.757, 0.878),  # #24C1E0 aqua
    (0.259, 0.522, 0.957),  # wrap → blue
]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_rgb(a: tuple[float, float, float],
             b: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t))


def sample_palette(u: float, palette=None) -> tuple[float, float, float]:
    """Sample the looping gradient at position u ∈ [0, 1)."""
    pal = palette or GOOGLE_AI_PALETTE
    u = u % 1.0
    n = len(pal) - 1  # last stop duplicates first
    pos = u * n
    i = int(pos)
    frac = pos - i
    return lerp_rgb(pal[i], pal[min(i + 1, n)], frac)


class Spring2D:
    """Under-damped spring — the orb chases the cursor with a soft lag."""

    def __init__(self, x: float = 0.0, y: float = 0.0,
                 omega: float = 14.0, zeta: float = 0.85):
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.omega = float(omega)
        self.zeta = float(zeta)

    def step(self, tx: float, ty: float, dt: float) -> None:
        dt = min(max(dt, 0.001), 0.05)
        w, z = self.omega, self.zeta
        # x
        ax = w * w * (tx - self.x) - 2 * z * w * self.vx
        self.vx += ax * dt
        self.x += self.vx * dt
        # y
        ay = w * w * (ty - self.y) - 2 * z * w * self.vy
        self.vy += ay * dt
        self.y += self.vy * dt

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)


class CursorTracker:
    """Fallback cursor position via integrated mouse deltas (evdev path).

    Used when the X11 pointer query is unavailable; always updated with raw
    deltas from the input monitor so it stays roughly correct.
    """

    def __init__(self, width: int = 1920, height: int = 1080):
        self.x = width / 2.0
        self.y = height / 2.0
        self.valid = False
        self.width = width
        self.height = height

    def update(self, dx: float, dy: float) -> None:
        self.x = min(max(self.x + dx, 0.0), float(self.width))
        self.y = min(max(self.y + dy, 0.0), float(self.height))
        self.valid = True

    def set_screen(self, width: int, height: int) -> None:
        self.width = max(1, width)
        self.height = max(1, height)

    def position(self) -> tuple[float, float]:
        return self.x, self.y


# Visual target parameters per logical state (smoothly interpolated at runtime).
_STATE_TARGETS: dict[str, dict[str, float]] = {
    "ready":     {"scale": 1.00, "flow": 0.45, "ripple": 0.0, "wobble": 0.0,
                  "swirl": 0.15, "glow": 0.55, "pulse": 0.0},
    "listening": {"scale": 1.16, "flow": 1.60, "ripple": 1.0, "wobble": 1.0,
                  "swirl": 0.40, "glow": 0.85, "pulse": 0.0},
    "thinking":  {"scale": 1.06, "flow": 2.40, "ripple": 0.25, "wobble": 0.25,
                  "swirl": 1.00, "glow": 0.75, "pulse": 0.0},
    "speaking":  {"scale": 1.05, "flow": 1.30, "ripple": 0.45, "wobble": 0.35,
                  "swirl": 0.30, "glow": 0.90, "pulse": 1.0},
}


def smooth_step(current: dict, target: dict, dt: float, rate: float = 9.0) -> dict:
    """Exponential approach — makes every state change feel buttery."""
    k = 1.0 - math.exp(-rate * max(dt, 0.0))
    out = {}
    for key, tgt in target.items():
        out[key] = lerp(current.get(key, tgt), tgt, k)
    return out


# ---------------------------------------------------------------------------
# GTK layer (lazy import)
# ---------------------------------------------------------------------------

def _gtk():
    """Import GTK3 through XWayland so the popup can be freely positioned."""
    # Must happen before gi.repository.Gdk loads: on GNOME Wayland the orb
    # needs XWayland for absolute move().
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or \
            os.environ.get("WAYLAND_DISPLAY"):
        if not os.environ.get("GDK_BACKEND"):
            os.environ["GDK_BACKEND"] = "x11"

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    import cairo  # pycairo
    return Gtk, Gdk, GLib, cairo


class OrbUI:
    """The orb itself. Public API mirrors what the assistant already calls."""

    def __init__(
        self,
        size: int = 132,
        opacity: float = 0.97,
        omega: float = 14.0,
        zeta: float = 0.85,
        tracker: CursorTracker | None = None,
        on_quit: Callable[[], None] | None = None,
    ):
        self.size = int(size)
        self.opacity = float(opacity)
        self.tracker = tracker or CursorTracker()
        self.on_quit = on_quit

        self.spring = Spring2D(self.tracker.x, self.tracker.y, omega, zeta)
        self.state = "READY"
        self.agent_state = "idle"
        self._last_answer: str = ""
        self._params: dict[str, float] = dict(_STATE_TARGETS["ready"])
        self._t = 0.0
        self._burst = 0.0            # 0..1 answer-received flash
        self._ripples: list[float] = []
        self._next_ripple = 0.0
        self._running = False
        self._last_tick = 0.0
        self._last_draw_pos: tuple[int, int] | None = None

        # lazy GTK
        self.Gtk = self.Gdk = self.GLib = self.cairo = None
        self.win = None
        self._tick_id = None

    # -- lifecycle ----------------------------------------------------
    def build(self) -> None:
        Gtk, Gdk, GLib, cairo = _gtk()
        self.Gtk, self.Gdk, self.GLib, self.cairo = Gtk, Gdk, GLib, cairo

        win = Gtk.Window(type=Gtk.WindowType.POPUP)  # override-redirect:
        win.set_app_paintable(True)                  # no chrome, always on top
        screen = Gdk.Display.get_default().get_default_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            win.set_visual(visual)
        win.set_size_request(self.size, self.size)

        area = Gtk.DrawingArea()
        area.set_size_request(self.size, self.size)
        area.connect("draw", self._on_draw)
        win.add(area)
        win.show_all()

        # Fully click-through: the cursor keeps working underneath us.
        try:
            win.input_shape_combine_region(cairo.Region())
        except Exception as exc:  # noqa: BLE001
            log.debug("input shape failed (still visual-only): %s", exc)

        # Seed position: real pointer if possible, else tracker/centre.
        pos = self._pointer() or self.tracker.position()
        self.spring.x, self.spring.y = pos
        self._place(self.spring.x, self.spring.y)

        self.win = win
        self._last_tick = time.monotonic()
        self._tick_id = GLib.timeout_add(16, self._tick)  # ~60 fps
        log.info("orb online (%dpx, click-through, follows cursor)", self.size)

    def run(self) -> None:
        """Blocking GTK main loop."""
        if self.win is None:
            self.build()
        self._running = True
        self.Gtk.main()
        self._running = False

    def quit(self) -> None:
        if self.Gtk is None:
            return
        try:
            if self._tick_id is not None:
                self.GLib.source_remove(self._tick_id)
            if self.win is not None:
                self.win.destroy()
            self.Gtk.main_quit()
        except Exception:  # noqa: BLE001
            pass
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:  # noqa: BLE001
                pass

    # -- thread-safe API (called from any thread) ---------------------
    def post(self, fn: Callable[[], None]) -> None:
        if self.GLib is not None:
            self.GLib.idle_add(fn)
        else:
            fn()

    def set_state(self, state: str, hint: str | None = None) -> None:
        del hint  # no text on the orb — ever
        s = (state or "READY").upper()
        log.debug("orb state → %s", s)
        self._set_state_safe(s)

    def _set_state_safe(self, s: str) -> None:
        def apply() -> bool:
            self.state = s
            return False
        self.post(apply)

    def show_transcript(self, text: str) -> None:
        log.info("heard: %s", text)  # text lives in the log, not on screen

    def show_output(self, text: str, title: str | None = None) -> None:
        del title
        self._last_answer = text

    def append_output(self, text: str) -> None:
        log.info("output: %s", text)

    def burst(self) -> None:
        """Answer/agent event → one bright sparkle pop."""
        def apply() -> bool:
            self._burst = 1.0
            return False
        self.post(apply)

    def set_agent(self, label: str) -> None:
        def apply() -> bool:
            label_l = (label or "idle").lower()
            if label_l in ("done", "working", "blocked", "error") and \
                    label_l != self.agent_state:
                self._burst = 1.0   # work started/finished = visual pop
            self.agent_state = label_l
            return False
        self.post(apply)

    def set_status(self, text: str) -> None:
        log.debug("status: %s", text)

    # -- cursor ---------------------------------------------------------
    def _pointer(self) -> tuple[float, float] | None:
        try:
            display = self.Gdk.Display.get_default()
            if display is None:
                return None
            seat = display.get_default_seat()
            pointer = seat.get_pointer() if seat else None
            if pointer is None:
                return None
            _screen, x, y = pointer.get_position()
            if x <= 0 and y <= 0:
                return None
            # sync tracker bounds
            geom = display.get_primary_monitor() if hasattr(display, "get_primary_monitor") else None
            if geom is not None:
                self.tracker.set_screen(geom.get_width() or 1920,
                                        geom.get_height() or 1080)
            self.tracker.x, self.tracker.y = float(x), float(y)
            self.tracker.valid = True
            return float(x), float(y)
        except Exception:  # noqa: BLE001
            return None

    def _place(self, cx: float, cy: float) -> None:
        half = self.size // 2
        x = int(round(cx)) - half
        y = int(round(cy)) - half
        if self._last_draw_pos == (x, y):
            return
        self._last_draw_pos = (x, y)
        try:
            self.win.move(x, y)
        except Exception:  # noqa: BLE001
            pass

    # -- frame ------------------------------------------------------------
    def _tick(self) -> bool:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self._t += dt

        target = self._pointer() or self.tracker.position()
        self.spring.step(target[0], target[1], dt)
        self._place(self.spring.x, self.spring.y)

        # visual state machine → smooth params
        key = self.state.lower()
        if key not in _STATE_TARGETS:
            key = "ready" if key in ("ready", "answer") else key
            if key not in _STATE_TARGETS:
                key = "ready"
        # speaking wins over ready while TTS runs (state set by speaker hook)
        self._params = smooth_step(self._params, _STATE_TARGETS[key], dt)

        # ripples while listening
        if self._params["ripple"] > 0.35 and self._t >= self._next_ripple:
            self._ripples.append(self._t)
            self._next_ripple = self._t + 0.85
        self._ripples = [r for r in self._ripples if self._t - r < 1.0]

        # burst decay
        if self._burst > 0.0:
            self._burst = max(0.0, self._burst - dt * 2.2)

        if self.win is not None:
            self.win.queue_draw()
        return True  # keep the timer alive

    # -- draw ----------------------------------------------------------------
    def _on_draw(self, _widget, cr) -> bool:  # noqa: ANN001
        Gtk, _Gdk, _GLib, cairo = self.Gtk, self.Gdk, self.GLib, self.cairo
        p = self._params
        t = self._t
        W = H = self.size
        cx, cy = W / 2.0, H / 2.0

        # full transparency each frame
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # breathing + speaking pulse + burst
        breathe = 1.0 + 0.045 * math.sin(t * 1.35) if p["pulse"] < 0.5 else \
            1.0 + 0.085 * abs(math.sin(t * math.pi * 3.6))
        burst_k = 1.0 + 0.30 * (self._burst ** 1.5)
        speed_lag = min(self.spring.speed * 0.004, 0.12)  # stretch when moving fast
        scale = p["scale"] * breathe * burst_k + speed_lag
        R = (self.size * 0.36) * scale

        flow = p["flow"]
        angle = t * flow

        # ---- outer glow ------------------------------------------------------
        glow_r = R * (1.55 + 0.15 * math.sin(t * 1.1))
        g = cairo.RadialGradient(cx, cy, R * 0.55, cx, cy, glow_r)
        col = sample_palette(angle / (2 * math.pi))
        g.add_color_stop_rgba(0.0, col[0], col[1], col[2], 0.42 * p["glow"])
        g.add_color_stop_rgba(0.6, col[0], col[1], col[2], 0.16 * p["glow"])
        g.add_color_stop_rgba(1.0, col[0], col[1], col[2], 0.0)
        cr.set_source(g)
        cr.arc(cx, cy, glow_r, 0, 2 * math.pi)
        cr.fill()

        # ---- ripple rings (listening) -----------------------------------------
        for born in self._ripples:
            age = (t - born) / 1.0
            rr = R * (1.05 + age * 1.35)
            alpha = (1.0 - age) * 0.55 * p["ripple"]
            rc = sample_palette(angle / (2 * math.pi) + age * 0.3)
            cr.set_source_rgba(rc[0], rc[1], rc[2], max(alpha, 0.0))
            cr.set_line_width(2.4 * (1.0 - age) + 0.6)
            cr.arc(cx, cy, rr, 0, 2 * math.pi)
            cr.stroke()

        # ---- main disc (wavy edge when listening) ------------------------------
        wob = p["wobble"]
        cr.new_path()
        steps = 140
        for i in range(steps + 1):
            th = (i / steps) * 2 * math.pi
            wave = 0.0
            if wob > 0.02:
                wave = 1.0 + wob * 0.040 * math.sin(7 * th + t * 9.0) + \
                       wob * 0.022 * math.sin(3 * th - t * 6.5)
            rr = R * wave
            x = cx + rr * math.cos(th)
            y = cy + rr * math.sin(th)
            if i == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.close_path()

        grad = cairo.LinearGradient(-R, 0, R, 0)
        matrix = cairo.Matrix.init_rotate(angle)
        grad.transform(matrix)
        n_stops = len(GOOGLE_AI_PALETTE)
        for i, col in enumerate(GOOGLE_AI_PALETTE):
            u = i / (n_stops - 1)
            boost = 1.0 + 0.15 * p["pulse"] + 0.25 * self._burst
            c = (min(col[0] * boost, 1.0), min(col[1] * boost, 1.0),
                 min(col[2] * boost, 1.0))
            grad.add_color_stop_rgba(u, c[0], c[1], c[2], self.opacity)
        cr.set_source(grad)
        cr.fill_preserve()

        # ---- inner depth: soft bottom shade + top-left specular ----------------
        sh = cairo.RadialGradient(cx, cy + R * 0.45, R * 0.1,
                                  cx, cy + R * 0.35, R * 1.05)
        sh.add_color_stop_rgba(0.0, 0.10, 0.06, 0.28, 0.0)
        sh.add_color_stop_rgba(1.0, 0.08, 0.04, 0.22, 0.38)
        cr.set_source(sh)
        cr.fill()

        hl = cairo.RadialGradient(cx - R * 0.34, cy - R * 0.38, R * 0.05,
                                  cx - R * 0.34, cy - R * 0.38, R * 0.85)
        hl.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.42)
        hl.add_color_stop_rgba(0.55, 1.0, 1.0, 1.0, 0.08)
        hl.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.0)
        cr.set_source(hl)
        cr.arc(cx, cy, R, 0, 2 * math.pi)
        cr.fill()

        # ---- thinking comet / swirl indicator ------------------------------------
        if p["swirl"] > 0.3:
            comet_a = t * (2.2 + 3.0 * p["swirl"])
            ox = cx + R * 0.78 * math.cos(comet_a)
            oy = cy + R * 0.78 * math.sin(comet_a)
            cg = cairo.RadialGradient(ox, oy, 0.5, ox, oy, R * 0.28)
            cg.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.95)
            cg.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.0)
            cr.set_source(cg)
            cr.arc(ox, oy, R * 0.28, 0, 2 * math.pi)
            cr.fill()

        # ---- Google sparkle (✦) orbiting the orb ----------------------------------
        spark_a = t * 0.9
        sr = R * (1.18 + 0.06 * math.sin(t * 2.0))
        sx = cx + sr * math.cos(spark_a)
        sy = cy + sr * math.sin(spark_a)
        tw = 0.5 + 0.5 * math.sin(t * 3.1)
        spark_size = R * (0.16 + 0.07 * tw) * (1.0 + self._burst)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.55 + 0.4 * tw)
        cr.move_to(sx, sy - spark_size)
        cr.curve_to(sx + spark_size * 0.14, sy - spark_size * 0.14,
                    sx + spark_size * 0.14, sy - spark_size * 0.14,
                    sx + spark_size, sy)
        cr.curve_to(sx + spark_size * 0.14, sy + spark_size * 0.14,
                    sx + spark_size * 0.14, sy + spark_size * 0.14,
                    sx, sy + spark_size)
        cr.curve_to(sx - spark_size * 0.14, sy + spark_size * 0.14,
                    sx - spark_size * 0.14, sy + spark_size * 0.14,
                    sx - spark_size, sy)
        cr.curve_to(sx - spark_size * 0.14, sy - spark_size * 0.14,
                    sx - spark_size * 0.14, sy - spark_size * 0.14,
                    sx, sy - spark_size)
        cr.close_path()
        cr.fill()

        # ---- answer burst ring -------------------------------------------------------
        if self._burst > 0.01:
            b = self._burst
            br = R * (1.1 + (1.0 - b) * 0.9)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.7 * b)
            cr.set_line_width(3.0 * b)
            cr.arc(cx, cy, br, 0, 2 * math.pi)
            cr.stroke()

        return True


__all__ = [
    "OrbUI", "CursorTracker", "Spring2D",
    "GOOGLE_AI_PALETTE", "sample_palette", "smooth_step", "lerp",
]
