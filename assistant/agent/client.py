"""Client used by the assistant to talk to Agent Mode (bridge + LLM fallback)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

import requests

from assistant.brain import prompts as craft
from assistant.config import Config
from assistant.logger import get_logger
from assistant.types import Reply

log = get_logger("agent.client")


class AgentClient:
    def __init__(self, cfg: Config, on_answer: Callable[[dict], None] | None = None):
        self.cfg = cfg
        self.host = cfg.get("agent.host", "127.0.0.1")
        self.port = int(cfg.get("agent.port", 8765))
        self.base = f"http://{self.host}:{self.port}"
        self.inbox_dir: Path = cfg.path_of("agent.inbox_dir")
        self.outbox_dir: Path = cfg.path_of("agent.outbox_dir")
        self.answer_timeout = float(cfg.get("agent.answer_timeout", 180))
        self.on_answer = on_answer
        self._llm_cfg = cfg.get("agent.llm", {}) or {}

    # ------------------------------------------------------------------
    def send(self, crafted: craft.CraftedPrompt) -> Reply:
        """Queue the prompt; return an immediate spoken acknowledgement.

        A watcher thread waits for the answer (or the LLM fallback) and
        fires self.on_answer when it arrives.
        """
        payload = crafted.as_dict()
        queued = False
        try:
            resp = requests.post(
                f"{self.base}/inbox", json=payload, timeout=3
            )
            queued = resp.ok
        except requests.RequestException as exc:
            log.warning("bridge POST /inbox failed (%s); writing file only", exc)

        # File fallback so an agent watching the folder still sees it.
        try:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
            json_path = self.inbox_dir / f"{crafted.prompt_id}.json"
            if not json_path.exists():
                json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                (self.inbox_dir / f"{crafted.prompt_id}.md").write_text(
                    f"<!-- id: {crafted.prompt_id} -->\n{crafted.body}\n",
                    encoding="utf-8",
                )
        except OSError as exc:
            log.error("could not write inbox file: %s", exc)

        if not queued:
            log.info("prompt %s queued via file inbox", crafted.prompt_id)

        threading.Thread(
            target=self._wait_for_answer,
            args=(crafted,),
            daemon=True,
            name=f"wait-{crafted.prompt_id[:8]}",
        ).start()

        queued_line = (
            f"📤 Sent to Agent Mode (#{crafted.prompt_id[-6:]})\n"
            f"   {crafted.short_ask}\n\n{crafted.body}"
        )
        return Reply(
            speak=crafted.short_ask,
            display=queued_line,
            data={"prompt_id": crafted.prompt_id, "queued": True},
            escalated=True,
        )

    # ------------------------------------------------------------------
    def _wait_for_answer(self, crafted: craft.CraftedPrompt) -> None:
        pid = crafted.prompt_id
        deadline = time.time() + self.answer_timeout
        while time.time() < deadline:
            answer = self._fetch_answer(pid)
            if answer:
                self._deliver(crafted, answer.get("answer", ""),
                              answer.get("source", "agent"))
                return
            time.sleep(1.5)

        log.warning("no agent answer for %s after %.0fs", pid, self.answer_timeout)
        if self._llm_enabled():
            answer = self._llm_answer(crafted)
            if answer:
                self._deliver(crafted, answer, "llm-fallback")
                return

        self._deliver(
            crafted,
            "speak: I'm still waiting on Agent Mode — ask me again in a bit.\n"
            "detail: No answer yet. The prompt remains in the inbox "
            f"(`{self.inbox_dir / (pid + '.md')}`).",
            "timeout",
        )

    def _fetch_answer(self, pid: str) -> dict | None:
        try:
            resp = requests.get(f"{self.base}/outbox/{pid}", timeout=3)
            if resp.ok:
                data = resp.json()
                if data.get("answer"):
                    return data
        except requests.RequestException:
            pass
        # file fallback
        path = self.outbox_dir / f"{pid}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("answer"):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _deliver(self, crafted: craft.CraftedPrompt, answer: str, source: str) -> None:
        speak, detail = craft.parse_answer(answer)
        payload = {
            "id": crafted.prompt_id,
            "answer": answer,
            "speak": speak,
            "detail": detail,
            "source": source,
            "title": crafted.title,
        }
        if self.on_answer:
            try:
                self.on_answer(payload)
            except Exception:  # noqa: BLE001
                log.exception("on_answer callback failed")

    # ------------------------------------------------------------------
    def _llm_enabled(self) -> bool:
        if not self._llm_cfg.get("enabled"):
            return False
        import os

        key_env = self._llm_cfg.get("api_key_env", "OPENAI_API_KEY")
        return bool(os.environ.get(key_env))

    def _llm_answer(self, crafted: craft.CraftedPrompt) -> str | None:
        """Fallback: answer through a configurable OpenAI-compatible API."""
        import os

        key_env = self._llm_cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(key_env, "")
        base_url = str(self._llm_cfg.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        model = self._llm_cfg.get("model", "gpt-4o-mini")
        timeout = float(self._llm_cfg.get("timeout", 60))
        system = (
            "You are Agent Mode inside a voice assistant on Fedora Workstation 44. "
            "Answer the structured brief exactly using the response contract "
            "'speak:' and 'detail:' sections."
        )
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": crafted.body},
                    ],
                    "temperature": 0.3,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            log.info("LLM fallback answered for %s", crafted.prompt_id)
            return content
        except (requests.RequestException, KeyError, IndexError) as exc:
            log.error("LLM fallback failed: %s", exc)
            return None
