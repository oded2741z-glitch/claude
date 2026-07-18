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

import os
import queue
import subprocess
import threading
import time
import tkinter as tk
import warnings
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning, module="ouster")

try:  # optional - used to draw the Python-logo app icon
    from PIL import Image, ImageDraw
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

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
    HAVE_OUSTER = True
    OUSTER_IMPORT_ERROR = None
except Exception as _e:  # SDK missing entirely
    HAVE_OUSTER = False
    OUSTER_IMPORT_ERROR = _e
    ouster_core = None
    open_source = None
    get_config = set_config = None

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
    ACCENT    = "#38bdf8"   # cyan accent (headers, highlights)
    GREEN     = "#34d399"   # start
    RED       = "#f87171"   # stop / record
    AMBER     = "#fbbf24"   # apply / warning
    LOG_BG    = "#0d1017"
    LOG_FG    = "#9fe8a9"
    STATUS_BG = "#0d1017"
    # Python brand palette (header bar + app icon)
    PY_BLUE   = "#3776AB"
    PY_BLUE_D = "#2b5b8c"
    PY_YELLOW = "#FFD43B"


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
    # Python-styled header bar
    style.configure("Header.TFrame", background=Theme.PY_BLUE)
    style.configure("HeaderStrip.TFrame", background=Theme.PY_YELLOW)
    style.configure("HeaderTitle.TLabel", background=Theme.PY_BLUE,
                    foreground="#ffffff",
                    font=(base_font.actual("family"), 14, "bold"))
    style.configure("HeaderSub.TLabel", background=Theme.PY_BLUE,
                    foreground=Theme.PY_YELLOW,
                    font=(base_font.actual("family"), 9))
    style.configure("HeaderIcon.TLabel", background=Theme.PY_BLUE)
    style.configure("Info.TLabel", background=Theme.PANEL,
                    foreground=Theme.FG, font=("monospace", 9))
    style.configure("Status.TLabel", background=Theme.STATUS_BG,
                    foreground=Theme.MUTED, padding=(10, 4))

    style.configure("TLabelframe", background=Theme.PANEL,
                    bordercolor=Theme.BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=Theme.PANEL,
                    foreground=Theme.ACCENT,
                    font=(base_font.actual("family"), 9, "bold"))

    # buttons -----------------------------------------------------------------
    def button(name, bg, fg=Theme.BG, active=None):
        style.configure(name, background=bg, foreground=fg,
                        bordercolor=bg, focusthickness=1, padding=(8, 6),
                        font=(base_font.actual("family"), 10, "bold"))
        style.map(name,
                  background=[("active", active or Theme.ACCENT),
                              ("disabled", Theme.FIELD)],
                  foreground=[("disabled", Theme.MUTED)])

    button("TButton", Theme.FIELD, fg=Theme.FG, active=Theme.BORDER)
    button("Accent.TButton", Theme.ACCENT)
    button("Start.TButton", Theme.GREEN)
    button("Stop.TButton", Theme.RED)
    button("Record.TButton", Theme.RED)
    button("Apply.TButton", Theme.AMBER)

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


def make_python_logo(size: int = 64):
    """Draw a simplified Python two-snakes logo and return a tk.PhotoImage.

    Drawn in code with Pillow (no asset files); returns None if Pillow is
    not installed.
    """
    if not HAVE_PIL:
        return None
    import base64
    import io

    s = 4  # supersampling factor for smooth edges
    n = 128 * s
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # dark rounded badge behind the snakes, so the blue snake stays visible
    # on the blue header bar
    badge = ImageDraw.Draw(img)
    badge.rounded_rectangle([0, 0, n - 1, n - 1], radius=30 * s,
                            fill=Theme.BG)

    def snake(color):
        piece = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        d = ImageDraw.Draw(piece)
        r = 20 * s
        # head + body (wide top bar) and neck (left column), L-shaped;
        # stops just above the centerline so the mirrored snake interlocks
        d.rounded_rectangle([18 * s, 8 * s, 110 * s, 61 * s],
                            radius=r, fill=color)
        d.rounded_rectangle([18 * s, 8 * s, 62 * s, 61 * s],
                            radius=r, fill=color)
        d.rectangle([18 * s, 30 * s, 62 * s, 61 * s], fill=color)
        # eye
        d.ellipse([38 * s, 18 * s, 50 * s, 30 * s], fill="#ffffff")
        return piece

    blue = snake("#4B8BBE")  # lighter Python blue, readable on dark badge
    yellow = snake(Theme.PY_YELLOW).rotate(180)
    img.alpha_composite(blue)
    img.alpha_composite(yellow)
    img = img.resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode())


