"""Minimal logical -> visual reordering for right-to-left text.

Tk has no bidirectional text support: it draws characters strictly in logical
order from left to right, so a Hebrew label ends up mirrored on screen.  The
helper below converts a logical string into the visual order Tk needs, which is
enough for labels, buttons and log lines (base direction is assumed RTL).

``to_visual`` is deliberately a small subset of the Unicode bidi algorithm:
strong runs keep their direction, neutral runs adopt the direction of their
neighbours (falling back to RTL), and mirrored characters are swapped inside
RTL runs.
"""

from __future__ import annotations

import unicodedata

_MIRROR = {
    "(": ")", ")": "(",
    "[": "]", "]": "[",
    "{": "}", "}": "{",
    "<": ">", ">": "<",
}

_RTL_BIDI = {"R", "AL", "AN"}
_LTR_BIDI = {"L", "EN"}


def _direction(ch: str) -> str:
    """Return 'R', 'L' or 'N' for a single character."""
    bidi = unicodedata.bidirectional(ch)
    if bidi in _RTL_BIDI:
        return "R"
    if bidi in _LTR_BIDI:
        return "L"
    return "N"


def contains_rtl(text: str) -> bool:
    return any(_direction(ch) == "R" for ch in text)


def _runs(line: str) -> list[tuple[str, str]]:
    """Split a line into (direction, text) runs."""
    runs: list[tuple[str, str]] = []
    for ch in line:
        d = _direction(ch)
        if runs and runs[-1][0] == d:
            runs[-1] = (d, runs[-1][1] + ch)
        else:
            runs.append((d, ch))
    return runs


def _resolve_neutrals(runs: list[tuple[str, str]], base: str) -> list[tuple[str, str]]:
    """Give every neutral run a direction, then merge neighbours that agree.

    Merging matters: "192.168.1.5" is three digit runs separated by neutral
    dots, and they have to be reordered as one block or the address comes out
    backwards.
    """
    resolved: list[tuple[str, str]] = []
    for i, (d, text) in enumerate(runs):
        if d == "N":
            before = next((r[0] for r in reversed(runs[:i]) if r[0] != "N"), None)
            after = next((r[0] for r in runs[i + 1:] if r[0] != "N"), None)
            d = before if (before is not None and before == after) else base
        if resolved and resolved[-1][0] == d:
            resolved[-1] = (d, resolved[-1][1] + text)
        else:
            resolved.append((d, text))
    return resolved


def _line_to_visual(line: str, base: str = "R") -> str:
    if not contains_rtl(line):
        return line

    # Trailing/leading whitespace is kept where the caller put it: reordering it
    # would shift text away from the widget edge for no reason.
    stripped = line.strip(" \t")
    if not stripped:
        return line
    lead = line[: line.index(stripped[0])]
    trail = line[line.index(stripped[0]) + len(stripped):]

    runs = _resolve_neutrals(_runs(stripped), base)
    out: list[str] = []
    for d, text in reversed(runs):
        if d == "R":
            out.append("".join(_MIRROR.get(ch, ch) for ch in reversed(text)))
        else:
            out.append(text)
    return lead + "".join(out) + trail


def to_visual(text: str, base: str = "R") -> str:
    """Reorder ``text`` so Tk's left-to-right drawing shows it correctly."""
    if not text or not contains_rtl(text):
        return text
    return "\n".join(_line_to_visual(line, base) for line in text.split("\n"))
