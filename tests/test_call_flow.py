"""End-to-end call flow over the loopback interface (no sound card needed)."""

import time
import unittest
from unittest import mock

import numpy as np

from lanphone import net
from lanphone import phone as phonelib
from lanphone.config import Settings
from lanphone.net import (
    AudioTransport,
    Discovery,
    SignalingServer,
    bind_with_fallback,
    diagnose_target,
    is_valid_host,
)
from lanphone.phone import IDLE, IN_CALL, RINGING, Phone

BASE_PORT = 51500


def wait_until(predicate, timeout=6.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, **data):
        self.events.append((kind, data))

    def keys(self):
        return [d.get("key") for k, d in self.events if k == "log"]


class CallFlowTest(unittest.TestCase):
    """Two phones on 127.0.0.1 - the same code path as two PCs on a LAN."""

    port_offset = 0

    def make_phone(self, name, auto_answer=False):
        CallFlowTest.port_offset += 10
        base = BASE_PORT + CallFlowTest.port_offset
        settings = Settings(display_name=name, auto_answer=auto_answer)
        recorder = Recorder()
        # Discovery is given a private port per phone so parallel test runs and
        # the real app never disturb each other.
        ph = Phone(
            settings,
            recorder,
            signaling_port=base,
            audio_port=base + 1,
            discovery_port=base + 2,
        )
        ph.start()
        self.addCleanup(ph.stop)
        return ph, recorder

    def connect(self, auto_answer=False):
        caller, caller_log = self.make_phone("caller")
        callee, callee_log = self.make_phone("callee", auto_answer=auto_answer)
        caller.place_call("127.0.0.1", callee.signaling.port)
        return caller, callee, caller_log, callee_log

    def test_answered_call_carries_audio_both_ways(self):
        caller, callee, caller_log, callee_log = self.connect()
        self.assertTrue(wait_until(lambda: callee.state == RINGING), "callee never rang")
        self.assertEqual(callee.peer_name, "caller")
        callee.answer()
        self.assertTrue(wait_until(lambda: caller.state == IN_CALL), "caller not in call")
        self.assertTrue(wait_until(lambda: callee.state == IN_CALL), "callee not in call")
        self.assertEqual(caller.peer_name, "callee")

        size = caller.settings.frame_samples
        tone = np.full(size, 0.25, dtype=np.float32)
        for _ in range(5):
            caller.transport.send_frame(tone)
            callee.transport.send_frame(-tone)
        self.assertTrue(wait_until(lambda: callee.engine.jitter.received >= 5))
        self.assertTrue(wait_until(lambda: caller.engine.jitter.received >= 5))

        frame, real = callee.engine.jitter.pop()
        self.assertTrue(real)
        self.assertAlmostEqual(float(frame[0]), 0.25, places=3)

        caller.hangup()
        self.assertTrue(wait_until(lambda: callee.state == IDLE), "callee did not hang up")
        self.assertTrue(wait_until(lambda: caller.state == IDLE))
        self.assertIn("log_peer_hangup", callee_log.keys())

    def test_auto_answer(self):
        caller, callee, _, _ = self.connect(auto_answer=True)
        self.assertTrue(wait_until(lambda: caller.state == IN_CALL), "auto answer failed")
        self.assertTrue(wait_until(lambda: callee.state == IN_CALL))

    def test_rejected_call(self):
        caller, callee, caller_log, _ = self.connect()
        self.assertTrue(wait_until(lambda: callee.state == RINGING))
        callee.reject()
        self.assertTrue(wait_until(lambda: caller.state == IDLE), "caller not released")
        self.assertTrue(wait_until(lambda: callee.state == IDLE))
        self.assertIn("log_rejected", caller_log.keys())

    def test_second_caller_gets_busy(self):
        caller, callee, _, _ = self.connect()
        self.assertTrue(wait_until(lambda: callee.state == RINGING))
        callee.answer()
        self.assertTrue(wait_until(lambda: callee.state == IN_CALL))

        third, third_log = self.make_phone("third")
        third.place_call("127.0.0.1", callee.signaling.port)
        self.assertTrue(wait_until(lambda: "log_busy" in third_log.keys()), "no busy reply")
        self.assertTrue(wait_until(lambda: third.state == IDLE))
        self.assertEqual(callee.state, IN_CALL)  # existing call untouched

    def test_both_sides_remember_each_other(self):
        caller, callee, caller_log, callee_log = self.connect(auto_answer=True)
        self.assertTrue(wait_until(lambda: caller.state == IN_CALL))

        # The caller stored the address it dialled, with the answered name.
        saved = caller.settings.saved_peers
        self.assertEqual([p["ip"] for p in saved], ["127.0.0.1"])
        self.assertEqual(saved[0]["name"], "callee")
        self.assertEqual(saved[0]["port"], callee.signaling.port)

        # The callee stored the caller, including the port to call it back on.
        saved = callee.settings.saved_peers
        self.assertEqual([p["ip"] for p in saved], ["127.0.0.1"])
        self.assertEqual(saved[0]["name"], "caller")
        self.assertEqual(saved[0]["port"], caller.signaling.port)

        # The interface is told to persist the list.
        self.assertIn("saved_peers", [kind for kind, _ in caller_log.events])
        self.assertIn("saved_peers", [kind for kind, _ in callee_log.events])

    def test_an_address_is_saved_even_when_the_call_fails(self):
        caller, caller_log = self.make_phone("caller")
        caller.place_call("192.0.2.99", 50505)
        self.assertTrue(wait_until(lambda: caller.state == IDLE))
        self.assertEqual([p["ip"] for p in caller.settings.saved_peers], ["192.0.2.99"])

    def test_forgetting_an_address(self):
        caller, caller_log = self.make_phone("caller")
        caller.remember_peer("10.1.1.1", 50505, "PC")
        self.assertTrue(caller.forget_peer("10.1.1.1"))
        self.assertEqual(caller.settings.saved_peers, [])
        self.assertIn("log_peer_forgotten", caller_log.keys())
        self.assertFalse(caller.forget_peer("10.1.1.1"))

    def test_unanswered_call_gives_up(self):
        with mock.patch.object(phonelib, "PING_INTERVAL", 0.05), mock.patch.object(
            phonelib, "CALL_TIMEOUT", 0.3
        ):
            caller, callee, _, callee_log = self.connect()
            self.assertTrue(wait_until(lambda: callee.state == RINGING))
            self.assertTrue(wait_until(lambda: caller.state == IDLE), "caller kept ringing")
            self.assertTrue(wait_until(lambda: callee.state == IDLE))
            self.assertIn("log_missed", callee_log.keys())

    def test_a_later_call_is_not_killed_by_the_previous_timestamp(self):
        timeout = 1.5
        with mock.patch.object(phonelib, "PING_INTERVAL", 0.05), mock.patch.object(
            phonelib, "CALL_TIMEOUT", timeout
        ):
            caller, callee, _, _ = self.connect(auto_answer=True)
            self.assertTrue(wait_until(lambda: caller.state == IN_CALL))
            caller.hangup()
            self.assertTrue(wait_until(lambda: caller.state == IDLE))
            # Wait out the no-answer timeout: a leftover start time from the
            # first call would make the ticker cancel the second one at once.
            time.sleep(timeout + 0.1)

            caller.place_call("127.0.0.1", callee.signaling.port)
            self.assertTrue(wait_until(lambda: caller.state == IN_CALL), "second call dropped")

    def test_unreachable_target_reports_and_recovers(self):
        caller, caller_log = self.make_phone("caller")
        caller.place_call("127.0.0.1", 9)  # nothing listening there
        self.assertTrue(wait_until(lambda: "log_call_failed" in caller_log.keys()))
        self.assertTrue(wait_until(lambda: caller.state == IDLE))

    def test_a_failed_call_explains_a_wrong_subnet(self):
        caller, caller_log = self.make_phone("caller")
        caller.local_ip = "192.168.0.7"
        # 192.0.2.x is the documentation range: nothing there to connect to.
        caller.place_call("192.0.2.11", 50505)
        self.assertTrue(wait_until(lambda: "log_call_failed" in caller_log.keys(), timeout=15))
        self.assertIn("log_why_other_subnet", caller_log.keys())
        # The message names both addresses, which is the whole point of it.
        entry = [d for k, d in caller_log.events if d.get("key") == "log_why_other_subnet"][0]
        self.assertEqual(entry["ip"], "192.0.2.11")
        self.assertEqual(entry["local"], "192.168.0.7")

    def test_caller_rate_is_adopted_by_callee(self):
        caller, caller_log = self.make_phone("caller")
        callee, callee_log = self.make_phone("callee", auto_answer=True)
        caller.settings.wire_rate = 32000
        caller.engine.configure_wire(32000, caller.settings.frame_ms, caller.settings.jitter_ms)
        caller.place_call("127.0.0.1", callee.signaling.port)
        self.assertTrue(wait_until(lambda: callee.state == IN_CALL))
        self.assertEqual(callee.engine.wire_rate, 32000)
        self.assertEqual(callee.engine.frame_samples, caller.engine.frame_samples)
        caller.hangup()
        # ... and the callee returns to its own configured format afterwards.
        self.assertTrue(wait_until(lambda: callee.engine.wire_rate == callee.settings.wire_rate))

    def test_stale_packets_from_a_previous_call_are_ignored(self):
        caller, callee, _, _ = self.connect(auto_answer=True)
        self.assertTrue(wait_until(lambda: caller.state == IN_CALL))
        size = caller.settings.frame_samples
        caller.transport.send_frame(np.zeros(size, dtype=np.float32))
        self.assertTrue(wait_until(lambda: callee.engine.jitter.received >= 1))
        caller.hangup()
        self.assertTrue(wait_until(lambda: callee.state == IDLE))
        before = callee.engine.jitter.received
        # The transport is closed, so nothing may reach the jitter buffer.
        caller.transport.send_frame(np.zeros(size, dtype=np.float32))
        time.sleep(0.2)
        self.assertEqual(callee.engine.jitter.received, before)


