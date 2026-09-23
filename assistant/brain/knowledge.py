"""Local knowledge base — the assistant's *own* knowledge.

Entries live in JSON. New facts can be taught at runtime
("remember that ...") and are persisted immediately.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from assistant.logger import get_logger

log = get_logger("knowledge")

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in",
    "on", "for", "and", "or", "you", "your", "me", "my", "i", "it", "that",
    "this", "with", "do", "does", "can", "could", "tell", "ask", "about",
    "what", "who", "where", "when", "why", "how", "at", "as", "by", "from",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 1}


def _tokens(text: str) -> set[str]:
    """Content words; falls back to raw words when everything is a stopword."""
    words = _words(text)
    content = {w for w in words if w not in _STOPWORDS}
    return content or words


class KnowledgeBase:
    def __init__(self, path: Path, seed_file: Path | None = None):
        self.path = path
        self.seed_file = seed_file
        self._lock = threading.Lock()
        self.entries: list[dict[str, Any]] = []
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.entries = data.get("entries", [])
                log.info("loaded %d knowledge entries", len(self.entries))
                return
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("knowledge file unreadable (%s); reseeding", exc)

        if self.seed_file and self.seed_file.exists():
            try:
                seed = json.loads(self.seed_file.read_text(encoding="utf-8"))
                self.entries = seed.get("entries", [])
            except (json.JSONDecodeError, OSError) as exc:
                log.error("seed knowledge unreadable: %s", exc)
                self.entries = []
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self.entries}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    # -- retrieval -------------------------------------------------------
    def search(self, query: str, limit: int = 3) -> list[tuple[float, dict[str, Any]]]:
        q_words = _words(query)
        if not q_words:
            return []
        q_content = {w for w in q_words if w not in _STOPWORDS}
        # All-stopword queries ("what can you do") match on raw words instead.
        use_raw = not q_content
        q_set = q_words if use_raw else q_content

        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in self.entries:
            topics = " ".join(entry.get("topics", []))
            t_words = _words(topics) | _words(entry.get("id", "").replace("_", " "))
            if not t_words:
                continue
            t_content = {w for w in t_words if w not in _STOPWORDS}
            t_set = t_words if (use_raw or not t_content) else t_content

            overlap = q_set & t_set
            if not overlap and use_raw:
                overlap = q_words & t_words
            if not overlap:
                continue
            score = len(overlap) / len(t_set) * 0.55 + \
                    len(overlap) / len(q_set) * 0.45
            # exact-ish phrase bonus
            low_q = query.lower()
            for topic in entry.get("topics", []):
                if topic and topic in low_q:
                    score += 0.35
                    break
            scored.append((score, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:limit]

    def lookup(self, query: str, threshold: float = 0.34) -> str | None:
        hits = self.search(query, limit=1)
        if hits and hits[0][0] >= threshold:
            entry = hits[0][1]
            log.info("knowledge hit %s (%.2f)", entry.get("id"), hits[0][0])
            return entry.get("answer")
        return None

    # -- mutation ----------------------------------------------------------
    def teach(self, fact: str, topic: str | None = None) -> str:
        """Store a new fact. Format: 'remember that X is Y'."""
        fact = fact.strip().rstrip(".")
        fact = re.sub(
            r"^(?:remember|note|learn|store)(?:\s+that)?\s+", "", fact,
            flags=re.I,
        ).strip()
        if not fact:
            return "I didn't catch a fact to remember."

        # Derive a short topic key from the beginning of the fact.
        words = fact.split()
        key = topic or " ".join(words[:4]).lower()
        entry_id = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")[:48] or "fact"

        with self._lock:
            for entry in self.entries:
                if entry.get("id") == entry_id:
                    entry["answer"] = fact
                    if key not in entry.get("topics", []):
                        entry.setdefault("topics", []).append(key)
                    self._save()
                    return f"Updated what I know: {fact}"
            self.entries.append({
                "id": entry_id,
                "topics": [key, key.replace(" ", "_")],
                "answer": fact,
            })
            self._save()
        return f"Got it, I'll remember that. {fact}"

    def forget_all_custom(self) -> int:
        with self._lock:
            before = len(self.entries)
            seed_ids: set[str] = set()
            if self.seed_file and self.seed_file.exists():
                try:
                    seed = json.loads(self.seed_file.read_text(encoding="utf-8"))
                    seed_ids = {e.get("id") for e in seed.get("entries", [])}
                except (json.JSONDecodeError, OSError):
                    pass
            self.entries = [e for e in self.entries if e.get("id") in seed_ids]
            self._save()
            return before - len(self.entries)

    def all_entries(self) -> list[dict[str, Any]]:
        return list(self.entries)
