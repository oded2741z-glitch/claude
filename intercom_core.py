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
_LOG_FILE: str = ""


def set_log_file(path: str) -> None:
    """Also append every log line to this file. For runs with no console."""
    global _LOG_FILE
    _LOG_FILE = path


def log(msg: str) -> None:
    """Single logging path for every process: one timestamped line.

    Written as one call under a lock - `print` emits the text and the newline
    separately, so lines from the audio, socket and server threads interleave.

    לוגים לעולם לא מפילים את הנוד: תחת pythonw.exe אין קונסולה ו-sys.stdout
    הוא None, ושירות עלול לרוץ בלי הרשאת כתיבה לקובץ הלוג.
    """
    line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
    with _LOG_LOCK:
        stream = sys.stdout
        if stream is not None:
            try:
                stream.write(line)
                stream.flush()
            except (OSError, ValueError):
                pass
        if _LOG_FILE:
            try:
                # נפתח בכל שורה כדי לשרוד מחיקה או רוטציה של הקובץ
                with open(_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


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
