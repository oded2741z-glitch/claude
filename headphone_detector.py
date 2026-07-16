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
import queue
import re
import subprocess
import sys
import threading
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
# Windows system-tray icon (notification area, next to the clock)
# --------------------------------------------------------------------------

class WindowsTrayIcon(threading.Thread):
    """Tray icon built directly on the Win32 API via ctypes (no packages).

    Runs its own native message loop in a background thread and posts
    "show" / "exit" commands to self.commands for the GUI thread to act on.
    """

    TRAY_MESSAGE = 0x0400 + 20  # WM_USER + 20
    CMD_OPEN, CMD_EXIT = 1, 2

    NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    WM_DESTROY, WM_CLOSE = 0x2, 0x10
    WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x202, 0x203, 0x205

    def __init__(self, tooltip):
        super().__init__(daemon=True)
        self.commands = queue.Queue()
        self.tooltip = tooltip
        self.hwnd = None
        self.hicon = None
        self.ready = threading.Event()

    # -- runs in the tray thread -------------------------------------------

    def run(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = user32 = ctypes.windll.user32
        self.shell32 = shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )
        # Declare handle-typed returns and arguments so ctypes uses
        # pointer-sized values on 64-bit Python (otherwise handles are
        # truncated to a C int and CreateWindowExW raises OverflowError).
        HMENU = wintypes.HMENU
        HWND = wintypes.HWND
        user32.CreateWindowExW.restype = HWND
        user32.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, HWND, HMENU,
            wintypes.HINSTANCE, wintypes.LPVOID,
        )
        user32.LoadIconW.restype = wintypes.HICON
        user32.CreatePopupMenu.restype = HMENU
        user32.AppendMenuW.argtypes = (
            HMENU, wintypes.UINT, ctypes.c_void_p, wintypes.LPCWSTR,
        )
        user32.TrackPopupMenu.argtypes = (
            HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, HWND, wintypes.LPVOID,
        )
        user32.DestroyMenu.argtypes = (HMENU,)
        user32.SetForegroundWindow.argtypes = (HWND,)
        user32.PostMessageW.argtypes = (
            HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )
        user32.PostQuitMessage.argtypes = (ctypes.c_int,)
        shell32.ExtractIconW.restype = wintypes.HICON
        shell32.ExtractIconW.argtypes = (
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        )
        shell32.Shell_NotifyIconW.argtypes = (
            wintypes.DWORD, ctypes.c_void_p,
        )
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256),
                ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", wintypes.DWORD),
            ]

        self.NOTIFYICONDATAW = NOTIFYICONDATAW

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == self.TRAY_MESSAGE:
                if lparam in (self.WM_LBUTTONUP, self.WM_LBUTTONDBLCLK):
                    self.commands.put("show")
                elif lparam == self.WM_RBUTTONUP:
                    self._show_menu(hwnd)
                return 0
            if msg == self.WM_DESTROY:
                data = self._make_data(0)
                shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(data))
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # Keep a reference so the callback isn't garbage-collected.
        self._wndproc = WNDPROC(wndproc)

        hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = "HeadphoneDetectorTray"
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            0, wc.lpszClassName, "Headphone Detector",
            0, 0, 0, 0, 0, None, None, hinstance, None,
        )

        # Speaker icon from Windows' own audio resources; generic fallback.
        hicon = shell32.ExtractIconW(
            hinstance, r"C:\Windows\System32\mmres.dll", 0
        )
        if not hicon or hicon == 1:
            hicon = user32.LoadIconW(None, 32512)  # IDI_APPLICATION
        self.hicon = hicon

        data = self._make_data(self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP)
        shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(data))
        self.ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _make_data(self, flags):
        data = self.NOTIFYICONDATAW()
        data.cbSize = self.ctypes.sizeof(data)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = self.TRAY_MESSAGE
        data.hIcon = self.hicon
        data.szTip = self.tooltip[:127]
        return data

    def _show_menu(self, hwnd):
        user32, ctypes, wintypes = self.user32, self.ctypes, self.wintypes
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, self.CMD_OPEN, "Open")
        user32.AppendMenuW(menu, 0, self.CMD_EXIT, "Exit")
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Required so the menu closes when clicking elsewhere.
        user32.SetForegroundWindow(hwnd)
        TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
            point.x, point.y, 0, hwnd, None,
        )
        user32.DestroyMenu(menu)
        if cmd == self.CMD_OPEN:
            self.commands.put("show")
        elif cmd == self.CMD_EXIT:
            self.commands.put("exit")

    # -- safe to call from the GUI thread ----------------------------------

    def set_tooltip(self, text):
        if not self.ready.is_set():
            return
        self.tooltip = text
        data = self._make_data(self.NIF_TIP)
        self.shell32.Shell_NotifyIconW(
            self.NIM_MODIFY, self.ctypes.byref(data)
        )

    def balloon(self, title, text):
        if not self.ready.is_set():
            return
        data = self._make_data(self.NIF_INFO)
        data.szInfoTitle = title[:63]
        data.szInfo = text[:255]
        self.shell32.Shell_NotifyIconW(
            self.NIM_MODIFY, self.ctypes.byref(data)
        )

    def stop(self):
        if self.ready.is_set() and self.hwnd:
            self.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)


