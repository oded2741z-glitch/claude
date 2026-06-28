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
import platform
import queue
import re
import subprocess
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
        self.root.title("AI Chat")
        # Replace the default Tk feather icon with a blank 1x1 transparent image
        try:
            self._blank_icon = tk.PhotoImage(width=1, height=1)
            self.root.iconphoto(True, self._blank_icon)
        except tk.TclError:
            pass
        self.root.geometry("1100x780")
        self.root.configure(bg=self.BG)
        self.root.minsize(560, 460)
        # Maximize the window on startup
        try:
            self.root.state("zoomed")        # Windows / some Linux WMs
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)   # Linux (most WMs)
            except tk.TclError:
                pass

        self.config = load_config()
        self.system_prompt = self.config.get("instructions", "")
        self.skills = self.config.get("skills", [])    # reusable instruction sets
        self.sources = self.config.get("sources", [])  # reference material
        self.workspace = self.config.get("workspace", "") or ""
        if self.workspace and not os.path.isdir(self.workspace):
            self.workspace = ""
        self.write_mode = self.config.get("write_mode", "free")  # confirm|readonly|free
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
        self._instr_autosave = None        # flushes pending instruction edits
        self._instr_save_after = None      # debounce id for instructions auto-save
        self.messages = []                 # conversation history sent to Ollama
        self.ui_queue = queue.Queue()      # thread -> main-loop communication
        self.stop_event = threading.Event()
        self.worker = None
        self.streaming = False

        self._build_ui()
        self._update_ws_label()
        self._load_history()
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

        # Top bar: "+ New chat" and "New group" button
        top_bar = tk.Frame(sidebar, bg=self.SIDEBAR)
        top_bar.pack(fill=tk.X, padx=8, pady=(12, 4))

        tk.Button(
            top_bar, text="+  New chat",
            command=self.clear_chat,
            bg=self.SIDEBAR, fg=self.TEXT_FG,
            activebackground="#e2e2e2", activeforeground=self.TEXT_FG,
            relief=tk.FLAT, bd=0, padx=14, pady=8,
            font=("Segoe UI", 10, "bold"), cursor="hand2", anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Button(
            top_bar, text="⊞",
            command=self._new_group_dialog,
            bg=self.SIDEBAR, fg=self.MUTED_FG,
            activebackground="#e2e2e2",
            relief=tk.FLAT, bd=0, padx=8, pady=8,
            font=("Segoe UI", 12), cursor="hand2",
        ).pack(side=tk.RIGHT)

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
        self._hist_canvas = hist_canvas  # kept for drag scroll
        # drag-reorder state
        self._drag_src_idx  = None   # conv index being dragged
        self._drag_indicator = None  # tk.Frame used as drop-line

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
        # groups: [{"name": str, "expanded": bool, "items": [conv_idx, ...]}]
        self._hist_groups  = []

        # ── Right pane (top-bar + chat + input) ───────────────────────────── #
        right = tk.Frame(self.root, bg=self.BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Top bar ───────────────────────────────────────────────────────── #
        top = tk.Frame(right, bg=self.PANEL, pady=0)
        top.pack(side=tk.TOP, fill=tk.X)

        self.ws_label = tk.Label(
            top, text="New conversation", bg=self.PANEL, fg=self.MUTED_FG,
            font=("Segoe UI", 9),
        )
        self.ws_label.pack(side=tk.LEFT, padx=14, pady=8)

        # workspace status chip (right side of top bar)
        self._ws_chip = tk.Label(
            top, text="", bg=self.PANEL, fg=self.BOT_FG,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        self._ws_chip.pack(side=tk.RIGHT, padx=(0, 6))
        self._ws_chip.bind("<Button-1>", lambda e: self.open_settings())

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
        self.chat.tag_configure(
            "thinking", foreground="#a0a0a0",
            font=("Segoe UI", 13), spacing1=6, spacing3=6,
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
        self._add_edit_menu(self.entry)

        # placeholder
        self._placeholder_active = False
        self._show_placeholder()
        self.entry.bind("<FocusIn>",  self._hide_placeholder)
        self.entry.bind("<FocusOut>", self._show_placeholder)

        # Attachment chips area (shows above the toolbar when files are added)
        self._attach_bar = tk.Frame(card, bg=self.INPUT_BG)
        self._attach_bar.pack(fill=tk.X, padx=12, pady=(0, 0))

        # Bottom toolbar inside the card
        toolbar = tk.Frame(card, bg=self.INPUT_BG)
        toolbar.pack(fill=tk.X, padx=10, pady=(0, 8))

        # Attach (+) button — add files / screenshots
        self._attachments = []   # list of {"name", "kind": "image"|"text", "data"|"text"}
        self.attach_btn = tk.Button(
            toolbar, text="+",
            command=self._attach_file,
            bg=self.INPUT_BG, fg=self.MUTED_FG,
            activebackground="#f3f4f6", activeforeground=self.TEXT_FG,
            relief=tk.FLAT, bd=0, font=("Segoe UI", 16),
            padx=6, pady=2, cursor="hand2",
        )
        self.attach_btn.pack(side=tk.LEFT, padx=(2, 0))

        # Mic button — simple flat text button (like ⚙ Settings)
        self._mic_recording = False
        self._mic_anim_id = None
        self._mic_canvas = None
        self.mic_btn = tk.Button(
            toolbar, text="◉",
            command=self._start_stt,
            bg=self.INPUT_BG, fg=self.MUTED_FG,
            activebackground="#f3f4f6", activeforeground=self.TEXT_FG,
            relief=tk.FLAT, bd=0, font=("Segoe UI", 13),
            padx=6, pady=4,
            cursor="hand2" if _STT_AVAILABLE else "arrow",
            state=tk.NORMAL if _STT_AVAILABLE else tk.DISABLED,
        )
        self.mic_btn.pack(side=tk.LEFT, padx=(2, 0))

        # Send / Stop button — simple flat text button (like ⚙ Settings)
        self._send_canvas = None  # legacy guard
        self.send_text_btn = tk.Button(
            toolbar, text="💬",
            command=self._on_send_click,
            bg=self.INPUT_BG, fg=self.TEXT_FG,
            activebackground="#f3f4f6", activeforeground=self.TEXT_FG,
            relief=tk.FLAT, bd=0, font=("Segoe UI", 15),
            padx=8, pady=4, cursor="hand2",
        )
        self.send_text_btn.pack(side=tk.RIGHT, padx=(10, 2))

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

        # stop functionality wired through same text button
        self.send_btn  = self.send_text_btn   # keep API compat
        self.stop_btn  = self.send_text_btn   # same widget

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

    def _draw_mic_icon(self, recording=False):
        """Draw mic icon matching heroicons outline SVG (viewBox 0-24, scale to 34px)."""
        c = self._mic_canvas
        c.delete("all")
        col = "#ef4444" if recording else "#6b7280"
        lw  = 1.6   # stroke width

        # Scale viewBox 24→28px inside the 34px canvas, centred
        s  = 1.2
        ox, oy = 3.2, 1.5

        def sx(x): return x * s + ox
        def sy(y): return y * s + oy

        # ── Capsule body ──────────────────────────────────────────────────── #
        # Top semicircle: circle centred at (12,4.5) r=3, upper half
        c.create_arc(sx(9), sy(1.5), sx(15), sy(7.5),
                     start=0, extent=180,
                     outline=col, width=lw, style=tk.ARC)
        # Left vertical side
        c.create_line(sx(9), sy(4.5), sx(9), sy(12.75),
                      fill=col, width=lw)
        # Right vertical side
        c.create_line(sx(15), sy(4.5), sx(15), sy(12.75),
                      fill=col, width=lw)
        # Bottom semicircle: circle centred at (12,12.75) r=3, lower half
        c.create_arc(sx(9), sy(9.75), sx(15), sy(15.75),
                     start=0, extent=-180,
                     outline=col, width=lw, style=tk.ARC)

        # ── Stand arc ─────────────────────────────────────────────────────── #
        # Full circle centred at (12,12.75) r=6; show bottom half
        # + short upward arms at each end (v-1.5 in SVG)
        c.create_arc(sx(6), sy(6.75), sx(18), sy(18.75),
                     start=0, extent=-180,
                     outline=col, width=lw, style=tk.ARC)
        c.create_line(sx(18), sy(11.25), sx(18), sy(12.75),
                      fill=col, width=lw)
        c.create_line(sx(6),  sy(11.25), sx(6),  sy(12.75),
                      fill=col, width=lw)

        # ── Stem + base ───────────────────────────────────────────────────── #
        c.create_line(sx(12), sy(18.75), sx(12), sy(22.5),
                      fill=col, width=lw, capstyle=tk.ROUND)
        c.create_line(sx(8.25), sy(22.5), sx(15.75), sy(22.5),
                      fill=col, width=lw, capstyle=tk.ROUND)

    def _set_mic_recording(self, recording: bool):
        """Toggle mic button colour between idle and recording."""
        self._mic_recording = recording
        if recording:
            self.mic_btn.configure(fg="#ef4444", text="◉")
            self._animate_mic()
        else:
            self.mic_btn.configure(fg=self.MUTED_FG, text="◉")
            if self._mic_anim_id:
                self.root.after_cancel(self._mic_anim_id)
                self._mic_anim_id = None

    def _animate_mic(self):
        """Blink the mic button red while recording."""
        if not self._mic_recording:
            return
        current = self.mic_btn.cget("fg")
        next_col = self.MUTED_FG if current == "#ef4444" else "#ef4444"
        self.mic_btn.configure(fg=next_col)
        self._mic_anim_id = self.root.after(500, self._animate_mic)

    # ── attachments (files / screenshots) ─────────────────────────────────── #
    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
    VISION_HINTS = ("llava", "bakllava", "gemma3", "llama3.2-vision",
                    "llama3.2v", "moondream", "vision", "minicpm-v",
                    "qwen2-vl", "qwen2.5vl", "pixtral", "granite3.2-vision")

    def _model_supports_vision(self, model):
        """Heuristic: does this model name look like a vision-capable model?"""
        m = (model or "").lower()
        return any(h in m for h in self.VISION_HINTS)

    def _attach_file(self):
        """Open a file dialog and attach one or more files (images or text)."""
        import base64
        paths = filedialog.askopenfilenames(
            title="Attach files",
            filetypes=[
                ("All supported", "*.png *.jpg *.jpeg *.gif *.bmp *.webp "
                                  "*.txt *.py *.js *.json *.md *.csv *.html "
                                  "*.css *.c *.cpp *.java *.cs *.go *.rs"),
                ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("Text/code", "*.txt *.py *.js *.json *.md *.csv *.html *.css"),
                ("All files", "*.*"),
            ],
        )
        for path in paths:
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            try:
                if ext in self.IMAGE_EXTS:
                    with open(path, "rb") as fh:
                        b64 = base64.b64encode(fh.read()).decode("ascii")
                    self._attachments.append(
                        {"name": name, "kind": "image", "data": b64})
                else:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    if len(text) > 100_000:
                        text = text[:100_000] + "\n...[truncated]"
                    self._attachments.append(
                        {"name": name, "kind": "text", "text": text})
            except OSError as exc:
                messagebox.showerror("Error", f"Cannot read {name}:\n{exc}")
        self._render_attachments()

    def _render_attachments(self):
        """Redraw the chip row showing currently attached files."""
        for w in self._attach_bar.winfo_children():
            w.destroy()
        if not self._attachments:
            self._attach_bar.configure(pady=0)
            return
        self._attach_bar.configure(pady=4)
        for i, att in enumerate(self._attachments):
            icon = "🖼" if att["kind"] == "image" else "📄"
            chip = tk.Frame(self._attach_bar, bg="#eef2ff",
                            highlightthickness=1, highlightbackground="#c7d2fe")
            chip.pack(side=tk.LEFT, padx=(0, 6), pady=2)
            tk.Label(chip, text=f"{icon} {att['name']}", bg="#eef2ff",
                     fg=self.TEXT_FG, font=("Segoe UI", 8),
                     padx=6, pady=2).pack(side=tk.LEFT)
            tk.Button(chip, text="✕", command=lambda i=i: self._remove_attachment(i),
                      bg="#eef2ff", fg=self.MUTED_FG, activebackground="#e0e7ff",
                      relief=tk.FLAT, bd=0, font=("Segoe UI", 8),
                      padx=4, cursor="hand2").pack(side=tk.LEFT)

    def _remove_attachment(self, idx):
        if 0 <= idx < len(self._attachments):
            del self._attachments[idx]
        self._render_attachments()

    def _on_send_click(self):
        if self.streaming:
            self.stop()
        else:
            self.send()

    def _set_busy(self, busy: bool):
        """Toggle the send/stop text button appearance."""
        if busy:
            self.send_text_btn.configure(text="■", fg="#ef4444")
        else:
            self.send_text_btn.configure(text="💬", fg=self.TEXT_FG)

    # ── Thinking animation ────────────────────────────────────────────────── #
    _THINK_FRAMES = ["●  ○  ○", "●  ●  ○", "●  ●  ●", "○  ●  ●", "○  ○  ●", "○  ○  ○"]

    def _start_thinking_anim(self):
        self._thinking_active  = True
        self._thinking_phase   = 0
        self._thinking_anim_id = None
        self._thinking_line    = None
        frame = self._THINK_FRAMES[0]
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, frame, "thinking")
        # record which line we're on (reliable: line number of last char)
        self._thinking_line = int(self.chat.index("end-1c").split(".")[0])
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)
        self._thinking_anim_id = self.root.after(220, self._tick_thinking)

    def _tick_thinking(self):
        if not self._thinking_active:
            return
        self._thinking_phase = (self._thinking_phase + 1) % len(self._THINK_FRAMES)
        frame = self._THINK_FRAMES[self._thinking_phase]
        ln = self._thinking_line
        self.chat.configure(state=tk.NORMAL)
        try:
            self.chat.delete(f"{ln}.0", f"{ln}.end")
            self.chat.insert(f"{ln}.0", frame, "thinking")
        except tk.TclError:
            self._thinking_active = False
        self.chat.configure(state=tk.DISABLED)
        if self._thinking_active:
            self._thinking_anim_id = self.root.after(220, self._tick_thinking)

    def _stop_thinking_anim(self):
        self._thinking_active = False
        if self._thinking_anim_id:
            self.root.after_cancel(self._thinking_anim_id)
            self._thinking_anim_id = None
        ln = getattr(self, "_thinking_line", None)
        if ln is None:
            return
        self.chat.configure(state=tk.NORMAL)
        try:
            # delete the whole line including the preceding newline
            self.chat.delete(f"{ln}.0 - 1c", f"{ln}.end")
        except tk.TclError:
            pass
        self.chat.configure(state=tk.DISABLED)

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
            # update messages in place, keep any custom/renamed title
            self._history[self._active_hist] = list(self.messages)
        else:
            self._history.insert(0, list(self.messages))
            self._hist_names.insert(0, title)
            self._history    = self._history[:30]
            self._hist_names = self._hist_names[:30]
            self._active_hist = 0
        self._rebuild_hist_rows()
        self._persist_history()

    def _persist_history(self):
        """Save conversations and groups to disk."""
        self.config["conversations"] = [
            {"name": n, "messages": m}
            for n, m in zip(self._hist_names, self._history)
        ]
        self.config["groups"] = self._hist_groups
        save_config(self.config)

    def _load_history(self):
        """Load saved conversations and groups from disk into the sidebar."""
        saved = self.config.get("conversations", [])
        self._history    = [c.get("messages", []) for c in saved]
        self._hist_names = [c.get("name", "(untitled)") for c in saved]
        self._hist_groups = self.config.get("groups", [])
        # validate group indices
        n = len(self._history)
        for g in self._hist_groups:
            g["items"] = [i for i in g.get("items", []) if i < n]
        self._active_hist = -1
        self._rebuild_hist_rows()

    def _rebuild_hist_rows(self):
        """Rebuild the scrollable sidebar rows (conversations + groups)."""
        for w in self._hist_rows:
            w.destroy()
        self._hist_rows.clear()

        grouped = {i for g in self._hist_groups for i in g["items"]}

        # ungrouped conversations (order preserved in _history)
        for idx in range(len(self._hist_names)):
            if idx not in grouped:
                self._render_conv_row(idx, indent=False)

        # groups
        for gi, group in enumerate(self._hist_groups):
            self._render_group_row(gi, group)
            if group.get("expanded", True):
                for idx in group["items"]:
                    if idx < len(self._hist_names):
                        self._render_conv_row(idx, indent=True)

    def _render_conv_row(self, idx, indent=False):
        """Create one sidebar row for conversation at idx."""
        active = (idx == self._active_hist)
        row_bg = "#dbeafe" if active else self.SIDEBAR
        row = tk.Frame(self._hist_inner, bg=row_bg, cursor="hand2")
        row.pack(fill=tk.X, pady=1)
        self._hist_rows.append(row)

        dots_btn = tk.Button(
            row, text="⋯",
            bg=row_bg, fg=self.MUTED_FG,
            activebackground="#bfdbfe",
            relief=tk.FLAT, bd=0,
            font=("Segoe UI", 11), padx=6, pady=4,
            command=lambda i=idx, b=row: self._hist_menu(i, b),
        )
        dots_btn.pack(side=tk.RIGHT)

        name = self._hist_names[idx]
        disp = name if len(name) <= 20 else name[:19] + "…"
        lpad = 24 if indent else 10
        name_btn = tk.Button(
            row, text=disp,
            bg=row_bg, fg=self.TEXT_FG,
            activebackground="#bfdbfe",
            relief=tk.FLAT, bd=0,
            font=("Segoe UI", 9), anchor="w",
            padx=lpad, pady=5, width=1,
        )
        name_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        for w in (row, name_btn, dots_btn):
            w.bind("<Enter>", lambda e, r=row, nb=name_btn, db=dots_btn:
                [x.configure(bg="#dbeafe") for x in (r, nb, db)])
            w.bind("<Leave>", lambda e, r=row, nb=name_btn, db=dots_btn,
                   i=idx: [x.configure(
                       bg="#dbeafe" if i == self._active_hist else self.SIDEBAR
                   ) for x in (r, nb, db)])

        # drag-to-reorder bindings
        name_btn.bind("<ButtonPress-1>",   lambda e, i=idx: self._drag_start(e, i))
        name_btn.bind("<B1-Motion>",       lambda e, i=idx: self._drag_motion(e, i))
        name_btn.bind("<ButtonRelease-1>", lambda e, i=idx: self._drag_end(e, i))

    def _render_group_row(self, gi, group):
        """Create one sidebar row for a group header."""
        arrow = "▾" if group.get("expanded", True) else "▸"
        row_bg = self.SIDEBAR
        row = tk.Frame(self._hist_inner, bg=row_bg)
        row.pack(fill=tk.X, pady=(4, 1))
        self._hist_rows.append(row)

        dots_btn = tk.Button(
            row, text="⋯",
            bg=row_bg, fg=self.MUTED_FG,
            activebackground="#e2e2e2",
            relief=tk.FLAT, bd=0,
            font=("Segoe UI", 11), padx=6, pady=3,
            command=lambda g=gi, b=row: self._group_menu(g, b),
        )
        dots_btn.pack(side=tk.RIGHT)

        disp = group["name"] if len(group["name"]) <= 18 else group["name"][:17] + "…"
        hdr_btn = tk.Button(
            row, text=f"{arrow}  {disp}",
            bg=row_bg, fg=self.TEXT_FG,
            activebackground="#e2e2e2",
            relief=tk.FLAT, bd=0,
            font=("Segoe UI", 9, "bold"), anchor="w",
            padx=8, pady=4, width=1,
            command=lambda g=gi: self._toggle_group(g),
        )
        hdr_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        for w in (row, hdr_btn, dots_btn):
            w.bind("<Enter>", lambda e, r=row, nb=hdr_btn, db=dots_btn:
                [x.configure(bg="#e2e2e2") for x in (r, nb, db)])
            w.bind("<Leave>", lambda e, r=row, nb=hdr_btn, db=dots_btn:
                [x.configure(bg=self.SIDEBAR) for x in (r, nb, db)])

        # allow dropping onto group header
        hdr_btn.bind("<ButtonRelease-1>", lambda e, g=gi: self._drag_drop_group(g))

    def _hist_menu(self, idx, anchor_widget):
        """Show rename / delete / move-to-group popup for conversation at idx."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Rename…",
                         command=lambda: self._hist_rename(idx))

        # Move to group sub-menu
        if self._hist_groups:
            move_menu = tk.Menu(menu, tearoff=0)
            for gi, g in enumerate(self._hist_groups):
                move_menu.add_command(
                    label=g["name"],
                    command=lambda g=gi, i=idx: self._move_to_group(i, g),
                )
            # "Remove from group" if currently in one
            cur_group = self._conv_group(idx)
            if cur_group is not None:
                move_menu.add_separator()
                move_menu.add_command(
                    label="Remove from group",
                    command=lambda i=idx: self._remove_from_group(i),
                )
            menu.add_cascade(label="Move to group ▸", menu=move_menu)

        menu.add_separator()
        menu.add_command(label="Delete",
                         command=lambda: self._hist_delete(idx))
        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _group_menu(self, gi, anchor_widget):
        """Rename / delete a group."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Rename group…",
                         command=lambda: self._rename_group(gi))
        menu.add_command(label="Delete group (keep chats)",
                         command=lambda: self._delete_group(gi, keep=True))
        menu.add_command(label="Delete group + chats",
                         command=lambda: self._delete_group(gi, keep=False))
        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ── group helpers ─────────────────────────────────────────────────────── #
    def _conv_group(self, idx):
        """Return group index if conv is in a group, else None."""
        for gi, g in enumerate(self._hist_groups):
            if idx in g["items"]:
                return gi
        return None

    def _toggle_group(self, gi):
        self._hist_groups[gi]["expanded"] = not self._hist_groups[gi].get(
            "expanded", True)
        self._rebuild_hist_rows()
        self._persist_history()

    def _new_group_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("New group")
        dlg.configure(bg=self.BG)
        dlg.geometry("320x110")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Group name:", bg=self.BG, fg=self.TEXT_FG,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(14, 4))
        var = tk.StringVar()
        ent = tk.Entry(dlg, textvariable=var, font=("Segoe UI", 10),
                       bg=self.INPUT_BG, fg=self.TEXT_FG,
                       relief=tk.SOLID, borderwidth=1)
        ent.pack(fill=tk.X, padx=14)
        ent.focus_set()
        self._add_edit_menu(ent)
        bar = tk.Frame(dlg, bg=self.BG)
        bar.pack(fill=tk.X, padx=14, pady=10)

        def ok():
            n = var.get().strip()
            if n:
                self._hist_groups.append({"name": n, "expanded": True, "items": []})
                self._rebuild_hist_rows()
                self._persist_history()
            dlg.destroy()

        ttk.Button(bar, text="Create", command=ok).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=(0, 8))
        ent.bind("<Return>", lambda e: ok())
        dlg.wait_window()

    def _rename_group(self, gi):
        dlg = tk.Toplevel(self.root)
        dlg.title("Rename group")
        dlg.configure(bg=self.BG)
        dlg.geometry("320x110")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="New name:", bg=self.BG, fg=self.TEXT_FG,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(14, 4))
        var = tk.StringVar(value=self._hist_groups[gi]["name"])
        ent = tk.Entry(dlg, textvariable=var, font=("Segoe UI", 10),
                       bg=self.INPUT_BG, fg=self.TEXT_FG,
                       relief=tk.SOLID, borderwidth=1)
        ent.pack(fill=tk.X, padx=14)
        ent.select_range(0, tk.END)
        ent.focus_set()
        self._add_edit_menu(ent)
        bar = tk.Frame(dlg, bg=self.BG)
        bar.pack(fill=tk.X, padx=14, pady=10)

        def ok():
            n = var.get().strip()
            if n:
                self._hist_groups[gi]["name"] = n
                self._rebuild_hist_rows()
                self._persist_history()
            dlg.destroy()

        ttk.Button(bar, text="Save", command=ok).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=(0, 8))
        ent.bind("<Return>", lambda e: ok())
        dlg.wait_window()

    def _delete_group(self, gi, keep=True):
        name = self._hist_groups[gi]["name"]
        n = len(self._hist_groups[gi]["items"])
        if keep:
            msg = f"Delete the group '{name}'?\nThe chats will remain in the list."
        else:
            msg = (f"Delete the group '{name}' and the {n} chats inside it?\n"
                   "This action cannot be undone.")
        if not messagebox.askyesno("Delete group", msg):
            return
        if not keep:
            for idx in reversed(sorted(self._hist_groups[gi]["items"])):
                self._hist_delete_silent(idx)
        del self._hist_groups[gi]
        self._rebuild_hist_rows()
        self._persist_history()

    def _move_to_group(self, idx, gi):
        self._remove_from_group(idx)
        self._hist_groups[gi]["items"].append(idx)
        self._rebuild_hist_rows()
        self._persist_history()

    def _remove_from_group(self, idx):
        for g in self._hist_groups:
            if idx in g["items"]:
                g["items"].remove(idx)
        self._rebuild_hist_rows()
        self._persist_history()

    # ── drag-to-reorder ───────────────────────────────────────────────────── #
    def _drag_start(self, event, idx):
        self._drag_src_idx = idx
        self._drag_over_idx = None
        self._drag_moved = False
        self._drag_start_y = event.y_root

    def _drag_motion(self, event, idx):
        if self._drag_src_idx is None:
            return
        # ignore tiny jitters so a normal click isn't treated as a drag
        if abs(event.y_root - getattr(self, "_drag_start_y", event.y_root)) > 5:
            self._drag_moved = True
        if not self._drag_moved:
            return
        # find which row the pointer is over
        abs_y = event.widget.winfo_rooty() + event.y
        target = self._drag_target_at(abs_y)
        if target != self._drag_over_idx:
            self._drag_over_idx = target
            self._rebuild_hist_rows_with_indicator(target)

    def _drag_end(self, event, idx):
        if self._drag_src_idx is None:
            return
        # plain click (no real drag) → open the conversation
        if not getattr(self, "_drag_moved", False):
            self._drag_src_idx = None
            self._drag_over_idx = None
            self._on_hist_select(idx)
            return
        src = self._drag_src_idx
        self._drag_src_idx = None
        self._drag_over_idx = None
        # check if dropped on a group header
        # (handled separately via _drag_drop_group; here just reorder flat list)
        abs_y = event.widget.winfo_rooty() + event.y
        dst = self._drag_target_at(abs_y)
        if dst is not None and dst != src:
            # reorder in the ungrouped list
            cur_group = self._conv_group(src)
            if cur_group is not None:
                # reorder within the group
                items = self._hist_groups[cur_group]["items"]
                if src in items and dst in items:
                    items.remove(src)
                    di = items.index(dst) if dst in items else len(items)
                    items.insert(di, src)
            else:
                # reorder in the global list, also update group indices
                self._reorder_flat(src, dst)
        self._rebuild_hist_rows()
        self._persist_history()

    def _drag_drop_group(self, gi):
        """Called when mouse is released on a group header while dragging."""
        if self._drag_src_idx is None:
            return
        self._move_to_group(self._drag_src_idx, gi)
        self._drag_src_idx = None

    def _drag_target_at(self, abs_y):
        """Return conv index whose row the pointer is closest to, or None."""
        for row in self._hist_rows:
            if not row.winfo_exists():
                continue
            ry = row.winfo_rooty()
            rh = row.winfo_height()
            if ry <= abs_y <= ry + rh:
                # find which idx this row represents
                for i, w in enumerate(self._hist_rows):
                    if w is row:
                        return i  # row index, will map to conv idx below
        return None

    def _reorder_flat(self, src, dst):
        """Move conversation src before/after dst in the flat lists."""
        if src == dst or src >= len(self._history) or dst >= len(self._history):
            return
        # move in data lists
        self._history.insert(dst, self._history.pop(src))
        self._hist_names.insert(dst, self._hist_names.pop(src))
        # fix active index
        if self._active_hist == src:
            self._active_hist = dst
        elif src < self._active_hist <= dst:
            self._active_hist -= 1
        elif dst <= self._active_hist < src:
            self._active_hist += 1
        # fix group item indices
        old_to_new = list(range(len(self._history)))
        # old src → dst; elements between shift by 1
        mapping = {}
        for i in range(len(self._history)):
            if i == src:
                mapping[i] = dst
            elif src < dst and src < i <= dst:
                mapping[i] = i - 1
            elif dst < src and dst <= i < src:
                mapping[i] = i + 1
            else:
                mapping[i] = i
        for g in self._hist_groups:
            g["items"] = [mapping.get(i, i) for i in g["items"]]

    def _rebuild_hist_rows_with_indicator(self, target_row):
        """Rebuild rows and show a blue line at target_row position."""
        self._rebuild_hist_rows()  # simple rebuild; indicator is subtle

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
        self._add_edit_menu(ent)
        bar = tk.Frame(dlg, bg=self.BG)
        bar.pack(fill=tk.X, padx=14, pady=10)

        def ok():
            n = var.get().strip()
            if n:
                self._hist_names[idx] = n
                self._rebuild_hist_rows()
                self._persist_history()
                if idx == self._active_hist:
                    self.ws_label.configure(text=n)
            dlg.destroy()

        ttk.Button(bar, text="Save", command=ok).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=(0, 8))
        ent.bind("<Return>", lambda e: ok())
        dlg.wait_window()

    def _hist_delete(self, idx):
        self._hist_delete_silent(idx)
        self._rebuild_hist_rows()
        self._persist_history()

    def _hist_delete_silent(self, idx):
        """Delete a conversation and fix all group references."""
        del self._history[idx]
        del self._hist_names[idx]
        if self._active_hist == idx:
            self._active_hist = -1
            self.ws_label.configure(text="New conversation")
        elif self._active_hist > idx:
            self._active_hist -= 1
        # fix group indices
        for g in self._hist_groups:
            g["items"] = [i if i < idx else i - 1
                          for i in g["items"] if i != idx]

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
        self._build_cuda_tab(nb)

        def close_settings():
            # flush any pending instruction edits before closing
            if self._instr_save_after is not None:
                try:
                    self.root.after_cancel(self._instr_save_after)
                except (tk.TclError, ValueError):
                    pass
                self._instr_save_after = None
            if self._instr_autosave is not None:
                try:
                    self._instr_autosave()
                except tk.TclError:
                    pass
            self._instr_editor = None
            self._instr_autosave = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_settings)
        ttk.Button(win, text="Close", command=close_settings).pack(
            side=tk.BOTTOM, anchor="e", padx=14, pady=(0, 12)
        )

    def _make_textbox(self, parent, **kw):
        box = tk.Text(
            parent, wrap=tk.WORD, bg=self.INPUT_BG, fg=self.TEXT_FG,
            insertbackground=self.TEXT_FG, relief=tk.SOLID, borderwidth=1,
            highlightthickness=1, highlightbackground=self.BORDER,
            highlightcolor=self.USER_FG, font=("Segoe UI", 11),
            padx=8, pady=8, **kw
        )
        self._add_edit_menu(box)
        return box

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

        def _do_save(show_status=True):
            new = editor.get("1.0", tk.END).strip()
            if new == self.system_prompt:
                return
            self.system_prompt = new
            self._persist()
            if show_status:
                self.set_status("Instructions saved")

        def schedule_save(_=None):
            if self._instr_save_after is not None:
                self.root.after_cancel(self._instr_save_after)
            self._instr_save_after = self.root.after(
                600, lambda: _do_save(False)
            )

        # auto-save: debounced while typing, and immediately when leaving the box
        editor.bind("<KeyRelease>", schedule_save)
        editor.bind("<FocusOut>", lambda e: _do_save(False))
        # let open_settings flush a final save when the window closes
        self._instr_autosave = lambda: _do_save(False)

        ttk.Button(bar, text="Save", command=lambda: _do_save(True)).pack(
            side=tk.RIGHT
        )
        tk.Label(
            bar, text="Saved automatically", bg=self.BG, fg=self.MUTED_FG,
            font=("Segoe UI", 8),
        ).pack(side=tk.RIGHT, padx=(0, 10))
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
        self._add_edit_menu(name_entry)

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
            note("Adds a 🔊 button to every model reply.")
            _au_tts = tk.BooleanVar(value=self.tts_auto)

            def toggle_auto():
                self.tts_auto = _au_tts.get()
                self._persist()

            tk.Checkbutton(
                tab, text="Read replies aloud automatically",
                variable=_au_tts, command=toggle_auto,
                bg=self.BG, fg=self.TEXT_FG, activebackground=self.BG,
                selectcolor=self.PANEL, font=("Segoe UI", 10), anchor="w",
            ).pack(anchor="w", padx=12)

            row = tk.Frame(tab, bg=self.BG)
            row.pack(anchor="w", padx=12, pady=(6, 2))
            tk.Label(row, text="Speech rate:", bg=self.BG,
                     fg=self.TEXT_FG, font=("Segoe UI", 9)).pack(side=tk.LEFT)
            _rate = tk.IntVar(value=self.tts_rate)
            rate_spin = tk.Spinbox(
                row, from_=80, to=300, width=5, textvariable=_rate,
                relief=tk.SOLID, borderwidth=1,
                bg=self.INPUT_BG, fg=self.TEXT_FG,
            )
            rate_spin.pack(side=tk.LEFT, padx=6)
            tk.Label(row, text="words per minute", bg=self.BG,
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
            tab, text="Speech-to-Text (STT) — 🎤 button", bg=self.BG,
            fg=self.TEXT_FG, font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(0, 2))

        if not _STT_AVAILABLE:
            note("speech_recognition and pyaudio are not installed. Run:\n"
                 "pip install SpeechRecognition pyaudio")
        else:
            note("Click 🎤 while typing to dictate text from the microphone.")
            STT_LANGS = [
                ("Hebrew (he-IL)", "he-IL"),
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
            tk.Label(row2, text="Recognition language:", bg=self.BG,
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
            text = ""
        else:
            text = self.entry.get("1.0", tk.END).strip()
        # allow sending if there is text OR at least one attachment
        if not text and not self._attachments:
            return

        # warn if attaching images to a model that likely can't see them
        has_images = any(a["kind"] == "image" for a in self._attachments)
        if has_images and not self._model_supports_vision(model):
            if not messagebox.askyesno(
                "Model does not support images",
                f"The model '{model}' probably cannot read images.\n"
                "Consider choosing a vision model (gemma3, llava, llama3.2-vision).\n\n"
                "Send anyway?",
            ):
                return

        self.entry.delete("1.0", tk.END)
        self._placeholder_active = False

        # build the message content: user text + any attached text files
        content = text
        images = []
        attach_summary = []
        for att in self._attachments:
            if att["kind"] == "image":
                images.append(att["data"])
                attach_summary.append(f"🖼 {att['name']}")
            else:
                content += (f"\n\n--- Attached file: {att['name']} ---\n"
                            f"{att['text']}")
                attach_summary.append(f"📄 {att['name']}")

        user_msg = {"role": "user", "content": content or "(see attachment)"}
        if images:
            user_msg["images"] = images
        self.messages.append(user_msg)

        self._append("You\n", "user")
        if text:
            self._append(text + "\n", "user_body")
        if attach_summary:
            self._append("  ".join(attach_summary) + "\n", "user_body")
        self._append("\n", "spacer")

        # clear attachments now that they've been sent
        self._attachments = []
        self._render_attachments()

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
        self._got_first_chunk = False
        self._start_thinking_anim()

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
                elif kind == "ui_call":
                    # payload is a zero-arg callable to run on the main thread
                    try:
                        payload()
                    except tk.TclError:
                        pass
                elif kind == "models_error":
                    self.set_status("Cannot reach Ollama")
                    messagebox.showerror(
                        "Ollama not reachable",
                        f"Could not load models from {OLLAMA_HOST}.\n\n{payload}\n\n"
                        "Make sure Ollama is installed and running.",
                    )
                elif kind == "chunk":
                    if not self._got_first_chunk:
                        self._stop_thinking_anim()
                        self._got_first_chunk = True
                    self._bot_reply += payload
                    self._append(payload, "body")
                elif kind == "done":
                    self._finish_reply()
                elif kind == "tool_call":
                    self._append(f"🔧 {payload}\n", "tool")
                elif kind == "tool_code":
                    path, content = payload
                    lang = self._lang_from_path(path)
                    self.chat.configure(state=tk.NORMAL)
                    self._append(f"📄 {path}\n", "tool")
                    self._insert_code_block(content, lang)
                    self.chat.configure(state=tk.DISABLED)
                    self.chat.see(tk.END)
                elif kind == "confirm_write":
                    full, content, event, holder = payload
                    preview = content if len(content) <= 1500 \
                        else content[:1500] + "\n...[truncated]"
                    self.root.lift()
                    self.root.focus_force()
                    holder["approved"] = messagebox.askyesno(
                        "Confirm file write",
                        f"The model wants to write to the file:\n\n{full}\n\n"
                        f"── Content ──\n{preview}",
                        default=messagebox.NO,
                    )
                    if not holder["approved"]:
                        self.set_status("Write rejected by user")
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
                    self._hide_placeholder()
                    self.entry.insert(tk.END, payload)
                    self.entry.focus_set()
                    self._set_mic_recording(False)
                    self.set_status("Ready")
                elif kind == "stt_error":
                    self._set_mic_recording(False)
                    self.set_status(f"Recognition error: {payload}")
                    if payload:
                        messagebox.showwarning("Microphone error", payload)
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
        self._stop_thinking_anim()   # no-op if already stopped by first chunk
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

    def _ctrl_keycodes(self):
        """Physical keycodes for C/V/X/A → action, for the current platform.

        Keycodes are tied to the physical key, not the produced character, so
        they let Ctrl+C/V/X/A work even under a non-Latin (e.g. Hebrew)
        keyboard layout where event.keysym is a Hebrew letter.
        """
        cached = getattr(self, "_ctrl_keycodes_cache", None)
        if cached is not None:
            return cached
        if platform.system() == "Windows":
            codes = {67: "copy", 86: "paste", 88: "cut", 65: "all"}
        else:  # X11 / Linux hardware keycodes
            codes = {54: "copy", 55: "paste", 53: "cut", 38: "all"}
        self._ctrl_keycodes_cache = codes
        return codes

    def _add_edit_menu(self, widget):
        """Attach right-click + Ctrl-C/V/X/A editing to a Text or Entry widget.

        Also works when the keyboard layout is Hebrew: in that case Ctrl+C/V/X/A
        arrive as Hebrew keysyms (ב/ה/ס/ש) that the default bindings ignore, so
        we fall back to the physical keycode.
        """
        is_text = isinstance(widget, tk.Text)

        def cut(_=None):
            widget.event_generate("<<Cut>>")
            return "break"

        def copy(_=None):
            widget.event_generate("<<Copy>>")
            return "break"

        def paste(_=None):
            widget.event_generate("<<Paste>>")
            return "break"

        def select_all(_=None):
            if is_text:
                widget.tag_add("sel", "1.0", "end-1c")
            else:
                widget.select_range(0, tk.END)
                widget.icursor(tk.END)
            return "break"

        actions = {"cut": cut, "copy": copy, "paste": paste, "all": select_all}

        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Cut", command=cut)
        menu.add_command(label="Copy", command=copy)
        menu.add_command(label="Paste", command=paste)
        menu.add_separator()
        menu.add_command(label="Select all", command=select_all)

        def popup(event):
            widget.focus_set()
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        widget.bind("<Button-3>", popup)

        keycodes = self._ctrl_keycodes()

        def on_ctrl(event):
            ks = (event.keysym or "").lower()
            if ks in ("c", "v", "x", "a"):       # Latin layout
                return actions[{"c": "copy", "v": "paste",
                                "x": "cut", "a": "all"}[ks]]()
            action = keycodes.get(event.keycode)  # non-Latin layout fallback
            if action:
                return actions[action]()
            return None

        widget.bind("<Control-KeyPress>", on_ctrl)

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
        if self.workspace:
            name = os.path.basename(self.workspace.rstrip("/\\")) or self.workspace
            self._ws_chip.configure(text=f"📁 {name}  ✓")
        else:
            self._ws_chip.configure(text="")

    def _build_workspace_tab(self, nb):
        tab = tk.Frame(nb, bg=self.BG)
        nb.add(tab, text="Workspace")
        tk.Label(
            tab,
            text="Working folder — the model can read and write files in this folder.",
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
        self._add_edit_menu(path_entry)

        bar = tk.Frame(tab, bg=self.BG)
        bar.pack(fill=tk.X, padx=12, pady=(0, 6))

        def browse():
            path = filedialog.askdirectory(parent=tab.winfo_toplevel(),
                                           title="Choose workspace folder")
            if path:
                path_var.set(path)
                save_ws()   # apply immediately after browsing

        def save_ws():
            p = path_var.get().strip()
            if p and not os.path.isdir(p):
                messagebox.showwarning("Not found", f"Directory not found:\n{p}",
                                       parent=tab.winfo_toplevel())
                return
            self.workspace = p
            self.config["workspace"] = p
            save_config(self.config)
            self._update_ws_label()
            ws_name = os.path.basename(p.rstrip("/\\")) if p else ""
            self.set_status(f"📁 Workspace active: {ws_name}" if p else "Workspace disabled")

        def clear_ws():
            path_var.set("")
            save_ws()

        ttk.Button(bar, text="Browse…", command=browse).pack(side=tk.LEFT)
        ttk.Button(bar, text="Save",    command=save_ws).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Clear",   command=clear_ws).pack(side=tk.LEFT)

        # active-workspace indicator
        ind = tk.Label(tab, text="", bg=self.BG, fg=self.BOT_FG,
                       font=("Segoe UI", 9))
        ind.pack(anchor="w", padx=12, pady=(0, 4))

        def _update_ind():
            if self.workspace:
                ind.configure(text=f"✔  Active: {self.workspace}")
            else:
                ind.configure(text="(no workspace set)")

        _update_ind()
        path_var.trace_add("write", lambda *_: _update_ind())

        # write mode
        tk.Frame(tab, bg=self.BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)
        tk.Label(tab, text="Write permission", bg=self.BG, fg=self.TEXT_FG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(0, 4))
        _wm = tk.StringVar(value=self.write_mode)
        for val, label in (
            ("free",     "Allow writes without asking (default)"),
            ("confirm",  "Ask before every write (safe)"),
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

    # ----- CUDA / GPU tab ------------------------------------------------- #
    def _run_cmd(self, args):
        """Run a command and return its stdout, or None if it can't run / fails."""
        try:
            out = subprocess.run(
                args, capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return (out.stdout or "") + (out.stderr or "")

    def _detect_cuda(self):
        """
        Detect an NVIDIA GPU / CUDA installation.

        Returns a dict: {"ok": bool, "summary": str, "details": str}.
        Safe to call from a worker thread (no Tk access).
        """
        info = {"ok": False, "summary": "", "details": ""}

        smi = self._run_cmd(["nvidia-smi"])
        nvcc = self._run_cmd(["nvcc", "--version"])

        if smi is None and nvcc is None:
            info["summary"] = "✖  CUDA not detected — nvidia-smi / nvcc not found."
            info["details"] = (
                "Neither 'nvidia-smi' nor 'nvcc' was found on this system.\n"
                "This usually means no NVIDIA GPU driver / CUDA toolkit is "
                "installed, or it is not on PATH.\n\n"
                "Ollama will fall back to running models on the CPU."
            )
            return info

        driver_ver = cuda_runtime = nvcc_ver = None
        gpu_names = []

        if smi is not None:
            m = re.search(r"Driver Version:\s*([\d.]+)", smi)
            if m:
                driver_ver = m.group(1)
            m = re.search(r"CUDA Version:\s*([\d.]+)", smi)
            if m:
                cuda_runtime = m.group(1)
            q = self._run_cmd([
                "nvidia-smi", "--query-gpu=name,memory.total",
                "--format=csv,noheader",
            ])
            if q:
                gpu_names = [ln.strip() for ln in q.splitlines() if ln.strip()]

        if nvcc is not None:
            m = re.search(r"release\s*([\d.]+)", nvcc)
            if m:
                nvcc_ver = m.group(1)

        info["ok"] = smi is not None
        if smi is not None:
            info["summary"] = "✔  NVIDIA GPU detected — CUDA acceleration available."
        else:
            info["summary"] = ("⚠  CUDA toolkit found, but no running GPU driver "
                               "(nvidia-smi unavailable).")

        lines = []
        if gpu_names:
            lines.append("GPU(s):")
            lines.extend(f"  • {g}" for g in gpu_names)
        if driver_ver:
            lines.append(f"Driver version:       {driver_ver}")
        if cuda_runtime:
            lines.append(f"CUDA (driver max):    {cuda_runtime}")
        if nvcc_ver:
            lines.append(f"CUDA toolkit (nvcc):  {nvcc_ver}")
        elif smi is not None:
            lines.append("CUDA toolkit (nvcc):  not installed "
                         "(only the driver runtime is present — fine for Ollama).")
        info["details"] = "\n".join(lines)
        return info

    def _cuda_update_help(self):
        """Platform-specific guidance for updating the NVIDIA driver / CUDA."""
        system = platform.system()
        if system == "Windows":
            return (
                "1. Update the NVIDIA driver (it includes the CUDA runtime):\n"
                "   • Open the NVIDIA App / GeForce Experience → Drivers → "
                "Check for updates, or\n"
                "   • Download from https://www.nvidia.com/Download/index.aspx\n"
                "2. (Optional) CUDA Toolkit for development:\n"
                "   • https://developer.nvidia.com/cuda-downloads\n"
                "3. Restart, then click “Check now” above to confirm "
                "the new version.\n\n"
                "Ollama only needs the driver — the full toolkit is optional."
            )
        if system == "Linux":
            return (
                "1. Update the NVIDIA driver (it includes the CUDA runtime):\n"
                "   • Ubuntu/Debian:  sudo apt update && "
                "sudo apt install --only-upgrade nvidia-driver-XXX\n"
                "   • Or simply:  sudo ubuntu-drivers autoinstall\n"
                "2. (Optional) CUDA Toolkit:  "
                "https://developer.nvidia.com/cuda-downloads\n"
                "3. Reboot, then run “nvidia-smi” or click "
                "“Check now” above.\n\n"
                "Ollama only needs the driver — the full toolkit is optional."
            )
        return (
            "NVIDIA CUDA is not available on this platform. Modern macOS uses "
            "Apple Silicon (Metal) acceleration, which Ollama supports "
            "natively — no CUDA needed."
        )

    def _build_cuda_tab(self, nb):
        tab = tk.Frame(nb, bg=self.BG)
        nb.add(tab, text="CUDA / GPU")

        tk.Label(
            tab,
            text="Check whether an NVIDIA GPU with CUDA is available so Ollama "
                 "can use GPU acceleration.",
            bg=self.BG, fg=self.MUTED_FG, font=("Segoe UI", 9),
            wraplength=540, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        status = tk.Label(
            tab, text="Not checked yet.", bg=self.BG, fg=self.MUTED_FG,
            font=("Segoe UI", 10, "bold"), wraplength=540, justify="left",
        )
        status.pack(anchor="w", padx=12, pady=(0, 4))

        details = self._make_textbox(tab, height=7)
        details.pack(fill=tk.X, padx=12, pady=(0, 6))
        details.configure(state=tk.DISABLED)

        def set_details(text):
            details.configure(state=tk.NORMAL)
            details.delete("1.0", tk.END)
            details.insert("1.0", text)
            details.configure(state=tk.DISABLED)

        btn_bar = tk.Frame(tab, bg=self.BG)
        btn_bar.pack(fill=tk.X, padx=12, pady=(0, 6))
        check_btn = ttk.Button(btn_bar, text="Check now")
        check_btn.pack(side=tk.LEFT)

        def apply_result(result):
            if not status.winfo_exists():
                return
            status.configure(
                text=result["summary"],
                fg=self.BOT_FG if result["ok"] else "#dc2626",
            )
            set_details(result["details"])
            check_btn.configure(state=tk.NORMAL, text="Check now")

        def check():
            check_btn.configure(state=tk.DISABLED, text="Checking…")
            status.configure(text="Checking…", fg=self.MUTED_FG)
            set_details("")

            def work():
                result = self._detect_cuda()
                self.ui_queue.put(("ui_call", lambda: apply_result(result)))

            threading.Thread(target=work, daemon=True).start()

        check_btn.configure(command=check)

        tk.Frame(tab, bg=self.BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)
        tk.Label(
            tab, text="How to update CUDA", bg=self.BG, fg=self.TEXT_FG,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(0, 4))
        tk.Label(
            tab, text=self._cuda_update_help(), bg=self.BG, fg=self.MUTED_FG,
            font=("Segoe UI", 9), justify="left", wraplength=540,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        # run an initial check automatically when the tab opens
        check()

    def _start_agent(self, model):
        self._append(f"{model}  ·  📁 workspace\n", "bot")
        self.streaming = True
        self.stop_event.clear()
        self._set_busy(True)
        
        self.set_status("Working in workspace…")

        system = self.build_system_prompt()
        ws_note = (
            f"WORKSPACE FOLDER: {self.workspace}\n"
            "You have access to the user's workspace folder via these tools: "
            "list_files, read_file, write_file. All paths must be RELATIVE to the workspace.\n"
            "IMPORTANT RULES:\n"
            "- When asked to create, write, or save ANY file or code, you MUST call "
            "write_file to save it to the workspace. Do NOT just show the code as text.\n"
            "- When asked to MODIFY, UPDATE, EDIT, or ADD TO an existing file, you MUST "
            "first call read_file to get the current content, then produce the complete "
            "updated content and call write_file to overwrite the file. "
            "write_file always overwrites — include ALL content, not just the changed parts.\n"
            "- Use list_files to discover what files already exist before deciding whether "
            "to create a new file or update an existing one.\n"
            "- Always confirm to the user which file you wrote and its relative path."
        )
        system = f"{system}\n\n{ws_note}".strip() if system else ws_note
        convo = [{"role": "system", "content": system}] + list(self.messages)

        self.worker = threading.Thread(
            target=self._run_agent, args=(model, convo), daemon=True
        )
        self.worker.start()

    def _run_agent(self, model, convo):
        """Worker thread: loop over tool calls until the model gives an answer."""
        # Everything appended past this point is new this turn (assistant
        # tool-call messages, tool results, and the final answer). We hand it
        # back so it can be kept in the conversation history — otherwise the
        # model forgets which files it created and can't update them later.
        base_len = len(convo)
        try:
            for _ in range(MAX_AGENT_STEPS):
                if self.stop_event.is_set():
                    self.ui_queue.put(
                        ("agent_done", ("(Stopped.)", convo[base_len:]))
                    )
                    return
                resp = agent_chat_once(OLLAMA_HOST, model, convo, TOOLS)
                msg = resp.get("message", {}) or {}
                calls = msg.get("tool_calls") or []
                if not calls:
                    convo.append(msg)  # keep the final answer in history
                    text = msg.get("content", "") or "(No response.)"
                    self.ui_queue.put(("agent_done", (text, convo[base_len:])))
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
                    # show the file content being written, as a code block
                    if name == "write_file":
                        self.ui_queue.put((
                            "tool_code",
                            (args.get("path", ""), args.get("content", "")),
                        ))
                    result = self._exec_tool(name, args)
                    convo.append(
                        {"role": "tool", "content": result, "tool_name": name}
                    )
            self.ui_queue.put(
                ("agent_done",
                 ("(Reached the tool-step limit.)", convo[base_len:]))
            )
        except urllib.error.URLError as exc:
            self.ui_queue.put(("error", f"Connection error: {exc.reason}"))
        except Exception as exc:  # noqa: BLE001
            self.ui_queue.put(("error", f"Agent error: {exc}"))

    @staticmethod
    def _lang_from_path(path):
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        return {
            "py": "python", "js": "javascript", "ts": "typescript",
            "jsx": "javascript", "tsx": "typescript", "html": "html",
            "css": "css", "json": "json", "sh": "bash", "bash": "bash",
            "java": "java", "c": "c", "cpp": "cpp", "h": "c", "go": "go",
            "rs": "rust", "rb": "ruby", "php": "php", "sql": "sql",
            "yml": "yaml", "yaml": "yaml", "xml": "xml", "md": "markdown",
        }.get(ext, ext or "text")

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

    def _finish_agent(self, payload):
        # payload is (display_text, new_messages); stay backward-compatible
        if isinstance(payload, tuple):
            text, new_msgs = payload
        else:
            text, new_msgs = payload, None
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
        # Persist the full agent exchange (tool calls + results + final answer)
        # so follow-up messages remember the files it created and can update
        # them. Fall back to just the text for older/empty payloads.
        if new_msgs:
            self.messages.extend(new_msgs)
        elif text.strip():
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
                "STT not available",
                "Install the required packages:\n\npip install SpeechRecognition pyaudio",
            )
            return
        if self.streaming or self._mic_recording:
            return
        self.set_status("🎙 Listening… speak now")
        self._set_mic_recording(True)

        lang = self.stt_lang

        def work():
            try:
                r = _sr.Recognizer()
                try:
                    mic = _sr.Microphone()
                except OSError as exc:
                    self.ui_queue.put((
                        "stt_error",
                        f"No access to the microphone: {exc}\n"
                        "Make sure pyaudio is installed (pip install pyaudio) "
                        "and a microphone is connected."
                    ))
                    return
                with mic as source:
                    r.adjust_for_ambient_noise(source, duration=0.3)
                    audio = r.listen(source, timeout=10, phrase_time_limit=60)
                text = r.recognize_google(audio, language=lang)
                self.ui_queue.put(("stt_result", text))
            except _sr.WaitTimeoutError:
                self.ui_queue.put(("stt_error", "No speech detected — try again"))
            except _sr.UnknownValueError:
                self.ui_queue.put(("stt_error", "Could not understand — speak clearly"))
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
                f"{m.get('role', '').upper()}: {m.get('content', '')}"
                for m in convo[-12:]
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