"""The window icon, drawn in code.

Tk ships a feather icon and uses it for every window unless it is given
another one.  Rather than carry an .ico file around, the icon is painted here
pixel by pixel into a ``PhotoImage``: a dark tile with the "oT" mark in the
accent colour, so the taskbar entry looks like the rest of the app.
"""

from __future__ import annotations

import tkinter as tk

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


def build(size: int = 32) -> tk.PhotoImage:
    """A ``size`` x ``size`` icon.  A Tk root must already exist."""
    grid = _pixels(size, _scale_for(size))
    image = tk.PhotoImage(width=size, height=size)
    image.put("{" + "} {".join(" ".join(row) for row in grid) + "}")
    return image


def apply(window: tk.Misc) -> tk.PhotoImage:
    """Give ``window`` (and every later one) the icon.

    The returned image must be kept alive by the caller: Tk only holds a weak
    reference, and a collected PhotoImage takes the icon down with it.
    """
    icon = build()
    try:
        window.iconphoto(True, icon)
    except tk.TclError:
        pass
    return icon
