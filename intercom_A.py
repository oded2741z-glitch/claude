#!/usr/bin/env python3
"""Headless P2P intercom - computer A (signalling server + peer).

GENERATED FILE - do not edit. Built from intercom_core.py, txt_bridge.py, signalling.py, node.py
by build_single_file.py; edit those and rebuild.

    pip install sounddevice numpy      # Linux also: apt install libportaudio2
    python intercom_A.py

Control it by editing control_A.txt, which any other program may rewrite while
this is running; the live state is written back to status_A.txt.
"""

# ==========================================================================
# intercom_core.py
# ==========================================================================
"""Shared P2P intercom core: wire protocol, PortAudio guards and the peer loop.

Both nodes import this module. The GUI originals carried two hand-synced
copies of these constants; a mismatch produced garbled audio instead of an
error, so they now live in exactly one place.

Nothing here imports tkinter - the whole stack runs from a plain console.
"""

import contextlib
import json
import socket
import sys
import threading
import time
from typing import Callable, Dict, Optional, Tuple

try:
    import sounddevice as sd
    import numpy as np
except ImportError as exc:  # pragma: no cover - deployment aid on a bare box
    raise SystemExit(
        f"Missing dependency: {exc.name}\n"
        "Install with:  pip install sounddevice numpy\n"
        "PortAudio must also be present on the OS (Linux: apt install libportaudio2)."
    )

# --- Audio format (identical on both nodes or the audio comes out as noise) ---
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
CHUNK_SIZE: int = 512
DTYPE: str = 'int16'
BUFFER_SIZE: int = 65535          # UDP חותך בשקט חבילות גדולות מהבאפר
TIMEOUT_SECS: float = 3.0         # אין אודיו מהעמית -> סוגרים ומתחברים מחדש

# --- P2P wire protocol ---
# חבילות סיגנלינג הן JSON ומתחילות ב-'{'. חבילות P2P מתויגות בבייט ראשון,
# כדי שלעולם לא ננגן הודעת בקרה כרעש.
AUDIO_TAG: bytes = b'\x01'
PUNCH_TAG: bytes = b'\x02'
PUNCH_PACKET: bytes = PUNCH_TAG + b'PUNCH'
P2P_TAGS: Tuple[bytes, bytes] = (AUDIO_TAG, PUNCH_TAG)

PUNCH_DURATION: float = 2.0
PUNCH_INTERVAL: float = 0.1
HW_REFRESH_GAP: float = 5.0       # מרווח מזערי בין אתחולי PortAudio
STREAM_CLOSE_WAIT: float = 5.0    # סגירת stream על התקן שנשלף עלולה להיתקע
DEVICE_POLL: float = 2.0
PEER_IDLE: float = 1.5            # מעבר לזה נחשב "אין אודיו מהעמית"

# Peer states reported to the TXT bridge
STATE_IDLE: str = "idle"
STATE_WAITING: str = "waiting"
STATE_PUNCHING: str = "punching"
STATE_LIVE: str = "live"


_LOG_LOCK: threading.Lock = threading.Lock()


def log(msg: str) -> None:
    """Single logging path for every process: timestamped line on stdout.

    Written as one call under a lock - `print` emits the text and the newline
    separately, so lines from the audio, socket and server threads interleave.
    """
    line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
    with _LOG_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def parse_json_dict(data: bytes) -> Optional[Dict]:
    """מפענח הודעת סיגנלינג. מחזיר None לכל דבר שאינו JSON dict תקין."""
    try:
        msg = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None  # חבילת אודיו / PUNCH / זבל
    return msg if isinstance(msg, dict) else None


