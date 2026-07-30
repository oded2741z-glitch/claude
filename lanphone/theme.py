"""Dark theme: near-black panels, orange accents, flat one-pixel borders.

Everything is styled through ttk's ``clam`` theme, which is the only built-in
one that honours colour options on Windows - the native ``vista`` theme draws
buttons and entries with system colours and ignores most of what is set here.
The classic (non-ttk) widgets, ``Listbox``, ``Text`` and ``Menu``, take their
colours directly and are handled by ``style_listbox`` / ``style_text``.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

# -- palette ---------------------------------------------------------------
BG = "#151515"  # window background
PANEL = "#1e1e1e"  # entries, comboboxes, group interiors
DEEP = "#0f0f0f"  # log and list backgrounds, troughs
BORDER = "#343434"
BORDER_BRIGHT = "#4d4d4d"
OUTLINE = "#2c5f6b"  # cool hairline around the main content panels

TEXT = "#d7d7d7"
TEXT_DIM = "#7e7e7e"
TEXT_OFF = "#565656"  # disabled

ACCENT = "#ff6a1f"  # orange: titles, meters, selection
ACCENT_SOFT = "#c8521a"
ACCENT_GLOW = "#ff8845"
DANGER = "#d81c0c"
DANGER_GLOW = "#ff2d19"
WARN = "#ffb02e"

BUTTON = "#2b2b2b"
BUTTON_HOVER = "#383838"
BUTTON_DOWN = "#464646"
BUTTON_OFF = "#1d1d1d"

# -- fonts -----------------------------------------------------------------
if sys.platform.startswith("win"):
    FAMILY = "Consolas"
elif sys.platform == "darwin":
    FAMILY = "Menlo"
else:
    FAMILY = "DejaVu Sans Mono"

SIZE = 9
FONT = (FAMILY, SIZE)
FONT_BOLD = (FAMILY, SIZE, "bold")
FONT_TITLE = (FAMILY, SIZE + 3, "bold")
FONT_STATUS = (FAMILY, SIZE + 1, "bold")

# Style names the interface asks for by name.
TITLE = "Title.TLabel"
STATUS = "Status.TLabel"
DIM = "Dim.TLabel"
ACCENT_LABEL = "Accent.TLabel"
ACCENT_BUTTON = "Accent.TButton"
DANGER_BUTTON = "Danger.TButton"
TOOL_BUTTON = "Tool.TButton"
METER = "Meter.Horizontal.TProgressbar"


def apply(root: tk.Misc) -> ttk.Style:
    """Point every widget class at the palette above."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # very old Tk without clam
        pass

    _fonts()
    root.configure(background=BG)

    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        fieldbackground=PANEL,
        troughcolor=DEEP,
        bordercolor=BORDER,
        lightcolor=BG,
        darkcolor=BG,
        focuscolor=ACCENT_SOFT,
        font=FONT,
    )

    # -- labels ----------------------------------------------------------
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure(DIM, background=BG, foreground=TEXT_DIM)
    style.configure(ACCENT_LABEL, background=BG, foreground=ACCENT)
    style.configure(TITLE, background=BG, foreground=ACCENT, font=FONT_TITLE)
    style.configure(STATUS, background=BG, foreground=TEXT, font=FONT_STATUS)

    # -- frames ----------------------------------------------------------
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure(
        "TLabelframe",
        background=BG,
        bordercolor=BORDER,
        lightcolor=BG,
        darkcolor=BG,
        borderwidth=1,
        relief="solid",
    )
    style.configure("TLabelframe.Label", background=BG, foreground=TEXT_DIM, font=FONT_BOLD)

    # -- buttons: flat, thin outline, no focus ring ----------------------
    for name in ("TButton", TOOL_BUTTON, ACCENT_BUTTON, DANGER_BUTTON):
        style.configure(
            name,
            background=BUTTON,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BUTTON,
            darkcolor=BUTTON,
            borderwidth=1,
            relief="flat",
            focusthickness=0,
            focuscolor="",
            padding=(10, 4),
            anchor="center",
            font=FONT,
        )
        style.map(
            name,
            background=[("disabled", BUTTON_OFF), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOVER)],
            lightcolor=[("disabled", BUTTON_OFF), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOVER)],
            darkcolor=[("disabled", BUTTON_OFF), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOVER)],
            foreground=[("disabled", TEXT_OFF)],
            bordercolor=[("active", BORDER_BRIGHT), ("focus", BORDER_BRIGHT)],
        )

    style.configure(TOOL_BUTTON, padding=(8, 2))
    # The primary action keeps the dark body and takes the accent in its text.
    style.configure(ACCENT_BUTTON, foreground=ACCENT, font=FONT_BOLD)
    style.map(
        ACCENT_BUTTON,
        foreground=[("disabled", TEXT_OFF), ("active", ACCENT_GLOW)],
        bordercolor=[("active", ACCENT_SOFT), ("!disabled", ACCENT_SOFT), ("disabled", BORDER)],
    )
    # Anything that ends a call is red, like the Quit button.
    style.configure(DANGER_BUTTON, background=DANGER, foreground="#ffffff", font=FONT_BOLD)
    for option in ("background", "lightcolor", "darkcolor"):
        style.map(
            DANGER_BUTTON,
            **{
                option: [
                    ("disabled", BUTTON_OFF),
                    ("pressed", DANGER),
                    ("active", DANGER_GLOW),
                    ("!disabled", DANGER),
                ]
            },
        )
    style.map(DANGER_BUTTON, foreground=[("disabled", TEXT_OFF)], bordercolor=[("!disabled", DANGER)])

    # -- checkbuttons ----------------------------------------------------
    style.configure(
        "TCheckbutton",
        background=BG,
        foreground=TEXT,
        indicatorbackground=DEEP,
        indicatorforeground=BG,
        bordercolor=BORDER,
        lightcolor=DEEP,
        darkcolor=DEEP,
        focusthickness=0,
        focuscolor="",
        padding=(2, 3),
    )
    style.map(
        "TCheckbutton",
        indicatorbackground=[("disabled", BUTTON_OFF), ("selected", ACCENT), ("active", BUTTON_HOVER)],
        indicatorforeground=[("selected", BG)],
        foreground=[("disabled", TEXT_OFF), ("active", "#ffffff")],
        background=[("active", BG)],
        bordercolor=[("active", BORDER_BRIGHT)],
    )

    # -- text entry widgets ----------------------------------------------
    style.configure(
        "TEntry",
        fieldbackground=PANEL,
        foreground=TEXT,
        insertcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=PANEL,
        darkcolor=PANEL,
        selectbackground=ACCENT_SOFT,
        selectforeground="#ffffff",
        padding=3,
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", BG), ("readonly", PANEL)],
        foreground=[("disabled", TEXT_OFF)],
        bordercolor=[("focus", ACCENT_SOFT)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=PANEL,
        background=BUTTON,
        foreground=TEXT,
        arrowcolor=ACCENT,
        insertcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=PANEL,
        darkcolor=PANEL,
        selectbackground=ACCENT_SOFT,
        selectforeground="#ffffff",
        padding=3,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", BG), ("readonly", PANEL)],
        foreground=[("disabled", TEXT_OFF)],
        arrowcolor=[("disabled", TEXT_OFF), ("active", ACCENT_GLOW)],
        bordercolor=[("focus", ACCENT_SOFT), ("active", BORDER_BRIGHT)],
    )
    # The dropdown list is a classic Listbox inside a popup window.
    for option, value in (
        ("background", DEEP),
        ("foreground", TEXT),
        ("selectBackground", ACCENT),
        ("selectForeground", BG),
        ("borderWidth", "0"),
        ("font", f"{{{FAMILY}}} {SIZE}"),
    ):
        root.option_add(f"*TCombobox*Listbox.{option}", value)

    style.configure(
        "TSpinbox",
        fieldbackground=PANEL,
        foreground=TEXT,
        arrowcolor=ACCENT,
        insertcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=PANEL,
        darkcolor=PANEL,
        padding=3,
    )
    style.map("TSpinbox", arrowcolor=[("disabled", TEXT_OFF), ("active", ACCENT_GLOW)])

    # -- sliders, meters, scrollbars -------------------------------------
    style.configure(
        "Horizontal.TScale",
        background=ACCENT,
        troughcolor=DEEP,
        bordercolor=BORDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT_SOFT,
    )
    style.map("Horizontal.TScale", background=[("active", ACCENT_GLOW), ("disabled", TEXT_OFF)])

    style.configure(
        METER,
        background=ACCENT,
        troughcolor=DEEP,
        bordercolor=BORDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT_SOFT,
        thickness=12,
    )

    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=BUTTON,
            troughcolor=DEEP,
            bordercolor=BORDER,
            arrowcolor=TEXT_DIM,
            lightcolor=BUTTON,
            darkcolor=BUTTON,
            borderwidth=1,
            relief="flat",
        )
        style.map(
            f"{orient}.TScrollbar",
            background=[("pressed", ACCENT_SOFT), ("active", BUTTON_HOVER)],
            arrowcolor=[("active", ACCENT)],
        )

    return style


