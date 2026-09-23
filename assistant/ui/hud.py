"""Always-on-top HUD: state pill, live transcript, agent answers, Talk button.

Voice is the primary interface — the HUD is a *display* (plus one Talk
button for accessibility when shaking the cursor isn't convenient).
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

from assistant.logger import get_logger

log = get_logger("hud")

BG = "#0f1419"
PANEL = "#1a2129"
FG = "#e6edf3"
MUTED = "#8b98a5"
ACCENT = "#3fb6ff"

STATE_COLORS = {
    "READY": "#2ea043",
    "LISTENING": "#f85149",
    "THINKING": "#d29922",
    "SPEAKING": "#a371f7",
    "ANSWER": "#3fb6ff",
}


class HUD:
    def __init__(
        self,
        width: int = 440,
        height: int = 620,
        x: int = 24,
        y: int = 24,
        always_on_top: bool = True,
        on_talk_press: Callable[[], None] | None = None,
        on_talk_release: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ):
        self.width = width
        self.height = height
        self.x = x
        self.y = y
        self.always_on_top = always_on_top
        self.on_talk_press = on_talk_press
        self.on_talk_release = on_talk_release
        self.on_close = on_close

        self.events: queue.Queue = queue.Queue()
        self.root: tk.Tk | None = None
        self._built = False

    # ------------------------------------------------------------------
    def build(self) -> None:
        if self._built:
            return
        root = tk.Tk()
        root.title("PSD Voice")
        root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")
        root.configure(bg=BG)
        if self.always_on_top:
            root.attributes("-topmost", True)
        try:
            root.attributes("-type", "dock")  # GNOME/Wayland: keep above, no decorations
        except tk.TclError:
            pass
        root.resizable(False, False)

        mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=10)
        base = tkfont.nametofont("TkDefaultFont").copy()
        base.configure(size=11)

        # --- header -------------------------------------------------
        header = tk.Frame(root, bg=PANEL, padx=12, pady=10)
        header.pack(fill="x")

        self._state_var = tk.StringVar(value="READY")
        self._dot = tk.Canvas(header, width=16, height=16, bg=PANEL,
                              highlightthickness=0)
        self._dot.pack(side="left")
        self._state_id = self._dot.create_oval(2, 2, 14, 14,
                                               fill=STATE_COLORS["READY"],
                                               outline="")
        tk.Label(header, text="PSD VOICE", bg=PANEL, fg=FG,
                 font=("TkDefaultFont", 12, "bold")).pack(side="left", padx=(8, 0))
        self._agent_pill = tk.Label(header, text="agent: idle", bg=PANEL,
                                    fg=MUTED, font=("TkDefaultFont", 9))
        self._agent_pill.pack(side="right")

        # --- hint ---------------------------------------------------
        self._hint = tk.Label(
            root,
            text="🖱️ Shake your cursor to talk  ·  or hold Talk",
            bg=BG, fg=MUTED, font=("TkDefaultFont", 9), pady=6,
        )
        self._hint.pack(fill="x", padx=12)

        # --- transcript ----------------------------------------------
        tk.Label(root, text="YOU SAID", bg=BG, fg=MUTED, anchor="w",
                 font=("TkDefaultFont", 8, "bold")).pack(fill="x", padx=12)
        self._transcript = tk.Text(
            root, height=3, bg=PANEL, fg=FG, insertbackground=FG,
            relief="flat", font=mono, padx=10, pady=8, wrap="word",
            state="disabled",
        )
        self._transcript.pack(fill="x", padx=12, pady=(2, 10))

        # --- agent answer ----------------------------------------------
        tk.Label(root, text="AGENT MODE / ASSISTANT", bg=BG, fg=MUTED,
                 anchor="w", font=("TkDefaultFont", 8, "bold")).pack(
            fill="x", padx=12)
        self._output = tk.Text(
            root, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
            font=mono, padx=10, pady=8, wrap="word", state="disabled",
        )
        self._output.pack(fill="both", expand=True, padx=12, pady=(2, 10))
        self._output.tag_configure("title", foreground=ACCENT,
                                   font=("TkDefaultFont", 11, "bold"))
        self._output.tag_configure("muted", foreground=MUTED)
        self._output.tag_configure("ok", foreground="#3fb46b")

        # --- buttons ---------------------------------------------------
        buttons = tk.Frame(root, bg=BG)
        buttons.pack(fill="x", padx=12, pady=(0, 12))

        self._talk_btn = tk.Button(
            buttons, text="🎤  TALK", bg=STATE_COLORS["LISTENING"], fg="white",
            activebackground="#ff6b63", activeforeground="white",
            relief="flat", padx=14, pady=8, font=("TkDefaultFont", 10, "bold"),
        )
        self._talk_btn.pack(side="left", fill="x", expand=True)
        if self.on_talk_press:
            self._talk_btn.bind("<ButtonPress-1>", lambda e: self._press())
            self._talk_btn.bind("<ButtonRelease-1>", lambda e: self._release())
        else:
            self._talk_btn.configure(state="disabled")

        self._status_line = tk.Label(root, text="ready", bg=BG, fg=MUTED,
                                     font=("TkDefaultFont", 8))
        self._status_line.pack(side="bottom", pady=(0, 6))

        root.protocol("WM_DELETE_WINDOW", self._close)
        self.root = root
        self._built = True

    def _press(self) -> None:
        if self.on_talk_press:
            self.on_talk_press()

    def _release(self) -> None:
        if self.on_talk_release:
            self.on_talk_release()

    def _close(self) -> None:
        if self.on_close:
            self.on_close()
        if self.root:
            self.root.destroy()
            self.root = None

    # ------------------------------------------------------------------
    # thread-safe API
    # ------------------------------------------------------------------
    def post(self, kind: str, **payload) -> None:
        self.events.put((kind, payload))

    def set_state(self, state: str, hint: str | None = None) -> None:
        self.post("state", state=state, hint=hint)

    def show_transcript(self, text: str) -> None:
        self.post("transcript", text=text)

    def show_output(self, text: str, title: str | None = None) -> None:
        self.post("output", text=text, title=title)

    def append_output(self, text: str) -> None:
        self.post("append", text=text)

    def set_agent(self, label: str) -> None:
        self.post("agent", label=label)

    def set_status(self, text: str) -> None:
        self.post("status", text=text)

    # ------------------------------------------------------------------
    def _drain(self) -> None:
        if not self.root:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._apply(kind, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _set_text(self, widget: tk.Text, text: str, tags: tuple = ()) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text, tags)
        widget.configure(state="disabled")

    def _apply(self, kind: str, payload: dict) -> None:
        root = self.root
        if not root:
            return
        if kind == "state":
            state = payload["state"].upper()
            color = STATE_COLORS.get(state, MUTED)
            self._dot.itemconfigure(self._state_id, fill=color)
            self._state_var.set(state)
            if payload.get("hint"):
                self._hint.configure(text=payload["hint"])
            self._status_line.configure(text=state.lower())
        elif kind == "transcript":
            self._set_text(self._transcript, payload["text"])
        elif kind == "output":
            text = payload.get("text", "")
            title = payload.get("title")
            self._output.configure(state="normal")
            self._output.delete("1.0", "end")
            if title:
                self._output.insert("end", title + "\n", ("title",))
            # highlight speak:/detail: sections
            lowered = text.lower()
            if "speak:" in lowered and "detail:" in lowered:
                import re
                parts = re.split(r"(?im)^(speak|detail)\s*:\s*", text)
                # parts = ['', 'speak', ..., 'detail', ...]
                i = 1
                while i < len(parts) - 1:
                    label = parts[i].lower()
                    body = parts[i + 1]
                    tag = "ok" if label == "speak" else ()
                    if tag:
                        self._output.insert("end", f"{label.upper()}\n",
                                            ("muted",))
                        self._output.insert("end", body.strip() + "\n", tag)
                    else:
                        self._output.insert("end", "\nDETAIL\n", ("muted",))
                        self._output.insert("end", body.strip() + "\n")
                    i += 2
            else:
                self._output.insert("end", text)
            self._output.configure(state="disabled")
            self._output.see("1.0")
        elif kind == "append":
            self._output.configure(state="normal")
            self._output.insert("end", payload["text"] + "\n")
            self._output.configure(state="disabled")
            self._output.see("end")
        elif kind == "agent":
            label = payload["label"]
            color = {"working": "#d29922", "done": "#3fb46b",
                     "blocked": "#f85149", "error": "#f85149"}.get(label, MUTED)
            self._agent_pill.configure(text=f"agent: {label}", fg=color)
        elif kind == "status":
            self._status_line.configure(text=payload["text"])

    # ------------------------------------------------------------------
    def run(self) -> None:
        """Enter the Tk main loop (blocking). Builds on the calling thread."""
        self.build()
        assert self.root is not None
        self.root.after(80, self._drain)
        self.root.mainloop()
