"""Call state machine: ties discovery, signalling, transport and audio together."""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable

import numpy as np

from . import audio as audiolib
from . import net, protocol
from .config import AUDIO_PORT, DISCOVERY_PORT, PROTOCOL_VERSION, SIGNALING_PORT, Settings

IDLE = "idle"
CALLING = "calling"
RINGING = "ringing"
IN_CALL = "in_call"

PING_INTERVAL = 2.0
CALL_TIMEOUT = 45.0


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
        self.rtt_ms: float | None = None

        self.inputs: list[audiolib.DeviceInfo] = []
        self.outputs: list[audiolib.DeviceInfo] = []
        self.selected_input: audiolib.DeviceInfo | None = None
        self.selected_output: audiolib.DeviceInfo | None = None
        self.audio_ok = False

        self.engine = audiolib.AudioEngine(
            settings.wire_rate,
            settings.frame_ms,
            settings.jitter_ms,
            self._on_captured_frame,
            self._on_audio_error,
        )
        self.engine.volume = settings.volume
        self.engine.mic_gain = settings.mic_gain

        self.transport = net.AudioTransport(audio_port, self._on_network_frame)
        self.signaling = net.SignalingServer(signaling_port, self._on_incoming_link)
        self.discovery: net.Discovery | None = None

        self._link: net.SignalingLink | None = None
        self._pending: dict[str, Any] | None = None
        self._call_id = 0
        self._call_started = 0.0
        self._ticker: threading.Thread | None = None
        self._stop_ticker = threading.Event()
        self._last_recovery = 0.0
        self._monitor_requested = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        self.local_ip = net.local_ip()
        self.transport.start()
        self.signaling.start()
        self.discovery = net.Discovery(
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

    def stop(self) -> None:
        try:
            self.hangup()
        except Exception:  # noqa: BLE001
            pass
        self._stop_ticker.set()
        if self.discovery is not None:
            self.discovery.stop()
        self.signaling.stop()
        self.transport.stop()
        self.engine.stop()

    # ------------------------------------------------------------------
    # devices
    # ------------------------------------------------------------------
    def refresh_devices(self, initial: bool = False) -> list[audiolib.DeviceInfo]:
        """Re-scan the system.  Returns devices that were not there before."""
        was_running = self.engine.running
        if was_running:
            self.engine.stop()

        previous = {dev.key for dev in self.inputs + self.outputs}
        try:
            if not initial:
                audiolib.refresh_devices()
            self.inputs, self.outputs = audiolib.list_devices()
        except audiolib.AudioUnavailable as exc:
            self.inputs, self.outputs = [], []
            self._emit("log", key="log_audio_error", err=str(exc))
            self._emit("devices")
            return []

        new_devices = [d for d in self.inputs + self.outputs if d.key not in previous]
        if not initial:
            self._emit("log", key="log_devices_refreshed", count=len(self.inputs) + len(self.outputs))
        for dev in new_devices:
            if not initial:
                self._emit("log", key="log_new_device", name=dev.label)

        self._resolve_selection(new_devices if not initial else None)
        self._emit("devices")
        if was_running or self._monitor_requested:
            self.ensure_audio()
        return new_devices

    def _resolve_selection(self, new_devices: list[audiolib.DeviceInfo] | None) -> None:
        """Keep the user's choice if it still exists, otherwise choose sensibly."""
        auto = self.settings.auto_pick_new_device
        fresh_in = [d for d in (new_devices or []) if d.is_input]
        fresh_out = [d for d in (new_devices or []) if not d.is_input]

        wanted_in = self.selected_input.key if self.selected_input else self.settings.input_device_name
        wanted_out = self.selected_output.key if self.selected_output else self.settings.output_device_name

        if auto and fresh_in:
            self.selected_input = audiolib.pick_default(fresh_in)
        else:
            self.selected_input = audiolib.find_device(self.inputs, wanted_in) or audiolib.pick_default(self.inputs)

        if auto and fresh_out:
            self.selected_output = audiolib.pick_default(fresh_out)
        else:
            self.selected_output = audiolib.find_device(self.outputs, wanted_out) or audiolib.pick_default(self.outputs)

        self._remember_selection()
        if not self.inputs:
            self._emit("log", key="log_no_input")
        if not self.outputs:
            self._emit("log", key="log_no_output")

    def _remember_selection(self) -> None:
        self.settings.input_device_name = self.selected_input.key if self.selected_input else ""
        self.settings.output_device_name = self.selected_output.key if self.selected_output else ""

    def select_input(self, dev: audiolib.DeviceInfo | None) -> None:
        self.selected_input = dev
        self._remember_selection()
        if self.engine.running:
            self.ensure_audio(restart=True)

    def select_output(self, dev: audiolib.DeviceInfo | None) -> None:
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
        except audiolib.AudioUnavailable as exc:
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
    def place_call(self, host: str, port: int = SIGNALING_PORT) -> None:
        with self._lock:
            if self.state != IDLE:
                return
            self.state = CALLING
            self.peer_ip = host
            self.peer_name = host
            self.rtt_ms = None
            self._call_id = random.getrandbits(32)
            # Starts the no-answer timeout; without this the ticker would still
            # be holding the timestamp of the previous call.
            self._call_started = time.monotonic()
        self.settings.last_peer_ip = host
        self._emit("state", state=CALLING)
        self._emit("log", key="log_calling", ip=host)
        threading.Thread(target=self._dial, args=(host, int(port)), daemon=True).start()

    def _dial(self, host: str, port: int) -> None:
        try:
            link = net.connect_signaling(host, port)
        except OSError as exc:
            self._emit("log", key="log_call_failed", ip=host, err=_reason(exc))
            self._finish_call(notify=False)
            return
        with self._lock:
            if self.state != CALLING:
                link.close()
                return
            self._link = link
        link.start(self._on_message, self._on_link_closed)
        self.ensure_audio()
        self.engine.ring_mode = audiolib.RING_OUTGOING
        link.send(
            {
                "t": protocol.INVITE,
                "v": PROTOCOL_VERSION,
                "name": self.settings.display_name,
                "call_id": self._call_id,
                "audio_port": self.transport.port,
                "rate": self.settings.wire_rate,
                "frame_ms": self.settings.frame_ms,
            }
        )
        self._start_ticker()

    # ------------------------------------------------------------------
    # incoming calls
    # ------------------------------------------------------------------
    def _on_incoming_link(self, link: net.SignalingLink) -> None:
        link.start(self._on_message, self._on_link_closed)

    def _on_message(self, link: net.SignalingLink, msg: dict[str, Any]) -> None:
        kind = msg.get("t")
        if kind == protocol.INVITE:
            self._handle_invite(link, msg)
        elif kind == protocol.RINGING:
            with self._lock:
                if self.state == CALLING:
                    self.peer_name = str(msg.get("name") or self.peer_ip)[:64]
            self._emit("log", key="log_ringing_out", name=self.peer_name)
            self._emit("state", state=CALLING)
        elif kind == protocol.ACCEPT:
            self._handle_accept(link, msg)
        elif kind == protocol.REJECT:
            self._emit("log", key="log_rejected")
            self._finish_call(notify=False)
        elif kind == protocol.BUSY:
            self._emit("log", key="log_busy")
            self._finish_call(notify=False)
        elif kind == protocol.BYE:
            if self.state in (IN_CALL, CALLING, RINGING):
                self._emit("log", key="log_peer_hangup")
            self._finish_call(notify=False)
        elif kind == protocol.PING:
            link.send({"t": protocol.PONG, "ts": msg.get("ts")})
        elif kind == protocol.PONG:
            try:
                sent = float(msg.get("ts") or 0.0)
            except (TypeError, ValueError):
                return
            if sent:
                self.rtt_ms = max(0.0, (time.monotonic() - sent) * 1000.0)

    def _handle_invite(self, link: net.SignalingLink, msg: dict[str, Any]) -> None:
        with self._lock:
            if self.state != IDLE or self._link is not None:
                link.send({"t": protocol.BUSY})
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
            self.state = RINGING
            self._call_started = time.monotonic()

        link.send({"t": protocol.RINGING, "name": self.settings.display_name})
        self._emit("state", state=RINGING)
        self._emit("log", key="log_incoming", name=self.peer_name, ip=self.peer_ip)
        self._emit("incoming", name=self.peer_name, ip=self.peer_ip)
        if rate != self.settings.wire_rate:
            self._emit("log", key="log_rate_from_peer", rate=rate)
        self.ensure_audio()
        self.engine.configure_wire(rate, frame_ms, self.settings.jitter_ms)
        self.engine.ring_mode = audiolib.RING_INCOMING
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
                "t": protocol.ACCEPT,
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
        link.send({"t": protocol.REJECT, "reason": "declined"})
        self._emit("log", key="log_rejected")
        self._finish_call(notify=False, close_delay=0.3)

    def _handle_accept(self, link: net.SignalingLink, msg: dict[str, Any]) -> None:
        with self._lock:
            if self.state != CALLING:
                return
            self.peer_name = str(msg.get("name") or self.peer_ip)[:64]
            port = int(msg.get("audio_port") or 0)
        if port <= 0:
            self._emit("log", key="log_link_lost")
            self._finish_call()
            return
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
            link.send({"t": protocol.REJECT if was_ringing else protocol.BYE})
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

    def _on_link_closed(self, link: net.SignalingLink) -> None:
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
            link.send({"t": protocol.PING, "ts": time.monotonic()})

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
    from .config import DEFAULT_WIRE_RATE, SUPPORTED_RATES

    try:
        rate = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIRE_RATE
    return rate if rate in SUPPORTED_RATES else DEFAULT_WIRE_RATE


def _clamp_frame_ms(value: Any, rate: int = 16000) -> int:
    from .config import DEFAULT_FRAME_MS, max_frame_ms

    limit = max_frame_ms(rate)
    try:
        frame_ms = int(value)
    except (TypeError, ValueError):
        return min(DEFAULT_FRAME_MS, limit)
    return max(10, min(limit, frame_ms))
