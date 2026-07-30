"""Jitter buffer for incoming audio frames.

UDP frames arrive early, late, twice or not at all.  This buffer reorders them,
holds a small backlog so playback never starves on normal network jitter, and
substitutes a fading copy of the previous frame when one is missing.
"""

from __future__ import annotations

import threading

import numpy as np


class JitterBuffer:
    def __init__(self, frame_samples: int, target_frames: int = 3, max_frames: int = 16) -> None:
        self.frame_samples = int(frame_samples)
        self.target_frames = max(1, int(target_frames))
        self.max_frames = max(self.target_frames + 1, int(max_frames))
        self._lock = threading.Lock()
        self._frames: dict[int, np.ndarray] = {}
        self._next_seq = 0
        self._playing = False
        self._last = np.zeros(self.frame_samples, dtype=np.float32)
        self._conceal_gain = 1.0
        self.received = 0
        self.lost = 0
        self.late = 0
        self.dropped = 0
        self.underruns = 0

    # -- configuration ---------------------------------------------------
    def reconfigure(self, frame_samples: int, target_frames: int, max_frames: int) -> None:
        with self._lock:
            self.frame_samples = int(frame_samples)
            self.target_frames = max(1, int(target_frames))
            self.max_frames = max(self.target_frames + 1, int(max_frames))
            self._reset_locked()

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        self._frames.clear()
        self._next_seq = 0
        self._playing = False
        self._last = np.zeros(self.frame_samples, dtype=np.float32)
        self._conceal_gain = 1.0

    def reset_stats(self) -> None:
        with self._lock:
            self.received = self.lost = self.late = self.dropped = self.underruns = 0

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._frames)

    # -- producer side ---------------------------------------------------
    def push(self, seq: int, samples: np.ndarray) -> None:
        frame = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(frame) != self.frame_samples:
            frame = _fit(frame, self.frame_samples)
        with self._lock:
            self.received += 1
            if not self._playing:
                self._frames[seq] = frame
                if len(self._frames) >= self.target_frames:
                    self._next_seq = min(self._frames)
                    self._playing = True
                return
            if seq < self._next_seq:
                self.late += 1
                return
            self._frames[seq] = frame
            while len(self._frames) > self.max_frames:
                # Running long: throw away the oldest frame so latency does not
                # keep growing (clock drift between the two machines).
                oldest = min(self._frames)
                del self._frames[oldest]
                self._next_seq = min(self._frames) if self._frames else oldest + 1
                self.dropped += 1

    # -- consumer side ---------------------------------------------------
    def pop(self) -> tuple[np.ndarray, bool]:
        """Return (frame, is_real).  Never blocks, never returns None."""
        with self._lock:
            if not self._playing:
                return np.zeros(self.frame_samples, dtype=np.float32), False

            frame = self._frames.pop(self._next_seq, None)
            if frame is not None:
                self._next_seq += 1
                self._last = frame
                self._conceal_gain = 1.0
                return frame, True

            if not self._frames:
                # Nothing at all left: wait for the buffer to refill.
                self.underruns += 1
                self._playing = False
                return self._conceal_locked(), False

            self.lost += 1
            self._next_seq += 1
            return self._conceal_locked(), False

    def _conceal_locked(self) -> np.ndarray:
        self._conceal_gain *= 0.4
        if self._conceal_gain < 0.02:
            return np.zeros(self.frame_samples, dtype=np.float32)
        return (self._last * self._conceal_gain).astype(np.float32)


def _fit(frame: np.ndarray, size: int) -> np.ndarray:
    if len(frame) > size:
        return frame[:size]
    out = np.zeros(size, dtype=np.float32)
    out[: len(frame)] = frame
    return out
