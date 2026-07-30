"""Application constants and persisted user settings."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields

APP_NAME = "LAN Phone"
PROTOCOL_VERSION = 1

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
    language: str = "he"
    rtl_fix: bool = True
    wire_rate: int = DEFAULT_WIRE_RATE
    frame_ms: int = DEFAULT_FRAME_MS
    jitter_ms: int = DEFAULT_JITTER_MS
    last_peer_ip: str = ""

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = default_display_name()
        if self.wire_rate not in SUPPORTED_RATES:
            self.wire_rate = DEFAULT_WIRE_RATE
        self.frame_ms = max(10, min(max_frame_ms(self.wire_rate), int(self.frame_ms)))
        self.jitter_ms = max(20, min(400, int(self.jitter_ms)))
        self.volume = max(0.0, min(2.0, float(self.volume)))
        self.mic_gain = max(0.0, min(4.0, float(self.mic_gain)))
        if self.language not in ("he", "en"):
            self.language = "he"

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
