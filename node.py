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

from intercom_core import AUDIO, IntercomPeer, log
from signalling import SignallingServer
from txt_bridge import ControlFile, StatusFile, as_bool, as_int

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
DEFAULT_SWITCH: Dict[str, str] = {"a": "switch_A.txt", "b": "switch_B.txt"}
DEFAULT_STATUS: Dict[str, str] = {"a": "status_A.txt", "b": "status_B.txt"}


class IntercomNode:
    def __init__(self, role: str, control_path: str, status_path: str,
                 defaults: Dict[str, str], switch_path: str = "") -> None:
        self.role: str = role
        self.cfg: Dict[str, str] = dict(defaults)
        self.control: ControlFile = ControlFile(control_path)
        # קובץ מתג אופציונלי: כל תוכנו מילה אחת, on / off / quit
        self.switch: Optional[ControlFile] = (
            ControlFile(switch_path, optional=True) if switch_path else None)
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
        if self.switch is not None:
            self.switch.ensure_word("on" if self._want("intercom") else "off")

        log(f"Starting headless node [{self.cfg.get('my_id')}] role={self.role.upper()} "
            f"-> signalling {self._server_addr()[0]}:{self._server_addr()[1]}")
        log(f"Control file: {self.control.path}"
            + (f" | Switch file: {self.switch.path}" if self.switch else "")
            + (f" | Status file: {self.status.path}" if self.status else ""))
        log("Press Ctrl+C to exit.")

        self._warn_loopback()
        self._sync_server()

        while not self.stop_event.is_set():
            try:
                # שני המקורות נקראים בכל סבב, והשינוי האחרון הוא הקובע
                for source in (self.control, self.switch):
                    changes = source.poll() if source is not None else None
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
    p.add_argument("--switch", help="one-word on/off/quit file (default switch_<ROLE>.txt); "
                                    "read only when it exists")
    p.add_argument("--no-switch", action="store_true", help="ignore the switch file")
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
    switch_path = "" if args.no_switch else _resolve_path(args.switch, args.role, DEFAULT_SWITCH)

    node = IntercomNode(args.role, control_path, status_path, defaults, switch_path)

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
    main()
