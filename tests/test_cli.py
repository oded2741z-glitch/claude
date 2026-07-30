"""Command line entry point, including the case a frozen build creates."""

import io
import unittest
from unittest import mock

from lanphone import __main__ as cli


class CliTest(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.sys, "stdout", out), mock.patch.object(cli.sys, "stderr", err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_network_report(self):
        code, out, _ = self.run_main(["--network"])
        self.assertEqual(code, 0)
        self.assertIn("local address", out)
        self.assertIn("50505", out)

    def test_list_devices_without_a_sound_card(self):
        code, out, err = self.run_main(["--list-devices"])
        # No PortAudio here, so this reports the problem rather than crashing.
        self.assertIn(code, (1, 2))
        self.assertTrue(out or err)

    def test_unknown_flag_exits(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--nope"])

    def test_report_survives_a_missing_console(self):
        """A windowed .exe has sys.stdout set to None: printing there raises."""
        shown = []
        with mock.patch.object(cli.sys, "stdout", None), mock.patch.object(
            cli, "_dialog", lambda text, error=False: shown.append(text)
        ):
            cli._report("hello")
        self.assertEqual(shown, ["hello"])

    def test_main_without_a_console_does_not_raise(self):
        with mock.patch.object(cli.sys, "stdout", None), mock.patch.object(
            cli.sys, "stderr", None
        ), mock.patch.object(cli, "_dialog") as dialog:
            self.assertEqual(cli.main(["--network"]), 0)
        self.assertTrue(dialog.called)

    def test_report_falls_back_when_the_stream_rejects_the_write(self):
        broken = mock.Mock()
        broken.write.side_effect = ValueError("I/O operation on closed file")
        shown = []
        with mock.patch.object(cli.sys, "stdout", broken), mock.patch.object(
            cli, "_dialog", lambda text, error=False: shown.append(text)
        ):
            cli._report("text")
        self.assertEqual(shown, ["text"])

    def test_entry_script_matches_the_module(self):
        import main as entry_script

        self.assertIs(entry_script.main, cli.main)


if __name__ == "__main__":
    unittest.main()