class NetHelpersTest(unittest.TestCase):
    def test_port_fallback(self):
        import socket

        first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        port = bind_with_fallback(first, 51999)
        other = bind_with_fallback(second, 51999)
        self.assertEqual(port, 51999)
        self.assertNotEqual(other, port)

    def test_diagnose_unreachable_targets(self):
        # The case behind "A socket operation was attempted to an unreachable
        # network": the two machines are on different subnets.
        self.assertEqual(diagnose_target("192.168.1.11", "192.168.0.7"), net.OTHER_SUBNET)
        self.assertEqual(diagnose_target("10.0.0.5", "192.168.1.7"), net.OTHER_SUBNET)
        self.assertEqual(diagnose_target("192.168.1.11", "192.168.1.11"), net.CALLING_SELF)
        self.assertEqual(diagnose_target("192.168.1.11", "127.0.0.1"), net.NO_NETWORK)
        self.assertEqual(diagnose_target("192.168.1.11", "169.254.3.4"), net.NO_DHCP)

    def test_no_diagnosis_when_there_is_nothing_to_say(self):
        # Same subnet: the address is fine, something else is wrong.
        self.assertIsNone(diagnose_target("192.168.1.11", "192.168.1.7"))
        # A host name cannot be compared with an address.
        self.assertIsNone(diagnose_target("other-pc", "192.168.1.7"))
        self.assertIsNone(diagnose_target("192.168.1.11", ""))
        self.assertIsNone(diagnose_target("  ", "192.168.1.7"))

    def test_local_ip_is_usable(self):
        address = net.local_ip()
        self.assertTrue(is_valid_host(address))
        self.assertFalse(address.startswith("169.254."))

    def test_host_validation(self):
        for good in ("192.168.1.10", "10.0.0.1", "127.0.0.1", "pc-oded"):
            self.assertTrue(is_valid_host(good), good)
        for bad in ("", "   ", "1.2.3.4.5.", "..."):
            self.assertFalse(is_valid_host(bad), repr(bad))

    def test_discovery_finds_a_peer_on_the_same_port(self):
        seen = []
        first = Discovery(lambda: "one", 1111, lambda peers: None, port=52050)
        second = Discovery(lambda: "two", 2222, seen.append, port=52050)
        first.start()
        second.start()
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)
        if first.bind_error is not None or second.bind_error is not None:
            self.skipTest("this platform does not allow two listeners on one UDP port")
        first.announce_now()
        found = wait_until(lambda: any(p["name"] == "one" for p in second.peers), timeout=5.0)
        if not found:
            self.skipTest("UDP broadcast is not available in this environment")
        peer = [p for p in second.peers if p["name"] == "one"][0]
        self.assertEqual(peer["sig_port"], 1111)
        self.assertTrue(seen)

    def test_a_leaving_peer_disappears_at_once(self):
        first = Discovery(lambda: "alpha", 1111, lambda peers: None, port=52060)
        second = Discovery(lambda: "beta", 2222, lambda peers: None, port=52060)
        first.start()
        second.start()
        self.addCleanup(second.stop)
        if first.bind_error is not None or second.bind_error is not None:
            first.stop()
            self.skipTest("this platform does not allow two listeners on one UDP port")
        first.announce_now()
        if not wait_until(lambda: any(p["name"] == "alpha" for p in second.peers), timeout=5.0):
            first.stop()
            self.skipTest("UDP broadcast is not available in this environment")
        first.stop()  # sends a farewell instead of leaving us to time it out
        self.assertTrue(
            wait_until(lambda: not second.peers, timeout=2.0),
            "peer was still listed after it shut down",
        )

    def test_signaling_server_reports_its_port(self):
        server = SignalingServer(52100, lambda link: None)
        server.start()
        self.addCleanup(server.stop)
        self.assertGreaterEqual(server.port, 52100)

    def test_transport_ignores_frames_when_no_call_is_open(self):
        received = []
        transport = AudioTransport(52200, lambda seq, flags, samples: received.append(seq))
        transport.start()
        self.addCleanup(transport.stop)
        transport.send_frame(np.zeros(160, dtype=np.float32))
        self.assertEqual(transport.sent, 0)
        self.assertFalse(transport.active)


class ClampTest(unittest.TestCase):
    def test_rate_and_frame_clamping(self):
        self.assertEqual(phonelib._clamp_rate(48000), 48000)
        self.assertEqual(phonelib._clamp_rate(7777), 16000)
        self.assertEqual(phonelib._clamp_rate(None), 16000)
        self.assertEqual(phonelib._clamp_frame_ms(30), 30)
        self.assertEqual(phonelib._clamp_frame_ms(9999), 40)
        self.assertEqual(phonelib._clamp_frame_ms("x"), 20)


if __name__ == "__main__":
    unittest.main()
