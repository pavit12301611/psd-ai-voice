"""Prompt engineering for Agent Mode.

Every escalation is wrapped in a structured brief so the answering agent
gets exact, unambiguous instructions — and so replies can be spoken back
in a short form while the full text lands on the HUD.
"""

from __future__ import annotations

import platform
import time
import uuid
from dataclasses import dataclass


@dataclass
class CraftedPrompt:
    prompt_id: str
    title: str
    body: str          # full markdown brief
    short_ask: str     # one sentence summary for TTS acknowledgment
    source: str        # voice utterance that triggered it
    created: float

    def as_dict(self) -> dict:
        return {
            "id": self.prompt_id,
            "title": self.title,
            "body": self.body,
            "short_ask": self.short_ask,
            "source": self.source,
            "created": self.created,
        }


def _environment_block() -> str:
    return (
        "- OS: Fedora Workstation 44 (Linux "
        f"{platform.release()}, {platform.machine()})\n"
        f"- Desktop: GNOME / Wayland (auto-detected: "
        f"{platform.node() or 'workstation'})\n"
        "- Assistant: PSD Voice (local, offline STT/TTS, HUD display)"
    )


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _title_from(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text[: limit - 1] + "…" if len(text) > limit else text


# ---------------------------------------------------------------------------
# Crafters
# ---------------------------------------------------------------------------

def craft_unknown(utterance: str, intent: str, note: str = "") -> CraftedPrompt:
    """The assistant heard the user but has no local answer."""
    body = f"""# Agent Task Request — answer needed

**User said:** "{utterance}"
**Assistant intent guess:** `{intent}`
**Created:** {time.strftime("%Y-%m-%d %H:%M:%S %z")}

## Task
Answer the user's question or request accurately and helpfully.

## Environment
{_environment_block()}

## Response contract (important)
1. `speak:` — one or two spoken sentences (≤ 40 words) the assistant will say aloud.
2. `detail:` — the full answer, steps, or explanation shown on the HUD.
   Use numbered steps for procedures. Keep total length under 400 words.

## Notes
{note or "No extra notes."}
"""
    return CraftedPrompt(
        prompt_id=_new_id(),
        title=f"Answer: {_title_from(utterance)}",
        body=body,
        short_ask="I've sent that to Agent Mode — one moment while it works on an answer.",
        source=utterance,
        created=time.time(),
    )


def craft_how_to(request: str, context: str = "") -> CraftedPrompt:
    """User wants step-by-step help doing something on this computer."""
    context_block = f"## Extra context\n{context}\n" if context else ""
    body = f"""# Agent Task Request — step-by-step guidance

**User asked:** "{request}"
**Created:** {time.strftime("%Y-%m-%d %H:%M:%S %z")}

## Task
Produce exact, actionable steps the user can follow on Fedora Workstation 44
(GNOME) to accomplish: {request}

## Environment
{_environment_block()}
{context_block}
## Response contract (important)
1. `speak:` — one sentence confirming the plan (≤ 30 words).
2. `detail:` — numbered steps (each step ≤ 25 words), any commands in
   backticks, warnings prefixed with `Care:`. End with how to verify success.

## Quality bar
- Assume an intermediate Fedora user; no prior steps omitted.
- Prefer GUI paths first, terminal commands second.
- If the task cannot be done safely, say so in `speak:` and explain in `detail:`.
"""
    return CraftedPrompt(
        prompt_id=_new_id(),
        title=f"How to: {_title_from(request)}",
        body=body,
        short_ask="Good question — I'm asking Agent Mode for exact steps.",
        source=request,
        created=time.time(),
    )


def craft_do_task(request: str) -> CraftedPrompt:
    """User wants the computer to *do* something — agent decides/implements."""
    body = f"""# Agent Task Request — execute work on this machine

**User asked:** "{request}"
**Created:** {time.strftime("%Y-%m-%d %H:%M:%S %z")}

## Task
Decide the concrete work needed to accomplish: {request}
If it is safe and can be performed by the agent session, do it and report
what changed. Otherwise return precise manual instructions.

## Environment
{_environment_block()}

## Response contract (important)
1. `speak:` — status sentence (≤ 30 words): what you did or what will be done.
2. `detail:` — either a list of changes performed, or numbered manual steps.
3. If blocked, state exactly what permission/input is required.
"""
    return CraftedPrompt(
        prompt_id=_new_id(),
        title=f"Do: {_title_from(request)}",
        body=body,
        short_ask="On it — handing that task to Agent Mode now.",
        source=request,
        created=time.time(),
    )


def craft_direct_prompt(dictated: str) -> CraftedPrompt:
    """User dictated a prompt verbatim; polish minimally, preserve intent."""
    body = f"""# Agent Task Request — user-dictated prompt

**User dictated:** "{dictated}"
**Created:** {time.strftime("%Y-%m-%d %H:%M:%S %z")}

## Task (verbatim from the user, polished for clarity)
{dictated}

## Environment
{_environment_block()}

## Response contract (important)
1. `speak:` — confirm acceptance or ask ONE clarifying question (≤ 30 words).
2. `detail:` — the completed work product, plan, or answer. If the task was
   executed on the machine, list exactly what changed.
"""
    return CraftedPrompt(
        prompt_id=_new_id(),
        title=f"Prompt: {_title_from(dictated)}",
        body=body,
        short_ask="Prompt received — I've queued it to Agent Mode.",
        source=dictated,
        created=time.time(),
    )


# ---------------------------------------------------------------------------
# Answer parsing
# ---------------------------------------------------------------------------

def extract_steps(detail: str) -> list[str]:
    """Pull numbered/bulleted steps out of an agent reply."""
    import re

    steps: list[str] = []
    for line in (detail or "").splitlines():
        m = re.match(r"^\s*(?:\d+[.)]|[-*•])\s+(.*\S)\s*$", line)
        if m:
            step = m.group(1).strip()
            # strip embedded "Step N:" prefixes
            step = re.sub(r"^(?:step\s*\d+\s*[:.-]\s*)", "", step, flags=re.I)
            if step:
                steps.append(step)
    return steps


def parse_answer(text: str) -> tuple[str, str]:
    """Split an agent reply into (speak, detail).

    Accepts either 'speak:/detail:' labels or returns the whole text as both.
    """
    text = (text or "").strip()
    if not text:
        return "I didn't get an answer back from Agent Mode.", ""

    speak, detail = "", ""
    if "speak:" in text.lower():
        parts = re_split_labels(text)
        speak = parts.get("speak", "").strip()
        detail = parts.get("detail", "").strip()
    if not speak:
        # first non-empty line becomes the spoken part
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        speak = lines[0] if lines else text
        detail = text
    if not detail:
        detail = text
    return speak, detail


def re_split_labels(text: str) -> dict[str, str]:
    import re

    pattern = re.compile(r"(?im)^\s*(speak|detail)\s*:\s*")
    matches = list(pattern.finditer(text))
    out: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[match.group(1).lower()] = text[match.end():end].strip()
    return out
