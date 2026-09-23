"""Desktop notifications (notify-send) + a clean env for launching GUI apps.

The orb itself shows NO text — agent answers and work-status changes are
displayed via standard desktop notifications instead.

`clean_env()` strips GDK_BACKEND so apps we launch never inherit the orb's
XWayland preference (they should use native Wayland when available).
"""

from __future__ import annotations

import os
import shutil
import subprocess

from assistant.logger import get_logger

log = get_logger("notify")


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GDK_BACKEND", None)
    return env


def notify(title: str, body: str, urgency: str = "normal",
           timeout_ms: int = 8000) -> None:
    """Best-effort desktop notification; never raises."""
    if not shutil.which("notify-send"):
        return
    title = (title or "PSD Voice").strip()[:120]
    body = (body or "").strip()
    if not body:
        return
    # keep notifications readable
    body = body if len(body) <= 600 else body[:597] + "…"
    try:
        subprocess.Popen(
            [
                "notify-send",
                "--app-name=PSD Voice",
                f"--urgency={urgency}",
                f"--expire-time={int(timeout_ms)}",
                title,
                body,
            ],
            env=clean_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log.debug("notify-send failed: %s", exc)
