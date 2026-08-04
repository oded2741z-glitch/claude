"""The window icon and the .ico written for the packaged executable.

No tkinter here on purpose: the build generates the icon file, and that has to
work wherever PyInstaller runs.
"""

import os
import struct
import tempfile
import unittest

from lanphone import appicon, theme


class PixelsTest(unittest.TestCase):
    def test_the_mark_stays_inside_the_tile(self):
        for size in (16, 32, 48, 64, 128, 256):
            grid = appicon._pixels(size, appicon._scale_for(size))
            self.assertEqual(len(grid), size)
            self.assertTrue(all(len(row) == size for row in grid))
            ink = [
                (x, y)
                for y, row in enumerate(grid)
                for x, cell in enumerate(row)
                if cell == theme.ACCENT
            ]
            self.assertTrue(ink, f"nothing drawn at {size}px")
            self.assertTrue(
                all(0 < x < size - 1 and 0 < y < size - 1 for x, y in ink),
                f"the mark touches the edge at {size}px",
            )

    def test_the_tile_uses_only_the_two_theme_colours(self):
        grid = appicon._pixels(32, appicon._scale_for(32))
        used = {cell for row in grid for cell in row}
        self.assertEqual(used, {theme.BG, theme.ACCENT})


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