class AudioGuard:
    """Process-wide PortAudio serialization.

    PortAudio is global state, so this is a singleton (`AUDIO` below) even when
    several objects use audio. Re-initializing the bindings while a stream is
    open kills the process at the C level with no traceback, so every re-init
    goes through `refresh()` and every stream through `counted_stream()`.
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._open_streams: int = 0     # מוגן ע"י _lock
        self._last_refresh: float = 0.0

    def refresh(self) -> None:
        """Restarts the PortAudio bindings. Never runs while a stream is open."""
        with self._lock:
            # המונה מוחזק על פני כל חיי ה-stream, כולל הסגירה (ראה counted_stream)
            if self._open_streams > 0:
                return
            # אתחול גלובלי חוזר בזמן שההתקן מתחבר/מתנתק הוא בעצמו גורם קריסה
            now = time.time()
            if now - self._last_refresh < HW_REFRESH_GAP:
                return
            self._last_refresh = now
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass

    @contextlib.contextmanager
    def counted_stream(self, factory: Callable):
        """Opens a stream while holding the open-stream counter for its whole lifetime.

        The counter must cover the close as well: closing a stream on a device
        that was just unplugged can block, and a refresh during that window
        would terminate PortAudio underneath a live stream.
        """
        with self._lock:
            self._open_streams += 1
        try:
            with factory() as stream:
                yield stream
        finally:
            with self._lock:
                self._open_streams -= 1

    def devices_ready(self) -> bool:
        """True when both a microphone and a speaker can serve our format.

        שאילתה על ההתקנים במקום לפתוח ולסגור streams אמיתיים - כל מחזור
        פתיחה/סגירה על התקן מתנתק הוא סיכון מיותר.
        """
        try:
            sd.check_output_settings(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE)
            sd.check_input_settings(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE)
            return True
        except Exception:
            return False

    def wait_for_devices(self, stop_event: threading.Event) -> bool:
        """Blocks until mic+speaker are usable. False if we were told to stop."""
        if self.devices_ready():
            return True
        log("Waiting for audio devices (mic + speaker) to be connected...")
        while not stop_event.is_set():
            self.refresh()  # ההתקן אולי בדיוק חובר ועדיין לא מזוהה
            if stop_event.wait(DEVICE_POLL):
                return False
            if self.devices_ready():
                log("Audio devices (mic + speaker) detected.")
                return True
        return False


AUDIO: AudioGuard = AudioGuard()


class IntercomPeer:
    """One side of the call: registers with the signalling server, punches the
    NAT, then streams audio straight to the other peer.

    A single UDP socket carries both the signalling JSON and the audio. This is
    deliberate - the NAT mapping created while registering is the very mapping
    the peer punches into. Never split these onto two sockets.
    """

    def __init__(self, my_id: str, server_addr: Tuple[str, int]) -> None:
        self.my_id: str = my_id
        self.server_addr: Tuple[str, int] = server_addr

        self.sock: Optional[socket.socket] = None
        self.is_running: bool = False
        self._stop_lock: threading.Lock = threading.Lock()

        self.rx_thread: Optional[threading.Thread] = None
        self.tx_thread: Optional[threading.Thread] = None

        # Peer address handling
        self.peer_lock: threading.Lock = threading.Lock()
        self.assigned_peer: Optional[Tuple[str, int]] = None
        self.locked_peer: Optional[Tuple[str, int]] = None
        self.tx_peer: Optional[Tuple[str, int]] = None
        self.peer_id: str = ""

        # Live state, read by the status writer from another thread
        self.state: str = STATE_IDLE
        self.call_started: float = 0.0
        self.last_rx: float = 0.0

    # ------------------------------------------------------------------
    # Signalling
    # ------------------------------------------------------------------
    def _await_peer(self, stop_event: threading.Event) -> Optional[Tuple[str, int]]:
        """ממתין לפרטי העמית. מתעלם מכל חבילה שאינה תשובת סיגנלינג תקינה."""
        msg: bytes = json.dumps({"id": self.my_id}).encode('utf-8')
        while self.is_running and not stop_event.is_set() and self.sock:
            try:
                self.sock.sendto(msg, self.server_addr)
            except (AttributeError, OSError):
                return None

            try:
                data, _ = self.sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue  # הסוקט ב-timeout של שנייה, אז זה גם קצב הרישום מחדש
            except ConnectionResetError:
                time.sleep(1)
                continue
            except (AttributeError, OSError):
                return None

            # חבילת אודיו מעמית שהקדים אותנו לא מפילה את הרישום
            info = parse_json_dict(data)
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

    # ------------------------------------------------------------------
    # Main call
    # ------------------------------------------------------------------
    def run(self, stop_event: threading.Event) -> None:
        """Blocking: one full attempt at a call. Returns when the call ends."""
        with self._stop_lock:
            self.is_running = True
        self.state = STATE_WAITING
        self.peer_id = ""
        self.call_started = 0.0
        self.last_rx = 0.0

        AUDIO.refresh()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None

        try:
            log(f"[{self.my_id}] Waiting for matching peer from "
                f"{self.server_addr[0]}:{self.server_addr[1]}...")

            peer_addr = self._await_peer(stop_event)
            if not self.is_running or stop_event.is_set() or not peer_addr:
                return

            with self.peer_lock:
                self.assigned_peer = peer_addr
                self.tx_peer = peer_addr

            who = f" ({self.peer_id})" if self.peer_id else ""
            log(f"Peer Target Assigned{who} -> IP: {peer_addr[0]}, Port: {peer_addr[1]}")
            log("Sending UDP Hole Punch packets...")
            self.state = STATE_PUNCHING

            punch_until = time.time() + PUNCH_DURATION
            while self.is_running and not stop_event.is_set() and time.time() < punch_until:
                try:
                    self.sock.sendto(PUNCH_PACKET, peer_addr)
                except (AttributeError, OSError):
                    return
                time.sleep(PUNCH_INTERVAL)

            if not self.is_running or stop_event.is_set():
                return

            log("NAT hole punched! Live audio streaming active.")
            self.state = STATE_LIVE
            self.call_started = time.time()

            self.rx_thread = threading.Thread(target=self._audio_receiver, daemon=True)
            self.tx_thread = threading.Thread(target=self._audio_sender, daemon=True)
            self.rx_thread.start()
            self.tx_thread.start()

            # יציאה כשאחד התרדים מת - למשל אוזניות שנשלפו
            while (self.is_running and not stop_event.is_set()
                   and self.rx_thread.is_alive() and self.tx_thread.is_alive()):
                time.sleep(0.2)

            if self.is_running and not stop_event.is_set():
                log("Audio stream interrupted by hardware state change. Reconnecting...")

        except Exception as e:
            log(f"Connection or hardware error: {e}")
            time.sleep(2)
        finally:
            self.stop()

    # ------------------------------------------------------------------
    # Audio streaming
    # ------------------------------------------------------------------
    def _peer_audio(self, addr: Tuple[str, int], data: bytes) -> Optional[bytes]:
        """מאמת שהחבילה הגיעה מהעמית ומחזיר payload רק לחבילות אודיו."""
        tag, payload = data[:1], data[1:]
        if tag not in P2P_TAGS:
            return None  # JSON מהשרת או זבל - לעולם לא מתנגן

        with self.peer_lock:
            if self.locked_peer is None:
                # ננעלים על הכתובת של החבילה המתויגת הראשונה, גם אם ה-NAT
                # הקצה פורט שונה מזה שהשרת דיווח
                self.locked_peer = addr
                self.tx_peer = addr
                if addr != self.assigned_peer:
                    log(f"Peer reached us from {addr[0]}:{addr[1]} "
                        f"(assigned {self.assigned_peer}). Locking on.")
            elif addr != self.locked_peer:
                return None  # מונע הזרקת אודיו מכל מקור אחר ברשת

        if tag != AUDIO_TAG or not payload:
            return None
        if len(payload) % (2 * CHANNELS) != 0:
            return None  # חבילה קטומה
        return payload

    def _audio_receiver(self) -> None:
        """Receives P2P audio and plays it."""
        last_data_time: float = time.time()
        try:
            with AUDIO.counted_stream(
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
                            log(f"Peer disconnected ({TIMEOUT_SECS:.0f} seconds timeout).")
                            self.is_running = False
                            break
                        continue
                    except ConnectionResetError:
                        log("Peer disconnected unexpectedly.")
                        self.is_running = False
                        break
                    except (AttributeError, OSError):
                        break  # הסוקט נסגר - יציאה נקייה

                    last_data_time = time.time()
                    payload = self._peer_audio(addr, data)
                    if payload is None:
                        continue
                    self.last_rx = last_data_time
                    stream.write(np.frombuffer(payload, dtype=np.int16).reshape(-1, CHANNELS))
        except Exception:
            if self.is_running:
                log("Output device error (speaker/headphones disconnected).")

    def _audio_sender(self) -> None:
        """Captures the microphone and transmits it to the peer."""
        try:
            with AUDIO.counted_stream(
                    lambda: sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                           dtype=DTYPE, blocksize=CHUNK_SIZE)) as stream:
                while self.is_running:
                    data, _ = stream.read(CHUNK_SIZE)
                    sock, peer = self.sock, self.tx_peer
                    if sock is None or peer is None:
                        break
                    try:
                        sock.sendto(AUDIO_TAG + data.tobytes(), peer)
                    except ConnectionResetError:
                        continue
                    except (AttributeError, OSError):
                        break  # הסוקט נסגר - יציאה נקייה
        except Exception:
            if self.is_running:
                log("Input device disconnected.")

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Safely shuts the socket down and resets state. Idempotent."""
        with self._stop_lock:
            was_connected = self.is_running
            self.is_running = False

        # דיווח ניתוק לשרת לפני שסוגרים, כדי שישחרר את הסלוט מיד
        if self.sock and self.my_id:
            try:
                self.sock.sendto(
                    json.dumps({"id": self.my_id, "status": "disconnected"}).encode('utf-8'),
                    self.server_addr)
            except OSError:
                pass

        # ממתינים לתרדי האודיו לפני סגירת הסוקט. סגירה מתחת לרגליהם משאירה
        # את כרטיס הקול תפוס ומייצרת חריגות מטעות. סגירת stream על התקן
        # שנשלף עלולה להיתקע, ולכן ההמתנה ארוכה יחסית: התרד עדיין מחזיק את
        # מונה ה-streams, אז רענון חומרה ייחסם עד שיסיים.
        current = threading.current_thread()
        for t in (self.rx_thread, self.tx_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=STREAM_CLOSE_WAIT)
                if t.is_alive():
                    log("Audio thread still closing the device; will not touch PortAudio.")
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
        self.peer_id = ""
        self.state = STATE_IDLE
        self.call_started = 0.0
        self.last_rx = 0.0

        if was_connected:
            log("Intercom connection closed.")

    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, str]:
        """Flat, printable view of the peer for the status file."""
        now = time.time()
        with self.peer_lock:
            locked, assigned = self.locked_peer, self.assigned_peer
        state = self.state
        if state == STATE_LIVE and (locked is None or now - self.last_rx > PEER_IDLE):
            state = "no-audio"
        addr = locked or assigned
        return {
            "state": state,
            "peer_id": self.peer_id,
            "peer_addr": f"{addr[0]}:{addr[1]}" if addr else "",
            "call_seconds": f"{int(now - self.call_started)}" if self.call_started else "0",
            "rx_age": f"{now - self.last_rx:.1f}" if self.last_rx else "",
        }

