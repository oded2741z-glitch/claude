"""A stand-in for the `sounddevice` module, for machines with no sound card.

Import order matters: `install()` must run before `intercom_core` is imported,
because that module binds `sd` at import time. Used by selftest.py so the two
nodes can be exercised over a real UDP path on a headless box.
"""

import sys
import threading
import time
import types
from typing import List

import numpy as np

SAMPLE_RATE = 16000
CHUNK = 512

# Everything the fake devices observed, for assertions in the tests
played: List[bytes] = []
_lock = threading.Lock()
_state = {"available": True, "initialized": 0}


class PortAudioError(Exception):
    pass


def set_available(available: bool) -> None:
    """Simulates plugging or unplugging the headphones."""
    _state["available"] = available


def _require_device() -> None:
    if not _state["available"]:
        raise PortAudioError("no device")


def check_output_settings(**_kwargs) -> None:
    _require_device()


def check_input_settings(**_kwargs) -> None:
    _require_device()


def _initialize() -> None:
    _state["initialized"] += 1


def _terminate() -> None:
    pass


class OutputStream:
    def __init__(self, **kwargs):
        _require_device()
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, block) -> None:
        _require_device()
        with _lock:
            played.append(np.asarray(block).tobytes())


class InputStream:
    def __init__(self, **kwargs):
        _require_device()
        self.kwargs = kwargs
        self.blocksize = kwargs.get("blocksize", CHUNK)
        self._phase = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, frames: int):
        _require_device()
        time.sleep(frames / SAMPLE_RATE)   # מדמה קצב זמן-אמת של כרטיס קול
        t = (np.arange(self._phase, self._phase + frames) / SAMPLE_RATE)
        self._phase += frames
        tone = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
        return tone.reshape(-1, 1), False


def query_devices(*_args, **_kwargs):
    return []


def install() -> types.ModuleType:
    """Registers this module as `sounddevice`. Call before importing the app."""
    module = sys.modules[__name__]
    sys.modules["sounddevice"] = module
    return module
