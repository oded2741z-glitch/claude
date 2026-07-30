"""Sound device handling and the capture/playback engine.

The interesting part here is device churn: a USB headset is usually plugged in
*after* the program is already running, and PortAudio caches its device list at
start-up.  ``refresh_devices`` re-initialises PortAudio so newly connected
devices show up, and the engine can be restarted on the new selection without
restarting the app.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np

from .jitter import JitterBuffer
from .resample import make_resampler

_sd = None


def backend():
    """Import sounddevice on first use, with a readable error if it is missing."""
    global _sd
    if _sd is None:
        try:
            import sounddevice  # noqa: PLC0415  (deliberately lazy)
        except OSError as exc:  # PortAudio library missing
            raise AudioUnavailable(f"PortAudio not available: {exc}") from exc
        except ImportError as exc:
            raise AudioUnavailable(
                "The 'sounddevice' package is missing. Install it with: pip install sounddevice"
            ) from exc
        _sd = sounddevice
    return _sd


class AudioUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------
# devices
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    hostapi: str
    channels: int
    samplerate: float
    is_input: bool

    @property
    def key(self) -> str:
        """Stable identity across replugs (the index is not stable)."""
        return f"{self.name}|{self.hostapi}"

    @property
    def label(self) -> str:
        return f"{self.name}  [{self.hostapi}]"


def refresh_devices() -> None:
    """Make PortAudio re-scan the system for devices."""
    sd = backend()
    try:
        sd._terminate()
    except Exception:  # noqa: BLE001 - never let a rescan kill the app
        pass
    sd._initialize()


def list_devices() -> tuple[list[DeviceInfo], list[DeviceInfo]]:
    """Return (inputs, outputs) as currently known to PortAudio."""
    sd = backend()
    try:
        hostapis = sd.query_hostapis()
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(str(exc)) from exc

    default_in, default_out = _default_indexes(sd)
    inputs: list[DeviceInfo] = []
    outputs: list[DeviceInfo] = []
    for index, dev in enumerate(devices):
        api = ""
        try:
            api = hostapis[dev["hostapi"]]["name"]
        except (IndexError, KeyError, TypeError):
            pass
        name = str(dev.get("name", "")).strip() or f"device {index}"
        rate = float(dev.get("default_samplerate") or 48000)
        if int(dev.get("max_input_channels", 0)) > 0:
            inputs.append(
                DeviceInfo(index, name, api, int(dev["max_input_channels"]), rate, True)
            )
        if int(dev.get("max_output_channels", 0)) > 0:
            outputs.append(
                DeviceInfo(index, name, api, int(dev["max_output_channels"]), rate, False)
            )

    inputs.sort(key=lambda d: (d.index != default_in, d.hostapi, d.name.lower()))
    outputs.sort(key=lambda d: (d.index != default_out, d.hostapi, d.name.lower()))
    return inputs, outputs


def _default_indexes(sd) -> tuple[int, int]:
    try:
        default = sd.default.device
        return int(default[0]), int(default[1])
    except Exception:  # noqa: BLE001
        return -1, -1


def find_device(devices: Iterable[DeviceInfo], key_or_name: str) -> DeviceInfo | None:
    """Resolve a remembered device, matching on the full key then on the name."""
    if not key_or_name:
        return None
    devices = list(devices)
    for dev in devices:
        if dev.key == key_or_name:
            return dev
    name = key_or_name.split("|")[0]
    for dev in devices:
        if dev.name == name:
            return dev
    return None


_HEADSET_HINTS = ("headset", "headphone", "usb", "earphone", "airpods", "buds", "אוזני")


def looks_like_headset(dev: DeviceInfo) -> bool:
    lowered = dev.name.lower()
    return any(hint in lowered for hint in _HEADSET_HINTS)


def pick_default(devices: list[DeviceInfo], previous: list[DeviceInfo] | None = None) -> DeviceInfo | None:
    """Choose a device: a newly appeared headset first, else the system default."""
    if not devices:
        return None
    if previous is not None:
        known = {dev.key for dev in previous}
        fresh = [dev for dev in devices if dev.key not in known]
        for dev in fresh:
            if looks_like_headset(dev):
                return dev
        if fresh:
            return fresh[0]
    for dev in devices:
        if looks_like_headset(dev):
            return dev
    return devices[0]


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------
RING_INCOMING = "incoming"
RING_OUTGOING = "outgoing"


class AudioEngine:
    """Captures from the microphone, plays back what arrives from the network."""

    def __init__(
        self,
        wire_rate: int,
        frame_ms: int,
        jitter_ms: int,
        on_frame: Callable[[np.ndarray, bool], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._on_frame = on_frame
        self._on_error = on_error
        self.wire_rate = int(wire_rate)
        self.frame_ms = int(frame_ms)
        self.jitter_ms = int(jitter_ms)

        self.muted = False
        self.monitor = False
        self.volume = 1.0
        self.mic_gain = 1.0
        self.transmitting = False
        self.ring_mode: str | None = None

        self.in_level = 0.0
        self.out_level = 0.0
        self.input_device: DeviceInfo | None = None
        self.output_device: DeviceInfo | None = None
        self.input_rate = 0
        self.output_rate = 0
        self.xruns = 0

        self.jitter = JitterBuffer(self.frame_samples, *self._jitter_depths())

        self._in_stream = None
        self._out_stream = None
        self._in_acc = np.zeros(0, dtype=np.float32)
        self._out_pending = np.zeros(0, dtype=np.float32)
        self._in_rs = None
        self._out_rs = None
        self._monitor_frames: list[np.ndarray] = []
        self._ring_pos = 0
        self._watchdog: threading.Thread | None = None
        self._stop_watchdog = threading.Event()
        self._reported_failure = False
        # While this is set the callbacks return immediately without taking the
        # lock.  Two reasons: they must not run against half-configured
        # resamplers, and PortAudio's abort() waits for the callback to return -
        # if that callback were blocked on our lock we would deadlock.
        self._configuring = True

    # -- configuration ---------------------------------------------------
    @property
    def frame_samples(self) -> int:
        return max(1, int(self.wire_rate * self.frame_ms / 1000))

    def _jitter_depths(self) -> tuple[int, int]:
        target = max(2, int(round(self.jitter_ms / max(1, self.frame_ms))))
        return target, max(target + 2, target * 4)

    def configure_wire(self, wire_rate: int, frame_ms: int, jitter_ms: int) -> None:
        """Change the network audio format (also used to follow the caller)."""
        with self._lock:
            if (wire_rate, frame_ms, jitter_ms) == (self.wire_rate, self.frame_ms, self.jitter_ms):
                return
            self.wire_rate = int(wire_rate)
            self.frame_ms = int(frame_ms)
            self.jitter_ms = int(jitter_ms)
            self.jitter.reconfigure(self.frame_samples, *self._jitter_depths())
            self._in_acc = np.zeros(0, dtype=np.float32)
            self._out_pending = np.zeros(0, dtype=np.float32)
            self._monitor_frames.clear()
            self._rebuild_resamplers()

    def _rebuild_resamplers(self) -> None:
        self._in_rs = make_resampler(self.input_rate, self.wire_rate) if self.input_rate else None
        self._out_rs = make_resampler(self.wire_rate, self.output_rate) if self.output_rate else None

    # -- lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._in_stream is not None or self._out_stream is not None

    def start(self, input_device: DeviceInfo | None, output_device: DeviceInfo | None) -> None:
        """Open the streams.  Raises AudioUnavailable if neither can be opened."""
        self.stop()
        sd = backend()
        errors: list[str] = []
        in_stream = out_stream = None
        in_rate = out_rate = 0

        # Streams are opened outside the lock (opening can block for a while)
        # and started only once the engine is configured for their rates.
        if input_device is not None:
            try:
                in_stream, in_rate = self._open_input(sd, input_device)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{input_device.name}: {exc}")
        if output_device is not None:
            try:
                out_stream, out_rate = self._open_output(sd, output_device)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{output_device.name}: {exc}")

        with self._lock:
            self._reported_failure = False
            self._in_stream, self.input_rate = in_stream, in_rate
            self._out_stream, self.output_rate = out_stream, out_rate
            self.input_device = input_device if in_stream is not None else None
            self.output_device = output_device if out_stream is not None else None
            self._in_acc = np.zeros(0, dtype=np.float32)
            self._out_pending = np.zeros(0, dtype=np.float32)
            self._monitor_frames.clear()
            self._rebuild_resamplers()
            self.jitter.reset()

        if not self.running:
            raise AudioUnavailable("; ".join(errors) or "no audio device selected")

        self._configuring = False
        for stream in (in_stream, out_stream):
            if stream is not None:
                try:
                    stream.start()
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
        if errors and self._on_error is not None:
            self._on_error(AudioUnavailable("; ".join(errors)))

        self._stop_watchdog.clear()
        self._watchdog = threading.Thread(target=self._watch_streams, daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        self._configuring = True
        self._stop_watchdog.set()
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=1.0)

        with self._lock:
            streams = (self._in_stream, self._out_stream)
            self._in_stream = self._out_stream = None
            self.input_device = self.output_device = None
            self.input_rate = self.output_rate = 0
            self.in_level = self.out_level = 0.0

        # Closing has to happen with the lock released: abort() waits for the
        # audio callback to return, and the callback wants the same lock.
        for stream in streams:
            if stream is None:
                continue
            try:
                stream.abort(ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            try:
                stream.close(ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    def begin_call(self) -> None:
        with self._lock:
            self.jitter.reset()
            self.jitter.reset_stats()
            self._out_pending = np.zeros(0, dtype=np.float32)
            self.ring_mode = None
            self.transmitting = True

    def end_call(self) -> None:
        with self._lock:
            self.transmitting = False
            self.ring_mode = None
            self.jitter.reset()

    def push_incoming(self, seq: int, samples: np.ndarray) -> None:
        self.jitter.push(seq, samples)

    # -- stream setup ----------------------------------------------------
    def _open_stream(self, factory, dev: DeviceInfo, callback, what: str):
        """Open a device, preferring the wire rate but accepting what it offers."""
        last: Exception | None = None
        for rate in _candidate_rates(self.wire_rate, dev.samplerate):
            for channels in _candidate_channels(dev.channels):
                try:
                    stream = factory(
                        device=dev.index,
                        channels=channels,
                        samplerate=rate,
                        dtype="float32",
                        blocksize=int(rate * self.frame_ms / 1000),
                        callback=callback,
                    )
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    continue
                return stream, int(stream.samplerate)
        raise AudioUnavailable(str(last or f"cannot open {what}"))

    def _open_input(self, sd, dev: DeviceInfo):
        return self._open_stream(sd.InputStream, dev, self._input_callback, "microphone")

    def _open_output(self, sd, dev: DeviceInfo):
        return self._open_stream(sd.OutputStream, dev, self._output_callback, "speaker")

    # -- audio callbacks -------------------------------------------------
    def _input_callback(self, indata, frames, time_info, status) -> None:
        if self._configuring:
            return
        if status:
            self.xruns += 1
        try:
            with self._lock:
                mono = np.asarray(indata, dtype=np.float32)
                mono = mono.mean(axis=1) if mono.ndim > 1 and mono.shape[1] > 1 else mono.reshape(-1)
                if self.muted:
                    mono = np.zeros(len(mono), dtype=np.float32)
                elif self.mic_gain != 1.0:
                    mono = np.clip(mono * self.mic_gain, -1.0, 1.0)
                self.in_level = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0

                if self._in_rs is not None:
                    mono = self._in_rs.process(mono)
                if len(mono):
                    self._in_acc = np.concatenate([self._in_acc, mono])

                size = self.frame_samples
                ready: list[np.ndarray] = []
                while len(self._in_acc) >= size:
                    ready.append(self._in_acc[:size].copy())
                    self._in_acc = self._in_acc[size:]
                monitor = self.monitor
                transmitting = self.transmitting
                muted = self.muted
                for frame in ready:
                    if monitor:
                        self._monitor_frames.append(frame)
                        if len(self._monitor_frames) > 8:
                            del self._monitor_frames[0]
            if transmitting:
                for frame in ready:
                    self._on_frame(frame, muted)
        except Exception as exc:  # noqa: BLE001 - a raise here would kill the stream
            self._fail(exc)

    def _output_callback(self, outdata, frames, time_info, status) -> None:
        if self._configuring:
            outdata.fill(0)
            return
        if status:
            self.xruns += 1
        try:
            with self._lock:
                while len(self._out_pending) < frames:
                    wire = self._next_wire_frame()
                    if self._out_rs is not None:
                        wire = self._out_rs.process(wire)
                    if len(wire):
                        self._out_pending = np.concatenate([self._out_pending, wire])
                chunk = self._out_pending[:frames]
                self._out_pending = self._out_pending[frames:]
                if self.volume != 1.0:
                    chunk = chunk * self.volume
                chunk = np.clip(chunk, -1.0, 1.0)
                self.out_level = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
            outdata[:] = chunk.reshape(-1, 1)
        except Exception as exc:  # noqa: BLE001
            outdata.fill(0)
            self._fail(exc)

    def _next_wire_frame(self) -> np.ndarray:
        """One frame of playback audio at the wire rate (lock already held)."""
        size = self.frame_samples
        if self.ring_mode is not None:
            return self._ring_frame(size, self.ring_mode)
        frame = np.zeros(size, dtype=np.float32)
        if self.transmitting:
            frame, _ = self.jitter.pop()
        if self.monitor and self._monitor_frames:
            frame = np.clip(frame + self._monitor_frames.pop(0), -1.0, 1.0)
        return frame

    def _ring_frame(self, size: int, mode: str) -> np.ndarray:
        t = (np.arange(size, dtype=np.float64) + self._ring_pos) / float(self.wire_rate)
        self._ring_pos += size
        if mode == RING_OUTGOING:
            period, bursts, tones, level = 4.0, ((0.0, 1.0),), (425.0,), 0.10
        else:
            period, bursts, tones, level = 3.0, ((0.0, 0.4), (0.6, 1.0)), (440.0, 554.0), 0.28
        phase = np.mod(t, period)
        env = np.zeros_like(phase)
        for start, end in bursts:
            env = np.maximum(env, _taper(phase, start, end))
        wave = np.zeros_like(phase)
        for freq in tones:
            wave += np.sin(2.0 * np.pi * freq * t)
        wave /= len(tones)
        return (wave * env * level).astype(np.float32)

    # -- failure handling ------------------------------------------------
    def _fail(self, exc: Exception) -> None:
        if self._reported_failure:
            return
        self._reported_failure = True
        if self._on_error is not None:
            self._on_error(exc)

    def _watch_streams(self) -> None:
        """Notice a device that was unplugged and report it once."""
        while not self._stop_watchdog.wait(1.0):
            dead = []
            with self._lock:
                for name, stream in (("input", self._in_stream), ("output", self._out_stream)):
                    if stream is None:
                        continue
                    try:
                        alive = bool(stream.active)
                    except Exception:  # noqa: BLE001
                        alive = False
                    if not alive:
                        dead.append(name)
            if dead:
                self._fail(AudioUnavailable(f"audio stream stopped ({', '.join(dead)})"))
                return


def _taper(phase: np.ndarray, start: float, end: float, ramp: float = 0.02) -> np.ndarray:
    """A 0..1 envelope over [start, end] with short linear edges (no clicks)."""
    return np.clip(np.minimum((phase - start) / ramp, (end - phase) / ramp), 0.0, 1.0)


def _candidate_rates(wire_rate: int, device_rate: float) -> list[int]:
    rates = [int(wire_rate), int(device_rate or 0), 48000, 44100, 32000, 16000]
    seen: set[int] = set()
    out: list[int] = []
    for rate in rates:
        if rate > 0 and rate not in seen:
            seen.add(rate)
            out.append(rate)
    return out


def _candidate_channels(max_channels: int) -> list[int]:
    if max_channels <= 1:
        return [1]
    return [1, min(2, max_channels)]


def wait_for_device(timeout: float = 5.0) -> None:
    """Small helper used by the CLI smoke test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inputs, outputs = list_devices()
        if inputs and outputs:
            return
        time.sleep(0.5)