# ==========================================================================
# txt_bridge.py
# ==========================================================================
"""The TXT bridge: how an external program drives a node and reads its state.

Two plain text files, no GUI and no sockets involved:

  control.txt  - written by the other program, polled by the node (commands in)
  status.txt   - written by the node, read by the other program (state out)

Both use `key = value` lines, and the control file also accepts the JSON
objects the old GUI wrote (`settings_A.txt` / `settings.txt`), so existing
files keep working unchanged.

Partial reads are expected: the other program may be halfway through writing
when we poll. A file that fails to parse is ignored and re-read on the next
tick, and the last good configuration stays in force. Our own writes are
atomic (temp file + os.replace) so the other program never sees a half file.
"""

import json
import os
import tempfile
import time
from typing import Dict, Optional, Tuple


TRUE_WORDS = {"1", "on", "true", "yes", "y", "start", "up", "enable", "enabled"}
FALSE_WORDS = {"0", "off", "false", "no", "n", "stop", "down", "disable", "disabled"}

# מילים נרדפות: הקבצים הישנים של ה-GUI השתמשו ב-"ip" ל-כתובת השרת
ALIASES: Dict[str, str] = {
    "ip": "server_ip",
    "host": "server_ip",
    "server": "server_ip",
    "id": "my_id",
    "name": "my_id",
    "external_ip": "ext_ip",
    "local": "local_mode",
    "call": "intercom",
}


