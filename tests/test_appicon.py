"""The window icon and the .ico written for the packaged executable.

No tkinter here on purpose: the build generates the icon file, and that has to
work wherever PyInstaller runs.
"""

import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

from lanphone import appicon

try:
    import tkinter  # noqa: F401

    HAVE_TKINTER = True
except ImportError:
    HAVE_TKINTER = False

try:
    import tkinter as _tk

    _root = _tk.Tk()  # PhotoImage needs a live root even just to construct one
    _root.destroy()
    HAVE_DISPLAY = True
except Exception:  # noqa: BLE001 - no tkinter, or no display
    HAVE_DISPLAY = False


class PixelsTest(unittest.TestCase):
    def test_the_tile_is_the_requested_size(self):
        for size in (16, 32, 48, 64, 128, 256):
            grid = appicon._pixels(size)
            self.assertEqual(len(grid), size)
            self.assertTrue(all(len(row) == size for row in grid))

    def test_the_tile_is_a_solid_black_square(self):
        grid = appicon._pixels(32)
        used = {cell for row in grid for cell in row}
        self.assertEqual(used, {"#000000"})
        self.assertEqual(appicon.ICON_COLOUR, "#000000")


class WriteIcoTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".ico")
        os.close(handle)
        self.addCleanup(os.unlink, self.path)

    def test_writes_a_file_windows_will_accept(self):
        appicon.write_ico(self.path)
        with open(self.path, "rb") as fh:
            blob = fh.read()

        reserved, kind, count = struct.unpack("<HHH", blob[:6])
        self.assertEqual((reserved, kind), (0, 1))  # 0, then 1 for "icon"
        self.assertEqual(count, len(appicon.ICO_SIZES))
        self.assertTrue(appicon.is_ico(self.path))

    def test_every_entry_points_at_real_data(self):
        appicon.write_ico(self.path)
        with open(self.path, "rb") as fh:
            blob = fh.read()
        count = struct.unpack("<H", blob[4:6])[0]

        for index in range(count):
            entry = blob[6 + 16 * index : 6 + 16 * (index + 1)]
            width, height, colors, _, planes, bits, length, offset = struct.unpack("<BBBBHHII", entry)
            expected = appicon.ICO_SIZES[index]
            self.assertEqual(width, height)
            self.assertEqual(width, 0 if expected >= 256 else expected)  # 256 is stored as 0
            self.assertEqual((colors, planes, bits), (0, 1, 32))
            self.assertLessEqual(offset + length, len(blob), "entry runs past the end of the file")
            # The image starts with a BITMAPINFOHEADER of the doubled height.
            header = struct.unpack("<Iii", blob[offset : offset + 12])
            self.assertEqual(header[0], 40)
            self.assertEqual(header[1], expected)
            self.assertEqual(header[2], expected * 2)

    def test_a_single_size_can_be_asked_for(self):
        appicon.write_ico(self.path, sizes=(32,))
        with open(self.path, "rb") as fh:
            self.assertEqual(struct.unpack("<H", fh.read(6)[4:])[0], 1)


