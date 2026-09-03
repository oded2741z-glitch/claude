import socket
import contextlib
import json
import queue
import threading
import time
import sounddevice as sd
import numpy as np
import os
from typing import Optional, Tuple, Dict, Any

# --- System & Audio Settings ---
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
CHUNK_SIZE: int = 512
DTYPE: str = 'int16'
BUFFER_SIZE: int = 65535
TIMEOUT_SECS: float = 3.0
SETTINGS_FILE: str = "settings.txt"

# --- P2P wire protocol (חייב להיות זהה ל-server.py) ---
AUDIO_TAG: bytes = b'\x01'
PUNCH_TAG: bytes = b'\x02'
PUNCH_PACKET: bytes = PUNCH_TAG + b'PUNCH'
P2P_TAGS: Tuple[bytes, bytes] = (AUDIO_TAG, PUNCH_TAG)

PUNCH_DURATION: float = 2.0
PUNCH_INTERVAL: float = 0.1
HW_REFRESH_GAP: float = 5.0
STREAM_CLOSE_WAIT: float = 5.0

HEARTBEAT_INTERVAL: float = 1.0
SERVER_TIMEOUT: float = 3.0
DEVICE_POLL_INTERVAL: float = 1.0
REPORT_REPEATS: int = 3
AUDIO_QUEUE_MAX: int = 64
CALL_RETRY_GAP: float = 1.0


