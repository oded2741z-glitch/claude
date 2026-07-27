#!/usr/bin/env python3
"""
Ouster Digital Lidar - GUI Control & Visualization
===================================================
A Tkinter GUI for Ubuntu 24.04 that wraps the official `ouster-sdk`
(as demonstrated in Ouster's "Digital Lidar SDK: Setup and Visualization"
video) and lets you:

  * Connect to an Ouster sensor by hostname / IP (e.g. os-122xxxxxxxxxx.local)
  * Read sensor metadata (product line, serial, firmware, current mode)
  * Configure the sensor (lidar mode, timestamp mode, UDP ports)
  * Live-stream scans and view RANGE / SIGNAL / REFLECTIVITY / NEAR_IR
    as destaggered 2D images inside the GUI
  * Launch Ouster's official 3D point-cloud viewer (ouster-cli ... viz)
  * Record the stream to a PCAP file and replay PCAP/OSF files offline
    (so the app is fully usable without a physical sensor)

Tested with ouster-sdk 1.0.0; also compatible with the older
`ouster.sdk.client` API (< 1.0).

Run:  python3 ouster_gui.py
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import tkinter as tk
import warnings
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning, module="ouster")

# --- ouster-sdk imports (support SDK >= 1.0 and older releases) --------------
try:
    from ouster.sdk import open_source
    try:  # ouster-sdk >= 1.0
        from ouster.sdk import core as ouster_core
        from ouster.sdk.sensor import get_config, set_config
    except ImportError:  # ouster-sdk < 1.0
        from ouster.sdk import client as ouster_core
        get_config = ouster_core.get_config
        set_config = ouster_core.set_config
    try:
        from ouster.sdk.sensor import SensorHttp
    except Exception:
        try:
            from ouster.sdk.client import SensorHttp
        except Exception:
            SensorHttp = None
    HAVE_OUSTER = True
    OUSTER_IMPORT_ERROR = None
except Exception as _e:  # SDK missing entirely
    HAVE_OUSTER = False
    OUSTER_IMPORT_ERROR = _e
    ouster_core = None
    open_source = None
    get_config = set_config = None
    SensorHttp = None

# --- MCAP export (optional: only needed for "Export to MCAP") ----------------
try:
    from mcap_protobuf.writer import Writer as McapWriter
    from foxglove_schemas_protobuf.PointCloud_pb2 import PointCloud
    from foxglove_schemas_protobuf.PackedElementField_pb2 import (
        PackedElementField)
    HAVE_MCAP = True
except Exception:
    HAVE_MCAP = False

# --- matplotlib embedded in Tk ------------------------------------------------
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


# ----------------------------------------------------------------- theme ----
class Theme:
    """Dark UI palette, applied through pure-ttk styling (no extra deps)."""
    BG        = "#12151d"   # window background
    PANEL     = "#1a1e29"   # side panel / cards
    CARD      = "#1f2431"   # inputs card surface
    FIELD     = "#262c3c"   # entry / combobox fields
    BORDER    = "#2c3345"
    FG        = "#e8ebf2"   # primary text
    MUTED     = "#8b93a7"   # secondary text
    # Python brand palette
    PY_BLUE   = "#3776AB"
    PY_BLUE_L = "#4B8BBE"   # lighter Python blue (focus, hover)
    PY_YELLOW = "#FFD43B"   # top accent strip
    ORANGE    = "#FF8C00"   # section titles
    ACCENT    = PY_BLUE_L
    LOG_BG    = "#0d1017"
    LOG_FG    = "#9fe8a9"


def apply_theme(root: tk.Tk):
    root.configure(bg=Theme.BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    base_font = tkfont.nametofont("TkDefaultFont")
    base_font.configure(size=10)
    root.option_add("*Font", base_font)

    style.configure(".", background=Theme.PANEL, foreground=Theme.FG,
                    bordercolor=Theme.BORDER, darkcolor=Theme.PANEL,
                    lightcolor=Theme.PANEL, troughcolor=Theme.FIELD,
                    focuscolor=Theme.ACCENT, selectbackground=Theme.ACCENT,
                    selectforeground=Theme.BG)

    style.configure("TFrame", background=Theme.BG)
    style.configure("Panel.TFrame", background=Theme.PANEL)

    style.configure("TLabel", background=Theme.PANEL, foreground=Theme.FG)
    style.configure("Muted.TLabel", background=Theme.PANEL,
                    foreground=Theme.MUTED)
    style.configure("Hint.TLabel", background=Theme.PANEL,
                    foreground=Theme.MUTED,
                    font=(base_font.actual("family"), 8))
    # thin orange accent strip under the title bar, matching section titles
    style.configure("HeaderStrip.TFrame", background=Theme.ORANGE)
    style.configure("Info.TLabel", background=Theme.PANEL,
                    foreground=Theme.FG, font=("monospace", 9))

    style.configure("TLabelframe", background=Theme.PANEL,
                    bordercolor=Theme.BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=Theme.PANEL,
                    foreground=Theme.ORANGE,
                    font=(base_font.actual("family"), 9, "bold"))

    # buttons: one uniform style for every button ------------------------------
    style.configure("TButton", background=Theme.FIELD, foreground=Theme.FG,
                    bordercolor=Theme.FIELD, focusthickness=1,
                    padding=(8, 6),
                    font=(base_font.actual("family"), 10, "bold"))
    style.map("TButton",
              background=[("active", Theme.BORDER),
                          ("disabled", Theme.FIELD)],
              foreground=[("disabled", Theme.MUTED)])

    style.configure("TCheckbutton", background=Theme.PANEL,
                    foreground=Theme.FG, focuscolor=Theme.PANEL)
    style.map("TCheckbutton",
              background=[("active", Theme.PANEL)],
              indicatorcolor=[("selected", Theme.ORANGE),
                              ("!selected", Theme.FIELD)])

    # inputs -------------------------------------------------------------------
    style.configure("TEntry", fieldbackground=Theme.FIELD,
                    foreground=Theme.FG, bordercolor=Theme.BORDER,
                    insertcolor=Theme.FG, padding=4)
    style.configure("TCombobox", fieldbackground=Theme.FIELD,
                    background=Theme.FIELD, foreground=Theme.FG,
                    bordercolor=Theme.BORDER, arrowcolor=Theme.ACCENT,
                    padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", Theme.FIELD)],
              foreground=[("readonly", Theme.FG)])
    root.option_add("*TCombobox*Listbox.background", Theme.FIELD)
    root.option_add("*TCombobox*Listbox.foreground", Theme.FG)
    root.option_add("*TCombobox*Listbox.selectBackground", Theme.ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", Theme.BG)


LIDAR_MODES = ["512x10", "512x20", "1024x10", "1024x20", "2048x10"]
TIMESTAMP_MODES = [
    "TIME_FROM_INTERNAL_OSC",
    "TIME_FROM_SYNC_PULSE_IN",
    "TIME_FROM_PTP_1588",
]
SETTINGS_PATH = os.path.join(os.path.expanduser("~"),
                             ".ouster_lidar_gui.json")

OPERATING_MODES = ["NORMAL", "STANDBY"]
SIGNAL_MULTIPLIERS = ["1", "2", "3", "0.5", "0.25"]
UNCHANGED = "(leave unchanged)"
UDP_PROFILES = [
    UNCHANGED,
    "RNG19_RFL8_SIG16_NIR16",        # standard single return
    "RNG19_RFL8_SIG16_NIR16_DUAL",   # dual return
    "RNG15_RFL8_NIR8",               # low data rate
    "LEGACY",
]

# (field name, plot title, colormap)
FIELD_SPECS = [
    ("RANGE", "Range [mm]", "viridis"),
    ("SIGNAL", "Signal", "magma"),
    ("REFLECTIVITY", "Reflectivity", "gray"),
    ("NEAR_IR", "Near-IR (ambient)", "cividis"),
]
FIELD_TITLES = {name: title for name, title, _ in FIELD_SPECS}


def parse_lidar_mode(mode_str: str):
    """LidarMode from string across SDK versions."""
    try:
        return ouster_core.LidarMode(mode_str)          # >= 1.0
    except (TypeError, ValueError):
        return ouster_core.LidarMode.from_string(mode_str)  # < 1.0


def source_metadata(source):
    """First sensor's SensorInfo from a scan/frame source, any SDK version."""
    si = getattr(source, "sensor_info", None)
    if isinstance(si, (list, tuple)) and si:
        return si[0]
    meta = getattr(source, "metadata", None)
    if meta is not None and not callable(meta):
        return meta
    return None


