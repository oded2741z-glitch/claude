import socket
import contextlib
import json
import threading
import time
import ipaddress
import tkinter as tk
from tkinter import messagebox
import sounddevice as sd
import numpy as np
import os
from collections import deque
from typing import Optional, Tuple, Dict, Any, NamedTuple

# --- Constants & Settings ---
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
CHUNK_SIZE: int = 512
DTYPE: str = 'int16'
BUFFER_SIZE: int = 65535          # UDP חותך בשקט חבילות גדולות מהבאפר
TIMEOUT_SECS: float = 3.0
SETTINGS_FILE: str = "settings_A.txt"

# --- P2P wire protocol ---
# חבילות סיגנלינג הן JSON ומתחילות ב-'{'. חבילות P2P מתויגות בבייט ראשון,
# כדי שלעולם לא ננגן הודעת בקרה כרעש.
AUDIO_TAG: bytes = b'\x01'
PUNCH_TAG: bytes = b'\x02'
PUNCH_PACKET: bytes = PUNCH_TAG + b'PUNCH'
P2P_TAGS: Tuple[bytes, bytes] = (AUDIO_TAG, PUNCH_TAG)

# --- Signalling status values (חייב להיות זהה ל-clint.py) ---
# ללא status = רישום ("יש אוזניות, חפש לי עמית")
STATUS_ALIVE: str = "alive"                 # פעימה במהלך שיחה: האוזניות עדיין מחוברות
STATUS_LEFT: str = "left"                   # יציאה מהשיחה - מצב האוזניות לא השתנה
STATUS_DISCONNECTED: str = "disconnected"   # האוזניות באמת נשלפו

# --- Signalling server behaviour ---
CLIENT_TTL: float = 30.0          # רישום ישן לא ישמש להצמדה
MATCH_RETRIES: int = 5            # UDP לא אמין - שולחים את פרטי העמית כמה פעמים
MATCH_RETRY_DELAY: float = 0.05
HP_TIMEOUT: float = 8.0           # בלי דיווח מהלקוח בטווח הזה - אובדן קשר
PUNCH_DURATION: float = 2.0
PUNCH_INTERVAL: float = 0.1
HW_REFRESH_GAP: float = 5.0       # מרווח מזערי בין אתחולי PortAudio
STREAM_CLOSE_WAIT: float = 5.0    # סגירת stream על התקן שנשלף עלולה להיתקע
HEARTBEAT_INTERVAL: float = 1.0   # פעימה לשרת הסיגנלינג במהלך שיחה
REPORT_RETRIES: int = 4           # כל דיווח מצב נשלח כמה פעמים (UDP לא אמין)
REPORT_RETRY_DELAY: float = 0.04

# --- Acoustic echo suppression (voice switch) ---
# ההד נולד בצד שמשמיע ברמקול: המיקרופון שלו קולט את קול הצד השני ומחזיר
# אותו. הפתרון כאן הוא voice switch פשוט - מנמיכים את המיקרופון המקומי כל
# עוד הצד השני מדבר. אין תלות בספריות DSP, ועובד גם מול רמקולים.
ECHO_SUPPRESSION: bool = True
FAR_END_ACTIVE_RMS: float = 300.0   # מעל זה נחשב "הצד השני מדבר" (int16 RMS)
DUCK_HANGOVER: float = 0.25         # כמה זמן להנמיך אחרי המילה האחרונה שנקלטה
DUCK_GAIN: float = 0.05             # לא אפס: החבילות חייבות להמשיך לזרום,
                                    # אחרת הצד השני יכריז ניתוק אחרי 3 שניות
BREAK_IN_RATIO: float = 2.5         # דיבור חזק פי כך מהנכנס גובר על ההנמכה


def audio_rms(block) -> float:
    """עוצמת בלוק אודיו. float32 כדי שלא תהיה גלישה בריבוע של int16."""
    if block is None or block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block.astype(np.float32)))))

# --- Dashboard ---
UI_TICK_MS: int = 200             # קצב רענון המחוונים
PEER_IDLE: float = 1.5            # אין אודיו מעבר לזה -> נורית הקליינט כבר לא ירוקה


class ClientState(NamedTuple):
    """מצב האוזניות של לקוח מרוחק, כפי שדווח לשרת."""
    connected: bool
    since: float        # מתי המצב השתנה (מוצג ליד השורה)
    last_seen: float    # מתי התקבל ממנו דיווח אחרון (בסיס לזיהוי אובדן קשר)
    note: str = ""      # הסבר קצר כשהמצב נגזר מאובדן קשר ולא מדיווח מפורש


class Theme:
    """Centralized configuration for UI colors and fonts."""
    BG: str = "#121212"
    FG: str = "#FFFFFF"
    BTN_BG: str = "#333333"
    ACCENT: str = "#ff7300"
    QUIT: str = "#ff0000"
    ENTRY_BG: str = "#222222"
    LOG_BG: str = "#1e1e1e"
    LOG_FG: str = "#FFFFFF"
    DIVIDER: str = "#444444"
    DISCONNECTED: str = "#888888"
    CONNECTED: str = "#00ff00"
    WAITING: str = "#f0ad4e"
    PUNCHING: str = "#0275d8"
    LED_OFF: str = "#3a3a3a"

    FONT_TITLE: Tuple[str, int, str] = ("Consolas", 11, "bold")
    FONT_LABEL: Tuple[str, int, str] = ("Arial", 10, "bold")
    FONT_ENTRY: Tuple[str, int] = ("Arial", 9)
    FONT_LOG: Tuple[str, int] = ("Consolas", 9)


