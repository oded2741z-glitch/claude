import unittest

from lanphone.i18n import EN, HE, Strings
from lanphone.rtl import contains_rtl, to_visual


class RtlTest(unittest.TestCase):
    def test_latin_text_is_untouched(self):
        for text in ("Call", "192.168.1.5", "sent 10 · recv 9", ""):
            self.assertEqual(to_visual(text), text)

    def test_hebrew_word_is_reversed_for_tk(self):
        # Tk draws characters left to right, so the visual order is reversed.
        self.assertEqual(to_visual("שלום"), "םולש")

    def test_latin_run_inside_hebrew_keeps_its_direction(self):
        visual = to_visual("שלום Call עולם")
        self.assertIn("Call", visual)
        self.assertEqual(visual, "םלוע Call םולש")

    def test_numbers_stay_readable(self):
        self.assertEqual(to_visual("שלב 2"), "2 בלש")
        self.assertIn("192.168.1.5", to_visual("הכתובת שלי: 192.168.1.5"))

    def test_brackets_are_mirrored(self):
        visual = to_visual("מהירות (48000)")
        self.assertEqual(visual, "(48000) תוריהמ")

    def test_lines_are_handled_separately(self):
        visual = to_visual("שלום\nCall")
        self.assertEqual(visual, "םולש\nCall")

    def test_reordering_is_reversible_for_pure_hebrew(self):
        for word in ("אוזניות", "מיקרופון", "התקשר"):
            self.assertEqual(to_visual(to_visual(word)), word)

    def test_contains_rtl(self):
        self.assertTrue(contains_rtl("abc ש"))
        self.assertFalse(contains_rtl("abc 123 !?"))


class StringsTest(unittest.TestCase):
    def test_tables_have_the_same_keys(self):
        self.assertEqual(set(HE), set(EN))

    def test_hebrew_is_reordered_only_when_asked(self):
        fixed = Strings("he", rtl_fix=True)
        plain = Strings("he", rtl_fix=False)
        self.assertEqual(plain("call"), HE["call"])
        self.assertEqual(fixed("call"), to_visual(HE["call"]))
        self.assertEqual(fixed.raw("call"), HE["call"])

    def test_english_is_never_reordered(self):
        english = Strings("en")
        self.assertEqual(english("call"), EN["call"])

    def test_formatting_happens_before_reordering(self):
        fixed = Strings("he")
        text = fixed("log_calling", ip="192.168.1.7")
        self.assertIn("192.168.1.7", text)
        self.assertEqual(text, to_visual(HE["log_calling"].format(ip="192.168.1.7")))

    def test_unknown_key_and_missing_field_do_not_raise(self):
        strings = Strings("he")
        self.assertEqual(strings("no_such_key"), "no_such_key")
        self.assertTrue(strings("log_calling"))  # missing {ip}

    def test_unknown_language_falls_back(self):
        self.assertEqual(Strings("fr").language, "he")

    def test_every_placeholder_matches_between_languages(self):
        import string

        for key, he_text in HE.items():
            he_fields = {f for _, f, _, _ in string.Formatter().parse(he_text) if f}
            en_fields = {f for _, f, _, _ in string.Formatter().parse(EN[key]) if f}
            self.assertEqual(he_fields, en_fields, f"placeholders differ for {key}")


if __name__ == "__main__":
    unittest.main()
