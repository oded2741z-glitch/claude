import unittest

import numpy as np

from lanphone.jitter import JitterBuffer
from lanphone.resample import Resampler, make_resampler


class TestResampler(unittest.TestCase):
    def test_passthrough_is_skipped(self):
        self.assertIsNone(make_resampler(16000, 16000))
        self.assertIsNotNone(make_resampler(48000, 16000))

    def test_output_length_tracks_ratio(self):
        for src, dst in ((48000, 16000), (16000, 48000), (44100, 16000), (16000, 44100)):
            rs = Resampler(src, dst)
            block = int(src * 0.02)
            produced = sum(len(rs.process(np.zeros(block, dtype=np.float32))) for _ in range(50))
            expected = 50 * block * dst / src
            self.assertLess(
                abs(produced - expected),
                max(4, expected * 0.01),
                f"{src}->{dst}: produced {produced}, expected ~{expected:.0f}",
            )

    def test_preserves_a_sine_wave(self):
        rs = Resampler(48000, 16000)
        freq = 440.0
        out = []
        for block in range(40):
            t = (np.arange(480) + block * 480) / 48000.0
            out.append(rs.process(np.sin(2 * np.pi * freq * t).astype(np.float32)))
        y = np.concatenate(out)
        # Drop the filter warm-up, then check the dominant frequency survived.
        y = y[400:]
        spectrum = np.abs(np.fft.rfft(y * np.hanning(len(y))))
        peak = np.fft.rfftfreq(len(y), 1 / 16000)[int(np.argmax(spectrum))]
        self.assertLess(abs(peak - freq), 15.0)
        self.assertGreater(float(np.sqrt(np.mean(y**2))), 0.5)

    def test_upsampling_is_continuous(self):
        rs = Resampler(16000, 48000)
        ramp = np.linspace(0, 1, 320, dtype=np.float32)
        first = rs.process(ramp)
        second = rs.process(ramp + 1.0)
        joined = np.concatenate([first, second])
        # A linear interpolator must not introduce jumps at block boundaries.
        self.assertLess(float(np.max(np.abs(np.diff(joined)))), 0.05)

    def test_no_crash_on_tiny_and_empty_blocks(self):
        rs = Resampler(48000, 16000)
        self.assertEqual(len(rs.process(np.zeros(0, dtype=np.float32))), 0)
        for _ in range(200):
            rs.process(np.zeros(1, dtype=np.float32))


class TestJitterBuffer(unittest.TestCase):
    def frame(self, value):
        return np.full(160, float(value), dtype=np.float32)

    def test_waits_for_target_then_plays_in_order(self):
        jb = JitterBuffer(160, target_frames=3)
        jb.push(0, self.frame(1))
        frame, real = jb.pop()
        self.assertFalse(real)  # still filling
        jb.push(1, self.frame(2))
        jb.push(2, self.frame(3))
        for expected in (1, 2, 3):
            frame, real = jb.pop()
            self.assertTrue(real)
            self.assertAlmostEqual(float(frame[0]), expected)

    def test_reorders_late_arrivals(self):
        jb = JitterBuffer(160, target_frames=2)
        jb.push(0, self.frame(1))
        jb.push(2, self.frame(3))
        jb.push(1, self.frame(2))
        values = [float(jb.pop()[0][0]) for _ in range(3)]
        self.assertEqual(values, [1.0, 2.0, 3.0])

    def test_conceals_a_lost_frame(self):
        jb = JitterBuffer(160, target_frames=2)
        jb.push(0, self.frame(1))
        jb.push(1, self.frame(1))
        jb.push(3, self.frame(1))
        jb.pop()
        jb.pop()
        frame, real = jb.pop()  # seq 2 never arrived
        self.assertFalse(real)
        self.assertEqual(jb.lost, 1)
        self.assertLess(abs(float(frame[0])), 1.0)  # faded, not silent
        frame, real = jb.pop()
        self.assertTrue(real)

    def test_drops_backlog_instead_of_growing_latency(self):
        jb = JitterBuffer(160, target_frames=2, max_frames=5)
        for seq in range(40):
            jb.push(seq, self.frame(seq))
        self.assertLessEqual(jb.depth, 5)
        self.assertGreater(jb.dropped, 0)

    def test_ignores_frames_that_are_too_late(self):
        jb = JitterBuffer(160, target_frames=1)
        jb.push(5, self.frame(1))
        jb.pop()
        jb.push(4, self.frame(9))
        self.assertEqual(jb.late, 1)

    def test_accepts_wrong_sized_frames(self):
        jb = JitterBuffer(160, target_frames=1)
        jb.push(0, np.ones(100, dtype=np.float32))
        frame, real = jb.pop()
        self.assertTrue(real)
        self.assertEqual(len(frame), 160)

    def test_underrun_refills_before_playing_again(self):
        jb = JitterBuffer(160, target_frames=2)
        jb.push(0, self.frame(1))
        jb.push(1, self.frame(1))
        jb.pop()
        jb.pop()
        jb.pop()  # empty -> underrun
        self.assertEqual(jb.underruns, 1)
        jb.push(2, self.frame(5))
        jb.push(3, self.frame(6))
        frame, real = jb.pop()
        self.assertTrue(real)
        self.assertAlmostEqual(float(frame[0]), 5.0)


if __name__ == "__main__":
    unittest.main()
