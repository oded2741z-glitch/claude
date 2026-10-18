import socket
import contextlib
import json
import threading
import time
import ipaddress
import webbrowser
import sounddevice as sd
import numpy as np
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple, Dict, Any

try:
    import winsound          # Windows בלבד - במערכות אחרות פשוט אין צלצול
except ImportError:
    winsound = None

# --- Constants & Settings ---
DEFAULT_SAMPLE_RATE: int = 24000
ALLOWED_RATES: Tuple[int, ...] = (16000, 24000, 48000)
RATE_LABELS: Dict[int, str] = {16000: "16k Narrow", 24000: "24k Balanced", 48000: "48k Wide"}
CHANNELS: int = 1


def chunk_for(rate: int) -> int:
    """Frames per packet, ~30 ms, so packet count stays steady across rates."""
    return max(256, rate // 32)


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
ACK_PACKET: bytes = b'{"ack":true}'   # מאשר לקליינט שהשרת חי

# --- Signalling server behaviour ---
CLIENT_TTL: float = 30.0          # רישום ישן לא ישמש להצמדה
MATCH_RETRIES: int = 5            # UDP לא אמין - שולחים את פרטי העמית כמה פעמים
MATCH_RETRY_DELAY: float = 0.05
CMD_RETRIES: int = 3              # אותו טעם: פקודה שנעלמת בדרך היא כפתור שלא עובד
CMD_RETRY_DELAY: float = 0.05
PUNCH_DURATION: float = 2.0
PUNCH_INTERVAL: float = 0.1
HW_REFRESH_GAP: float = 5.0       # מרווח מזערי בין אתחולי PortAudio
STREAM_CLOSE_WAIT: float = 5.0    # סגירת stream על התקן שנשלף עלולה להיתקע

# --- Dashboard ---
UI_TICK_MS: int = 250             # קצב רענון המחוונים בדף
PEER_IDLE: float = 1.5            # אין אודיו מעבר לזה -> נורית הקליינט כבר לא ירוקה
HP_REPORT_TTL: float = 5.0        # אין דיווח מהלקוח מעבר לזה -> מצב האוזניות לא ידוע

# --- Web page ---
WEB_HOST: str = "127.0.0.1"       # הדף נפתח רק במחשב הזה
WEB_PORT: int = 8080
ALLOWED_HOSTS: Tuple[str, ...] = (f"127.0.0.1:{WEB_PORT}", f"localhost:{WEB_PORT}")

# --- Ring on headphone connect ---
RING_INTERVAL: float = 1.5        # מרווח בין ביפים
RING_FREQ: int = 1000             # Hz
RING_MS: int = 200                # אורך הביפ. Beep חוסם, אז קצר = השתקה מיידית


HELP_TEXT: str = (
    "P2P Intercom\n\n"
    "1. Start the internal signalling server on one machine (or point both\n"
    "   clients at an external one).\n"
    "2. Set Server IP / Port / My ID on both sides - the two IDs must differ.\n"
    "3. Press START INTERCOM on both. The server matches the first two\n"
    "   clients it sees and hands each one the other's address.\n\n"
    "External IP: only relevant when the signalling server is NOT on the\n"
    "public internet. The port is never translated, so this requires a\n"
    "static port forward on the router. On a single LAN, tick Local Mode.\n\n"
    "Hole punching works with cone NAT. Symmetric NAT needs a relay (TURN).\n\n"
    "(c) oT - All rights reserved."
)


class IntercomServer:
    def __init__(self) -> None:
        # Threading & Concurrency Flags
        self.audio_lock: threading.Lock = threading.Lock()
        self.last_refresh_time: float = 0.0
        self._open_streams: int = 0          # כמה streams פתוחים כרגע (מוגן ע"י audio_lock)

        self.sock: Optional[socket.socket] = None
        self.is_running: bool = False
        self.rx_thread: Optional[threading.Thread] = None
        self.tx_thread: Optional[threading.Thread] = None
        self._stop_lock: threading.Lock = threading.Lock()

        # Peer address handling
        self.peer_lock: threading.Lock = threading.Lock()
        self.assigned_peer: Optional[Tuple[str, int]] = None
        self.locked_peer: Optional[Tuple[str, int]] = None
        self.tx_peer: Optional[Tuple[str, int]] = None

        # Settings (נערכים מהדף)
        self._settings_lock: threading.Lock = threading.Lock()
        self.ext_ip: str = "10.20.1.200"
        self.local_mode: bool = False
        self.server_ip: str = "127.0.0.1"
        self.server_port_text: str = "9999"

        # Signalling target (נשמר כדי לדווח ניתוק בסגירה)
        self.signalling_addr: Optional[Tuple[str, int]] = None
        self.my_id: str = "node_A"
        self.peer_id: str = ""

        self.sample_rate: int = DEFAULT_SAMPLE_RATE
        self.chunk_size: int = chunk_for(DEFAULT_SAMPLE_RATE)

        # Dashboard state (נכתב מתרדי הרקע, נקרא ע"י הדף)
        self.status_text: str = "DISCONNECTED"
        self.status_kind: str = "off"
        self._last_rx: float = 0.0
        self._call_started: float = 0.0
        self._server_port: int = 0
        self._server_error: str = ""
        # מצב אוזניות של לקוחות מרוחקים בלבד:
        # id -> (connected, since, last_seen, addr)
        self._hp_lock: threading.Lock = threading.Lock()
        self._remote_clients: Dict[str, Tuple[bool, float, float, Optional[Tuple[str, int]]]] = {}

        # צלצול חוזר כשהאוזניות של הלקוח מתחברות, עד לחיצה
        self._ring_lock: threading.Lock = threading.Lock()
        self._ring_stop: threading.Event = threading.Event()
        self._ring_thread: Optional[threading.Thread] = None
        self._ringing: bool = False

        # הסוקט של השרת הפנימי. פקודות ללקוח חייבות לצאת ממנו, כי הלקוח
        # מקבל פקודות רק מכתובת השרת שהוא רשום אליה.
        self._server_sock: Optional[socket.socket] = None

        self._server_lock: threading.Lock = threading.Lock()
        self.server_thread: Optional[threading.Thread] = None
        self.server_stop_event: threading.Event = threading.Event()
        self._rewrite_warned: bool = False

        self.httpd: Optional[ThreadingHTTPServer] = None
        self._close_lock: threading.Lock = threading.Lock()
        self._closed: bool = False

        self.load_settings()

    # ------------------------------------------------------------------
    # Remote client control
    # ------------------------------------------------------------------
    def shutdown_client(self, confirmed: bool, expected_id: Any) -> Dict[str, Any]:
        """Tells the most recently heard client to exit its process."""
        with self._hp_lock:
            entries = sorted(self._remote_clients.items(), key=lambda kv: kv[1][2], reverse=True)
        target = next(((cid, e[3]) for cid, e in entries if e[3] is not None), None)
        if target is None:
            return {"message": "No client has reported to this server yet."}

        client_id, addr = target
        sock = self._server_sock
        if sock is None:
            # הלקוח מקבל פקודות רק מכתובת השרת, ולכן חייבים לשלוח מהסוקט שלו
            return {"message": "Start the internal signalling server first.\n\n"
                               "The command is sent from its socket, and the client accepts "
                               "it only from the server address it registered with."}

        # האישור חייב להתייחס לאותו לקוח שהוצג למשתמש
        if not confirmed or expected_id != client_id:
            return {"confirm": f"Shut down {client_id} at {addr[0]}:{addr[1]}?\n\n"
                               "The client process will exit and has to be restarted "
                               "on that machine.",
                    "target": client_id}

        payload = json.dumps({"cmd": "shutdown"}).encode('utf-8')
        for _ in range(CMD_RETRIES):
            try:
                sock.sendto(payload, addr)
            except OSError as e:
                self.log(f"[Server] Failed to send shutdown to {client_id}: {e}")
                return {"message": f"Failed to send shutdown to {client_id}: {e}"}
            time.sleep(CMD_RETRY_DELAY)

        self.stop_ring()
        self.log(f"[Server] Shutdown command sent to {client_id} ({addr[0]}:{addr[1]}).")
        return {}

    # ------------------------------------------------------------------
    # Ring on headphone connect
    # ------------------------------------------------------------------
    def _ring_loop(self) -> None:
        while not self._ring_stop.is_set():
            if winsound is not None:
                try:
                    winsound.Beep(RING_FREQ, RING_MS)
                except Exception:
                    pass
            if self._ring_stop.wait(RING_INTERVAL):
                break

    def start_ring(self) -> None:
        """Repeats a short beep until someone acknowledges it."""
        with self._ring_lock:
            if self._ringing:
                return
            self._ringing = True
            self._ring_stop.clear()
            self._ring_thread = threading.Thread(target=self._ring_loop, daemon=True)
            self._ring_thread.start()

    def stop_ring(self) -> None:
        with self._ring_lock:
            if not self._ringing:
                return
            self._ringing = False
            self._ring_stop.set()
            self._ring_thread = None

    def _silence_ring(self) -> None:
        if self._ringing:
            self.stop_ring()
            self.log("Ring silenced.")

    def log(self, msg: str) -> None:
        """Events go to stdout only - the dashboard carries the state itself."""
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    # ------------------------------------------------------------------
    # Dashboard state
    # ------------------------------------------------------------------
    @staticmethod
    def _row(kind: str, text: str, text_kind: Optional[str] = None) -> Dict[str, str]:
        return {"kind": kind, "text": text,
                "text_kind": text_kind or ("muted" if kind == "off" else "normal")}

    def _server_row(self) -> Dict[str, str]:
        server_up = self.server_thread is not None and self.server_thread.is_alive()
        if server_up:
            return self._row("on", f"internal server listening on port {self._server_port}")
        if self._server_error:
            return self._row("err", self._server_error, "err")
        if self.is_running and self.signalling_addr:
            # לא מארחים שרת, אבל רשומים מול שרת חיצוני
            host, port = self.signalling_addr
            if self.assigned_peer:
                return self._row("on", f"registered with {host}:{port}")
            return self._row("wait", f"registering with {host}:{port}...")
        return self._row("off", "stopped")

    def _client_row(self, now: float) -> Dict[str, str]:
        with self.peer_lock:
            peer, locked = self.assigned_peer, self.locked_peer
        if not self.is_running:
            return self._row("off", "not connected")
        if peer is None:
            return self._row("wait", "waiting for a peer...")
        if locked and now - self._last_rx < PEER_IDLE:
            elapsed = int(now - self._call_started) if self._call_started else 0
            name = self.peer_id or "peer"
            return self._row("on", f"{name}  ·  {locked[0]}:{locked[1]}  ·  "
                                   f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        state = "no audio from peer" if self._call_started else "punching NAT..."
        return self._row("wait", f"{peer[0]}:{peer[1]}  ·  {state}")

    def _mark_headphones(self, client_id: str, connected: bool,
                         addr: Optional[Tuple[str, int]] = None) -> bool:
        """Records a REMOTE client's headphone state. True on a real change."""
        if not client_id or client_id == self.my_id:
            return False  # המצב שלנו לא מעניין - השורה נועדה להראות את הצד השני
        now = time.time()
        with self._hp_lock:
            previous = self._remote_clients.get(client_id)
            known_addr = addr or (previous[3] if previous else None)
            if previous is not None and previous[0] == connected:
                # אותו מצב - שומרים על חותמת השינוי ומרעננים רק את זמן הדיווח
                self._remote_clients[client_id] = (connected, previous[1], now, known_addr)
                return False
            self._remote_clients[client_id] = (connected, now, now, known_addr)
        return True

    def _headphones_row(self) -> Dict[str, str]:
        with self._hp_lock:
            entries = sorted(self._remote_clients.items(), key=lambda kv: kv[1][2], reverse=True)
        if not entries:
            return self._row("off", "waiting for a client to report...")

        client_id, (connected, since, last_seen, _addr) = entries[0]
        stamp = time.strftime('%H:%M:%S', time.localtime(since))
        extra = f"   (+{len(entries) - 1} more)" if len(entries) > 1 else ""
        age = time.time() - last_seen
        if age > HP_REPORT_TTL:
            # הלקוח הפסיק לדווח - עדיף "לא ידוע" על פני נורית ירוקה משקרת
            last_state = "connected" if connected else "disconnected"
            return self._row("off", f"{client_id}: no report for {int(age)}s  ·  "
                                    f"last known: {last_state}{extra}")
        if connected:
            # הרמז שבגללו השורה קיימת: הצד השני מוכן, אפשר לפתוח שיחה
            if self._ringing:
                hint = "   ->  click to silence"
            elif not self.is_running:
                hint = "   ->  press START INTERCOM"
            else:
                hint = ""
            return self._row("on", f"{client_id} connected  ·  {stamp}{extra}{hint}")
        return self._row("err", f"{client_id} disconnected  ·  {stamp}{extra}", "err")

    def get_state(self) -> Dict[str, Any]:
        """Everything the page needs for one redraw."""
        now = time.time()
        alive = self.server_thread is not None and self.server_thread.is_alive()
        if not alive:
            server_button = {"text": "START INTERNAL SERVER", "style": "secondary", "busy": False}
        elif self._server_sock is None:
            server_button = {"text": "STARTING SERVER...", "style": "secondary", "busy": True}
        else:
            server_button = {"text": "STOP INTERNAL SERVER", "style": "primary", "busy": False}
        return {
            "settings": {"ext_ip": self.ext_ip, "local_mode": self.local_mode,
                         "ip": self.server_ip, "port": self.server_port_text,
                         "my_id": self.my_id, "sample_rate": self.sample_rate},
            "running": self.is_running,
            "status": {"text": self.status_text, "kind": self.status_kind},
            "server_button": server_button,
            "ringing": self._ringing,
            "rows": {"SERVER": self._server_row(),
                     "CLIENT": self._client_row(now),
                     "HEADPHONES": self._headphones_row()},
        }

    def update_status(self, text: str, kind: str) -> None:
        self.status_text = text
        self.status_kind = kind

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _set_rate(self, value: Any) -> None:
        try:
            rate = int(value)
        except (TypeError, ValueError):
            rate = DEFAULT_SAMPLE_RATE
        if rate not in ALLOWED_RATES:
            rate = DEFAULT_SAMPLE_RATE
        if rate == self.sample_rate:
            return
        self.sample_rate = rate
        self.chunk_size = chunk_for(rate)
        self.log(f"Audio sample rate set to {rate} ({RATE_LABELS[rate]}).")

    def apply_settings(self, data: Dict[str, Any]) -> None:
        """Takes the values typed in the page. Locked fields stay put during a call."""
        with self._settings_lock:
            if "local_mode" in data:
                self.local_mode = bool(data["local_mode"])
            if not self.is_running:
                for attr, key in (("ext_ip", "ext_ip"), ("server_ip", "ip"),
                                  ("server_port_text", "port"), ("my_id", "my_id")):
                    if key in data:
                        setattr(self, attr, str(data[key]).strip())
                if "sample_rate" in data:
                    self._set_rate(data["sample_rate"])
            self.save_settings()

    def save_settings(self) -> None:
        """Serializes current settings into a localized JSON config."""
        data: Dict[str, Any] = {
            "ext_ip": self.ext_ip,
            "local_mode": self.local_mode,
            "ip": self.server_ip,
            "port": self.server_port_text,
            "my_id": self.my_id,
            "sample_rate": self.sample_rate
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            self.log(f"Error saving settings: {e}")

    def load_settings(self) -> None:
        """Parses the JSON settings file into the current settings."""
        if not os.path.exists(SETTINGS_FILE):
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)

            self.ext_ip = str(data.get("ext_ip", self.ext_ip))
            self.server_ip = str(data.get("ip", self.server_ip))
            self.server_port_text = str(data.get("port", self.server_port_text))
            self.my_id = str(data.get("my_id", self.my_id))
            self.local_mode = bool(data.get("local_mode", False))
            rate = data.get("sample_rate", DEFAULT_SAMPLE_RATE)
            if rate not in ALLOWED_RATES:
                rate = DEFAULT_SAMPLE_RATE
            self.sample_rate = rate
            self.chunk_size = chunk_for(rate)
        except (OSError, json.JSONDecodeError, AttributeError) as e:
            self.log(f"Error loading settings: {e}")

    # ------------------------------------------------------------------
    # Signalling server
    # ------------------------------------------------------------------
    def toggle_local_server(self) -> Optional[str]:
        with self._server_lock:
            if self.server_thread is None or not self.server_thread.is_alive():
                return self.start_local_server()
            self.stop_local_server()
            return None

    def start_local_server(self) -> Optional[str]:
        try:
            server_port: int = int(self.server_port_text)
        except ValueError:
            self.log("Failed to start server: Invalid port number.")
            return "Port must be a valid number."

        ext_ip: str = self.ext_ip
        use_local: bool = self.local_mode

        self.server_stop_event.clear()
        self._rewrite_warned = False
        self._server_port = server_port
        self._server_error = ""
        self.server_thread = threading.Thread(target=self._run_internal_server,
                                              args=(server_port, ext_ip, use_local), daemon=True)
        self.server_thread.start()
        return None

    def stop_local_server(self) -> None:
        self.stop_ring()
        self.server_stop_event.set()
        if self.server_thread and self.server_thread.is_alive():
            # לסוקט השרת יש timeout של שנייה - נותנים לו מספיק זמן לשחרר את הפורט
            self.server_thread.join(timeout=2.5)
        self.server_thread = None
        self.log("Internal Signalling Server STOPPED.")

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
                     "not translated, so this needs a static port forward.")
        return ext_ip

    def _purge_stale(self, clients: Dict[str, Tuple[Tuple[str, int], float]], now: float) -> None:
        for cid in [c for c, (_, ts) in clients.items() if now - ts > CLIENT_TTL]:
            del clients[cid]
            self.log(f"[Server] {cid}: registration expired.")

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

        # כל הודעה תקינה מקבלת ACK, כדי שהלקוח ידע שהשרת חי
        try:
            server_sock.sendto(ACK_PACKET, addr)
        except OSError:
            pass

        status = msg.get("status")

        # דיווח מצב אוזניות מפורש. לא נכנס לבריכת ההצמדה - זה רק סטטוס.
        if status == "hp":
            connected = bool(msg.get("headphones"))
            if self._mark_headphones(client_id, connected, addr):
                if connected:
                    self.log(f"[Server] {client_id}: Headphones connected !!!")
                    # לא מצלצלים באמצע שיחה - הביפ ייכנס לאודיו החי
                    if not self.is_running:
                        self.start_ring()
                else:
                    self.log(f"[Server] {client_id}: Headphones disconnected !!!")
                    self.stop_ring()
            if not connected:
                clients.pop(client_id, None)
            return

        # קבלת דיווח ניתוק מהלקוח. חייב להירשם גם אם הלקוח כבר לא ב-clients:
        # אחרי הצמדה הרשימה מתאפסת, וזה בדיוק המקרה של ניתוק באמצע שיחה.
        if status == "disconnected":
            clients.pop(client_id, None)
            if self._mark_headphones(client_id, False, addr):
                self.log(f"[Server] {client_id}: Headphones disconnected !!!")
                self.stop_ring()
            return

        now = time.time()
        self._purge_stale(clients, now)

        # רישום להצמדה. לקוח שנרשם בהכרח מחזיק אוזניות תקינות.
        if self._mark_headphones(client_id, True, addr):
            self.log(f"[Server] {client_id}: Headphones connected !!!")
            if not self.is_running:
                self.start_ring()
        if client_id not in clients:
            self.log(f"[Server] {client_id}: registered, waiting for a match.")
        clients[client_id] = (addr, now)

        if len(clients) == 2:
            (peer1_id, (addr1, _)), (peer2_id, (addr2, _)) = clients.items()
            self.log(f"[Server] Matched {peer1_id} <---> {peer2_id}.")

            ip1 = self._visible_ip(addr1[0], ext_ip, use_local)
            ip2 = self._visible_ip(addr2[0], ext_ip, use_local)

            reply1 = json.dumps({"peer_ip": ip2, "peer_port": addr2[1], "peer_id": peer2_id,
                                 "sample_rate": self.sample_rate}).encode('utf-8')
            reply2 = json.dumps({"peer_ip": ip1, "peer_port": addr1[1], "peer_id": peer1_id,
                                 "sample_rate": self.sample_rate}).encode('utf-8')
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
            self.log(f"[Server] Bind Error: {e}")
            return

        clients: Dict[str, Tuple[Tuple[str, int], float]] = {}
        self._server_sock = server_sock
        self.log(f"Internal Signalling Server STARTED on port {port}.")

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
                    self.log(f"[Server] Socket error: {e}")
                    break

                # חבילה פגומה אחת לא מפילה את השרת
                try:
                    self._handle_signalling(server_sock, clients, data, addr, ext_ip, use_local)
                except Exception as e:
                    self.log(f"[Server] Error handling packet: {e}")

        self._server_sock = None

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
        try:
            with factory() as stream:
                yield stream
        finally:
            with self.audio_lock:
                self._open_streams -= 1

    # ------------------------------------------------------------------
    # Intercom connection
    # ------------------------------------------------------------------
    def toggle_intercom(self) -> Optional[str]:
        if not self.is_running:
            return self.start_connection()
        self.stop_connection()
        return None

    def start_connection(self) -> Optional[str]:
        self.save_settings()
        try:
            server_port: int = int(self.server_port_text)
        except ValueError:
            return "Port must be a valid number."

        server_ip = self.server_ip
        my_id = self.my_id
        if not my_id:
            return "Please enter a valid ID."

        with self._stop_lock:
            if self.is_running:      # מגן מפני לחיצה כפולה מהירה
                return None
            self.is_running = True

        # לענות לשיחה זו גם השתקה - אחרת הצלצול נכנס לאודיו של האינטרקום
        self.stop_ring()

        self.last_refresh_time = 0.0
        self.peer_id = ""
        self._call_started = 0.0
        self._last_rx = 0.0
        self.signalling_addr = (server_ip, server_port)
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None

        self._safe_refresh_hardware()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)

        threading.Thread(target=self._connection_flow, args=(server_ip, server_port, my_id),
                         daemon=True).start()
        return None

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

            self.update_status("WAITING FOR PEER...", "wait")
            self.log("Waiting for matching peer from server...")
            self.log("Please wait 5 seconds...")

            peer_addr = self._await_peer(server_ip, server_port, my_id)
            if not self.is_running or not peer_addr:
                self.stop_connection()
                return

            with self.peer_lock:
                self.assigned_peer = peer_addr
                self.tx_peer = peer_addr

            self.log(f"Peer Target Assigned -> IP: {peer_addr[0]}, Port: {peer_addr[1]}")
            self.update_status("PUNCHING NAT...", "info")

            punch_until = time.time() + PUNCH_DURATION
            while self.is_running and time.time() < punch_until:
                try:
                    self.sock.sendto(PUNCH_PACKET, peer_addr)
                except (AttributeError, OSError):
                    break
                time.sleep(PUNCH_INTERVAL)

            if not self.is_running:
                return

            self.update_status("CONNECTED (P2P AUDIO)", "on")
            self.log("NAT hole punched! Live audio streaming active.")

            self._call_started = time.time()
            self.rx_thread = threading.Thread(target=self._audio_receiver, daemon=True)
            self.tx_thread = threading.Thread(target=self._audio_sender, daemon=True)
            self.rx_thread.start()
            self.tx_thread.start()

        except Exception as e:
            # בלי זה חריגה כאן משאירה את הדף תקוע ב-WAITING FOR PEER לנצח
            if self.is_running:
                self.log(f"Connection error: {e}")
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
                             f"{self.assigned_peer}). Locking on.")
            elif addr != self.locked_peer:
                return None  # מונע הזרקת אודיו מכל מקור אחר ברשת

        if tag != AUDIO_TAG or not payload:
            return None
        if len(payload) % (2 * CHANNELS) != 0:
            return None  # חבילה קטומה
        return payload

    def _stop_later(self) -> None:
        """stop_connection ממתין לתרדי האודיו, ולכן לא נקרא מתוכם ישירות."""
        threading.Thread(target=self.stop_connection, daemon=True).start()

    def _audio_receiver(self) -> None:
        last_data_time: float = time.time()
        while self.is_running:
            restart_needed = False
            try:
                with self._counted_stream(
                        lambda: sd.OutputStream(samplerate=self.sample_rate, channels=CHANNELS,
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
                                    self._stop_later()
                                return
                            continue
                        except ConnectionResetError:
                            if self.is_running:
                                self.log("Peer disconnected unexpectedly.")
                                self._stop_later()
                            return
                        except (AttributeError, OSError):
                            return  # הסוקט נסגר - יציאה נקייה, בלי הודעת חומרה מטעה

                        last_data_time = time.time()
                        payload = self._peer_audio(addr, data)
                        if payload is None:
                            continue
                        block = np.frombuffer(payload, dtype=np.int16)
                        self._last_rx = last_data_time
                        try:
                            stream.write(block.reshape(-1, CHANNELS))
                        except sd.PortAudioError:
                            if self.is_running:
                                self.log("Output device error. Reconnecting...")
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
                        lambda: sd.InputStream(samplerate=self.sample_rate, channels=CHANNELS, dtype=DTYPE,
                                               blocksize=self.chunk_size)) as stream:
                    while self.is_running:
                        try:
                            data, _ = stream.read(self.chunk_size)
                        except sd.PortAudioError:
                            if self.is_running:
                                self.log("Input device disconnected. Reconnecting...")
                            restart_needed = True
                            break

                        sock, peer = self.sock, self.tx_peer
                        if sock is None or peer is None:
                            return
                        try:
                            sock.sendto(AUDIO_TAG + data.tobytes(), peer)
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

        # דיווח ניתוק לשרת כדי שישחרר את הסלוט מיד
        if self.sock and self.signalling_addr and self.my_id:
            try:
                self.sock.sendto(json.dumps({"id": self.my_id, "status": "disconnected"}).encode('utf-8'),
                                 self.signalling_addr)
            except OSError:
                pass

        # קודם ממתינים לתרדי האודיו, ורק אז סוגרים את הסוקט. סגירה מתחת
        # לרגליהם משאירה את כרטיס הקול תפוס ומייצרת חריגות מטעות.
        current = threading.current_thread()
        for t in (self.rx_thread, self.tx_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=STREAM_CLOSE_WAIT)
                if t.is_alive():
                    self.log("Audio thread still closing the device; will not touch PortAudio.")
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

        # החזרת הלוח למצב מנותק (הדף יצייר בסבב הבא)
        self.peer_id = ""
        self._call_started = 0.0
        self._last_rx = 0.0
        self.update_status("DISCONNECTED", "off")
        self.log("Intercom stopped.")

    # ------------------------------------------------------------------
    # Page actions & shutdown
    # ------------------------------------------------------------------
    def handle_action(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if self._closed:
            return {"message": "Intercom Server is shutting down."}

        settings = body.get("settings")
        if isinstance(settings, dict):
            self.apply_settings(settings)

        action = body.get("action")
        if action == "settings":
            return {}
        if action == "toggle_server":
            error = self.toggle_local_server()
            return {"message": error} if error else {}
        if action == "toggle_intercom":
            error = self.toggle_intercom()
            return {"message": error} if error else {}
        if action == "shutdown_client":
            return self.shutdown_client(bool(body.get("confirmed")), body.get("target"))
        if action == "silence":
            self._silence_ring()
            return {}
        if action == "quit":
            threading.Thread(target=self.quit, daemon=True).start()
            return {}
        return {"message": "Unknown action."}

    def shutdown_all(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.stop_ring()
        self.stop_connection()
        with self._server_lock:
            if self.server_thread is not None and self.server_thread.is_alive():
                self.stop_local_server()

    def quit(self) -> None:
        self.shutdown_all()
        if self.httpd is not None:
            self.httpd.shutdown()


# ----------------------------------------------------------------------
# Web page
# ----------------------------------------------------------------------
PAGE_HTML: str = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/svg+xml" href="__FAVICON__">
<link rel="shortcut icon" href="__FAVICON__">
<meta name="theme-color" content="#f9fafb">
<title>Intercom Server</title>
<meta name="description" content="Local control page for the P2P intercom signalling server and call.">
<link rel="canonical" href="__URL__">
<meta property="og:title" content="Intercom Server">
<meta property="og:description" content="Local control page for the P2P intercom signalling server and call.">
<meta property="og:type" content="website">
<style>
:root {
  --primary-accent: #2563eb;
  --primary-hover:  #1d4ed8;
  --bg-light:       #f9fafb;
  --text-dark:      #111827;
  --text-gray:      #64748b;
  --border-color:   #e2e8f0;
}
* { margin:0; padding:0; box-sizing:border-box; font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
html, body { overflow-x:hidden; }
body { background:var(--bg-light); color:var(--text-dark); min-height:100vh; }
.title-row { display:flex; align-items:center; justify-content:space-between; gap:1rem; flex-wrap:wrap; }
.title-actions { display:flex; gap:0.5rem; }
.btn-outline { background:none; border:1px solid var(--border-color); border-radius:8px; padding:0.5rem 1rem; font-size:0.85rem; font-weight:600; color:var(--text-gray); cursor:pointer; }
.btn-outline:hover { border-color:var(--primary-accent); color:var(--primary-accent); }
.work-wrap { max-width:760px; margin:0 auto; padding:3rem 1.5rem; }
h1 { font-size:2.2rem; letter-spacing:-1px; color:var(--text-dark); }
.subtitle { font-size:1.05rem; color:var(--text-gray); line-height:1.6; margin-bottom:1rem; }
.section-label { font-size:0.75rem; font-weight:700; letter-spacing:2px; text-transform:uppercase; color:var(--text-gray); margin:2rem 0 1rem; }
.panel { background:#ffffff; border:1px solid var(--border-color); border-radius:12px; padding:2rem; display:flex; flex-direction:column; gap:1.25rem; box-shadow:0 4px 12px rgba(0,0,0,0.04); }
.help-text { white-space:pre-line; color:#475569; font-size:0.95rem; line-height:1.6; }
.grid { display:grid; grid-template-columns:2fr 1fr; gap:1rem; }
.field { display:flex; flex-direction:column; gap:0.4rem; }
.field label, .field .label { font-size:0.85rem; font-weight:700; color:var(--text-gray); }
input[type="text"] { background:#ffffff; border:1px solid var(--border-color); color:var(--text-dark); border-radius:8px; padding:0.6rem 0.8rem; font-size:1rem; width:100%; }
input[type="text"]:focus { outline:none; border-color:var(--primary-accent); }
input[type="text"]:disabled { background:var(--bg-light); color:var(--text-gray); }
input[type="checkbox"], input[type="radio"] { accent-color:var(--primary-accent); width:16px; height:16px; }
.check, .rate { display:flex; align-items:center; gap:8px; font-size:0.95rem; font-weight:600; color:var(--text-dark); cursor:pointer; }
.rates { display:flex; gap:1.5rem; flex-wrap:wrap; }
.btn-primary { background:var(--primary-accent); color:#fff; border:1px solid var(--primary-accent); border-radius:8px; padding:0.85rem 1.5rem; font-weight:700; font-size:0.95rem; cursor:pointer; transition:background 0.2s; }
.btn-primary:hover { background:var(--primary-hover); }
.btn-secondary { background:#ffffff; color:var(--text-dark); border:1px solid var(--border-color); border-radius:8px; padding:0.85rem 1.5rem; font-weight:700; font-size:0.95rem; cursor:pointer; }
.btn-secondary:hover { border-color:var(--primary-accent); }
.btn-danger { background:#fee2e2; color:#991b1b; border:1px solid #fee2e2; border-radius:8px; padding:0.85rem 1.5rem; font-weight:700; font-size:0.95rem; cursor:pointer; }
.btn-danger:hover { border-color:#991b1b; }
.title-actions .btn-danger { padding:0.5rem 1rem; font-size:0.85rem; font-weight:600; }
button:disabled { opacity:0.5; cursor:not-allowed; }
[hidden] { display:none !important; }
.status { display:flex; align-items:center; justify-content:center; gap:8px; font-weight:700; font-size:0.95rem; color:var(--text-gray); }
.dot { width:8px; height:8px; border-radius:50%; background:#cbd5e1; flex-shrink:0; }
[data-kind="on"] .dot { background:#10b981; }
[data-kind="wait"] .dot { background:#92400e; }
[data-kind="info"] .dot { background:var(--primary-accent); }
[data-kind="err"] .dot { background:#991b1b; }
.status[data-kind="on"] { color:#065f46; }
.status[data-kind="wait"] { color:#92400e; }
.status[data-kind="info"] { color:#1d4ed8; }
.status[data-kind="err"] { color:#991b1b; }
.rows { display:flex; flex-direction:column; gap:0.75rem; }
.row { background:#ffffff; border:1px solid var(--border-color); border-radius:12px; padding:1rem 1.25rem; box-shadow:0 4px 12px rgba(0,0,0,0.04); }
.row-head { display:flex; align-items:center; gap:10px; }
.row-title { font-size:0.75rem; font-weight:700; letter-spacing:2px; color:var(--text-dark); }
.row-detail { margin:0.4rem 0 0 18px; font-size:0.95rem; color:var(--text-dark); white-space:pre-wrap; word-break:break-word; }
.row-detail[data-text="muted"] { color:var(--text-gray); }
.row-detail[data-text="err"] { color:#991b1b; }
.row.ringing { border-color:#bfdbfe; cursor:pointer; }
.acc-btn { position:fixed; bottom:30px; left:30px; width:50px; height:50px; border-radius:50%; background:var(--primary-accent); display:flex; align-items:center; justify-content:center; cursor:pointer; border:none; box-shadow:0 4px 12px rgba(0,0,0,0.15); z-index:1000; }
.acc-menu { position:fixed; bottom:90px; left:30px; width:260px; display:none; flex-direction:column; gap:0.8rem; padding:1.5rem; background:white; border:1px solid var(--border-color); border-radius:12px; box-shadow:0 10px 30px rgba(0,0,0,0.2); z-index:1000; }
.acc-toggle { width:100%; padding:0.6rem; border:1px solid var(--border-color); border-radius:6px; background:white; font-weight:700; text-align:left; cursor:pointer; }
.acc-toggle:hover { border-color:var(--primary-accent); }
body.acc-high-contrast { background:#000; color:#fff; }
body.acc-high-contrast * { background:#000 !important; color:#fff !important; border-color:#fff !important; }
body.acc-large-text * { font-size:120% !important; }
body.acc-highlight-links a { background:yellow !important; color:black !important; padding:2px !important; }
@media (max-width:768px) { h1 { font-size:1.8rem; } }
@media (max-width:600px) { .grid { grid-template-columns:1fr; } .work-wrap { padding:2rem 1rem; } .panel { padding:1.5rem; } }
</style>
</head>
<body>
<div class="work-wrap">
  <div class="title-row">
    <h1>Intercom Server</h1>
    <div class="title-actions">
      <button class="btn-outline" id="help-btn">Help</button>
      <button class="btn-danger" id="quit-btn">Quit</button>
    </div>
  </div>
  <p class="subtitle">Signalling server, live call and client status on this computer.</p>

  <div class="panel" id="help-panel" hidden><div class="help-text" id="help-text"></div></div>

  <div class="section-label">Integrated Signalling Server</div>
  <div class="panel">
    <div class="field"><label for="ext_ip">External IP</label><input type="text" id="ext_ip" autocomplete="off"></div>
    <label class="check"><input type="checkbox" id="local_mode"> Use Local Mode (Ignore External IP)</label>
    <button class="btn-secondary" id="server-btn">START INTERNAL SERVER</button>
  </div>

  <div class="section-label">Connection Settings</div>
  <div class="panel">
    <div class="grid">
      <div class="field"><label for="ip">Server IP</label><input type="text" id="ip" autocomplete="off"></div>
      <div class="field"><label for="port">Port</label><input type="text" id="port" autocomplete="off"></div>
      <div class="field"><label for="my_id">My ID</label><input type="text" id="my_id" autocomplete="off"></div>
    </div>
    <div class="field"><span class="label">Audio</span><div class="rates" id="rates"></div></div>
    <div class="status" id="status" data-kind="off"><span class="dot"></span><span id="status-text">STATUS: DISCONNECTED</span></div>
    <button class="btn-primary" id="intercom-btn">START INTERCOM</button>
    <button class="btn-danger" id="shutdown-btn">SHUTDOWN CLIENT</button>
  </div>

  <div class="section-label">Live Status</div>
  <div class="rows">
    <div class="row" id="row-SERVER" data-kind="off"><div class="row-head"><span class="dot"></span><span class="row-title">SERVER CONNECTION</span></div><div class="row-detail" data-text="muted">-</div></div>
    <div class="row" id="row-CLIENT" data-kind="off"><div class="row-head"><span class="dot"></span><span class="row-title">CLIENT CONNECTION</span></div><div class="row-detail" data-text="muted">-</div></div>
    <div class="row" id="row-HEADPHONES" data-kind="off"><div class="row-head"><span class="dot"></span><span class="row-title">CLIENT HEADPHONES</span></div><div class="row-detail" data-text="muted">-</div></div>
  </div>
</div>

<button class="acc-btn" onclick="document.getElementById('acc-menu').style.display=(document.getElementById('acc-menu').style.display==='flex'?'none':'flex')">
  <svg viewBox="0 0 24 24" fill="white" width="24"><path d="M12 2c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zm9 7h-6v13h-2v-6h-2v6H9V9H3V7h18v2z"/></svg>
</button>
<div class="acc-menu" id="acc-menu">
  <h4 style="margin-bottom:0.5rem;">Accessibility Options</h4>
  <button class="acc-toggle" onclick="document.body.classList.toggle('acc-high-contrast')">High Contrast Mode</button>
  <button class="acc-toggle" onclick="document.body.classList.toggle('acc-large-text')">Increase Font Size</button>
  <button class="acc-toggle" onclick="document.body.classList.toggle('acc-highlight-links')">Highlight Links</button>
  <button class="acc-toggle" onclick="location.reload()" style="color:red;border-color:red;margin-top:10px;">Reset All</button>
</div>

<script>
const RATES = __RATES__;
const HELP = __HELP__;
const POLL_MS = __POLL__;
const LOCKED = ["ext_ip", "ip", "port", "my_id"];
let loaded = false;
let stopped = false;

function $(id) { return document.getElementById(id); }

function readSettings() {
  const r = document.querySelector('input[name="rate"]:checked');
  return {
    ext_ip: $("ext_ip").value.trim(),
    local_mode: $("local_mode").checked,
    ip: $("ip").value.trim(),
    port: $("port").value.trim(),
    my_id: $("my_id").value.trim(),
    sample_rate: r ? parseInt(r.value, 10) : 0
  };
}

async function act(action, extra) {
  const body = Object.assign({ action: action }, extra || {});
  if (loaded) body.settings = readSettings();
  try {
    const res = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Intercom": "1" },
      body: JSON.stringify(body)
    });
    return await res.json();
  } catch (e) {
    return { message: "Intercom Server is not responding." };
  }
}

function show(res) { if (res && res.message) alert(res.message); }

async function run(action) {
  show(await act(action));
  refresh();
}

async function shutdownClient() {
  let res = await act("shutdown_client");
  while (res && res.confirm) {
    if (!confirm(res.confirm)) return;
    res = await act("shutdown_client", { confirmed: true, target: res.target });
  }
  show(res);
  refresh();
}

function setRow(key, row) {
  const el = $("row-" + key);
  el.dataset.kind = row.kind;
  const detail = el.querySelector(".row-detail");
  detail.textContent = row.text;
  detail.dataset.text = row.text_kind;
}

function fill(s) {
  for (const k of LOCKED) $(k).value = s[k];
  $("local_mode").checked = s.local_mode;
  const r = document.querySelector('input[name="rate"][value="' + s.sample_rate + '"]');
  if (r) r.checked = true;
}

function render(s) {
  if (!loaded) { fill(s.settings); loaded = true; }
  for (const k of LOCKED) $(k).disabled = s.running;
  document.querySelectorAll('input[name="rate"]').forEach(function (r) { r.disabled = s.running; });

  const sb = $("server-btn");
  sb.textContent = s.server_button.text;
  sb.className = s.server_button.style === "primary" ? "btn-primary" : "btn-secondary";
  sb.disabled = s.server_button.busy;

  const ib = $("intercom-btn");
  ib.textContent = s.running ? "DISCONNECT" : "START INTERCOM";
  ib.className = s.running ? "btn-danger" : "btn-primary";

  $("status").dataset.kind = s.status.kind;
  $("status-text").textContent = "STATUS: " + s.status.text;

  for (const key of ["SERVER", "CLIENT", "HEADPHONES"]) setRow(key, s.rows[key]);
  $("row-HEADPHONES").classList.toggle("ringing", s.ringing);
}

function renderStopped() {
  document.querySelectorAll(".work-wrap button, .work-wrap input, #help-btn, #quit-btn").forEach(function (el) { el.disabled = true; });
  $("status").dataset.kind = "err";
  $("status-text").textContent = "STATUS: INTERCOM SERVER IS NOT RUNNING";
  for (const key of ["SERVER", "CLIENT", "HEADPHONES"]) setRow(key, { kind: "off", text: "-", text_kind: "muted" });
}

async function refresh() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    render(await res.json());
  } catch (e) {
    renderStopped();
  }
}

function loop() {
  if (stopped) return;
  refresh().finally(function () { setTimeout(loop, POLL_MS); });
}

for (const pair of RATES) {
  const label = document.createElement("label");
  label.className = "rate";
  const input = document.createElement("input");
  input.type = "radio";
  input.name = "rate";
  input.value = String(pair[0]);
  input.addEventListener("change", function () { act("settings"); });
  label.appendChild(input);
  label.appendChild(document.createTextNode(" " + pair[1]));
  $("rates").appendChild(label);
}

$("help-text").textContent = HELP;
for (const k of LOCKED) $(k).addEventListener("change", function () { act("settings"); });
$("local_mode").addEventListener("change", function () { act("settings"); });
$("help-btn").addEventListener("click", function () { $("help-panel").hidden = !$("help-panel").hidden; });
$("server-btn").addEventListener("click", function () { run("toggle_server"); });
$("intercom-btn").addEventListener("click", function () { run("toggle_intercom"); });
$("shutdown-btn").addEventListener("click", shutdownClient);
$("row-HEADPHONES").addEventListener("click", function () { act("silence"); });
$("quit-btn").addEventListener("click", async function () {
  stopped = true;
  await act("quit");
  renderStopped();
});

loop();
</script>
</body>
</html>
"""

FAVICON: str = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
                "<rect width='32' height='32' rx='8' fill='%232563eb'/>"
                "<circle cx='16' cy='16' r='6' fill='white'/></svg>")
PAGE_URL: str = f"http://{WEB_HOST}:{WEB_PORT}/"
PAGE_BYTES: bytes = (PAGE_HTML
                     .replace("__FAVICON__", FAVICON)
                     .replace("__URL__", PAGE_URL)
                     .replace("__RATES__", json.dumps([[r, RATE_LABELS[r]] for r in ALLOWED_RATES]))
                     .replace("__HELP__", json.dumps(HELP_TEXT))
                     .replace("__POLL__", str(UI_TICK_MS))
                     .encode("utf-8"))


class WebHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Dict[str, Any], code: int = 200) -> None:
        self._send(code, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _local_host(self) -> bool:
        # חוסם אתרים זרים שמנסים לדבר עם הדף דרך שם דומיין (DNS rebinding)
        return (self.headers.get("Host") or "").strip().lower() in ALLOWED_HOSTS

    def do_GET(self) -> None:
        if not self._local_host():
            self._send(403, b"Forbidden", "text/plain")
            return
        app: IntercomServer = self.server.app  # type: ignore[attr-defined]
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE_BYTES, "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(app.get_state())
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self) -> None:
        # כותרת מותאמת מכריחה דפדפן לבקש אישור CORS, שלא ניתן, ולכן אתר זר
        # לא יכול ללחוץ על הכפתורים בשמנו
        if not self._local_host() or self.headers.get("X-Intercom") != "1":
            self._send(403, b"Forbidden", "text/plain")
            return
        if self.path != "/api/action":
            self._send(404, b"Not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send(400, b"Bad request", "text/plain")
            return
        if not isinstance(body, dict):
            self._send(400, b"Bad request", "text/plain")
            return
        app: IntercomServer = self.server.app  # type: ignore[attr-defined]
        self._json(app.handle_action(body))


def main() -> None:
    app = IntercomServer()
    try:
        httpd = ThreadingHTTPServer((WEB_HOST, WEB_PORT), WebHandler)
    except OSError as e:
        app.log(f"Cannot open the control page on port {WEB_PORT}: {e}")
        return
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    app.httpd = httpd

    app.log(f"Intercom Server control page: {PAGE_URL}")
    app.log("Press Ctrl+C or the Quit button to exit.")
    webbrowser.open(PAGE_URL)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        app.log("Exiting...")
    finally:
        app.shutdown_all()
        httpd.server_close()
        app.log("Intercom Server stopped.")


if __name__ == "__main__":
    main()
