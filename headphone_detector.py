#!/usr/bin/env python3
"""Headphone connection detector.

Detects whether headphones (wired or Bluetooth) are currently connected,
on Windows, Linux, or macOS. Uses only the standard library — platform
audio state is queried through the native system tools.

Usage:
    python headphone_detector.py            # one-shot check
    python headphone_detector.py --watch    # keep monitoring for changes
    python headphone_detector.py --json     # machine-readable output
"""

import argparse
import glob
import json
import platform
import re
import subprocess
import sys
import time

# Keywords that identify a headphone-like audio device by name.
HEADPHONE_KEYWORDS = (
    "headphone", "headphones", "headset", "earphone", "earbud",
    "airpods", "buds", "אוזניות",
)


def _run(cmd, timeout=10):
    """Run a command and return its stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _looks_like_headphones(name):
    lowered = name.lower()
    return any(keyword in lowered for keyword in HEADPHONE_KEYWORDS)


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------

def _linux_usb_audio():
    """USB audio devices with playback capability (headsets, USB headphones)."""
    devices = []
    try:
        with open("/proc/asound/cards") as cards_file:
            content = cards_file.read()
    except OSError:
        return devices
    # Lines look like: " 1 [Headset ]: USB-Audio - Logitech USB Headset"
    for match in re.finditer(
        r"^\s*(\d+)\s+\[.*?\]:\s+USB-Audio\s+-\s+(.+)$", content, re.M
    ):
        card, name = match.group(1), match.group(2).strip()
        # Only cards with a playback PCM stream — rules out USB webcams/mics.
        if glob.glob(f"/proc/asound/card{card}/pcm*p"):
            devices.append(f"{name} (USB)")
    return devices


def detect_linux():
    devices = []

    devices.extend(_linux_usb_audio())

    # PulseAudio / PipeWire: inspect sink ports and their jack availability.
    output = _run(["pactl", "list", "sinks"])
    if output is not None:
        current_sink_desc = ""
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("Description:"):
                current_sink_desc = stripped.split(":", 1)[1].strip()
                # Bluetooth headphones show up as their own sink.
                if _looks_like_headphones(current_sink_desc):
                    devices.append(current_sink_desc)
            # Wired headphones appear as a port whose jack reports
            # "availability: available" when something is plugged in.
            match = re.match(
                r"([\w\-\+]+):\s+(.*?)\s+\(.*availab", stripped
            )
            if match and _looks_like_headphones(match.group(1) + match.group(2)):
                if "not available" not in stripped:
                    devices.append(
                        f"{match.group(2)} ({current_sink_desc})"
                    )

    # ALSA fallback: jack detection controls report on/off directly.
    if not devices:
        output = _run(["bash", "-c", "amixer contents 2>/dev/null"])
        if output is not None:
            blocks = output.split("numid=")
            for block in blocks:
                if "Headphone Jack" in block and "values=on" in block:
                    devices.append("Headphone Jack (ALSA)")

    # Bluetooth fallback: connected devices of audio class.
    output = _run(["bash", "-c", "bluetoothctl devices Connected 2>/dev/null"])
    if output is not None:
        for line in output.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3 and _looks_like_headphones(parts[2]):
                devices.append(f"{parts[2]} (Bluetooth)")

    return devices


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------

def detect_windows():
    devices = []

    # Audio endpoints whose name identifies them as headphones/headsets.
    script = (
        "Get-PnpDevice -Class AudioEndpoint -Status OK | "
        "ForEach-Object { $_.FriendlyName }"
    )
    output = _run(["powershell", "-NoProfile", "-Command", script], timeout=30)
    if output is not None:
        for line in output.splitlines():
            name = line.strip()
            if name and _looks_like_headphones(name):
                devices.append(name)

    # USB audio devices (USB headsets/headphones), regardless of name.
    script = (
        "Get-PnpDevice -Class MEDIA -Status OK | "
        "Where-Object { $_.InstanceId -like 'USB*' } | "
        "ForEach-Object { $_.FriendlyName }"
    )
    output = _run(["powershell", "-NoProfile", "-Command", script], timeout=30)
    if output is not None:
        for line in output.splitlines():
            name = line.strip()
            if name:
                devices.append(f"{name} (USB)")

    return devices


# --------------------------------------------------------------------------
# macOS
# --------------------------------------------------------------------------

def detect_macos():
    devices = []
    output = _run(["system_profiler", "SPAudioDataType", "-json"], timeout=30)
    if output is not None:
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            data = {}
        for group in data.get("SPAudioDataType", []):
            for item in group.get("_items", []):
                name = item.get("_name", "")
                is_output = item.get("coreaudio_device_output") is not None
                transport = item.get("coreaudio_device_transport", "")
                if not is_output:
                    continue
                if transport == "coreaudio_device_type_usb":
                    devices.append(f"{name} (USB)")
                elif (
                    _looks_like_headphones(name)
                    or transport == "coreaudio_device_type_bluetooth"
                ):
                    devices.append(name)
    return devices


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def detect():
    """Return a list of connected headphone device names."""
    system = platform.system()
    if system == "Linux":
        devices = detect_linux()
    elif system == "Windows":
        devices = detect_windows()
    elif system == "Darwin":
        devices = detect_macos()
    else:
        raise RuntimeError(f"Unsupported platform: {system}")
    # Deduplicate while preserving order.
    return list(dict.fromkeys(devices))


def report(devices, as_json):
    if as_json:
        print(json.dumps(
            {"connected": bool(devices), "devices": devices},
            ensure_ascii=False,
        ))
        return
    if devices:
        print("🎧 Headphones connected:")
        for name in devices:
            print(f"  - {name}")
    else:
        print("🔇 No headphones connected.")


def main():
    parser = argparse.ArgumentParser(
        description="Detect whether headphones are connected."
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="keep running and report when headphones connect/disconnect",
    )
    parser.add_argument(
        "--interval", type=float, default=2.0,
        help="polling interval in seconds for --watch (default: 2)",
    )
    parser.add_argument(
        "--json", action="store_true", help="output JSON instead of text",
    )
    args = parser.parse_args()

    try:
        devices = detect()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2

    report(devices, args.json)

    if args.watch:
        previous = bool(devices)
        try:
            while True:
                time.sleep(args.interval)
                devices = detect()
                connected = bool(devices)
                if connected != previous:
                    timestamp = time.strftime("%H:%M:%S")
                    if not args.json:
                        print(f"[{timestamp}] state changed:")
                    report(devices, args.json)
                    previous = connected
        except KeyboardInterrupt:
            pass
        return 0

    # Exit code mirrors the result so scripts can use it directly:
    # 0 = connected, 1 = not connected.
    return 0 if devices else 1


if __name__ == "__main__":
    sys.exit(main())
