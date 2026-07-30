"""Networking: peer discovery, call signalling and the audio stream socket."""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
import uuid
from typing import Any, Callable

from . import protocol
from .config import (
    DISCOVERY_INTERVAL,
    DISCOVERY_PORT,
    PEER_TIMEOUT,
    PORT_SEARCH_RANGE,
    PROTOCOL_VERSION,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def local_ip() -> str:
    """Best guess at the LAN address of this machine."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; this only asks the routing table which source
        # address would be used for an outbound connection.
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        pass
    finally:
        sock.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def broadcast_targets(ip: str | None = None) -> list[str]:
    """Addresses presence packets are sent to."""
    targets = ["255.255.255.255"]
    ip = ip or local_ip()
    try:
        # Home routers hand out /24 subnets; the directed broadcast gets through
        # on setups where 255.255.255.255 is filtered.
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        targets.append(str(net.broadcast_address))
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
        payload = protocol.encode_message(
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
            payload = protocol.encode_message({"t": "bye", "id": self.node_id})
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
                msg = protocol.decode_message(data.strip())
            except protocol.ProtocolError:
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
                self.sock.sendall(protocol.encode_message(msg))
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
                if len(buffer) > protocol.MAX_MESSAGE_SIZE:
                    break
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = protocol.decode_message(line)
                    except protocol.ProtocolError:
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
        packet = protocol.encode_audio(self._call_id, self._seq, samples, silence)
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
                call_id, seq, flags, samples = protocol.decode_audio(data)
            except protocol.ProtocolError:
                self.bad += 1
                continue
            if call_id != self._call_id:
                continue  # leftover packets from a previous call
            self.received += 1
            self._on_frame(seq, flags, samples)
