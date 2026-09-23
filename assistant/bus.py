"""Tiny synchronous event bus for cross-thread notifications."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

Handler = Callable[..., None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def emit(self, event: str, **kwargs: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            try:
                handler(**kwargs)
            except Exception:  # noqa: BLE001 — never let a listener kill the bus
                from assistant.logger import get_logger

                get_logger("bus").exception("handler failed for event %s", event)
