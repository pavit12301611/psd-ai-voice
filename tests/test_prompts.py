"""Prompt crafting + answer parsing."""

from assistant.brain import prompts


def test_craft_unknown_structure():
    p = prompts.craft_unknown("why is the sky blue", "knowledge_query")
    assert p.prompt_id
    assert "why is the sky blue" in p.body
    assert "Response contract" in p.body
    assert "speak:" in p.body
    assert "detail:" in p.body
    assert "Fedora Workstation 44" in p.body
    assert p.short_ask and len(p.short_ask.split()) < 25


def test_craft_how_to():
    p = prompts.craft_how_to("create a new user on Fedora")
    assert "create a new user" in p.body.lower()
    assert "numbered steps" in p.body.lower() or "steps" in p.body.lower()
    assert p.title.startswith("How to:")


def test_craft_do_task():
    p = prompts.craft_do_task("clean old logs from my system")
    assert "clean old logs" in p.body
    assert p.title.startswith("Do:")


def test_craft_direct_prompt_preserves_user_words():
    dictated = "write a python script that renames photos by date taken"
    p = prompts.craft_direct_prompt(dictated)
    assert dictated in p.body
    assert p.source == dictated
    assert p.title.startswith("Prompt:")


def test_parse_answer_labeled():
    speak, detail = prompts.parse_answer(
        "speak: Two steps, ready when you are.\n"
        "detail:\n1. Run sudo dnf install vim\n2. Verify with vim --version"
    )
    assert "Two steps" in speak
    assert "dnf install vim" in detail


def test_parse_answer_unlabeled_first_line_becomes_speak():
    speak, detail = prompts.parse_answer("Just do the restart.\nIt takes a minute.")
    assert speak == "Just do the restart."
    assert "restart" in detail


def test_extract_steps():
    detail = (
        "Here's the plan:\n"
        "1. Open a terminal\n"
        "2. Run `sudo useradd alice`\n"
        "- Verify with `id alice`\n"
        "Done!"
    )
    steps = prompts.extract_steps(detail)
    assert len(steps) == 3
    assert "useradd alice" in steps[1]


def test_as_dict_roundtrip():
    p = prompts.craft_unknown("test question", "unknown")
    d = p.as_dict()
    assert set(d) == {"id", "title", "body", "short_ask", "source", "created"}
