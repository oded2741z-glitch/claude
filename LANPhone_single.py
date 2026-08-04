"""LAN Phone - voice calls between two PCs on the same local network.

This single file is GENERATED - do not edit it by hand.  The real source is
the ``lanphone`` package; regenerate with::

    python tools/bundle.py

It exists so the whole program can be copied around as one file.  Run it with
``python LANPhone_single.py`` exactly like ``python main.py``.
"""

from __future__ import annotations


import argparse
import ctypes
import ipaddress
import json
import math
import numpy as np
import os
import queue
import random
import socket
import struct
import sys
import threading
import time
import tkinter as tk
import uuid
from dataclasses import asdict, dataclass, field, fields
from tkinter import messagebox, ttk
from typing import Any, Callable, Iterable, TYPE_CHECKING


# --------------------------------------------------------------------------
# from lanphone/config.py
# --------------------------------------------------------------------------

APP_NAME = "LAN Phone"
PROTOCOL_VERSION = 1

# How many previously called addresses are kept.
MAX_SAVED_PEERS = 12

# Default ports.  All of them fall back to the next free port when taken, which
# makes it possible to run two copies of the app on a single machine for testing.
SIGNALING_PORT = 50505
DISCOVERY_PORT = 50506
AUDIO_PORT = 50507
PORT_SEARCH_RANGE = 20

# Presence broadcast timing.
DISCOVERY_INTERVAL = 2.0
PEER_TIMEOUT = 8.0

# Audio wire format.  The rate below is what travels over the network; the
# sound devices may run at any rate, the engine resamples when needed.
SUPPORTED_RATES = (16000, 24000, 32000, 48000)
DEFAULT_WIRE_RATE = 16000
DEFAULT_FRAME_MS = 20
DEFAULT_JITTER_MS = 60

# Keep a single audio packet below the usual 1500 byte MTU (minus IP and UDP
# headers) so the network never has to fragment it: one lost fragment would
# throw away a whole frame.
MAX_AUDIO_PAYLOAD = 1400


def max_frame_ms(wire_rate: int) -> int:
    """Longest frame (multiple of 10 ms) that still fits in one datagram."""
    samples = MAX_AUDIO_PAYLOAD // 2
    fits = int(samples * 1000 / max(1, wire_rate)) // 10 * 10
    return max(10, min(40, fits))


def config_dir() -> str:
    """Per-user directory for the settings file."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "LANPhone")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "lanphone")


def config_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def _clean_saved_peers(raw: object) -> list[dict]:
    """Accept only well-formed entries: the file may have been edited by hand."""
    cleaned: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return cleaned
    for item in raw:
        if not isinstance(item, dict):
            continue
        ip = str(item.get("ip") or "").strip()
        if not ip or ip in seen:
            continue
        try:
            port = int(item.get("port") or SIGNALING_PORT)
        except (TypeError, ValueError):
            port = SIGNALING_PORT
        seen.add(ip)
        cleaned.append(
            {
                "ip": ip,
                "port": port if 0 < port < 65536 else SIGNALING_PORT,
                "name": str(item.get("name") or "").strip()[:64],
            }
        )
    return cleaned[:MAX_SAVED_PEERS]


def default_display_name() -> str:
    import socket

    for value in (os.environ.get("COMPUTERNAME"), socket.gethostname()):
        if value:
            return value
    return "PC"


@dataclass
class Settings:
    display_name: str = ""
    # Devices are remembered by name, not by index: index numbers change every
    # time a USB headset is unplugged or a device is added.
    input_device_name: str = ""
    output_device_name: str = ""
    volume: float = 1.0
    mic_gain: float = 1.0
    auto_answer: bool = False
    auto_pick_new_device: bool = True
    wire_rate: int = DEFAULT_WIRE_RATE
    frame_ms: int = DEFAULT_FRAME_MS
    jitter_ms: int = DEFAULT_JITTER_MS
    last_peer_ip: str = ""
    # Addresses that were called (or called us), most recent first.
    saved_peers: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = default_display_name()
        if self.wire_rate not in SUPPORTED_RATES:
            self.wire_rate = DEFAULT_WIRE_RATE
        self.frame_ms = max(10, min(max_frame_ms(self.wire_rate), int(self.frame_ms)))
        self.jitter_ms = max(20, min(400, int(self.jitter_ms)))
        self.volume = max(0.0, min(2.0, float(self.volume)))
        self.mic_gain = max(0.0, min(4.0, float(self.mic_gain)))
        self.saved_peers = _clean_saved_peers(self.saved_peers)

    # -- saved addresses -------------------------------------------------
    def remember_peer(self, ip: str, port: int = SIGNALING_PORT, name: str = "") -> bool:
        """Move an address to the top of the saved list.  True if anything changed."""
        ip = (ip or "").strip()
        if not ip:
            return False
        known = next((p for p in self.saved_peers if p["ip"] == ip), None)
        entry = {
            "ip": ip,
            "port": int(port) if port else SIGNALING_PORT,
            "name": (name or "").strip()[:64] or (known or {}).get("name", ""),
        }
        others = [p for p in self.saved_peers if p["ip"] != ip]
        changed = self.saved_peers[:1] != [entry] or len(others) + 1 != len(self.saved_peers)
        self.saved_peers = [entry, *others][:MAX_SAVED_PEERS]
        self.last_peer_ip = ip
        return changed

    def forget_peer(self, ip: str) -> bool:
        ip = (ip or "").strip()
        before = len(self.saved_peers)
        self.saved_peers = [p for p in self.saved_peers if p["ip"] != ip]
        return len(self.saved_peers) != before

    def saved_port(self, ip: str, default: int = SIGNALING_PORT) -> int:
        for peer in self.saved_peers:
            if peer["ip"] == (ip or "").strip():
                return int(peer.get("port") or default)
        return default

    @classmethod
    def load(cls) -> "Settings":
        try:
            with open(config_path(), "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        try:
            os.makedirs(config_dir(), exist_ok=True)
            tmp = config_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2, ensure_ascii=False)
            os.replace(tmp, config_path())
        except OSError:
            pass  # settings are a convenience, never fail the app over them

    @property
    def frame_samples(self) -> int:
        return int(self.wire_rate * self.frame_ms / 1000)

# --------------------------------------------------------------------------
# from lanphone/i18n.py
# --------------------------------------------------------------------------

EN = {
    "app_title": "LAN Phone",
    "my_name": "My name:",
    "my_ip": "My address:",
    "status": "Status:",
    "state_idle": "Idle",
    "state_calling": "Calling...",
    "state_ringing": "Incoming call",
    "state_in_call": "In call",
    "peers_group": "Computers on the network",
    "no_peers": "(searching...)",
    "address": "Address:",
    "forget": "Forget",
    "call": "Call",
    "hangup": "Hang up",
    "answer": "Answer",
    "reject": "Reject",
    "audio_group": "Audio devices",
    "mic": "Microphone:",
    "speaker": "Headset / speaker:",
    "refresh_devices": "Refresh devices",
    "auto_pick_new": "Always use a headset when one is connected",
    "auto_answer": "Auto-answer incoming calls",
    "mic_level": "Mic level:",
    "volume": "Volume:",
    "mic_gain": "Mic gain:",
    "mute": "Mute microphone",
    "monitor": "Self test (hear yourself)",
    "log_group": "Log",
    "settings": "Settings",
    "quit": "Quit",
    "close": "Close",
    "save": "Save",
    "cancel": "Cancel",
    "wire_rate": "Audio quality (samples/second):",
    "frame_ms": "Frame length (ms):",
    "jitter_ms": "Jitter buffer (ms):",
    "stats": "sent {sent} · recv {recv} · lost {lost} · buffer {depth} · rtt {rtt}",
    "rtt_unknown": "?",
    "log_started": "Ready. Local address {ip}, call port {port}.",
    "log_audio_ready": "Audio running: mic \"{mic}\", output \"{out}\".",
    "log_audio_error": "Audio error: {err}",
    "log_no_input": "No microphone found. Connect a headset - it is picked up automatically.",
    "log_no_output": "No audio output found. Connect a headset - it is picked up automatically.",
    "log_devices_refreshed": "Device list updated ({count} devices).",
    "log_new_device": "New device detected: {name}",
    "log_using_device": "Now using: {name}",
    "log_device_gone": "Audio device disconnected: {name}",
    "log_discovery_error": "Automatic discovery is off ({err}). Use a manual address.",
    "log_peer_found": "Found on the network: {name} ({ip})",
    "log_peer_left": "Left the network: {name}",
    "log_calling": "Calling {ip}...",
    "log_call_failed": "Could not reach {ip}: {err}",
    "log_why_other_subnet": (
        "This computer is on network {local} and {ip} is on a different one - "
        "both computers must be connected to the same router."
    ),
    "log_why_calling_self": "{ip} is this computer's own address.",
    "log_why_no_network": "This computer has no active network connection.",
    "log_why_no_dhcp": "The address {local} means the router never assigned one - check the network connection.",
    "log_ringing_out": "Ringing {name}...",
    "log_incoming": "Incoming call from {name} ({ip})",
    "log_call_started": "Call started with {name}.",
    "log_call_ended": "Call ended.",
    "log_rejected": "Call rejected.",
    "log_busy": "The other side is busy.",
    "log_peer_hangup": "The other side hung up.",
    "log_link_lost": "Connection lost.",
    "log_missed": "Missed call from {name}.",
    "log_monitor_on": "Self test on - you hear your own microphone.",
    "log_monitor_off": "Self test off.",
    "log_settings_saved": "Settings saved.",
    "log_peer_forgotten": "Removed {ip} from the saved addresses.",
    "log_rate_from_peer": "Caller selected {rate} Hz.",
    "err_no_target": "Pick a computer from the list or type an IP address.",
    "err_bad_ip": "Invalid address: {ip}",
}

# Log lines are coloured by what they say: trouble in amber, call progress in
# the accent colour, everything else plain.
ALERT_KEYS = frozenset(
    {
        "log_audio_error",
        "log_no_input",
        "log_no_output",
        "log_call_failed",
        "log_link_lost",
        "log_device_gone",
        "log_discovery_error",
        "log_busy",
        "log_missed",
        "log_why_other_subnet",
        "log_why_calling_self",
        "log_why_no_network",
        "log_why_no_dhcp",
    }
)
EVENT_KEYS = frozenset(
    {
        "log_incoming",
        "log_call_started",
        "log_ringing_out",
        "log_new_device",
        "log_using_device",
        "log_audio_ready",
    }
)


def severity(key: str) -> str:
    """Text tag for a log key: 'alert', 'event' or '' for plain."""
    if key in ALERT_KEYS:
        return "alert"
    if key in EVENT_KEYS:
        return "event"
    return ""


class Strings:
    """Look up an interface string, formatted."""

    def __call__(self, key: str, **kwargs: object) -> str:
        text = EN.get(key, key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError):
                pass
        return text

# --------------------------------------------------------------------------
# from lanphone/theme.py
# --------------------------------------------------------------------------

if TYPE_CHECKING:  # imported lazily below: the palette is useful without Tk
    import tkinter as tk
    from tkinter import ttk

# -- palette ---------------------------------------------------------------
BG = "#151515"  # window background
PANEL = "#1e1e1e"  # entries, comboboxes, group interiors
DEEP = "#0f0f0f"  # log and list backgrounds, troughs
BORDER = "#343434"
BORDER_BRIGHT = "#4d4d4d"
OUTLINE = "#2c5f6b"  # cool hairline around the main content panels

TEXT = "#d7d7d7"
TEXT_DIM = "#7e7e7e"
TEXT_OFF = "#565656"  # disabled

ACCENT = "#ff6a1f"  # orange: titles, meters, selection
ACCENT_SOFT = "#c8521a"
ACCENT_GLOW = "#ff8845"
DANGER = "#d81c0c"
DANGER_GLOW = "#ff2d19"
WARN = "#ffb02e"

BUTTON = "#2b2b2b"
BUTTON_HOVER = "#383838"
BUTTON_DOWN = "#464646"
BUTTON_OFF = "#1d1d1d"

# -- fonts -----------------------------------------------------------------
if sys.platform.startswith("win"):
    FAMILY = "Consolas"
elif sys.platform == "darwin":
    FAMILY = "Menlo"
else:
    FAMILY = "DejaVu Sans Mono"

SIZE = 9
FONT = (FAMILY, SIZE)
FONT_BOLD = (FAMILY, SIZE, "bold")
FONT_TITLE = (FAMILY, SIZE + 3, "bold")
FONT_STATUS = (FAMILY, SIZE + 1, "bold")

# Style names the interface asks for by name.
TITLE = "Title.TLabel"
STATUS = "Status.TLabel"
DIM = "Dim.TLabel"
ACCENT_LABEL = "Accent.TLabel"
ACCENT_BUTTON = "Accent.TButton"
DANGER_BUTTON = "Danger.TButton"
TOOL_BUTTON = "Tool.TButton"
METER = "Meter.Horizontal.TProgressbar"
WATERMARK_STYLE = "Watermark.TLabel"


def apply_theme(root: tk.Misc) -> ttk.Style:
    """Point every widget class at the palette above."""
    import tkinter as tk
    from tkinter import ttk

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # very old Tk without clam
        pass

    _fonts()
    root.configure(background=BG)

    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        fieldbackground=PANEL,
        troughcolor=DEEP,
        bordercolor=BORDER,
        lightcolor=BG,
        darkcolor=BG,
        focuscolor=ACCENT_SOFT,
        font=FONT,
    )

    # -- labels ----------------------------------------------------------
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure(DIM, background=BG, foreground=TEXT_DIM)
    style.configure(ACCENT_LABEL, background=BG, foreground=ACCENT)
    style.configure(TITLE, background=BG, foreground=ACCENT, font=FONT_TITLE)
    style.configure(STATUS, background=BG, foreground=TEXT, font=FONT_STATUS)
    style.configure(WATERMARK_STYLE, background=BG, foreground=TEXT_OFF, font=FONT_BOLD)

    # -- frames ----------------------------------------------------------
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure(
        "TLabelframe",
        background=BG,
        bordercolor=BORDER,
        lightcolor=BG,
        darkcolor=BG,
        borderwidth=1,
        relief="solid",
    )
    style.configure("TLabelframe.Label", background=BG, foreground=TEXT_DIM, font=FONT_BOLD)

    # -- buttons: flat, thin outline, no focus ring ----------------------
    for name in ("TButton", TOOL_BUTTON, ACCENT_BUTTON, DANGER_BUTTON):
        style.configure(
            name,
            background=BUTTON,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BUTTON,
            darkcolor=BUTTON,
            borderwidth=1,
            relief="flat",
            focusthickness=0,
            focuscolor="",
            padding=(10, 4),
            anchor="center",
            font=FONT,
        )
        style.map(
            name,
            background=[("disabled", BUTTON_OFF), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOVER)],
            lightcolor=[("disabled", BUTTON_OFF), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOVER)],
            darkcolor=[("disabled", BUTTON_OFF), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOVER)],
            foreground=[("disabled", TEXT_OFF)],
            bordercolor=[("active", BORDER_BRIGHT), ("focus", BORDER_BRIGHT)],
        )

    style.configure(TOOL_BUTTON, padding=(8, 2))
    # The primary action keeps the dark body and takes the accent in its text.
    style.configure(ACCENT_BUTTON, foreground=ACCENT, font=FONT_BOLD)
    style.map(
        ACCENT_BUTTON,
        foreground=[("disabled", TEXT_OFF), ("active", ACCENT_GLOW)],
        bordercolor=[("active", ACCENT_SOFT), ("!disabled", ACCENT_SOFT), ("disabled", BORDER)],
    )
    # Anything that ends a call is red, like the Quit button.
    style.configure(DANGER_BUTTON, background=DANGER, foreground="#ffffff", font=FONT_BOLD)
    for option in ("background", "lightcolor", "darkcolor"):
        style.map(
            DANGER_BUTTON,
            **{
                option: [
                    ("disabled", BUTTON_OFF),
                    ("pressed", DANGER),
                    ("active", DANGER_GLOW),
                    ("!disabled", DANGER),
                ]
            },
        )
    style.map(DANGER_BUTTON, foreground=[("disabled", TEXT_OFF)], bordercolor=[("!disabled", DANGER)])

    # -- checkbuttons ----------------------------------------------------
    style.configure(
        "TCheckbutton",
        background=BG,
        foreground=TEXT,
        indicatorbackground=DEEP,
        indicatorforeground=BG,
        bordercolor=BORDER,
        lightcolor=DEEP,
        darkcolor=DEEP,
        focusthickness=0,
        focuscolor="",
        padding=(2, 3),
    )
    style.map(
        "TCheckbutton",
        indicatorbackground=[("disabled", BUTTON_OFF), ("selected", ACCENT), ("active", BUTTON_HOVER)],
        indicatorforeground=[("selected", BG)],
        foreground=[("disabled", TEXT_OFF), ("active", "#ffffff")],
        background=[("active", BG)],
        bordercolor=[("active", BORDER_BRIGHT)],
    )

    # -- text entry widgets ----------------------------------------------
    style.configure(
        "TEntry",
        fieldbackground=PANEL,
        foreground=TEXT,
        insertcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=PANEL,
        darkcolor=PANEL,
        selectbackground=ACCENT_SOFT,
        selectforeground="#ffffff",
        padding=3,
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", BG), ("readonly", PANEL)],
        foreground=[("disabled", TEXT_OFF)],
        bordercolor=[("focus", ACCENT_SOFT)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=PANEL,
        background=BUTTON,
        foreground=TEXT,
        arrowcolor=ACCENT,
        insertcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=PANEL,
        darkcolor=PANEL,
        selectbackground=ACCENT_SOFT,
        selectforeground="#ffffff",
        padding=3,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", BG), ("readonly", PANEL)],
        foreground=[("disabled", TEXT_OFF)],
        arrowcolor=[("disabled", TEXT_OFF), ("active", ACCENT_GLOW)],
        bordercolor=[("focus", ACCENT_SOFT), ("active", BORDER_BRIGHT)],
    )
    # The dropdown list is a classic Listbox inside a popup window.
    for option, value in (
        ("background", DEEP),
        ("foreground", TEXT),
        ("selectBackground", ACCENT),
        ("selectForeground", BG),
        ("borderWidth", "0"),
        ("font", f"{{{FAMILY}}} {SIZE}"),
    ):
        root.option_add(f"*TCombobox*Listbox.{option}", value)

    style.configure(
        "TSpinbox",
        fieldbackground=PANEL,
        foreground=TEXT,
        arrowcolor=ACCENT,
        insertcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=PANEL,
        darkcolor=PANEL,
        padding=3,
    )
    style.map("TSpinbox", arrowcolor=[("disabled", TEXT_OFF), ("active", ACCENT_GLOW)])

    # -- sliders, meters, scrollbars -------------------------------------
    style.configure(
        "Horizontal.TScale",
        background=ACCENT,
        troughcolor=DEEP,
        bordercolor=BORDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT_SOFT,
    )
    style.map("Horizontal.TScale", background=[("active", ACCENT_GLOW), ("disabled", TEXT_OFF)])

    style.configure(
        METER,
        background=ACCENT,
        troughcolor=DEEP,
        bordercolor=BORDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT_SOFT,
        thickness=12,
    )

    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=BUTTON,
            troughcolor=DEEP,
            bordercolor=BORDER,
            arrowcolor=TEXT_DIM,
            lightcolor=BUTTON,
            darkcolor=BUTTON,
            borderwidth=1,
            relief="flat",
        )
        style.map(
            f"{orient}.TScrollbar",
            background=[("pressed", ACCENT_SOFT), ("active", BUTTON_HOVER)],
            arrowcolor=[("active", ACCENT)],
        )

    return style


def _fonts() -> None:
    try:
        import tkinter.font as tkfont

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont"):
            tkfont.nametofont(name).configure(family=FAMILY, size=SIZE)
    except Exception:  # noqa: BLE001 - fonts are cosmetic
        pass


def style_listbox(widget: tk.Listbox) -> None:
    widget.configure(
        background=DEEP,
        foreground=TEXT,
        selectbackground=ACCENT,
        selectforeground=BG,
        disabledforeground=TEXT_DIM,
        highlightthickness=1,
        highlightbackground=OUTLINE,
        highlightcolor=ACCENT_SOFT,
        borderwidth=0,
        relief="flat",
        activestyle="none",
        font=FONT,
    )


def style_text(widget: tk.Text) -> None:
    widget.configure(
        background=DEEP,
        foreground=TEXT,
        insertbackground=ACCENT,
        selectbackground=ACCENT_SOFT,
        selectforeground="#ffffff",
        highlightthickness=1,
        highlightbackground=OUTLINE,
        highlightcolor=OUTLINE,
        borderwidth=0,
        relief="flat",
        font=FONT,
    )


def style_menu(widget: tk.Menu) -> None:
    widget.configure(
        background=PANEL,
        foreground=TEXT,
        activebackground=ACCENT,
        activeforeground=BG,
        disabledforeground=TEXT_OFF,
        borderwidth=1,
        relief="flat",
        font=FONT,
    )

# --------------------------------------------------------------------------
# from lanphone/protocol.py
# --------------------------------------------------------------------------

MAGIC = b"LPh1"

# magic | call_id | seq | flags | codec | samples
AUDIO_HEADER = struct.Struct("!4sIIBBH")
AUDIO_HEADER_SIZE = AUDIO_HEADER.size

CODEC_PCM16 = 0

FLAG_SILENCE = 1 << 0


class ProtocolError(ValueError):
    pass


# --------------------------------------------------------------------------
# audio packets
# --------------------------------------------------------------------------
def encode_audio(call_id: int, seq: int, samples: np.ndarray, silence: bool = False) -> bytes:
    """Pack one frame of mono float32 audio as 16 bit PCM."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    data = (pcm * 32767.0).astype("<i2").tobytes()
    flags = FLAG_SILENCE if silence else 0
    header = AUDIO_HEADER.pack(
        MAGIC, call_id & 0xFFFFFFFF, seq & 0xFFFFFFFF, flags, CODEC_PCM16, len(pcm) & 0xFFFF
    )
    return header + data