def frames_from_item(item):
    """Normalize one iteration item to a list of LidarFrame/LidarScan.

    SDK >= 1.0 yields FrameSet objects; older SDKs yield a LidarScan or a
    list of Optional[LidarScan] for multi-sensor sources.
    """
    if item is None:
        return []
    valid_frames = getattr(item, "valid_frames", None)
    if callable(valid_frames):
        return list(valid_frames())
    if isinstance(item, (list, tuple)):
        return [x for x in item if x is not None]
    return [item]


def percentile_scale(img: np.ndarray, lo=1.0, hi=99.0) -> np.ndarray:
    """Auto-exposure style scaling of a field image to [0, 1]."""
    img = img.astype(np.float64)
    vmin, vmax = np.percentile(img, [lo, hi])
    if vmax <= vmin:
        vmax = vmin + 1.0
    return np.clip((img - vmin) / (vmax - vmin), 0.0, 1.0)


class ScanReader(threading.Thread):
    """Background thread that reads frames from a sensor or a recorded file
    and pushes the latest destaggered field images into a queue."""

    def __init__(self, source_url: str, out_queue: queue.Queue, log_fn,
                 is_file: bool = False, loop: bool = False):
        super().__init__(daemon=True)
        self.source_url = source_url
        self.out_queue = out_queue
        self.log = log_fn
        self.is_file = is_file
        self.loop = loop
        self._stop_event = threading.Event()
        self.metadata = None

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            first = True
            while not self._stop_event.is_set():
                self._play_once(first)
                first = False
                # loop only recorded files, and only if asked to
                if not (self.is_file and self.loop):
                    break
                if self._stop_event.is_set():
                    break
                self.log("Looping recording...")
            self.log("Stream ended.")
        except Exception as e:
            self.out_queue.put(("error", str(e)))
        finally:
            self.out_queue.put(("stopped", None))

    def _play_once(self, announce=True):
        source = None
        try:
            if announce:
                self.log(f"Opening source: {self.source_url} ...")
            source = open_source(self.source_url, sensor_idx=0)
            self.metadata = source_metadata(source)
            if self.metadata is not None:
                self.out_queue.put(("metadata", self.metadata))
            if announce:
                self.log("Source opened, streaming...")

            for item in source:
                if self._stop_event.is_set():
                    break
                for frame in frames_from_item(item):
                    images = self._extract_images(frame)
                    if not images:
                        continue
                    # keep only the freshest frame in the queue, but never
                    # drop pending metadata/error events
                    pending = []
                    try:
                        while True:
                            old = self.out_queue.get_nowait()
                            if old[0] != "frame":
                                pending.append(old)
                    except queue.Empty:
                        pass
                    for ev in pending:
                        self.out_queue.put(ev)
                    self.out_queue.put(("frame", images, frame.frame_id,
                                        self._frame_status(frame)))
                if self.is_file:
                    time.sleep(0.1)  # pace file playback at ~10 Hz
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass

    def _extract_images(self, frame):
        info = getattr(frame, "sensor_info", None) or self.metadata
        try:
            available = set(frame.fields)
        except Exception:
            available = None
        images = {}
        for name, _title, _cmap in FIELD_SPECS:
            if available is not None and name not in available:
                continue
            try:
                img = frame.field(name)
                if info is not None:
                    img = ouster_core.destagger(info, img)
                images[name] = percentile_scale(img)
            except Exception:
                continue
        return images

    @staticmethod
    def _frame_status(frame):
        """Runtime health flags carried on each frame."""
        status = {}
        for attr in ("shot_limiting", "shot_limiting_countdown",
                     "thermal_shutdown", "shutdown_countdown",
                     "frame_status"):
            try:
                val = getattr(frame, attr, None)
                if val is not None:
                    status[attr] = str(val)
            except Exception:
                pass
        return status


