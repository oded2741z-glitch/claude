"""Choosing a headset, and noticing one that is plugged in later."""

import threading
import time
import unittest
from unittest import mock

from lanphone import audio as audiolib
from lanphone import phone as phonelib
from lanphone.audio import DeviceInfo, preferred_device
from lanphone.config import Settings
from lanphone.phone import Phone

BUILTIN_MIC = DeviceInfo(0, "Realtek Microphone Array", "MME", 2, 48000.0, True)
BUILTIN_OUT = DeviceInfo(1, "Speakers (Realtek Audio)", "MME", 2, 48000.0, False)
HEADSET_MIC = DeviceInfo(2, "Microphone (USB PnP Audio Device)", "MME", 1, 48000.0, True)
HEADSET_OUT = DeviceInfo(3, "Headset Earphone (USB PnP)", "MME", 2, 48000.0, False)
MONITOR_OUT = DeviceInfo(4, "DELL U2415 (NVIDIA High Definition Audio)", "MME", 2, 48000.0, False)


class PreferredDeviceTest(unittest.TestCase):
    def test_a_headset_that_just_appeared_wins(self):
        devices = [BUILTIN_MIC, HEADSET_MIC]
        chosen = preferred_device(devices, BUILTIN_MIC, [HEADSET_MIC], prefer_headset=True)
        self.assertEqual(chosen, HEADSET_MIC)

    def test_a_new_device_that_is_not_a_headset_does_not_steal_the_selection(self):
        devices = [BUILTIN_OUT, MONITOR_OUT]
        chosen = preferred_device(devices, BUILTIN_OUT, [MONITOR_OUT], prefer_headset=True)
        self.assertEqual(chosen, BUILTIN_OUT)

    def test_the_current_device_is_kept_when_nothing_new_arrived(self):
        devices = [BUILTIN_MIC, HEADSET_MIC]
        self.assertEqual(preferred_device(devices, BUILTIN_MIC, [], True), BUILTIN_MIC)

    def test_a_headset_is_used_at_startup_when_nothing_is_remembered(self):
        devices = [BUILTIN_MIC, HEADSET_MIC]
        self.assertEqual(preferred_device(devices, None, [], True), HEADSET_MIC)

    def test_a_remembered_device_beats_the_headset_preference(self):
        devices = [BUILTIN_MIC, HEADSET_MIC]
        chosen = preferred_device(devices, None, [], True, remembered=BUILTIN_MIC.key)
        self.assertEqual(chosen, BUILTIN_MIC)

    def test_the_preference_can_be_turned_off(self):
        devices = [BUILTIN_MIC, HEADSET_MIC]
        self.assertEqual(preferred_device(devices, BUILTIN_MIC, [HEADSET_MIC], False), BUILTIN_MIC)
        self.assertEqual(preferred_device(devices, None, [], False), BUILTIN_MIC)  # system default

    def test_a_vanished_device_falls_back(self):
        chosen = preferred_device([BUILTIN_MIC], HEADSET_MIC, [], True)
        self.assertEqual(chosen, BUILTIN_MIC)

    def test_no_devices_at_all(self):
        self.assertIsNone(preferred_device([], HEADSET_MIC, [], True))


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, **data):
        self.events.append((kind, data))

    def keys(self):
        return [d.get("key") for k, d in self.events if k == "log"]