def decode_audio(packet: bytes) -> tuple[int, int, int, np.ndarray]:
    """Return (call_id, seq, flags, samples).  Raises ProtocolError on junk."""
    if len(packet) < AUDIO_HEADER_SIZE:
        raise ProtocolError("packet too short")
    magic, call_id, seq, flags, codec, count = AUDIO_HEADER.unpack_from(packet)
    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if codec != CODEC_PCM16:
        raise ProtocolError(f"unsupported codec {codec}")
    body = packet[AUDIO_HEADER_SIZE:]
    if len(body) != count * 2:
        raise ProtocolError("payload length mismatch")
    pcm = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
    return call_id, seq, flags, pcm


# --------------------------------------------------------------------------
# control messages (newline delimited JSON over TCP)
# --------------------------------------------------------------------------
INVITE = "invite"
MSG_RINGING = "ringing"
ACCEPT = "accept"
REJECT = "reject"
BUSY = "busy"
BYE = "bye"
PING = "ping"
PONG = "pong"

MAX_MESSAGE_SIZE = 64 * 1024


def encode_message(msg: dict[str, Any]) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode_message(line: bytes) -> dict[str, Any]:
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"bad control message: {exc}") from exc
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        raise ProtocolError("control message must be an object with a 't' field")
    return msg

# --------------------------------------------------------------------------
# from lanphone/jitter.py
# --------------------------------------------------------------------------

class JitterBuffer:
    def __init__(self, frame_samples: int, target_frames: int = 3, max_frames: int = 16) -> None:
        self.frame_samples = int(frame_samples)
        self.target_frames = max(1, int(target_frames))
        self.max_frames = max(self.target_frames + 1, int(max_frames))
        self._lock = threading.Lock()
        self._frames: dict[int, np.ndarray] = {}
        self._next_seq = 0
        self._playing = False
        self._last = np.zeros(self.frame_samples, dtype=np.float32)
        self._conceal_gain = 1.0
        self.received = 0
        self.lost = 0
        self.late = 0
        self.dropped = 0
        self.underruns = 0

    # -- configuration ---------------------------------------------------
    def reconfigure(self, frame_samples: int, target_frames: int, max_frames: int) -> None:
        with self._lock:
            self.frame_samples = int(frame_samples)
            self.target_frames = max(1, int(target_frames))
            self.max_frames = max(self.target_frames + 1, int(max_frames))
            self._reset_locked()

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        self._frames.clear()
        self._next_seq = 0
        self._playing = False
        self._last = np.zeros(self.frame_samples, dtype=np.float32)
        self._conceal_gain = 1.0

    def reset_stats(self) -> None:
        with self._lock:
            self.received = self.lost = self.late = self.dropped = self.underruns = 0

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._frames)

    # -- producer side ---------------------------------------------------
    def push(self, seq: int, samples: np.ndarray) -> None:
        frame = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(frame) != self.frame_samples:
            frame = _fit(frame, self.frame_samples)
        with self._lock:
            self.received += 1
            if not self._playing:
                self._frames[seq] = frame
                if len(self._frames) >= self.target_frames:
                    self._next_seq = min(self._frames)
                    self._playing = True
                return
            if seq < self._next_seq:
                self.late += 1
                return
            self._frames[seq] = frame
            while len(self._frames) > self.max_frames:
                # Running long: throw away the oldest frame so latency does not
                # keep growing (clock drift between the two machines).
                oldest = min(self._frames)
                del self._frames[oldest]
                self._next_seq = min(self._frames) if self._frames else oldest + 1
                self.dropped += 1

    # -- consumer side ---------------------------------------------------
    def pop(self) -> tuple[np.ndarray, bool]:
        """Return (frame, is_real).  Never blocks, never returns None."""
        with self._lock:
            if not self._playing:
                return np.zeros(self.frame_samples, dtype=np.float32), False

            frame = self._frames.pop(self._next_seq, None)
            if frame is not None:
                self._next_seq += 1
                self._last = frame
                self._conceal_gain = 1.0
                return frame, True

            if not self._frames:
                # Nothing at all left: wait for the buffer to refill.
                self.underruns += 1
                self._playing = False
                return self._conceal_locked(), False

            self.lost += 1
            self._next_seq += 1
            return self._conceal_locked(), False

    def _conceal_locked(self) -> np.ndarray:
        self._conceal_gain *= 0.4
        if self._conceal_gain < 0.02:
            return np.zeros(self.frame_samples, dtype=np.float32)
        return (self._last * self._conceal_gain).astype(np.float32)


def _fit(frame: np.ndarray, size: int) -> np.ndarray:
    if len(frame) > size:
        return frame[:size]
    out = np.zeros(size, dtype=np.float32)
    out[: len(frame)] = frame
    return out

# --------------------------------------------------------------------------
# from lanphone/resample.py
# --------------------------------------------------------------------------

