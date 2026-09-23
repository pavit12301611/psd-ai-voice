# PSD AI Voice Assistant 🎙️

A **fully-featured, voice-first Python assistant** built for **Fedora Workstation 44**.
It has its own offline knowledge base, understands everyday speech, and — when it
doesn't know something — **crafts a proper prompt and hands it to Agent Mode**
(the Arena coding agent). Answers come back **spoken aloud** and **shown as a
desktop notification** — while a small **AI gradient orb** (no text, ever)
follows your cursor and reacts to every state.

One command to run it:

```bash
./run.sh
```

---

## ✨ Features

| Area | What it does |
|---|---|
| 🔮 **AI orb (the only window)** | A small frameless circle with a flowing **Google-AI gradient** (blue → violet → rose → aqua) that **spring-follows your cursor**. **No text, ever** — states are pure motion: breathing / ripples + waveform / vortex / pulses / sparkle burst. Fully **click-through**. |
| 🖱️ **Light-shake activation** | Even a **gentle wiggle** triggers it: 3 reversals inside 1 s, ~18 px of travel (both axes count); sub-pixel jitter ignored. Wayland `evdev` · X11 `pynput` · `Ctrl+Alt+V` hotkey. |
| 🎙️ **Voice in / voice out** | Offline STT (**Vosk**) + TTS (**espeak-ng**), energy VAD with auto end-of-speech, conversation sessions that stay open until you're done. |
| 🧠 **Own knowledge base** | Local JSON knowledge with smart retrieval — "who are you", "what can you do", how agent mode works, etc. **Teachable**: say *"remember that the deploy server is thor"*. |
| 🤖 **Agent Mode bridge** | Anything unknown → **structured prompt** → localhost bridge → the Arena agent answers → **spoken aloud + desktop notification** (the orb never shows text). Optional LLM fallback (OpenAI-compatible) when no agent is online. |
| 📊 **Work-status watcher** | Watches `data/agent_status.json` and **announces**: *"Agent mode update: I'm now working on …"* / *"… I'm done with …"*. Ask anytime: *"what's the agent status?"* |
| 📅 **Calendar** | *"add project sync tomorrow at 3 pm to my calendar"* → SQLite store + standards-compliant `.ics` export + opens GNOME Calendar. |
| 🚀 **App launcher** | *"open Firefox"*, *"launch terminal"* — fuzzy-matches installed `.desktop` entries, launches via `gio`/`gtk-launch`. |
| ⚙️ **System control** | Volume up/down/set %, mute, brightness, screenshot, lock screen, suspend. |
| ⏰ **Timers & time** | *"set a timer for 10 minutes"*, *"what time is it?"*, *"what's today's date?"* |
| 📝 **Notes** | *"note: buy coffee beans"* / *"read my notes"* / *"clear notes"*. |
| 🛠️ **Work companion** | *"how do I create a user on Fedora?"* → Agent Mode returns numbered steps → walk through them: *"next step"*, *"repeat"*. |
| ✍️ **Prompt dictation** | *"prompt: refactor my backup script to run nightly"* → polished into a structured brief and queued to Agent Mode. |
| 🔮 **Orb UI** | The one and only window: circle, gradient, motion. Full text (agent answers, steps, status) is delivered via voice + `notify-send` notifications. |
| 📴 **Works offline** | STT/TTS/knowledge/skills are fully local; only Agent answers need the bridge (localhost) or an optional LLM API key. |

---

## 🚀 Quick start (Fedora Workstation 44)

```bash
git clone <this-repo> && cd psd-ai-voice
./run.sh            # installs deps on first run, then starts
```

First run will:

1. Install system packages with `dnf` (python3, tk, portaudio, espeak-ng, evdev…)
2. Create a virtualenv and `pip install -r requirements.txt`
3. Download the offline Vosk speech model (~40 MB)
4. Ask (once) to add your user to the **`input` group** so cursor-shake works natively on Wayland — **log out/in once** after accepting
5. Launch the assistant — the AI orb appears glued to your cursor

### Useful flags

```bash
./run.sh --setup        # deps + model only, don't start
./run.sh --text         # text REPL instead of voice (debugging)
./run.sh --once "open firefox"   # one-shot: process & print, exit
./run.sh --no-orb       # voice only, no orb overlay
./run.sh --mute         # no spoken audio (orb + notifications only)
./run.sh --autostart    # also install GNOME login autostart entry
./run.sh --dev          # reinstall python deps
./run.sh -y --setup     # non-interactive setup
```

