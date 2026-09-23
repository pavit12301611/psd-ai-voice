"""Configuration loading for the PSD Voice Assistant."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

# Repository root (parent of the assistant/ package).
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "config.yaml"

_DEEP = copy.deepcopy


def _deep_merge(base: dict, override: dict) -> dict:
    out = _DEEP(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_path(value: str | Path) -> Path:
    """Resolve a config path against the repository root unless absolute."""
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = ROOT / path
    return path


class Config:
    """Dict wrapper with dotted access: cfg['agent.port'] and cfg.get(...)."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.data = data
        self.path = path or DEFAULT_CONFIG_PATH

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
        data: dict[str, Any] = {}
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        return cls(data, cfg_path)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path_of(self, dotted: str, default: Any = None) -> Path:
        return resolve_path(self.get(dotted, default))

    def __getitem__(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel:
            raise KeyError(dotted)
        return value

    def merged(self, overrides: dict[str, Any]) -> "Config":
        return Config(_deep_merge(self.data, overrides), self.path)


def ensure_runtime_dirs(cfg: Config) -> None:
    """Create every directory the assistant needs at runtime."""
    dirs = [
        cfg.path_of("knowledge.path").parent,
        cfg.path_of("calendar.db").parent,
        cfg.path_of("calendar.ics_dir"),
        cfg.path_of("notes.path").parent,
        cfg.path_of("agent.inbox_dir"),
        cfg.path_of("agent.outbox_dir"),
        cfg.path_of("agent.status_file").parent,
        cfg.path_of("logging.file").parent,
        cfg.path_of("stt.model_path").parent,
    ]
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