class Resampler:
    def __init__(self, src_rate: int, dst_rate: int) -> None:
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self.ratio = float(src_rate) / float(dst_rate)

        self._kernel: np.ndarray | None = None
        if self.ratio > 1.05:
            # Anti-alias filter: cut everything the destination rate cannot hold.
            taps = int(round(self.ratio)) * 4 + 1
            kernel = np.hanning(taps + 2)[1:-1].astype(np.float32)
            self._kernel = kernel / float(kernel.sum())
        self.reset()

    @property
    def passthrough(self) -> bool:
        return self.src_rate == self.dst_rate

    def reset(self) -> None:
        self._buf = np.zeros(1, dtype=np.float32)
        self._pos = 0.0
        if self._kernel is not None:
            self._fir_state = np.zeros(len(self._kernel) - 1, dtype=np.float32)

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self.passthrough:
            return x.copy()
        if len(x) == 0:
            return np.zeros(0, dtype=np.float32)

        if self._kernel is not None:
            padded = np.concatenate([self._fir_state, x])
            if len(padded) < len(self._kernel):
                self._fir_state = padded
                return np.zeros(0, dtype=np.float32)
            self._fir_state = padded[-(len(self._kernel) - 1):].copy()
            x = np.convolve(padded, self._kernel, mode="valid").astype(np.float32)

        buf = np.concatenate([self._buf, x])
        last = len(buf) - 1
        span = last - self._pos
        if span < 0:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)

        count = int(np.floor(span / self.ratio)) + 1
        idx = self._pos + self.ratio * np.arange(count, dtype=np.float64)
        i0 = idx.astype(np.int64)
        i1 = np.minimum(i0 + 1, last)
        frac = (idx - i0).astype(np.float32)
        out = buf[i0] * (1.0 - frac) + buf[i1] * frac

        keep_from = int(i0[-1])
        self._buf = buf[keep_from:].copy()
        self._pos = float(self._pos + self.ratio * count - keep_from)
        return out.astype(np.float32, copy=False)


def make_resampler(src_rate: int, dst_rate: int) -> Resampler | None:
    """``None`` when no conversion is needed, so callers can skip the work."""
    if int(src_rate) == int(dst_rate):
        return None
    return Resampler(src_rate, dst_rate)

# --------------------------------------------------------------------------
# from lanphone/appicon.py
# --------------------------------------------------------------------------

# Names accepted for a hand-made icon, checked in this order.
ICON_FILENAMES = ("app_icon.ico", "icon.ico", "lanphone.ico")

ICON_COLOUR = "#000000"


def _pixels(size: int) -> list[list[str]]:
    return [[ICON_COLOUR] * size for _ in range(size)]


def build(size: int = 32):
    """A ``size`` x ``size`` ``PhotoImage``.  A Tk root must already exist."""
    import tkinter as tk

    grid = _pixels(size)
    image = tk.PhotoImage(width=size, height=size)
    image.put("{" + "} {".join(" ".join(row) for row in grid) + "}")
    return image


def _search_dirs() -> list[str]:
    """Places a hand-made icon file might live, most likely first.

    Covers running from source (``python main.py``) and the packaged .exe:
    PyInstaller extracts bundled data next to ``sys._MEIPASS``, and
    ``LANPhone.spec`` bundles the user's own icon file there when it finds
    one at build time (see ``resolve_icon`` in the spec).
    """
    dirs = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
        dirs.append(os.path.dirname(sys.executable))
    if sys.argv and sys.argv[0]:
        dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirs.append(os.getcwd())
    seen: list[str] = []
    for directory in dirs:
        if directory and directory not in seen:
            seen.append(directory)
    return seen


def find_icon_file() -> str | None:
    """A real .ico placed next to the app, if there is one.  See ``_search_dirs``."""
    for directory in _search_dirs():
        for name in ICON_FILENAMES:
            path = os.path.join(directory, name)
            if os.path.exists(path) and is_ico(path):
                return path
    return None


def apply_app_icon(window):
    """Give ``window`` (and every later one) the app's icon.

    Prefers a real ``app_icon.ico`` if one can be found (Windows only: that is
    the one platform where Tk can load a .ico file as-is).  Otherwise the
    generated tile is used, and its ``PhotoImage`` is returned so the caller
    can keep a reference - Tk only holds a weak one, and a collected
    PhotoImage takes the icon down with it.  ``None`` means a real file is in
    use and there is nothing the caller needs to keep alive.
    """
    import tkinter as tk

    path = find_icon_file()
    if path and sys.platform.startswith("win"):
        try:
            window.iconbitmap(default=path)
            return None
        except tk.TclError:
            pass  # fall through to the generated tile

    icon = build()
    try:
        window.iconphoto(True, icon)
    except tk.TclError:
        pass
    return icon


# --------------------------------------------------------------------------
# .ico file, for the packaged executable
# --------------------------------------------------------------------------
ICO_MAGIC = b"\x00\x00\x01\x00"
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def is_ico(path: str) -> bool:
    """True if the file really is a Windows icon.

    Worth checking before handing it to PyInstaller: a .png renamed to .ico
    is a common mistake and it fails the whole build rather than the icon.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(6)
    except OSError:
        return False
    if len(header) < 6 or header[:4] != ICO_MAGIC:
        return False
    return int.from_bytes(header[4:6], "little") > 0  # at least one image


def _bmp_image(size: int) -> bytes:
    """One icon image in the BMP form .ico files use: header, BGRA, AND mask."""
    grid = _pixels(size)
    rgb = {}
    for colour in {cell for row in grid for cell in row}:
        value = colour.lstrip("#")
        rgb[colour] = bytes((int(value[4:6], 16), int(value[2:4], 16), int(value[0:2], 16), 255))

    # BITMAPINFOHEADER, with double height: the XOR image plus the AND mask.
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
    pixels = b"".join(b"".join(rgb[cell] for cell in row) for row in reversed(grid))
    # Fully opaque, so the mask is zeroed; rows are padded to four bytes.
    mask_row = (size + 31) // 32 * 4
    return header + pixels + bytes(mask_row * size)


def write_ico(path: str, sizes: tuple[int, ...] = ICO_SIZES) -> str:
    """Write a real multi-size .ico.  No image library involved."""
    images = [_bmp_image(size) for size in sizes]
    offset = 6 + 16 * len(images)
    directory = bytearray(ICO_MAGIC[:4] + struct.pack("<H", len(images)))
    directory[4:6] = struct.pack("<H", len(images))
    entries = bytearray()
    for size, image in zip(sizes, images):
        stored = 0 if size >= 256 else size  # 256 is written as zero
        entries += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    with open(path, "wb") as fh:
        fh.write(ICO_MAGIC + struct.pack("<H", len(images)) + bytes(entries) + b"".join(images))
    return path

# --------------------------------------------------------------------------
# from lanphone/winchrome.py
# --------------------------------------------------------------------------

# DwmSetWindowAttribute attributes.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 builds before 20H1
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36

GWL_EXSTYLE = -20
WS_EX_DLGMODALFRAME = 0x00000001
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020


def is_supported() -> bool:
    return sys.platform.startswith("win")


def colorref(color: str) -> int:
    """'#RRGGBB' -> the 0x00BBGGRR integer Win32 wants."""
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected #RRGGBB, got {color!r}")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


def _hwnd(window) -> int:
    """The HWND that owns the caption (Tk's own window is a child of it)."""
    window.update_idletasks()
    handle = int(window.winfo_id())
    parent = ctypes.windll.user32.GetParent(handle)
    return parent or handle


def _set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    data = ctypes.c_int(value)
    # The handle must go as a pointer: a 64-bit HWND does not fit the c_int
    # that ctypes would pick for a plain Python integer.
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd),
        ctypes.c_uint(attribute),
        ctypes.byref(data),
        ctypes.c_uint(ctypes.sizeof(data)),
    )
    return result == 0


def dark_titlebar(window, caption: str | None = None, text: str | None = None) -> bool:
    """Make the title bar dark.  True when Windows accepted something."""
    if not is_supported():
        return False
    try:
        hwnd = _hwnd(window)
        applied = _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        if not applied:
            applied = _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1)
        # Windows 11 only; older builds reject these and keep the dark default.
        if caption:
            applied |= _set_attribute(hwnd, DWMWA_CAPTION_COLOR, colorref(caption))
            applied |= _set_attribute(hwnd, DWMWA_BORDER_COLOR, colorref(caption))
        if text:
            applied |= _set_attribute(hwnd, DWMWA_TEXT_COLOR, colorref(text))
        return bool(applied)
    except Exception:  # noqa: BLE001 - a plain title bar is not worth crashing over
        return False


