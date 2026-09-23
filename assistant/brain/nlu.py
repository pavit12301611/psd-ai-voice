"""Rule-based NLU tuned specifically for desktop-assistant tasks.

The assistant is deliberately "trained" only for this domain:
calendar, apps, system control, notes/timers, status checks, how-to
requests, direct agent prompts, and knowledge queries. Everything else
is escalated to Agent Mode with a crafted prompt.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Pattern

from assistant.types import NLUResult

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_CONTRACTIONS = {
    "what's": "what is",
    "whats": "what is",
    "where's": "where is",
    "how do i": "how do i",
    "don't": "do not",
    "can't": "can not",
    "won't": "will not",
    "i'm": "i am",
    "it's": "it is",
    "let's": "let us",
    "gonna": "going to",
    "wanna": "want to",
    "gimme": "give me",
    "ok google": "assistant",
    "hey assistant": "assistant",
    "please": "",
}

_FILLER = re.compile(
    r"\b(um+|uh+|like|you know|basically|actually)\b", re.IGNORECASE
)

_PUNCT = re.compile(r"[^\w\s:%.'-]", re.IGNORECASE)


def normalize(text: str) -> str:
    text = text.strip().lower()
    for bad, good in _CONTRACTIONS.items():
        text = text.replace(bad, good)
    text = _FILLER.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Pattern table — ordered by priority (first confident match wins)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, Pattern[str], float]] = [
    # --- direct prompt dictation -----------------------------------------
    ("prompt_direct", re.compile(r"^(?:hey |please )?prompt[:,]?\s+", re.I), 0.99),
    ("prompt_direct", re.compile(r"\b(tell|instruct|ask)\s+the\s+agent\s+(to|about)\b"), 0.95),
    ("prompt_direct", re.compile(r"\bhand\s+(this|it)\s+to\s+(the\s+)?agent\b"), 0.93),
    ("prompt_direct", re.compile(r"\bwrite\s+(me\s+)?a\s+prompt\s+to\b"), 0.93),
    # --- status ------------------------------------------------------------
    ("status_check", re.compile(r"\b(what|whats|what's)?\s*(is\s+)?(the\s+)?"
                                 r"(agent|your|project|work)\s*(mode)?\s*"
                                 r"(work\s*)?(status|progress|state)\b"), 0.97),
    ("status_check", re.compile(r"\b(are you|is the agent)\s+working\b"), 0.97),
    ("status_check", re.compile(r"\b(done yet|finished yet|any (updates|news))\b"), 0.9),
    ("status_check", re.compile(r"\b(project|agent)\s+update\b"), 0.9),
    # --- teach / remember ---------------------------------------------------
    ("teach", re.compile(r"^(?:remember|note|learn|store)(?:\s+that)?\s+.+"), 0.96),
    ("teach", re.compile(r"\bteach\s+(you|yourself)\b"), 0.93),
    # --- calendar ------------------------------------------------------------
    ("calendar_add", re.compile(
        r"\b(add|schedule|create|put|book|set up|remind me)\b.*\b(calendar|event|"
        r"meeting|appointment|reminder)\b"), 0.96),
    ("calendar_add", re.compile(
        r"\b(calendar|event|meeting|appointment)\b.*\b(today|tomorrow|tonight|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"\d{1,2}(:\d{2})?\s*(am|pm)?)\b"), 0.9),
    ("calendar_add", re.compile(r"^add .+ to (my|the) calendar$"), 0.97),
    ("calendar_list", re.compile(
        r"\b(what|show|list|check|read)\b.*\b(calendar|events|appointments|"
        r"schedule|agenda)\b"), 0.94),
    ("calendar_list", re.compile(r"\bmy (schedule|agenda|day)\b"), 0.9),
    ("calendar_open", re.compile(r"\bopen (my |the )?calendar\b"), 0.6),  # apps skill can outrank
    # --- system control -------------------------------------------------------
    ("volume_up", re.compile(r"\b(volume|sound)\s*(up|louder|increase|raise)\b"), 0.97),
    ("volume_up", re.compile(r"\b(turn|make)\b.*\b(louder|up)\b.*\b(?:volume|sound)\b"), 0.9),
    ("volume_down", re.compile(r"\b(volume|sound)\s*(down|quieter|lower|decrease)\b"), 0.97),
    ("volume_down", re.compile(r"\b(turn|make)\b.*\b(down|quieter)\b.*\b(?:volume|sound)\b"), 0.9),
    ("mute", re.compile(r"\b(mute|unmute|silence)\b"), 0.95),
    ("volume_set", re.compile(r"\b(set|put)\s+volume\s+(to\s+)?(\d{1,3})\s*(percent|%)?"), 0.98),
    ("volume_set", re.compile(r"\bvolume\s+(to\s+)?(\d{1,3})\s*(percent|%)"), 0.96),
    ("brightness_up", re.compile(r"\b(brightness|screen)\s*(up|brighter|increase)\b"), 0.95),
    ("brightness_down", re.compile(r"\b(brightness|screen)\s*(down|dimmer|decrease|lower)\b"), 0.95),
    ("screenshot", re.compile(r"\b(screenshot|screen shot|capture|snip|grab)\b"), 0.97),
    ("lock_screen", re.compile(r"\b(lock|sleep|suspend)\s*(the\s+)?(screen|computer|laptop|machine)?\b"), 0.93),
    # --- timers / time ----------------------------------------------------------
    ("timer_set", re.compile(r"\b(set|start)?\s*a?\s*timer\s*(for|of)?\s*"
                             r"(\d+\s*(?:hours?|minutes?|seconds?)"
                             r"(?:\s*(?:and|,)?\s*\d+\s*(?:hours?|minutes?|seconds?))*)"), 0.98),
    ("time_query", re.compile(r"\b(what|whats|what's)\s+(time|the time|it)\b|"
                              r"\bcurrent time\b|\btime is it\b"), 0.95),
    ("date_query", re.compile(r"\b(what|whats|what's|todays|today's)?\s*"
                              r"(date|day is it|the date|today)\b|what day"), 0.93),
    # --- notes --------------------------------------------------------------------
    ("note_add", re.compile(r"^(?:add|take|make)\s+(?:a\s+)?(?:quick\s+)?note\b"), 0.96),
    ("note_add", re.compile(r"^note[:,]\s*"), 0.97),
    ("note_read", re.compile(r"\b(read|show|list|what are)\b.*\bnotes?\b"), 0.94),
    ("note_clear", re.compile(r"\b(clear|delete|erase|wipe)\b.*\bnotes?\b"), 0.95),
    # --- apps -----------------------------------------------------------------------
    ("open_app", re.compile(r"^(?:please\s+)?(?:open|launch|start|run|fire up)\s+(?:up\s+)?"), 0.99),
    ("open_app", re.compile(r"\bopen\s+(?:the\s+)?[a-z0-9][a-z0-9 .+-]{0,40}$"), 0.96),
    # --- meta / social (before how_to so "what can you do" isn't a how-to) ---
    ("help_general", re.compile(r"^(?:what can you do|list (?:your )?(?:skills|commands)|i need help)\b"), 0.96),
    ("greeting", re.compile(r"^(hello|hi|hey|good (morning|afternoon|evening)|yo|howdy)\b"), 0.9),
    ("thanks", re.compile(r"^(thanks|thank you|cheers|appreciate it|ta)\b"), 0.95),
    ("goodbye", re.compile(r"^(bye|goodbye|see you|good night|shut ?down|exit|quit)\b"), 0.9),
    ("identity", re.compile(r"\b(who|what) (are|r) (you|u)\b|your name\b|introduce yourself\b"), 0.95),
    # --- how-to / work companion ------------------------------------------------------
    ("how_to", re.compile(r"^(?:how|what) (?:do|does|can|should|would|to|is) (i|we|you|it|this)\b"), 0.92),
    ("how_to", re.compile(r"^(?:help me|walk me through|guide me|show me how)\b"), 0.95),
    ("how_to", re.compile(r"^(?:teach me|explain|instruct me)\b"), 0.9),
    ("do_task", re.compile(r"^(?:can|could|would) you (?:please )?(?:do|handle|take care of)\b"), 0.9),
    ("do_task", re.compile(r"^(?:do|handle|take care of|execute|perform)\b"), 0.88),
    ("do_task", re.compile(r"^(?:i (?:want|need|would like) you to)\b"), 0.85),
    # --- next step navigation ------------------------------------------------------------
    ("next_step", re.compile(r"^(?:next|continue|go on|keep going|next step|then what)\b"), 0.95),
    ("repeat_step", re.compile(r"^(?:repeat|say (?:that )?again|come again|what did you say)\b"), 0.93),
]

_INTENTS_NEVER_ESCALATE = {
    "greeting", "thanks", "goodbye", "identity", "help_general",
    "repeat_step", "next_step", "time_query", "date_query",
}

_ENTITY_RES: dict[str, Pattern[str]] = {
    "percent": re.compile(r"(\d{1,3})\s*(?:percent|%)"),
    "duration": re.compile(
        r"(\d+)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)", re.I),
    "time": re.compile(
        r"\b(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\b", re.I),
    "bare_time": re.compile(r"\b(\d{1,2}:\d{2})\b"),
}


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _extract_entities(normalized: str, raw: str) -> dict[str, Any]:
    entities: dict[str, Any] = {}
    source = raw or normalized

    m = _ENTITY_RES["percent"].search(normalized)
    if m:
        entities["percent"] = min(int(m.group(1)), 150)

    m = _ENTITY_RES["duration"].search(normalized)
    if m:
        entities["duration_value"] = int(m.group(1))
        unit = m.group(2).lower()
        if unit.endswith("s") and not unit.endswith("ss"):
            unit = unit[:-1]  # minutes→minute, hours→hour, secs→sec, mins→min
        unit = {"hr": "hour", "min": "minute", "sec": "second"}.get(unit, unit)
        entities["duration_unit"] = unit

    m = _ENTITY_RES["time"].search(normalized) or _ENTITY_RES["bare_time"].search(normalized)
    if m:
        entities["time_hint"] = m.group(1).replace(".", "").upper()

    return {"text": source, **entities} if source else entities


class NLU:
    """Intent + entity parser for the assistant's trained domain."""

    def parse(self, text: str) -> NLUResult:
        raw = (text or "").strip()
        normalized = normalize(raw)
        entities = _extract_entities(normalized, raw)

        if not normalized:
            return NLUResult("unknown", 0.0, raw, normalized, entities)

        best_custom: tuple[str, float] | None = None
        for intent, pattern, confidence in _PATTERNS:
            if pattern.search(normalized) or pattern.search(raw.lower()):
                # keep scanning only if a later pattern could score higher —
                # the table is priority-ordered, so first hit is good enough
                best_custom = (intent, confidence)
                break

        if best_custom:
            intent, confidence = best_custom
            if intent == "open_app":
                app = self._app_target(normalized, raw)
                if app:
                    entities["app"] = app
                else:
                    intent, confidence = "unknown", 0.3
            if intent == "calendar_add":
                entities["event_text"] = raw
            if intent == "timer_set":
                pass  # duration entity already extracted
            if intent == "prompt_direct":
                entities["dictated"] = self._strip_prompt_marker(raw)
            return NLUResult(intent, confidence, raw, normalized, entities)

        # Knowledge-shaped question heuristics (resolved later by the KB).
        if normalized.startswith(("what ", "who ", "where ", "why ", "when ")):
            return NLUResult("knowledge_query", 0.5, raw, normalized, entities)

        return NLUResult("unknown", 0.0, raw, normalized, entities)

    # ------------------------------------------------------------------
    @staticmethod
    def _app_target(normalized: str, raw: str) -> str:
        for verb in ("please ",):
            normalized = normalized.replace(verb, "")
        m = re.search(
            r"\b(?:open|launch|start|run|fire up)\s+(?:up\s+)?(.+)$", normalized
        )
        if not m:
            return ""
        target = m.group(1).strip()
        target = re.sub(
            r"\b(for me|on my computer|on this computer|please|now)\b", "", target
        ).strip()
        # strip trailing politeness / filler
        target = re.sub(r"^(the|a|an)\s+", "", target)
        return target.strip(" .!?")

    @staticmethod
    def _strip_prompt_marker(raw: str) -> str:
        text = raw.strip()
        text = re.sub(
            r"^(?:hey\s+|please\s+)?prompt[:,]?\s*", "", text, flags=re.I
        )
        text = re.sub(
            r"^(?:tell|instruct|ask)\s+the\s+agent\s+(?:to|about)\s+", "",
            text, flags=re.I,
        )
        text = re.sub(r"^hand\s+(?:this|it)\s+to\s+(?:the\s+)?agent\s+", "",
                      text, flags=re.I)
        text = text.strip(" .,")
        return text or raw.strip()


def never_escalates(intent: str) -> bool:
    return intent in _INTENTS_NEVER_ESCALATE