def _fonts() -> None:
    try:
        import tkinter.font as tkfont

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont"):
            tkfont.nametofont(name).configure(family=FAMILY, size=SIZE)
    except Exception:  # noqa: BLE001 - fonts are cosmetic
        pass


def style_listbox(widget: tk.Listbox) -> None:
    widget.configure(
        background=DEEP,
        foreground=TEXT,
        selectbackground=ACCENT,
        selectforeground=BG,
        disabledforeground=TEXT_DIM,
        highlightthickness=1,
        highlightbackground=OUTLINE,
        highlightcolor=ACCENT_SOFT,
        borderwidth=0,
        relief="flat",
        activestyle="none",
        font=FONT,
    )


def style_text(widget: tk.Text) -> None:
    widget.configure(
        background=DEEP,
        foreground=TEXT,
        insertbackground=ACCENT,
        selectbackground=ACCENT_SOFT,
        selectforeground="#ffffff",
        highlightthickness=1,
        highlightbackground=OUTLINE,
        highlightcolor=OUTLINE,
        borderwidth=0,
        relief="flat",
        font=FONT,
    )


def style_menu(widget: tk.Menu) -> None:
    widget.configure(
        background=PANEL,
        foreground=TEXT,
        activebackground=ACCENT,
        activeforeground=BG,
        disabledforeground=TEXT_OFF,
        borderwidth=1,
        relief="flat",
        font=FONT,
    )