HELP_TEXT: str = (
    "P2P Intercom\n\n"
    "1. Start the internal signalling server on one machine (or point both\n"
    "   clients at an external one).\n"
    "2. Set Server IP / Port / My ID on both sides - the two IDs must differ.\n"
    "3. Press START INTERCOM on both. The server matches the first two\n"
    "   clients it sees and hands each one the other's address.\n\n"
    "CLIENT HEADPHONES row: every client keeps reporting its state - while\n"
    "waiting for a device, while registering and (as a heartbeat) during a\n"
    "call. A client that stops reporting for 8 seconds is shown as lost,\n"
    "so a dropped UDP packet can never leave a stale 'connected'.\n\n"
    "External IP: only relevant when the signalling server is NOT on the\n"
    "public internet. The port is never translated, so this requires a\n"
    "static port forward on the router. On a single LAN, tick Local Mode.\n\n"
    "Hole punching works with cone NAT. Symmetric NAT needs a relay (TURN)."
)


class IntercomGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root: tk.Tk = root
        self.root.geometry("480x640")
        self.root.configure(bg=Theme.BG)
        self.root.overrideredirect(True)

        # Threading & Concurrency Flags
        self.audio_lock: threading.Lock = threading.Lock()
        self.last_refresh_time: float = 0.0
        self._open_streams: int = 0          # כמה streams פתוחים כרגע (מוגן ע"י audio_lock)

        self.sock: Optional[socket.socket] = None
        self.is_running: bool = False
        self.rx_thread: Optional[threading.Thread] = None
        self.tx_thread: Optional[threading.Thread] = None
        self.hb_thread: Optional[threading.Thread] = None
        self._stop_lock: threading.Lock = threading.Lock()

        # Peer address handling
        self.peer_lock: threading.Lock = threading.Lock()
        self.assigned_peer: Optional[Tuple[str, int]] = None
        self.locked_peer: Optional[Tuple[str, int]] = None
        self.tx_peer: Optional[Tuple[str, int]] = None

        # Signalling target (נשמר כדי לדווח ניתוק בסגירה)
        self.signalling_addr: Optional[Tuple[str, int]] = None
        self.my_id: str = ""
        self.peer_id: str = ""

        # Dashboard state (נכתב מתרדי הרקע, נקרא מתרד ה-UI)
        self._events: deque = deque(maxlen=200)
        self._last_rx: float = 0.0
        self._call_started: float = 0.0
        # Echo suppression state (נכתב ע"י תרד הקליטה, נקרא ע"י תרד השידור)
        self._far_end_active: float = 0.0
        self._far_end_level: float = 0.0
        self._server_port: int = 0
        self._server_error: str = ""
        # מצב אוזניות של לקוחות מרוחקים בלבד: id -> ClientState
        self._hp_lock: threading.Lock = threading.Lock()
        self._remote_clients: Dict[str, ClientState] = {}

        self.server_thread: Optional[threading.Thread] = None
        self.server_stop_event: threading.Event = threading.Event()
        self._rewrite_warned: bool = False

        # Window Drag Coordinates
        self._offset_x: int = 0
        self._offset_y: int = 0

        self._create_widgets()
        self.load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._ui_tick()

    def start_move(self, event: tk.Event) -> None:
        """Records initial coordinates for borderless window dragging."""
        self._offset_x = event.x
        self._offset_y = event.y

    def do_move(self, event: tk.Event) -> None:
        """Calculates and applies new window position during a drag event."""
        x: int = self.root.winfo_x() + event.x - self._offset_x
        y: int = self.root.winfo_y() + event.y - self._offset_y
        self.root.geometry(f"+{x}+{y}")

    def _create_widgets(self) -> None:
        """Initializes and packs all Tkinter widgets using the Theme class."""
        # --- Custom Title Bar ---
        title_bar = tk.Frame(self.root, bg=Theme.BG)
        title_bar.pack(fill="x", pady=5, padx=5)
        title_bar.bind("<ButtonPress-1>", self.start_move)
        title_bar.bind("<B1-Motion>", self.do_move)

        title_lbl = tk.Label(title_bar, text="Intercom Server", font=Theme.FONT_TITLE,
                             bg=Theme.BG, fg=Theme.ACCENT)
        title_lbl.pack(side="left", padx=10)
        title_lbl.bind("<ButtonPress-1>", self.start_move)
        title_lbl.bind("<B1-Motion>", self.do_move)

        tk.Button(title_bar, text="Quit", bg=Theme.QUIT, fg=Theme.FG, font=Theme.FONT_LABEL,
                  relief="flat", width=6, command=self.on_close, activebackground="#cc0000",
                  activeforeground=Theme.FG).pack(side="right", padx=2)

        tk.Button(title_bar, text="Help", bg=Theme.BTN_BG, fg=Theme.FG, font=Theme.FONT_LABEL,
                  relief="flat", width=6, command=self.show_help, activebackground=Theme.DIVIDER,
                  activeforeground=Theme.FG).pack(side="right", padx=2)

        tk.Frame(self.root, bg=Theme.DIVIDER, height=1).pack(fill="x", padx=10, pady=5)

        # --- Integrated Signalling Server ---
        tk.Label(self.root, text="Integrated Signalling Server", font=Theme.FONT_LABEL, bg=Theme.BG, fg=Theme.FG).pack(anchor="w", padx=15, pady=(10, 0))
        server_frame = tk.Frame(self.root, bg=Theme.BG)
        server_frame.pack(fill="x", padx=20, pady=5)

        tk.Label(server_frame, text="External IP:", bg=Theme.BG, fg=Theme.FG, font=Theme.FONT_ENTRY).grid(row=0, column=0, sticky="w", pady=5)
        self.ext_ip_entry = tk.Entry(server_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG,
                                     relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=20)
        self.ext_ip_entry.grid(row=0, column=1, padx=10, pady=5, sticky="w")

        self.local_mode_var = tk.BooleanVar(value=False)
        tk.Checkbutton(server_frame, text="Use Local Mode (Ignore External IP)", variable=self.local_mode_var,
                       command=self.save_settings, bg=Theme.BG, fg=Theme.FG, selectcolor=Theme.BTN_BG,
                       activebackground=Theme.BG, activeforeground=Theme.FG).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

        self.server_btn = tk.Button(server_frame, text="START INTERNAL SERVER", font=("Arial", 9, "bold"),
                                    bg=Theme.BTN_BG, fg=Theme.FG, relief="flat", width=35, pady=4,
                                    command=self.toggle_local_server, activebackground=Theme.DIVIDER, activeforeground=Theme.FG)
        self.server_btn.grid(row=2, column=0, columnspan=2, pady=10)

        tk.Frame(self.root, bg=Theme.DIVIDER, height=1).pack(fill="x", padx=10, pady=5)

        # --- Connection Settings ---
        tk.Label(self.root, text="Connection Settings", font=Theme.FONT_LABEL, bg=Theme.BG, fg=Theme.FG).pack(anchor="w", padx=15, pady=(5, 0))
        config_frame = tk.Frame(self.root, bg=Theme.BG)
        config_frame.pack(fill="x", padx=20, pady=5)

        tk.Label(config_frame, text="Server IP:", bg=Theme.BG, fg=Theme.FG).grid(row=0, column=0, sticky="w", pady=5)
        self.server_ip_entry = tk.Entry(config_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG, relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=18)
        self.server_ip_entry.grid(row=0, column=1, pady=5, padx=5)

        tk.Label(config_frame, text="Port:", bg=Theme.BG, fg=Theme.FG).grid(row=0, column=2, sticky="w", pady=5, padx=(10, 0))
        self.server_port_entry = tk.Entry(config_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG, relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=7)
        self.server_port_entry.grid(row=0, column=3, pady=5, padx=5)

        tk.Label(config_frame, text="My ID:", bg=Theme.BG, fg=Theme.FG).grid(row=1, column=0, sticky="w", pady=5)
        self.my_id_entry = tk.Entry(config_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG, relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=18)
        self.my_id_entry.grid(row=1, column=1, pady=5, padx=5)

        self.status_label = tk.Label(self.root, text="STATUS: DISCONNECTED", font=Theme.FONT_LABEL, bg=Theme.BG, fg=Theme.DISCONNECTED)
        self.status_label.pack(pady=10)

        # --- Main Toggle Button ---
        btn_border = tk.Frame(self.root, bg=Theme.FG, padx=1, pady=1)
        btn_border.pack(pady=5)
        self.toggle_btn = tk.Button(btn_border, text="START INTERCOM", font=("Consolas", 12, "bold"),
                                    bg=Theme.ACCENT, fg="#000000", activebackground="#e66a00", activeforeground="#000000",
                                    relief="flat", width=42, pady=6, command=self.toggle_intercom)
        self.toggle_btn.pack()

        # --- Live Dashboard (replaces the scrolling text log) ---
        self._create_dashboard()

        tk.Label(self.root, text="oT", font=("Arial", 9, "bold"), bg=Theme.BG, fg=Theme.DIVIDER).pack(side="bottom", anchor="e", padx=10, pady=5)

    def _create_dashboard(self) -> None:
        """Three status rows: signalling server, client link, client headphones."""
        dash = tk.Frame(self.root, bg=Theme.BG)
        dash.pack(fill="both", expand=True, padx=15, pady=(5, 5))

        self.rows: Dict[str, Tuple[tk.Canvas, int, tk.Label]] = {}
        for key, title in (("SERVER", "SERVER CONNECTION"),
                           ("CLIENT", "CLIENT CONNECTION"),
                           ("HEADPHONES", "CLIENT HEADPHONES")):
            card = tk.Frame(dash, bg=Theme.LOG_BG, padx=10, pady=8)
            card.pack(fill="x", pady=4)

            head = tk.Frame(card, bg=Theme.LOG_BG)
            head.pack(fill="x")
            canvas = tk.Canvas(head, width=16, height=16, bg=Theme.LOG_BG, highlightthickness=0)
            canvas.pack(side="left")
            dot = canvas.create_oval(3, 3, 14, 14, fill=Theme.LED_OFF, outline="")
            tk.Label(head, text=title, bg=Theme.LOG_BG, fg=Theme.FG,
                     font=Theme.FONT_LABEL).pack(side="left", padx=(6, 0))

            detail = tk.Label(card, text="-", anchor="w", bg=Theme.LOG_BG, fg=Theme.DISCONNECTED,
                              font=Theme.FONT_LOG)
            detail.pack(fill="x", padx=(22, 0), pady=(2, 0))
            self.rows[key] = (canvas, dot, detail)

    def show_help(self) -> None:
        messagebox.showinfo("Help", HELP_TEXT)

    def log(self, msg: str, color_tag: str = "white") -> None:
        """Events go to stdout only - the dashboard carries the state itself."""
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self._events.append(line)

    # ------------------------------------------------------------------
    # Dashboard refresh
    # ------------------------------------------------------------------
    def _set_row(self, key: str, color: str, text: str, text_color: Optional[str] = None) -> None:
        canvas, dot, detail = self.rows[key]
        canvas.itemconfig(dot, fill=color)
        detail.config(text=text, fg=text_color or (Theme.FG if color != Theme.LED_OFF else Theme.DISCONNECTED))

    def _refresh_server_row(self) -> None:
        server_up = self.server_thread is not None and self.server_thread.is_alive()
        if server_up:
            self._set_row("SERVER", Theme.CONNECTED, f"internal server listening on port {self._server_port}")
        elif self._server_error:
            self._set_row("SERVER", Theme.QUIT, self._server_error, Theme.QUIT)
        elif self.is_running and self.signalling_addr:
            # לא מארחים שרת, אבל רשומים מול שרת חיצוני
            host, port = self.signalling_addr
            if self.assigned_peer:
                self._set_row("SERVER", Theme.CONNECTED, f"registered with {host}:{port}")
            else:
                self._set_row("SERVER", Theme.WAITING, f"registering with {host}:{port}...")
        else:
            self._set_row("SERVER", Theme.LED_OFF, "stopped")

    def _refresh_client_row(self, now: float) -> None:
        with self.peer_lock:
            peer, locked = self.assigned_peer, self.locked_peer
        if not self.is_running:
            self._set_row("CLIENT", Theme.LED_OFF, "not connected")
        elif peer is None:
            self._set_row("CLIENT", Theme.WAITING, "waiting for a peer...")
        elif locked and now - self._last_rx < PEER_IDLE:
            elapsed = int(now - self._call_started) if self._call_started else 0
            name = self.peer_id or "peer"
            self._set_row("CLIENT", Theme.CONNECTED,
                          f"{name}  ·  {locked[0]}:{locked[1]}  ·  {elapsed // 60:02d}:{elapsed % 60:02d}")
        else:
            state = "no audio from peer" if self._call_started else "punching NAT..."
            self._set_row("CLIENT", Theme.WAITING, f"{peer[0]}:{peer[1]}  ·  {state}")

    # ------------------------------------------------------------------
    # Remote headphone state
    # ------------------------------------------------------------------
    def _mark_headphones(self, client_id: str, connected: bool, note: str = "") -> bool:
        """Records a REMOTE client's headphone state. True on a real change."""
        if not client_id or client_id == self.my_id:
            return False  # המצב שלנו לא מעניין - השורה נועדה להראות את הצד השני
        now = time.time()
        with self._hp_lock:
            previous = self._remote_clients.get(client_id)
            if previous is not None and previous.connected == connected:
                # אותו מצב - לא מאפסים את חותמת השינוי, רק מרעננים את הקשר
                self._remote_clients[client_id] = previous._replace(last_seen=now, note="")
                return False
            self._remote_clients[client_id] = ClientState(connected, now, now, note)
        return True

    def _touch_headphones(self, client_id: str) -> None:
        """דיווח שאינו נוגע למצב האוזניות (יציאה משיחה) - רק מרענן את הקשר."""
        if not client_id or client_id == self.my_id:
            return
        now = time.time()
        with self._hp_lock:
            previous = self._remote_clients.get(client_id)
            if previous is not None:
                self._remote_clients[client_id] = previous._replace(last_seen=now, note="")

    def _expire_headphone_reports(self, now: float) -> None:
        """לקוח שהפסיק לדווח נחשב מנותק, גם אם חבילת הניתוק שלו אבדה.

        זו הרשת הביטחון של החיווי: הלקוח משדר את מצבו כל 1-2 שניות, ולכן
        שתיקה של 8 שניות פירושה שהאוזניות (או התהליך) כבר לא שם.
        """
        lost = []
        with self._hp_lock:
            for cid, state in list(self._remote_clients.items()):
                if state.connected and now - state.last_seen > HP_TIMEOUT:
                    self._remote_clients[cid] = ClientState(False, now, state.last_seen, "no contact")
                    lost.append(cid)
        for cid in lost:
            self.log(f"[Server] {cid}: no report for {HP_TIMEOUT:.0f}s - "
                     f"Headphones disconnected !!!", "red")

    def _refresh_headphones_row(self) -> None:
        with self._hp_lock:
            entries = sorted(self._remote_clients.items(), key=lambda kv: kv[1].since, reverse=True)
        if not entries:
            self._set_row("HEADPHONES", Theme.LED_OFF, "waiting for a client to report...")
            return

        client_id, state = entries[0]
        stamp = time.strftime('%H:%M:%S', time.localtime(state.since))
        extra = f"   (+{len(entries) - 1} more)" if len(entries) > 1 else ""
        if state.connected:
            # הרמז שבגללו השורה קיימת: הצד השני מוכן, אפשר לפתוח שיחה
            hint = "   ->  press START INTERCOM" if not self.is_running else ""
            self._set_row("HEADPHONES", Theme.CONNECTED,
                          f"{client_id} connected  ·  {stamp}{extra}{hint}")
        elif state.note:
            # מצב שנגזר משתיקת הלקוח ולא מדיווח מפורש - מסומן אחרת
            self._set_row("HEADPHONES", Theme.WAITING,
                          f"{client_id} disconnected ({state.note})  ·  {stamp}{extra}", Theme.WAITING)
        else:
            self._set_row("HEADPHONES", Theme.QUIT,
                          f"{client_id} disconnected  ·  {stamp}{extra}", Theme.QUIT)

    def _ui_tick(self) -> None:
        """Single periodic redraw. Worker threads only write plain values."""
        now = time.time()
        try:
            # קריאה מתרד ה-UI, כדי שנדע את ה-ID שלנו עוד לפני לחיצה על START
            self.my_id = self.my_id_entry.get().strip()
            self._expire_headphone_reports(now)
            self._refresh_server_row()
            self._refresh_client_row(now)
            self._refresh_headphones_row()
            self.root.after(UI_TICK_MS, self._ui_tick)
        except tk.TclError:
            return  # החלון נסגר

    def update_status(self, text: str, color: str) -> None:
        """Thread-safe status label update."""
        def apply() -> None:
            try:
                self.status_label.config(text=f"STATUS: {text}", fg=color)
            except tk.TclError:
                pass
        self.root.after(0, apply)

    def save_settings(self) -> None:
        """Serializes current UI entries into a localized JSON config."""
        data: Dict[str, Any] = {
            "ext_ip": self.ext_ip_entry.get().strip(),
            "local_mode": self.local_mode_var.get(),
            "ip": self.server_ip_entry.get().strip(),
            "port": self.server_port_entry.get().strip(),
            "my_id": self.my_id_entry.get().strip()
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            self.log(f"Error saving settings: {e}")

    def load_settings(self) -> None:
        """Parses the JSON settings file and populates UI elements."""
        if not os.path.exists(SETTINGS_FILE):
            self.ext_ip_entry.insert(0, "10.20.1.200")
            self.server_ip_entry.insert(0, "127.0.0.1")
            self.server_port_entry.insert(0, "9999")
            self.my_id_entry.insert(0, "node_A")
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)

            for entry, key, default in [
                (self.ext_ip_entry, "ext_ip", "10.20.1.200"),
                (self.server_ip_entry, "ip", "127.0.0.1"),
                (self.server_port_entry, "port", "9999"),
                (self.my_id_entry, "my_id", "node_A")
            ]:
                entry.delete(0, tk.END)
                entry.insert(0, str(data.get(key, default)))

            self.local_mode_var.set(data.get("local_mode", False))
        except (OSError, json.JSONDecodeError) as e:
            self.log(f"Error loading settings: {e}")

    # ------------------------------------------------------------------
    # Signalling server
    # ------------------------------------------------------------------
    def toggle_local_server(self) -> None:
        if self.server_thread is None or not self.server_thread.is_alive():
            self.start_local_server()
        else:
            self.stop_local_server()

    def start_local_server(self) -> None:
        try:
            server_port: int = int(self.server_port_entry.get().strip())
        except ValueError:
            self.log("Failed to start server: Invalid port number.", "red")
            return

        ext_ip: str = self.ext_ip_entry.get().strip()
        use_local: bool = self.local_mode_var.get()

        self.server_stop_event.clear()
        self._rewrite_warned = False
        self._server_port = server_port
        self._server_error = ""
        # הכפתור מתעדכן רק אחרי ש-bind באמת הצליח
        self.server_btn.config(text="STARTING SERVER...", bg=Theme.BTN_BG, fg=Theme.FG)
        self.server_thread = threading.Thread(target=self._run_internal_server,
                                              args=(server_port, ext_ip, use_local), daemon=True)
        self.server_thread.start()

    def stop_local_server(self) -> None:
        self.server_stop_event.set()
        if self.server_thread and self.server_thread.is_alive():
            # לסוקט השרת יש timeout של שנייה - נותנים לו מספיק זמן לשחרר את הפורט
            self.server_thread.join(timeout=2.5)
        self.server_thread = None
        # בלי שרת אין דיווחים, ולכן כל מצב מוצג יהיה ניחוש ישן
        with self._hp_lock:
            self._remote_clients.clear()
        self._mark_server_stopped()
        self.log("Internal Signalling Server STOPPED.")

    def _mark_server_started(self, port: int) -> None:
        self.server_btn.config(text="STOP INTERNAL SERVER", bg=Theme.ACCENT, fg="#000000")
        self.log(f"Internal Signalling Server STARTED on port {port}.", "green")

    def _mark_server_stopped(self) -> None:
        try:
            self.server_btn.config(text="START INTERNAL SERVER", bg=Theme.BTN_BG, fg=Theme.FG)
        except tk.TclError:
            pass

    @staticmethod
    def _parse_json_dict(data: bytes) -> Optional[Dict[str, Any]]:
        """מפענח הודעת סיגנלינג. מחזיר None לכל דבר שאינו JSON dict תקין."""
        try:
            msg = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None  # חבילת אודיו / PUNCH / זבל
        return msg if isinstance(msg, dict) else None

    def _visible_ip(self, ip: str, ext_ip: str, use_local: bool) -> str:
        """בוחר את הכתובת שתדווח לעמית. מכסה את כל הטווחים הפרטיים, לא רק 192.168."""
        if use_local or not ext_ip:
            return ip
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if not (parsed.is_private or parsed.is_loopback):
            return ip
        if not self._rewrite_warned:
            self._rewrite_warned = True
            self.log("[Server] Rewriting private IPs to External IP. Note: the PORT is "
                     "not translated, so this needs a static port forward.", "yellow")
        return ext_ip

    def _purge_stale(self, clients: Dict[str, Tuple[Tuple[str, int], float]], now: float) -> None:
        for cid in [c for c, (_, ts) in clients.items() if now - ts > CLIENT_TTL]:
            del clients[cid]
            self.log(f"[Server] {cid}: registration expired.", "yellow")

    def _handle_signalling(self, server_sock: socket.socket,
                           clients: Dict[str, Tuple[Tuple[str, int], float]],
                           data: bytes, addr: Tuple[str, int],
                           ext_ip: str, use_local: bool) -> None:
        msg = self._parse_json_dict(data)
        if msg is None:
            return

        client_id = msg.get("id")
        if not isinstance(client_id, str) or not client_id.strip():
            return
        client_id = client_id.strip()
        status = msg.get("status")

        # --- דיווחי מצב שאינם רישום ---
        # חשוב שלא ייכנסו לבריכת ההצמדה: כל פעימה הייתה גורמת להצמדה חוזרת
        # באמצע שיחה.
        if status == STATUS_DISCONNECTED:
            # האוזניות באמת נשלפו. הלקוח שולח את זה כמה פעמים וגם חוזר על כך
            # כל 2 שניות כל עוד אין התקן, ולכן אובדן חבילה כבר לא מסתיר אירוע.
            clients.pop(client_id, None)
            if self._mark_headphones(client_id, False):
                self.log(f"[Server] {client_id}: Headphones disconnected !!!", "red")
            return

        if status == STATUS_LEFT:
            # יציאה מהשיחה (timeout / DISCONNECT ידני) - האוזניות עדיין שם
            clients.pop(client_id, None)
            self._touch_headphones(client_id)
            return

        if status == STATUS_ALIVE:
            # פעימה במהלך שיחה: מוכיחה שהאוזניות והתהליך חיים
            if self._mark_headphones(client_id, True):
                self.log(f"[Server] {client_id}: Headphones connected !!!", "purple")
            return

        # --- רישום רגיל: יש אוזניות, מחפש עמית ---
        now = time.time()
        self._purge_stale(clients, now)

        if self._mark_headphones(client_id, True):
            self.log(f"[Server] {client_id}: Headphones connected !!!", "purple")
        clients[client_id] = (addr, now)

        if len(clients) == 2:
            (peer1_id, (addr1, _)), (peer2_id, (addr2, _)) = clients.items()
            self.log(f"[Server] Matched {peer1_id} <---> {peer2_id}.", "green")

            ip1 = self._visible_ip(addr1[0], ext_ip, use_local)
            ip2 = self._visible_ip(addr2[0], ext_ip, use_local)

            reply1 = json.dumps({"peer_ip": ip2, "peer_port": addr2[1], "peer_id": peer2_id}).encode('utf-8')
            reply2 = json.dumps({"peer_ip": ip1, "peer_port": addr1[1], "peer_id": peer1_id}).encode('utf-8')
            for _ in range(MATCH_RETRIES):
                server_sock.sendto(reply1, addr1)
                server_sock.sendto(reply2, addr2)
                time.sleep(MATCH_RETRY_DELAY)

            clients.clear()

    def _run_internal_server(self, port: int, ext_ip: str, use_local: bool) -> None:
        """UDP Signalling server for peer registration and matching."""
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server_sock.bind(('0.0.0.0', port))
            server_sock.settimeout(1.0)
        except OSError as e:
            self._server_error = f"bind failed on port {port}: {e.strerror or e}"
            self.log(f"[Server] Bind Error: {e}", "red")
            self.root.after(0, self._mark_server_stopped)
            return

        self.root.after(0, self._mark_server_started, port)
        clients: Dict[str, Tuple[Tuple[str, int], float]] = {}

        with server_sock:
            while not self.server_stop_event.is_set():
                try:
                    data, addr = server_sock.recvfrom(BUFFER_SIZE)
                except socket.timeout:
                    self._purge_stale(clients, time.time())
                    continue
                except ConnectionResetError:
                    continue  # Windows WinError 10054
                except OSError as e:
                    self.log(f"[Server] Socket error: {e}", "red")
                    break

                # חבילה פגומה אחת לא מפילה את השרת
                try:
                    self._handle_signalling(server_sock, clients, data, addr, ext_ip, use_local)
                except Exception as e:
                    self.log(f"[Server] Error handling packet: {e}", "red")

        self.root.after(0, self._mark_server_stopped)

    # ------------------------------------------------------------------
    # Audio hardware
    # ------------------------------------------------------------------
    def _safe_refresh_hardware(self) -> None:
        """Restarts the PortAudio bindings. Never runs while a stream is open."""
        if not self.is_running:
            return
        with self.audio_lock:
            # אתחול PortAudio בזמן ש-stream פתוח מקריס את התהליך ברמת ה-C,
            # בלי traceback. המונה מוחזק על פני כל חיי ה-stream (ראה _counted_stream).
            if self._open_streams > 0:
                return
            # אתחול גלובלי חוזר בזמן שההתקן מתחבר/מתנתק הוא בעצמו גורם קריסה
            current_time = time.time()
            if current_time - self.last_refresh_time < HW_REFRESH_GAP:
                return
            self.last_refresh_time = current_time
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass

    @contextlib.contextmanager
    def _counted_stream(self, factory):
        """Opens a stream while holding the open-stream counter for its whole lifetime.

        The counter must cover the close as well: closing a stream on a device
        that was just unplugged can block, and a refresh during that window
        would terminate PortAudio underneath a live stream.
        """
        with self.audio_lock:
            self._open_streams += 1
        stream = None
        try:
            stream = factory()
            stream.start()
            yield stream
        finally:
            if stream is not None:
                # abort ולא stop: היציאה הרגילה מ-with קוראת ל-stop, שממתין
                # לניקוז הבאפרים - וזה בדיוק מה שנתקע לנצח על התקן USB שנשלף.
                # תרד תקוע כזה מחזיק את המונה, ואז רשימת ההתקנים של PortAudio
                # לא מתרעננת לעולם - כלומר חיבור האוזניות מחדש כבר לא ייקלט.
                try:
                    stream.abort()
                except Exception:
                    pass
                try:
                    stream.close(ignore_errors=True)
                except Exception:
                    pass
            with self.audio_lock:
                self._open_streams -= 1

    # ------------------------------------------------------------------
    # Intercom connection
    # ------------------------------------------------------------------
    def toggle_intercom(self) -> None:
        if not self.is_running:
            self.start_connection()
        else:
            self.stop_connection()

    def _send_signalling(self, payload: Dict[str, Any], repeats: int = REPORT_RETRIES) -> None:
        """שולח הודעת בקרה לשרת הסיגנלינג עם חזרות (UDP בודד נעלם בשקט)."""
        if not self.signalling_addr:
            return
        data = json.dumps(payload).encode('utf-8')

        sock = self.sock
        temp: Optional[socket.socket] = None
        if sock is None:
            try:
                temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except OSError:
                return
            sock = temp

        try:
            for i in range(max(1, repeats)):
                try:
                    sock.sendto(data, self.signalling_addr)
                except (AttributeError, OSError):
                    return
                if i + 1 < repeats:
                    time.sleep(REPORT_RETRY_DELAY)
        finally:
            if temp is not None:
                with contextlib.suppress(OSError):
                    temp.close()

    def _heartbeat(self) -> None:
        """פעימה לשרת כל עוד השיחה חיה - אחרי ההצמדה אין יותר רישום."""
        while self.is_running:
            self._send_signalling({"id": self.my_id, "status": STATUS_ALIVE}, repeats=1)
            deadline = time.time() + HEARTBEAT_INTERVAL
            while self.is_running and time.time() < deadline:
                time.sleep(0.1)

    def start_connection(self) -> None:
        self.save_settings()
        try:
            server_port: int = int(self.server_port_entry.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Port must be a valid number.")
            return

        server_ip = self.server_ip_entry.get().strip()
        my_id = self.my_id_entry.get().strip()
        if not my_id:
            messagebox.showerror("Error", "Please enter a valid ID.")
            return

        with self._stop_lock:
            if self.is_running:      # מגן מפני לחיצה כפולה מהירה
                return
            self.is_running = True

        self.last_refresh_time = 0.0
        self.my_id = my_id
        self.peer_id = ""
        self._call_started = 0.0
        self._last_rx = 0.0
        self._far_end_active = 0.0
        self._far_end_level = 0.0
        self.signalling_addr = (server_ip, server_port)
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None

        self.toggle_btn.config(text="DISCONNECT", bg=Theme.QUIT, fg=Theme.FG)
        for entry in (self.server_ip_entry, self.server_port_entry, self.my_id_entry, self.ext_ip_entry):
            entry.config(state="disabled")

        self._safe_refresh_hardware()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)

        threading.Thread(target=self._connection_flow, args=(server_ip, server_port, my_id),
                         daemon=True).start()

    def _await_peer(self, server_ip: str, server_port: int, my_id: str) -> Optional[Tuple[str, int]]:
        """ממתין לפרטי העמית. מתעלם מכל חבילה שאינה תשובת סיגנלינג תקינה."""
        msg: bytes = json.dumps({"id": my_id}).encode('utf-8')
        while self.is_running and self.sock:
            try:
                self.sock.sendto(msg, (server_ip, server_port))
            except (AttributeError, OSError):
                return None

            try:
                data, _ = self.sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except ConnectionResetError:
                time.sleep(1)
                continue
            except (AttributeError, OSError):
                return None

            # חבילת אודיו מעמית שהקדים אותנו לא מפילה את החיבור
            info = self._parse_json_dict(data)
            if not info or "peer_ip" not in info or "peer_port" not in info:
                continue
            try:
                peer = (str(info["peer_ip"]), int(info["peer_port"]))
            except (TypeError, ValueError):
                continue
            peer_id = info.get("peer_id")
            self.peer_id = peer_id.strip() if isinstance(peer_id, str) else ""
            return peer
        return None

    def _connection_flow(self, server_ip: str, server_port: int, my_id: str) -> None:
        try:
            if not self.sock:
                return

            self.update_status("WAITING FOR PEER...", Theme.WAITING)
            self.log("Waiting for matching peer from server...")
            self.log("Please wait 5 seconds...", "yellow")

            peer_addr = self._await_peer(server_ip, server_port, my_id)
            if not self.is_running or not peer_addr:
                self.stop_connection()
                return

            with self.peer_lock:
                self.assigned_peer = peer_addr
                self.tx_peer = peer_addr

            self.log(f"Peer Target Assigned -> IP: {peer_addr[0]}, Port: {peer_addr[1]}", "green")
            self.update_status("PUNCHING NAT...", Theme.PUNCHING)

            punch_until = time.time() + PUNCH_DURATION
            while self.is_running and time.time() < punch_until:
                try:
                    self.sock.sendto(PUNCH_PACKET, peer_addr)
                except (AttributeError, OSError):
                    break
                time.sleep(PUNCH_INTERVAL)

            if not self.is_running:
                return

            self.update_status("CONNECTED (P2P AUDIO)", Theme.CONNECTED)
            self.log("NAT hole punched! Live audio streaming active.", "green")

            self._call_started = time.time()
            self.rx_thread = threading.Thread(target=self._audio_receiver, daemon=True)
            self.tx_thread = threading.Thread(target=self._audio_sender, daemon=True)
            self.hb_thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.rx_thread.start()
            self.tx_thread.start()
            self.hb_thread.start()

        except Exception as e:
            # בלי זה חריגה כאן משאירה את ה-GUI תקוע ב-WAITING FOR PEER לנצח
            if self.is_running:
                self.log(f"Connection error: {e}", "red")
                self.stop_connection()

    def _peer_audio(self, addr: Tuple[str, int], data: bytes) -> Optional[bytes]:
        """מאמת שהחבילה הגיעה מהעמית ומחזיר payload רק לחבילות אודיו."""
        tag, payload = data[:1], data[1:]
        if tag not in P2P_TAGS:
            return None  # JSON מהשרת או זבל - לעולם לא מתנגן

        with self.peer_lock:
            if self.locked_peer is None:
                # ננעלים על הכתובת שממנה הגיעה החבילה המתויגת הראשונה. זה גם
                # מכסה מצב שבו ה-NAT הקצה פורט שונה מזה שהשרת דיווח.
                self.locked_peer = addr
                self.tx_peer = addr
                if addr != self.assigned_peer:
                    self.log(f"Peer reached us from {addr[0]}:{addr[1]} (assigned "
                             f"{self.assigned_peer}). Locking on.", "yellow")
            elif addr != self.locked_peer:
                return None  # מונע הזרקת אודיו מכל מקור אחר ברשת

        if tag != AUDIO_TAG or not payload:
            return None
        if len(payload) % (2 * CHANNELS) != 0:
            return None  # חבילה קטומה
        return payload


    def _note_far_end(self, block) -> None:
        """מסמן שהצד השני מדבר, כדי שנוכל להנמיך את המיקרופון ולמנוע הד."""
        if not ECHO_SUPPRESSION:
            return
        level = audio_rms(block)
        if level > FAR_END_ACTIVE_RMS:
            self._far_end_level = level
            self._far_end_active = time.time()

    def _tx_block(self, block):
        """מנמיך את המיקרופון בזמן שהצד השני מדבר.

        זה מה שמונע מהצד השני לשמוע את עצמו: מה שהרמקול שלנו משמיע חוזר
        למיקרופון שלנו, ובלי ההנמכה היינו משדרים אותו בחזרה כהד.
        """
        if not ECHO_SUPPRESSION:
            return block
        if time.time() - self._far_end_active > DUCK_HANGOVER:
            return block
        if audio_rms(block) > self._far_end_level * BREAK_IN_RATIO:
            return block   # דיבור חזק מספיק גובר, כדי שאפשר יהיה להפסיק את הצד השני
        return (block * DUCK_GAIN).astype(np.int16)

    def _audio_receiver(self) -> None:
        last_data_time: float = time.time()
        while self.is_running:
            restart_needed = False
            try:
                with self._counted_stream(
                        lambda: sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                                dtype=DTYPE)) as stream:
                    while self.is_running:
                        sock = self.sock
                        if sock is None:
                            break
                        try:
                            data, addr = sock.recvfrom(BUFFER_SIZE)
                        except socket.timeout:
                            if time.time() - last_data_time > TIMEOUT_SECS:
                                if self.is_running:
                                    self.log("Peer disconnected (3 seconds timeout).")
                                    self.root.after(0, self.stop_connection)
                                return
                            continue
                        except ConnectionResetError:
                            if self.is_running:
                                self.log("Peer disconnected unexpectedly.")
                                self.root.after(0, self.stop_connection)
                            return
                        except (AttributeError, OSError):
                            return  # הסוקט נסגר - יציאה נקייה, בלי הודעת חומרה מטעה

                        last_data_time = time.time()
                        payload = self._peer_audio(addr, data)
                        if payload is None:
                            continue
                        block = np.frombuffer(payload, dtype=np.int16)
                        self._note_far_end(block)
                        self._last_rx = last_data_time
                        try:
                            stream.write(block.reshape(-1, CHANNELS))
                        except sd.PortAudioError:
                            if self.is_running:
                                self.log("Output device error. Reconnecting...", "red")
                            restart_needed = True
                            break
            except sd.PortAudioError:
                restart_needed = True

            if not restart_needed or not self.is_running:
                return
            time.sleep(2)
            self._safe_refresh_hardware()

    def _audio_sender(self) -> None:
        while self.is_running:
            restart_needed = False
            try:
                with self._counted_stream(
                        lambda: sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                                               blocksize=CHUNK_SIZE)) as stream:
                    while self.is_running:
                        try:
                            data, _ = stream.read(CHUNK_SIZE)
                        except sd.PortAudioError:
                            if self.is_running:
                                self.log("Input device disconnected. Reconnecting...", "red")
                            restart_needed = True
                            break

                        sock, peer = self.sock, self.tx_peer
                        if sock is None or peer is None:
                            return
                        try:
                            sock.sendto(AUDIO_TAG + self._tx_block(data).tobytes(), peer)
                        except ConnectionResetError:
                            continue
                        except (AttributeError, OSError):
                            return  # הסוקט נסגר - יציאה נקייה
            except sd.PortAudioError:
                restart_needed = True

            if not restart_needed or not self.is_running:
                return
            time.sleep(2)
            self._safe_refresh_hardware()

    def stop_connection(self) -> None:
        with self._stop_lock:
            if not self.is_running:
                return
            self.is_running = False

        # דיווח לשרת כדי שישחרר את הסלוט מיד. "left" ולא "disconnected":
        # סיום שיחה אינו שליפת אוזניות, ודיווח שגוי כאן היה מדליק חיווי
        # "אוזניות נותקו" שקרי אצל הצד השני.
        if self.signalling_addr and self.my_id:
            self._send_signalling({"id": self.my_id, "status": STATUS_LEFT})

        # קודם ממתינים לתרדי האודיו, ורק אז סוגרים את הסוקט. סגירה מתחת
        # לרגליהם משאירה את כרטיס הקול תפוס ומייצרת חריגות מטעות.
        current = threading.current_thread()
        if self.hb_thread is not None and self.hb_thread.is_alive() and self.hb_thread is not current:
            self.hb_thread.join(timeout=1.0)
        self.hb_thread = None

        for t in (self.rx_thread, self.tx_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=STREAM_CLOSE_WAIT)
                if t.is_alive():
                    self.log("Audio thread still closing the device; will not touch PortAudio.", "yellow")
        self.rx_thread = None
        self.tx_thread = None

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            finally:
                self.sock = None

        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None

        # החזרת הלוח למצב מנותק (ה-tick ידאג לצייר)
        self.peer_id = ""
        self._call_started = 0.0
        self._last_rx = 0.0

        def reset_gui() -> None:
            try:
                self.toggle_btn.config(text="START INTERCOM", bg=Theme.ACCENT, fg="#000000")
                for entry in (self.server_ip_entry, self.server_port_entry, self.my_id_entry, self.ext_ip_entry):
                    entry.config(state="normal")
                self.status_label.config(text="STATUS: DISCONNECTED", fg=Theme.DISCONNECTED)
                self.log("Intercom stopped.", "red")
            except tk.TclError:
                pass

        self.root.after(0, reset_gui)

    def on_close(self) -> None:
        self.stop_connection()
        self.stop_local_server()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = IntercomGUI(root)
    root.mainloop()