class HotPlugTest(unittest.TestCase):
    """The phone is built but never started: no sockets, no sound card."""

    def setUp(self):
        self.log = Recorder()
        self.phone = Phone(Settings(), self.log)
        self.addCleanup(self.phone._stop_device_watch.set)

    def plug_in_headset(self):
        self.phone._apply_devices(
            [BUILTIN_MIC, HEADSET_MIC], [BUILTIN_OUT, HEADSET_OUT], announce=True
        )

    def test_a_headset_plugged_in_later_is_selected_and_logged(self):
        self.phone._apply_devices([BUILTIN_MIC], [BUILTIN_OUT], announce=False)
        self.assertEqual(self.phone.selected_input, BUILTIN_MIC)

        self.plug_in_headset()
        self.assertEqual(self.phone.selected_input, HEADSET_MIC)
        self.assertEqual(self.phone.selected_output, HEADSET_OUT)
        self.assertEqual(self.log.keys().count("log_using_device"), 2)
        # The choice is remembered for the next start.
        self.assertEqual(self.phone.settings.input_device_name, HEADSET_MIC.key)

    def test_unplugging_falls_back_to_what_is_left(self):
        self.phone._apply_devices([BUILTIN_MIC], [BUILTIN_OUT], announce=False)
        self.plug_in_headset()
        self.phone._apply_devices([BUILTIN_MIC], [BUILTIN_OUT], announce=True)
        self.assertEqual(self.phone.selected_input, BUILTIN_MIC)

    def test_nothing_is_announced_when_nothing_changed(self):
        self.phone._apply_devices([BUILTIN_MIC], [BUILTIN_OUT], announce=False)
        self.log.events.clear()
        self.phone._apply_devices([BUILTIN_MIC], [BUILTIN_OUT], announce=True)
        self.assertNotIn("log_using_device", self.log.keys())
        self.assertNotIn("log_new_device", self.log.keys())

    def test_the_watcher_rescans_when_the_system_reports_a_change(self):
        """The Windows path: a cheap counter changes, so we rescan."""
        tokens = iter([(1, 1), (1, 1), (2, 2), (2, 2), (2, 2), (2, 2)])
        scans = []

        def fake_token():
            try:
                return next(tokens)
            except StopIteration:
                return (2, 2)

        def fake_scan():
            scans.append(True)
            return [BUILTIN_MIC, HEADSET_MIC], [BUILTIN_OUT, HEADSET_OUT]

        self.phone._apply_devices([BUILTIN_MIC], [BUILTIN_OUT], announce=False)
        with mock.patch.object(phonelib, "DEVICE_POLL_INTERVAL", 0.02), mock.patch.object(
            audiolib, "device_change_token", fake_token
        ), mock.patch.object(audiolib, "scan_devices", fake_scan):
            watcher = threading.Thread(target=self.phone._watch_devices, daemon=True)
            watcher.start()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and self.phone.selected_input != HEADSET_MIC:
                time.sleep(0.02)
            self.phone._stop_device_watch.set()
            watcher.join(timeout=2.0)

        self.assertTrue(scans, "the watcher never rescanned")
        self.assertEqual(self.phone.selected_input, HEADSET_MIC)

    def test_the_watcher_without_a_cheap_signal_compares_the_lists(self):
        """Linux/macOS path: rescan while idle and look for a difference."""
        state = {"devices": ([BUILTIN_MIC], [BUILTIN_OUT])}

        self.phone._apply_devices(*state["devices"], announce=False)
        with mock.patch.object(phonelib, "DEVICE_POLL_INTERVAL", 0.02), mock.patch.object(
            audiolib, "device_change_token", lambda: None
        ), mock.patch.object(audiolib, "scan_devices", lambda: state["devices"]):
            watcher = threading.Thread(target=self.phone._watch_devices, daemon=True)
            watcher.start()
            time.sleep(0.1)
            self.assertEqual(self.phone.selected_input, BUILTIN_MIC)  # nothing changed yet

            state["devices"] = ([BUILTIN_MIC, HEADSET_MIC], [BUILTIN_OUT, HEADSET_OUT])
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and self.phone.selected_input != HEADSET_MIC:
                time.sleep(0.02)
            self.phone._stop_device_watch.set()
            watcher.join(timeout=2.0)

        self.assertEqual(self.phone.selected_input, HEADSET_MIC)

    def test_the_watcher_survives_an_audio_failure(self):
        def boom():
            raise audiolib.AudioUnavailable("no sound system")

        with mock.patch.object(phonelib, "DEVICE_POLL_INTERVAL", 0.02), mock.patch.object(
            audiolib, "device_change_token", lambda: None
        ), mock.patch.object(audiolib, "scan_devices", boom):
            watcher = threading.Thread(target=self.phone._watch_devices, daemon=True)
            watcher.start()
            time.sleep(0.15)
            self.assertTrue(watcher.is_alive())
            self.phone._stop_device_watch.set()
            watcher.join(timeout=2.0)
        self.assertFalse(watcher.is_alive())


class ChangeTokenTest(unittest.TestCase):
    def test_token_is_a_pair_or_none(self):
        token = audiolib.device_change_token()
        if token is None:
            self.assertFalse(audiolib.sys.platform.startswith("win"))
        else:
            self.assertEqual(len(token), 2)
            self.assertEqual(token, audiolib.device_change_token())  # stable


if __name__ == "__main__":
    unittest.main()