def hide_titlebar_icon(window, clear_icons: bool = False) -> bool:
    """Remove the icon from the left of the title bar.

    ``clear_icons`` also drops the window's icons, which empties the taskbar
    entry back to Tk's feather - so it stays off by default and the app's own
    icon (see ``appicon``) is what the taskbar and Alt-Tab show.
    """
    if not is_supported():
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = _hwnd(window)
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.restype = ctypes.c_void_p
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

        style = get_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE) or 0
        set_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ctypes.c_void_p(int(style) | WS_EX_DLGMODALFRAME))
        if clear_icons:
            for which in (ICON_SMALL, ICON_BIG):
                user32.SendMessageW(ctypes.c_void_p(hwnd), WM_SETICON, which, 0)
        # The frame only re-reads its style when told to redraw itself.
        user32.SetWindowPos(
            ctypes.c_void_p(hwnd), 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def apply_window_chrome(window, caption: str | None = None, text: str | None = None) -> bool:
    """Dark, matching, icon-less title bar.  Safe to call on any platform."""
    dark = dark_titlebar(window, caption, text)
    icon = hide_titlebar_icon(window)
    return dark or icon

# --------------------------------------------------------------------------
# from lanphone/audio.py
# --------------------------------------------------------------------------

_sd = None

# PortAudio is a single global: re-initialising it in one thread while another
# opens a stream would crash. Every entry point that touches it takes this.
PORTAUDIO_LOCK = threading.RLock()


def backend():
    """Import sounddevice on first use, with a readable error if it is missing."""
    global _sd
    if _sd is None:
        try:
            import sounddevice  # noqa: PLC0415  (deliberately lazy)
        except OSError as exc:  # PortAudio library missing
            raise AudioUnavailable(f"PortAudio not available: {exc}") from exc
        except ImportError as exc:
            raise AudioUnavailable(
                "The 'sounddevice' package is missing. Install it with: pip install sounddevice"
            ) from exc
        _sd = sounddevice
    return _sd


class AudioUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------
# devices
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    hostapi: str
    channels: int
    samplerate: float
    is_input: bool

    @property
    def key(self) -> str:
        """Stable identity across replugs (the index is not stable)."""
        return f"{self.name}|{self.hostapi}"

    @property
    def label(self) -> str:
        return f"{self.name}  [{self.hostapi}]"


def refresh_devices() -> None:
    """Make PortAudio re-scan the system for devices."""
    sd = backend()
    with PORTAUDIO_LOCK:
        try:
            sd._terminate()
        except Exception:  # noqa: BLE001 - never let a rescan kill the app
            pass
        sd._initialize()


def scan_devices() -> tuple[list[DeviceInfo], list[DeviceInfo]]:
    """Re-scan the system and return what is connected right now."""
    with PORTAUDIO_LOCK:
        refresh_devices()
        return list_devices()


def device_change_token() -> tuple[int, int] | None:
    """A cheap value that changes when the system's audio devices change.

    PortAudio caches its device list, so noticing a headset being plugged in
    otherwise costs a full re-initialisation - which cannot be done while a
    call is running.  On Windows these two MME counters answer the question
    directly and cost nothing, so the expensive rescan happens only when
    something really did change.  ``None`` where there is no such shortcut.
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        winmm = ctypes.windll.winmm
        return int(winmm.waveInGetNumDevs()), int(winmm.waveOutGetNumDevs())
    except Exception:  # noqa: BLE001
        return None


def list_devices() -> tuple[list[DeviceInfo], list[DeviceInfo]]:
    """Return (inputs, outputs) as currently known to PortAudio."""
    sd = backend()
    try:
        hostapis = sd.query_hostapis()
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(str(exc)) from exc

    default_in, default_out = _default_indexes(sd)
    inputs: list[DeviceInfo] = []
    outputs: list[DeviceInfo] = []
    for index, dev in enumerate(devices):
        api = ""
        try:
            api = hostapis[dev["hostapi"]]["name"]
        except (IndexError, KeyError, TypeError):
            pass
        name = str(dev.get("name", "")).strip() or f"device {index}"
        rate = float(dev.get("default_samplerate") or 48000)
        if int(dev.get("max_input_channels", 0)) > 0:
            inputs.append(
                DeviceInfo(index, name, api, int(dev["max_input_channels"]), rate, True)
            )
        if int(dev.get("max_output_channels", 0)) > 0:
            outputs.append(
                DeviceInfo(index, name, api, int(dev["max_output_channels"]), rate, False)
            )

    inputs.sort(key=lambda d: (d.index != default_in, d.hostapi, d.name.lower()))
    outputs.sort(key=lambda d: (d.index != default_out, d.hostapi, d.name.lower()))
    return inputs, outputs


def _default_indexes(sd) -> tuple[int, int]:
    try:
        default = sd.default.device
        return int(default[0]), int(default[1])
    except Exception:  # noqa: BLE001
        return -1, -1


def find_device(devices: Iterable[DeviceInfo], key_or_name: str) -> DeviceInfo | None:
    """Resolve a remembered device, matching on the full key then on the name."""
    if not key_or_name:
        return None
    devices = list(devices)
    for dev in devices:
        if dev.key == key_or_name:
            return dev
    name = key_or_name.split("|")[0]
    for dev in devices:
        if dev.name == name:
            return dev
    return None


_HEADSET_HINTS = ("headset", "headphone", "usb", "earphone", "airpods", "buds", "אוזני")


def looks_like_headset(dev: DeviceInfo) -> bool:
    lowered = dev.name.lower()
    return any(hint in lowered for hint in _HEADSET_HINTS)


def preferred_device(
    devices: list[DeviceInfo],
    current: DeviceInfo | None,
    fresh: list[DeviceInfo],
    prefer_headset: bool,
    remembered: str = "",
) -> DeviceInfo | None:
    """Which device to use now, in order of priority:

    a headset that was just plugged in, the device already in use if it is
    still there, the one remembered from last time, any headset, the system
    default.  A headset appearing wins over the current choice on purpose -
    that is the whole point of plugging one in.
    """
    if not devices:
        return None
    if prefer_headset:
        for dev in fresh:
            if looks_like_headset(dev):
                return dev
    if current is not None:
        still_there = find_device(devices, current.key)
        if still_there is not None:
            return still_there
    known = find_device(devices, remembered)
    if known is not None:
        return known
    if prefer_headset:
        for dev in devices:
            if looks_like_headset(dev):
                return dev
    return devices[0]


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------
RING_INCOMING = "incoming"
RING_OUTGOING = "outgoing"


class AudioEngine:
    """Captures from the microphone, plays back what arrives from the network."""

    def __init__(
        self,
        wire_rate: int,
        frame_ms: int,
        jitter_ms: int,
        on_frame: Callable[[np.ndarray, bool], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._on_frame = on_frame
        self._on_error = on_error
        self.wire_rate = int(wire_rate)
        self.frame_ms = int(frame_ms)
        self.jitter_ms = int(jitter_ms)

        self.muted = False
        self.monitor = False
        self.volume = 1.0
        self.mic_gain = 1.0
        self.transmitting = False
        self.ring_mode: str | None = None

        self.in_level = 0.0
        self.out_level = 0.0
        self.input_device: DeviceInfo | None = None
        self.output_device: DeviceInfo | None = None
        self.input_rate = 0
        self.output_rate = 0
        self.xruns = 0

        self.jitter = JitterBuffer(self.frame_samples, *self._jitter_depths())

        self._in_stream = None
        self._out_stream = None
        self._in_acc = np.zeros(0, dtype=np.float32)
        self._out_pending = np.zeros(0, dtype=np.float32)
        self._in_rs = None
        self._out_rs = None
        self._monitor_frames: list[np.ndarray] = []
        self._ring_pos = 0
        self._watchdog: threading.Thread | None = None
        self._stop_watchdog = threading.Event()
        self._reported_failure = False
        # While this is set the callbacks return immediately without taking the
        # lock.  Two reasons: they must not run against half-configured
        # resamplers, and PortAudio's abort() waits for the callback to return -
        # if that callback were blocked on our lock we would deadlock.
        self._configuring = True

    # -- configuration ---------------------------------------------------
    @property
    def frame_samples(self) -> int:
        return max(1, int(self.wire_rate * self.frame_ms / 1000))

    def _jitter_depths(self) -> tuple[int, int]:
        target = max(2, int(round(self.jitter_ms / max(1, self.frame_ms))))
        return target, max(target + 2, target * 4)

    def configure_wire(self, wire_rate: int, frame_ms: int, jitter_ms: int) -> None:
        """Change the network audio format (also used to follow the caller)."""
        with self._lock:
            if (wire_rate, frame_ms, jitter_ms) == (self.wire_rate, self.frame_ms, self.jitter_ms):
                return
            self.wire_rate = int(wire_rate)
            self.frame_ms = int(frame_ms)
            self.jitter_ms = int(jitter_ms)
            self.jitter.reconfigure(self.frame_samples, *self._jitter_depths())
            self._in_acc = np.zeros(0, dtype=np.float32)
            self._out_pending = np.zeros(0, dtype=np.float32)
            self._monitor_frames.clear()
            self._rebuild_resamplers()

    def _rebuild_resamplers(self) -> None:
        self._in_rs = make_resampler(self.input_rate, self.wire_rate) if self.input_rate else None
        self._out_rs = make_resampler(self.wire_rate, self.output_rate) if self.output_rate else None

    # -- lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._in_stream is not None or self._out_stream is not None

    def start(self, input_device: DeviceInfo | None, output_device: DeviceInfo | None) -> None:
        """Open the streams.  Raises AudioUnavailable if neither can be opened."""
        self.stop()
        sd = backend()
        errors: list[str] = []
        in_stream = out_stream = None
        in_rate = out_rate = 0

        # Streams are opened outside the engine lock (opening can block for a
        # while) and started only once the engine is configured for their rates.
        # PORTAUDIO_LOCK keeps a device rescan from pulling the library out
        # from under us while a stream is being opened.
        with PORTAUDIO_LOCK:
            if input_device is not None:
                try:
                    in_stream, in_rate = self._open_input(sd, input_device)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{input_device.name}: {exc}")
            if output_device is not None:
                try:
                    out_stream, out_rate = self._open_output(sd, output_device)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{output_device.name}: {exc}")

        with self._lock:
            self._reported_failure = False
            self._in_stream, self.input_rate = in_stream, in_rate
            self._out_stream, self.output_rate = out_stream, out_rate
            self.input_device = input_device if in_stream is not None else None
            self.output_device = output_device if out_stream is not None else None
            self._in_acc = np.zeros(0, dtype=np.float32)
            self._out_pending = np.zeros(0, dtype=np.float32)
            self._monitor_frames.clear()
            self._rebuild_resamplers()
            self.jitter.reset()

        if not self.running:
            raise AudioUnavailable("; ".join(errors) or "no audio device selected")

        self._configuring = False
        for stream in (in_stream, out_stream):
            if stream is not None:
                try:
                    stream.start()
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
        if errors and self._on_error is not None:
            self._on_error(AudioUnavailable("; ".join(errors)))

        self._stop_watchdog.clear()
        self._watchdog = threading.Thread(target=self._watch_streams, daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        self._configuring = True
        self._stop_watchdog.set()
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=1.0)

        with self._lock:
            streams = (self._in_stream, self._out_stream)
            self._in_stream = self._out_stream = None
            self.input_device = self.output_device = None
            self.input_rate = self.output_rate = 0
            self.in_level = self.out_level = 0.0

        # Closing has to happen with the engine lock released: abort() waits for
        # the audio callback to return, and the callback wants that same lock.
        with PORTAUDIO_LOCK:
            for stream in streams:
                if stream is None:
                    continue
                try:
                    stream.abort(ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    stream.close(ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass

    def begin_call(self) -> None:
        with self._lock:
            self.jitter.reset()
            self.jitter.reset_stats()
            self._out_pending = np.zeros(0, dtype=np.float32)
            self.ring_mode = None
            self.transmitting = True

    def end_call(self) -> None:
        with self._lock:
            self.transmitting = False
            self.ring_mode = None
            self.jitter.reset()

    def push_incoming(self, seq: int, samples: np.ndarray) -> None:
        self.jitter.push(seq, samples)

    # -- stream setup ----------------------------------------------------
    def _open_stream(self, factory, dev: DeviceInfo, callback, what: str):
        """Open a device, preferring the wire rate but accepting what it offers."""
        last: Exception | None = None
        for rate in _candidate_rates(self.wire_rate, dev.samplerate):
            for channels in _candidate_channels(dev.channels):
                try:
                    stream = factory(
                        device=dev.index,
                        channels=channels,
                        samplerate=rate,
                        dtype="float32",
                        blocksize=int(rate * self.frame_ms / 1000),
                        callback=callback,
                    )
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    continue
                return stream, int(stream.samplerate)
        raise AudioUnavailable(str(last or f"cannot open {what}"))

    def _open_input(self, sd, dev: DeviceInfo):
        return self._open_stream(sd.InputStream, dev, self._input_callback, "microphone")

    def _open_output(self, sd, dev: DeviceInfo):
        return self._open_stream(sd.OutputStream, dev, self._output_callback, "speaker")

    # -- audio callbacks -------------------------------------------------
    def _input_callback(self, indata, frames, time_info, status) -> None:
        if self._configuring:
            return
        if status:
            self.xruns += 1
        try:
            with self._lock:
                mono = np.asarray(indata, dtype=np.float32)
                mono = mono.mean(axis=1) if mono.ndim > 1 and mono.shape[1] > 1 else mono.reshape(-1)
                if self.muted:
                    mono = np.zeros(len(mono), dtype=np.float32)
                elif self.mic_gain != 1.0:
                    mono = np.clip(mono * self.mic_gain, -1.0, 1.0)
                self.in_level = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0

                if self._in_rs is not None:
                    mono = self._in_rs.process(mono)
                if len(mono):
                    self._in_acc = np.concatenate([self._in_acc, mono])

                size = self.frame_samples
                ready: list[np.ndarray] = []
                while len(self._in_acc) >= size:
                    ready.append(self._in_acc[:size].copy())
                    self._in_acc = self._in_acc[size:]
                monitor = self.monitor
                transmitting = self.transmitting
                muted = self.muted
                for frame in ready:
                    if monitor:
                        self._monitor_frames.append(frame)
                        if len(self._monitor_frames) > 8:
                            del self._monitor_frames[0]
            if transmitting:
                for frame in ready:
                    self._on_frame(frame, muted)
        except Exception as exc:  # noqa: BLE001 - a raise here would kill the stream
            self._fail(exc)

    def _output_callback(self, outdata, frames, time_info, status) -> None:
        if self._configuring:
            outdata.fill(0)
            return
        if status:
            self.xruns += 1
        try:
            with self._lock:
                while len(self._out_pending) < frames:
                    wire = self._next_wire_frame()
                    if self._out_rs is not None:
                        wire = self._out_rs.process(wire)
                    if len(wire):
                        self._out_pending = np.concatenate([self._out_pending, wire])
                chunk = self._out_pending[:frames]
                self._out_pending = self._out_pending[frames:]
                if self.volume != 1.0:
                    chunk = chunk * self.volume
                chunk = np.clip(chunk, -1.0, 1.0)
                self.out_level = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
            outdata[:] = chunk.reshape(-1, 1)
        except Exception as exc:  # noqa: BLE001
            outdata.fill(0)
            self._fail(exc)

    def _next_wire_frame(self) -> np.ndarray:
        """One frame of playback audio at the wire rate (lock already held)."""
        size = self.frame_samples
        if self.ring_mode is not None:
            return self._ring_frame(size, self.ring_mode)
        frame = np.zeros(size, dtype=np.float32)
        if self.transmitting:
            frame, _ = self.jitter.pop()
        if self.monitor and self._monitor_frames:
            frame = np.clip(frame + self._monitor_frames.pop(0), -1.0, 1.0)
        return frame

    def _ring_frame(self, size: int, mode: str) -> np.ndarray:
        t = (np.arange(size, dtype=np.float64) + self._ring_pos) / float(self.wire_rate)
        self._ring_pos += size
        if mode == RING_OUTGOING:
            period, bursts, tones, level = 4.0, ((0.0, 1.0),), (425.0,), 0.10
        else:
            period, bursts, tones, level = 3.0, ((0.0, 0.4), (0.6, 1.0)), (440.0, 554.0), 0.28
        phase = np.mod(t, period)
        env = np.zeros_like(phase)
        for start, end in bursts:
            env = np.maximum(env, _taper(phase, start, end))
        wave = np.zeros_like(phase)
        for freq in tones:
            wave += np.sin(2.0 * np.pi * freq * t)
        wave /= len(tones)
        return (wave * env * level).astype(np.float32)

    # -- failure handling ------------------------------------------------
    def _fail(self, exc: Exception) -> None:
        if self._reported_failure:
            return
        self._reported_failure = True
        if self._on_error is not None:
            self._on_error(exc)

    def _watch_streams(self) -> None:
        """Notice a device that was unplugged and report it once."""
        while not self._stop_watchdog.wait(1.0):
            dead = []
            with self._lock:
                for name, stream in (("input", self._in_stream), ("output", self._out_stream)):
                    if stream is None:
                        continue
                    try:
                        alive = bool(stream.active)
                    except Exception:  # noqa: BLE001
                        alive = False
                    if not alive:
                        dead.append(name)
            if dead:
                self._fail(AudioUnavailable(f"audio stream stopped ({', '.join(dead)})"))
                return


def _taper(phase: np.ndarray, start: float, end: float, ramp: float = 0.02) -> np.ndarray:
    """A 0..1 envelope over [start, end] with short linear edges (no clicks)."""
    return np.clip(np.minimum((phase - start) / ramp, (end - phase) / ramp), 0.0, 1.0)


def _candidate_rates(wire_rate: int, device_rate: float) -> list[int]:
    rates = [int(wire_rate), int(device_rate or 0), 48000, 44100, 32000, 16000]
    seen: set[int] = set()
    out: list[int] = []
    for rate in rates:
        if rate > 0 and rate not in seen:
            seen.add(rate)
            out.append(rate)
    return out


def _candidate_channels(max_channels: int) -> list[int]:
    if max_channels <= 1:
        return [1]
    return [1, min(2, max_channels)]


def wait_for_device(timeout: float = 5.0) -> None:
    """Small helper used by the CLI smoke test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inputs, outputs = list_devices()
        if inputs and outputs:
            return
        time.sleep(0.5)

# --------------------------------------------------------------------------
# from lanphone/net.py
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def local_ip(target: str = "8.8.8.8") -> str:
    """The address this machine would use to reach ``target``."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; this only asks the routing table which source
        # address would be used for an outbound connection.
        sock.connect((target, 80))
        return sock.getsockname()[0]
    except OSError:
        pass
    finally:
        sock.close()
    # No default route at all - a LAN with no gateway, for instance. Fall back
    # to whatever the machine calls itself, skipping addresses that mean
    # "no network": loopback and the 169.254.x.x DHCP-failed range.
    addresses = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.append(info[4][0])
    except OSError:
        pass
    for address in addresses:
        if not address.startswith(("127.", "169.254.")):
            return address
    return addresses[0] if addresses else "127.0.0.1"


# Reasons a call could not even leave this machine.
OTHER_SUBNET = "other_subnet"
CALLING_SELF = "calling_self"
NO_NETWORK = "no_network"
NO_DHCP = "no_dhcp"


def diagnose_target(host: str, local: str) -> str | None:
    """Explain why ``host`` is unreachable, or None when there is no clue.

    Windows reports "an unreachable network" for anything with no route, which
    is accurate and useless. Nearly always the two machines are simply on
    different subnets, and comparing the addresses says so outright.
    """
    try:
        target = ipaddress.ip_address(host.strip())
        here = ipaddress.ip_address(local.strip())
    except ValueError:
        return None  # a host name, or no address of our own: cannot judge
    if target == here:
        return CALLING_SELF
    if here.is_loopback:
        return NO_NETWORK
    if here.is_link_local:  # 169.254.x.x: DHCP never answered
        return NO_DHCP
    # Home routers hand out /24s, so this is a strong hint rather than proof.
    if ipaddress.ip_network(f"{here}/24", strict=False) != ipaddress.ip_network(
        f"{target}/24", strict=False
    ):
        return OTHER_SUBNET
    return None


def broadcast_targets(ip: str | None = None) -> list[str]:
    """Addresses presence packets are sent to."""
    targets = ["255.255.255.255"]
    ip = ip or local_ip()
    try:
        # Home routers hand out /24 subnets; the directed broadcast gets through
        # on setups where 255.255.255.255 is filtered.
        subnet = ipaddress.ip_network(f"{ip}/24", strict=False)
        targets.append(str(subnet.broadcast_address))
    except ValueError:
        pass
    return targets


def is_valid_host(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return all(part and len(part) < 64 for part in text.split("."))


def bind_with_fallback(sock: socket.socket, port: int, host: str = "") -> int:
    """Bind to ``port``, or the next free one, so a second copy can also run."""
    last: OSError | None = None
    for candidate in range(port, port + PORT_SEARCH_RANGE):
        try:
            sock.bind((host, candidate))
            return candidate
        except OSError as exc:
            last = exc
    raise OSError(f"no free port in {port}..{port + PORT_SEARCH_RANGE - 1}") from last


# --------------------------------------------------------------------------
# presence discovery
# --------------------------------------------------------------------------
class Discovery:
    """Announces this machine on the LAN and tracks everyone else."""

    def __init__(
        self,
        name_getter: Callable[[], str],
        signaling_port: int,
        on_change: Callable[[list[dict[str, Any]]], None],
        port: int = DISCOVERY_PORT,
    ) -> None:
        self.node_id = uuid.uuid4().hex[:12]
        self._name_getter = name_getter
        self._signaling_port = signaling_port
        self._on_change = on_change
        self._port = port
        self._peers: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._rx: socket.socket | None = None
        self._tx: socket.socket | None = None
        self.bind_error: OSError | None = None

    def start(self) -> None:
        """Never fatal: without discovery the app still works by typed address."""
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Two copies of the app on one machine must both receive the broadcasts.
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            rx.bind(("", self._port))
            rx.settimeout(0.5)
            self._rx = rx
        except OSError as exc:
            rx.close()
            self.bind_error = exc

        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        loops = [self._announce_loop]
        if self._rx is not None:
            loops.append(self._listen_loop)
        for target in loops:
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        # Join before closing the sockets: it gives the announce loop its chance
        # to send the farewell packet, so we vanish from the peer list at once
        # instead of timing out there.  Both loops notice the event in <= 0.5 s.
        for thread in self._threads:
            thread.join(timeout=1.5)
        for sock in (self._rx, self._tx):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    @property
    def peers(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._peers.values(), key=lambda p: p["name"].lower())

    def announce_now(self) -> None:
        self._send_presence()

    def forget_all(self) -> None:
        with self._lock:
            self._peers.clear()
        self._on_change(self.peers)

    # -- internals -------------------------------------------------------
    def _send_presence(self) -> None:
        if self._tx is None:
            return
        payload = encode_message               (
            {
                "t": "hello",
                "v": PROTOCOL_VERSION,
                "id": self.node_id,
                "name": self._name_getter(),
                "sig": self._signaling_port,
            }
        )
        for target in broadcast_targets():
            try:
                self._tx.sendto(payload, (target, self._port))
            except OSError:
                pass

    def _announce_loop(self) -> None:
        while not self._stop.is_set():
            self._send_presence()
            self._expire()
            self._stop.wait(DISCOVERY_INTERVAL)
        # Politely disappear from the other side's list straight away.
        if self._tx is not None:
            payload = encode_message               ({"t": "bye", "id": self.node_id})
            for target in broadcast_targets():
                try:
                    self._tx.sendto(payload, (target, self._port))
                except OSError:
                    pass

    def _listen_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._rx.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = decode_message               (data.strip())
            except ProtocolError              :
                continue
            self._handle(msg, addr[0])

    def _handle(self, msg: dict[str, Any], ip: str) -> None:
        node_id = msg.get("id")
        if not isinstance(node_id, str) or node_id == self.node_id:
            return
        changed = False
        if msg.get("t") == "bye":
            with self._lock:
                changed = self._peers.pop(node_id, None) is not None
        elif msg.get("t") == "hello":
            peer = {
                "id": node_id,
                "name": str(msg.get("name") or ip)[:64],
                "ip": ip,
                "sig_port": int(msg.get("sig") or 0),
                "last_seen": time.monotonic(),
            }
            if not peer["sig_port"]:
                return
            with self._lock:
                known = self._peers.get(node_id)
                changed = (
                    known is None
                    or known["name"] != peer["name"]
                    or known["ip"] != peer["ip"]
                    or known["sig_port"] != peer["sig_port"]
                )
                self._peers[node_id] = peer
        if changed:
            self._on_change(self.peers)

    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            stale = [k for k, v in self._peers.items() if now - v["last_seen"] > PEER_TIMEOUT]
            for key in stale:
                del self._peers[key]
        if stale:
            self._on_change(self.peers)


# --------------------------------------------------------------------------
# signalling
# --------------------------------------------------------------------------
class SignalingLink:
    """One TCP control connection, kept open for the duration of a call."""

    def __init__(self, sock: socket.socket, peer_ip: str) -> None:
        self.sock = sock
        self.peer_ip = peer_ip
        self.closed = threading.Event()
        self._on_message: Callable[["SignalingLink", dict[str, Any]], None] | None = None
        self._on_close: Callable[["SignalingLink"], None] | None = None
        self._send_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(
        self,
        on_message: Callable[["SignalingLink", dict[str, Any]], None],
        on_close: Callable[["SignalingLink"], None],
    ) -> None:
        self._on_message = on_message
        self._on_close = on_close
        self.sock.settimeout(None)
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def send(self, msg: dict[str, Any]) -> bool:
        if self.closed.is_set():
            return False
        try:
            with self._send_lock:
                self.sock.sendall(encode_message               (msg))
            return True
        except OSError:
            self.close()
            return False

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        if self._on_close is not None:
            self._on_close(self)

    def _read_loop(self) -> None:
        buffer = b""
        try:
            while not self.closed.is_set():
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_MESSAGE_SIZE                 :
                    break
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = decode_message               (line)
                    except ProtocolError              :
                        continue
                    if self._on_message is not None:
                        self._on_message(self, msg)
        except OSError:
            pass
        finally:
            self.close()


class SignalingServer:
    """Listens for incoming calls."""

    def __init__(self, port: int, on_link: Callable[[SignalingLink], None]) -> None:
        self._wanted_port = port
        self._on_link = on_link
        self.port = port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.port = bind_with_fallback(self._sock, self._wanted_port)
        self._sock.listen(4)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._on_link(SignalingLink(conn, addr[0]))


def connect_signaling(ip: str, port: int, timeout: float = 5.0) -> SignalingLink:
    sock = socket.create_connection((ip, port), timeout=timeout)
    return SignalingLink(sock, ip)


# --------------------------------------------------------------------------
# audio transport
# --------------------------------------------------------------------------
class AudioTransport:
    """UDP socket carrying the voice frames of the current call."""

    def __init__(self, port: int, on_frame: Callable[[int, int, Any], None]) -> None:
        self._wanted_port = port
        self._on_frame = on_frame
        self.port = port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._remote: tuple[str, int] | None = None
        self._call_id = 0
        self._seq = 0
        self.sent = 0
        self.received = 0
        self.bad = 0

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.port = bind_with_fallback(self._sock, self._wanted_port)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def open_call(self, remote_ip: str, remote_port: int, call_id: int) -> None:
        self._remote = (remote_ip, int(remote_port))
        self._call_id = int(call_id)
        self._seq = 0
        self.sent = self.received = self.bad = 0

    def close_call(self) -> None:
        self._remote = None
        self._call_id = 0

    @property
    def active(self) -> bool:
        return self._remote is not None

    def send_frame(self, samples: Any, silence: bool = False) -> None:
        remote = self._remote
        if remote is None or self._sock is None:
            return
        packet = encode_audio             (self._call_id, self._seq, samples, silence)
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        try:
            self._sock.sendto(packet, remote)
            self.sent += 1
        except OSError:
            pass

    def _recv_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            remote = self._remote
            if remote is None or addr[0] != remote[0]:
                continue
            try:
                call_id, seq, flags, samples = decode_audio             (data)
            except ProtocolError              :
                self.bad += 1
                continue
            if call_id != self._call_id:
                continue  # leftover packets from a previous call
            self.received += 1
            self._on_frame(seq, flags, samples)

# --------------------------------------------------------------------------
# from lanphone/phone.py
# --------------------------------------------------------------------------

IDLE = "idle"
CALLING = "calling"
RINGING = "ringing"
IN_CALL = "in_call"

PING_INTERVAL = 2.0
CALL_TIMEOUT = 45.0
# How often to look for a headset that was just plugged in.
DEVICE_POLL_INTERVAL = 1.5


class Phone:
    def __init__(
        self,
        settings: Settings,
        emit: Callable[..., None],
        signaling_port: int = SIGNALING_PORT,
        audio_port: int = AUDIO_PORT,
        discovery_port: int = DISCOVERY_PORT,
    ) -> None:
        self.settings = settings
        self._emit = emit
        self._lock = threading.RLock()
        self._discovery_port = discovery_port

        self.state = IDLE
        self.local_ip = "127.0.0.1"
        self.peer_name = ""
        self.peer_ip = ""
        self._peer_port = SIGNALING_PORT
        self.rtt_ms: float | None = None

        self.inputs: list[DeviceInfo           ] = []
        self.outputs: list[DeviceInfo           ] = []
        self.selected_input: DeviceInfo            | None = None
        self.selected_output: DeviceInfo            | None = None
        self.audio_ok = False

        self.engine = AudioEngine            (
            settings.wire_rate,
            settings.frame_ms,
            settings.jitter_ms,
            self._on_captured_frame,
            self._on_audio_error,
        )
        self.engine.volume = settings.volume
        self.engine.mic_gain = settings.mic_gain

        self.transport = AudioTransport               (audio_port, self._on_network_frame)
        self.signaling = SignalingServer                (signaling_port, self._on_incoming_link)
        self.discovery: Discovery           | None = None

        self._link: SignalingLink               | None = None
        self._pending: dict[str, Any] | None = None
        self._call_id = 0
        self._call_started = 0.0
        self._ticker: threading.Thread | None = None
        self._stop_ticker = threading.Event()
        self._last_recovery = 0.0
        self._monitor_requested = False
        self._audio_was_running = False
        self._device_watch: threading.Thread | None = None
        self._stop_device_watch = threading.Event()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        self.local_ip = local_ip         ()
        self.transport.start()
        self.signaling.start()
        self.discovery = Discovery          (
            lambda: self.settings.display_name,
            self.signaling.port,
            lambda peers: self._emit("peers", peers=peers),
            self._discovery_port,
        )
        self.discovery.start()
        if self.discovery.bind_error is not None:
            self._emit("log", key="log_discovery_error", err=str(self.discovery.bind_error))
        self._emit("log", key="log_started", ip=self.local_ip, port=self.signaling.port)
        self.refresh_devices(initial=True)
        self._stop_device_watch.clear()
        self._device_watch = threading.Thread(target=self._watch_devices, daemon=True)
        self._device_watch.start()

    def stop(self) -> None:
        try:
            self.hangup()
        except Exception:  # noqa: BLE001
            pass
        self._stop_ticker.set()
        self._stop_device_watch.set()
        if self._device_watch is not None:
            self._device_watch.join(timeout=2.0)
        if self.discovery is not None:
            self.discovery.stop()
        self.signaling.stop()
        self.transport.stop()
        self.engine.stop()

    # ------------------------------------------------------------------
    # devices
    # ------------------------------------------------------------------
    def refresh_devices(self, initial: bool = False) -> list[DeviceInfo           ]:
        """Re-scan the system.  Returns devices that were not there before."""
        try:
            devices = list_devices             () if initial else self._rescan()
        except AudioUnavailable                  as exc:
            self.inputs, self.outputs = [], []
            self._emit("log", key="log_audio_error", err=str(exc))
            self._emit("devices")
            return []
        return self._apply_devices(*devices, announce=not initial)

    def _rescan(self) -> tuple[list[DeviceInfo           ], list[DeviceInfo           ]]:
        """Ask the system again.  Stops the streams: PortAudio has to restart."""
        self._audio_was_running = self.engine.running
        if self._audio_was_running:
            self.engine.stop()
        return scan_devices             ()

    def _apply_devices(
        self,
        inputs: list[DeviceInfo           ],
        outputs: list[DeviceInfo           ],
        announce: bool,
    ) -> list[DeviceInfo           ]:
        previous = {dev.key for dev in self.inputs + self.outputs}
        self.inputs, self.outputs = inputs, outputs
        fresh = [d for d in inputs + outputs if d.key not in previous]

        if announce:
            self._emit("log", key="log_devices_refreshed", count=len(inputs) + len(outputs))
            for dev in fresh:
                self._emit("log", key="log_new_device", name=dev.label)

        before = (self.selected_input, self.selected_output)
        self._resolve_selection(fresh if announce else [])
        if announce:
            for chosen, was in zip((self.selected_input, self.selected_output), before):
                if chosen is not None and chosen != was:
                    self._emit("log", key="log_using_device", name=chosen.label)

        self._emit("devices")
        if self._audio_was_running or self._monitor_requested:
            self._audio_was_running = False
            self.ensure_audio(restart=True)
        return fresh

    def _resolve_selection(self, fresh: list[DeviceInfo           ]) -> None:
        """Pick the devices to use, preferring a headset that just appeared."""
        prefer = self.settings.auto_pick_new_device
        self.selected_input = preferred_device                 (
            self.inputs,
            self.selected_input,
            [d for d in fresh if d.is_input],
            prefer,
            self.settings.input_device_name,
        )
        self.selected_output = preferred_device                 (
            self.outputs,
            self.selected_output,
            [d for d in fresh if not d.is_input],
            prefer,
            self.settings.output_device_name,
        )
        self._remember_selection()
        if not self.inputs:
            self._emit("log", key="log_no_input")
        if not self.outputs:
            self._emit("log", key="log_no_output")

    # -- automatic hot-plug detection ------------------------------------
    def _watch_devices(self) -> None:
        """Notice a headset being plugged in without anyone pressing Refresh."""
        token = device_change_token                    ()
        while not self._stop_device_watch.wait(DEVICE_POLL_INTERVAL):
            try:
                if token is not None:
                    # Cheap check (Windows): only rescan when it really changed,
                    # so this can run during a call too.
                    current = device_change_token                    ()
                    if current == token:
                        continue
                    token = current
                    self.refresh_devices()
                    continue
                # No cheap signal: the rescan itself is the check, so it may
                # only run while the sound card is free.
                if self.engine.running:
                    continue
                inputs, outputs = scan_devices             ()
                if {d.key for d in inputs + outputs} != {
                    d.key for d in self.inputs + self.outputs
                }:
                    self._apply_devices(inputs, outputs, announce=True)
            except AudioUnavailable                 :
                continue
            except Exception:  # noqa: BLE001 - a watcher must never take the app down
                continue

    def _remember_selection(self) -> None:
        self.settings.input_device_name = self.selected_input.key if self.selected_input else ""
        self.settings.output_device_name = self.selected_output.key if self.selected_output else ""

    def select_input(self, dev: DeviceInfo            | None) -> None:
        self.selected_input = dev
        self._remember_selection()
        if self.engine.running:
            self.ensure_audio(restart=True)

    def select_output(self, dev: DeviceInfo            | None) -> None:
        self.selected_output = dev
        self._remember_selection()
        if self.engine.running:
            self.ensure_audio(restart=True)

    def ensure_audio(self, restart: bool = False) -> bool:
        """Open the audio streams for the current selection."""
        if self.engine.running and not restart:
            return True
        if self.selected_input is None and self.selected_output is None:
            self.audio_ok = False
            return False
        try:
            self.engine.start(self.selected_input, self.selected_output)
        except AudioUnavailable                  as exc:
            self.audio_ok = False
            self._emit("log", key="log_audio_error", err=str(exc))
            return False
        self.audio_ok = True
        self._emit(
            "log",
            key="log_audio_ready",
            mic=self.selected_input.name if self.selected_input else "-",
            out=self.selected_output.name if self.selected_output else "-",
        )
        return True

    def release_audio(self) -> None:
        """Close the devices when idle, so a replug can be detected."""
        if self._monitor_requested or self.state != IDLE:
            return
        self.engine.stop()
        self.audio_ok = False

    # -- knobs -----------------------------------------------------------
    def set_mute(self, muted: bool) -> None:
        self.engine.muted = bool(muted)

    def set_volume(self, volume: float) -> None:
        self.engine.volume = max(0.0, min(2.0, float(volume)))
        self.settings.volume = self.engine.volume

    def set_mic_gain(self, gain: float) -> None:
        self.engine.mic_gain = max(0.0, min(4.0, float(gain)))
        self.settings.mic_gain = self.engine.mic_gain

    def set_monitor(self, enabled: bool) -> None:
        self._monitor_requested = bool(enabled)
        self.engine.monitor = bool(enabled)
        if enabled:
            self.ensure_audio()
            self._emit("log", key="log_monitor_on")
        else:
            self._emit("log", key="log_monitor_off")
            self.release_audio()

    # ------------------------------------------------------------------
    # outgoing calls
    # ------------------------------------------------------------------
    def remember_peer(self, ip: str, port: int = SIGNALING_PORT, name: str = "") -> None:
        """Add an address to the saved list and let the interface persist it."""
        if self.settings.remember_peer(ip, port, name):
            self._emit("saved_peers")

    def forget_peer(self, ip: str) -> bool:
        if not self.settings.forget_peer(ip):
            return False
        self._emit("saved_peers")
        self._emit("log", key="log_peer_forgotten", ip=ip)
        return True

    def place_call(self, host: str, port: int = SIGNALING_PORT) -> None:
        with self._lock:
            if self.state != IDLE:
                return
            self.state = CALLING
            self.peer_ip = host
            self.peer_name = host
            self._peer_port = int(port)
            self.rtt_ms = None
            self._call_id = random.getrandbits(32)
            # Starts the no-answer timeout; without this the ticker would still
            # be holding the timestamp of the previous call.
            self._call_started = time.monotonic()
        self.remember_peer(host, port)
        self._emit("state", state=CALLING)
        self._emit("log", key="log_calling", ip=host)
        threading.Thread(target=self._dial, args=(host, int(port)), daemon=True).start()

    def _dial(self, host: str, port: int) -> None:
        try:
            link = connect_signaling                  (host, port)
        except OSError as exc:
            self._emit("log", key="log_call_failed", ip=host, err=_reason(exc))
            reason = diagnose_target                (host, self.local_ip)
            if reason is not None:
                self._emit("log", key=f"log_why_{reason}", ip=host, local=self.local_ip)
            self._finish_call(notify=False)
            return
        with self._lock:
            if self.state != CALLING:
                link.close()
                return
            self._link = link
        link.start(self._on_message, self._on_link_closed)
        self.ensure_audio()
        self.engine.ring_mode = RING_OUTGOING              
        link.send(
            {
                "t": INVITE       ,
                "v": PROTOCOL_VERSION,
                "name": self.settings.display_name,
                "call_id": self._call_id,
                "audio_port": self.transport.port,
                "sig_port": self.signaling.port,  # so the callee can call back
                "rate": self.settings.wire_rate,
                "frame_ms": self.settings.frame_ms,
            }
        )
        self._start_ticker()

    # ------------------------------------------------------------------
    # incoming calls
    # ------------------------------------------------------------------
    def _on_incoming_link(self, link: SignalingLink              ) -> None:
        link.start(self._on_message, self._on_link_closed)

    def _on_message(self, link: SignalingLink              , msg: dict[str, Any]) -> None:
        kind = msg.get("t")
        if kind == INVITE       :
            self._handle_invite(link, msg)
        elif kind == MSG_RINGING        :
            with self._lock:
                if self.state == CALLING:
                    self.peer_name = str(msg.get("name") or self.peer_ip)[:64]
                    self.remember_peer(self.peer_ip, self._peer_port, self.peer_name)
            self._emit("log", key="log_ringing_out", name=self.peer_name)
            self._emit("state", state=CALLING)
        elif kind == ACCEPT       :
            self._handle_accept(link, msg)
        elif kind == REJECT       :
            self._emit("log", key="log_rejected")
            self._finish_call(notify=False)
        elif kind == BUSY     :
            self._emit("log", key="log_busy")
            self._finish_call(notify=False)
        elif kind == BYE    :
            if self.state in (IN_CALL, CALLING, RINGING):
                self._emit("log", key="log_peer_hangup")
            self._finish_call(notify=False)
        elif kind == PING     :
            link.send({"t": PONG     , "ts": msg.get("ts")})
        elif kind == PONG     :
            try:
                sent = float(msg.get("ts") or 0.0)
            except (TypeError, ValueError):
                return
            if sent:
                self.rtt_ms = max(0.0, (time.monotonic() - sent) * 1000.0)

    def _handle_invite(self, link: SignalingLink              , msg: dict[str, Any]) -> None:
        with self._lock:
            if self.state != IDLE or self._link is not None:
                link.send({"t": BUSY     })
                threading.Timer(0.3, link.close).start()
                return
            rate = _clamp_rate(msg.get("rate"))
            frame_ms = _clamp_frame_ms(msg.get("frame_ms"), rate)
            self._link = link
            self._pending = {
                "name": str(msg.get("name") or link.peer_ip)[:64],
                "ip": link.peer_ip,
                "call_id": int(msg.get("call_id") or 0) & 0xFFFFFFFF,
                "audio_port": int(msg.get("audio_port") or 0),
                "rate": rate,
                "frame_ms": frame_ms,
            }
            self.peer_name = self._pending["name"]
            self.peer_ip = link.peer_ip
            self._peer_port = _clamp_port(msg.get("sig_port"))
            self.state = RINGING
            self._call_started = time.monotonic()

        link.send({"t": MSG_RINGING        , "name": self.settings.display_name})
        # An incoming call is worth remembering too, so calling back is one click.
        self.remember_peer(link.peer_ip, self._peer_port, self.peer_name)
        self._emit("state", state=RINGING)
        self._emit("log", key="log_incoming", name=self.peer_name, ip=self.peer_ip)
        self._emit("incoming", name=self.peer_name, ip=self.peer_ip)
        if rate != self.settings.wire_rate:
            self._emit("log", key="log_rate_from_peer", rate=rate)
        self.ensure_audio()
        self.engine.configure_wire(rate, frame_ms, self.settings.jitter_ms)
        self.engine.ring_mode = RING_INCOMING              
        self._start_ticker()
        if self.settings.auto_answer:
            threading.Timer(0.6, self.answer).start()

    def answer(self) -> None:
        with self._lock:
            if self.state != RINGING or self._link is None or self._pending is None:
                return
            link, pending = self._link, self._pending
            self._pending = None
            self._call_id = pending["call_id"]
        link.send(
            {
                "t": ACCEPT       ,
                "name": self.settings.display_name,
                "audio_port": self.transport.port,
            }
        )
        self._begin_media(pending["ip"], pending["audio_port"], pending["call_id"])

    def reject(self) -> None:
        with self._lock:
            if self.state != RINGING or self._link is None:
                return
            link = self._link
        link.send({"t": REJECT       , "reason": "declined"})
        self._emit("log", key="log_rejected")
        self._finish_call(notify=False, close_delay=0.3)

    def _handle_accept(self, link: SignalingLink              , msg: dict[str, Any]) -> None:
        with self._lock:
            if self.state != CALLING:
                return
            self.peer_name = str(msg.get("name") or self.peer_ip)[:64]
            port = int(msg.get("audio_port") or 0)
        if port <= 0:
            self._emit("log", key="log_link_lost")
            self._finish_call()
            return
        self.remember_peer(link.peer_ip, self._peer_port, self.peer_name)
        self._begin_media(link.peer_ip, port, self._call_id)

    # ------------------------------------------------------------------
    # media
    # ------------------------------------------------------------------
    def _begin_media(self, remote_ip: str, remote_port: int, call_id: int) -> None:
        with self._lock:
            self.state = IN_CALL
            self._call_started = time.monotonic()
        self.ensure_audio()
        self.engine.begin_call()
        self.transport.open_call(remote_ip, remote_port, call_id)
        self._emit("state", state=IN_CALL)
        self._emit("log", key="log_call_started", name=self.peer_name or remote_ip)
        self._start_ticker()

    def hangup(self) -> None:
        with self._lock:
            active = self.state != IDLE
            link = self._link
            was_ringing = self.state == RINGING
        if not active:
            return
        if link is not None:
            link.send({"t": REJECT        if was_ringing else BYE    })
        self._finish_call(close_delay=0.3)

    def _finish_call(self, notify: bool = True, close_delay: float = 0.0) -> None:
        with self._lock:
            if self.state == IDLE and self._link is None:
                return
            link, self._link = self._link, None
            pending, self._pending = self._pending, None
            self.state = IDLE
            self.peer_name = ""
            self.rtt_ms = None
        self._stop_ticker.set()
        self.transport.close_call()
        self.engine.end_call()
        if link is not None:
            if close_delay > 0:
                threading.Timer(close_delay, link.close).start()
            else:
                link.close()
        if pending is not None:
            self._emit("log", key="log_missed", name=pending["name"])
        elif notify:
            self._emit("log", key="log_call_ended")
        # Restore the configured format after following a caller's choice.
        self.engine.configure_wire(
            self.settings.wire_rate, self.settings.frame_ms, self.settings.jitter_ms
        )
        self._emit("state", state=IDLE)
        self.release_audio()

    def _on_link_closed(self, link: SignalingLink              ) -> None:
        with self._lock:
            if self._link is not link:
                return
            state = self.state
        if state == IN_CALL:
            self._emit("log", key="log_link_lost")
        self._finish_call(notify=state != IN_CALL)

    def _on_captured_frame(self, frame: np.ndarray, muted: bool) -> None:
        self.transport.send_frame(frame, silence=muted)

    def _on_network_frame(self, seq: int, flags: int, samples: np.ndarray) -> None:
        self.engine.push_incoming(seq, samples)

    def _on_audio_error(self, exc: Exception) -> None:
        self._emit("log", key="log_audio_error", err=str(exc))
        self.audio_ok = False
        now = time.monotonic()
        if now - self._last_recovery < 3.0:
            return
        self._last_recovery = now
        threading.Thread(target=self._recover_audio, daemon=True).start()

    def _recover_audio(self) -> None:
        time.sleep(0.5)
        if self.state == IDLE and not self._monitor_requested:
            self.engine.stop()
            return
        self.refresh_devices()

    # ------------------------------------------------------------------
    # periodic work
    # ------------------------------------------------------------------
    def _start_ticker(self) -> None:
        if self._ticker is not None and self._ticker.is_alive():
            return
        self._stop_ticker.clear()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
        self._ticker.start()

    def _tick_loop(self) -> None:
        while not self._stop_ticker.wait(PING_INTERVAL):
            with self._lock:
                link = self._link
                state = self.state
                started = self._call_started
            if link is None or state == IDLE:
                return
            if state in (CALLING, RINGING) and started and time.monotonic() - started > CALL_TIMEOUT:
                self._emit("log", key="log_call_ended")
                self._finish_call(notify=False, close_delay=0.2)
                return
            link.send({"t": PING     , "ts": time.monotonic()})

    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        jitter = self.engine.jitter
        return {
            "sent": self.transport.sent,
            "recv": self.transport.received,
            "lost": jitter.lost + jitter.dropped,
            "depth": jitter.depth,
            "rtt": self.rtt_ms,
            "in_level": self.engine.in_level,
            "out_level": self.engine.out_level,
            "state": self.state,
            "peer": self.peer_name or self.peer_ip,
        }

    @property
    def peers(self) -> list[dict[str, Any]]:
        return self.discovery.peers if self.discovery is not None else []


def _reason(exc: OSError) -> str:
    return getattr(exc, "strerror", None) or str(exc) or exc.__class__.__name__


def _clamp_rate(value: Any) -> int:

    try:
        rate = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIRE_RATE
    return rate if rate in SUPPORTED_RATES else DEFAULT_WIRE_RATE


def _clamp_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return SIGNALING_PORT
    return port if 0 < port < 65536 else SIGNALING_PORT


def _clamp_frame_ms(value: Any, rate: int = 16000) -> int:

    limit = max_frame_ms(rate)
    try:
        frame_ms = int(value)
    except (TypeError, ValueError):
        return min(DEFAULT_FRAME_MS, limit)
    return max(10, min(limit, frame_ms))

# --------------------------------------------------------------------------
# from lanphone/gui.py
# --------------------------------------------------------------------------

VERSION = "1.0"
WATERMARK = "oT"
TICK_MS = 100
MAX_LOG_LINES = 400


class PhoneApp:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.S = Strings()
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self.phone = Phone(self.settings, self._emit)
        self._log_lines: list[tuple[str, str, str]] = []  # (time, text, tag)
        self._peers: list[dict[str, Any]] = []
        self._input_devices: list[DeviceInfo           ] = []
        self._output_devices: list[DeviceInfo           ] = []
        self._saved: list[dict[str, Any]] = []
        self._in_peak = 0.0
        self._out_peak = 0.0
        self._topmost_until = 0.0
        self._alive = True

        self.root = tk.Tk()
        self.root.title(self._window_title())
        self.root.minsize(780, 580)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.style = apply_theme      (self.root)
        # Kept on the instance on purpose: Tk drops an icon whose PhotoImage
        # gets garbage collected, and the feather comes back.
        self.icon = apply_app_icon      (self.root)
        apply_window_chrome      (self.root, caption=BG   , text=TEXT     )
        self.container = ttk.Frame(self.root, padding=(2, 0, 2, 2))
        self.container.pack(fill="both", expand=True)
        self._build_ui()
        self.root.after(TICK_MS, self._tick)
        self.root.after(200, self._start_phone)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def _window_title(self) -> str:
        translated = self.S("app_title")
        return translated if translated == APP_NAME else f"{translated} - {APP_NAME}"

    def _build_ui(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()

        pad = {"padx": 8, "pady": 4}
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(3, weight=1)
        self._build_toolbar()

        # -- header --------------------------------------------------
        header = ttk.Frame(self.container)
        header.grid(row=1, column=0, sticky="ew", **pad)
        header.columnconfigure(1, weight=1)
        name_col, ip_col = 0, 2

        ttk.Label(header, text=self.S("my_name"), style=DIM    ).grid(
            row=0, column=name_col, sticky="w"
        )
        self.name_var = tk.StringVar(value=self.settings.display_name)
        name_entry = ttk.Entry(header, textvariable=self.name_var, width=24)
        name_entry.grid(row=0, column=1, sticky="w", padx=6)
        name_entry.bind("<FocusOut>", self._on_name_changed)
        name_entry.bind("<Return>", self._on_name_changed)

        self.ip_label = ttk.Label(header, text=f"{self.S('my_ip')} ...", style=DIM    )
        self.ip_label.grid(row=0, column=ip_col, sticky="e", padx=6)

        self.status_label = ttk.Label(
            header, text=f"{self.S('status')} {self.S('state_idle')}", style=STATUS       
        )
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # -- middle: peers + audio -----------------------------------
        middle = ttk.Frame(self.container)
        middle.grid(row=2, column=0, sticky="ew", **pad)
        middle.columnconfigure(0, weight=1, uniform="cols")
        middle.columnconfigure(1, weight=1, uniform="cols")
        left_col, right_col = 0, 1

        peers_box = ttk.LabelFrame(middle, text=self.S("peers_group"))
        peers_box.grid(row=0, column=left_col, sticky="nsew", padx=4)
        peers_box.columnconfigure(0, weight=1)

        list_row = ttk.Frame(peers_box)
        list_row.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        list_row.columnconfigure(0, weight=1)
        self.peer_list = tk.Listbox(list_row, height=6, exportselection=False)
        style_listbox              (self.peer_list)
        self.peer_list.grid(row=0, column=0, sticky="ew")
        self.peer_list.bind("<Double-Button-1>", lambda _e: self._on_call())
        self.peer_list.bind("<<ListboxSelect>>", self._on_peer_selected)
        scroll = ttk.Scrollbar(list_row, orient="vertical", command=self.peer_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.peer_list.configure(yscrollcommand=scroll.set)

        label_col, field_col = 0, 1
        ip_row = ttk.Frame(peers_box)
        ip_row.grid(row=1, column=0, sticky="ew", padx=6, pady=4)
        addr_cols = (0, 1, 2)  # label | address box | forget
        ip_row.columnconfigure(addr_cols[1], weight=1)
        ttk.Label(ip_row, text=self.S("address"), style=DIM    ).grid(row=0, column=addr_cols[0], sticky="w")
        self.ip_var = tk.StringVar(value=self.settings.last_peer_ip)
        # Editable on purpose: type a new address, or pick a saved one.
        self.ip_combo = ttk.Combobox(ip_row, textvariable=self.ip_var, width=18)
        self.ip_combo.grid(row=0, column=addr_cols[1], sticky="ew", padx=6)
        self.ip_combo.bind("<Return>", lambda _e: self._on_call())
        self.ip_combo.bind("<<ComboboxSelected>>", self._on_saved_selected)
        self.forget_btn = ttk.Button(
            ip_row, text=self.S("forget"), width=8, command=self._on_forget
        )
        self.forget_btn.grid(row=0, column=addr_cols[2], sticky="e")

        buttons = ttk.Frame(peers_box)
        buttons.grid(row=2, column=0, sticky="ew", padx=6, pady=(4, 8))
        for col in range(4):
            buttons.columnconfigure(col, weight=1)
        self.call_btn = ttk.Button(
            buttons, text=self.S("call"), style=ACCENT_BUTTON              , command=self._on_call
        )
        self.hangup_btn = ttk.Button(
            buttons, text=self.S("hangup"), style=DANGER_BUTTON              , command=self._on_hangup
        )
        self.answer_btn = ttk.Button(
            buttons, text=self.S("answer"), style=ACCENT_BUTTON              , command=self._on_answer
        )
        self.reject_btn = ttk.Button(buttons, text=self.S("reject"), command=self._on_reject)
        order = [self.call_btn, self.hangup_btn, self.answer_btn, self.reject_btn]
        for col, btn in enumerate(order):
            btn.grid(row=0, column=col, sticky="ew", padx=2)

        audio_box = ttk.LabelFrame(middle, text=self.S("audio_group"))
        audio_box.grid(row=0, column=right_col, sticky="nsew", padx=4)
        audio_box.columnconfigure(field_col, weight=1)

        ttk.Label(audio_box, text=self.S("mic"), style=DIM    ).grid(
            row=0, column=label_col, sticky="w", padx=6, pady=3
        )
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(audio_box, textvariable=self.mic_var, state="readonly", width=30)
        self.mic_combo.grid(row=0, column=field_col, sticky="ew", padx=6, pady=3)
        self.mic_combo.bind("<<ComboboxSelected>>", self._on_mic_selected)

        ttk.Label(audio_box, text=self.S("speaker"), style=DIM    ).grid(
            row=1, column=label_col, sticky="w", padx=6, pady=3
        )
        self.out_var = tk.StringVar()
        self.out_combo = ttk.Combobox(audio_box, textvariable=self.out_var, state="readonly", width=30)
        self.out_combo.grid(row=1, column=field_col, sticky="ew", padx=6, pady=3)
        self.out_combo.bind("<<ComboboxSelected>>", self._on_out_selected)

        ttk.Button(audio_box, text=self.S("refresh_devices"), command=self._on_refresh).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2)
        )

        self.auto_pick_var = tk.BooleanVar(value=self.settings.auto_pick_new_device)
        ttk.Checkbutton(
            audio_box,
            text=self.S("auto_pick_new"),
            variable=self.auto_pick_var,
            command=self._on_auto_pick,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=6)

        self.auto_answer_var = tk.BooleanVar(value=self.settings.auto_answer)
        ttk.Checkbutton(
            audio_box,
            text=self.S("auto_answer"),
            variable=self.auto_answer_var,
            command=self._on_auto_answer,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6)

        ttk.Label(audio_box, text=self.S("mic_level"), style=DIM    ).grid(
            row=5, column=label_col, sticky="w", padx=6, pady=3
        )
        self.mic_meter = ttk.Progressbar(audio_box, maximum=100, style=METER      )
        self.mic_meter.grid(row=5, column=field_col, sticky="ew", padx=6, pady=3)

        ttk.Label(audio_box, text=self.S("volume"), style=DIM    ).grid(
            row=6, column=label_col, sticky="w", padx=6, pady=3
        )
        self.volume_var = tk.DoubleVar(value=self.settings.volume)
        ttk.Scale(
            audio_box, from_=0.0, to=2.0, variable=self.volume_var, command=self._on_volume
        ).grid(row=6, column=field_col, sticky="ew", padx=6, pady=3)

        ttk.Label(audio_box, text=self.S("mic_gain"), style=DIM    ).grid(
            row=7, column=label_col, sticky="w", padx=6, pady=3
        )
        self.gain_var = tk.DoubleVar(value=self.settings.mic_gain)
        ttk.Scale(
            audio_box, from_=0.0, to=4.0, variable=self.gain_var, command=self._on_gain
        ).grid(row=7, column=field_col, sticky="ew", padx=6, pady=3)

        toggles = ttk.Frame(audio_box)
        toggles.grid(row=8, column=0, columnspan=2, sticky="ew", padx=6, pady=(4, 8))
        self.mute_var = tk.BooleanVar(value=False)
        self.monitor_var = tk.BooleanVar(value=False)
        mute_check = ttk.Checkbutton(
            toggles, text=self.S("mute"), variable=self.mute_var, command=self._on_mute
        )
        monitor_check = ttk.Checkbutton(
            toggles, text=self.S("monitor"), variable=self.monitor_var, command=self._on_monitor
        )
        mute_col, monitor_col = 0, 1
        toggles.columnconfigure(mute_col, weight=0)
        toggles.columnconfigure(monitor_col, weight=1)
        mute_check.grid(row=0, column=mute_col, sticky="w")
        monitor_check.grid(row=0, column=monitor_col, sticky="w", padx=12)

        # -- log -----------------------------------------------------
        log_box = ttk.LabelFrame(self.container, text=self.S("log_group"))
        log_box.grid(row=3, column=0, sticky="nsew", **pad)
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_box, height=10, wrap="word", state="disabled")
        style_text           (self.log_text)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.tag_configure("line", justify="left")
        self.log_text.tag_configure("stamp", foreground=TEXT_DIM         )
        self.log_text.tag_configure("alert", foreground=WARN     )
        self.log_text.tag_configure("event", foreground=ACCENT       )

        footer = ttk.Frame(self.container)
        footer.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        footer.columnconfigure(0, weight=1)
        self.stats_label = ttk.Label(footer, text="", anchor="w", style=DIM    )
        self.stats_label.grid(row=0, column=0, sticky="ew")
        ttk.Label(footer, text=WATERMARK, style=WATERMARK_STYLE          ).grid(row=0, column=1, sticky="e")

        self._render_log()
        self._render_devices()
        self._render_peers()
        self._render_saved()
        self._update_state(self.phone.state)

    def _build_toolbar(self) -> None:
        """A strip of flat buttons with the app name across the middle.

        Deliberately not a native menu bar: Windows draws that one itself and
        ignores the colours, which would leave a pale band above a dark window.
        """
        bar = ttk.Frame(self.container, padding=(6, 6, 6, 2))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)

        left = ttk.Frame(bar)
        right = ttk.Frame(bar)
        left_col, right_col = 0, 2
        left.grid(row=0, column=left_col, sticky="w")
        right.grid(row=0, column=right_col, sticky="e")

        ttk.Button(
            left, text=self.S("settings"), style=TOOL_BUTTON            , command=self._open_settings
        ).pack(side="left", padx=(0, 4))

        ttk.Label(bar, text=APP_NAME.upper(), style=TITLE      , anchor="center").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(
            right, text=self.S("quit"), style=DANGER_BUTTON              , command=self._on_close
        ).pack(side="right")

    # ------------------------------------------------------------------
    # phone events
    # ------------------------------------------------------------------
    def _emit(self, kind: str, **data: Any) -> None:
        """Called from worker and audio threads - only queues."""
        self.events.put((kind, data))

    def _start_phone(self) -> None:
        try:
            self.phone.start()
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            self.root.destroy()
            return
        self.ip_label.configure(text=f"{self.S('my_ip')} {self.phone.local_ip}")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "log":
                key = data.pop("key", "")
                self._append_log(self.S(key, **data), tag=severity(key))
            elif kind == "state":
                self._update_state(data.get("state", IDLE))
            elif kind == "peers":
                self._peers = data.get("peers") or []
                self._render_peers()
            elif kind == "devices":
                self._render_devices()
            elif kind == "saved_peers":
                self.settings.save()
                self._render_saved()
            elif kind == "incoming":
                self._alert_incoming()

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _append_log(self, text: str, tag: str = "") -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log_lines.append((stamp, text, tag))
        del self._log_lines[:-MAX_LOG_LINES]
        self.log_text.configure(state="normal")
        self._insert_log_line(*self._log_lines[-1])
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _insert_log_line(self, stamp: str, text: str, tag: str) -> None:
        self.log_text.insert("end", f"{stamp}  ", ("line", "stamp"))
        self.log_text.insert("end", text + "\n", ("line", tag) if tag else "line")

    def _render_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for line in self._log_lines:
            self._insert_log_line(*line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _render_peers(self) -> None:
        selected = self._selected_peer_key()
        # A disabled Listbox ignores delete/insert, so re-enable it first.
        self.peer_list.configure(state="normal")
        self.peer_list.delete(0, "end")
        if not self._peers:
            self.peer_list.insert("end", self.S("no_peers"))
            self.peer_list.itemconfigure(0, foreground=ACCENT_SOFT            )
            self.peer_list.configure(state="disabled")
            return
        for index, peer in enumerate(self._peers):
            label = f"{peer['name']}  ({peer['ip']})"
            self.peer_list.insert("end", label)
            if peer["id"] == selected:
                self.peer_list.selection_set(index)

    def _selected_peer_key(self) -> str | None:
        try:
            picks = self.peer_list.curselection()
        except tk.TclError:
            return None
        if not picks or not self._peers:
            return None
        index = int(picks[0])
        if index >= len(self._peers):
            return None
        return self._peers[index]["id"]

    def _render_saved(self) -> None:
        """Fill the address dropdown from the saved list."""
        self._saved = list(self.settings.saved_peers)
        labels = []
        for peer in self._saved:
            name = peer.get("name")
            labels.append(f"{peer['ip']}  ({name})" if name else peer["ip"])
        self.ip_combo.configure(values=labels)
        self.forget_btn.configure(state="normal" if self._saved else "disabled")

    def _render_devices(self) -> None:
        self._input_devices = list(self.phone.inputs)
        self._output_devices = list(self.phone.outputs)
        self.mic_combo.configure(values=[d.label for d in self._input_devices])
        self.out_combo.configure(values=[d.label for d in self._output_devices])
        if self.phone.selected_input in self._input_devices:
            self.mic_var.set(self.phone.selected_input.label)
        else:
            self.mic_var.set("")
        if self.phone.selected_output in self._output_devices:
            self.out_var.set(self.phone.selected_output.label)
        else:
            self.out_var.set("")

    def _update_state(self, state: str) -> None:
        labels = {
            IDLE: "state_idle",
            CALLING: "state_calling",
            RINGING: "state_ringing",
            IN_CALL: "state_in_call",
        }
        text = self.S(labels.get(state, "state_idle"))
        peer = self.phone.peer_name or self.phone.peer_ip
        if state != IDLE and peer:
            text = f"{self.S(labels.get(state, 'state_idle'))} - {peer}"
        self.status_label.configure(
            text=f"{self.S('status')} {text}",
            foreground=TEXT      if state == IDLE else ACCENT       ,
        )

        def enable(widget: ttk.Button, on: bool) -> None:
            widget.configure(state="normal" if on else "disabled")

        enable(self.call_btn, state == IDLE)
        enable(self.hangup_btn, state in (CALLING, IN_CALL))
        enable(self.answer_btn, state == RINGING)
        enable(self.reject_btn, state == RINGING)

    def _alert_incoming(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self._topmost_until = time.monotonic() + 2.0
            self.root.bell()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # periodic tick
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if not self._alive:
            return
        self._drain_events()
        stats = self.phone.stats()
        self._in_peak = max(stats["in_level"], self._in_peak * 0.82)
        self._out_peak = max(stats["out_level"], self._out_peak * 0.82)
        self.mic_meter.configure(value=_level_percent(self._in_peak))
        rtt = self.S("rtt_unknown") if stats["rtt"] is None else f"{stats['rtt']:.0f} ms"
        self.stats_label.configure(
            text=self.S(
                "stats",
                sent=stats["sent"],
                recv=stats["recv"],
                lost=stats["lost"],
                depth=stats["depth"],
                rtt=rtt,
            )
        )
        if self._topmost_until and time.monotonic() > self._topmost_until:
            self._topmost_until = 0.0
            try:
                self.root.attributes("-topmost", False)
            except tk.TclError:
                pass
        self.root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------------
    # widget callbacks
    # ------------------------------------------------------------------
    def _on_name_changed(self, _event: Any = None) -> None:
        name = self.name_var.get().strip()
        if name and name != self.settings.display_name:
            self.settings.display_name = name
            self.settings.save()
            if self.phone.discovery is not None:
                self.phone.discovery.announce_now()

    def _on_peer_selected(self, _event: Any = None) -> None:
        key = self._selected_peer_key()
        for peer in self._peers:
            if peer["id"] == key:
                self.ip_var.set(peer["ip"])
                return

    def _on_saved_selected(self, _event: Any = None) -> None:
        """Selecting from the dropdown puts the bare address in the box."""
        index = self.ip_combo.current()
        if 0 <= index < len(self._saved):
            self.ip_var.set(self._saved[index]["ip"])

    def _on_forget(self) -> None:
        address = self.ip_var.get().strip()
        if not self.phone.forget_peer(address) and self._saved:
            # Nothing typed that matches: drop the most recent entry instead.
            self.phone.forget_peer(self._saved[0]["ip"])

    def _call_target(self) -> tuple[str, int] | None:
        key = self._selected_peer_key()
        typed = self.ip_var.get().strip()
        for peer in self._peers:
            if peer["id"] == key and (not typed or typed == peer["ip"]):
                return peer["ip"], peer["sig_port"]
        if not typed:
            return None
        for peer in self._peers:
            if peer["ip"] == typed:
                return peer["ip"], peer["sig_port"]
        # Not on the network right now: use the port saved with the address.
        return typed, self.settings.saved_port(typed, SIGNALING_PORT)

    def _on_call(self) -> None:
        if self.phone.state != IDLE:
            return
        target = self._call_target()
        if target is None:
            messagebox.showinfo(APP_NAME, self.S("err_no_target"))
            return
        host, port = target
        if not is_valid_host              (host):
            messagebox.showerror(APP_NAME, self.S("err_bad_ip", ip=host))
            return
        self.settings.save()
        self.phone.place_call(host, port)

    def _on_hangup(self) -> None:
        self.phone.hangup()

    def _on_answer(self) -> None:
        self.phone.answer()

    def _on_reject(self) -> None:
        self.phone.reject()

    def _on_refresh(self) -> None:
        self.phone.refresh_devices()

    def _on_mic_selected(self, _event: Any = None) -> None:
        index = self.mic_combo.current()
        if 0 <= index < len(self._input_devices):
            self.phone.select_input(self._input_devices[index])
            self.settings.save()

    def _on_out_selected(self, _event: Any = None) -> None:
        index = self.out_combo.current()
        if 0 <= index < len(self._output_devices):
            self.phone.select_output(self._output_devices[index])
            self.settings.save()

    def _on_auto_pick(self) -> None:
        self.settings.auto_pick_new_device = bool(self.auto_pick_var.get())
        self.settings.save()

    def _on_auto_answer(self) -> None:
        self.settings.auto_answer = bool(self.auto_answer_var.get())
        self.settings.save()

    def _on_mute(self) -> None:
        self.phone.set_mute(bool(self.mute_var.get()))

    def _on_monitor(self) -> None:
        self.phone.set_monitor(bool(self.monitor_var.get()))

    def _on_volume(self, _value: Any = None) -> None:
        self.phone.set_volume(self.volume_var.get())

    def _on_gain(self, _value: Any = None) -> None:
        self.phone.set_mic_gain(self.gain_var.get())

    # ------------------------------------------------------------------
    # settings dialog
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title(self.S("settings"))
        win.transient(self.root)
        win.resizable(False, False)
        win.configure(background=BG   )
        apply_window_chrome      (win, caption=BG   , text=TEXT     )
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        label_col, field_col = 0, 1
        frame.columnconfigure(field_col, weight=1)

        rate_var = tk.StringVar(value=str(self.settings.wire_rate))
        frame_var = tk.StringVar(value=str(self.settings.frame_ms))
        jitter_var = tk.StringVar(value=str(self.settings.jitter_ms))

        ttk.Label(frame, text=self.S("wire_rate"), style=DIM    ).grid(
            row=0, column=label_col, sticky="w", pady=4
        )
        ttk.Combobox(
            frame,
            textvariable=rate_var,
            values=[str(rate) for rate in SUPPORTED_RATES],
            state="readonly",
            width=10,
        ).grid(row=0, column=field_col, sticky="ew", padx=8)

        ttk.Label(frame, text=self.S("frame_ms"), style=DIM    ).grid(
            row=1, column=label_col, sticky="w", pady=4
        )
        ttk.Spinbox(frame, from_=10, to=40, increment=10, textvariable=frame_var, width=8).grid(
            row=1, column=field_col, sticky="ew", padx=8
        )

        ttk.Label(frame, text=self.S("jitter_ms"), style=DIM    ).grid(
            row=2, column=label_col, sticky="w", pady=4
        )
        ttk.Spinbox(frame, from_=20, to=400, increment=20, textvariable=jitter_var, width=8).grid(
            row=2, column=field_col, sticky="ew", padx=8
        )

        def save() -> None:
            try:
                self.settings.wire_rate = int(rate_var.get())
                self.settings.frame_ms = int(frame_var.get())
                self.settings.jitter_ms = int(jitter_var.get())
            except ValueError:
                pass
            self.settings.__post_init__()
            self.settings.save()
            self.phone.engine.configure_wire(
                self.settings.wire_rate, self.settings.frame_ms, self.settings.jitter_ms
            )
            win.destroy()
            self._append_log(self.S("log_settings_saved"))

        row = ttk.Frame(frame)
        row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(row, text=self.S("save"), style=ACCENT_BUTTON              , command=save).pack(
            side="left"
        )
        ttk.Button(row, text=self.S("cancel"), command=win.destroy).pack(
            side="left", padx=8
        )

    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        self._alive = False
        try:
            self.root.withdraw()  # shutting the sockets down takes a moment
        except tk.TclError:
            pass
        try:
            self.settings.save()
            self.phone.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _level_percent(rms: float) -> float:
    """Map an RMS level to a 0..100 meter on a decibel-ish scale."""
    if rms <= 1e-5:
        return 0.0
    db = 20.0 * math.log10(rms)
    return max(0.0, min(100.0, (db + 55.0) / 55.0 * 100.0))


def run_gui() -> int:
    app = PhoneApp()
    app.run()
    return 0

# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def _dialog(text: str, error: bool = False) -> None:
    """Show text in a message box, for when there is nowhere to print it."""
    try:
        import tkinter as tk
        from tkinter import messagebox


        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(APP_NAME, text)
        root.destroy()
    except Exception:  # noqa: BLE001 - nothing left to report with
        pass


def _report(text: str, error: bool = False) -> None:
    """Print, or pop up a dialog when there is no console.

    A windowed build (``LANPhone.exe``) has ``sys.stdout`` set to None, and
    printing to it would raise, so the text goes into a message box instead.
    """
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        try:
            print(text, file=stream)
            return
        except (OSError, ValueError):
            pass  # console went away mid-write; fall through to the dialog
    _dialog(text, error)


def _describe_devices() -> tuple[str, int]:

    try:
        inputs, outputs = list_devices             ()
    except AudioUnavailable                  as exc:
        return f"audio unavailable: {exc}", 2

    lines = ["inputs:"]
    lines += [
        f"  [{dev.index:>2}] {dev.label}  ({dev.channels} ch, {dev.samplerate:.0f} Hz)"
        for dev in inputs
    ] or ["  (none)"]
    lines.append("outputs:")
    lines += [
        f"  [{dev.index:>2}] {dev.label}  ({dev.channels} ch, {dev.samplerate:.0f} Hz)"
        for dev in outputs
    ] or ["  (none)"]
    if not inputs:
        lines.append("no microphone found - connect a headset and run again")
    if not outputs:
        lines.append("no audio output found - connect a headset and run again")
    return "\n".join(lines), 0 if (inputs and outputs) else 1


def _describe_network() -> str:

    ip = local_ip         ()
    return "\n".join(
        [
            f"local address : {ip}",
            f"broadcast     : {', '.join(broadcast_targets                  (ip))}",
            f"ports         : signalling TCP {SIGNALING_PORT}, "
            f"discovery UDP {DISCOVERY_PORT}, audio UDP {AUDIO_PORT}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lanphone", description=__doc__)
    parser.add_argument("--list-devices", action="store_true", help="show the audio devices and exit")
    parser.add_argument("--network", action="store_true", help="show local address and ports, then exit")
    args = parser.parse_args(argv)

    if args.list_devices:
        text, code = _describe_devices()
        _report(text, error=code == 2)
        return code
    if args.network:
        _report(_describe_network())
        return 0

    try:
        gui_main = run_gui
    except ImportError as exc:  # tkinter missing
        _report(
            f"cannot start the interface: {exc}\n"
            "On Windows use the python.org installer (it includes tkinter).",
            error=True,
        )
        return 2
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