def enable_dark_title_bar(root: tk.Tk):
    """Ask Windows to draw this window's title bar dark (no-op elsewhere;
    on Linux the title bar color follows the desktop theme).

    Windows only repaints the frame on certain events, so without a nudge
    the bar stays white until the first click. We apply the attribute
    twice: once immediately, and once shortly after the window is mapped,
    followed by a 1-pixel resize bounce that forces DWM to redraw the
    frame right away.
    """
    if sys.platform != "win32":
        return

    def apply(repaint: bool):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new/old)
                value = ctypes.c_int(1)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value),
                        ctypes.sizeof(value)) == 0:
                    break
            if repaint:
                w, h = root.winfo_width(), root.winfo_height()
                if w > 1 and h > 1:
                    root.geometry(f"{w}x{h + 1}")
                    root.update_idletasks()
                    root.geometry(f"{w}x{h}")
        except Exception:
            pass

    root.update_idletasks()
    apply(False)
    root.after(150, lambda: apply(True))


class OusterGuiApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Ouster Digital Lidar Control  ·  Powered by Python")
        root.geometry("1280x860")
        apply_theme(root)
        enable_dark_title_bar(root)

        self.reader = None
        self.frame_queue = queue.Queue(maxsize=4)
        self.record_proc = None
        self.viz_proc = None
        self.image_artists = {}
        self.last_frame_status = {}
        self.settings = self._load_settings()

        self._build_ui()
        self._poll_queue()

        if not HAVE_OUSTER:
            self.log("WARNING: ouster-sdk is not installed "
                     f"({OUSTER_IMPORT_ERROR}).")
            self.log("Install it with:  pip install ouster-sdk")

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        # Blank (transparent) window icon - hides Tk's default feather icon
        self.icon_img = tk.PhotoImage(width=16, height=16)
        self.root.iconphoto(True, self.icon_img)
        ttk.Frame(self.root, style="HeaderStrip.TFrame",
                  height=3).pack(fill=tk.X)

        main = ttk.Frame(self.root, padding=(10, 4, 10, 6))
        main.pack(fill=tk.BOTH, expand=True)

        # scrollable left panel (the controls can be taller than the window)
        left_container = ttk.Frame(main, width=348)
        left_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_container.pack_propagate(False)
        left_canvas = tk.Canvas(left_container, bg=Theme.BG,
                                highlightthickness=0, bd=0)
        vbar = ttk.Scrollbar(left_container, orient="vertical",
                             command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left = ttk.Frame(left_canvas)
        win_id = left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda e: left_canvas.configure(
            scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>", lambda e: left_canvas.itemconfigure(
            win_id, width=e.width))

        def _wheel(e):
            delta = -1 if getattr(e, "num", None) == 5 else (
                1 if getattr(e, "num", None) == 4 else int(-e.delta / 120))
            left_canvas.yview_scroll(delta, "units")
        # only scroll the panel while the pointer is actually over it
        left_container.bind("<Enter>", lambda e: (
            left_canvas.bind_all("<MouseWheel>", _wheel),
            left_canvas.bind_all("<Button-4>", _wheel),
            left_canvas.bind_all("<Button-5>", _wheel)))
        left_container.bind("<Leave>", lambda e: (
            left_canvas.unbind_all("<MouseWheel>"),
            left_canvas.unbind_all("<Button-4>"),
            left_canvas.unbind_all("<Button-5>")))

        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- Connection -------------------------------------------------------
        conn = ttk.LabelFrame(left, text="  SENSOR CONNECTION  ", padding=10)
        conn.pack(fill=tk.X, pady=4)
        ttk.Label(conn, text="Hostname / IP:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.host_var = tk.StringVar(value="os-122xxxxxxxxxx.local")
        ttk.Entry(conn, textvariable=self.host_var).pack(fill=tk.X, pady=(3, 0))
        ttk.Label(conn, text="e.g. os-122xxxxxxxxxx.local  or  192.168.1.50",
                  style="Hint.TLabel").pack(anchor=tk.W, pady=(0, 3))
        row = ttk.Frame(conn, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Button(row, text="Get Sensor Info",
                   command=self.on_get_info).pack(side=tk.LEFT, expand=True,
                                                  fill=tk.X, padx=(0, 3))
        ttk.Button(row, text="Get Config",
                   command=self.on_get_config).pack(side=tk.LEFT, expand=True,
                                                    fill=tk.X, padx=(3, 0))
        row2 = ttk.Frame(conn, style="Panel.TFrame")
        row2.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(row2, text="Get Status",
                   command=self.on_get_status).pack(side=tk.LEFT, expand=True,
                                                    fill=tk.X, padx=(0, 3))
        ttk.Button(row2, text="Reinitialize",
                   command=self.on_reinit).pack(side=tk.LEFT, expand=True,
                                                fill=tk.X, padx=(3, 0))
        ttk.Button(conn, text="Network / IP address...",
                   command=self.on_network).pack(fill=tk.X, pady=(2, 0))

        # --- Configuration ----------------------------------------------------
        cfg = ttk.LabelFrame(left, text="  SENSOR CONFIGURATION  ", padding=10)
        cfg.pack(fill=tk.X, pady=4)
        ttk.Label(cfg, text="Lidar mode:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.mode_var = tk.StringVar(value="1024x10")
        ttk.Combobox(cfg, textvariable=self.mode_var, values=LIDAR_MODES,
                     state="readonly").pack(fill=tk.X, pady=3)
        ttk.Label(cfg, text="Timestamp mode:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.ts_var = tk.StringVar(value=TIMESTAMP_MODES[0])
        ttk.Combobox(cfg, textvariable=self.ts_var, values=TIMESTAMP_MODES,
                     state="readonly").pack(fill=tk.X, pady=3)
        ttk.Label(cfg, text="Operating mode:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.opmode_var = tk.StringVar(value=OPERATING_MODES[0])
        ttk.Combobox(cfg, textvariable=self.opmode_var,
                     values=OPERATING_MODES,
                     state="readonly").pack(fill=tk.X, pady=3)
        ttk.Label(cfg, text="Signal multiplier:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.sigmult_var = tk.StringVar(value=SIGNAL_MULTIPLIERS[0])
        ttk.Combobox(cfg, textvariable=self.sigmult_var,
                     values=SIGNAL_MULTIPLIERS,
                     state="readonly").pack(fill=tk.X, pady=3)
        ttk.Label(cfg, text="UDP data profile:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.profile_var = tk.StringVar(value=UNCHANGED)
        ttk.Combobox(cfg, textvariable=self.profile_var, values=UDP_PROFILES,
                     state="readonly").pack(fill=tk.X, pady=3)
        az = ttk.Frame(cfg, style="Panel.TFrame")
        az.pack(fill=tk.X, pady=3)
        ttk.Label(az, text="Azimuth window (deg):",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=4,
                                             sticky=tk.W)
        ttk.Label(az, text="start", style="Muted.TLabel").grid(row=1, column=0)
        self.az_start_var = tk.StringVar(value="0")
        ttk.Entry(az, textvariable=self.az_start_var,
                  width=6).grid(row=1, column=1, padx=(2, 8))
        ttk.Label(az, text="end", style="Muted.TLabel").grid(row=1, column=2)
        self.az_end_var = tk.StringVar(value="360")
        ttk.Entry(az, textvariable=self.az_end_var,
                  width=6).grid(row=1, column=3, padx=2)
        ports = ttk.Frame(cfg, style="Panel.TFrame")
        ports.pack(fill=tk.X, pady=3)
        ttk.Label(ports, text="Lidar port:",
                  style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W,
                                             pady=1)
        self.lidar_port_var = tk.StringVar(value="7502")
        ttk.Entry(ports, textvariable=self.lidar_port_var,
                  width=8).grid(row=0, column=1, padx=6, pady=1)
        ttk.Label(ports, text="IMU port:",
                  style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W,
                                             pady=1)
        self.imu_port_var = tk.StringVar(value="7503")
        ttk.Entry(ports, textvariable=self.imu_port_var,
                  width=8).grid(row=1, column=1, padx=6, pady=1)
        self.persist_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg, text="Persist (keep after reboot)",
                        variable=self.persist_var,
                        style="TCheckbutton").pack(anchor=tk.W, pady=(6, 0))
        ttk.Button(cfg, text="Apply Configuration",
                   command=self.on_apply_config).pack(fill=tk.X, pady=(6, 0))

        # --- Streaming ----------------------------------------------------------
        stream = ttk.LabelFrame(left, text="  LIVE STREAM  ", padding=10)
        stream.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(stream, text="▶  Start Stream",
                                    command=self.on_start_stream)
        self.start_btn.pack(fill=tk.X, pady=3)
        self.stop_btn = ttk.Button(stream, text="■  Stop Stream",
                                   command=self.on_stop_stream,
                                   state=tk.DISABLED)
        self.stop_btn.pack(fill=tk.X, pady=3)
        ttk.Button(stream, text="Open 3D Viewer (point cloud)",
                   command=self.on_open_3d).pack(fill=tk.X, pady=3)

        # --- Recording / Playback ------------------------------------------------
        rec = ttk.LabelFrame(left, text="  RECORD / PLAYBACK  ", padding=10)
        rec.pack(fill=tk.X, pady=4)
        self.record_btn = ttk.Button(rec, text="●  Start Recording",
                                     command=self.on_toggle_record)
        self.record_btn.pack(fill=tk.X, pady=3)
        ttk.Button(rec, text="▶  Play Recording (PCAP / OSF)...",
                   command=self.on_open_file).pack(fill=tk.X, pady=3)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rec, text="Loop playback (repeat)",
                        variable=self.loop_var,
                        style="TCheckbutton").pack(anchor=tk.W, pady=(2, 0))
        ttk.Button(rec, text="Export to MCAP (Foxglove)...",
                   command=self.on_export_mcap).pack(fill=tk.X, pady=(6, 0))

        # --- Help ---------------------------------------------------------------
        ttk.Button(left, text="?  Help",
                   command=self.on_help).pack(fill=tk.X, pady=4)

        # --- Log --------------------------------------------------------------------
        logf = ttk.LabelFrame(left, text="  LOG  ", padding=6)
        logf.pack(fill=tk.X, pady=4)
        self.log_widget = scrolledtext.ScrolledText(
            logf, height=7, state=tk.DISABLED, font=("monospace", 8),
            bg=Theme.LOG_BG, fg=Theme.LOG_FG, insertbackground=Theme.LOG_FG,
            relief=tk.FLAT, borderwidth=0, highlightthickness=0)
        self.log_widget.pack(fill=tk.X)

        # --- Right side: sensor info + image canvas -----------------------------------
        info = ttk.LabelFrame(right, text="  SENSOR METADATA  ", padding=8)
        info.pack(fill=tk.X)
        self.info_var = tk.StringVar(value="Not connected.")
        ttk.Label(info, textvariable=self.info_var, style="Info.TLabel",
                  justify=tk.LEFT).pack(anchor=tk.W)

        viz = ttk.LabelFrame(right, text="  2D FIELD IMAGES (DESTAGGERED)  ",
                             padding=6)
        viz.pack(fill=tk.BOTH, expand=True, pady=6)

        # view selector: show all four, or one field enlarged
        toolbar = ttk.Frame(viz, style="Panel.TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(toolbar, text="View:",
                  style="Muted.TLabel").pack(side=tk.LEFT, padx=(2, 6))
        self.view_buttons = {}
        btn = ttk.Button(toolbar, text="⊞ All (4)",
                         command=lambda: self._set_view(None))
        btn.pack(side=tk.LEFT, padx=2)
        self.view_buttons[None] = btn
        for name, title, _cmap in FIELD_SPECS:
            short = title.split(" [")[0].split(" (")[0]
            b = ttk.Button(toolbar, text=short,
                           command=lambda n=name: self._set_view(n))
            b.pack(side=tk.LEFT, padx=2)
            self.view_buttons[name] = b
        ttk.Label(toolbar, text="(tip: click an image to enlarge it)",
                  style="Muted.TLabel").pack(side=tk.RIGHT, padx=4)

        self.view_field = None       # None = 4-up grid; else a field name
        self.last_images = {}        # freshest frame, for instant redraw
        self.last_frame_id = 0

        self.fig = Figure(figsize=(8, 6), dpi=90, tight_layout=True,
                          facecolor=Theme.PANEL)
        self.axes = {}
        self.canvas = FigureCanvasTkAgg(self.fig, master=viz)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_click)
        widget = self.canvas.get_tk_widget()
        widget.configure(bg=Theme.PANEL, highlightthickness=0)
        widget.pack(fill=tk.BOTH, expand=True)
        self._build_axes()

        # "oT" watermark, bottom-right corner, floating above everything
        watermark = tk.Label(self.root, text="oT", bg=Theme.PANEL,
                             fg=Theme.MUTED, font=("TkDefaultFont", 11,
                                                   "bold italic"))
        watermark.place(relx=1.0, rely=1.0, anchor=tk.SE, x=-10, y=-8)

        # remember the form fields between sessions
        self._persist_vars = {
            "host": self.host_var,
            "lidar_mode": self.mode_var,
            "timestamp_mode": self.ts_var,
            "operating_mode": self.opmode_var,
            "signal_multiplier": self.sigmult_var,
            "udp_profile": self.profile_var,
            "az_start": self.az_start_var,
            "az_end": self.az_end_var,
            "lidar_port": self.lidar_port_var,
            "imu_port": self.imu_port_var,
            "persist": self.persist_var,
        }
        for key, var in self._persist_vars.items():
            if key in self.settings:
                try:
                    var.set(self.settings[key])
                except Exception:
                    pass

    # ------------------------------------------------------------ settings --
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self):
        data = {}
        for key, var in getattr(self, "_persist_vars", {}).items():
            try:
                data[key] = var.get()
            except Exception:
                pass
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.log(f"Could not save settings: {e}")

    def _style_axis(self, ax, title):
        ax.set_facecolor(Theme.BG)
        ax.set_title(title, fontsize=9, color=Theme.FG, loc="left")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(Theme.BORDER)

    def _build_axes(self):
        """(Re)create the subplots for the current view (all 4 or one)."""
        self.fig.clear()
        self.axes = {}
        self.image_artists = {}
        specs = (FIELD_SPECS if self.view_field is None
                 else [s for s in FIELD_SPECS if s[0] == self.view_field])
        for i, (name, title, cmap) in enumerate(specs):
            ax = self.fig.add_subplot(len(specs), 1, i + 1)
            self._style_axis(ax, title)
            self.axes[name] = (ax, cmap)
        # highlight the active view button
        for key, b in self.view_buttons.items():
            b.state(["pressed"] if key == self.view_field else ["!pressed"])
        self.canvas.draw_idle()

    def _set_view(self, field):
        if field == self.view_field:
            return
        self.view_field = field
        self._build_axes()
        if self.last_images:                 # redraw immediately, no wait
            self._draw_frame(self.last_images, self.last_frame_id)

    def _on_canvas_click(self, event):
        """Click an image to enlarge it; click again to return to the grid."""
        if self.view_field is not None:
            self._set_view(None)             # already enlarged -> back to grid
            return
        for name, (ax, _cmap) in self.axes.items():
            if event.inaxes is ax:
                self._set_view(name)
                return

    # ------------------------------------------------------------- helpers --
    def log(self, msg: str):
        def append():
            self.log_widget.configure(state=tk.NORMAL)
            self.log_widget.insert(tk.END,
                                   time.strftime("[%H:%M:%S] ") + msg + "\n")
            self.log_widget.see(tk.END)
            self.log_widget.configure(state=tk.DISABLED)
        # log() may be called from worker threads
        self.root.after(0, append)

    def _require_sdk(self) -> bool:
        if not HAVE_OUSTER:
            messagebox.showerror(
                "ouster-sdk missing",
                "The ouster-sdk Python package is not installed.\n\n"
                "Install it with:\n    pip install ouster-sdk")
            return False
        return True

    def _host(self) -> str:
        return self.host_var.get().strip()

    # ------------------------------------------------------------- actions --
    def on_get_info(self):
        """Open the sensor's built-in web dashboard in the default browser."""
        host = self._host()
        if not host:
            messagebox.showerror("Get Sensor Info",
                                 "Please enter the sensor hostname or IP.")
            return
        url = host if host.startswith(("http://", "https://")) \
            else f"http://{host}"
        self.log(f"Opening sensor web page: {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            self.log(f"ERROR opening browser: {e}")
            messagebox.showerror("Get Sensor Info",
                                 f"Could not open the browser:\n{e}")

    def _show_metadata(self, info):
        if info is None:
            self.info_var.set("No metadata available.")
            return
        try:
            fmt = info.format
            text = (
                f"Product line : {info.prod_line}\n"
                f"Serial number: {info.sn}\n"
                f"Firmware     : {info.fw_rev}\n"
                f"Mode         : {info.config.lidar_mode}\n"
                f"Resolution   : {fmt.columns_per_frame} x "
                f"{fmt.pixels_per_column}"
            )
        except Exception:
            text = str(info)
        self.info_var.set(text)
        self.log("Sensor metadata received.")

    def on_get_config(self):
        if not self._require_sdk():
            return
        host = self._host()

        def work():
            try:
                cfg = get_config(host)
                self.log(f"Current config:\n{cfg}")
            except Exception as e:
                self.log(f"ERROR: {e}")

        threading.Thread(target=work, daemon=True).start()

    def on_reinit(self):
        """Reinitialize (restart the data path / relaser) of the sensor."""
        if not self._require_sdk():
            return
        if SensorHttp is None:
            messagebox.showerror("Reinitialize",
                                 "This ouster-sdk version does not expose "
                                 "the sensor HTTP API.")
            return
        host = self._host()
        if not messagebox.askyesno(
                "Reinitialize sensor",
                f"Reinitialize {host}?\n\nThe sensor will briefly stop "
                "sending data while it restarts (a few seconds)."):
            return
        if self.reader is not None:
            self.on_stop_stream()

        def work():
            try:
                http = SensorHttp.create(host)
                http.reinitialize()
                self.log("Sensor reinitialized.")
            except Exception as e:
                self.log(f"ERROR reinitializing: {e}")

        self.log(f"Reinitializing {host} ...")
        threading.Thread(target=work, daemon=True).start()

    def on_network(self):
        """Open a dialog to view / change the sensor's IP configuration."""
        if not self._require_sdk():
            return
        if SensorHttp is None:
            messagebox.showerror("Network",
                                 "This ouster-sdk version does not expose "
                                 "the sensor HTTP API.")
            return
        host = self._host()

        win = tk.Toplevel(self.root)
        win.title(f"Network / IP  ·  {host}")
        win.geometry("560x520")
        win.configure(bg=Theme.BG)
        win.transient(self.root)

        cur = ttk.LabelFrame(win, text="  CURRENT NETWORK CONFIG  ", padding=8)
        cur.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 6))
        cfg_text = scrolledtext.ScrolledText(
            cur, wrap=tk.WORD, font=("monospace", 9), height=10,
            bg=Theme.PANEL, fg=Theme.FG, relief=tk.FLAT, borderwidth=0,
            highlightthickness=0)
        cfg_text.pack(fill=tk.BOTH, expand=True)
        cfg_text.insert(tk.END, "Loading...")
        cfg_text.configure(state=tk.DISABLED)

        def refresh():
            def work():
                try:
                    data = json.loads(SensorHttp.create(host).network())
                    txt = json.dumps(data, indent=2)
                except Exception as e:
                    txt = f"Could not read network config:\n{e}"
                def show():
                    cfg_text.configure(state=tk.NORMAL)
                    cfg_text.delete("1.0", tk.END)
                    cfg_text.insert(tk.END, txt)
                    cfg_text.configure(state=tk.DISABLED)
                self.root.after(0, show)
            threading.Thread(target=work, daemon=True).start()

        refresh()

        setf = ttk.LabelFrame(win, text="  SET STATIC IP  ", padding=8)
        setf.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(setf, text="IP / CIDR (e.g. 192.168.1.50/24):",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2,
                                             sticky=tk.W)
        ip_var = tk.StringVar()
        ttk.Entry(setf, textvariable=ip_var, width=28).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Label(setf, text="Gateway (optional):",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2,
                                             sticky=tk.W)
        gw_var = tk.StringVar()
        ttk.Entry(setf, textvariable=gw_var, width=28).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=2)

        def run_action(desc, fn, confirm):
            if not messagebox.askyesno("Change sensor IP", confirm,
                                       parent=win):
                return
            if self.reader is not None:
                self.on_stop_stream()

            def work():
                try:
                    fn()
                    self.log(desc + " - done. The sensor is applying the new "
                             "network settings; reconnect with the new "
                             "address.")
                except Exception as e:
                    self.log(f"ERROR ({desc}): {e}")
                self.root.after(1500, refresh)
            self.log(desc + " ...")
            threading.Thread(target=work, daemon=True).start()

        def apply_static():
            ip = ip_var.get().strip()
            gw = gw_var.get().strip()
            if not ip:
                messagebox.showerror("Set Static IP",
                                     "Please enter an IP / CIDR.", parent=win)
                return
            run_action(
                f"Setting static IP {ip}",
                (lambda: SensorHttp.create(host).set_static_ip(ip, gw)) if gw
                else (lambda: SensorHttp.create(host).set_static_ip(ip)),
                f"Set the sensor's static IP to:\n  {ip}"
                + (f"  (gateway {gw})" if gw else "")
                + "\n\nWARNING: you will lose the current connection and must "
                  "reconnect using the NEW address. Continue?")

        ttk.Button(setf, text="Apply Static IP",
                   command=apply_static).grid(row=4, column=0, sticky=tk.W,
                                              pady=(6, 0))

        def revert_dhcp():
            run_action(
                "Reverting to DHCP / link-local",
                lambda: SensorHttp.create(host).delete_static_ip(),
                "Remove the static IP and return the sensor to "
                "DHCP / link-local addressing?\n\nWARNING: the sensor's "
                "address will change and you must reconnect. Continue?")

        btns = ttk.Frame(win, style="TFrame")
        btns.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btns, text="Revert to DHCP / Link-Local",
                   command=revert_dhcp).pack(side=tk.LEFT)
        ttk.Button(btns, text="Refresh",
                   command=refresh).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Close",
                   command=win.destroy).pack(side=tk.RIGHT)

    def on_get_status(self):
        """Query the sensor for status / telemetry and show it in a window."""
        if not self._require_sdk():
            return
        host = self._host()
        self.log(f"Querying status from {host} ...")
        threading.Thread(target=self._fetch_status, args=(host,),
                         daemon=True).start()

    def _fetch_status(self, host):
        sections = {}
        # 1. sensor info (status, product, firmware) via SDK HTTP API
        if SensorHttp is not None:
            try:
                http = SensorHttp.create(host)
                sections["Sensor Info"] = json.loads(http.sensor_info())
            except Exception as e:
                sections["Sensor Info"] = {"error": str(e)}
        # 2. telemetry (voltage, current, temperatures) via HTTP endpoint
        for name, ep in (("Telemetry", "/api/v1/sensor/telemetry"),
                         ("Alerts", "/api/v1/sensor/alerts")):
            try:
                url = f"http://{host}{ep}"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    sections[name] = json.loads(resp.read().decode())
            except Exception as e:
                sections[name] = {"error": str(e)}
        # 3. live shot-limiting / thermal state from the most recent frame
        if self.reader is not None and self.last_frame_status:
            sections["Live frame status"] = self.last_frame_status
        self.root.after(0, lambda: self._show_status(host, sections))

    def _show_status(self, host, sections):
        self.log("Sensor status received.")
        win = tk.Toplevel(self.root)
        win.title(f"Sensor Status  ·  {host}")
        win.geometry("560x620")
        win.configure(bg=Theme.BG)
        win.transient(self.root)

        body = scrolledtext.ScrolledText(
            win, wrap=tk.WORD, font=("monospace", 10),
            bg=Theme.PANEL, fg=Theme.FG, insertbackground=Theme.FG,
            relief=tk.FLAT, borderwidth=0, highlightthickness=0,
            padx=14, pady=10)
        body.pack(fill=tk.BOTH, expand=True)
        body.tag_configure("heading", foreground=Theme.ORANGE,
                           font=("monospace", 11, "bold"))
        for title, data in sections.items():
            body.insert(tk.END, f"{title}\n", "heading")
            body.insert(tk.END, json.dumps(data, indent=2) + "\n\n")
        body.configure(state=tk.DISABLED)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)

    def on_apply_config(self):
        if not self._require_sdk():
            return
        host = self._host()
        mode = self.mode_var.get()
        ts = self.ts_var.get()
        opmode = self.opmode_var.get()
        sigmult = self.sigmult_var.get()
        profile = self.profile_var.get()
        persist = self.persist_var.get()
        try:
            lidar_port = int(self.lidar_port_var.get())
            imu_port = int(self.imu_port_var.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Ports must be integers.")
            return
        try:
            az_start = float(self.az_start_var.get())
            az_end = float(self.az_end_var.get())
            if not (0 <= az_start <= 360 and 0 <= az_end <= 360):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid azimuth window",
                                 "Azimuth start/end must be numbers "
                                 "between 0 and 360.")
            return

        def work():
            try:
                cfg = ouster_core.SensorConfig()
                cfg.lidar_mode = parse_lidar_mode(mode)
                cfg.timestamp_mode = getattr(ouster_core.TimestampMode, ts)
                cfg.operating_mode = getattr(ouster_core.OperatingMode,
                                             opmode)
                cfg.signal_multiplier = float(sigmult)
                # azimuth window in millidegrees
                cfg.azimuth_window = (int(az_start * 1000),
                                      int(az_end * 1000))
                if profile != UNCHANGED:
                    cfg.udp_profile_lidar = getattr(
                        ouster_core.UDPProfileLidar, profile)
                cfg.udp_port_lidar = lidar_port
                cfg.udp_port_imu = imu_port
                set_config(host, cfg, persist=persist, udp_dest_auto=True)
                self.log(
                    f"Configuration applied: mode={mode}, ts={ts}, "
                    f"op={opmode}, signal_mult={sigmult}, "
                    f"azimuth=({az_start},{az_end})deg, "
                    f"profile={profile}, ports={lidar_port}/{imu_port}, "
                    f"persist={persist}")
            except Exception as e:
                self.log(f"ERROR: {e}")

        self._save_settings()
        self.log("Applying configuration (sensor will reinitialize)...")
        threading.Thread(target=work, daemon=True).start()

    def on_start_stream(self, source_url=None, is_file=False):
        if not self._require_sdk():
            return
        if self.reader is not None:
            self.log("Stream already running.")
            return
        if not is_file:
            self._save_settings()
        url = source_url or self._host()
        loop = is_file and self.loop_var.get()
        self.reader = ScanReader(url, self.frame_queue, self.log,
                                 is_file=is_file, loop=loop)
        self.reader.start()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def on_stop_stream(self):
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def on_open_file(self):
        if not self._require_sdk():
            return
        path = filedialog.askopenfilename(
            title="Open recording (PCAP / OSF)",
            filetypes=[("Lidar recordings", "*.pcap *.osf"),
                       ("All files", "*")])
        if path:
            self.on_stop_stream()
            self.on_start_stream(source_url=path, is_file=True)

    def on_export_mcap(self):
        """Convert a PCAP/OSF recording to an MCAP file with foxglove
        PointCloud messages, viewable directly in Foxglove."""
        if not self._require_sdk():
            return
        if not HAVE_MCAP:
            messagebox.showerror(
                "Export to MCAP",
                "MCAP export needs extra packages. Install them with:\n\n"
                "    pip install mcap mcap-protobuf-support "
                "foxglove-schemas-protobuf protobuf")
            return
        in_path = filedialog.askopenfilename(
            title="Recording to convert (PCAP / OSF)",
            filetypes=[("Lidar recordings", "*.pcap *.osf"),
                       ("All files", "*")])
        if not in_path:
            return
        out_path = filedialog.asksaveasfilename(
            title="Save MCAP as",
            defaultextension=".mcap",
            initialfile=os.path.splitext(os.path.basename(in_path))[0]
            + ".mcap",
            filetypes=[("MCAP files", "*.mcap")])
        if not out_path:
            return
        threading.Thread(target=self._export_mcap_worker,
                         args=(in_path, out_path), daemon=True).start()

    def _export_mcap_worker(self, in_path, out_path):
        try:
            self.log(f"Exporting {os.path.basename(in_path)} to MCAP ...")
            src = open_source(in_path, sensor_idx=0)
            info = source_metadata(src)
            xyzlut = ouster_core.XYZLut(info, use_extrinsics=False)
            F32 = PackedElementField.FLOAT32
            fields = [PackedElementField(name="x", offset=0, type=F32),
                      PackedElementField(name="y", offset=4, type=F32),
                      PackedElementField(name="z", offset=8, type=F32),
                      PackedElementField(name="intensity", offset=12,
                                         type=F32)]
            n = 0
            with open(out_path, "wb") as fh, McapWriter(fh) as writer:
                for item in src:
                    for frame in frames_from_item(item):
                        rng = frame.field(ouster_core.ChanField.RANGE)
                        xyz = xyzlut(rng).astype(np.float32).reshape(-1, 3)
                        try:
                            inten = frame.field(
                                ouster_core.ChanField.SIGNAL)
                        except Exception:
                            inten = rng
                        inten = inten.astype(np.float32).reshape(-1, 1)
                        mask = (rng.reshape(-1) > 0) & \
                            np.isfinite(xyz).all(1)
                        pts = np.hstack([xyz, inten])[mask]
                        ts = int(n * 1e8)  # ~10 Hz fallback timeline
                        msg = PointCloud(frame_id="ouster", point_stride=16,
                                         fields=fields, data=pts.tobytes())
                        msg.timestamp.FromNanoseconds(ts)
                        writer.write_message(topic="/ouster/points",
                                             message=msg, log_time=ts,
                                             publish_time=ts)
                        n += 1
                        if n % 50 == 0:
                            self.log(f"  ...{n} frames written")
            try:
                src.close()
            except Exception:
                pass
            self.log(f"MCAP export complete: {n} frames -> {out_path}")
            self.root.after(0, lambda: messagebox.showinfo(
                "Export to MCAP",
                f"Done. Wrote {n} point-cloud frames to:\n{out_path}\n\n"
                "Open it in Foxglove and add a 3D panel."))
        except Exception as e:
            self.log(f"ERROR exporting MCAP: {e}")
            self.root.after(0, lambda: messagebox.showerror(
                "Export to MCAP", f"Export failed:\n{e}"))

    def on_help(self):
        """Open README.md in a scrollable window inside the app."""
        readme = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "README.md")
        try:
            with open(readme, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            messagebox.showerror("Help", f"Could not open README.md:\n{e}")
            return

        win = tk.Toplevel(self.root)
        win.title("Help  ·  README")
        win.geometry("820x640")
        win.configure(bg=Theme.BG)
        win.transient(self.root)

        body = scrolledtext.ScrolledText(
            win, wrap=tk.WORD, font=("monospace", 10),
            bg=Theme.PANEL, fg=Theme.FG, insertbackground=Theme.FG,
            relief=tk.FLAT, borderwidth=0, highlightthickness=0,
            padx=14, pady=10)
        body.pack(fill=tk.BOTH, expand=True)
        # highlight markdown headings in orange
        body.tag_configure("heading", foreground=Theme.ORANGE,
                           font=("monospace", 11, "bold"))
        for line in text.splitlines(keepends=True):
            if line.startswith("#"):
                body.insert(tk.END, line, "heading")
            else:
                body.insert(tk.END, line)
        body.configure(state=tk.DISABLED)

        ttk.Button(win, text="Close",
                   command=win.destroy).pack(pady=6)

    def _ouster_cli_cmd(self, *args):
        """Build an ouster-cli command that always uses this venv's Python,
        so it works even when 'ouster-cli' is not on PATH (e.g. on Windows
        when the app is started by double-click)."""
        return [sys.executable, "-c",
                "from ouster.cli.core import run; run()", *args]

    def on_open_3d(self):
        """Launch Ouster's official 3D point-cloud viewer.

        The live 2D stream binds the UDP data port, so a separate viewer
        process cannot open the sensor at the same time. We stop the 2D
        stream first, then launch the viewer a moment later.
        """
        if not self._require_sdk():
            return
        if self.reader is not None:
            self.on_stop_stream()
            self.log("Stopped 2D stream to free the sensor; "
                     "opening 3D viewer...")
            self.root.after(1500, self._launch_3d)
        else:
            self._launch_3d()

    def _launch_3d(self):
        host = self._host()
        cmd = self._ouster_cli_cmd("source", host, "viz")
        try:
            self.viz_proc = subprocess.Popen(cmd)
            self.log(f"Launched 3D viewer for {host} "
                     "(a separate window will open shortly).")
        except Exception as e:
            self.log(f"ERROR launching 3D viewer: {e}")
            messagebox.showerror("3D Viewer",
                                 f"Could not launch the 3D viewer:\n{e}")

    def on_toggle_record(self):
        if self.record_proc is None:
            path = filedialog.asksaveasfilename(
                title="Save recording as",
                defaultextension=".pcap",
                filetypes=[("PCAP files", "*.pcap")])
            if not path:
                return
            if self.reader is not None:
                self.on_stop_stream()
                self.log("Stopped 2D stream to free the sensor for "
                         "recording.")
            host = self._host()
            cmd = self._ouster_cli_cmd("source", host, "save", path)
            try:
                self.record_proc = subprocess.Popen(cmd)
                self.record_btn.configure(text="■  Stop Recording")
                self.log(f"Recording started -> {path}")
            except Exception as e:
                self.log(f"ERROR starting recording: {e}")
                messagebox.showerror("Recording",
                                     f"Could not start recording:\n{e}")
        else:
            self.record_proc.terminate()
            self.record_proc = None
            self.record_btn.configure(text="●  Start Recording")
            self.log("Recording stopped.")

    # ------------------------------------------------------------ rendering --
    def _poll_queue(self):
        try:
            while True:
                item = self.frame_queue.get_nowait()
                kind = item[0]
                if kind == "frame":
                    self._draw_frame(item[1], item[2])
                    if len(item) > 3:
                        self.last_frame_status = item[3]
                elif kind == "metadata":
                    self._show_metadata(item[1])
                elif kind == "error":
                    self.log(f"STREAM ERROR: {item[1]}")
                    self.on_stop_stream()
                elif kind == "stopped":
                    if self.reader is not None:
                        self.on_stop_stream()
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _draw_frame(self, images: dict, frame_id: int):
        self.last_images = images
        self.last_frame_id = frame_id
        for name, img in images.items():
            if name not in self.axes:        # not shown in current view
                continue
            ax, cmap = self.axes[name]
            artist = self.image_artists.get(name)
            if artist is None or artist.get_array().shape != img.shape:
                ax.clear()
                self._style_axis(ax, FIELD_TITLES[name])
                self.image_artists[name] = ax.imshow(
                    img, cmap=cmap, aspect="auto", vmin=0.0, vmax=1.0)
            else:
                artist.set_data(img)
        self.canvas.draw_idle()

    # ------------------------------------------------------------- shutdown --
    def on_close(self):
        self._save_settings()
        self.on_stop_stream()
        for proc in (self.record_proc, self.viz_proc):
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = OusterGuiApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