---

## 🗣️ How to use it

There is **no window and no text** — just the glowing AI orb glued to your cursor.

1. **Light-shake the cursor** (a gentle wiggle is enough) — or press **Ctrl+Alt+V**.
2. The orb grows, sprouts ripple rings and a waveform edge + you hear a beep → **speak**.
3. It answers **out loud**; Agent answers and work status additionally pop a
   **desktop notification** with the full text (the orb never shows text).
   The orb stays pure: breathing → vortex → pulses → sparkle burst.
4. The session stays armed (~20 s) for natural follow-ups. Shake anytime to re-arm.

### Things to say

```text
add team sync tomorrow at 3 pm to my calendar
what's on my calendar
open firefox
open terminal
volume up / set volume to 40 percent
brightness down
take a screenshot
lock the screen
set a timer for 10 minutes
what time is it
note: reply to Alice about the invoice
read my notes
what's the agent status
are you working on my project
remember that the deploy server is called thor
how do I create a new user on Fedora
prompt: write a bash script that cleans files older than 30 days in /tmp
next step
what can you do
```

---

## 🤖 Agent Mode — how the loop works

```
┌────────────┐  shake & speak   ┌──────────────────┐
│    You     │ ───────────────► │  PSD Voice (app) │
└────────────┘                  └───────┬──────────┘
                                        │ 1. no local answer?
                                        │    craft structured prompt
                                        ▼
                              data/agent_inbox/<id>.md
                              POST http://127.0.0.1:8765/inbox
                                        │
                                        ▼
                              ┌─────────────────────┐
                              │  Agent Mode bridge  │  ← the Arena agent
                              │  (in-app server)    │    session (or LLM
                              └──────────┬──────────┘    fallback) answers
                                         │ 2. POST /outbox {id, answer}
                                         ▼
                              Orb sparkle-burst + notification + TTS speaks "speak:" line
```

**Prompt format** (crafted by `assistant/brain/prompts.py`): every request carries
*User said → inferred intent → environment → exact Task → Response contract* with
mandatory `speak:` (≤ 40 words, spoken) and `detail:` (full answer/steps, shown
in the notification)
sections — so replies always come back display- and speech-ready.

### Driving it from an Arena agent session

Tell the agent, in Arena chat:

> **"Check the voice assistant inbox and answer any pending prompts."**

It should:

1. Read `data/agent_inbox/*.json` (or `GET http://127.0.0.1:8765/inbox`)
2. Answer by writing `data/agent_outbox/<id>.json` **or** `POST /outbox`:

```json
{
  "id": "20260923-153000-ab12cd34",
  "source": "arena-agent",
  "answer": "speak: Two commands will do it.\ndetail:\n1. Run `sudo useradd…`\n2. Verify with `id newuser`"
}
```

3. Push progress to `data/agent_status.json` (or `POST /status`) — the assistant
   will **announce it by voice**:

```json
{
  "state": "working",
  "task": "Refactor login module",
  "detail": "Extracting token validation…",
  "project": "psd-ai-voice",
  "updated_at": "2026-09-23T15:31:02+0000"
}
```

`state` ∈ `idle | working | done | blocked | error`.

The bridge also runs standalone:

```bash
.venv/bin/python agent_server/server.py
```

### Optional LLM fallback

If you want answers even when no Arena agent is watching, set in
`config/config.yaml` → `agent.llm.enabled: true` and export an API key:

```bash
export OPENAI_API_KEY=sk-...
```

Any OpenAI-compatible endpoint works via `agent.llm.base_url` / `model`.

---

## 🧠 "Training" it — teaching new knowledge

The assistant ships scoped to **this** domain only (desktop + work companion).
Extend its own knowledge without code:

```bash
# by voice
"remember that the staging URL is staging.example.com"
"teach you that standup is every day at 9:15"
```

or edit `data/knowledge.json` directly (entries: `id`, `topics[]`, `answer`).

To change **what it can *do***, add a skill in `assistant/skills/` and register it
in `AssistantApp.skills` (`assistant/app.py`). To change **what it understands**,
extend `_PATTERNS` in `assistant/brain/nlu.py`.

---

