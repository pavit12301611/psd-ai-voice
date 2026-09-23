"""Agent Mode bridge — a tiny localhost HTTP server + file inbox/outbox.

Flow
----
1. The assistant crafts a prompt and POSTs it to /inbox
   (also written as data/agent_inbox/<id>.md so an external agent can
   simply watch the folder).
2. The answering agent — the Arena agent session, or an optional LLM
   fallback — POSTs the reply to /outbox.
3. The bridge notifies the assistant, which shows the answer on the HUD
   and speaks it.

Endpoints
---------
GET  /health           → {"ok": true}
GET  /inbox            → pending (unanswered) prompts
POST /inbox            → queue a prompt
GET  /outbox           → all answers
POST /outbox           → submit an answer {id, answer, source?}
GET  /status           → current agent work status
POST /status           → update agent work status
GET  /events           → long-poll for new outbox/status events (25 s)
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from assistant.logger import get_logger

log = get_logger("bridge")


class BridgeState:
    def __init__(self, inbox_dir: Path, outbox_dir: Path, status_file: Path):
        self.inbox_dir = inbox_dir
        self.outbox_dir = outbox_dir
        self.status_file = status_file
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.answered_ids: set[str] = set()
        for path in outbox_dir.glob("*.json"):
            try:
                self.answered_ids.add(json.loads(path.read_text())["id"])
            except (json.JSONDecodeError, OSError, KeyError):
                pass
        # event log for long-polling: {"kind": "answer"|"status", "payload": ...}
        self.events: list[dict[str, Any]] = []
        self.event_cv = threading.Condition()
        self.on_answer: Callable[[dict], None] | None = None
        self.on_status: Callable[[dict], None] | None = None

    # -- inbox ---------------------------------------------------------
    def add_prompt(self, prompt: dict) -> dict:
        with self.lock:
            pid = prompt["id"]
            path = self.inbox_dir / f"{pid}.json"
            path.write_text(json.dumps(prompt, indent=2), encoding="utf-8")
            # also a readable markdown version for humans/agents watching the folder
            md = self.inbox_dir / f"{pid}.md"
            md.write_text(
                f"<!-- id: {pid} -->\n{prompt.get('body', '')}\n",
                encoding="utf-8",
            )
        log.info("queued prompt %s: %s", pid, prompt.get("title"))
        return {"ok": True, "id": pid}

    def list_inbox(self) -> list[dict]:
        out = []
        for path in sorted(self.inbox_dir.glob("*.json")):
            try:
                prompt = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            prompt["answered"] = prompt.get("id") in self.answered_ids
            out.append(prompt)
        return out

    # -- outbox ----------------------------------------------------------
    def add_answer(self, answer: dict) -> dict:
        pid = str(answer.get("id") or "").strip()
        if not pid:
            return {"ok": False, "error": "missing id"}
        answer.setdefault("source", "agent")
        answer.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with self.lock:
            self.answered_ids.add(pid)
            (self.outbox_dir / f"{pid}.json").write_text(
                json.dumps(answer, indent=2), encoding="utf-8"
            )
            (self.outbox_dir / f"{pid}.md").write_text(
                answer.get("answer", ""), encoding="utf-8"
            )
        log.info("answer received for %s (source=%s)", pid, answer.get("source"))
        self._push_event("answer", answer)
        callback = self.on_answer
        if callback:
            threading.Thread(
                target=lambda: self._safe(callback, answer), daemon=True
            ).start()
        return {"ok": True, "id": pid}

    def list_outbox(self) -> list[dict]:
        out = []
        for path in sorted(self.outbox_dir.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return out

    def answer_for(self, pid: str) -> dict | None:
        path = self.outbox_dir / f"{pid}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    # -- status ------------------------------------------------------------
    def set_status(self, status: dict) -> dict:
        status.setdefault("updated_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with self.lock:
            self.status_file.write_text(
                json.dumps(status, indent=2), encoding="utf-8"
            )
        log.info("status → %s (%s)", status.get("state"), status.get("task", ""))
        self._push_event("status", status)
        callback = self.on_status
        if callback:
            threading.Thread(
                target=lambda: self._safe(callback, status), daemon=True
            ).start()
        return {"ok": True, "status": status}

    def get_status(self) -> dict:
        if self.status_file.exists():
            try:
                return json.loads(self.status_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"state": "idle", "task": "", "detail": "", "updated_at": None}

    # -- events ------------------------------------------------------------
    def _push_event(self, kind: str, payload: dict) -> None:
        with self.event_cv:
            self.events.append({"kind": kind, "payload": payload,
                                "ts": time.time()})
            if len(self.events) > 200:
                self.events = self.events[-200:]
            self.event_cv.notify_all()

    def wait_events(self, since_ts: float, timeout: float = 25.0) -> list[dict]:
        deadline = time.time() + timeout
        with self.event_cv:
            while time.time() < deadline:
                fresh = [e for e in self.events if e["ts"] > since_ts]
                if fresh:
                    return fresh
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self.event_cv.wait(timeout=remaining)
        return []

    @staticmethod
    def _safe(callback: Callable, payload: dict) -> None:
        try:
            callback(payload)
        except Exception:  # noqa: BLE001
            log.exception("bridge callback failed")


class _Handler(BaseHTTPRequestHandler):
    state: BridgeState  # injected

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.debug("http " + fmt, *args)

    # helpers -----------------------------------------------------------
    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # verbs ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            return self._json(200, {"ok": True, "service": "psd-agent-bridge"})
        if path == "/inbox":
            return self._json(200, {"prompts": self.state.list_inbox()})
        if path == "/outbox":
            return self._json(200, {"answers": self.state.list_outbox()})
        if path.startswith("/outbox/"):
            pid = path.rsplit("/", 1)[-1]
            answer = self.state.answer_for(pid)
            return self._json(200, answer or {"id": pid, "answer": None})
        if path == "/status":
            return self._json(200, self.state.get_status())
        if path == "/events":
            import urllib.parse as up

            query = up.parse_qs(up.urlparse(self.path).query)
            since = float(query.get("since", ["0"])[0])
            events = self.state.wait_events(since, timeout=25.0)
            return self._json(200, {"events": events})
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        payload = self._read_json()
        if path == "/inbox":
            if not payload.get("id") or not payload.get("body"):
                return self._json(400, {"ok": False, "error": "id and body required"})
            return self._json(200, self.state.add_prompt(payload))
        if path == "/outbox":
            if not payload.get("id") or not payload.get("answer"):
                return self._json(400, {"ok": False, "error": "id and answer required"})
            return self._json(200, self.state.add_answer(payload))
        if path == "/status":
            if not payload.get("state"):
                return self._json(400, {"ok": False, "error": "state required"})
            return self._json(200, self.state.set_status(payload))
        return self._json(404, {"error": "not found"})


class AgentBridge:
    """Embedded HTTP server exposing the inbox/outbox/status protocol."""

    def __init__(self, host: str, port: int, inbox_dir: Path,
                 outbox_dir: Path, status_file: Path):
        self.state = BridgeState(inbox_dir, outbox_dir, status_file)
        self._handler_cls = type("BoundHandler", (_Handler,), {"state": self.state})
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def available(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        if self._started:
            return
        try:
            self._server = ThreadingHTTPServer(
                (self.host, self.port), self._handler_cls
            )
        except OSError as exc:
            # Port busy (e.g. a second bridge already running) — degrade to
            # file-only mode instead of crashing; inbox/outbox files still work.
            log.warning(
                "agent bridge could not bind %s:%s (%s); file inbox/outbox only",
                self.host, self.port, exc,
            )
            self._server = None
            self._started = True
            return
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="agent-bridge", daemon=True
        )
        self._thread.start()
        self._started = True
        log.info("agent bridge listening on %s", self.base_url)

    def stop(self) -> None:
        # shutdown() deadlocks if serve_forever was never running — guard it.
        if not self._started:
            return
        self._started = False
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