class IntercomCLI:
    def __init__(self) -> None:
        self.sock: Optional[socket.socket] = None
        self.shutdown_event: threading.Event = threading.Event()
        self.in_call: bool = False

        self.audio_lock: threading.Lock = threading.Lock()
        self._open_streams: int = 0
        self._last_refresh: float = 0.0

        self.rx_audio_thread: Optional[threading.Thread] = None
        self.tx_audio_thread: Optional[threading.Thread] = None

        self.peer_lock: threading.Lock = threading.Lock()
        self.assigned_peer: Optional[Tuple[str, int]] = None
        self.locked_peer: Optional[Tuple[str, int]] = None
        self.tx_peer: Optional[Tuple[str, int]] = None

        self.server_ip: str = "192.168.1.11"
        self.server_port: int = 9999
        self.my_id: str = "node_B"
        self.peer_id: str = ""

        self.headphones_ok: bool = False
        self._server_online: bool = False
        self._last_ack: float = 0.0
        self._last_rx: float = 0.0

        self._peer_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._audio_q: "queue.Queue[bytes]" = queue.Queue(maxsize=AUDIO_QUEUE_MAX)

        self.load_settings(quiet=False)

    def log(self, msg: str) -> None:
        """Thread-safe standard output logging."""
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def load_settings(self, quiet: bool = True) -> None:
        """Parses the JSON settings file safely and populates instance variables."""
        if not os.path.exists(SETTINGS_FILE):
            self.log(f"{SETTINGS_FILE} not found. Creating default settings file.")
            self.save_settings()
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
            ip = str(data.get("ip", self.server_ip)).strip()
            port = int(data.get("port", self.server_port))
            my_id = str(data.get("my_id", self.my_id)).strip()
        except Exception as e:
            self.log(f"Error loading {SETTINGS_FILE}: {e}. Keeping current values.")
            return

        changed = (ip, port, my_id) != (self.server_ip, self.server_port, self.my_id)
        self.server_ip, self.server_port, self.my_id = ip, port, my_id
        if changed or not quiet:
            self.log(f"Settings: IP={self.server_ip}, Port={self.server_port}, ID={self.my_id}")

    def save_settings(self) -> None:
        """Serializes current configuration into a localized JSON config."""
        data: Dict[str, str] = {
            "ip": self.server_ip,
            "port": str(self.server_port),
            "my_id": self.my_id
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            self.log(f"Error saving settings: {e}")

    # ------------------------------------------------------------------
    # Signalling helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_json_dict(data: bytes) -> Optional[Dict[str, Any]]:
        try:
            msg = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return msg if isinstance(msg, dict) else None

    def _send_json(self, payload: Dict[str, Any], repeats: int = 1) -> None:
        sock = self.sock
        if sock is None:
            return
        data = json.dumps(payload).encode('utf-8')
        target = (self.server_ip, self.server_port)
        for _ in range(max(1, repeats)):
            try:
                sock.sendto(data, target)
            except (AttributeError, OSError):
                return

    def _report_headphones(self, repeats: int = REPORT_REPEATS) -> None:
        """Explicit headphone-state report. Never enters the matching pool."""
        self._send_json({"id": self.my_id, "status": "hp",
                         "headphones": self.headphones_ok}, repeats)

    # ------------------------------------------------------------------
    # Audio hardware
    # ------------------------------------------------------------------
    def _safe_refresh_hardware(self) -> None:
        """Restarts the PortAudio bindings. Never runs while a stream is open."""
        with self.audio_lock:
            # אתחול PortAudio בזמן ש-stream פתוח מקריס את התהליך ברמת ה-C,
            # בלי traceback. המונה מוחזק על פני כל חיי ה-stream (ראה _counted_stream).
            if self._open_streams > 0:
                return
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
    def _counted_stream(self, factory):
        """Opens a stream while holding the open-stream counter for its whole lifetime."""
        with self.audio_lock:
            self._open_streams += 1
        try:
            with factory() as stream:
                yield stream
        finally:
            with self.audio_lock:
                self._open_streams -= 1

    def _devices_ready(self) -> bool:
        try:
            sd.check_output_settings(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE)
            sd.check_input_settings(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE)
            return True
        except Exception:
            return False

    def _device_loop(self) -> None:
        """Polls the audio hardware forever. Logs and reports every state change."""
        while not self.shutdown_event.is_set():
            ready = self._devices_ready()
            if ready != self.headphones_ok:
                self.headphones_ok = ready
                if ready:
                    self.log("HEADPHONES: CONNECTED  (mic + speaker ready)")
                else:
                    self.log("HEADPHONES: DISCONNECTED")
                self._report_headphones()
                if not ready and self.in_call:
                    self.log("Ending call: audio device is gone.")
                    self.in_call = False
            elif not ready:
                self._safe_refresh_hardware()
            self.shutdown_event.wait(DEVICE_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Server link
    # ------------------------------------------------------------------
    def _signal_loop(self) -> None:
        """Heartbeat to the server plus SERVER CONNECTED / DISCONNECTED tracking."""
        while not self.shutdown_event.is_set():
            self._report_headphones(1)
            online = (time.time() - self._last_ack) <= SERVER_TIMEOUT
            if online != self._server_online:
                self._server_online = online
                if online:
                    self.log(f"SERVER: CONNECTED  ({self.server_ip}:{self.server_port})")
                else:
                    self.log(f"SERVER: DISCONNECTED  ({self.server_ip}:{self.server_port} not responding)")
            self.shutdown_event.wait(HEARTBEAT_INTERVAL)

    def _rx_loop(self) -> None:
        """Single owner of recvfrom. Dispatches signalling and P2P audio."""
        while not self.shutdown_event.is_set():
            sock = self.sock
            if sock is None:
                self.shutdown_event.wait(0.2)
                continue
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except (AttributeError, OSError):
                if self.shutdown_event.is_set():
                    return
                self.shutdown_event.wait(0.5)
                continue

            if not data:
                continue

            if data[:1] == b'{':
                info = self._parse_json_dict(data)
                if info is None:
                    continue
                self._last_ack = time.time()
                if "peer_ip" in info and "peer_port" in info:
                    self._peer_q.put(info)
                continue

            payload = self._peer_audio(addr, data)
            if payload is None:
                continue
            try:
                self._audio_q.put_nowait(payload)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self._audio_q.get_nowait()
                with contextlib.suppress(queue.Full):
                    self._audio_q.put_nowait(payload)

    def _peer_audio(self, addr: Tuple[str, int], data: bytes) -> Optional[bytes]:
        """מאמת שהחבילה הגיעה מהעמית ומחזיר payload רק לחבילות אודיו."""
        tag, payload = data[:1], data[1:]
        if tag not in P2P_TAGS:
            return None

        with self.peer_lock:
            if self.locked_peer is None:
                if self.assigned_peer is None:
                    return None
                # ננעלים על הכתובת של החבילה המתויגת הראשונה, גם אם ה-NAT
                # הקצה פורט שונה מזה שהשרת דיווח
                self.locked_peer = addr
                self.tx_peer = addr
                if addr != self.assigned_peer:
                    self.log(f"Peer reached us from {addr[0]}:{addr[1]} "
                             f"(assigned {self.assigned_peer}). Locking on.")
            elif addr != self.locked_peer:
                return None  # מונע הזרקת אודיו מכל מקור אחר ברשת

        self._last_rx = time.time()
        if tag != AUDIO_TAG or not payload:
            return None
        if len(payload) % (2 * CHANNELS) != 0:
            return None
        return payload

    # ------------------------------------------------------------------
    # Call flow
    # ------------------------------------------------------------------
    def _drain(self, q: "queue.Queue") -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _await_peer(self) -> Optional[Tuple[str, int]]:
        """Registers for matching once per second until the server answers."""
        last_send = 0.0
        while self.in_call and self.headphones_ok and not self.shutdown_event.is_set():
            now = time.time()
            if now - last_send >= HEARTBEAT_INTERVAL:
                self._send_json({"id": self.my_id})
                last_send = now
            try:
                info = self._peer_q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                peer = (str(info["peer_ip"]), int(info["peer_port"]))
            except (KeyError, TypeError, ValueError):
                continue
            peer_id = info.get("peer_id")
            self.peer_id = peer_id.strip() if isinstance(peer_id, str) else ""
            return peer
        return None

    def _run_call(self) -> None:
        """Registration, NAT hole punching and live audio for one call."""
        self.load_settings()
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None
        self.peer_id = ""
        self._drain(self._audio_q)
        self._drain(self._peer_q)
        self.in_call = True

        try:
            self.log("Registering with server and waiting for a matching peer...")
            peer_addr = self._await_peer()
            if peer_addr is None or not self.in_call:
                return

            with self.peer_lock:
                self.assigned_peer = peer_addr
                self.tx_peer = peer_addr

            who = f" ({self.peer_id})" if self.peer_id else ""
            self.log(f"Peer Target Assigned{who} -> IP: {peer_addr[0]}, Port: {peer_addr[1]}")
            self.log("Sending UDP Hole Punch packets...")

            punch_until = time.time() + PUNCH_DURATION
            while self.in_call and time.time() < punch_until:
                sock = self.sock
                if sock is None:
                    return
                try:
                    sock.sendto(PUNCH_PACKET, peer_addr)
                except (AttributeError, OSError):
                    return
                time.sleep(PUNCH_INTERVAL)

            if not self.in_call:
                return

            self._last_rx = time.time()
            self.log("NAT hole punched! Live audio streaming active.")

            self.rx_audio_thread = threading.Thread(target=self._audio_player, daemon=True)
            self.tx_audio_thread = threading.Thread(target=self._audio_sender, daemon=True)
            self.rx_audio_thread.start()
            self.tx_audio_thread.start()

            while (self.in_call and self.headphones_ok and not self.shutdown_event.is_set()
                   and self.rx_audio_thread.is_alive() and self.tx_audio_thread.is_alive()):
                time.sleep(0.25)

        except Exception as e:
            self.log(f"Call error: {e}")
        finally:
            self.end_call()

    def end_call(self) -> None:
        was_active = self.in_call
        self.in_call = False

        # ממתינים לתרדי האודיו לפני שמשחררים את המצב. סגירת stream על התקן
        # שנשלף עלולה להיתקע, והתרד עדיין מחזיק את מונה ה-streams.
        current = threading.current_thread()
        for t in (self.rx_audio_thread, self.tx_audio_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=STREAM_CLOSE_WAIT)
                if t.is_alive():
                    self.log("Audio thread still closing the device; will not touch PortAudio.")
        self.rx_audio_thread = None
        self.tx_audio_thread = None

        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None
        self.peer_id = ""
        self._drain(self._audio_q)
        self._drain(self._peer_q)

        if was_active:
            self.log("Call ended. Waiting for the next peer...")

    # ------------------------------------------------------------------
    # Audio streaming
    # ------------------------------------------------------------------
    def _audio_player(self) -> None:
        try:
            with self._counted_stream(
                    lambda: sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                            dtype=DTYPE)) as stream:
                while self.in_call and not self.shutdown_event.is_set():
                    try:
                        payload = self._audio_q.get(timeout=0.25)
                    except queue.Empty:
                        if time.time() - self._last_rx > TIMEOUT_SECS:
                            self.log("Peer disconnected (3 seconds timeout).")
                            self.in_call = False
                            break
                        continue
                    stream.write(np.frombuffer(payload, dtype=np.int16).reshape(-1, CHANNELS))
        except Exception:
            if self.in_call:
                self.log("HEADPHONES: output device error (speaker lost).")
                self.in_call = False

    def _audio_sender(self) -> None:
        try:
            with self._counted_stream(
                    lambda: sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                                           blocksize=CHUNK_SIZE)) as stream:
                while self.in_call and not self.shutdown_event.is_set():
                    data, _ = stream.read(CHUNK_SIZE)
                    sock, peer = self.sock, self.tx_peer
                    if sock is None or peer is None:
                        break
                    try:
                        sock.sendto(AUDIO_TAG + data.tobytes(), peer)
                    except ConnectionResetError:
                        continue
                    except (AttributeError, OSError):
                        break
        except Exception:
            if self.in_call:
                self.log("HEADPHONES: input device error (mic lost).")
                self.in_call = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def start(self) -> None:
        self.log(f"Starting Headless Client [{self.my_id}] -> Server {self.server_ip}:{self.server_port}")
        self.log("Press Ctrl+C to exit.")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)

        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._signal_loop, daemon=True).start()
        threading.Thread(target=self._device_loop, daemon=True).start()

        try:
            while not self.shutdown_event.is_set():
                if self.headphones_ok and not self.in_call:
                    self._run_call()
                    time.sleep(CALL_RETRY_GAP)
                else:
                    time.sleep(0.25)
        except KeyboardInterrupt:
            self.log("Exiting...")
        finally:
            self.stop()

    def stop(self) -> None:
        self.shutdown_event.set()
        self.end_call()

        # דיווח ניתוק לשרת כדי שישחרר את הסלוט ויכבה את הנורית מיד
        self._send_json({"id": self.my_id, "status": "disconnected"}, REPORT_REPEATS)

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            finally:
                self.sock = None
        self.log("Client stopped.")


if __name__ == "__main__":
    cli = IntercomCLI()
    cli.start()