# --------------------------------------------------------------------------
# GUI (tkinter — part of the standard library)
# --------------------------------------------------------------------------

POLL_INTERVAL_MS = 30000  # check the connection every 30 seconds


class HeadphoneApp:
    def __init__(self, root, tk, ttk, filedialog, tray=None):
        self.root = root
        self.tk = tk
        self.filedialog = filedialog
        self.tray = tray
        root.title("Headphone Detector")
        root.geometry("520x330")
        root.minsize(440, 300)

        self.previous_connected = None
        self.after_id = None
        self.controller = ProgramController()
        self.hide_hint_shown = False

        settings = load_settings()

        # Connected devices list.
        devices_frame = ttk.LabelFrame(root, text="Connected devices")
        devices_frame.pack(fill="both", expand=True, padx=15, pady=(12, 8))

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

        if self.tray is not None:
            # Start minimized to the notification area next to the clock.
            root.withdraw()
            self.tray.balloon(
                "Headphone Detector",
                "Running here in the notification area. "
                "Click the icon to open the window.",
            )
            self.poll_tray()

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
            append_log(f"Could not open log file: {error}")

    def log_event(self, message):
        """Write the event to the TXT log file."""
        append_log(message)

    # -- system tray --------------------------------------------------------

    def poll_tray(self):
        try:
            while True:
                command = self.tray.commands.get_nowait()
                if command == "show":
                    self.show_window()
                elif command == "exit":
                    self.exit_app()
                    return
        except queue.Empty:
            pass
        self.root.after(250, self.poll_tray)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def exit_app(self):
        self.persist_settings()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

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
            self.devices_list.delete(0, "end")
            self.devices_list.insert("end", f"  Error: {error}")
            return
        connected = bool(devices)

        self.devices_list.delete(0, "end")
        if connected:
            for name in devices:
                self.devices_list.insert("end", f"  🎧 {name}")
        else:
            self.devices_list.insert("end", "  (no headphones connected)")

        if self.tray is not None and connected != self.previous_connected:
            state = ("Headphones connected" if connected
                     else "No headphones connected")
            self.tray.set_tooltip(f"Headphone Detector — {state}")

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
        # With a tray icon, closing the window keeps monitoring in the
        # background; use the tray menu's Exit to really quit.
        if self.tray is not None:
            self.persist_settings()
            self.root.withdraw()
            if not self.hide_hint_shown:
                self.hide_hint_shown = True
                self.tray.balloon(
                    "Headphone Detector",
                    "Still running here. Right-click the icon and "
                    "choose Exit to quit.",
                )
            return
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

    tray = None
    if platform.system() == "Windows":
        try:
            tray = WindowsTrayIcon("Headphone Detector")
            tray.start()
            tray.ready.wait(timeout=5)
        except Exception:
            tray = None
        if tray is not None and not tray.ready.is_set():
            tray = None  # tray thread failed; run as a normal window

    HeadphoneApp(root, tk, ttk, filedialog, tray)
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
        "--interval", type=float, default=30.0,
        help="polling interval in seconds for --watch (default: 30)",
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
