#!/usr/bin/env python3
"""Headphone connection detector — single-file app (CLI + GUI).

Detects whether headphones (wired, USB, or Bluetooth) are connected, on
Windows, Linux, or macOS. Uses only the standard library.

Every connect/disconnect event is appended to headphone_log.txt next to
this script. The GUI can also open a chosen program when headphones
connect and close it when they disconnect.

Usage:
    python headphone_detector.py            # open the GUI (default)
    python headphone_detector.py --cli      # one-shot check in the terminal
    python headphone_detector.py --watch    # keep monitoring in the terminal
    python headphone_detector.py --json     # machine-readable output
"""

import argparse
import glob
import json
import os
import platform
import re
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "headphone_log.txt")
SETTINGS_FILE = os.path.join(BASE_DIR, "headphone_settings.json")

# Keywords that identify a headphone-like audio device by name.
HEADPHONE_KEYWORDS = (
    "headphone", "headphones", "headset", "earphone", "earbud",
    "airpods", "buds", "אוזניות",
)

# Software-only audio devices that are always "present" and would make the
# detector report connected forever (Oculus/Steam/VB-Audio virtual sinks).
VIRTUAL_DEVICE_KEYWORDS = (
    "virtual", "vb-audio", "voicemeeter", "steam streaming", "cable input",
    "cable output", "wave link", "nvidia broadcast",
)


def _is_virtual_device(name):
    lowered = name.lower()
    return any(keyword in lowered for keyword in VIRTUAL_DEVICE_KEYWORDS)


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


def append_log(message):
    """Append a timestamped line to headphone_log.txt and return it."""
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " - " + message
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except OSError:
        pass
    return line


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
# Detection entry point
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
    devices = [name for name in devices if not _is_virtual_device(name)]
    # Deduplicate while preserving order.
    return list(dict.fromkeys(devices))