def as_bool(value: str, default: bool = False) -> bool:
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return default


def as_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_config_text(text: str) -> Optional[Dict[str, str]]:
    """Parses JSON or `key = value` text. None means 'unusable, try again later'."""
    text = text.strip()
    if not text:
        return None

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None  # כתיבה חלקית של הקובץ
        if not isinstance(data, dict):
            return None
        raw = {str(k): str(v) for k, v in data.items()}
    else:
        raw = {}
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            for sep in ("=", ":"):
                if sep in line:
                    key, value = line.split(sep, 1)
                    raw[key.strip()] = value.strip()
                    break

    if not raw:
        return None
    return {ALIASES.get(k.strip().lower(), k.strip().lower()): v for k, v in raw.items()}


class ControlFile:
    """Polls the control file and reports the configuration when it changes."""

    def __init__(self, path: str) -> None:
        self.path: str = path
        self._signature: Optional[Tuple[float, int]] = None
        self._config: Dict[str, str] = {}
        self._missing_logged: bool = False

    @property
    def config(self) -> Dict[str, str]:
        return dict(self._config)

    def ensure_template(self, defaults: Dict[str, str]) -> None:
        """Writes a commented template so the other program sees the format."""
        if os.path.exists(self.path):
            return
        lines = [
            "# Intercom control file - edit from any program, changes apply live.",
            "# intercom = on | off      open or close the call",
            "# command  = quit          shut the node down",
            "",
        ]
        lines += [f"{k} = {v}" for k, v in defaults.items()]
        try:
            write_text_atomic(self.path, "\n".join(lines) + "\n")
            log(f"Created control file: {self.path}")
        except OSError as e:
            log(f"Could not create {self.path}: {e}")

    def poll(self) -> Optional[Dict[str, str]]:
        """Returns the new configuration if it changed, otherwise None."""
        try:
            stat = os.stat(self.path)
        except OSError:
            if not self._missing_logged:
                self._missing_logged = True
                log(f"Control file {self.path} is missing; keeping current settings.")
            return None
        self._missing_logged = False

        signature = (stat.st_mtime, stat.st_size)
        if signature == self._signature:
            return None

        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return None  # ננסה שוב בסבב הבא

        config = parse_config_text(text)
        if config is None:
            # לא שומרים חתימה: קריאה חלקית תיקרא שוב בסבב הבא
            return None

        self._signature = signature
        if config == self._config:
            return None
        self._config = config
        return dict(config)


def write_text_atomic(path: str, text: str) -> None:
    """Writes via a temp file in the same directory, then replaces in one step."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with_suppressed_unlink(tmp)
        raise


def with_suppressed_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class StatusFile:
    """Writes the node state out, skipping writes that would change nothing."""

    def __init__(self, path: str) -> None:
        self.path: str = path
        self._last: str = ""
        self._error_logged: bool = False

    def write(self, fields: Dict[str, str]) -> None:
        body = "\n".join(f"{k} = {v}" for k, v in fields.items())
        if body == self._last:
            return  # לא שוחקים את הדיסק כשאין שינוי
        stamped = f"updated = {time.strftime('%Y-%m-%d %H:%M:%S')}\n{body}\n"
        try:
            write_text_atomic(self.path, stamped)
            self._last = body
            self._error_logged = False
        except OSError as e:
            if not self._error_logged:
                self._error_logged = True
                log(f"Could not write {self.path}: {e}")

# ==========================================================================
# signalling.py
# ==========================================================================
"""Headless UDP signalling server: registers peers, matches two, steps aside.

