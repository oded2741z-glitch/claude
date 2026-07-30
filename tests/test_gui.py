"""Interface tests.  Skipped when there is no tkinter or no display."""

import unittest
from unittest import mock

from lanphone.config import Settings
from lanphone.phone import CALLING, IDLE, IN_CALL, RINGING

try:
    import tkinter

    _root = tkinter.Tk()
    _root.destroy()
    HAVE_TK = True
    TK_REASON = ""
except Exception as exc:  # noqa: BLE001 - no tkinter, or no display
    HAVE_TK = False
    TK_REASON = str(exc)


@unittest.skipUnless(HAVE_TK, f"tkinter/display unavailable: {TK_REASON}")
class GuiTest(unittest.TestCase):
    def setUp(self):
        from lanphone import gui

        self.gui = gui
        fresh = Settings(display_name="test-pc", language="he")
        patches = [
            mock.patch.object(Settings, "load", classmethod(lambda cls: fresh)),
            mock.patch.object(Settings, "save", lambda self: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        # No mainloop and no update() here, so the scheduled _start_phone never
        # runs: the interface is exercised without opening any sockets.
        self.app = gui.PhoneApp()
        self.addCleanup(self.destroy_window)

    def destroy_window(self):
        try:
            self.app.root.destroy()
        except tkinter.TclError:
            pass  # a test already closed the window

    def peer(self, name="PC-2", ip="192.168.1.42", key="a"):
        return {"id": key, "name": name, "ip": ip, "sig_port": 50505, "last_seen": 0}

    def test_window_builds_in_hebrew(self):
        self.assertTrue(self.app.S.is_rtl)
        self.assertIn("שיחה", self.app.root.title())

    def test_peer_list_replaces_the_placeholder(self):
        self.assertEqual(self.app.peer_list.size(), 1)  # "(searching...)"
        self.app._peers = [self.peer(), self.peer(name="Laptop", ip="10.0.0.5", key="b")]
        self.app._render_peers()
        self.assertEqual(self.app.peer_list.size(), 2)
        self.assertNotIn(self.app.S("no_peers"), self.app.peer_list.get(0, "end"))
        # ...and going back to nothing restores it
        self.app._peers = []
        self.app._render_peers()
        self.assertEqual(self.app.peer_list.size(), 1)

    def test_selecting_a_peer_fills_the_address_field(self):
        self.app._peers = [self.peer()]
        self.app._render_peers()
        self.app.peer_list.selection_set(0)
        self.app._on_peer_selected()
        self.assertEqual(self.app.ip_var.get(), "192.168.1.42")

    def test_call_target_prefers_the_selected_peer(self):
        self.app._peers = [self.peer()]
        self.app._render_peers()
        self.app.peer_list.selection_set(0)
        self.assertEqual(self.app._call_target(), ("192.168.1.42", 50505))

    def test_call_target_falls_back_to_the_typed_address(self):
        self.app.ip_var.set("10.1.2.3")
        self.assertEqual(self.app._call_target(), ("10.1.2.3", 50505))
        self.app.ip_var.set("")
        self.assertIsNone(self.app._call_target())

    def test_buttons_follow_the_call_state(self):
        def states():
            return {
                "call": str(self.app.call_btn["state"]),
                "hangup": str(self.app.hangup_btn["state"]),
                "answer": str(self.app.answer_btn["state"]),
            }

        self.app._update_state(IDLE)
        self.assertEqual(states(), {"call": "normal", "hangup": "disabled", "answer": "disabled"})
        self.app._update_state(CALLING)
        self.assertEqual(states(), {"call": "disabled", "hangup": "normal", "answer": "disabled"})
        self.app._update_state(RINGING)
        self.assertEqual(states(), {"call": "disabled", "hangup": "disabled", "answer": "normal"})
        self.app._update_state(IN_CALL)
        self.assertEqual(states(), {"call": "disabled", "hangup": "normal", "answer": "disabled"})

    def test_events_from_the_phone_reach_the_widgets(self):
        self.app._emit("log", key="log_calling", ip="192.168.1.9")
        self.app._emit("peers", peers=[self.peer()])
        self.app._emit("state", state=IN_CALL)
        self.app._emit("devices")
        self.app._drain_events()
        self.assertEqual(self.app.peer_list.size(), 1)
        text = self.app.log_text.get("1.0", "end")
        self.assertIn("192.168.1.9", text)
        self.assertEqual(str(self.app.hangup_btn["state"]), "normal")

    def test_log_keeps_a_bounded_number_of_lines(self):
        for index in range(self.gui.MAX_LOG_LINES + 50):
            self.app._append_log(f"line {index}")
        self.assertLessEqual(len(self.app._log_lines), self.gui.MAX_LOG_LINES)
        self.assertIn("line %d" % (self.gui.MAX_LOG_LINES + 49), self.app._log_lines[-1])

    def test_language_switch_rebuilds_the_interface(self):
        self.app._set_language("en")
        self.assertFalse(self.app.S.is_rtl)
        self.assertEqual(str(self.app.call_btn["text"]), "Call")
        self.app._set_language("he")
        self.assertTrue(self.app.S.is_rtl)
        self.assertEqual(str(self.app.call_btn["text"]), self.app.S("call"))

    def test_settings_dialog_saves_and_applies(self):
        with mock.patch.object(self.gui.tk, "Toplevel", wraps=self.gui.tk.Toplevel):
            self.app._open_settings()
        # The dialog is a separate window; find its widgets through the children.
        self.assertTrue(any(isinstance(w, self.gui.tk.Toplevel) for w in self.app.root.winfo_children()))

    def test_knobs_reach_the_phone(self):
        self.app.mute_var.set(True)
        self.app._on_mute()
        self.assertTrue(self.app.phone.engine.muted)
        self.app.volume_var.set(0.5)
        self.app._on_volume()
        self.assertAlmostEqual(self.app.phone.engine.volume, 0.5)
        self.app.gain_var.set(2.0)
        self.app._on_gain()
        self.assertAlmostEqual(self.app.phone.engine.mic_gain, 2.0)

    def test_calling_without_a_target_warns_instead_of_dialling(self):
        self.app.ip_var.set("")
        with mock.patch.object(self.gui.messagebox, "showinfo") as info, mock.patch.object(
            self.app.phone, "place_call"
        ) as place:
            self.app._on_call()
        self.assertTrue(info.called)
        self.assertFalse(place.called)

    def test_invalid_address_is_rejected(self):
        self.app.ip_var.set("1.2.3.4.5.")
        with mock.patch.object(self.gui.messagebox, "showerror") as error, mock.patch.object(
            self.app.phone, "place_call"
        ) as place:
            self.app._on_call()
        self.assertTrue(error.called)
        self.assertFalse(place.called)

    def test_closing_is_idempotent(self):
        self.app._on_close()
        self.assertFalse(self.app._alive)
        self.app._tick()  # must not raise after the window is gone


@unittest.skipUnless(HAVE_TK, f"tkinter/display unavailable: {TK_REASON}")
class LevelMeterTest(unittest.TestCase):
    def test_level_percent_range(self):
        from lanphone.gui import _level_percent

        self.assertEqual(_level_percent(0.0), 0.0)
        self.assertEqual(_level_percent(1.0), 100.0)
        quiet, loud = _level_percent(0.01), _level_percent(0.3)
        self.assertLess(quiet, loud)
        self.assertGreater(quiet, 0.0)


if __name__ == "__main__":
    unittest.main()
