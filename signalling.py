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

from intercom_core import BUFFER_SIZE, log, parse_json_dict

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
