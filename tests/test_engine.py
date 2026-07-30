"""Capture/playback engine tests with a stand-in for the sound card."""

import unittest

import numpy as np

from lanphone import audio as audiolib
from lanphone.audio import RING_INCOMING, AudioEngine, DeviceInfo, find_device, looks_like_headset, pick_default


class FakeStream:
    def __init__(self, callback, samplerate, channels, blocksize):
        self.callback = callback
        self.samplerate = samplerate
        self.channels = channels
        self.blocksize = blocksize
        self.active = False
        self.closed = False

    def start(self):
        self.active = True

    def abort(self, ignore_errors=True):
        self.active = False

    def close(self, ignore_errors=True):
        self.active = False
        self.closed = True


class FakeSoundDevice:
    """Accepts only the rates a real USB headset would offer."""

    def __init__(self, supported=(48000,)):
        self.supported = supported
        self.streams = []

    def _make(self, samplerate, channels, blocksize, callback):
        if samplerate not in self.supported:
            raise RuntimeError(f"{samplerate} Hz not supported")
        if channels > 2:
            raise RuntimeError("too many channels")
        stream = FakeStream(callback, samplerate, channels, blocksize)
        self.streams.append(stream)
        return stream

    def InputStream(self, device, channels, samplerate, dtype, blocksize, callback):  # noqa: N802
        return self._make(samplerate, channels, blocksize, callback)

    def OutputStream(self, device, channels, samplerate, dtype, blocksize, callback):  # noqa: N802
        return self._make(samplerate, channels, blocksize, callback)


