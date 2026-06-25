#!/usr/bin/env python3
"""
Ollama Chat - a minimal desktop chat client for locally installed Ollama models.

Requirements:
    - Python 3.8+ (uses only the standard library: tkinter, urllib, json, threading)
    - Ollama running locally (default: http://localhost:11434)

Run:
    python ollama_chat.py
"""

import json
import os
import queue
import re
import threading
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import pyttsx3 as _pyttsx3
    _TTS_AVAILABLE = True
except ImportError:
    _TTS_AVAILABLE = False

try:
    import speech_recognition as _sr
    _STT_AVAILABLE = True
except ImportError:
    _STT_AVAILABLE = False

OLLAMA_HOST = "http://localhost:11434"
REQUEST_TIMEOUT = 300  # seconds
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ollama_chat_config.json")


def load_config():
    """Load persisted settings (e.g. custom instructions) from disk."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(config):
    """Persist settings to disk; failures are non-fatal."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Ollama API helpers (standard library only)
# --------------------------------------------------------------------------- #
def list_models(host=OLLAMA_HOST):
    """Return a list of model names that are downloaded in Ollama."""
    url = f"{host}/api/tags"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = [m["name"] for m in data.get("models", [])]
    return sorted(models)


def chat_stream(host, model, messages, stop_event, chunk_cb, done_cb, error_cb):
    """
    Stream a chat completion from Ollama.

    Calls chunk_cb(text) for every token chunk, done_cb() when finished,
    and error_cb(message) on failure. Honors stop_event to abort early.
    """
    url = f"{host}/api/chat"
    payload = json.dumps(
        {"model": model, "messages": messages, "stream": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            for raw_line in resp:
                if stop_event.is_set():
                    break
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                obj = json.loads(line)
                if "error" in obj:
                    error_cb(obj["error"])
                    return
                content = obj.get("message", {}).get("content", "")
                if content:
                    chunk_cb(content)
                if obj.get("done"):
                    break
        done_cb()
    except urllib.error.URLError as exc:
        error_cb(f"Connection error: {exc.reason}. Is Ollama running?")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        error_cb(f"Unexpected error: {exc}")


# --------------------------------------------------------------------------- #
# Workspace agent (Ollama tool-calling, sandboxed to a chosen folder)
# --------------------------------------------------------------------------- #
MAX_AGENT_STEPS = 12      # safety cap on tool-call rounds per message
MAX_READ_CHARS = 100_000  # cap on file content returned to the model

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and folders inside the working folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the workspace. "
                                       "Use '.' for the root.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the text content of a file in the working folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Relative path to the file."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file in the working "
                           "folder. The user must approve every write.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Relative path to the file."},
                    "content": {"type": "string",
                                "description": "Full new content of the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def agent_chat_once(host, model, messages, tools):
    """Single non-streaming chat call that may return tool_calls."""
    payload = json.dumps(
        {"model": model, "messages": messages, "tools": tools, "stream": False}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat_complete(host, model, messages):
    """Single non-streaming chat call with no tools (used for meta tasks)."""
    payload = json.dumps(
        {"model": model, "messages": messages, "stream": False}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


AUTO_UPDATE_SYSTEM = (
    "You maintain a concise set of custom instructions (a system prompt) for an "
    "AI assistant, based on an ongoing conversation with a user. Given the "
    "CURRENT INSTRUCTIONS and the RECENT CONVERSATION, output an improved "
    "version that captures the user's durable preferences, facts about them, "
    "their tone, language, and standing requests. Keep it concise (a short "
    "paragraph or a few bullet points). Do not include the conversation itself. "
    "Output ONLY the instructions text, with no preamble, no explanations, and "
    "no code fences."
)


def clean_instructions(text):
    """Strip code fences / obvious preambles the model may add."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[\w]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text


def safe_join(workspace, rel):
    """Resolve rel against workspace, refusing anything outside it."""
    full = os.path.realpath(os.path.join(workspace, rel or "."))
    root = os.path.realpath(workspace)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("Path is outside the workspace.")
    return full


# --------------------------------------------------------------------------- #
# TTS worker (runs pyttsx3 in a dedicated thread to avoid blocking the UI)
# --------------------------------------------------------------------------- #
class _TTSWorker:
    def __init__(self):
        self._q = queue.Queue()
        self._engine = None
        threading.Thread(target=self._loop, daemon=True).start()

    def speak(self, text, rate=175):
        if not _TTS_AVAILABLE:
            return
        # discard any queued-but-not-yet-spoken item before adding new one
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._q.put(("speak", text, rate))

    def stop(self):
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass

    def _loop(self):
        if not _TTS_AVAILABLE:
            return
        try:
            self._engine = _pyttsx3.init()
        except Exception:
            return
        while True:
            item = self._q.get()
            if item[0] == "speak":
                _, text, rate = item
                try:
                    self._engine.setProperty("rate", rate)
                    self._engine.say(text)
                    self._engine.runAndWait()
                except Exception:
                    pass


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class OllamaChatApp:
    # Clean minimal palette matching the screenshot
    BG        = "#f7f7f8"    # app background (light gray)
    CHAT_BG   = "#f7f7f8"   # chat transcript background
    PANEL     = "#ffffff"    # surfaces / cards
    BORDER    = "#e5e5e5"    # subtle separators
    USER_FG   = "#2563eb"    # user name accent (blue)
    BOT_FG    = "#16a34a"    # assistant name accent (green)
    TEXT_FG   = "#1a1a1a"    # primary text
    MUTED_FG  = "#9ca3af"    # hints / secondary text
    USER_BUBBLE  = "#eff6ff" # user message background
    BOT_BUBBLE   = "#ffffff" # assistant message background
    INPUT_BG  = "#ffffff"    # input field / card background
    SEND_BTN  = "#1a1a1a"    # send button circle color

    def __init__(self, root):
        self.root = root
        self.root.title("Ollama Chat")
        self.root.geometry("820x680")
        self.root.configure(bg=self.BG)
        self.root.minsize(560, 460)

        self.config = load_config()
        self.system_prompt = self.config.get("instructions", "")
        self.skills = self.config.get("skills", [])    # reusable instruction sets
        self.sources = self.config.get("sources", [])  # reference material
        self.workspace = self.config.get("workspace", "") or ""
        if self.workspace and not os.path.isdir(self.workspace):
            self.workspace = ""
        self.write_mode = self.config.get("write_mode", "confirm")  # confirm|readonly|free
        self.tts_auto = bool(self.config.get("tts_auto", False))
        self.tts_rate = int(self.config.get("tts_rate", 175))
        self.stt_lang = self.config.get("stt_lang", "he-IL")
        self._tts = _TTSWorker()
        self.auto_update = bool(self.config.get("auto_update", False))
        self.auto_update_model = self.config.get("auto_update_model", "")
        self.auto_update_every = int(self.config.get("auto_update_every", 3) or 3)
        self._reply_count = 0
        self._updating_instructions = False
        self._instr_editor = None          # live ref to the Instructions editor
        self.messages = []                 # conversation history sent to Ollama
        self.ui_queue = queue.Queue()      # thread -> main-loop communication
        self.stop_event = threading.Event()
        self.worker = None
        self.streaming = False

        self._build_ui()
        self._update_ws_label()
        self._poll_queue()
        self.refresh_models()

    # ----- layout --------------------------------------------------------- #
    def _build_ui(self):
        self.root.configure(bg=self.BG)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TButton", padding=4, relief="flat",
            background=self.PANEL, foreground=self.TEXT_FG,
            borderwidth=0, focuscolor=self.PANEL,
        )
        style.map(
            "TButton",
            background=[("active", "#ececec"), ("pressed", "#e0e0e0")],
        )
        style.configure(
            "TCombobox", fieldbackground=self.PANEL, background=self.PANEL,
            foreground=self.TEXT_FG, arrowcolor=self.MUTED_FG,
            bordercolor=self.BORDER, lightcolor=self.BORDER,
            darkcolor=self.BORDER,
        )
        style.configure(
            "Vertical.TScrollbar", background=self.BG,
            troughcolor=self.BG, bordercolor=self.BG, arrowcolor=self.BG,
            width=6,
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", "#c7c7c7")],
        )

        # ── Sidebar (left) ────────────────────────────────────────────────── #
        self.SIDEBAR  = "#f0f0f0"
        sidebar = tk.Frame(self.root, bg=self.SIDEBAR, width=200)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        tk.Frame(sidebar, bg=self.BORDER, width=1).pack(side=tk.RIGHT, fill=tk.Y)

        # "New chat" button at the top of the sidebar
        tk.Button(
            sidebar, text="✦  New chat",
            command=self.clear_chat,
            bg=self.SIDEBAR, fg=self.TEXT_FG,
            activebackground="#e2e2e2", activeforeground=self.TEXT_FG,
            relief=tk.FLAT, bd=0, padx=14, pady=10,
            font=("Segoe UI", 10, "bold"), cursor="hand2", anchor="w",
        ).pack(fill=tk.X, padx=8, pady=(12, 4))

        tk.Frame(sidebar, bg=self.BORDER, height=1).pack(fill=tk.X, padx=8, pady=4)

        # Conversation history — scrollable frame of custom rows
        tk.Label(
            sidebar, text="Recent chats", bg=self.SIDEBAR, fg=self.MUTED_FG,
            font=("Segoe UI", 8), anchor="w",
        ).pack(fill=tk.X, padx=14, pady=(4, 2))

        hist_outer = tk.Frame(sidebar, bg=self.SIDEBAR)
        hist_outer.pack(fill=tk.BOTH, expand=True, padx=4)

        hist_canvas = tk.Canvas(
            hist_outer, bg=self.SIDEBAR, highlightthickness=0, bd=0,
        )
        hist_scroll = ttk.Scrollbar(hist_outer, orient=tk.VERTICAL,
                                    command=hist_canvas.yview)
        hist_canvas.configure(yscrollcommand=hist_scroll.set)
        hist_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        hist_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._hist_inner = tk.Frame(hist_canvas, bg=self.SIDEBAR)
        self._hist_canvas_win = hist_canvas.create_window(
            (0, 0), window=self._hist_inner, anchor="nw",
        )

        def _on_inner_resize(e):
            hist_canvas.configure(scrollregion=hist_canvas.bbox("all"))
            hist_canvas.itemconfig(self._hist_canvas_win, width=e.width)

        self._hist_inner.bind("<Configure>", _on_inner_resize)
        hist_canvas.bind("<Configure>",
            lambda e: hist_canvas.itemconfig(
                self._hist_canvas_win, width=e.width))

        # mousewheel scroll inside the history area
        def _on_wheel(e):
            hist_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        hist_canvas.bind_all("<MouseWheel>", _on_wheel)

        self._hist_rows = []   # list of tk.Frame widgets (one per conversation)

        # Bottom sidebar buttons: Settings
        tk.Frame(sidebar, bg=self.BORDER, height=1).pack(fill=tk.X, padx=8, pady=4)
        for label, cmd in (
            ("⚙  Settings", self.open_settings),
        ):
            tk.Button(
                sidebar, text=label, command=cmd,
                bg=self.SIDEBAR, fg=self.TEXT_FG,
                activebackground="#e2e2e2",
                relief=tk.FLAT, bd=0, padx=14, pady=8,
                font=("Segoe UI", 9), cursor="hand2", anchor="w",
            ).pack(fill=tk.X, padx=8, pady=(0, 4))

        # conversation history storage
        self._history      = []   # list of saved message lists
        self._hist_names   = []   # matching display names
        self._active_hist  = -1   # index of currently displayed conversation (-1 = new)

        # ── Right pane (top-bar + chat + input) ───────────────────────────── #
        right = tk.Frame(self.root, bg=self.BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Top bar ───────────────────────────────────────────────────────── #
        top = tk.Frame(right, bg=self.PANEL, pady=0)
        top.pack(side=tk.TOP, fill=tk.X)

        self.ws_label = tk.Label(
            top, text="New conversation", bg=self.PANEL, fg=self.MUTED_FG,
            font=("Segoe UI", 9), cursor="hand2",
        )
        self.ws_label.pack(side=tk.LEFT, padx=14, pady=8)

        self.status = tk.Label(
            top, text="", bg=self.PANEL, fg=self.MUTED_FG, font=("Segoe UI", 8)
        )
        self.status.pack(side=tk.RIGHT, padx=12)

        tk.Frame(right, bg=self.BORDER, height=1).pack(side=tk.TOP, fill=tk.X)

        # ── Chat transcript ────────────────────────────────────────────────── #
        chat_wrap = tk.Frame(right, bg=self.CHAT_BG)
        chat_wrap.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(chat_wrap, orient=tk.VERTICAL)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 2))

        self.chat = tk.Text(
            chat_wrap, wrap=tk.WORD, bg=self.CHAT_BG, fg=self.TEXT_FG,
            insertbackground=self.TEXT_FG, relief=tk.FLAT, bd=0,
            font=("Segoe UI", 11), padx=24, pady=16, state=tk.DISABLED,
            yscrollcommand=scroll.set,
        )
        self.chat.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.configure(command=self.chat.yview)

        self.chat.tag_configure(
            "user", foreground=self.USER_FG,
            font=("Segoe UI", 9, "bold"),
            spacing1=16, spacing3=2,
        )
        self.chat.tag_configure(
            "bot", foreground=self.MUTED_FG,
            font=("Segoe UI", 9, "bold"),
            spacing1=16, spacing3=2,
        )
        self.chat.tag_configure(
            "user_body", foreground=self.TEXT_FG, background=self.USER_BUBBLE,
            spacing1=4, spacing3=10, lmargin1=0, lmargin2=0, rmargin=0,
        )
        self.chat.tag_configure(
            "body", foreground=self.TEXT_FG, background=self.BOT_BUBBLE,
            spacing1=4, spacing3=10, lmargin1=0, lmargin2=0, rmargin=0,
        )
        self.chat.tag_configure("spacer", background=self.CHAT_BG)
        self.chat.tag_configure(
            "tool", foreground="#b45309", font=("Consolas", 9),
            spacing1=2,
        )

        self._add_context_menu(self.chat)
        self.chat.bind("<Control-c>", lambda e: self._copy_selection(self.chat))
        self.chat.bind("<Control-C>", lambda e: self._copy_selection(self.chat))

        # ── Input card ────────────────────────────────────────────────────── #
        # Outer padding frame (gray background shows as page bg)
        pad = tk.Frame(right, bg=self.BG)
        pad.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(8, 16))

        # White card with border
        card = tk.Frame(
            pad, bg=self.INPUT_BG,
            highlightthickness=1, highlightbackground=self.BORDER,
            highlightcolor="#a5b4fc",
        )
        card.pack(fill=tk.X)

        # Text area
        self.entry = tk.Text(
            card, height=3, wrap=tk.WORD,
            bg=self.INPUT_BG, fg=self.TEXT_FG,
            insertbackground=self.TEXT_FG,
            relief=tk.FLAT, bd=0,
            font=("Segoe UI", 11), padx=14, pady=12,
        )
        self.entry.pack(fill=tk.BOTH, expand=True)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Shift-Return>", lambda e: None)

        # placeholder
        self._placeholder_active = False
        self._show_placeholder()
        self.entry.bind("<FocusIn>",  self._hide_placeholder)
        self.entry.bind("<FocusOut>", self._show_placeholder)

        # Bottom toolbar inside the card
        toolbar = tk.Frame(card, bg=self.INPUT_BG)
        toolbar.pack(fill=tk.X, padx=10, pady=(0, 8))

        # Mic button (left)
        self.mic_btn = tk.Button(
            toolbar, text="🎤",
            command=self._start_stt,
            bg=self.INPUT_BG, fg=self.MUTED_FG,
            activebackground="#f3f4f6", activeforeground=self.TEXT_FG,
            relief=tk.FLAT, bd=0, font=("Segoe UI", 13),
            cursor="hand2" if _STT_AVAILABLE else "arrow",
            state=tk.NORMAL if _STT_AVAILABLE else tk.DISABLED,
        )
        self.mic_btn.pack(side=tk.LEFT)

        # Send / Stop button (circular) — packed FIRST so it sits at far right
        self._send_canvas = tk.Canvas(
            toolbar, width=34, height=34,
            bg=self.INPUT_BG, highlightthickness=0,
        )
        self._send_canvas.pack(side=tk.RIGHT, padx=(10, 2))
        self._draw_send_btn(active=True)
        self._send_canvas.bind("<Button-1>", lambda e: self._on_send_click())
        self._send_canvas.configure(cursor="hand2")

        def _hover(_):
            col = "#ef4444" if self.streaming else "#404040"
            self._send_canvas.itemconfig("circle", fill=col, outline=col)

        def _leave(_):
            col = "#ef4444" if self.streaming else self.SEND_BTN
            self._send_canvas.itemconfig("circle", fill=col, outline=col)

        self._send_canvas.bind("<Enter>", _hover)
        self._send_canvas.bind("<Leave>", _leave)

        # Model selector + refresh (to the LEFT of the send button)
        self.model_var = tk.StringVar()
        model_frame = tk.Frame(toolbar, bg=self.INPUT_BG)
        model_frame.pack(side=tk.RIGHT, padx=(0, 8))

        # Refresh button (rightmost inside the model group)
        tk.Button(
            model_frame, text="↻",
            command=self.refresh_models,
            bg=self.INPUT_BG, fg=self.MUTED_FG,
            activebackground="#f3f4f6",
            relief=tk.FLAT, bd=0, font=("Segoe UI", 11),
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(6, 0))

        self.model_combo = ttk.Combobox(
            model_frame, textvariable=self.model_var,
            state="readonly", width=16,
            font=("Segoe UI", 9),
        )
        self.model_combo.pack(side=tk.RIGHT)

        # stop functionality wired through same canvas button
        self.send_btn  = self._send_canvas   # keep API compat (state changes below)
        self.stop_btn  = self._send_canvas   # same widget

    def _draw_circle(self, color):
        """Draw a filled rounded square (smooth spline corners)."""
        c = self._send_canvas
        x0, y0, x1, y1, r = 3, 3, 31, 31, 9
        pts = [
            x0 + r, y0,  x1 - r, y0,  x1, y0,  x1, y0 + r,
            x1, y1 - r,  x1, y1,  x1 - r, y1,  x0 + r, y1,
            x0, y1,  x0, y1 - r,  x0, y0 + r,  x0, y0,
        ]
        c.create_polygon(
            pts, fill=color, outline=color, smooth=True, tags="circle",
        )

    def _draw_send_btn(self, active=True):
        c = self._send_canvas
        c.delete("all")
        self._draw_circle(self.SEND_BTN if active else "#ef4444")
        # clean up-arrow: filled triangle head + rounded stem
        c.create_polygon(
            17, 9, 11, 17, 23, 17,
            fill="white", outline="white", tags="arrow",
        )
        c.create_line(
            17, 15, 17, 25, fill="white", width=3,
            capstyle=tk.ROUND, tags="arrow",
        )

    def _on_send_click(self):
        if self.streaming:
            self.stop()
        else:
            self.send()

    def _set_busy(self, busy: bool):
        """Toggle the send/stop canvas button appearance."""
        c = self._send_canvas
        c.delete("all")
        if busy:
            self._draw_circle("#ef4444")
            # rounded stop square
            c.create_rectangle(
                12, 12, 22, 22, fill="white", outline="white", tags="arrow",
            )
        else:
            self._draw_send_btn(active=True)

    def _show_placeholder(self, event=None):
        if not self.entry.get("1.0", "end-1c"):
            self._placeholder_active = True
            self.entry.insert("1.0", "Type your question here...")
            self.entry.configure(fg="#b0b0b0")

    def _hide_placeholder(self, event=None):
        if self._placeholder_active:
            self.entry.delete("1.0", tk.END)
            self.entry.configure(fg=self.TEXT_FG)
            self._placeholder_active = False

    # ----- model handling ------------------------------------------------- #
    def refresh_models(self):
        self.set_status("Loading models...")

        def work():
            try:
                models = list_models()
                self.ui_queue.put(("models", models))
            except Exception as exc:  # noqa: BLE001
                self.ui_queue.put(("models_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_models(self, models):
        if not models:
            self.set_status("No models found - run 'ollama pull <model>'")
            self.model_combo["values"] = []
            return
        self.model_combo["values"] = models
        if self.model_var.get() not in models:
            self.model_var.set(models[0])
        self.set_status(f"{len(models)} model(s) available")

    # ----- chat transcript helpers ---------------------------------------- #
    def _append(self, text, tag="body"):
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, text, tag)
        self.chat.see(tk.END)
        self.chat.configure(state=tk.DISABLED)

    def clear_chat(self):
        if self.streaming:
            return
        if self.messages:
            self._save_to_history()
        self.messages.clear()
        self._active_hist = -1
        self.chat.configure(state=tk.NORMAL)
        self.chat.delete("1.0", tk.END)
        self.chat.configure(state=tk.DISABLED)
        self.ws_label.configure(text="New conversation")
        self.set_status("New conversation")
        self._rebuild_hist_rows()

    def _save_to_history(self):
        """Snapshot current conversation. If already editing one, update in place."""
        first_user = next(
            (m["content"] for m in self.messages if m["role"] == "user"), None
        )
        if not first_user:
            return
        title = first_user[:38].replace("\n", " ")
        if len(first_user) > 38:
            title += "…"

        if self._active_hist >= 0:
            # update existing slot
            self._history[self._active_hist]    = list(self.messages)
            self._hist_names[self._active_hist] = title
        else:
            self._history.insert(0, list(self.messages))
            self._hist_names.insert(0, title)
            self._history    = self._history[:30]
            self._hist_names = self._hist_names[:30]
            self._active_hist = 0
        self._rebuild_hist_rows()

    def _rebuild_hist_rows(self):
        """Rebuild the scrollable sidebar rows from scratch."""
        for w in self._hist_rows:
            w.destroy()
        self._hist_rows.clear()

        for idx, name in enumerate(self._hist_names):
            active = (idx == self._active_hist)
            row_bg = "#dbeafe" if active else self.SIDEBAR
            row = tk.Frame(self._hist_inner, bg=row_bg, cursor="hand2")
            row.pack(fill=tk.X, pady=1)
            self._hist_rows.append(row)

            name_btn = tk.Button(
                row, text=name,
                bg=row_bg, fg=self.TEXT_FG,
                activebackground="#bfdbfe",
                relief=tk.FLAT, bd=0,
                font=("Segoe UI", 9), anchor="w",
                padx=10, pady=5,
                command=lambda i=idx: self._on_hist_select(i),
            )
            name_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

            dots_btn = tk.Button(
                row, text="⋯",
                bg=row_bg, fg=self.MUTED_FG,
                activebackground="#bfdbfe",
                relief=tk.FLAT, bd=0,
                font=("Segoe UI", 11), padx=6, pady=4,
                command=lambda i=idx, b=row: self._hist_menu(i, b),
            )
            dots_btn.pack(side=tk.RIGHT)

            # hover highlight
            for w in (row, name_btn, dots_btn):
                w.bind("<Enter>", lambda e, r=row, nb=name_btn, db=dots_btn:
                    [x.configure(bg="#dbeafe") for x in (r, nb, db)])
                w.bind("<Leave>", lambda e, r=row, nb=name_btn, db=dots_btn,
                       i=idx: [x.configure(
                           bg="#dbeafe" if i == self._active_hist else self.SIDEBAR
                       ) for x in (r, nb, db)])

    def _hist_menu(self, idx, anchor_widget):
        """Show rename / delete popup for conversation at idx."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Rename…",
                         command=lambda: self._hist_rename(idx))
        menu.add_command(label="Delete",
                         command=lambda: self._hist_delete(idx))
        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _hist_rename(self, idx):
        dlg = tk.Toplevel(self.root)
        dlg.title("Rename conversation")
        dlg.configure(bg=self.BG)
        dlg.geometry("360x120")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="New name:", bg=self.BG, fg=self.TEXT_FG,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(14, 4))
        var = tk.StringVar(value=self._hist_names[idx])
        ent = tk.Entry(dlg, textvariable=var, font=("Segoe UI", 10),
                       bg=self.INPUT_BG, fg=self.TEXT_FG,
                       relief=tk.SOLID, borderwidth=1)
        ent.pack(fill=tk.X, padx=14)
        ent.select_range(0, tk.END)
        ent.focus_set()
        bar = tk.Frame(dlg, bg=self.BG)
        bar.pack(fill=tk.X, padx=14, pady=10)

        def ok():
            n = var.get().strip()
            if n:
                self._hist_names[idx] = n
                self._rebuild_hist_rows()
                if idx == self._active_hist:
                    self.ws_label.configure(text=n)
            dlg.destroy()

        ttk.Button(bar, text="Save", command=ok).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=(0, 8))
        ent.bind("<Return>", lambda e: ok())
        dlg.wait_window()

    def _hist_delete(self, idx):
        del self._history[idx]
        del self._hist_names[idx]
        if self._active_hist == idx:
            self._active_hist = -1
            self.ws_label.configure(text="New conversation")
        elif self._active_hist > idx:
            self._active_hist -= 1
        self._rebuild_hist_rows()

    def _on_hist_select(self, idx):
        if self.streaming:
            return
        if idx >= len(self._history):
            return
        # auto-save current unsaved work before switching
        if self.messages and self._active_hist == -1:
            self._save_to_history()
        self._active_hist = idx
        self.messages = list(self._history[idx])
        self.chat.configure(state=tk.NORMAL)
        self.chat.delete("1.0", tk.END)
        self.chat.configure(state=tk.DISABLED)
        for m in self.messages:
            if m["role"] == "user":
                self._append("You\n", "user")
                self._append(m["content"] + "\n", "user_body")
            elif m["role"] == "assistant":
                self._append("Assistant\n", "bot")
                self.chat.configure(state=tk.NORMAL)
                self._insert_markdown(m["content"])
                self.chat.insert(tk.END, "\n")
                self.chat.configure(state=tk.DISABLED)
            self._append("\n", "spacer")
        self.ws_label.configure(text=self._hist_names[idx])
        self._rebuild_hist_rows()   # update highlight

    # ----- settings: persistence & prompt building ------------------------ #
    def _persist(self):
        self.config["instructions"] = self.system_prompt
        self.config["skills"] = self.skills
        self.config["sources"] = self.sources
        self.config["auto_update"] = self.auto_update
        self.config["auto_update_model"] = self.auto_update_model
        self.config["auto_update_every"] = self.auto_update_every
        self.config["tts_auto"] = self.tts_auto
        self.config["tts_rate"] = self.tts_rate
        self.config["stt_lang"] = self.stt_lang
        save_config(self.config)

    def _read_source(self, src):
        """Return the text content of a source, reading files fresh each time."""
        if src.get("type") == "text":
            return src.get("content", "").strip()
        path = src.get("path")
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read().strip()
            except OSError:
                return ""
        return ""

    def build_system_prompt(self):
        """Combine instructions + enabled skills + enabled sources."""
        parts = []
        if self.system_prompt.strip():
            parts.append(self.system_prompt.strip())
        for sk in self.skills:
            if sk.get("enabled") and sk.get("content", "").strip():
                parts.append(
                    f"## Skill: {sk.get('name', 'Skill')}\n{sk['content'].strip()}"
                )
        src_blocks = []
        for src in self.sources:
            if not src.get("enabled"):
                continue
            text = self._read_source(src)
            if text:
                src_blocks.append(f"### Source: {src.get('name', 'source')}\n{text}")
        if src_blocks:
            parts.append(
                "Use the following reference sources to answer the user:\n\n"
                + "\n\n".join(src_blocks)
            )
        return "\n\n".join(parts)

    # ----- settings: window ----------------------------------------------- #
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.configure(bg=self.BG)
        win.geometry("600x540")
        win.transient(self.root)
        win.grab_set()

        style = ttk.Style()
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab", padding=(16, 7), background=self.PANEL,
            foreground=self.TEXT_FG, font=("Segoe UI", 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.BG)],
            foreground=[("selected", self.USER_FG)],
        )

        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 4))
        self._build_instructions_tab(nb)
        self._build_skills_tab(nb)
        self._build_sources_tab(nb)
        self._build_voice_tab(nb)
        self._build_workspace_tab(nb)

        ttk.Button(win, text="Close", command=win.destroy).pack(
            side=tk.BOTTOM, anchor="e", padx=14, pady=(0, 12)
        )

    def _make_textbox(self, parent, **kw):
        return tk.Text(
            parent, wrap=tk.WORD, bg=self.INPUT_BG, fg=self.TEXT_FG,
            insertbackground=self.TEXT_FG, relief=tk.SOLID, borderwidth=1,
            highlightthickness=1, highlightbackground=self.BORDER,
            highlightcolor=self.USER_FG, font=("Segoe UI", 11),
            padx=8, pady=8, **kw
        )

    def _make_listbox(self, parent):
        return tk.Listbox(
            parent, bg=self.INPUT_BG, fg=self.TEXT_FG, relief=tk.SOLID,
            borderwidth=1, highlightthickness=0, font=("Segoe UI", 10),
            activestyle="none", selectbackground="#dbe7ff",
            selectforeground=self.TEXT_FG,
        )

    def _build_instructions_tab(self, nb):
        tab = tk.Frame(nb, bg=self.BG)
        nb.add(tab, text="Instructions")
        tk.Label(
            tab,
            text="Custom instructions the model follows in every chat "
                 "(system prompt).",
            bg=self.BG, fg=self.MUTED_FG, font=("Segoe UI", 9),
            wraplength=520, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        editor = self._make_textbox(tab)
        editor.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        editor.insert("1.0", self.system_prompt)
        editor.focus_set()
        self._instr_editor = editor  # so auto-update can refresh the text live

        bar = tk.Frame(tab, bg=self.BG)
        bar.pack(fill=tk.X, padx=12, pady=(0, 6))

        def save():
            self.system_prompt = editor.get("1.0", tk.END).strip()
            self._persist()
            self.set_status("Instructions saved")

        ttk.Button(bar, text="Save", command=save).pack(side=tk.RIGHT)
        ttk.Button(
            bar, text="Clear", command=lambda: editor.delete("1.0", tk.END)
        ).pack(side=tk.LEFT)

        # --- auto-update from conversation --- #
        tk.Frame(tab, bg=self.BORDER, height=1).pack(fill=tk.X, padx=12, pady=4)
        au = tk.Frame(tab, bg=self.BG)
        au.pack(fill=tk.X, padx=12, pady=(2, 10))

        self._au_enabled = tk.BooleanVar(value=self.auto_update)
        self._au_model = tk.StringVar(value=self.auto_update_model)
        self._au_every = tk.StringVar(value=str(self.auto_update_every))

        def save_au(*_):
            self.auto_update = self._au_enabled.get()
            self.auto_update_model = self._au_model.get().strip()
            try:
                self.auto_update_every = max(1, int(self._au_every.get()))
            except ValueError:
                self.auto_update_every = 3
                self._au_every.set("3")
            self._persist()

        tk.Checkbutton(
            au, text="Auto-update instructions from the conversation",
            variable=self._au_enabled, command=save_au, bg=self.BG,
            fg=self.TEXT_FG, activebackground=self.BG, selectcolor=self.PANEL,
            font=("Segoe UI", 10, "bold"), anchor="w",
        ).pack(anchor="w")
        tk.Label(
            au,
            text="A model reviews the chat and rewrites the instructions above "
                 "to remember your preferences.",
            bg=self.BG, fg=self.MUTED_FG, font=("Segoe UI", 8),
            wraplength=520, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        row = tk.Frame(au, bg=self.BG)
        row.pack(anchor="w")
        tk.Label(
            row, text="Model:", bg=self.BG, fg=self.TEXT_FG,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)
        models = list(self.model_combo["values"])
        au_combo = ttk.Combobox(
            row, textvariable=self._au_model, values=models, width=22,
            state="readonly",
        )
        au_combo.pack(side=tk.LEFT, padx=(4, 12))
        au_combo.bind("<<ComboboxSelected>>", save_au)
        tk.Label(
            row, text="every", bg=self.BG, fg=self.TEXT_FG,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)
        every_spin = tk.Spinbox(
            row, from_=1, to=50, width=4, textvariable=self._au_every,
            command=save_au, relief=tk.SOLID, borderwidth=1,
            bg=self.INPUT_BG, fg=self.TEXT_FG,
        )
        every_spin.pack(side=tk.LEFT, padx=4)
        every_spin.bind("<FocusOut>", save_au)
        tk.Label(
            row, text="replies", bg=self.BG, fg=self.TEXT_FG,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

    def _item_dialog(self, title, name_val="", content_val="",
                     content_label="Content"):
        """Modal editor for a name + multiline content. Returns dict or None."""
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=self.BG)
        dlg.geometry("520x440")
        dlg.transient(self.root)
        dlg.grab_set()
        result = {}

        tk.Label(
            dlg, text="Name", bg=self.BG, fg=self.TEXT_FG,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(14, 2))
        name_entry = tk.Entry(
            dlg, bg=self.INPUT_BG, fg=self.TEXT_FG,
            insertbackground=self.TEXT_FG, relief=tk.SOLID, borderwidth=1,
            font=("Segoe UI", 11),
        )
        name_entry.pack(fill=tk.X, padx=14)
        name_entry.insert(0, name_val)

        tk.Label(
            dlg, text=content_label, bg=self.BG, fg=self.TEXT_FG,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(10, 2))
        content = self._make_textbox(dlg)
        content.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        content.insert("1.0", content_val)
        name_entry.focus_set()

        bar = tk.Frame(dlg, bg=self.BG)
        bar.pack(fill=tk.X, padx=14, pady=10)

        def ok():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning(
                    "Name required", "Please enter a name.", parent=dlg
                )
                return
            result["name"] = name
            result["content"] = content.get("1.0", tk.END).strip()
            dlg.destroy()

        ttk.Button(bar, text="Save", command=ok).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        dlg.wait_window()
        return result or None

    def _build_skills_tab(self, nb):
        tab = tk.Frame(nb, bg=self.BG)
        nb.add(tab, text="Skills")
        tk.Label(
            tab,
            text="Reusable instruction sets. Enabled skills are added to "
                 "every chat.",
            bg=self.BG, fg=self.MUTED_FG, font=("Segoe UI", 9),
            wraplength=520, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        lb = self._make_listbox(tab)
        lb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        def refresh():
            lb.delete(0, tk.END)
            for sk in self.skills:
                mark = "✓" if sk.get("enabled") else "○"
                lb.insert(tk.END, f"  {mark}   {sk.get('name', '(unnamed)')}")

        def selected():
            sel = lb.curselection()
            return sel[0] if sel else None

        def add():
            r = self._item_dialog("New skill", content_label="Skill instructions")
            if r:
                self.skills.append(
                    {"name": r["name"], "content": r["content"], "enabled": True}
                )
                self._persist()
                refresh()

        def edit():
            i = selected()
            if i is None:
                return
            sk = self.skills[i]
            r = self._item_dialog(
                "Edit skill", sk["name"], sk.get("content", ""),
                "Skill instructions",
            )
            if r:
                sk["name"], sk["content"] = r["name"], r["content"]
                self._persist()
                refresh()

        def toggle():
            i = selected()
            if i is None:
                return
            self.skills[i]["enabled"] = not self.skills[i].get("enabled")
            self._persist()
            refresh()

        def delete():
            i = selected()
            if i is None:
                return
            del self.skills[i]
            self._persist()
            refresh()

        lb.bind("<Double-Button-1>", lambda e: edit())
        bar = tk.Frame(tab, bg=self.BG)
        bar.pack(fill=tk.X, padx=12, pady=(0, 10))
        ttk.Button(bar, text="Add", command=add).pack(side=tk.LEFT)
        ttk.Button(bar, text="Edit", command=edit).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="On/Off", command=toggle).pack(side=tk.LEFT)
        ttk.Button(bar, text="Delete", command=delete).pack(side=tk.RIGHT)
        refresh()

    def _build_sources_tab(self, nb):
        tab = tk.Frame(nb, bg=self.BG)
        nb.add(tab, text="Sources")
        tk.Label(
            tab,
            text="Reference material (files or pasted text) given to the "
                 "model as context. Files are re-read on each message.",
            bg=self.BG, fg=self.MUTED_FG, font=("Segoe UI", 9),
            wraplength=520, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        lb = self._make_listbox(tab)
        lb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        def refresh():
            lb.delete(0, tk.END)
            for src in self.sources:
                mark = "✓" if src.get("enabled") else "○"
                kind = src.get("type", "text")
                lb.insert(
                    tk.END,
                    f"  {mark}   {src.get('name', '(unnamed)')}   ·  {kind}",
                )

        def selected():
            sel = lb.curselection()
            return sel[0] if sel else None

        def add_file():
            path = filedialog.askopenfilename(
                parent=tab.winfo_toplevel(),
                title="Choose a text file",
                filetypes=[
                    ("Text files",
                     "*.txt *.md *.py *.js *.ts *.json *.csv *.html *.css "
                     "*.java *.c *.cpp *.go *.rs *.yml *.yaml *.xml *.log *.ini"),
                    ("All files", "*.*"),
                ],
            )
            if not path:
                return
            self.sources.append(
                {"name": os.path.basename(path), "type": "file",
                 "path": path, "enabled": True}
            )
            self._persist()
            refresh()

        def add_text():
            r = self._item_dialog("Add text source", content_label="Source text")
            if r:
                self.sources.append(
                    {"name": r["name"], "type": "text",
                     "content": r["content"], "enabled": True}
                )
                self._persist()
                refresh()

        def edit():
            i = selected()
            if i is None:
                return
            src = self.sources[i]
            if src.get("type") == "text":
                r = self._item_dialog(
                    "Edit source", src.get("name", ""),
                    src.get("content", ""), "Source text",
                )
                if r:
                    src["name"], src["content"] = r["name"], r["content"]
                    self._persist()
                    refresh()
            else:
                messagebox.showinfo(
                    "File source",
                    f"Path:\n{src.get('path')}\n\n"
                    "This file is read fresh each time you send a message.",
                    parent=tab.winfo_toplevel(),
                )

        def toggle():
            i = selected()
            if i is None:
                return
            self.sources[i]["enabled"] = not self.sources[i].get("enabled")
            self._persist()
            refresh()

        def delete():
            i = selected()
            if i is None:
                return
            del self.sources[i]
            self._persist()
            refresh()

        lb.bind("<Double-Button-1>", lambda e: edit())
        bar = tk.Frame(tab, bg=self.BG)
        bar.pack(fill=tk.X, padx=12, pady=(0, 10))
        ttk.Button(bar, text="Add file…", command=add_file).pack(side=tk.LEFT)
        ttk.Button(bar, text="Add text…", command=add_text).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(bar, text="Edit", command=edit).pack(side=tk.LEFT)
        ttk.Button(bar, text="On/Off", command=toggle).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Delete", command=delete).pack(side=tk.RIGHT)
        refresh()

    def _build_voice_tab(self, nb):
        tab = tk.Frame(nb, bg=self.BG)
        nb.add(tab, text="Voice")

        def note(text):
            tk.Label(
                tab, text=text, bg=self.BG, fg=self.MUTED_FG,
                font=("Segoe UI", 9), wraplength=520, justify="left",
            ).pack(anchor="w", padx=12, pady=(0, 4))

        # --- TTS section ---
        tk.Label(
            tab, text="Text-to-Speech (TTS)", bg=self.BG, fg=self.TEXT_FG,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(14, 2))

        if not _TTS_AVAILABLE:
            note("pyttsx3 is not installed. Run:  pip install pyttsx3")
        else:
            note("הוסף כפתור 🔊 לכל תשובה של המודל.")
            _au_tts = tk.BooleanVar(value=self.tts_auto)

            def toggle_auto():
                self.tts_auto = _au_tts.get()
                self._persist()

            tk.Checkbutton(
                tab, text="השמע תשובות אוטומטית",
                variable=_au_tts, command=toggle_auto,
                bg=self.BG, fg=self.TEXT_FG, activebackground=self.BG,
                selectcolor=self.PANEL, font=("Segoe UI", 10), anchor="w",
            ).pack(anchor="w", padx=12)

            row = tk.Frame(tab, bg=self.BG)
            row.pack(anchor="w", padx=12, pady=(6, 2))
            tk.Label(row, text="מהירות דיבור:", bg=self.BG,
                     fg=self.TEXT_FG, font=("Segoe UI", 9)).pack(side=tk.LEFT)
            _rate = tk.IntVar(value=self.tts_rate)
            rate_spin = tk.Spinbox(
                row, from_=80, to=300, width=5, textvariable=_rate,
                relief=tk.SOLID, borderwidth=1,
                bg=self.INPUT_BG, fg=self.TEXT_FG,
            )
            rate_spin.pack(side=tk.LEFT, padx=6)
            tk.Label(row, text="מילים לדקה", bg=self.BG,
                     fg=self.MUTED_FG, font=("Segoe UI", 9)).pack(side=tk.LEFT)

            def save_rate(*_):
                try:
                    self.tts_rate = max(80, min(300, int(_rate.get())))
                except ValueError:
                    pass
                self._persist()

            rate_spin.bind("<FocusOut>", save_rate)
            rate_spin.configure(command=save_rate)

        # --- STT section ---
        tk.Frame(tab, bg=self.BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)
        tk.Label(
            tab, text="Speech-to-Text (STT) — כפתור 🎤", bg=self.BG,
            fg=self.TEXT_FG, font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(0, 2))

        if not _STT_AVAILABLE:
            note("speech_recognition ו-pyaudio אינם מותקנים. הרץ:\n"
                 "pip install SpeechRecognition pyaudio")
        else:
            note("לחץ 🎤 בזמן הקלדה כדי להכתיב טקסט מהמיקרופון.")
            STT_LANGS = [
                ("עברית (he-IL)", "he-IL"),
                ("English (en-US)", "en-US"),
                ("Arabic (ar-SA)", "ar-SA"),
                ("Français (fr-FR)", "fr-FR"),
                ("Deutsch (de-DE)", "de-DE"),
                ("Español (es-ES)", "es-ES"),
            ]
            lang_labels = [l for l, _ in STT_LANGS]
            lang_codes = [c for _, c in STT_LANGS]
            cur_idx = lang_codes.index(self.stt_lang) if self.stt_lang in lang_codes else 0
            _lang_var = tk.StringVar(value=lang_labels[cur_idx])

            row2 = tk.Frame(tab, bg=self.BG)
            row2.pack(anchor="w", padx=12, pady=(4, 2))
            tk.Label(row2, text="שפת זיהוי:", bg=self.BG,
                     fg=self.TEXT_FG, font=("Segoe UI", 9)).pack(side=tk.LEFT)
            lang_combo = ttk.Combobox(
                row2, textvariable=_lang_var, values=lang_labels,
                state="readonly", width=20,
            )
            lang_combo.pack(side=tk.LEFT, padx=6)

            def on_lang(*_):
                label = _lang_var.get()
                if label in lang_labels:
                    self.stt_lang = lang_codes[lang_labels.index(label)]
                    self._persist()

            lang_combo.bind("<<ComboboxSelected>>", on_lang)

    # ----- sending -------------------------------------------------------- #
    def _on_return(self, event):
        if event.state & 0x0001:  # Shift held -> newline
            return
        self.send()
        return "break"

    def send(self):
        if self.streaming:
            return
        model = self.model_var.get()
        if not model:
            messagebox.showwarning("No model", "Please select a model first.")
            return
        if self._placeholder_active:
            return
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return

        self.entry.delete("1.0", tk.END)
        self._placeholder_active = False
        self.messages.append({"role": "user", "content": text})
        self._append("You\n", "user")
        self._append(text + "\n", "user_body")
        self._append("\n", "spacer")

        if self.workspace:
            self._start_agent(model)
            return

        self._append(f"{model}\n", "bot")

        # mark where the assistant reply begins so we can reformat it on done
        self.chat.configure(state=tk.NORMAL)
        self.chat.mark_set("reply_start", "end-1c")
        self.chat.mark_gravity("reply_start", "left")
        self.chat.configure(state=tk.DISABLED)

        self.streaming = True
        self.stop_event.clear()
        self._set_busy(True)
        
        self.set_status("Generating...")
        self._bot_reply = ""

        outgoing = list(self.messages)
        system = self.build_system_prompt()
        if system:
            outgoing = [{"role": "system", "content": system}] + outgoing

        self.worker = threading.Thread(
            target=chat_stream,
            args=(
                OLLAMA_HOST, model, outgoing, self.stop_event,
                lambda c: self.ui_queue.put(("chunk", c)),
                lambda: self.ui_queue.put(("done", None)),
                lambda e: self.ui_queue.put(("error", e)),
            ),
            daemon=True,
        )
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.set_status("Stopping...")

    # ----- main-loop queue pump ------------------------------------------- #
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "models":
                    self._apply_models(payload)
                elif kind == "models_error":
                    self.set_status("Cannot reach Ollama")
                    messagebox.showerror(
                        "Ollama not reachable",
                        f"Could not load models from {OLLAMA_HOST}.\n\n{payload}\n\n"
                        "Make sure Ollama is installed and running.",
                    )
                elif kind == "chunk":
                    self._bot_reply += payload
                    self._append(payload, "body")
                elif kind == "done":
                    self._finish_reply()
                elif kind == "tool_call":
                    self._append(f"🔧 {payload}\n", "tool")
                elif kind == "confirm_write":
                    full, content, event, holder = payload
                    preview = content if len(content) <= 1500 \
                        else content[:1500] + "\n...[truncated]"
                    holder["approved"] = messagebox.askyesno(
                        "Allow file write?",
                        f"The model wants to write this file:\n\n{full}\n\n"
                        f"----- content -----\n{preview}",
                    )
                    event.set()
                elif kind == "agent_done":
                    self._finish_agent(payload)
                elif kind == "instructions_updated":
                    self.system_prompt = payload
                    self._persist()
                    self._updating_instructions = False
                    if self._instr_editor is not None:
                        try:
                            self._instr_editor.delete("1.0", tk.END)
                            self._instr_editor.insert("1.0", payload)
                        except tk.TclError:
                            self._instr_editor = None
                    self.set_status("Instructions updated from chat")
                elif kind == "instructions_idle":
                    self._updating_instructions = False
                    self.set_status("Ready")
                elif kind == "stt_result":
                    self.entry.insert(tk.END, payload)
                    self.mic_btn.configure(state=tk.NORMAL)
                    self.set_status("Ready")
                elif kind == "stt_error":
                    self.mic_btn.configure(state=tk.NORMAL)
                    self.set_status(f"שגיאת זיהוי: {payload}")
                elif kind == "error":
                    self._append(f"\n[Error] {payload}\n", "body")
                    if self.streaming and self.workspace:
                        self._append("\n", "spacer")
                        self.streaming = False
                        self._set_busy(False)
                        
                        self.set_status("Finished with error")
                    else:
                        self._finish_reply(error=True)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_queue)

    def _finish_reply(self, error=False):
        if not error:
            self._render_reply(self._bot_reply)
        if self._bot_reply.strip():
            self.messages.append(
                {"role": "assistant", "content": self._bot_reply}
            )
        self._append("\n\n", "spacer")
        self.streaming = False
        self._set_busy(False)
        
        self.set_status("Ready" if not error else "Finished with error")
        if not error:
            self._save_to_history()   # update sidebar after each reply
            self._maybe_auto_update()
            if self.tts_auto and self._bot_reply.strip():
                self._speak(self._bot_reply)

    # ----- rich rendering (code blocks + copy) ---------------------------- #
    def _render_reply(self, text):
        """Replace the plain streamed reply with a markdown-formatted version."""
        self.chat.configure(state=tk.NORMAL)
        self.chat.delete("reply_start", "end-1c")
        self._insert_markdown(text)
        # per-message action buttons (copy + speak)
        self.chat.insert(tk.END, "\n")
        copy_btn = tk.Button(
            self.chat, text="⧉ Copy", relief=tk.FLAT, bg=self.BG,
            fg=self.MUTED_FG, activebackground=self.PANEL,
            font=("Segoe UI", 8), cursor="hand2", bd=0,
            command=lambda t=text: self._copy_text(t, "Reply copied"),
        )
        self.chat.window_create(tk.END, window=copy_btn)
        if _TTS_AVAILABLE:
            speak_btn = tk.Button(
                self.chat, text="🔊", relief=tk.FLAT, bg=self.BG,
                fg=self.MUTED_FG, activebackground=self.PANEL,
                font=("Segoe UI", 8), cursor="hand2", bd=0,
                command=lambda t=text: self._speak(t),
            )
            self.chat.window_create(tk.END, window=speak_btn)
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _insert_markdown(self, text):
        """Insert text, rendering ```fenced``` blocks as framed code boxes."""
        idx = 0
        pattern = re.compile(r"```[ \t]*([\w+#.\-]*)[ \t]*\n?(.*?)```", re.DOTALL)
        for m in pattern.finditer(text):
            before = text[idx:m.start()]
            if before:
                self.chat.insert(tk.END, before, "body")
            self._insert_code_block(m.group(2), m.group(1))
            idx = m.end()
        rest = text[idx:]
        if rest:
            self.chat.insert(tk.END, rest, "body")

    def _insert_code_block(self, code, lang):
        code = code.strip("\n")
        lines = code.split("\n")
        width = min(max((len(ln) for ln in lines), default=20) + 1, 100)
        height = min(len(lines), 40)

        container = tk.Frame(
            self.chat, bg="#f6f8fa", highlightthickness=1,
            highlightbackground="#d0d7de", bd=0,
        )
        header = tk.Frame(container, bg="#eaeef2")
        header.pack(fill=tk.X)

        # collapse / expand toggle
        toggle_btn = tk.Button(
            header, text="▼", relief=tk.FLAT, bg="#eaeef2", fg="#57606a",
            activebackground="#dfe3e8", font=("Segoe UI", 8, "bold"),
            cursor="hand2", bd=0, padx=4,
        )
        toggle_btn.pack(side=tk.LEFT, padx=(6, 0), pady=1)

        tk.Label(
            header, text=(lang or "code"), bg="#eaeef2", fg="#57606a",
            font=("Segoe UI", 8, "bold"),
        ).pack(side=tk.LEFT, padx=8, pady=2)
        tk.Button(
            header, text="Copy", relief=tk.FLAT, bg="#eaeef2", fg=self.USER_FG,
            activebackground="#dfe3e8", font=("Segoe UI", 8, "bold"),
            cursor="hand2", bd=0, padx=8,
            command=lambda c=code: self._copy_text(c, "Code copied"),
        ).pack(side=tk.RIGHT, padx=4, pady=1)

        body = tk.Text(
            container, wrap=tk.NONE, bg="#f6f8fa", fg="#1f2328",
            font=("Consolas", 10), relief=tk.FLAT, bd=0, padx=10, pady=8,
            width=width, height=height,
        )
        body.insert("1.0", code)
        self._highlight_code(body, code, (lang or "").lower())
        body.configure(state=tk.DISABLED)
        body.pack(fill=tk.BOTH, expand=True)

        # wire up collapse/expand
        state = {"open": True}

        def toggle():
            if state["open"]:
                body.pack_forget()
                toggle_btn.configure(text="▶")
                state["open"] = False
            else:
                body.pack(fill=tk.BOTH, expand=True)
                toggle_btn.configure(text="▼")
                state["open"] = True

        toggle_btn.configure(command=toggle)

        self.chat.insert(tk.END, "\n")
        self.chat.window_create(tk.END, window=container, padx=6, pady=4)
        self.chat.insert(tk.END, "\n")

    # GitHub-light-ish syntax palette
    _SYNTAX_COLORS = {
        "keyword": "#cf222e",   # red
        "string": "#0a3069",    # dark blue
        "comment": "#6e7781",   # gray
        "number": "#0550ae",    # blue
        "function": "#8250df",  # purple
        "builtin": "#0550ae",   # blue
    }

    _KEYWORDS = {
        "def", "class", "return", "if", "elif", "else", "for", "while", "in",
        "import", "from", "as", "try", "except", "finally", "with", "lambda",
        "yield", "pass", "break", "continue", "and", "or", "not", "is", "None",
        "True", "False", "global", "nonlocal", "raise", "assert", "del", "async",
        "await", "function", "const", "let", "var", "new", "this", "typeof",
        "instanceof", "void", "public", "private", "protected", "static", "final",
        "int", "float", "double", "char", "bool", "boolean", "string", "str",
        "switch", "case", "default", "do", "throw", "catch", "extends",
        "implements", "interface", "package", "struct", "enum", "func", "type",
        "fn", "let", "mut", "use", "match", "self", "super", "export", "default",
    }

    def _highlight_code(self, widget, code, lang):
        """Apply lightweight regex-based syntax highlighting to a Text widget."""
        for tag, color in self._SYNTAX_COLORS.items():
            widget.tag_configure(tag, foreground=color)

        # comment styles by language
        if lang in ("py", "python", "rb", "ruby", "sh", "bash", "yaml", "yml",
                    "toml", "ini", "r", "perl", "pl"):
            line_comment = "#"
        else:
            line_comment = "//"

        def add_tags(pattern, tag, flags=0):
            for m in re.finditer(pattern, code, flags):
                start = f"1.0+{m.start()}c"
                end = f"1.0+{m.end()}c"
                widget.tag_add(tag, start, end)

        # strings (single, double, backtick) - tag first so keywords inside skip
        add_tags(r"(\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\")", "string")
        add_tags(r"('[^'\\\n]*(?:\\.[^'\\\n]*)*')", "string")
        add_tags(r"(`[^`\\]*(?:\\.[^`\\]*)*`)", "string")
        # numbers
        add_tags(r"\b\d+\.?\d*\b", "number")
        # function names: word followed by (
        add_tags(r"\b([A-Za-z_]\w*)\s*(?=\()", "function")
        # keywords
        for kw in self._KEYWORDS:
            add_tags(r"\b" + re.escape(kw) + r"\b", "keyword")
        # comments last so they win over everything on the line
        add_tags(re.escape(line_comment) + r"[^\n]*", "comment")
        add_tags(r"/\*.*?\*/", "comment", re.DOTALL)

        # priority: comments/strings should override keywords & numbers
        widget.tag_raise("function")
        widget.tag_raise("number")
        widget.tag_raise("keyword")
        widget.tag_raise("string")
        widget.tag_raise("comment")

    # ----- copy helpers --------------------------------------------------- #
    def _copy_text(self, text, status="Copied"):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(status)

    def _copy_selection(self, widget):
        try:
            sel = widget.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        self._copy_text(sel, "Copied selection")
        return "break"

    def _copy_all(self):
        parts = []
        for m in self.messages:
            who = "You" if m["role"] == "user" else "Assistant"
            parts.append(f"{who}:\n{m['content']}")
        if parts:
            self._copy_text("\n\n".join(parts), "Conversation copied")

    def _select_all(self, widget):
        widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _add_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(
            label="Copy", command=lambda: self._copy_selection(widget)
        )
        menu.add_command(label="Copy whole conversation", command=self._copy_all)
        menu.add_separator()
        menu.add_command(
            label="Select all", command=lambda: self._select_all(widget)
        )

        def popup(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        widget.bind("<Button-3>", popup)

    # ----- workspace agent ------------------------------------------------ #
    def choose_workspace(self):
        path = filedialog.askdirectory(
            parent=self.root, title="Choose a working folder for the model"
        )
        if not path:
            return
        self.workspace = path
        self.config["workspace"] = path
        save_config(self.config)
        self._update_ws_label()
        self.set_status(f"Workspace: {path}")

    def clear_workspace(self):
        if not self.workspace:
            return
        if not messagebox.askyesno(
            "Disable workspace",
            "Stop giving the model access to the working folder?",
        ):
            return
        self.workspace = ""
        self.config["workspace"] = ""
        save_config(self.config)
        self._update_ws_label()
        self.set_status("Workspace disabled")

    def _update_ws_label(self):
        # ws_label in new design shows conversation title, not workspace path
        pass

    def _build_workspace_tab(self, nb):
        tab = tk.Frame(nb, bg=self.BG)
        nb.add(tab, text="Workspace")
        tk.Label(
            tab,
            text="תיקיית עבודה — המודל יוכל לקרוא ולכתוב קבצים בתיקייה זו.",
            bg=self.BG, fg=self.MUTED_FG, font=("Segoe UI", 9),
            wraplength=520, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        path_var = tk.StringVar(value=self.workspace or "")
        path_entry = tk.Entry(
            tab, textvariable=path_var,
            bg=self.INPUT_BG, fg=self.TEXT_FG,
            insertbackground=self.TEXT_FG, relief=tk.SOLID, borderwidth=1,
            font=("Segoe UI", 10),
        )
        path_entry.pack(fill=tk.X, padx=12, pady=(0, 6))

        bar = tk.Frame(tab, bg=self.BG)
        bar.pack(fill=tk.X, padx=12, pady=(0, 6))

        def browse():
            path = filedialog.askdirectory(parent=tab.winfo_toplevel(),
                                           title="Choose workspace folder")
            if path:
                path_var.set(path)

        def save_ws():
            p = path_var.get().strip()
            if p and not os.path.isdir(p):
                messagebox.showwarning("Not found", f"Directory not found:\n{p}",
                                       parent=tab.winfo_toplevel())
                return
            self.workspace = p
            self.config["workspace"] = p
            save_config(self.config)
            self.set_status(f"Workspace: {p}" if p else "Workspace disabled")

        def clear_ws():
            path_var.set("")
            save_ws()

        ttk.Button(bar, text="Browse…", command=browse).pack(side=tk.LEFT)
        ttk.Button(bar, text="Save", command=save_ws).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Clear", command=clear_ws).pack(side=tk.LEFT)

        # write mode
        tk.Frame(tab, bg=self.BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)
        tk.Label(tab, text="Write permission", bg=self.BG, fg=self.TEXT_FG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(0, 4))
        _wm = tk.StringVar(value=self.write_mode)
        for val, label in (
            ("confirm",  "Ask before every write (safe)"),
            ("free",     "Allow writes without asking"),
            ("readonly", "Read-only — no writes"),
        ):
            tk.Radiobutton(
                tab, text=label, variable=_wm, value=val,
                bg=self.BG, fg=self.TEXT_FG, activebackground=self.BG,
                selectcolor=self.PANEL, font=("Segoe UI", 9), anchor="w",
                command=lambda v=val: self._set_write_mode(v),
            ).pack(anchor="w", padx=22)

    def _set_write_mode(self, mode):
        self.write_mode = mode
        self.config["write_mode"] = mode
        save_config(self.config)

    def _start_agent(self, model):
        self._append(f"{model}  ·  📁 workspace\n", "bot")
        self.streaming = True
        self.stop_event.clear()
        self._set_busy(True)
        
        self.set_status("Working in workspace…")

        system = self.build_system_prompt()
        ws_note = (
            "You can work with files in the user's working folder using the "
            "tools list_files, read_file and write_file. All paths are RELATIVE "
            "to that folder. Never assume file contents — inspect them with the "
            f"tools. Workspace path: {self.workspace}"
        )
        system = f"{system}\n\n{ws_note}".strip() if system else ws_note
        convo = [{"role": "system", "content": system}] + list(self.messages)

        self.worker = threading.Thread(
            target=self._run_agent, args=(model, convo), daemon=True
        )
        self.worker.start()

    def _run_agent(self, model, convo):
        """Worker thread: loop over tool calls until the model gives an answer."""
        try:
            for _ in range(MAX_AGENT_STEPS):
                if self.stop_event.is_set():
                    self.ui_queue.put(("agent_done", "(Stopped.)"))
                    return
                resp = agent_chat_once(OLLAMA_HOST, model, convo, TOOLS)
                msg = resp.get("message", {}) or {}
                calls = msg.get("tool_calls") or []
                if not calls:
                    self.ui_queue.put(
                        ("agent_done", msg.get("content", "") or "(No response.)")
                    )
                    return
                convo.append(msg)
                for tc in calls:
                    fn = tc.get("function", {}) or {}
                    name = fn.get("name", "")
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    self.ui_queue.put(("tool_call", self._format_call(name, args)))
                    result = self._exec_tool(name, args)
                    convo.append(
                        {"role": "tool", "content": result, "tool_name": name}
                    )
            self.ui_queue.put(("agent_done", "(Reached the tool-step limit.)"))
        except urllib.error.URLError as exc:
            self.ui_queue.put(("error", f"Connection error: {exc.reason}"))
        except Exception as exc:  # noqa: BLE001
            self.ui_queue.put(("error", f"Agent error: {exc}"))

    @staticmethod
    def _format_call(name, args):
        if name == "write_file":
            return f"write_file(path={args.get('path')!r})"
        if name == "read_file":
            return f"read_file(path={args.get('path')!r})"
        if name == "list_files":
            return f"list_files(path={args.get('path', '.')!r})"
        return f"{name}({args})"

    def _exec_tool(self, name, args):
        """Execute a tool inside the sandboxed workspace (worker thread)."""
        try:
            if name == "list_files":
                full = safe_join(self.workspace, args.get("path", "."))
                if not os.path.isdir(full):
                    return f"Not a directory: {args.get('path')}"
                entries = []
                for e in sorted(os.listdir(full)):
                    p = os.path.join(full, e)
                    if os.path.isdir(p):
                        entries.append(f"[dir]  {e}/")
                    else:
                        entries.append(f"       {e}  ({os.path.getsize(p)} bytes)")
                return "Contents:\n" + "\n".join(entries) if entries \
                    else "(empty folder)"

            if name == "read_file":
                full = safe_join(self.workspace, args.get("path", ""))
                if not os.path.isfile(full):
                    return f"File not found: {args.get('path')}"
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    data = fh.read(MAX_READ_CHARS + 1)
                if len(data) > MAX_READ_CHARS:
                    data = data[:MAX_READ_CHARS] + "\n...[truncated]"
                return data

            if name == "write_file":
                rel = args.get("path", "")
                content = args.get("content", "")
                full = safe_join(self.workspace, rel)
                if self.write_mode == "readonly":
                    return "Write denied: the workspace is read-only."
                if self.write_mode == "confirm" and not self._confirm_write(
                    full, content
                ):
                    return "Write denied by the user."
                os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(content)
                return f"Wrote {len(content)} characters to {rel}."

            return f"Unknown tool: {name}"
        except Exception as exc:  # noqa: BLE001
            return f"Tool error: {exc}"

    def _confirm_write(self, full, content):
        """Block the worker until the user approves the write on the main thread."""
        event = threading.Event()
        holder = {}
        self.ui_queue.put(("confirm_write", (full, content, event, holder)))
        event.wait()
        return holder.get("approved", False)

    def _finish_agent(self, text):
        self._append("\n", "spacer")
        self.chat.configure(state=tk.NORMAL)
        self._insert_markdown(text)
        self.chat.insert(tk.END, "\n")
        btn = tk.Button(
            self.chat, text="⧉ Copy", relief=tk.FLAT, bg=self.BG,
            fg=self.MUTED_FG, activebackground=self.PANEL,
            font=("Segoe UI", 8), cursor="hand2", bd=0,
            command=lambda t=text: self._copy_text(t, "Reply copied"),
        )
        self.chat.window_create(tk.END, window=btn)
        if _TTS_AVAILABLE:
            speak_btn = tk.Button(
                self.chat, text="🔊", relief=tk.FLAT, bg=self.BG,
                fg=self.MUTED_FG, activebackground=self.PANEL,
                font=("Segoe UI", 8), cursor="hand2", bd=0,
                command=lambda t=text: self._speak(t),
            )
            self.chat.window_create(tk.END, window=speak_btn)
        self.chat.configure(state=tk.DISABLED)
        self._append("\n\n", "spacer")
        self.chat.see(tk.END)
        if text.strip():
            self.messages.append({"role": "assistant", "content": text})
        self.streaming = False
        self._set_busy(False)
        
        self.set_status("Ready")
        self._maybe_auto_update()
        if self.tts_auto and text.strip():
            self._speak(text)

    # ----- TTS / STT ------------------------------------------------------ #
    def _speak(self, text):
        """Send text to the TTS worker (strips markdown for cleaner audio)."""
        clean = re.sub(r"```.*?```", "", text, flags=re.DOTALL)  # remove code blocks
        clean = re.sub(r"[#*`_~>]", "", clean).strip()
        if clean:
            self._tts.speak(clean, rate=self.tts_rate)

    def _start_stt(self):
        if not _STT_AVAILABLE:
            messagebox.showinfo(
                "STT לא זמין",
                "התקן את החבילות הנדרשות:\n\npip install SpeechRecognition pyaudio",
            )
            return
        if self.streaming:
            return
        self.set_status("מקשיב… דבר עכשיו")
        self.mic_btn.configure(state=tk.DISABLED)

        lang = self.stt_lang

        def work():
            try:
                r = _sr.Recognizer()
                with _sr.Microphone() as source:
                    r.adjust_for_ambient_noise(source, duration=0.4)
                    audio = r.listen(source, timeout=8, phrase_time_limit=60)
                text = r.recognize_google(audio, language=lang)
                self.ui_queue.put(("stt_result", text))
            except _sr.WaitTimeoutError:
                self.ui_queue.put(("stt_error", "לא זוהה קול"))
            except _sr.UnknownValueError:
                self.ui_queue.put(("stt_error", "לא ניתן להבין את הקול"))
            except Exception as exc:  # noqa: BLE001
                self.ui_queue.put(("stt_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    # ----- auto-update instructions --------------------------------------- #
    def _maybe_auto_update(self):
        if not self.auto_update or self._updating_instructions:
            return
        self._reply_count += 1
        if self._reply_count < max(1, self.auto_update_every):
            return
        self._reply_count = 0
        model = self.auto_update_model or self.model_var.get()
        if not model or not self.messages:
            return
        self._updating_instructions = True
        self.set_status("Updating instructions…")
        threading.Thread(
            target=self._auto_update_worker,
            args=(model, self.system_prompt, list(self.messages)),
            daemon=True,
        ).start()

    def _auto_update_worker(self, model, current, convo):
        try:
            transcript = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in convo[-12:]
            )
            meta = [
                {"role": "system", "content": AUTO_UPDATE_SYSTEM},
                {"role": "user", "content":
                    f"CURRENT INSTRUCTIONS:\n{current or '(empty)'}\n\n"
                    f"RECENT CONVERSATION:\n{transcript}\n\n"
                    "Return the updated instructions now."},
            ]
            resp = chat_complete(OLLAMA_HOST, model, meta)
            new_text = clean_instructions(
                resp.get("message", {}).get("content", "")
            )
            if new_text:
                self.ui_queue.put(("instructions_updated", new_text))
            else:
                self.ui_queue.put(("instructions_idle", None))
        except Exception:  # noqa: BLE001 - keep old instructions on failure
            self.ui_queue.put(("instructions_idle", None))

    # ----- misc ----------------------------------------------------------- #
    def set_status(self, text):
        self.status.configure(text=text)


def main():
    root = tk.Tk()
    OllamaChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
