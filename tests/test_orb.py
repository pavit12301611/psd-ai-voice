"""AI orb — pure maths (spring, palette, smoothing) + light-shake profile.

GTK is NOT imported here: the orb module keeps all gi imports lazy so the
maths stays unit-testable headlessly.
"""

import math

import pytest

from assistant.activation.mouse_shake import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DIRECTION_CHANGES,
    DEFAULT_MIN_TRAVEL_PX,
    DEFAULT_WINDOW_SECONDS,
    ShakeDetector,
)
from assistant.ui.orb import (
    GOOGLE_AI_PALETTE,
    Spring2D,
    sample_palette,
    smooth_step,
)


# ---------------------------------------------------------------------------
# Spring follow — must converge smoothly, no NaNs, minimal overshoot
# ---------------------------------------------------------------------------
def test_spring_converges_to_cursor():
    s = Spring2D(0.0, 0.0, omega=14.0, zeta=0.85)
    tx, ty = 800.0, 450.0
    dt = 1 / 60.0
    for _ in range(int(2.0 / dt)):  # 2 seconds
        s.step(tx, ty, dt)
    assert math.isfinite(s.x) and math.isfinite(s.y)
    assert abs(s.x - tx) < 2.0
    assert abs(s.y - ty) < 2.0


def test_spring_no_exploding_on_large_dt():
    s = Spring2D(100.0, 100.0)
    s.step(900.0, 500.0, dt=0.5)   # clamped internally
    assert math.isfinite(s.x) and math.isfinite(s.y)


def test_spring_overshoot_is_small():
    """zeta=0.85 → slight, pleasant lag; never wild oscillation."""
    s = Spring2D(0.0, 0.0, omega=14.0, zeta=0.85)
    tx, ty, dt = 500.0, 0.0, 1 / 60.0
    max_x = 0.0
    for _ in range(int(1.5 / dt)):
        s.step(tx, ty, dt)
        max_x = max(max_x, s.x)
    assert max_x < tx * 1.15  # ≤15% overshoot


# ---------------------------------------------------------------------------
# Google AI palette — seamless loop, sane colours
# ---------------------------------------------------------------------------
def test_palette_wraps_seamlessly():
    a = sample_palette(0.0)
    b = sample_palette(1.0)
    for i in range(3):
        assert a[i] == pytest.approx(b[i], abs=1e-6)


def test_palette_values_in_range():
    for u in [i / 32 for i in range(32)]:
        r, g, b = sample_palette(u)
        assert 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0


def test_palette_has_google_brands():
    # first stop ≈ Google blue #4285F4
    r, g, b = GOOGLE_AI_PALETTE[0]
    assert r == pytest.approx(0x42 / 255, abs=0.01)
    assert g == pytest.approx(0x85 / 255, abs=0.01)
    assert b == pytest.approx(0xF4 / 255, abs=0.01)


def test_smooth_step_approaches_target():
    cur = {"scale": 1.0, "flow": 0.0}
    tgt = {"scale": 1.2, "flow": 2.0}
    for _ in range(120):  # 2 s at 60 fps
        cur = smooth_step(cur, tgt, dt=1 / 60)
    assert cur["scale"] == pytest.approx(1.2, abs=1e-3)
    assert cur["flow"] == pytest.approx(2.0, abs=1e-3)


def test_smooth_step_first_step_is_gentle():
    """One 60fps frame covers only a small slice of the jump — no popping."""
    cur = {"scale": 1.0}
    nxt = smooth_step(cur, {"scale": 2.0}, dt=1 / 60)
    moved = nxt["scale"] - 1.0
    assert 0.0 < moved < 0.16 * 1.0  # ≤16% of the full delta per frame


# ---------------------------------------------------------------------------
# Light shake profile — the whole point: a GENTLE wiggle must trigger
# ---------------------------------------------------------------------------
def test_light_shake_fires_with_default_profile():
    """~5px flicks, 3 reversals, under 1s — the everyday light shake."""
    fired = []
    det = ShakeDetector(on_shake=lambda: fired.append(1))
    # exactly the default "light" profile
    assert DEFAULT_DIRECTION_CHANGES == 3
    assert DEFAULT_MIN_TRAVEL_PX <= 24
    assert DEFAULT_WINDOW_SECONDS >= 0.9
    assert DEFAULT_COOLDOWN_SECONDS <= 1.0

    for dx in (6, -6, 6):          # three tiny flicks, two reversals
        det.feed(dx, 0)
    for _ in range(60):
        if fired:
            break
        import time
        time.sleep(0.02)
    assert fired, "a light 5-8px shake must trigger listening"


def test_diagonal_light_shake_counts_both_axes():
    fired = []
    det = ShakeDetector(on_shake=lambda: fired.append(1))
    # gentle diagonal wobble: each event tiny on one axis, travel sums both
    for dx, dy in ((4, 4), (-4, -4), (4, 4)):
        det.feed(dx, dy)
    for _ in range(60):
        if fired:
            break
        import time
        time.sleep(0.02)
    assert fired


def test_unidirectional_scroll_never_triggers():
    fired = []
    det = ShakeDetector(on_shake=lambda: fired.append(1))
    for _ in range(60):
        det.feed(8, 0)   # pure rightward scroll
        import time
        time.sleep(0.005)
    import time
    time.sleep(0.1)
    assert not fired


def test_on_move_callback_receives_raw_deltas():
    seen = []
    det = ShakeDetector(on_move=lambda dx, dy: seen.append((dx, dy)))
    det.feed(3.0, -2.0)
    det.feed(0.1, 0.0)   # below deadzone but still tracked for the orb
    assert seen == [(3.0, -2.0), (0.1, 0.0)]


def test_subpixel_jitter_ignored_for_shake():
    fired = []
    det = ShakeDetector(on_shake=lambda: fired.append(1))
    for _ in range(50):
        det.feed(0.3, -0.3)   # hand tremor / sub-pixel noise
    import time
    time.sleep(0.1)
    assert not fired