## 📁 Project layout

```
psd-ai-voice/
├── run.sh                     ← Fedora 44 one-command setup + launcher
├── requirements.txt
├── config/
│   ├── config.yaml            ← all tunables (mic, orb, shake, agent…)
│   └── knowledge.seed.json    ← initial "own knowledge"
├── assistant/
│   ├── app.py                 ← orchestrator (sessions, loops, wiring)
│   ├── brain/
│   │   ├── nlu.py             ← intent/entity parser (trained for this domain)
│   │   ├── knowledge.py       ← local knowledge base (+ teach/forget)
│   │   ├── prompts.py         ← prompt engineering + answer parsing
│   │   └── router.py          ← skill → knowledge → agent escalation
│   ├── agent/
│   │   ├── bridge.py          ← localhost inbox/outbox/status HTTP server
│   │   ├── client.py          ← send prompt, wait for answer, LLM fallback
│   │   └── status_watch.py    ← announces work started/finished
│   ├── skills/                ← calendar, apps, system, time, notes, status, meta
│   ├── audio/                 ← microphone + energy VAD, speaker (queued TTS)
│   ├── stt/                   ← vosk (default) / faster-whisper (optional)
│   ├── activation/            ← cursor-shake detector (evdev | pynput)
│   └── ui/orb.py              ← AI orb: GTK3+cairo, spring-follow, click-through
├── agent_server/server.py     ← bridge as a standalone process
└── tests/                     ← unit tests (no mic needed)
```

### Runtime data (git-ignored)

```
data/
├── knowledge.json         learned facts
├── calendar.db + calendar/assistant.ics
├── notes.json
├── agent_status.json      ← agent writes; assistant announces changes
├── agent_inbox/           ← prompts awaiting answers  (*.json + *.md)
├── agent_outbox/          ← answers (*.json + *.md)
├── models/vosk/…          offline speech model
└── assistant.log
```

---

## ⚙️ Configuration highlights — `config/config.yaml`

```yaml
orb:                          # the ONLY window — circle, gradient, no text
  enabled: true
  size: 132                   # diameter px
  follow_omega: 14.0          # spring sharpness (cursor follow)
  follow_zeta: 0.85           # damping — floatier = more lag
activation:
  shake:                      # LIGHT shake profile
    direction_changes: 3
    window_seconds: 1.0
    min_travel_px: 18
    cooldown_seconds: 0.8
  session_timeout: 20         # silence before it disarms
  always_listening: false     # true = auto re-arm forever ("always active")
audio:
  vad_energy_threshold: 400   # raise if it hears background noise
agent:
  port: 8765
  answer_timeout: 180
  llm: {enabled: false}
```

---

## 🧪 Testing (no microphone required)

```bash
.venv/bin/python -m pytest tests/ -q
./run.sh --once "what can you do"
./run.sh --once "prompt: list my top 5 fedora tweaks"   # queues an inbox prompt
ls data/agent_inbox/
```

---

## 🔧 Troubleshooting

| Symptom | Fix |
|---|---|
| Shake does nothing (Wayland) | Accept the **input group** prompt in `run.sh`, log out/in. Or press `Ctrl+Alt+V`. |
| Orb not visible | `dnf install python3-gobject python3-cairo xorg-x11-server-Xwayland`, then restart. Check `data/assistant.log` for "orb online". |
| Orb visible but frozen | XWayland hiccup — restart the assistant (`./run.sh`). |
| No speech detected | Raise mic gain, or lower `audio.vad_energy_threshold` (try `250`). |
| Gibberish transcripts | Run `./run.sh --setup` to re-fetch the Vosk model; speak closer to the mic. |
| No spoken replies | Check `espeak-ng` installed (`dnf install espeak-ng`); try `espeak-ng "test"`. |
| No answer notifications | Check `libnotify`/`notify-send` exists (`dnf install libnotify`). |
| Agent never answers | Ensure the assistant is running (bridge port 8765), then tell the Arena agent to check the inbox. Inspect `data/agent_inbox/`, `data/agent_outbox/`. |
| Port busy | Change `agent.port` in `config/config.yaml`. |
| Mic busy | Close other recording apps (Zoom, browser tabs). |

Logs: `tail -f data/assistant.log`

---

## 📄 License

MIT — see repository default. Built for the Fedora Workstation 44 desktop.
