"""Calendar skill — parsing, storage, ICS export."""

from datetime import datetime, timedelta

from assistant.skills.calendar_skill import CalendarSkill


def make(cfg) -> CalendarSkill:
    return CalendarSkill(cfg)


def test_add_event_parses_title_and_when(cfg):
    from assistant.brain.nlu import NLU

    skill = make(cfg)
    nlu = NLU().parse("add project sync tomorrow at 3 pm to my calendar")
    reply = skill.handle(nlu)
    assert reply is not None
    assert "project sync" in reply.data["title"].lower()
    start = datetime.fromisoformat(reply.data["start"])
    tomorrow = datetime.now().astimezone() + timedelta(days=1)
    assert start.date() == tomorrow.date()
    assert start.hour == 15
    assert "tomorrow" in reply.speak.lower()


def test_add_event_without_when_asks(cfg):
    from assistant.brain.nlu import NLU

    skill = make(cfg)
    nlu = NLU().parse("add dentist appointment to my calendar")
    reply = skill.handle(nlu)
    assert reply is not None
    assert "tell me when" in reply.speak.lower() or "when" in reply.speak.lower()


def test_list_events(cfg):
    from assistant.brain.nlu import NLU

    skill = make(cfg)
    add = NLU().parse("add lunch with Sam tomorrow at noon to my calendar")
    skill.handle(add)
    reply = skill.handle(NLU().parse("what's on my calendar"))
    assert reply is not None
    assert "lunch with Sam" in reply.display
    assert "nothing" not in reply.speak.lower()


def test_ics_export(cfg):
    from assistant.brain.nlu import NLU

    skill = make(cfg)
    skill.handle(NLU().parse("add retro friday at 4 pm to my calendar"))
    ics = skill._export_ics()
    content = ics.read_text()
    assert content.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in content
    assert "SUMMARY:retro" in content or "SUMMARY:" in content
    assert content.strip().endswith("END:VCALENDAR")


def test_empty_list(cfg):
    skill = make(cfg)
    reply = skill.handle(
        __import__("assistant.brain.nlu", fromlist=["NLU"]).NLU().parse(
            "show my schedule"
        )
    )
    assert reply is not None
    assert "nothing" in reply.speak.lower()