LIDAR_MODES = ["512x10", "512x20", "1024x10", "1024x20", "2048x10"]
TIMESTAMP_MODES = [
    "TIME_FROM_INTERNAL_OSC",
    "TIME_FROM_SYNC_PULSE_IN",
    "TIME_FROM_PTP_1588",
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
                 is_file: bool = False):
        super().__init__(daemon=True)
        self.source_url = source_url
        self.out_queue = out_queue
        self.log = log_fn
        self.is_file = is_file
        self._stop_event = threading.Event()
        self.metadata = None

    def stop(self):
        self._stop_event.set()

    def run(self):
        source = None
        try:
            self.log(f"Opening source: {self.source_url} ...")
            source = open_source(self.source_url, sensor_idx=0)
            self.metadata = source_metadata(source)
            if self.metadata is not None:
                self.out_queue.put(("metadata", self.metadata))
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
                    self.out_queue.put(("frame", images, frame.frame_id))
                if self.is_file:
                    time.sleep(0.1)  # pace file playback at ~10 Hz

            self.log("Stream ended.")
        except Exception as e:
            self.out_queue.put(("error", str(e)))
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass
            self.out_queue.put(("stopped", None))

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


class OusterGuiApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Ouster Digital Lidar - Control & Visualization")
        root.geometry("1280x860")
        apply_theme(root)

        self.reader = None
        self.frame_queue = queue.Queue(maxsize=4)
        self.record_proc = None
        self.viz_proc = None
        self.image_artists = {}

        self._build_ui()
        self._poll_queue()

        if not HAVE_OUSTER:
            self.log("WARNING: ouster-sdk is not installed "
                     f"({OUSTER_IMPORT_ERROR}).")
            self.log("Install it with:  pip install ouster-sdk")

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        # header bar (Python-branded: blue bar, yellow strip, snakes logo) -----
        header = ttk.Frame(self.root, style="Header.TFrame",
                           padding=(14, 8, 14, 8))
        header.pack(fill=tk.X)
        self.logo_img = make_python_logo(36)
        self.icon_img = make_python_logo(64)
        if self.logo_img is not None:
            ttk.Label(header, image=self.logo_img,
                      style="HeaderIcon.TLabel").pack(side=tk.LEFT,
                                                      padx=(0, 10))
        if self.icon_img is not None:
            self.root.iconphoto(True, self.icon_img)
        titles = ttk.Frame(header, style="Header.TFrame")
        titles.pack(side=tk.LEFT)
        ttk.Label(titles, text="Ouster Digital Lidar Control",
                  style="HeaderTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(titles, text="Powered by Python  ·  ouster-sdk",
                  style="HeaderSub.TLabel").pack(anchor=tk.W)
        ttk.Frame(self.root, style="HeaderStrip.TFrame",
                  height=3).pack(fill=tk.X)

        main = ttk.Frame(self.root, padding=(10, 4, 10, 6))
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main, width=330)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- Connection -------------------------------------------------------
        conn = ttk.LabelFrame(left, text="  SENSOR CONNECTION  ", padding=10)
        conn.pack(fill=tk.X, pady=4)
        ttk.Label(conn, text="Hostname / IP:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.host_var = tk.StringVar(value="os-122xxxxxxxxxx.local")
        ttk.Entry(conn, textvariable=self.host_var).pack(fill=tk.X, pady=3)
        row = ttk.Frame(conn, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Button(row, text="Get Sensor Info", style="Accent.TButton",
                   command=self.on_get_info).pack(side=tk.LEFT, expand=True,
                                                  fill=tk.X, padx=(0, 3))
        ttk.Button(row, text="Get Config",
                   command=self.on_get_config).pack(side=tk.LEFT, expand=True,
                                                    fill=tk.X, padx=(3, 0))

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
        ttk.Button(cfg, text="Apply Configuration", style="Apply.TButton",
                   command=self.on_apply_config).pack(fill=tk.X, pady=(8, 0))

        # --- Streaming ----------------------------------------------------------
        stream = ttk.LabelFrame(left, text="  LIVE STREAM  ", padding=10)
        stream.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(stream, text="▶  Start Stream",
                                    style="Start.TButton",
                                    command=self.on_start_stream)
        self.start_btn.pack(fill=tk.X, pady=3)
        self.stop_btn = ttk.Button(stream, text="■  Stop Stream",
                                   style="Stop.TButton",
                                   command=self.on_stop_stream,
                                   state=tk.DISABLED)
        self.stop_btn.pack(fill=tk.X, pady=3)
        ttk.Button(stream, text="Open 3D Viewer (point cloud)",
                   command=self.on_open_3d).pack(fill=tk.X, pady=3)

        # --- Recording / Playback ------------------------------------------------
        rec = ttk.LabelFrame(left, text="  RECORD / PLAYBACK  ", padding=10)
        rec.pack(fill=tk.X, pady=4)
        self.record_btn = ttk.Button(rec, text="●  Start Recording",
                                     style="Record.TButton",
                                     command=self.on_toggle_record)
        self.record_btn.pack(fill=tk.X, pady=3)
        ttk.Button(rec, text="Open PCAP / OSF File...",
                   command=self.on_open_file).pack(fill=tk.X, pady=3)

        # --- Log --------------------------------------------------------------------
        logf = ttk.LabelFrame(left, text="  LOG  ", padding=6)
        logf.pack(fill=tk.BOTH, expand=True, pady=4)
        self.log_widget = scrolledtext.ScrolledText(
            logf, height=8, state=tk.DISABLED, font=("monospace", 8),
            bg=Theme.LOG_BG, fg=Theme.LOG_FG, insertbackground=Theme.LOG_FG,
            relief=tk.FLAT, borderwidth=0, highlightthickness=0)
        self.log_widget.pack(fill=tk.BOTH, expand=True)

        # --- Right side: sensor info + image canvas -----------------------------------
        info = ttk.LabelFrame(right, text="  SENSOR METADATA  ", padding=8)
        info.pack(fill=tk.X)
        self.info_var = tk.StringVar(value="Not connected.")
        ttk.Label(info, textvariable=self.info_var, style="Info.TLabel",
                  justify=tk.LEFT).pack(anchor=tk.W)

        viz = ttk.LabelFrame(right, text="  2D FIELD IMAGES (DESTAGGERED)  ",
                             padding=6)
        viz.pack(fill=tk.BOTH, expand=True, pady=6)
        self.fig = Figure(figsize=(8, 6), dpi=90, tight_layout=True,
                          facecolor=Theme.PANEL)
        self.axes = {}
        for i, (name, title, cmap) in enumerate(FIELD_SPECS):
            ax = self.fig.add_subplot(len(FIELD_SPECS), 1, i + 1)
            self._style_axis(ax, title)
            self.axes[name] = (ax, cmap)
        self.canvas = FigureCanvasTkAgg(self.fig, master=viz)
        widget = self.canvas.get_tk_widget()
        widget.configure(bg=Theme.PANEL, highlightthickness=0)
        widget.pack(fill=tk.BOTH, expand=True)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status_var,
                  style="Status.TLabel",
                  anchor=tk.W).pack(side=tk.BOTTOM, fill=tk.X)

    def _style_axis(self, ax, title):
        ax.set_facecolor(Theme.BG)
        ax.set_title(title, fontsize=9, color=Theme.FG, loc="left")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(Theme.BORDER)

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
        if not self._require_sdk():
            return
        host = self._host()
        self.log(f"Fetching metadata from {host} ...")
        threading.Thread(target=self._fetch_info, args=(host,),
                         daemon=True).start()

    def _fetch_info(self, host):
        try:
            src = open_source(host, sensor_idx=0)
            info = source_metadata(src)
            try:
                src.close()
            except Exception:
                pass
            self.root.after(0, lambda: self._show_metadata(info))
        except Exception as e:
            self.log(f"ERROR: {e}")

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

    def on_apply_config(self):
        if not self._require_sdk():
            return
        host = self._host()
        mode = self.mode_var.get()
        ts = self.ts_var.get()
        try:
            lidar_port = int(self.lidar_port_var.get())
            imu_port = int(self.imu_port_var.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Ports must be integers.")
            return

        def work():
            try:
                cfg = ouster_core.SensorConfig()
                cfg.lidar_mode = parse_lidar_mode(mode)
                cfg.timestamp_mode = getattr(ouster_core.TimestampMode, ts)
                cfg.udp_port_lidar = lidar_port
                cfg.udp_port_imu = imu_port
                set_config(host, cfg, persist=False, udp_dest_auto=True)
                self.log(f"Configuration applied: mode={mode}, ts={ts}, "
                         f"ports={lidar_port}/{imu_port}")
            except Exception as e:
                self.log(f"ERROR: {e}")

        self.log("Applying configuration (sensor will reinitialize)...")
        threading.Thread(target=work, daemon=True).start()

    def on_start_stream(self, source_url=None, is_file=False):
        if not self._require_sdk():
            return
        if self.reader is not None:
            self.log("Stream already running.")
            return
        url = source_url or self._host()
        self.reader = ScanReader(url, self.frame_queue, self.log,
                                 is_file=is_file)
        self.reader.start()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_var.set(f"Streaming from {url} ...")

    def on_stop_stream(self):
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.status_var.set("Stream stopped.")

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

    def on_open_3d(self):
        """Launch Ouster's official 3D point-cloud viewer via ouster-cli."""
        host = self._host()
        cmd = ["ouster-cli", "source", host, "viz"]
        try:
            self.viz_proc = subprocess.Popen(cmd)
            self.log("Launched 3D viewer: " + " ".join(cmd))
        except FileNotFoundError:
            messagebox.showerror(
                "ouster-cli not found",
                "ouster-cli was not found on PATH.\n"
                "It is installed together with:  pip install ouster-sdk")

    def on_toggle_record(self):
        if self.record_proc is None:
            path = filedialog.asksaveasfilename(
                title="Save recording as",
                defaultextension=".pcap",
                filetypes=[("PCAP files", "*.pcap")])
            if not path:
                return
            host = self._host()
            cmd = ["ouster-cli", "source", host, "save", path]
            try:
                self.record_proc = subprocess.Popen(cmd)
                self.record_btn.configure(text="■  Stop Recording")
                self.log("Recording started: " + " ".join(cmd))
            except FileNotFoundError:
                messagebox.showerror(
                    "ouster-cli not found",
                    "ouster-cli was not found on PATH.\n"
                    "It is installed together with:  pip install ouster-sdk")
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
        for name, img in images.items():
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
        self.status_var.set(f"Streaming... frame {frame_id}")

    # ------------------------------------------------------------- shutdown --
    def on_close(self):
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
