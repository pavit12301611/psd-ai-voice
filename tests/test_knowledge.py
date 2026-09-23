"""Knowledge base retrieval + teach/forget."""

from assistant.brain.knowledge import KnowledgeBase  # noqa: F401 (fixture type)


def test_seed_loaded(knowledge):
    assert len(knowledge.entries) >= 8


def test_lookup_capabilities(knowledge):
    answer = knowledge.lookup("what can you do")
    assert answer is not None
    assert "calendar" in answer.lower() or "agent mode" in answer.lower()


def test_lookup_agent_mode(knowledge):
    answer = knowledge.lookup("how does agent mode work")
    assert answer is not None
    assert "prompt" in answer.lower() or "agent" in answer.lower()


def test_lookup_miss(knowledge):
    assert knowledge.lookup("quantum chromodynamics lattice gauge theory") is None


def test_teach_and_retrieve(cfg):
    kb = KnowledgeBase(cfg.path_of("knowledge.path"),
                       cfg.path_of("knowledge.seed_file"))
    confirmation = kb.teach("remember that the deploy server is called thor")
    assert "thor" in confirmation.lower()
    answer = kb.lookup("what is the deploy server")
    assert answer and "thor" in answer.lower()


def test_teach_persists(cfg):
    path = cfg.path_of("knowledge.path")
    kb1 = KnowledgeBase(path, cfg.path_of("knowledge.seed_file"))
    kb1.teach("remember that standup starts at 9:15")
    kb2 = KnowledgeBase(path, cfg.path_of("knowledge.seed_file"))
    assert kb2.lookup("when is standup") is not None


def test_forget_custom_keeps_seed(knowledge):
    knowledge.teach("remember that my favourite color is teal")
    removed = knowledge.forget_all_custom()
    assert removed >= 1
    assert knowledge.lookup("my favourite color") is None
    # seed entries survive
    assert knowledge.lookup("what can you do") is not None
