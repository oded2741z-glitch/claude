"""Entry point: ``python -m lanphone``."""

from __future__ import annotations

import argparse
import sys


def _list_devices() -> int:
    from . import audio as audiolib

    try:
        inputs, outputs = audiolib.list_devices()
    except audiolib.AudioUnavailable as exc:
        print(f"audio unavailable: {exc}", file=sys.stderr)
        return 2
    print("inputs:")
    for dev in inputs:
        print(f"  [{dev.index:>2}] {dev.label}  ({dev.channels} ch, {dev.samplerate:.0f} Hz)")
    print("outputs:")
    for dev in outputs:
        print(f"  [{dev.index:>2}] {dev.label}  ({dev.channels} ch, {dev.samplerate:.0f} Hz)")
    if not inputs:
        print("no microphone found - connect a headset and run again", file=sys.stderr)
    if not outputs:
        print("no output found - connect a headset and run again", file=sys.stderr)
    return 0


def _network_info() -> int:
    from . import net
    from .config import AUDIO_PORT, DISCOVERY_PORT, SIGNALING_PORT

    ip = net.local_ip()
    print(f"local address : {ip}")
    print(f"broadcast     : {', '.join(net.broadcast_targets(ip))}")
    print(f"ports         : signalling TCP {SIGNALING_PORT}, discovery UDP {DISCOVERY_PORT}, audio UDP {AUDIO_PORT}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lanphone", description=__doc__)
    parser.add_argument("--list-devices", action="store_true", help="print the audio devices and exit")
    parser.add_argument("--network", action="store_true", help="print local address and ports, then exit")
    args = parser.parse_args(argv)

    if args.list_devices:
        return _list_devices()
    if args.network:
        return _network_info()

    try:
        from .gui import main as gui_main
    except ImportError as exc:  # tkinter missing
        print(f"cannot start the interface: {exc}", file=sys.stderr)
        print("On Windows use the python.org installer (it includes tkinter).", file=sys.stderr)
        return 2
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
