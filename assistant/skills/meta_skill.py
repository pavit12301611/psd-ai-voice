"""Meta skills: help tour, identity, social, teach, step navigation."""

from __future__ import annotations

import json
from pathlib import Path

from assistant import __version__
from assistant.brain.knowledge import KnowledgeBase
from assistant.skills.base import Skill
from assistant.types import NLUResult, Reply


class MetaSkill(Skill):
    """greeting / thanks / goodbye / identity / help / repeat / next."""

    name = "meta"

    def can_handle(self, nlu: NLUResult) -> float:
        if nlu.intent in {
            "greeting", "thanks", "goodbye", "identity",
            "help_general", "repeat_step", "next_step",
        }:
            return 0.9
        return 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        intent = nlu.intent
        if intent == "greeting":
            return Reply(
                speak="Hey! Shake the cursor and tell me what you need.",
                display="👋 Ready when you are.",
            )
        if intent == "thanks":
            return Reply(speak="Anytime.", display="😊 Anytime.")
        if intent == "goodbye":
            return Reply(
                speak="Goodbye. I'll stay ready in the background.",
                display="👋 Session stays armed in the background.",
            )
        if intent == "identity":
            return Reply(
                speak=(
                    f"I'm PSD Voice version {__version__}, your on-device "
                    "assistant. I run entirely on this computer and escalate "
                    "hard questions to Agent Mode."
                ),
                display=(
                    f"🧠 PSD Voice v{__version__}\n"
                    "   Offline STT/TTS · local knowledge · Agent Mode bridge"
                ),
            )
        if intent == "help_general":
            return Reply(
                speak=(
                    "I can manage your calendar, open apps, change volume "
                    "and brightness, take screenshots, lock the screen, set "
                    "timers, keep notes, check Agent Mode's work status, and "
                    "answer from my own knowledge. Shake your cursor to talk."
                ),
                display=(
                    "🧭 What I can do\n"
                    "  📅 “add standup tomorrow at 9 am to my calendar”\n"
                    "  🚀 “open Firefox”\n"
                    "  🔊 “volume up” · “set volume to 40 percent”\n"
                    "  💡 “brightness down”\n"
                    "  📸 “take a screenshot”\n"
                    "  🔒 “lock the screen”\n"
                    "  ⏰ “set a timer for 10 minutes”\n"
                    "  📝 “note: buy coffee beans”\n"
                    "  🤖 “what's the agent status?”\n"
                    "  🛠️ “how do I create a new user on Fedora?”\n"
                    "  ✍️ “prompt: write a script that renames my photos”\n"
                    "  🧠 “remember that the deploy server is thor”"
                ),
            )
        if intent == "next_step":
            if self.app and self.app.work_steps:
                idx = self.app.work_step_index
                steps = self.app.work_steps
                if idx < len(steps):
                    step = steps[self.app.work_step_index]
                    self.app.work_step_index += 1
                    nxt = (f" Next: {steps[self.app.work_step_index]}"
                           if self.app.work_step_index < len(steps) else
                           " That was the last step — tell me if anything failed.")
                    return Reply(
                        speak=f"Step {idx + 1}: {step}{nxt}",
                        display=self._steps_display(steps, idx),
                    )
                return Reply(
                    speak="That was the last step. Want me to ask Agent Mode for what's next?",
                    display="✅ All steps completed.",
                )
            return Reply(
                speak="We don't have an active step list yet. Ask me a how-to question first.",
                display="ℹ️ No active step list.",
            )
        if intent == "repeat_step":
            if self.app and self.app.work_steps:
                idx = max(0, self.app.work_step_index - 1)
                return Reply(
                    speak=f"Again: {self.app.work_steps[idx]}",
                    display=self._steps_display(self.app.work_steps, idx),
                )
            return Reply(speak="Nothing to repeat yet.")
        return None

    @staticmethod
    def _steps_display(steps: list[str], current: int) -> str:
        lines = ["🛠️ Work steps:"]
        for i, step in enumerate(steps):
            marker = "👉" if i == current else ("✅" if i < current else "▫️")
            lines.append(f"  {marker} {i + 1}. {step}")
        return "\n".join(lines)


class TeachSkill(Skill):
    """'remember that X' → writes into the local knowledge base."""

    name = "teach"

    def __init__(self, cfg, app=None):
        super().__init__(cfg, app)
        self.kb: KnowledgeBase | None = None  # wired by the app

    def can_handle(self, nlu: NLUResult) -> float:
        return 0.97 if nlu.intent == "teach" else 0.0

    def handle(self, nlu: NLUResult) -> Reply | None:
        if self.kb is None:
            return Reply(speak="My knowledge store isn't available yet.")
        confirmation = self.kb.teach(nlu.raw)
        return Reply(
            speak=confirmation,
            display=f"🧠 Knowledge updated:\n   {confirmation}",
        )