Audio never passes through here. The server hands each side the other's
(ip, port) and is out of the media path from that moment on.
"""

import ipaddress
import json
import socket
import threading
import time
from typing import Dict, Optional, Tuple


CLIENT_TTL: float = 30.0          # רישום ישן לא ישמש להצמדה
MATCH_RETRIES: int = 5            # UDP לא אמין - שולחים את פרטי העמית כמה פעמים
MATCH_RETRY_DELAY: float = 0.05
BIND_WAIT: float = 3.0
JOIN_WAIT: float = 2.5


class SignallingServer:
    def __init__(self, port: int, ext_ip: str = "", local_mode: bool = False,
                 local_id: str = "") -> None:
        self.port: int = port
        self.ext_ip: str = ext_ip          # ניתן לעדכון חי מקובץ הבקרה
        self.local_mode: bool = local_mode
        self.local_id: str = local_id      # הרישום שלנו לא נספר כ"לקוח מרוחק"

        self.error: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._bound: threading.Event = threading.Event()
        self._rewrite_warned: bool = False

        # מצב אוזניות של לקוחות מרוחקים בלבד: id -> (connected, since)
        self._lock: threading.Lock = threading.Lock()
        self._remote_clients: Dict[str, Tuple[bool, float]] = {}

    # ------------------------------------------------------------------
    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Starts the listener thread and waits for the bind result."""
        if self.is_alive:
            return True
        self.error = ""
        self._rewrite_warned = False
        self._stop_event.clear()
        self._bound.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._bound.wait(BIND_WAIT)
        return self.is_alive and not self.error

    def stop(self) -> None:
        if not self.is_alive:
            self._thread = None
            return
        self._stop_event.set()
        # לסוקט השרת יש timeout של שנייה - נותנים לו מספיק זמן לשחרר את הפורט
        self._thread.join(timeout=JOIN_WAIT)
        self._thread = None
        log("[Server] Internal signalling server STOPPED.")

    def remote_clients(self) -> Dict[str, Tuple[bool, float]]:
        with self._lock:
            return dict(self._remote_clients)

    # ------------------------------------------------------------------
    def _visible_ip(self, ip: str) -> str:
        """בוחר את הכתובת שתדווח לעמית. מכסה את כל הטווחים הפרטיים, לא רק 192.168."""
        if self.local_mode or not self.ext_ip:
            return ip
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if not (parsed.is_private or parsed.is_loopback):
            return ip
        if not self._rewrite_warned:
            self._rewrite_warned = True
            log("[Server] Rewriting private IPs to External IP. Note: the PORT is "
                "not translated, so this needs a static port forward.")
        return self.ext_ip

    def _mark_headphones(self, client_id: str, connected: bool) -> bool:
        """Records a REMOTE client's headphone state. True on a real change."""
        if not client_id or client_id == self.local_id:
            return False  # המצב שלנו לא מעניין - השורה נועדה להראות את הצד השני
        with self._lock:
            previous = self._remote_clients.get(client_id)
            if previous is not None and previous[0] == connected:
                return False  # אותו מצב - לא מאפסים את חותמת הזמן
            self._remote_clients[client_id] = (connected, time.time())
        return True

    def _purge_stale(self, clients: Dict[str, Tuple[Tuple[str, int], float]], now: float) -> None:
        for cid in [c for c, (_, ts) in clients.items() if now - ts > CLIENT_TTL]:
            del clients[cid]
            log(f"[Server] {cid}: registration expired.")

    def _handle_signalling(self, server_sock: socket.socket,
                           clients: Dict[str, Tuple[Tuple[str, int], float]],
                           data: bytes, addr: Tuple[str, int]) -> None:
        msg = parse_json_dict(data)
        if msg is None:
            return

        client_id = msg.get("id")
        if not isinstance(client_id, str) or not client_id.strip():
            return
        client_id = client_id.strip()

        # קבלת דיווח ניתוק מהלקוח. חייב להירשם גם אם הלקוח כבר לא ב-clients:
        # אחרי הצמדה הרשימה מתאפסת, וזה בדיוק המקרה של ניתוק באמצע שיחה.
        if msg.get("status") == "disconnected":
            clients.pop(client_id, None)
            if self._mark_headphones(client_id, False):
                log(f"[Server] {client_id}: Headphones disconnected !!!")
            return

        now = time.time()
        self._purge_stale(clients, now)

        # דיווח התחברות מהלקוח. הרישום שלנו עצמנו לא מדווח כאוזניות
        if self._mark_headphones(client_id, True):
            log(f"[Server] {client_id}: Headphones connected !!!")
        elif client_id not in clients:
            log(f"[Server] {client_id}: registered.")
        clients[client_id] = (addr, now)

        if len(clients) == 2:
            (peer1_id, (addr1, _)), (peer2_id, (addr2, _)) = clients.items()
            log(f"[Server] Matched {peer1_id} <---> {peer2_id}.")

            ip1 = self._visible_ip(addr1[0])
            ip2 = self._visible_ip(addr2[0])

            reply1 = json.dumps({"peer_ip": ip2, "peer_port": addr2[1], "peer_id": peer2_id}).encode('utf-8')
            reply2 = json.dumps({"peer_ip": ip1, "peer_port": addr1[1], "peer_id": peer1_id}).encode('utf-8')
            for _ in range(MATCH_RETRIES):
                server_sock.sendto(reply1, addr1)
                server_sock.sendto(reply2, addr2)
                time.sleep(MATCH_RETRY_DELAY)

            clients.clear()

    def _run(self) -> None:
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server_sock.bind(('0.0.0.0', self.port))
            server_sock.settimeout(1.0)
        except OSError as e:
            self.error = f"bind failed on port {self.port}: {e.strerror or e}"
            log(f"[Server] Bind Error: {e}")
            self._bound.set()
            return

        log(f"[Server] Internal signalling server STARTED on port {self.port}.")
        self._bound.set()
        clients: Dict[str, Tuple[Tuple[str, int], float]] = {}

        with server_sock:
            while not self._stop_event.is_set():
                try:
                    data, addr = server_sock.recvfrom(BUFFER_SIZE)
                except socket.timeout:
                    self._purge_stale(clients, time.time())
                    continue
                except ConnectionResetError:
                    continue  # Windows WinError 10054
                except OSError as e:
                    log(f"[Server] Socket error: {e}")
                    break

                # חבילה פגומה אחת לא מפילה את השרת
                try:
                    self._handle_signalling(server_sock, clients, data, addr)
                except Exception as e:
                    log(f"[Server] Error handling packet: {e}")

