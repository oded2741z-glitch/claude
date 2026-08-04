"""The window icon, drawn in code.

Tk ships a feather icon and uses it for every window unless it is given
another one.  Rather than carry an .ico file around, the icon is painted here
pixel by pixel into a ``PhotoImage``: a dark tile with the "oT" mark in the
accent colour, so the taskbar entry looks like the rest of the app.
"""

from __future__ import annotations

import struct

from . import theme

# 5x7 pixel letters.  '#' is ink, anything else is background.
GLYPHS = {
    "o": (
        ".....",
        ".....",
        ".###.",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "T": (
        "#####",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
    ),
}

MARK = "oT"
GLYPH_WIDTH = 5
GLYPH_HEIGHT = 7
GLYPH_GAP = 1
MARK_CELLS = GLYPH_WIDTH * len(MARK) + GLYPH_GAP * (len(MARK) - 1)


def _scale_for(size: int) -> int:
    """Biggest whole-pixel scale that leaves a margin on every side."""
    margin = max(1, size // 10)
    room = size - 2 * margin
    return max(1, min(room // MARK_CELLS, room // GLYPH_HEIGHT))


def _pixels(size: int, scale: int) -> list[list[str]]:
    background, ink = theme.BG, theme.ACCENT
    grid = [[background] * size for _ in range(size)]

    text_width = MARK_CELLS * scale
    left = (size - text_width) // 2
    top = (size - GLYPH_HEIGHT * scale) // 2

    for index, letter in enumerate(MARK):
        rows = GLYPHS[letter]
        offset = left + index * (GLYPH_WIDTH + GLYPH_GAP) * scale
        for y, row in enumerate(rows):
            for x, cell in enumerate(row):
                if cell != "#":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        py, px = top + y * scale + dy, offset + x * scale + dx
                        if 0 <= py < size and 0 <= px < size:
                            grid[py][px] = ink
    return grid


def build(size: int = 32):
    """A ``size`` x ``size`` ``PhotoImage``.  A Tk root must already exist."""
    import tkinter as tk

    grid = _pixels(size, _scale_for(size))
    image = tk.PhotoImage(width=size, height=size)
    image.put("{" + "} {".join(" ".join(row) for row in grid) + "}")
    return image


def apply(window):
    """Give ``window`` (and every later one) the icon.

    The returned image must be kept alive by the caller: Tk only holds a weak
    reference, and a collected PhotoImage takes the icon down with it.
    """
    import tkinter as tk

    icon = build()
    try:
        window.iconphoto(True, icon)
    except tk.TclError:
        pass
    return icon


# --------------------------------------------------------------------------
# .ico file, for the packaged executable
# --------------------------------------------------------------------------
ICO_MAGIC = b"\x00\x00\x01\x00"
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def is_ico(path: str) -> bool:
    """True if the file really is a Windows icon.

    Worth checking before handing it to PyInstaller: a .png renamed to .ico
    is a common mistake and it fails the whole build rather than the icon.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(6)
    except OSError:
        return False
    if len(header) < 6 or header[:4] != ICO_MAGIC:
        return False
    return int.from_bytes(header[4:6], "little") > 0  # at least one image


def _bmp_image(size: int) -> bytes:
    """One icon image in the BMP form .ico files use: header, BGRA, AND mask."""
    grid = _pixels(size, _scale_for(size))
    rgb = {}
    for color in (theme.BG, theme.ACCENT):
        value = color.lstrip("#")
        rgb[color] = bytes((int(value[4:6], 16), int(value[2:4], 16), int(value[0:2], 16), 255))

    # BITMAPINFOHEADER, with double height: the XOR image plus the AND mask.
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
    pixels = b"".join(b"".join(rgb[cell] for cell in row) for row in reversed(grid))
    # Fully opaque, so the mask is zeroed; rows are padded to four bytes.
    mask_row = (size + 31) // 32 * 4
    return header + pixels + bytes(mask_row * size)


def write_ico(path: str, sizes: tuple[int, ...] = ICO_SIZES) -> str:
    """Write a real multi-size .ico.  No image library involved."""
    images = [_bmp_image(size) for size in sizes]
    offset = 6 + 16 * len(images)
    directory = bytearray(ICO_MAGIC[:4] + struct.pack("<H", len(images)))
    directory[4:6] = struct.pack("<H", len(images))
    entries = bytearray()
    for size, image in zip(sizes, images):
        stored = 0 if size >= 256 else size  # 256 is written as zero
        entries += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    with open(path, "wb") as fh:
        fh.write(ICO_MAGIC + struct.pack("<H", len(images)) + bytes(entries) + b"".join(images))
    return path
