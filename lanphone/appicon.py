"""The window icon: a hand-made .ico if the app has one, else a black square.

Tk ships a feather icon and uses it for every window unless it is given
another one.  If a real ``app_icon.ico`` sits next to the app, that is what
gets used - see ``find_icon_file`` for exactly where it looks.  Failing that,
a plain black square is painted in code, so the window is never left with
Tk's default feather.
"""

from __future__ import annotations

import os
import struct
import sys

# Names accepted for a hand-made icon, checked in this order.
ICON_FILENAMES = ("app_icon.ico", "icon.ico", "lanphone.ico")

ICON_COLOUR = "#000000"


def _pixels(size: int) -> list[list[str]]:
    return [[ICON_COLOUR] * size for _ in range(size)]


def build(size: int = 32):
    """A ``size`` x ``size`` ``PhotoImage``.  A Tk root must already exist."""
    import tkinter as tk

    grid = _pixels(size)
    image = tk.PhotoImage(width=size, height=size)
    image.put("{" + "} {".join(" ".join(row) for row in grid) + "}")
    return image


def _search_dirs() -> list[str]:
    """Places a hand-made icon file might live, most likely first.

    Covers running from source (``python main.py``) and the packaged .exe:
    PyInstaller extracts bundled data next to ``sys._MEIPASS``, and
    ``LANPhone.spec`` bundles the user's own icon file there when it finds
    one at build time (see ``resolve_icon`` in the spec).
    """
    dirs = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
        dirs.append(os.path.dirname(sys.executable))
    if sys.argv and sys.argv[0]:
        dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirs.append(os.getcwd())
    seen: list[str] = []
    for directory in dirs:
        if directory and directory not in seen:
            seen.append(directory)
    return seen


def find_icon_file() -> str | None:
    """A real .ico placed next to the app, if there is one.  See ``_search_dirs``."""
    for directory in _search_dirs():
        for name in ICON_FILENAMES:
            path = os.path.join(directory, name)
            if os.path.exists(path) and is_ico(path):
                return path
    return None


def apply(window):
    """Give ``window`` (and every later one) the app's icon.

    Prefers a real ``app_icon.ico`` if one can be found (Windows only: that is
    the one platform where Tk can load a .ico file as-is).  Otherwise the
    generated tile is used, and its ``PhotoImage`` is returned so the caller
    can keep a reference - Tk only holds a weak one, and a collected
    PhotoImage takes the icon down with it.  ``None`` means a real file is in
    use and there is nothing the caller needs to keep alive.
    """
    import tkinter as tk

    path = find_icon_file()
    if path and sys.platform.startswith("win"):
        try:
            window.iconbitmap(default=path)
            return None
        except tk.TclError:
            pass  # fall through to the generated tile

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
    grid = _pixels(size)
    rgb = {}
    for colour in {cell for row in grid for cell in row}:
        value = colour.lstrip("#")
        rgb[colour] = bytes((int(value[4:6], 16), int(value[2:4], 16), int(value[0:2], 16), 255))

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