# ==========================================================================
# node.py
# ==========================================================================
"""Headless intercom node. One file, two roles, no GUI.

    Computer A (headphones, hosts the signalling server):
        python node.py --role a

    Computer B (speakers + microphone, client only):
        python node.py --role b --server-ip <A's address>

Everything the old GUI exposed as a button is now a line in a TXT file that
any other program can rewrite while the node is running (see txt_bridge.py):

    intercom = off      closes the call        intercom = on     opens it
    command  = quit     shuts the node down

The node writes its own state back to the status file every tick, so the
other program can tell whether the far side is connected without a screen.
"""

import argparse
import os
import signal
import socket
import threading
import time
from typing import Dict, Optional


POLL_INTERVAL: float = 0.5        # קצב קריאת קובץ הבקרה
RECONNECT_DELAY: float = 1.0      # השהיה לפני ניסיון חיבור חוזר
DEVICE_NOTICE_GAP: float = 30.0   # לא מציפים את הלוג בהודעת "אין התקנים"

ROLE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "a": {
        "my_id": "node_A",
        "server_ip": "127.0.0.1",   # השרת רץ על המחשב הזה
        "port": "9999",
        "ext_ip": "",
        "local_mode": "on",
        "signalling": "on",
        "intercom": "on",
    },
    "b": {
        "my_id": "node_B",
        "server_ip": "192.168.1.11",
        "port": "9999",
        "ext_ip": "",
        "local_mode": "on",
        "signalling": "off",
        "intercom": "on",
    },
}

# הקבצים שגרסת ה-GUI כתבה - נמשיך לקרוא אותם אם הם כבר קיימים
LEGACY_CONTROL: Dict[str, str] = {"a": "settings_A.txt", "b": "settings.txt"}
DEFAULT_CONTROL: Dict[str, str] = {"a": "control_A.txt", "b": "control_B.txt"}
DEFAULT_STATUS: Dict[str, str] = {"a": "status_A.txt", "b": "status_B.txt"}


