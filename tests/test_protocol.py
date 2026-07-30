import unittest

import numpy as np

from lanphone import protocol
from lanphone.config import MAX_AUDIO_PAYLOAD, SUPPORTED_RATES, Settings, max_frame_ms


class TestAudioPackets(unittest.TestCase):
    def test_round_trip(self):
        samples = np.linspace(-1.0, 1.0, 320, dtype=np.float32)
        packet = protocol.encode_audio(0xDEADBEEF, 7, samples)
        call_id, seq, flags, decoded = protocol.decode_audio(packet)
        self.assertEqual(call_id, 0xDEADBEEF)
        self.assertEqual(seq, 7)
        self.assertEqual(flags, 0)
        self.assertEqual(len(decoded), len(samples))
        self.assertLess(float(np.max(np.abs(decoded - samples))), 1e-3)

    def test_silence_flag(self):
        packet = protocol.encode_audio(1, 1, np.zeros(160, dtype=np.float32), silence=True)
        _, _, flags, _ = protocol.decode_audio(packet)
        self.assertTrue(flags & protocol.FLAG_SILENCE)

    def test_clips_out_of_range_input(self):
        samples = np.array([-4.0, 4.0], dtype=np.float32)
        _, _, _, decoded = protocol.decode_audio(protocol.encode_audio(1, 0, samples))
        self.assertLessEqual(float(np.max(decoded)), 1.0)
        self.assertGreaterEqual(float(np.min(decoded)), -1.0)

    def test_sequence_and_call_id_wrap(self):
        packet = protocol.encode_audio(2**32 + 5, 2**32 + 3, np.zeros(8, dtype=np.float32))
        call_id, seq, _, _ = protocol.decode_audio(packet)
        self.assertEqual((call_id, seq), (5, 3))

    def test_rejects_junk(self):
        for junk in (b"", b"xx", b"NOPE" + b"\x00" * 12, protocol.MAGIC + b"\x00" * 3):
            with self.assertRaises(protocol.ProtocolError):
                protocol.decode_audio(junk)

    def test_rejects_truncated_payload(self):
        packet = protocol.encode_audio(1, 1, np.zeros(160, dtype=np.float32))
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_audio(packet[:-4])

    def test_every_reachable_setting_fits_in_one_datagram(self):
        """No combination the settings allow may need IP fragmentation."""
        for rate in SUPPORTED_RATES:
            for frame_ms in (10, 20, 30, 40):
                settings = Settings(wire_rate=rate, frame_ms=frame_ms)
                packet = protocol.encode_audio(
                    1, 1, np.zeros(settings.frame_samples, dtype=np.float32)
                )
                self.assertLessEqual(
                    len(packet),
                    MAX_AUDIO_PAYLOAD + protocol.AUDIO_HEADER_SIZE,
                    f"{rate} Hz / {settings.frame_ms} ms produces {len(packet)} bytes",
                )
                self.assertLessEqual(len(packet), 1472)  # MTU 1500 minus IP+UDP

    def test_frame_length_is_capped_per_rate(self):
        self.assertEqual(max_frame_ms(16000), 40)
        self.assertEqual(max_frame_ms(48000), 10)
        self.assertEqual(Settings(wire_rate=48000, frame_ms=40).frame_ms, 10)
        self.assertEqual(Settings(wire_rate=16000, frame_ms=40).frame_ms, 40)


class TestControlMessages(unittest.TestCase):
    def test_round_trip_with_unicode(self):
        msg = {"t": protocol.INVITE, "name": "המחשב של עודד", "call_id": 42}
        line = protocol.encode_message(msg)
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(protocol.decode_message(line.strip()), msg)

    def test_rejects_non_objects(self):
        for junk in (b"[]", b'"hi"', b"{}", b"not json", b'{"x":1}'):
            with self.assertRaises(protocol.ProtocolError):
                protocol.decode_message(junk)


class TestSettings(unittest.TestCase):
    def test_clamps_bad_values(self):
        settings = Settings(wire_rate=12345, frame_ms=500, jitter_ms=1, volume=9.0, language="de")
        self.assertIn(settings.wire_rate, SUPPORTED_RATES)
        self.assertEqual(settings.frame_ms, max_frame_ms(settings.wire_rate))
        self.assertEqual(settings.jitter_ms, 20)
        self.assertEqual(settings.volume, 2.0)
        self.assertEqual(settings.language, "he")
        self.assertTrue(settings.display_name)

    def test_frame_samples(self):
        self.assertEqual(Settings(wire_rate=16000, frame_ms=20).frame_samples, 320)


if __name__ == "__main__":
    unittest.main()
