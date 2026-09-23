"""NLU — intent & entity parsing for the assistant's trained domain."""

from assistant.brain.nlu import NLU, normalize


nlu = NLU()


def test_normalize():
    assert normalize("  What's   the TIME?! ") == "what is the time"
    assert normalize("I'm gonna open Firefox") == "i am going to open firefox"


def test_open_app():
    r = nlu.parse("please open firefox")
    assert r.intent == "open_app"
    assert r.entities["app"] == "firefox"

    r = nlu.parse("open GNOME Calendar for me")
    assert r.intent == "open_app"
    assert "calendar" in r.entities["app"].lower()


def test_calendar_add():
    r = nlu.parse("add project sync tomorrow at 3 pm to my calendar")
    assert r.intent == "calendar_add"
    assert "event_text" in r.entities


def test_calendar_list():
    r = nlu.parse("what's on my calendar")
    assert r.intent == "calendar_list"


def test_volume_intents():
    assert nlu.parse("volume up").intent == "volume_up"
    assert nlu.parse("turn the volume down").intent == "volume_down"
    r = nlu.parse("set volume to 40 percent")
    assert r.intent == "volume_set"
    assert r.entities["percent"] == 40
    assert nlu.parse("mute the sound").intent == "mute"


def test_system_intents():
    assert nlu.parse("take a screenshot").intent == "screenshot"
    assert nlu.parse("lock the screen").intent == "lock_screen"


def test_time_and_timer():
    assert nlu.parse("what time is it").intent == "time_query"
    assert nlu.parse("what's today's date").intent == "date_query"
    r = nlu.parse("set a timer for 5 minutes")
    assert r.intent == "timer_set"
    assert r.entities["duration_value"] == 5
    assert r.entities["duration_unit"] == "minute"


def test_notes():
    r = nlu.parse("note: buy coffee beans")
    assert r.intent == "note_add"
    assert nlu.parse("read my notes").intent == "note_read"
    assert nlu.parse("clear my notes").intent == "note_clear"


def test_status_check():
    assert nlu.parse("what's the agent status").intent == "status_check"
    assert nlu.parse("are you working").intent == "status_check"
    assert nlu.parse("is the project done yet").intent == "status_check"


def test_prompt_direct():
    r = nlu.parse("prompt: write a backup script for my home folder")
    assert r.intent == "prompt_direct"
    assert "backup script" in r.entities["dictated"]


def test_how_to():
    assert nlu.parse("how do I create a new user on Fedora").intent == "how_to"
    assert nlu.parse("walk me through installing docker").intent == "how_to"


def test_teach():
    r = nlu.parse("remember that the deploy server is thor")
    assert r.intent == "teach"


def test_social_and_meta():
    assert nlu.parse("hello there").intent == "greeting"
    assert nlu.parse("thanks a lot").intent == "thanks"
    assert nlu.parse("who are you").intent == "identity"
    assert nlu.parse("what can you do").intent == "help_general"
    assert nlu.parse("next step").intent == "next_step"
    assert nlu.parse("repeat that").intent == "repeat_step"


def test_unknown_goes_to_knowledge_or_agent():
    r = nlu.parse("why do migrating birds fly in a V shape")
    assert r.intent in ("knowledge_query", "unknown")