class IntercomNode:
    def __init__(self, role: str, control_path: str, status_path: str,
                 defaults: Dict[str, str]) -> None:
        self.role: str = role
        self.cfg: Dict[str, str] = dict(defaults)
        self.control: ControlFile = ControlFile(control_path)
        self.status: StatusFile = StatusFile(status_path) if status_path else None

        self.stop_event: threading.Event = threading.Event()
        self.server: Optional[SignallingServer] = None

        self.peer: Optional[IntercomPeer] = None
        self.peer_thread: Optional[threading.Thread] = None
        self.call_stop: threading.Event = threading.Event()
        self._next_attempt: float = 0.0
        self._device_notice: float = 0.0
        self._loopback_warned: bool = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def _want(self, key: str) -> bool:
        return as_bool(self.cfg.get(key, ""), default=False)

    def _server_addr(self):
        return (self.cfg.get("server_ip", "127.0.0.1").strip(),
                as_int(self.cfg.get("port", "9999"), 9999))

    def _apply(self, changes: Dict[str, str]) -> None:
        """Applies a fresh control file. Only restarts what actually changed."""
        previous = dict(self.cfg)
        self.cfg.update(changes)

        if str(self.cfg.pop("command", "")).strip().lower() in ("quit", "exit", "shutdown"):
            log("Control file requested shutdown.")
            self.stop_event.set()
            return

        changed = [k for k in self.cfg if self.cfg.get(k) != previous.get(k)]
        if changed:
            log("Control file changed: " + ", ".join(f"{k}={self.cfg[k]}" for k in sorted(changed)))

        # ext_ip / local_mode משפיעים על השרת מיידית, בלי אתחול
        if self.server is not None:
            self.server.ext_ip = self.cfg.get("ext_ip", "").strip()
            self.server.local_mode = self._want("local_mode")
            self.server.local_id = self.cfg.get("my_id", "").strip()

        port_changed = self.cfg.get("port") != previous.get("port")
        if self.role == "a" and (port_changed or self.cfg.get("signalling") != previous.get("signalling")):
            self._sync_server(restart=port_changed)

        # שינוי זהות או יעד מחייב לפתוח את השיחה מחדש
        if (self.cfg.get("my_id") != previous.get("my_id")
                or self.cfg.get("server_ip") != previous.get("server_ip")
                or port_changed):
            self._end_call("settings changed")

        if not self._want("intercom"):
            self._end_call("intercom turned off")

        self._warn_loopback()

    def _warn_loopback(self) -> None:
        """Registering over loopback hands the far side an address it cannot use."""
        if self.role != "a":
            return  # תפקיד B מול 127.0.0.1 הוא בדיקה לגיטימית על מחשב יחיד
        host = self._server_addr()[0]
        rewriting = not self._want("local_mode") and bool(self.cfg.get("ext_ip", "").strip())
        if not host.startswith("127.") or rewriting:
            self._loopback_warned = False
            return
        if self._loopback_warned:
            return
        self._loopback_warned = True
        log(f"WARNING: registering over {host}. The other computer will be handed that "
            f"address and will not reach us. Set 'server_ip' to this machine's LAN "
            f"address (detected: {detect_local_ip()}) in {self.control.path}.")

    # ------------------------------------------------------------------
    # Signalling server (role A only)
    # ------------------------------------------------------------------
    def _sync_server(self, restart: bool = False) -> None:
        if self.role != "a":
            return
        want = self._want("signalling")
        port = as_int(self.cfg.get("port", "9999"), 9999)

        if self.server is not None and (restart or not want):
            self.server.stop()
            self.server = None
        if not want:
            return
        if self.server is None:
            self.server = SignallingServer(port=port,
                                           ext_ip=self.cfg.get("ext_ip", "").strip(),
                                           local_mode=self._want("local_mode"),
                                           local_id=self.cfg.get("my_id", "").strip())
            if not self.server.start():
                log(f"[Server] Could not start: {self.server.error}")

    # ------------------------------------------------------------------
    # The call
    # ------------------------------------------------------------------
    def _call_active(self) -> bool:
        return self.peer_thread is not None and self.peer_thread.is_alive()

    def _end_call(self, reason: str) -> None:
        if self.peer is None and self.peer_thread is None:
            return
        log(f"Closing intercom ({reason}).")
        self.call_stop.set()
        if self.peer is not None:
            self.peer.stop()
        if self.peer_thread is not None and self.peer_thread.is_alive():
            self.peer_thread.join(timeout=10.0)
        self.peer_thread = None
        self.peer = None
        self._next_attempt = time.time() + RECONNECT_DELAY

    def _supervise_call(self) -> None:
        """Keeps exactly one call attempt alive whenever the bridge asks for it."""
        if not self._want("intercom") or self.stop_event.is_set():
            if self._call_active():
                self._end_call("intercom turned off")
            return

        if self._call_active():
            return
        if self.peer_thread is not None:      # התרד סיים - ניקוי לפני ניסיון חדש
            self.peer_thread = None
            self.peer = None
            self._next_attempt = time.time() + RECONNECT_DELAY

        if time.time() < self._next_attempt:
            return

        # בדיקה לא חוסמת: קובץ הבקרה חייב להישאר מגיב גם בלי אוזניות
        if not AUDIO.devices_ready():
            AUDIO.refresh()               # אולי ההתקן בדיוק חובר ועדיין לא מזוהה
            now = time.time()
            if now - self._device_notice > DEVICE_NOTICE_GAP:
                self._device_notice = now
                log("Waiting for audio devices (mic + speaker) to be connected...")
            self._next_attempt = time.time() + 2.0
            return

        my_id = self.cfg.get("my_id", "").strip()
        if not my_id:
            log("my_id is empty in the control file; cannot register.")
            self._next_attempt = time.time() + 5.0
            return

        self.call_stop = threading.Event()
        self.peer = IntercomPeer(my_id, self._server_addr())
        self.peer_thread = threading.Thread(target=self.peer.run, args=(self.call_stop,),
                                            daemon=True)
        self.peer_thread.start()

    # ------------------------------------------------------------------
    # Status out
    # ------------------------------------------------------------------
    def _write_status(self) -> None:
        if self.status is None:
            return
        fields: Dict[str, str] = {
            "node": self.cfg.get("my_id", ""),
            "role": self.role,
            "intercom": "on" if self._want("intercom") else "off",
            "server_addr": "{}:{}".format(*self._server_addr()),
        }

        # הקבוצה קבועה: תוכנה שקוראת את הקובץ לא צריכה להתמודד עם מפתח שנעלם
        if self.role == "a":
            if self.server is None:
                fields["signalling"] = "off"
            elif self.server.is_alive:
                fields["signalling"] = "up"
            else:
                fields["signalling"] = "error"
            fields["signalling_error"] = self.server.error if self.server else ""

        if self.peer is not None and self._call_active():
            fields.update(self.peer.snapshot())
        else:
            fields.update({"state": "idle", "peer_id": "", "peer_addr": "",
                           "call_seconds": "0", "rx_age": ""})

        # מצב הצד השני כפי שהשרת רואה אותו - זה הרמז שאפשר לפתוח שיחה
        if self.role == "a":
            remote = self.server.remote_clients() if self.server else {}
            fields["clients"] = ", ".join(
                f"{cid}:{'connected' if state else 'disconnected'}"
                for cid, (state, _) in sorted(remote.items(), key=lambda kv: kv[1][1], reverse=True))
            fields["remote_ready"] = "yes" if any(s for s, _ in remote.values()) else "no"

        self.status.write(fields)

    # ------------------------------------------------------------------
    def run(self) -> None:
        self.control.ensure_template(self.cfg)
        first = self.control.poll()
        if first:
            self.cfg.update(first)

        log(f"Starting headless node [{self.cfg.get('my_id')}] role={self.role.upper()} "
            f"-> signalling {self._server_addr()[0]}:{self._server_addr()[1]}")
        log(f"Control file: {self.control.path}"
            + (f" | Status file: {self.status.path}" if self.status else ""))
        log("Press Ctrl+C to exit.")

        self._warn_loopback()
        self._sync_server()

        while not self.stop_event.is_set():
            try:
                changes = self.control.poll()
                if changes:
                    self._apply(changes)
                    if self.stop_event.is_set():
                        break
                self._supervise_call()
                self._write_status()
            except Exception as e:
                log(f"Node loop error: {e}")
            self.stop_event.wait(POLL_INTERVAL)

        self.shutdown()

    def shutdown(self) -> None:
        self.stop_event.set()
        self._end_call("shutting down")
        if self.server is not None:
            self.server.stop()
            self.server = None
        self._write_status()
        log("Node stopped.")


