"""Agent bridge — full inbox → agent → outbox loop over real HTTP."""

import json
import time

import pytest
import requests

from assistant.agent.bridge import AgentBridge


@pytest.fixture()
def bridge(cfg):
    b = AgentBridge(
        host="127.0.0.1",
        port=8765 + (hash(cfg.path_of("agent.inbox_dir")) % 200),
        inbox_dir=cfg.path_of("agent.inbox_dir"),
        outbox_dir=cfg.path_of("agent.outbox_dir"),
        status_file=cfg.path_of("agent.status_file"),
    )
    b.start()
    time.sleep(0.15)
    yield b
    b.stop()


def test_health(bridge):
    r = requests.get(f"{bridge.base_url}/health", timeout=3)
    assert r.ok and r.json()["ok"] is True


def test_inbox_outbox_roundtrip(bridge):
    prompt = {
        "id": "test-001",
        "title": "Answer: why do cats purr",
        "body": "# Agent Task Request\nUser asked why do cats purr",
        "short_ask": "asking agent",
        "source": "why do cats purr",
        "created": time.time(),
    }
    r = requests.post(f"{bridge.base_url}/inbox", json=prompt, timeout=3)
    assert r.ok and r.json()["ok"]

    # files exist for folder-watching agents
    inbox = bridge.state.inbox_dir
    assert (inbox / "test-001.json").exists()
    assert (inbox / "test-001.md").exists()

    # pending list shows it unanswered
    listing = requests.get(f"{bridge.base_url}/inbox", timeout=3).json()["prompts"]
    mine = [p for p in listing if p["id"] == "test-001"][0]
    assert mine["answered"] is False

    # the answering agent (this simulates the Arena agent) replies
    answer = {
        "id": "test-001",
        "source": "arena-agent",
        "answer": "speak: Cats purr to self-soothe and communicate.\ndetail:"
                  "\n1. Muscles twitch at 25 Hz.\n2. It signals contentment.",
    }
    r = requests.post(f"{bridge.base_url}/outbox", json=answer, timeout=3)
    assert r.ok and r.json()["ok"]

    got = requests.get(f"{bridge.base_url}/outbox/test-001", timeout=3).json()
    assert "self-soothe" in got["answer"]

    # files written too
    out = bridge.state.outbox_dir
    saved = json.loads((out / "test-001.json").read_text())
    assert saved["source"] == "arena-agent"


def test_status_update_and_read(bridge):
    status = {"state": "working", "task": "Refactor login module",
              "detail": "extracting tokens"}
    r = requests.post(f"{bridge.base_url}/status", json=status, timeout=3)
    assert r.ok
    got = requests.get(f"{bridge.base_url}/status", timeout=3).json()
    assert got["state"] == "working"
    assert got["task"] == "Refactor login module"
    assert got["updated_at"]
    # on-disk file for the status watcher
    disk = json.loads(bridge.state.status_file.read_text())
    assert disk["state"] == "working"


def test_answer_callback_fires(bridge):
    received = []
    bridge.state.on_answer = received.append
    requests.post(f"{bridge.base_url}/inbox", json={
        "id": "cb-1", "title": "t", "body": "b", "short_ask": "s",
        "source": "x", "created": 0,
    }, timeout=3)
    requests.post(f"{bridge.base_url}/outbox", json={
        "id": "cb-1", "answer": "speak: hi\ndetail: yo", "source": "arena-agent",
    }, timeout=3)
    for _ in range(50):
        if received:
            break
        time.sleep(0.02)
    assert received and received[0]["id"] == "cb-1"


def test_validation(bridge):
    r = requests.post(f"{bridge.base_url}/inbox", json={"title": "no id"}, timeout=3)
    assert r.status_code == 400
    r = requests.post(f"{bridge.base_url}/outbox", json={"answer": "x"}, timeout=3)
    assert r.status_code == 400
