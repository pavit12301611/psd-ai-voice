"""Cursor-shake detector logic (pure — no input devices needed)."""

import time

from assistant.activation.mouse_shake import ShakeDetector


def test_shake_triggers_callback():
    fired = []
    det = ShakeDetector(
        direction_changes=4,
        window_seconds=2.0,
        min_travel=50,
        cooldown_seconds=0.0,
        on_shake=lambda: fired.append(1),
    )
    # rapid left-right-left-right wiggle
    for dx in (20, -20, 20, -20, 20, -20):
        det.feed(dx, 0)
    # callback runs in a thread — give it a moment
    for _ in range(50):
        if fired:
            break
        time.sleep(0.02)
    assert fired, "expected shake callback to fire"


def test_slow_drift_does_not_trigger():
    fired = []
    det = ShakeDetector(
        direction_changes=4,
        window_seconds=0.7,
        min_travel=100,
        cooldown_seconds=0.0,
        on_shake=lambda: fired.append(1),
    )
    # steady rightward scroll — no reversals
    for _ in range(30):
        det.feed(30, 0)
        time.sleep(0.001)
    time.sleep(0.05)
    assert not fired


def test_small_wiggle_below_travel_threshold():
    fired = []
    det = ShakeDetector(
        direction_changes=4,
        window_seconds=2.0,
        min_travel=1000,  # huge travel requirement
        cooldown_seconds=0.0,
        on_shake=lambda: fired.append(1),
    )
    for dx in (2, -2, 2, -2, 2, -2):
        det.feed(dx, 0)
    time.sleep(0.05)
    assert not fired


def test_cooldown_prevents_double_fire():
    fired = []
    det = ShakeDetector(
        direction_changes=4,
        window_seconds=2.0,
        min_travel=50,
        cooldown_seconds=5.0,
        on_shake=lambda: fired.append(1),
    )
    for dx in (20, -20, 20, -20, 20, -20):
        det.feed(dx, 0)
    for _ in range(50):
        if fired:
            break
        time.sleep(0.02)
    # immediate second shake within cooldown → ignored
    for dx in (20, -20, 20, -20, 20, -20):
        det.feed(dx, 0)
    time.sleep(0.1)
    assert len(fired) == 1


def test_vertical_shake_also_counts():
    fired = []
    det = ShakeDetector(
        direction_changes=4, window_seconds=2.0, min_travel=50,
        cooldown_seconds=0.0, on_shake=lambda: fired.append(1),
    )
    for dy in (25, -25, 25, -25, 25, -25):
        det.feed(0, dy)
    for _ in range(50):
        if fired:
            break
        time.sleep(0.02)
    assert fired
