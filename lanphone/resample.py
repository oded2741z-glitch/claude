"""Streaming sample rate conversion for mono float32 audio.

USB headsets rarely run at the rate we put on the wire, so both the capture and
the playback path may need conversion.  This is a linear interpolator with a
short FIR low-pass in front of it when downsampling, which is plenty for speech
and costs almost nothing per frame.
"""

from __future__ import annotations

import numpy as np


class Resampler:
    def __init__(self, src_rate: int, dst_rate: int) -> None:
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self.ratio = float(src_rate) / float(dst_rate)

        self._kernel: np.ndarray | None = None
        if self.ratio > 1.05:
            # Anti-alias filter: cut everything the destination rate cannot hold.
            taps = int(round(self.ratio)) * 4 + 1
            kernel = np.hanning(taps + 2)[1:-1].astype(np.float32)
            self._kernel = kernel / float(kernel.sum())
        self.reset()

    @property
    def passthrough(self) -> bool:
        return self.src_rate == self.dst_rate

    def reset(self) -> None:
        self._buf = np.zeros(1, dtype=np.float32)
        self._pos = 0.0
        if self._kernel is not None:
            self._fir_state = np.zeros(len(self._kernel) - 1, dtype=np.float32)

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self.passthrough:
            return x.copy()
        if len(x) == 0:
            return np.zeros(0, dtype=np.float32)

        if self._kernel is not None:
            padded = np.concatenate([self._fir_state, x])
            if len(padded) < len(self._kernel):
                self._fir_state = padded
                return np.zeros(0, dtype=np.float32)
            self._fir_state = padded[-(len(self._kernel) - 1):].copy()
            x = np.convolve(padded, self._kernel, mode="valid").astype(np.float32)

        buf = np.concatenate([self._buf, x])
        last = len(buf) - 1
        span = last - self._pos
        if span < 0:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)

        count = int(np.floor(span / self.ratio)) + 1
        idx = self._pos + self.ratio * np.arange(count, dtype=np.float64)
        i0 = idx.astype(np.int64)
        i1 = np.minimum(i0 + 1, last)
        frac = (idx - i0).astype(np.float32)
        out = buf[i0] * (1.0 - frac) + buf[i1] * frac

        keep_from = int(i0[-1])
        self._buf = buf[keep_from:].copy()
        self._pos = float(self._pos + self.ratio * count - keep_from)
        return out.astype(np.float32, copy=False)


def make_resampler(src_rate: int, dst_rate: int) -> Resampler | None:
    """``None`` when no conversion is needed, so callers can skip the work."""
    if int(src_rate) == int(dst_rate):
        return None
    return Resampler(src_rate, dst_rate)