class FindIconFileTest(unittest.TestCase):
    """Where a hand-made app_icon.ico is looked for, and what wins."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write_real_ico(self, *parts):
        path = os.path.join(self.tmp.name, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        appicon.write_ico(path, sizes=(16,))
        return path

    def test_no_file_anywhere_means_none(self):
        with mock.patch.object(appicon, "_search_dirs", return_value=[self.tmp.name]):
            self.assertIsNone(appicon.find_icon_file())

    def test_finds_app_icon_ico(self):
        self.write_real_ico("app_icon.ico")
        with mock.patch.object(appicon, "_search_dirs", return_value=[self.tmp.name]):
            found = appicon.find_icon_file()
        self.assertEqual(found, os.path.join(self.tmp.name, "app_icon.ico"))

    def test_app_icon_ico_is_preferred_over_icon_ico(self):
        self.write_real_ico("icon.ico")
        self.write_real_ico("app_icon.ico")
        with mock.patch.object(appicon, "_search_dirs", return_value=[self.tmp.name]):
            found = appicon.find_icon_file()
        self.assertEqual(os.path.basename(found), "app_icon.ico")

    def test_a_renamed_png_is_skipped_in_favour_of_a_real_one_further_along(self):
        fake_dir = os.path.join(self.tmp.name, "a")
        real_dir = os.path.join(self.tmp.name, "b")
        os.makedirs(fake_dir)
        with open(os.path.join(fake_dir, "app_icon.ico"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)  # a renamed .png
        self.write_real_ico("b", "app_icon.ico")
        with mock.patch.object(appicon, "_search_dirs", return_value=[fake_dir, real_dir]):
            found = appicon.find_icon_file()
        self.assertEqual(found, os.path.join(real_dir, "app_icon.ico"))

    def test_search_dirs_includes_the_running_scripts_directory(self):
        with mock.patch.object(sys, "argv", ["/somewhere/main.py"]):
            self.assertIn("/somewhere", appicon._search_dirs())

    def test_search_dirs_prefers_the_frozen_bundle_when_frozen(self):
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
            sys, "_MEIPASS", "/bundle", create=True
        ), mock.patch.object(sys, "executable", "/dist/LANPhone.exe"):
            dirs = appicon._search_dirs()
        self.assertEqual(dirs[0], "/bundle")
        self.assertIn("/dist", dirs)


@unittest.skipUnless(HAVE_TKINTER, "tkinter is not installed")
class ApplyPrefersFileTest(unittest.TestCase):
    """The one apply() path that never touches a real Tk root.

    It returns before calling build(), so unlike the fallback tests below it
    runs headless: importing the tkinter module (for tk.TclError) works fine
    without a display, only constructing a PhotoImage needs one.
    """

    def test_prefers_a_real_icon_file_on_windows(self):
        window = mock.Mock()
        with mock.patch.object(appicon, "find_icon_file", return_value="/x/app_icon.ico"), mock.patch.object(
            sys, "platform", "win32"
        ):
            result = appicon.apply(window)
        window.iconbitmap.assert_called_once_with(default="/x/app_icon.ico")
        window.iconphoto.assert_not_called()
        self.assertIsNone(result)  # nothing generated, nothing to keep alive


@unittest.skipUnless(HAVE_DISPLAY, "no live Tk root available")
class ApplyFallsBackTest(unittest.TestCase):
    """These paths reach build(), which needs a real, undestroyed root.

    A create-then-destroy probe (like the module-level HAVE_DISPLAY check)
    is not enough - PhotoImage looks up Tk's *current* default root, so one
    has to stay alive for the duration of each test.
    """

    def setUp(self):
        self.root = _tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_falls_back_to_the_drawn_tile_off_windows_even_with_a_real_file(self):
        window = mock.Mock()
        with mock.patch.object(appicon, "find_icon_file", return_value="/x/app_icon.ico"), mock.patch.object(
            sys, "platform", "linux"
        ):
            result = appicon.apply(window)
        window.iconbitmap.assert_not_called()
        window.iconphoto.assert_called_once()
        self.assertIsNotNone(result)

    def test_falls_back_to_the_drawn_tile_with_no_file(self):
        window = mock.Mock()
        with mock.patch.object(appicon, "find_icon_file", return_value=None), mock.patch.object(
            sys, "platform", "win32"
        ):
            result = appicon.apply(window)
        window.iconbitmap.assert_not_called()
        window.iconphoto.assert_called_once()
        self.assertIsNotNone(result)

    def test_a_file_iconbitmap_rejects_still_falls_back(self):
        import tkinter as tk

        window = mock.Mock()
        window.iconbitmap.side_effect = tk.TclError("nope")
        with mock.patch.object(appicon, "find_icon_file", return_value="/x/app_icon.ico"), mock.patch.object(
            sys, "platform", "win32"
        ):
            result = appicon.apply(window)
        window.iconphoto.assert_called_once()
        self.assertIsNotNone(result)


class IsIcoTest(unittest.TestCase):
    """PyInstaller aborts the build on a file that only looks like an icon."""

    def write(self, data):
        handle, path = tempfile.mkstemp(suffix=".ico")
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        self.addCleanup(os.unlink, path)
        return path

    def test_accepts_a_real_icon(self):
        self.assertTrue(appicon.is_ico(self.write(appicon.ICO_MAGIC + struct.pack("<H", 1) + b"\0" * 16)))

    def test_rejects_a_renamed_png(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
        self.assertFalse(appicon.is_ico(self.write(png)))

    def test_rejects_a_renamed_jpeg_and_empty_and_truncated_files(self):
        self.assertFalse(appicon.is_ico(self.write(b"\xff\xd8\xff\xe0" + b"\0" * 16)))
        self.assertFalse(appicon.is_ico(self.write(b"")))
        self.assertFalse(appicon.is_ico(self.write(appicon.ICO_MAGIC)))  # header cut short

    def test_rejects_an_icon_declaring_no_images(self):
        self.assertFalse(appicon.is_ico(self.write(appicon.ICO_MAGIC + struct.pack("<H", 0))))

    def test_a_missing_file_is_not_an_icon(self):
        self.assertFalse(appicon.is_ico("/no/such/file.ico"))


if __name__ == "__main__":
    unittest.main()
