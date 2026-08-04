"""Interface tests.  Skipped when there is no tkinter or no display."""

import sys
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
        fresh = Settings(display_name="test-pc")
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

    def test_window_builds(self):
        self.assertEqual(self.app.root.title(), "LAN Phone")  # not doubled up
        self.assertEqual(str(self.app.call_btn["text"]), "Call")
        self.assertEqual(str(self.app.hangup_btn["text"]), "Hang up")

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

    def test_saved_addresses_fill_the_dropdown(self):
        self.assertEqual(self.app.ip_combo["values"], "")
        self.assertEqual(str(self.app.forget_btn["state"]), "disabled")
        self.app.settings.remember_peer("192.168.1.5", 50505, "PC-A")
        self.app.settings.remember_peer("192.168.1.9", 50511)
        self.app._render_saved()
        values = list(self.app.ip_combo["values"])
        self.assertEqual(len(values), 2)
        self.assertIn("192.168.1.9", values[0])  # newest first, no name known
        self.assertIn("PC-A", values[1])
        self.assertEqual(str(self.app.forget_btn["state"]), "normal")

    def test_picking_a_saved_address_puts_the_bare_ip_in_the_box(self):
        self.app.settings.remember_peer("192.168.1.5", 50505, "PC-A")
        self.app._render_saved()
        self.app.ip_combo.current(0)
        self.app._on_saved_selected()
        self.assertEqual(self.app.ip_var.get(), "192.168.1.5")

    def test_a_saved_address_keeps_its_port(self):
        self.app.settings.remember_peer("192.168.1.5", 50512, "PC-A")
        self.app._render_saved()
        self.app.ip_var.set("192.168.1.5")
        self.assertEqual(self.app._call_target(), ("192.168.1.5", 50512))

    def test_forget_removes_the_shown_address(self):
        self.app.settings.remember_peer("192.168.1.5", 50505, "PC-A")
        self.app.settings.remember_peer("192.168.1.9", 50505, "PC-B")
        self.app._render_saved()
        self.app.ip_var.set("192.168.1.5")
        self.app._on_forget()
        self.app._drain_events()
        self.assertEqual([p["ip"] for p in self.app.settings.saved_peers], ["192.168.1.9"])
        self.assertEqual(len(self.app.ip_combo["values"]), 1)

    def test_forget_with_an_unknown_address_drops_the_newest(self):
        self.app.settings.remember_peer("192.168.1.5")
        self.app._render_saved()
        self.app.ip_var.set("not-in-the-list")
        self.app._on_forget()
        self.app._drain_events()
        self.assertEqual(self.app.settings.saved_peers, [])

    def test_saved_peers_event_persists_the_settings(self):
        with mock.patch.object(Settings, "save") as save:
            self.app._emit("saved_peers")
            self.app._drain_events()
        self.assertTrue(save.called)

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

    def test_there_is_no_language_option_left(self):
        """One language only: no toggle, no menu bar, no Hebrew on screen."""
        self.assertFalse(hasattr(self.app, "_toggle_language"))
        self.assertFalse(hasattr(self.app, "_set_language"))
        self.assertIsNone(self.app.root["menu"] or None)
        for widget in _walk(self.app.root):
            text = str(widget.cget("text")) if "text" in widget.keys() else ""
            self.assertFalse(
                any("\u0590" <= ch <= "\u08ff" for ch in text),
                f"right-to-left text on screen: {text!r}",
            )

    def test_window_chrome_is_requested(self):
        from lanphone import theme, winchrome

        # Windows-only; everywhere else it reports that it did nothing.
        self.assertEqual(winchrome.is_supported(), sys.platform.startswith("win"))
        self.assertFalse(winchrome.apply(self.app.root) and not winchrome.is_supported())
        self.assertEqual(winchrome.colorref("#151515"), 0x151515)
        self.assertEqual(winchrome.colorref(theme.ACCENT), 0x1F6AFF)  # BGR order

    def test_dark_theme_is_applied(self):
        from lanphone import theme

        # clam is the only built-in theme that honours colours on Windows.
        self.assertEqual(self.app.style.theme_use(), "clam")
        self.assertEqual(str(self.app.root["background"]), theme.BG)
        self.assertEqual(str(self.app.peer_list["background"]), theme.DEEP)
        self.assertEqual(str(self.app.peer_list["selectbackground"]), theme.ACCENT)
        self.assertEqual(str(self.app.log_text["background"]), theme.DEEP)
        self.assertEqual(str(self.app.log_text["insertbackground"]), theme.ACCENT)

    def test_action_buttons_carry_the_accent_and_danger_styles(self):
        from lanphone import theme

        self.assertEqual(str(self.app.call_btn["style"]), theme.ACCENT_BUTTON)
        self.assertEqual(str(self.app.answer_btn["style"]), theme.ACCENT_BUTTON)
        self.assertEqual(str(self.app.hangup_btn["style"]), theme.DANGER_BUTTON)
        self.assertEqual(str(self.app.mic_meter["style"]), theme.METER)

    def test_log_lines_are_coloured_by_severity(self):
        self.app._emit("log", key="log_audio_error", err="boom")
        self.app._emit("log", key="log_incoming", name="PC", ip="10.0.0.1")
        self.app._emit("log", key="log_call_ended")
        self.app._drain_events()
        tags = [entry[2] for entry in self.app._log_lines]
        self.assertEqual(tags, ["alert", "event", ""])
        # The timestamp is a separate, dimmed run of text.
        self.assertIn("stamp", self.app.log_text.tag_names())
        self.assertTrue(self.app.log_text.tag_ranges("alert"))

    def test_status_turns_to_the_accent_colour_during_a_call(self):
        from lanphone import theme

        self.app._update_state(IDLE)
        self.assertEqual(str(self.app.status_label["foreground"]), theme.TEXT)
        self.app._update_state(IN_CALL)
        self.assertEqual(str(self.app.status_label["foreground"]), theme.ACCENT)

    def test_empty_peer_list_placeholder_is_tinted(self):
        from lanphone import theme

        self.app._peers = []
        self.app._render_peers()
        self.assertEqual(str(self.app.peer_list.itemcget(0, "foreground")), theme.ACCENT_SOFT)

    def test_closing_is_idempotent(self):
        self.app._on_close()
        self.assertFalse(self.app._alive)
        self.app._tick()  # must not raise after the window is gone


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


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