# --------------------------------------------------------------------------
# Settings (persisted next to the script)
# --------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "program_path": "",
    "open_on_connect": False,
    "close_on_disconnect": False,
}


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as settings_file:
            data = json.load(settings_file)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    settings = dict(DEFAULT_SETTINGS)
    for key in settings:
        if key in data:
            settings[key] = data[key]
    return settings


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as settings_file:
            json.dump(settings, settings_file, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Program launcher (open on connect / close on disconnect)
# --------------------------------------------------------------------------

class ProgramController:
    """Opens and closes the user-chosen program on headphone events."""

    def __init__(self):
        self.process = None

    def open(self, path):
        path = path.strip()
        if not path:
            return "No program selected."
        if self.process is not None and self.process.poll() is None:
            return "Program is already running."
        try:
            self.process = subprocess.Popen([path])
        except OSError as error:
            return f"Could not open program: {error}"
        return f"Opened program: {os.path.basename(path)}"

    def close(self, path):
        path = path.strip()
        # First choice: terminate the process we started ourselves.
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            return "Closed program."
        self.process = None
        if not path:
            return "No program selected."
        # Fallback: close by executable name (works even if the program
        # was already running before this tool started it).
        exe_name = os.path.basename(path)
        if platform.system() == "Windows":
            result = _run(["taskkill", "/IM", exe_name, "/F"], timeout=15)
        else:
            result = _run(["pkill", "-f", exe_name], timeout=15)
        if result is None:
            return f"Program not running: {exe_name}"
        return f"Closed program: {exe_name}"


# --------------------------------------------------------------------------
# GUI (tkinter — part of the standard library)
# --------------------------------------------------------------------------

POLL_INTERVAL_MS = 2000

CONNECTED_COLOR = "#1a7f37"
DISCONNECTED_COLOR = "#c62828"


class HeadphoneApp:
    def __init__(self, root, tk, ttk, filedialog):
        self.root = root
        self.tk = tk
        self.filedialog = filedialog
        root.title("Headphone Detector")
        root.geometry("520x600")
        root.minsize(440, 520)

        self.previous_connected = None
        self.after_id = None
        self.controller = ProgramController()

        settings = load_settings()

        # Big status area.
        self.icon_label = tk.Label(root, text="…", font=("Segoe UI Emoji", 44))
        self.icon_label.pack(pady=(15, 0))

        self.status_label = tk.Label(root, text="Checking…",
                                     font=("Arial", 17, "bold"))
        self.status_label.pack(pady=(5, 10))

        # Connected devices list.
        devices_frame = ttk.LabelFrame(root, text="Connected devices")
        devices_frame.pack(fill="both", expand=True, padx=15, pady=(0, 8))

        self.devices_list = tk.Listbox(devices_frame, font=("Arial", 11), height=4)
        self.devices_list.pack(fill="both", expand=True, padx=8, pady=8)

        # Program actions.
        actions_frame = ttk.LabelFrame(root, text="Program to open/close")
        actions_frame.pack(fill="x", padx=15, pady=(0, 8))

        path_row = ttk.Frame(actions_frame)
        path_row.pack(fill="x", padx=8, pady=(8, 4))

        self.program_var = tk.StringVar(value=settings["program_path"])
        self.program_entry = ttk.Entry(path_row, textvariable=self.program_var)
        self.program_entry.pack(side="left", fill="x", expand=True)

        browse_button = ttk.Button(path_row, text="Browse…", command=self.browse)
        browse_button.pack(side="left", padx=(6, 0))

        self.open_var = tk.BooleanVar(value=settings["open_on_connect"])
        open_check = ttk.Checkbutton(
            actions_frame,
            text="Open the program when headphones connect",
            variable=self.open_var, command=self.on_open_toggled,
        )
        open_check.pack(anchor="w", padx=8)

        self.close_var = tk.BooleanVar(value=settings["close_on_disconnect"])
        close_check = ttk.Checkbutton(
            actions_frame,
            text="Close the program when headphones disconnect",
            variable=self.close_var, command=self.on_close_toggled,
        )
        close_check.pack(anchor="w", padx=8, pady=(0, 8))

        # Event log.
        log_frame = ttk.LabelFrame(
            root, text="Event log (saved to headphone_log.txt)"
        )
        log_frame.pack(fill="both", expand=True, padx=15, pady=(0, 8))

        self.log_text = tk.Text(
            log_frame, font=("Arial", 10), height=6, state="disabled"
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # Bottom buttons.
        buttons_row = ttk.Frame(root)
        buttons_row.pack(pady=(0, 12))

        refresh_button = ttk.Button(buttons_row, text="Refresh now",
                                    command=self.refresh)
        refresh_button.pack(side="left", padx=4)

        open_log_button = ttk.Button(buttons_row, text="Open log file",
                                     command=self.open_log_file)
        open_log_button.pack(side="left", padx=4)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()

    # -- helpers ----------------------------------------------------------

    def browse(self):
        if platform.system() == "Windows":
            filetypes = [("Programs", "*.exe"), ("All files", "*.*")]
        else:
            filetypes = [("All files", "*.*")]
        path = self.filedialog.askopenfilename(
            title="Choose a program", filetypes=filetypes
        )
        if path:
            self.program_var.set(path)
            self.persist_settings()

    def persist_settings(self):
        save_settings({
            "program_path": self.program_var.get(),
            "open_on_connect": self.open_var.get(),
            "close_on_disconnect": self.close_var.get(),
        })

    def on_open_toggled(self):
        # If headphones are already connected when the option is turned on,
        # act immediately instead of waiting for the next connect event.
        self.persist_settings()
        if self.open_var.get() and self.previous_connected:
            self.log_event(self.controller.open(self.program_var.get()))

    def on_close_toggled(self):
        self.persist_settings()
        if self.close_var.get() and self.previous_connected is False:
            self.log_event(self.controller.close(self.program_var.get()))

    def open_log_file(self):
        if not os.path.exists(LOG_FILE):
            append_log("Log file created.")
        try:
            if platform.system() == "Windows":
                os.startfile(LOG_FILE)  # noqa: attribute exists on Windows
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", LOG_FILE])
            else:
                subprocess.Popen(["xdg-open", LOG_FILE])
        except OSError as error:
            self.show_log(f"Could not open log file: {error}")

    def show_log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def log_event(self, message):
        """Write to the TXT log file and mirror it in the window."""
        self.show_log(append_log(message))

    # -- main loop --------------------------------------------------------

    def refresh(self):
        # Cancel any pending poll so the manual refresh button can't stack
        # multiple polling loops.
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        try:
            devices = detect()
        except RuntimeError as error:
            self.icon_label.configure(text="⚠️")
            self.status_label.configure(text=str(error), fg="black")
            return
        connected = bool(devices)

        if connected:
            self.icon_label.configure(text="🎧")
            self.status_label.configure(text="Headphones connected",
                                        fg=CONNECTED_COLOR)
        else:
            self.icon_label.configure(text="🔇")
            self.status_label.configure(text="No headphones connected",
                                        fg=DISCONNECTED_COLOR)

        self.devices_list.delete(0, "end")
        for name in devices:
            self.devices_list.insert("end", f"  {name}")

        if connected != self.previous_connected:
            if self.previous_connected is None:
                state = "CONNECTED" if connected else "DISCONNECTED"
                detail = " - " + ", ".join(devices) if devices else ""
                self.log_event(f"Started, initial state: {state}{detail}")
                # Headphones already plugged in at startup count as connected.
                if connected and self.open_var.get():
                    self.log_event(self.controller.open(self.program_var.get()))
            elif connected:
                self.log_event("CONNECTED - " + ", ".join(devices))
                if self.open_var.get():
                    self.log_event(self.controller.open(self.program_var.get()))
            else:
                self.log_event("DISCONNECTED")
                if self.close_var.get():
                    self.log_event(self.controller.close(self.program_var.get()))
            self.previous_connected = connected

        self.after_id = self.root.after(POLL_INTERVAL_MS, self.refresh)

    def on_close(self):
        self.persist_settings()
        self.root.destroy()


def run_gui():
    """Open the GUI. Returns False if tkinter/display is unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
        root = tk.Tk()
    except Exception as error:
        print(f"GUI unavailable ({error}); falling back to terminal mode.",
              file=sys.stderr)
        return False
    HeadphoneApp(root, tk, ttk, filedialog)
    root.mainloop()
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

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


def run_cli(args):
    try:
        devices = detect()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2

    report(devices, args.json)

    if args.watch:
        previous = bool(devices)
        state = "CONNECTED" if previous else "DISCONNECTED"
        append_log(f"Watch started, initial state: {state}")
        try:
            while True:
                time.sleep(args.interval)
                devices = detect()
                connected = bool(devices)
                if connected != previous:
                    if connected:
                        append_log("CONNECTED - " + ", ".join(devices))
                    else:
                        append_log("DISCONNECTED")
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


def main():
    parser = argparse.ArgumentParser(
        description="Detect whether headphones are connected."
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="run a one-shot check in the terminal instead of opening the GUI",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="keep running in the terminal and report connect/disconnect",
    )
    parser.add_argument(
        "--interval", type=float, default=2.0,
        help="polling interval in seconds for --watch (default: 2)",
    )
    parser.add_argument(
        "--json", action="store_true", help="output JSON instead of text",
    )
    args = parser.parse_args()

    # Any terminal-oriented flag selects CLI mode; the default is the GUI.
    if args.cli or args.watch or args.json:
        return run_cli(args)
    if run_gui():
        return 0
    return run_cli(args)


if __name__ == "__main__":
    sys.exit(main())
