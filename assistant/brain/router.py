"""Routes an utterance to: skill → knowledge → Agent Mode escalation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from assistant.brain import prompts as prompt_crafting
from assistant.brain.knowledge import KnowledgeBase
from assistant.brain.nlu import NLU, never_escalates
from assistant.logger import get_logger
from assistant.types import NLUResult, Reply

log = get_logger("router")

Escalator = Callable[[prompt_crafting.CraftedPrompt], Reply]


@dataclass
class RouteOutcome:
    nlu: NLUResult
    reply: Reply
    handled_by: str  # skill name | "knowledge" | "agent" | "none"


class Router:
    def __init__(
        self,
        knowledge: KnowledgeBase,
        skills: list[Any],
        escalate: Escalator,
    ):
        self.nlu = NLU()
        self.knowledge = knowledge
        self.skills = skills
        self.escalate = escalate

    def handle(self, text: str) -> RouteOutcome:
        nlu = self.nlu.parse(text)
        log.info("nlu: intent=%s conf=%.2f text=%r",
                 nlu.intent, nlu.confidence, text)

        # 1) Skills (priority order, custom confidence gates)
        for skill in self.skills:
            confidence = skill.can_handle(nlu)
            if confidence > 0:
                try:
                    reply = skill.handle(nlu)
                except Exception:  # noqa: BLE001
                    log.exception("skill %s crashed", skill.name)
                    continue
                if reply is not None:
                    log.info("handled by skill %s (%.2f)", skill.name, confidence)
                    return RouteOutcome(nlu, reply, skill.name)

        # 2) Local knowledge
        if nlu.intent in ("knowledge_query", "unknown") or nlu.confidence < 0.6:
            answer = self.knowledge.lookup(nlu.raw)
            if answer:
                log.info("handled by knowledge base")
                return RouteOutcome(
                    nlu, Reply(speak=answer, display=answer, data={"source": "knowledge"}),
                    "knowledge",
                )

        # 3) Built-in intents that never leave the machine
        if never_escalates(nlu.intent):
            reply = Reply(
                speak="I'm ready — shake the cursor and tell me what you need.",
                display="No action taken.",
            )
            return RouteOutcome(nlu, reply, "builtin")

        # 4) Agent Mode escalation with a properly crafted prompt
        log.info("escalating to Agent Mode: %r", nlu.raw)
        if nlu.intent == "how_to":
            crafted = prompt_crafting.craft_how_to(nlu.raw)
        elif nlu.intent == "do_task":
            crafted = prompt_crafting.craft_do_task(nlu.raw)
        elif nlu.intent == "prompt_direct":
            crafted = prompt_crafting.craft_direct_prompt(
                nlu.entities.get("dictated") or nlu.raw
            )
        else:
            crafted = prompt_crafting.craft_unknown(nlu.raw, nlu.intent)

        reply = self.escalate(crafted)
        return RouteOutcome(nlu, reply, "agent")
