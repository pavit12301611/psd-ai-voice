#!/usr/bin/env python3
"""Standalone Agent Mode bridge server.

Normally the bridge runs inside the assistant process. This script exists
so the bridge can also run on its own (e.g. while the assistant HUD is
closed but the user still wants an agent endpoint):

    python3 agent_server/server.py            # defaults from config.yaml
    python3 agent_server/server.py --port 8765
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.agent.bridge import AgentBridge  # noqa: E402
from assistant.config import Config, ensure_runtime_dirs  # noqa: E402
from assistant.logger import setup_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="PSD Agent Mode bridge server")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = Config.load(args.config)
    ensure_runtime_dirs(cfg)
    setup_logging(
        cfg.get("logging.level", "INFO"),
        cfg.path_of("logging.file"),
    )

    bridge = AgentBridge(
        host=args.host or cfg.get("agent.host", "127.0.0.1"),
        port=args.port or int(cfg.get("agent.port", 8765)),
        inbox_dir=cfg.path_of("agent.inbox_dir"),
        outbox_dir=cfg.path_of("agent.outbox_dir"),
        status_file=cfg.path_of("agent.status_file"),
    )
    bridge.start()
    print(f"Agent bridge ready → {bridge.base_url}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down.")
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
