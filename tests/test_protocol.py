import unittest

import numpy as np

from lanphone import protocol
from lanphone.config import (
    DEFAULT_LANGUAGE,
    MAX_AUDIO_PAYLOAD,
    MAX_SAVED_PEERS,
    SIGNALING_PORT,
    SUPPORTED_RATES,
    Settings,
    max_frame_ms,
)


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
        self.assertEqual(settings.language, DEFAULT_LANGUAGE)
        self.assertTrue(settings.display_name)

    def test_the_interface_defaults_to_english(self):
        self.assertEqual(DEFAULT_LANGUAGE, "en")
        self.assertEqual(Settings().language, "en")
        # Hebrew stays available and is kept when it was chosen.
        self.assertEqual(Settings(language="he").language, "he")

    def test_frame_samples(self):
        self.assertEqual(Settings(wire_rate=16000, frame_ms=20).frame_samples, 320)


class SavedAddressTest(unittest.TestCase):
    def test_addresses_are_kept_most_recent_first(self):
        settings = Settings()
        self.assertTrue(settings.remember_peer("192.168.1.5", 50505, "PC-A"))
        self.assertTrue(settings.remember_peer("192.168.1.9", 50505, "PC-B"))
        self.assertEqual([p["ip"] for p in settings.saved_peers], ["192.168.1.9", "192.168.1.5"])
        self.assertEqual(settings.last_peer_ip, "192.168.1.9")

    def test_calling_the_same_address_again_moves_it_up_without_duplicating(self):
        settings = Settings()
        settings.remember_peer("10.0.0.1", 50505, "A")
        settings.remember_peer("10.0.0.2", 50505, "B")
        self.assertTrue(settings.remember_peer("10.0.0.1"))
        self.assertEqual([p["ip"] for p in settings.saved_peers], ["10.0.0.1", "10.0.0.2"])
        # A later call with no name must not wipe the name we already had.
        self.assertEqual(settings.saved_peers[0]["name"], "A")

    def test_repeating_the_top_entry_reports_no_change(self):
        settings = Settings()
        settings.remember_peer("10.0.0.1", 50505, "A")
        self.assertFalse(settings.remember_peer("10.0.0.1", 50505, "A"))
        self.assertTrue(settings.remember_peer("10.0.0.1", 50505, "A2"))

    def test_blank_addresses_are_ignored(self):
        settings = Settings()
        self.assertFalse(settings.remember_peer("   "))
        self.assertFalse(settings.remember_peer(""))
        self.assertEqual(settings.saved_peers, [])

    def test_the_list_is_capped(self):
        settings = Settings()
        for index in range(MAX_SAVED_PEERS + 8):
            settings.remember_peer(f"10.0.0.{index}")
        self.assertEqual(len(settings.saved_peers), MAX_SAVED_PEERS)
        self.assertEqual(settings.saved_peers[0]["ip"], f"10.0.0.{MAX_SAVED_PEERS + 7}")

    def test_forget(self):
        settings = Settings()
        settings.remember_peer("10.0.0.1")
        self.assertTrue(settings.forget_peer("10.0.0.1"))
        self.assertFalse(settings.forget_peer("10.0.0.1"))
        self.assertEqual(settings.saved_peers, [])

    def test_port_is_remembered_per_address(self):
        settings = Settings()
        settings.remember_peer("10.0.0.1", 50512)
        self.assertEqual(settings.saved_port("10.0.0.1"), 50512)
        self.assertEqual(settings.saved_port("10.0.0.99"), SIGNALING_PORT)

    def test_survives_a_round_trip_through_json(self):
        import json
        from dataclasses import asdict

        settings = Settings()
        settings.remember_peer("192.168.1.5", 50505, "PC-A")
        restored = Settings(**json.loads(json.dumps(asdict(settings))))
        self.assertEqual(restored.saved_peers, settings.saved_peers)

    def test_a_hand_edited_file_cannot_break_the_app(self):
        settings = Settings(
            saved_peers=[
                {"ip": "10.0.0.1", "port": "nonsense", "name": None},
                {"ip": "  "},
                {"nope": 1},
                "not a dict",
                {"ip": "10.0.0.1"},  # duplicate
                {"ip": "10.0.0.2", "port": 99999},
            ]
        )
        self.assertEqual([p["ip"] for p in settings.saved_peers], ["10.0.0.1", "10.0.0.2"])
        self.assertTrue(all(0 < p["port"] < 65536 for p in settings.saved_peers))
        self.assertTrue(all(isinstance(p["name"], str) for p in settings.saved_peers))

    def test_saved_peers_are_not_shared_between_instances(self):
        first, second = Settings(), Settings()
        first.remember_peer("10.0.0.1")
        self.assertEqual(second.saved_peers, [])


if __name__ == "__main__":
    unittest.main()
