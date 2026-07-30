"""Entry point: ``python -m lanphone``."""

from __future__ import annotations

import argparse
import sys


def _dialog(text: str, error: bool = False) -> None:
    """Show text in a message box, for when there is nowhere to print it."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        from .config import APP_NAME

        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(APP_NAME, text)
        root.destroy()
    except Exception:  # noqa: BLE001 - nothing left to report with
        pass


def _report(text: str, error: bool = False) -> None:
    """Print, or pop up a dialog when there is no console.

    A windowed build (``LANPhone.exe``) has ``sys.stdout`` set to None, and
    printing to it would raise, so the text goes into a message box instead.
    """
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        try:
            print(text, file=stream)
            return
        except (OSError, ValueError):
            pass  # console went away mid-write; fall through to the dialog
    _dialog(text, error)


def _describe_devices() -> tuple[str, int]:
    from . import audio as audiolib

    try:
        inputs, outputs = audiolib.list_devices()
    except audiolib.AudioUnavailable as exc:
        return f"audio unavailable: {exc}", 2

    lines = ["inputs:"]
    lines += [
        f"  [{dev.index:>2}] {dev.label}  ({dev.channels} ch, {dev.samplerate:.0f} Hz)"
        for dev in inputs
    ] or ["  (none)"]
    lines.append("outputs:")
    lines += [
        f"  [{dev.index:>2}] {dev.label}  ({dev.channels} ch, {dev.samplerate:.0f} Hz)"
        for dev in outputs
    ] or ["  (none)"]
    if not inputs:
        lines.append("no microphone found - connect a headset and run again")
    if not outputs:
        lines.append("no audio output found - connect a headset and run again")
    return "\n".join(lines), 0 if (inputs and outputs) else 1


def _describe_network() -> str:
    from . import net
    from .config import AUDIO_PORT, DISCOVERY_PORT, SIGNALING_PORT

    ip = net.local_ip()
    return "\n".join(
        [
            f"local address : {ip}",
            f"broadcast     : {', '.join(net.broadcast_targets(ip))}",
            f"ports         : signalling TCP {SIGNALING_PORT}, "
            f"discovery UDP {DISCOVERY_PORT}, audio UDP {AUDIO_PORT}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lanphone", description=__doc__)
    parser.add_argument("--list-devices", action="store_true", help="show the audio devices and exit")
    parser.add_argument("--network", action="store_true", help="show local address and ports, then exit")
    args = parser.parse_args(argv)

    if args.list_devices:
        text, code = _describe_devices()
        _report(text, error=code == 2)
        return code
    if args.network:
        _report(_describe_network())
        return 0

    try:
        from .gui import main as gui_main
    except ImportError as exc:  # tkinter missing
        _report(
            f"cannot start the interface: {exc}\n"
            "On Windows use the python.org installer (it includes tkinter).",
            error=True,
        )
        return 2
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