def detect_local_ip() -> str:
    """This machine's LAN address.

    חשוב לתפקיד A: אם העמית המקומי נרשם דרך 127.0.0.1, כתובת המקור שהשרת
    רואה היא לולאה מקומית - וזו הכתובת חסרת התועלת שתימסר למחשב B.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))  # UDP connect רק בוחר מסלול, לא שולח דבר
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _resolve_path(explicit: Optional[str], role: str, table: Dict[str, str],
                  legacy: Optional[Dict[str, str]] = None) -> str:
    if explicit:
        return explicit
    default = table[role]
    if legacy and not os.path.exists(default) and os.path.exists(legacy[role]):
        return legacy[role]          # ממשיכים מהקובץ שגרסת ה-GUI השאירה
    return default


def parse_args(default_role: str = "b") -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Headless P2P intercom node (no GUI).")
    p.add_argument("--role", choices=("a", "b"), default=default_role,
                   help="a = signalling server + peer (computer A), b = peer only (computer B)")
    p.add_argument("--control", help="control file to read (default control_<ROLE>.txt)")
    p.add_argument("--status", help="status file to write (default status_<ROLE>.txt)")
    p.add_argument("--no-status", action="store_true", help="do not write a status file")
    p.add_argument("--id", dest="my_id", help="this node's id (must differ from the peer's)")
    p.add_argument("--server-ip", help="signalling server address")
    p.add_argument("--port", type=int, help="signalling server port")
    p.add_argument("--ext-ip", help="external IP reported to peers (needs a static port forward)")
    p.add_argument("--local-mode", choices=("on", "off"),
                   help="on = ignore --ext-ip (single LAN). Default: on unless --ext-ip is given")
    p.add_argument("--no-signalling", action="store_true",
                   help="role a only: do not host the signalling server")
    p.add_argument("--no-call", action="store_true",
                   help="start idle; wait for 'intercom = on' in the control file")
    return p.parse_args()


def main(default_role: str = "b") -> None:
    """`default_role` is what the single-file builds pin down (see build_single_file.py)."""
    args = parse_args(default_role)
    defaults = dict(ROLE_DEFAULTS[args.role])
    if args.role == "a":
        # הרישום העצמי חייב לצאת דרך כרטיס הרשת, אחרת B יקבל 127.0.0.1
        defaults["server_ip"] = detect_local_ip()

    if args.my_id:
        defaults["my_id"] = args.my_id
    if args.server_ip:
        defaults["server_ip"] = args.server_ip
    if args.port:
        defaults["port"] = str(args.port)
    if args.ext_ip:
        defaults["ext_ip"] = args.ext_ip
        defaults["local_mode"] = "off"
    if args.local_mode:
        defaults["local_mode"] = args.local_mode
    if args.no_signalling:
        defaults["signalling"] = "off"
    if args.no_call:
        defaults["intercom"] = "off"

    control_path = _resolve_path(args.control, args.role, DEFAULT_CONTROL, LEGACY_CONTROL)
    status_path = "" if args.no_status else _resolve_path(args.status, args.role, DEFAULT_STATUS)

    node = IntercomNode(args.role, control_path, status_path, defaults)

    def handle_signal(signum, _frame) -> None:
        log(f"Received signal {signum}; exiting.")
        node.stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    try:
        node.run()
    except KeyboardInterrupt:
        node.shutdown()

if __name__ == "__main__":
    main(default_role="a")
