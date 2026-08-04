"""Windows title bar: dark, coloured to match, and without the app icon.

Tk cannot touch the title bar - Windows draws it - so this goes through the
Win32 API directly:

* ``DwmSetWindowAttribute`` with the immersive-dark-mode attribute turns the
  caption dark on Windows 10 1809 and later.  Windows 11 22000+ also accepts an
  explicit caption, text and border colour, which is what makes it match the
  window exactly instead of being "the system's dark".
* ``WS_EX_DLGMODALFRAME`` plus clearing both icons removes the icon from the
  caption.

Everything here is best-effort: on any other platform, or an older Windows, the
calls are skipped or fail quietly and the window simply keeps its normal frame.
"""

from __future__ import annotations

import ctypes
import sys

# DwmSetWindowAttribute attributes.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 builds before 20H1
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36

GWL_EXSTYLE = -20
WS_EX_DLGMODALFRAME = 0x00000001
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020


def is_supported() -> bool:
    return sys.platform.startswith("win")


def colorref(color: str) -> int:
    """'#RRGGBB' -> the 0x00BBGGRR integer Win32 wants."""
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected #RRGGBB, got {color!r}")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


def _hwnd(window) -> int:
    """The HWND that owns the caption (Tk's own window is a child of it)."""
    window.update_idletasks()
    handle = int(window.winfo_id())
    parent = ctypes.windll.user32.GetParent(handle)
    return parent or handle


def _set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    data = ctypes.c_int(value)
    # The handle must go as a pointer: a 64-bit HWND does not fit the c_int
    # that ctypes would pick for a plain Python integer.
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd),
        ctypes.c_uint(attribute),
        ctypes.byref(data),
        ctypes.c_uint(ctypes.sizeof(data)),
    )
    return result == 0


def dark_titlebar(window, caption: str | None = None, text: str | None = None) -> bool:
    """Make the title bar dark.  True when Windows accepted something."""
    if not is_supported():
        return False
    try:
        hwnd = _hwnd(window)
        applied = _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        if not applied:
            applied = _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1)
        # Windows 11 only; older builds reject these and keep the dark default.
        if caption:
            applied |= _set_attribute(hwnd, DWMWA_CAPTION_COLOR, colorref(caption))
            applied |= _set_attribute(hwnd, DWMWA_BORDER_COLOR, colorref(caption))
        if text:
            applied |= _set_attribute(hwnd, DWMWA_TEXT_COLOR, colorref(text))
        return bool(applied)
    except Exception:  # noqa: BLE001 - a plain title bar is not worth crashing over
        return False


def hide_titlebar_icon(window) -> bool:
    """Remove the icon from the left of the title bar."""
    if not is_supported():
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = _hwnd(window)
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.restype = ctypes.c_void_p
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

        style = get_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE) or 0
        set_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ctypes.c_void_p(int(style) | WS_EX_DLGMODALFRAME))
        for which in (ICON_SMALL, ICON_BIG):
            user32.SendMessageW(ctypes.c_void_p(hwnd), WM_SETICON, which, 0)
        # The frame only re-reads its style when told to redraw itself.
        user32.SetWindowPos(
            ctypes.c_void_p(hwnd), 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def apply(window, caption: str | None = None, text: str | None = None) -> bool:
    """Dark, matching, icon-less title bar.  Safe to call on any platform."""
    dark = dark_titlebar(window, caption, text)
    icon = hide_titlebar_icon(window)
    return dark or icon