MIC = DeviceInfo(3, "USB Headset Mic", "MME", 1, 48000.0, True)
SPEAKER = DeviceInfo(4, "USB Headset Out", "MME", 2, 48000.0, False)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSoundDevice()
        self._saved = audiolib._sd
        audiolib._sd = self.fake
        self.addCleanup(lambda: setattr(audiolib, "_sd", self._saved))
        self.frames = []
        self.errors = []
        self.engine = AudioEngine(16000, 20, 60, self._on_frame, self.errors.append)
        self.addCleanup(self.engine.stop)

    def _on_frame(self, frame, muted):
        self.frames.append((np.array(frame), muted))

    def start(self, mic=MIC, speaker=SPEAKER):
        self.engine.start(mic, speaker)
        self.assertTrue(self.engine.running)
        return self.fake.streams

    def feed_mic(self, blocks=1, value=0.5, block_size=960, channels=1):
        stream = self.fake.streams[0]
        for _ in range(blocks):
            data = np.full((block_size, channels), value, dtype=np.float32)
            stream.callback(data, block_size, None, None)

    def play(self, size=960, channels=2):
        outdata = np.zeros((size, channels), dtype=np.float32)
        self.fake.streams[1].callback(outdata, size, None, None)
        return outdata

    # -- device handling -------------------------------------------------
    def test_falls_back_to_a_rate_the_device_supports(self):
        self.start()
        self.assertEqual(self.engine.input_rate, 48000)  # wire rate is 16000
        self.assertEqual(self.engine.output_rate, 48000)
        self.assertFalse(self.errors)

    def test_no_resampler_when_the_device_matches_the_wire_rate(self):
        audiolib._sd = FakeSoundDevice(supported=(16000,))
        self.fake = audiolib._sd
        self.start()
        self.assertEqual(self.engine.input_rate, 16000)
        self.assertIsNone(self.engine._in_rs)
        self.assertIsNone(self.engine._out_rs)

    def test_start_raises_when_no_device_can_be_opened(self):
        audiolib._sd = FakeSoundDevice(supported=(96000,))
        with self.assertRaises(audiolib.AudioUnavailable):
            self.engine.start(MIC, SPEAKER)
        self.assertFalse(self.engine.running)

    def test_stop_closes_every_stream(self):
        streams = self.start()
        self.engine.stop()
        self.assertFalse(self.engine.running)
        self.assertTrue(all(stream.closed for stream in streams))

    # -- capture path ----------------------------------------------------
    def test_capture_produces_exact_wire_frames(self):
        self.start()
        self.engine.begin_call()
        self.feed_mic(blocks=5)
        self.assertGreaterEqual(len(self.frames), 4)  # 20 ms in, 20 ms out
        for frame, muted in self.frames:
            self.assertEqual(len(frame), self.engine.frame_samples)
            self.assertFalse(muted)
        self.assertGreater(self.engine.in_level, 0.1)

    def test_nothing_is_sent_before_the_call_starts(self):
        self.start()
        self.feed_mic(blocks=5)
        self.assertEqual(self.frames, [])

    def test_mute_sends_flagged_silence(self):
        self.start()
        self.engine.begin_call()
        self.engine.muted = True
        self.feed_mic(blocks=3)
        self.assertTrue(self.frames)
        for frame, muted in self.frames:
            self.assertTrue(muted)
            self.assertLess(float(np.max(np.abs(frame))), 1e-6)
        self.assertEqual(self.engine.in_level, 0.0)

    def test_mic_gain_is_applied_and_clipped(self):
        self.start()
        self.engine.begin_call()
        self.engine.mic_gain = 4.0
        self.feed_mic(blocks=3, value=0.5)
        peak = max(float(np.max(np.abs(f))) for f, _ in self.frames)
        self.assertLessEqual(peak, 1.0)
        self.assertGreater(peak, 0.9)

    def test_stereo_microphone_is_mixed_to_mono(self):
        self.start()
        self.engine.begin_call()
        stream = self.fake.streams[0]
        data = np.zeros((960, 2), dtype=np.float32)
        data[:, 0] = 0.8
        data[:, 1] = 0.0
        for _ in range(3):
            stream.callback(data, 960, None, None)
        peak = max(float(np.max(np.abs(f))) for f, _ in self.frames)
        self.assertAlmostEqual(peak, 0.4, delta=0.05)

    # -- playback path ---------------------------------------------------
    def test_incoming_audio_reaches_the_speaker(self):
        self.start()
        self.engine.begin_call()
        size = self.engine.frame_samples
        for seq in range(6):
            self.engine.push_incoming(seq, np.full(size, 0.4, dtype=np.float32))
        outdata = self.play()
        self.assertGreater(float(np.mean(np.abs(outdata))), 0.2)
        # Every channel of the headset gets the same mono audio.
        self.assertTrue(np.allclose(outdata[:, 0], outdata[:, 1]))
        self.assertFalse(self.errors)

    def test_volume_scales_the_output(self):
        self.start()
        self.engine.begin_call()
        size = self.engine.frame_samples
        for seq in range(12):
            self.engine.push_incoming(seq, np.full(size, 0.2, dtype=np.float32))
        loud = float(np.mean(np.abs(self.play())))
        self.engine.volume = 0.25
        for seq in range(12, 24):
            self.engine.push_incoming(seq, np.full(size, 0.2, dtype=np.float32))
        quiet = float(np.mean(np.abs(self.play())))
        self.assertLess(quiet, loud / 2)

    def test_output_is_silent_but_valid_with_no_incoming_audio(self):
        self.start()
        self.engine.begin_call()
        outdata = self.play()
        self.assertEqual(outdata.shape, (960, 2))
        self.assertLess(float(np.max(np.abs(outdata))), 1e-6)

    def test_output_never_clips(self):
        self.start()
        self.engine.begin_call()
        self.engine.volume = 2.0
        size = self.engine.frame_samples
        for seq in range(12):
            self.engine.push_incoming(seq, np.full(size, 0.9, dtype=np.float32))
        self.assertLessEqual(float(np.max(np.abs(self.play()))), 1.0)

    def test_ringtone_plays_without_any_network_audio(self):
        self.start()
        self.engine.ring_mode = RING_INCOMING
        loudest = 0.0
        for _ in range(20):  # the ring cadence has gaps, so sample a while
            loudest = max(loudest, float(np.max(np.abs(self.play()))))
        self.assertGreater(loudest, 0.05)

    def test_monitor_feeds_the_microphone_back(self):
        self.start()
        self.engine.monitor = True
        self.feed_mic(blocks=4, value=0.3)
        heard = max(float(np.max(np.abs(self.play()))) for _ in range(3))
        self.assertGreater(heard, 0.1)

    # -- reconfiguration -------------------------------------------------
    def test_configure_wire_changes_frame_size_and_survives_playback(self):
        self.start()
        self.engine.begin_call()
        self.engine.configure_wire(48000, 10, 60)
        self.assertEqual(self.engine.frame_samples, 480)
        self.assertIsNone(self.engine._in_rs)  # device is 48 kHz too now
        self.feed_mic(blocks=3)
        self.assertTrue(all(len(f) == 480 for f, _ in self.frames))
        self.play()
        self.assertFalse(self.errors)

    def test_callbacks_are_inert_while_reconfiguring(self):
        self.start()
        self.engine.begin_call()
        streams = list(self.fake.streams)
        self.engine.stop()
        streams[0].callback(np.ones((960, 1), dtype=np.float32), 960, None, None)
        outdata = np.ones((960, 2), dtype=np.float32)
        streams[1].callback(outdata, 960, None, None)
        self.assertEqual(self.frames, [])
        self.assertLess(float(np.max(np.abs(outdata))), 1e-6)

    def test_a_callback_failure_is_reported_once(self):
        self.start()
        self.engine.begin_call()
        self.fake.streams[0].callback("not audio", 960, None, None)
        self.fake.streams[0].callback("not audio", 960, None, None)
        self.assertEqual(len(self.errors), 1)


class DeviceHelpersTest(unittest.TestCase):
    def test_headset_detection(self):
        self.assertTrue(looks_like_headset(MIC))
        self.assertTrue(looks_like_headset(DeviceInfo(0, "Jabra Headphone", "MME", 1, 48000, True)))
        self.assertFalse(looks_like_headset(DeviceInfo(0, "Realtek Digital Output", "MME", 2, 48000, False)))

    def test_new_headset_wins_over_the_default(self):
        old = [DeviceInfo(0, "Realtek Microphone", "MME", 1, 48000, True)]
        now = old + [MIC]
        self.assertEqual(pick_default(now, previous=old), MIC)
        self.assertEqual(pick_default(old, previous=old), old[0])
        self.assertIsNone(pick_default([]))

    def test_devices_are_found_by_key_then_by_name(self):
        devices = [MIC, DeviceInfo(9, "Other", "MME", 1, 48000, True)]
        self.assertEqual(find_device(devices, MIC.key), MIC)
        # The index moved and the host API string changed: match on the name.
        self.assertEqual(find_device(devices, "USB Headset Mic|Windows WASAPI"), MIC)
        self.assertIsNone(find_device(devices, "Nothing Like This"))
        self.assertIsNone(find_device(devices, ""))


if __name__ == "__main__":
    unittest.main()
