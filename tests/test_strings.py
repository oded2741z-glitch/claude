import string
import unittest

from lanphone.i18n import ALERT_KEYS, EN, EVENT_KEYS, Strings, severity


class StringsTest(unittest.TestCase):
    def setUp(self):
        self.S = Strings()

    def test_lookup(self):
        self.assertEqual(self.S("call"), "Call")
        self.assertEqual(self.S("hangup"), "Hang up")

    def test_formatting(self):
        text = self.S("log_calling", ip="192.168.1.7")
        self.assertEqual(text, EN["log_calling"].format(ip="192.168.1.7"))

    def test_unknown_key_and_missing_field_do_not_raise(self):
        self.assertEqual(self.S("no_such_key"), "no_such_key")
        self.assertTrue(self.S("log_calling"))  # missing {ip}
        self.assertTrue(self.S("log_calling", wrong="x"))

    def test_no_right_to_left_text_is_left_anywhere(self):
        """The interface is English only; Tk cannot lay Hebrew out correctly."""
        for key, text in EN.items():
            rtl = [ch for ch in text if "֐" <= ch <= "ࣿ"]
            self.assertFalse(rtl, f"{key} still contains right-to-left text: {text!r}")

    def test_every_string_is_reachable_by_a_placeholder_free_lookup(self):
        for key, text in EN.items():
            fields = {name for _, name, _, _ in string.Formatter().parse(text) if name}
            filled = self.S(key, **dict.fromkeys(fields, "x"))
            self.assertNotIn("{", filled, f"{key} still has a placeholder")


class SeverityTest(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(severity("log_audio_error"), "alert")
        self.assertEqual(severity("log_incoming"), "event")
        self.assertEqual(severity("log_call_ended"), "")
        self.assertEqual(severity(""), "")

    def test_every_coloured_key_actually_exists(self):
        """A renamed key would silently lose its colour."""
        for key in ALERT_KEYS | EVENT_KEYS:
            self.assertIn(key, EN, f"{key} is coloured but not a real string")

    def test_the_two_sets_do_not_overlap(self):
        self.assertFalse(ALERT_KEYS & EVENT_KEYS)


if __name__ == "__main__":
    unittest.main()
