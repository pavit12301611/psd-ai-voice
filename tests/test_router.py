"""Router + skills + escalation wiring (no audio, no network for most)."""

import time

import pytest

from assistant.agent.bridge import AgentBridge
from assistant.agent.client import AgentClient
from assistant.brain.knowledge import KnowledgeBase
from assistant.brain.router import Router
from assistant.skills.apps_skill import AppsSkill
from assistant.skills.base import Skill
from assistant.skills.calendar_skill import CalendarSkill
from assistant.skills.meta_skill import MetaSkill, TeachSkill
from assistant.skills.notes_skill import NotesSkill
from assistant.skills.status_skill import StatusSkill
from assistant.skills.system_skill import SystemSkill
from assistant.skills.time_skill import TimeSkill
from assistant.types import NLUResult, Reply


class DummyEscalator:
    def __init__(self):
        self.crafted = []

    def __call__(self, crafted):
        self.crafted.append(crafted)
        return Reply(speak=crafted.short_ask, display=crafted.body, escalated=True)


@pytest.fixture()
def router(cfg, knowledge):
    esc = DummyEscalator()
    skills = [
        StatusSkill(cfg),
        TeachSkill(cfg),
        MetaSkill(cfg),
        TimeSkill(cfg),
        NotesSkill(cfg),
        SystemSkill(cfg),
        CalendarSkill(cfg),
        AppsSkill(cfg),
    ]
    teach = skills[1]
    teach.kb = knowledge
    r = Router(knowledge, skills, esc)
    r.escalator = esc
    return r


def test_help_routed_to_meta(router):
    out = router.handle("what can you do")
    assert out.handled_by == "meta"
    assert "calendar" in out.reply.speak.lower()


def test_identity(router):
    out = router.handle("who are you")
    assert out.handled_by == "meta"
    assert "PSD Voice" in out.reply.speak


def test_time_skill(router):
    out = router.handle("what time is it")
    assert out.handled_by == "time"


def test_status_skill_reads_file(router, cfg):
    import json
    cfg.path_of("agent.status_file").write_text(json.dumps({
        "state": "working", "task": "build HUD", "detail": "wiring buttons",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }))
    out = router.handle("what's the agent status")
    assert out.handled_by == "status"
    assert "build HUD" in out.reply.speak
    assert "working" in out.reply.display.lower()


def test_teach_then_remember(router, knowledge):
    out = router.handle("remember that the api key lives in ~/.secrets")
    assert out.handled_by == "teach"
    out2 = router.handle("where does the api key live")
    assert out2.handled_by == "knowledge"
    assert ".secrets" in out2.reply.speak


def test_knowledge_answers_locally(router):
    out = router.handle("what are your capabilities")
    # hits seed 'capabilities' entry either as help_general or via knowledge
    assert out.handled_by in ("meta", "knowledge")
    assert "calendar" in out.reply.speak.lower() or "volume" in out.reply.speak.lower()


def test_unknown_escalates_with_crafted_prompt(router):
    out = router.handle("explain the heisenberg uncertainty principle with flair")
    assert out.handled_by == "agent"
    assert out.reply.escalated
    assert len(router.escalator.crafted) == 1
    body = router.escalator.crafted[0].body
    assert "Response contract" in body
    assert "heisenberg" in body.lower()


def test_how_to_escalates_as_how_to(router):
    out = router.handle("how do I set up a VPN on Fedora")
    assert out.handled_by == "agent"
    crafted = router.escalator.crafted[-1]
    assert crafted.title.startswith("How to:")
    assert "VPN" in crafted.body


def test_prompt_direct_escalates(router):
    out = router.handle("prompt: delete all node_modules folders in my projects")
    assert out.handled_by == "agent"
    crafted = router.escalator.crafted[-1]
    assert crafted.title.startswith("Prompt:")
    assert "node_modules" in crafted.body


def test_volume_never_escalates(router):
    out = router.handle("volume up")
    assert out.handled_by == "system"
    assert router.escalator.crafted == []


# ---------------------------------------------------------------------------
# Full client loop against a live bridge (file-based agent reply)
# ---------------------------------------------------------------------------
def test_agent_client_end_to_end(cfg, tmp_runtime):
    bridge = AgentBridge(
        host="127.0.0.1",
        port=8901,
        inbox_dir=cfg.path_of("agent.inbox_dir"),
        outbox_dir=cfg.path_of("agent.outbox_dir"),
        status_file=cfg.path_of("agent.status_file"),
    )
    bridge.start()
    time.sleep(0.15)
    got = []
    client = AgentClient(cfg, on_answer=got.append)

    from assistant.brain import prompts

    crafted = prompts.craft_how_to("rotate my LUKS disk encryption passphrase")
    reply = client.send(crafted)
    assert reply.escalated
    assert "Agent Mode" in reply.speak or "agent" in reply.speak.lower()

    # wait for the prompt to land in the inbox (file or HTTP)
    inbox_json = cfg.path_of("agent.inbox_dir") / f"{crafted.prompt_id}.json"
    for _ in range(50):
        if inbox_json.exists():
            break
        time.sleep(0.05)
    assert inbox_json.exists()

    # simulate the Arena agent answering
    import requests
    requests.post(f"{bridge.base_url}/outbox", json={
        "id": crafted.prompt_id,
        "source": "arena-agent",
        "answer": "speak: Two steps and you're done.\ndetail:\n"
                  "1. Run `cryptsetup luksChangeKey`\n2. Test with a reboot.",
    }, timeout=3)

    for _ in range(100):
        if got:
            break
        time.sleep(0.05)
    assert got, "answer callback never fired"
    assert "Two steps" in got[0]["speak"]
    assert "luksChangeKey" in got[0]["detail"]
    bridge.stop()
