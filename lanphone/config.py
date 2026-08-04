"""Application constants and persisted user settings."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields

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
