"""Main orchestrator — wires activation, audio, brain, skills, agent, orb.

The only visible UI is the AI orb (assistant/ui/orb.py): a small text-free
circle with a Google-AI gradient that spring-follows the cursor. Everything
else is voice + desktop notifications.
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from dataclasses import dataclass

from assistant.agent.bridge import AgentBridge
from assistant.agent.client import AgentClient
from assistant.agent.status_watch import StatusWatcher
from assistant.brain import prompts as craft
from assistant.brain.knowledge import KnowledgeBase
from assistant.brain.router import Router
from assistant.config import Config, ensure_runtime_dirs
from assistant.logger import get_logger, setup_logging
from assistant.notify import notify
from assistant.skills.apps_skill import AppsSkill
from assistant.skills.base import Skill
from assistant.skills.calendar_skill import CalendarSkill
from assistant.skills.meta_skill import MetaSkill, TeachSkill
from assistant.skills.notes_skill import NotesSkill
from assistant.skills.status_skill import StatusSkill
from assistant.skills.system_skill import SystemSkill
from assistant.skills.time_skill import TimeSkill
from assistant.types import Reply
from assistant.ui.orb import CursorTracker

log = get_logger("app")


@dataclass
class Session:
    active: bool = False
    deadline: float = 0.0
    follow_up: bool = False


@dataclass
class Options:
    text: bool = False            # REPL instead of voice
    once: str | None = None       # single utterance, print reply, exit
    no_orb: bool = False
    mute: bool = False
    config: str | None = None


class AssistantApp:
    def __init__(self, options: Options | None = None):
        self.options = options or Options()
        self.cfg = Config.load(self.options.config)
        ensure_runtime_dirs(self.cfg)
        setup_logging(
            self.cfg.get("logging.level", "INFO"),
            self.cfg.path_of("logging.file"),
        )
        log.info("PSD Voice starting (root=%s)", self.cfg.path_of("knowledge.path").parent)

        # --- shared state ------------------------------------------------
        self.session = Session()
        self.work_steps: list[str] = []
        self.work_step_index = 0
        self._stop = threading.Event()
        self._state = "READY"
        self.cursor = CursorTracker()

        # --- knowledge + skills -------------------------------------------
        self.knowledge = KnowledgeBase(
            self.cfg.path_of("knowledge.path"),
            self.cfg.path_of("knowledge.seed_file"),
        )

        # --- agent bridge + client ------------------------------------------
        self.bridge = AgentBridge(
            host=self.cfg.get("agent.host", "127.0.0.1"),
            port=int(self.cfg.get("agent.port", 8765)),
            inbox_dir=self.cfg.path_of("agent.inbox_dir"),
            outbox_dir=self.cfg.path_of("agent.outbox_dir"),
            status_file=self.cfg.path_of("agent.status_file"),
        )
        self.client = AgentClient(self.cfg, on_answer=self._on_agent_answer)
        self.bridge.state.on_answer = self._on_bridge_answer
        self.bridge.state.on_status = self._on_status_event

        # skills (order matters)
        self.teach_skill = TeachSkill(self.cfg, self)
        self.teach_skill.kb = self.knowledge
        self.skills: list[Skill] = [
            StatusSkill(self.cfg, self),
            self.teach_skill,
            MetaSkill(self.cfg, self),
            TimeSkill(self.cfg, self),
            NotesSkill(self.cfg, self),
            SystemSkill(self.cfg, self),
            CalendarSkill(self.cfg, self),
            AppsSkill(self.cfg, self),
        ]

        self.router = Router(self.knowledge, self.skills, self.escalate)

        # --- optional hardware pieces (lazy) ---------------------------------
        self.speaker = None
        self.stt = None
        self.mic = None
        self.orb = None
        self.input_monitor = None
        self.vad = None
        self._session_lock = threading.Lock()

        self.status_watcher = StatusWatcher(
            self.cfg.path_of("agent.status_file"),
            announce=self.announce,
        )

    # ==================================================================
    # Lifecycle
    # ==================================================================
    def start(self) -> None:
        """Start bridge, watchers, orb, audio pipeline."""
        self.bridge.start()
        self.status_watcher.start()

        if self.options.once is not None:
            return  # handled by run_once

        self._start_speaker()

        if self.options.text:
            return  # REPL mode: no orb/mic needed

        self._start_orb()
        self._start_audio_pipeline()
        signal.signal(signal.SIGINT, self._signal_stop)
        signal.signal(signal.SIGTERM, self._signal_stop)

    def run_forever(self) -> None:
        if self.options.text:
            self._repl()
            return
        if self.orb is not None:
            self.orb.run()  # GTK main loop; worker threads keep running
            self.shutdown()
            return
        # headless: block until stop
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        self.shutdown()

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        log.info("shutting down")
        self._stop.set()
        try:
            self.status_watcher.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.orb is not None:
            try:
                self.orb.quit()
            except Exception:  # noqa: BLE001
                pass
        for closer in (self.input_monitor, self.mic, self.speaker):
            if closer is not None:
                try:
                    closer.stop() if hasattr(closer, "stop") else closer.close()
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.bridge.stop()
        except Exception:  # noqa: BLE001
            pass

    def _signal_stop(self, signum, frame) -> None:  # noqa: ANN001
        log.info("received signal %s", signum)
        self._stop.set()
        if self.orb is not None:
            try:
                self.orb.quit()
            except Exception:  # noqa: BLE001
                pass

    # ==================================================================
    # Component starters
    # ==================================================================
    def _start_speaker(self) -> None:
        from assistant.audio.speaker import Speaker

        self.speaker = Speaker(
            rate=int(self.cfg.get("tts.rate", 170)),
            volume=float(self.cfg.get("tts.volume", 1.0)),
            voice=self.cfg.get("tts.voice"),
            mute=bool(self.options.mute or self.cfg.get("tts.mute", False)),
            on_state=lambda speaking: self._set_state(
                "SPEAKING" if speaking else "READY"
            ),
        )
        self.speaker.start()

    def _start_orb(self) -> None:
        """The one and only window: a tiny text-free gradient orb."""
        if self.options.no_orb or not self.cfg.get("orb.enabled", True):
            log.info("orb disabled")
            return
        try:
            from assistant.ui.orb import OrbUI
        except Exception as exc:  # noqa: BLE001
            log.warning("orb module unavailable: %s", exc)
            return
        try:
            self.orb = OrbUI(
                size=int(self.cfg.get("orb.size", 132)),
                opacity=float(self.cfg.get("orb.opacity", 0.97)),
                omega=float(self.cfg.get("orb.follow_omega", 14.0)),
                zeta=float(self.cfg.get("orb.follow_zeta", 0.85)),
                tracker=self.cursor,
            )
            # Build here (main thread) so GTK owns this thread; run() reuses it.
            self.orb.build()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "orb unavailable (%s) — running voice-only with notifications",
                exc,
            )
            self.orb = None
            return
        self.orb.set_state("READY")
        self.orb.set_agent(self.bridge.state.get_status().get("state", "idle"))

    def _start_audio_pipeline(self) -> None:
        from assistant.activation.mouse_shake import ShakeDetector, start_input_monitor
        from assistant.audio.microphone import EnergyVAD, Microphone
        from assistant.stt import build_stt

        # light-shake detector → arms a listening session; also feeds the
        # orb's cursor tracker with raw deltas (pointer-query fallback).
        detector = ShakeDetector(
            direction_changes=int(
                self.cfg.get("activation.shake.direction_changes", 3)),
            window_seconds=float(
                self.cfg.get("activation.shake.window_seconds", 1.0)),
            min_travel=float(self.cfg.get("activation.shake.min_travel_px", 18)),
            cooldown_seconds=float(
                self.cfg.get("activation.shake.cooldown_seconds", 0.8)),
            min_event_px=float(
                self.cfg.get("activation.shake.min_event_px", 0.8)),
            on_shake=self._on_shake,
            on_move=self.cursor.update,
        )
        self.input_monitor = start_input_monitor(
            self.cfg, detector, on_hotkey=self._on_shake
        )

        try:
            self.stt = build_stt(self.cfg)
        except Exception as exc:  # noqa: BLE001
            log.error("STT init failed: %s", exc)
            self._announce_now(
                "My speech recognition model isn't ready. Run ./run.sh --setup "
                "to download it.",
                f"⚠️ STT init failed: {exc}",
            )

        try:
            self.mic = Microphone(
                sample_rate=int(self.cfg.get("audio.sample_rate", 16000)),
                device=self.cfg.get("audio.device"),
            )
            self.mic.start()
        except Exception as exc:  # noqa: BLE001
            log.error("microphone unavailable: %s", exc)
            self._announce_now(
                "I can't open the microphone. Check your input device settings.",
                f"⚠️ Mic error: {exc}",
            )
            self.mic = None

        self.vad = EnergyVAD(
            threshold=float(self.cfg.get("audio.vad_energy_threshold", 400)),
            sample_rate=int(self.cfg.get("audio.sample_rate", 16000)),
            silence_seconds=float(self.cfg.get("audio.silence_seconds", 1.2)),
            min_speech_seconds=float(self.cfg.get("audio.min_speech_seconds", 0.35)),
            max_seconds=float(self.cfg.get("audio.max_utterance_seconds", 12)),
        )

        threading.Thread(target=self._listen_loop, name="listen", daemon=True).start()
        threading.Thread(target=self._session_loop, name="session", daemon=True).start()
        log.info("audio pipeline up (light shake → listen → transcribe → respond)")

    # ==================================================================
    # Session (listening) management
    # ==================================================================
    def arm_session(self) -> None:
        timeout = float(self.cfg.get("activation.session_timeout", 20))
        with self._session_lock:
            self.session.active = True
            self.session.deadline = time.monotonic() + timeout
        if self.mic:
            self.mic.clear()
        if self.vad:
            self.vad.reset()
        from assistant.audio.speaker import earcon

        earcon("listen")
        self._set_state("LISTENING")
        log.info("session armed (%.0fs)", timeout)

    def extend_session(self, extra: float | None = None) -> None:
        timeout = float(self.cfg.get("activation.session_timeout", 20))
        with self._session_lock:
            if self.session.active:
                self.session.deadline = time.monotonic() + (extra or timeout)

    def disarm_session(self) -> None:
        with self._session_lock:
            self.session.active = False
            self.session.deadline = 0.0
        if self._state == "LISTENING":
            self._set_state("READY")

    def _session_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.25)
            with self._session_lock:
                expired = self.session.active and \
                    time.monotonic() > self.session.deadline
            if expired:
                if self.cfg.get("activation.always_listening", False):
                    log.info("session re-armed (always_listening)")
                    self.arm_session()
                else:
                    log.info("session timed out")
                    self.disarm_session()

    def _on_shake(self) -> None:
        log.info("light shake detected → listening")
        self.arm_session()

    def _session_is_active(self) -> bool:
        with self._session_lock:
            return self.session.active

    # ==================================================================
    # Listen → transcribe → respond
    # ==================================================================
    def _listen_loop(self) -> None:
        while not self._stop.is_set():
            if self.mic is None:
                time.sleep(0.5)
                continue
            frame = self.mic.read(timeout=0.4)
            if frame is None:
                continue
            if not self._session_is_active():
                continue
            state, utterance = self.vad.feed(frame)
            if state == "speech" and self._state != "LISTENING":
                self._set_state("LISTENING")
            if state != "done" or utterance is None:
                continue

            self.extend_session(extra=8.0)
            threading.Thread(
                target=self._handle_utterance_async,
                args=(utterance,),
                daemon=True,
            ).start()

    def _handle_utterance_async(self, frames) -> None:
        try:
            self._handle_voice(frames)
        except Exception:  # noqa: BLE001
            log.exception("utterance handling failed")
            self.speak("Something went wrong processing that — say that again?")

    def _handle_voice(self, frames) -> None:
        from assistant.audio.speaker import earcon

        self._set_state("THINKING")
        earcon("think")
        if self.stt is None:
            self.speak("Speech recognition isn't ready yet.")
            return
        transcript = self.stt.transcribe(frames)
        text = transcript.text.strip()
        log.info("heard: %r (conf=%.2f)", text, transcript.confidence)
        if self.orb:
            self.orb.show_transcript(text or "…")
        if not text:
            self.speak("I didn't catch that. Say that again, please.")
            self.extend_session()
            return
        if transcript.confidence and transcript.confidence < 0.25:
            self.speak(
                f"I think you said: {text}. I'm not confident — say it again "
                "or start with 'prompt' to dictate directly."
            )
            self.extend_session()
            return

        reply = self.handle_text(text)
        if reply:
            self.respond(reply)
        self.extend_session()

    # ==================================================================
    # Core text pipeline (shared by voice, REPL, and --once)
    # ==================================================================
    def handle_text(self, text: str) -> Reply | None:
        if not text or not text.strip():
            return None
        outcome = self.router.handle(text.strip())
        return outcome.reply

    def respond(self, reply: Reply) -> None:
        if reply.escalated:
            self._set_state("THINKING")   # waiting for Agent Mode
            if self.orb:
                self.orb.show_output(reply.shown())
        self.speak(reply.speak)

    def speak(self, text: str) -> None:
        if self.speaker:
            self.speaker.say(text)
        else:
            log.info("[speak] %s", text)

    def announce(self, speak: str, display: str) -> None:
        """Proactive push (status watcher, timers, agent events): voice + notification."""
        self._announce_now(speak, display)

    def _announce_now(self, speak: str, display: str,
                      title: str = "PSD Voice") -> None:
        if self.orb:
            self.orb.append_output(display)
            self.orb.burst()
        notify(title, display)
        self.speak(speak)

    def _set_state(self, state: str) -> None:
        self._state = state
        if self.orb:
            self.orb.set_state(state)

    # ==================================================================
    # Agent Mode integration
    # ==================================================================
    def escalate(self, crafted: craft.CraftedPrompt) -> Reply:
        return self.client.send(crafted)

    def escalate_how_to(self, request: str) -> Reply:
        return self.client.send(craft.craft_how_to(request))

    def _on_agent_answer(self, payload: dict) -> None:
        """Answer from the client waiter (agent, LLM fallback, or timeout)."""
        self._on_bridge_answer(payload)

    def _on_bridge_answer(self, payload: dict) -> None:
        source = payload.get("source", "agent")
        speak = payload.get("speak") or ""
        detail = payload.get("detail") or payload.get("answer") or ""

        if self.orb:
            self.orb.show_output(f"speak: {speak}\ndetail:\n{detail}")
            self.orb.set_agent("done" if source not in ("timeout",) else "idle")
            self.orb.burst()            # bright sparkle pop = answer landed

        if source == "timeout":
            notify("Agent Mode", speak or "No answer yet.", urgency="low")
            self.speak(speak or "No answer from Agent Mode yet.")
            return

        # The orb shows NO text — the answer is DISPLAYED via notification
        # and SPOKEN in full summary form.
        notify("🤖 Agent Mode answer", f"{speak}\n\n{detail}", urgency="normal",
               timeout_ms=12000)
        log.info("agent answer (%s): %s | %s", source, speak, detail[:200])
        self.speak(speak or (detail.splitlines()[0] if detail else
                             "Here's the answer from Agent Mode."))

        # Extract numbered steps so the user can navigate them by voice.
        steps = craft.extract_steps(detail)
        if steps:
            self.work_steps = steps
            self.work_step_index = 1
            log.info("loaded %d navigable steps ('next step' ready)", len(steps))
            if self.orb:
                self.orb.append_output(
                    f"{len(steps)} steps loaded — say 'next step' to walk through"
                )

    def _on_status_event(self, status: dict) -> None:
        state = status.get("state", "idle")
        if self.orb:
            self.orb.set_agent(state)
        # Spoken announcement comes from StatusWatcher; this only syncs the
        # orb's colour/motion instantly (working = amber shimmer, done = pop).

    # ==================================================================
    # Text REPL (`--text`)
    # ==================================================================
    def _repl(self) -> None:
        print("PSD Voice — text mode. Type an utterance, or /quit to exit.")
        print("Examples: open firefox | add standup tomorrow at 9 am to my calendar |")
        print("          what's the agent status | prompt: write a backup script")
        while not self._stop.is_set():
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line in ("/quit", "/exit", "quit", "exit"):
                break
            reply = self.handle_text(line)
            if reply:
                print(f"assistant> {reply.speak}")
                if reply.display and reply.display != reply.speak:
                    print(f"---\n{reply.display}\n---")
        self.shutdown()


# ======================================================================
# CLI
# ======================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assistant",
        description="PSD AI Voice Assistant — voice-first desktop companion "
                    "with a cursor-following AI orb",
    )
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--text", action="store_true",
                        help="text REPL instead of voice mode (debug)")
    parser.add_argument("--once", metavar="UTTERANCE",
                        help="process one utterance, print reply, exit")
    parser.add_argument("--no-orb", "--no-hud", dest="no_orb",
                        action="store_true",
                        help="disable the AI orb overlay")
    parser.add_argument("--mute", action="store_true", help="no TTS output")
    return parser


def run_once(opts: Options) -> int:
    app = AssistantApp(opts)
    app.bridge.start()  # live endpoint while this command processes
    reply = app.handle_text(opts.once or "")
    if reply is None:
        print("assistant> (no reply)")
        app.bridge.stop()
        return 1
    print(f"assistant> {reply.speak}")
    if reply.display and reply.display != reply.speak:
        print("---")
        print(reply.display)
        print("---")
    time.sleep(0.4)
    app.bridge.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    opts = Options(
        text=args.text,
        once=args.once,
        no_orb=args.no_orb,
        mute=args.mute,
        config=args.config,
    )
    if opts.once is not None:
        return run_once(opts)

    app = AssistantApp(opts)
    app.start()
    app.run_forever()
    return 0
