#!/usr/bin/env python3
"""
Ouster Lidar Fleet Manager - Projects / Equipment / Sensors
===========================================================
A Tkinter application that organizes Ouster digital lidars into a simple
three-level hierarchy and lets you talk to each sensor:

    Project  ->  Equipment  ->  Ouster sensor  ->  Sensor dashboard

  * Projects    - a site, a customer, a research program ...
  * Equipment   - the vehicle / mast / robot the sensors are mounted on
  * Sensors     - one entry per physical Ouster lidar (hostname or IP)

Everything is stored locally in ~/.ouster_projects.json, so the tree, the
per-sensor configuration and the per-sensor network settings survive
restarts and can be copied between machines.

From a sensor's dashboard you can:

  * READ FROM THE SENSOR   - pull the live configuration, read metadata,
    query status / telemetry / alerts, stream scans and view
    RANGE / SIGNAL / REFLECTIVITY / NEAR_IR as destaggered 2D images,
    and open Ouster's official 3D point-cloud viewer.
  * WRITE TO THE SENSOR    - push the configuration stored in the project
    (lidar mode, timestamp mode, operating mode, signal multiplier,
    azimuth window, UDP profile, UDP ports) and push the stored network
    settings (static IP / gateway, or revert to DHCP).
  * Record the stream to a PCAP file, replay PCAP/OSF recordings offline
    (so the app is fully usable without a physical sensor) and export a
    recording to MCAP for Foxglove.

Tested with ouster-sdk 1.0.0; also compatible with the older
`ouster.sdk.client` API (< 1.0).

Run:  python3 ouster_gui.py
"""

import copy
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
import tkinter as tk
import warnings
from collections import deque
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

__version__ = "2.0.0"

import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning, module="ouster")

# --- ouster-sdk imports (support SDK >= 1.0 and older releases) --------------
try:
    from ouster.sdk import open_source
    try:
        from ouster.sdk import open_packet_source
    except Exception:
        open_packet_source = None
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
    # Quiet the native SDK console logger. It otherwise prints harmless
    # "Duplicate metadata type / Already registered" errors at startup on
    # some platforms; real problems are still surfaced in the app's Log
    # panel via caught exceptions.
    try:
        ouster_core.init_logger("critical")
    except Exception:
        pass
    HAVE_OUSTER = True
    OUSTER_IMPORT_ERROR = None
except Exception as _e:  # SDK missing entirely
    HAVE_OUSTER = False
    OUSTER_IMPORT_ERROR = _e
    ouster_core = None
    open_source = None
    open_packet_source = None
    get_config = set_config = None
    SensorHttp = None

# --- MCAP export (optional: only needed for "Export to MCAP") ----------------
try:
    from mcap.writer import Writer as McapWriter
    from mcap_protobuf.schema import build_file_descriptor_set
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


# set by apply_theme() once the default font family is known
TITLE_FONT = ("TkDefaultFont", 15, "bold")
CRUMB_FONT = ("TkDefaultFont", 10, "bold")


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

    # breadcrumb bar ------------------------------------------------------------
    style.configure("Crumb.TFrame", background=Theme.BG)
    style.configure("Crumb.TLabel", background=Theme.BG,
                    foreground=Theme.MUTED)
    style.configure("CrumbLink.TLabel", background=Theme.BG,
                    foreground=Theme.ACCENT)
    style.configure("CrumbHere.TLabel", background=Theme.BG,
                    foreground=Theme.FG)
    global TITLE_FONT, CRUMB_FONT
    TITLE_FONT = (base_font.actual("family"), 15, "bold")
    CRUMB_FONT = (base_font.actual("family"), 10, "bold")
    # NOTE: root.option_add("*Font", ...) above wins over a style's font for
    # ttk widgets, so screen titles pass their font directly (see TITLE_FONT).
    style.configure("Title.TLabel", background=Theme.BG, foreground=Theme.FG)
    style.configure("Subtitle.TLabel", background=Theme.BG,
                    foreground=Theme.MUTED)
    style.configure("Status.TLabel", background=Theme.PANEL,
                    foreground=Theme.MUTED, padding=(8, 3))

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
    # primary call-to-action (Open, Push to sensor, ...)
    style.configure("Accent.TButton", background=Theme.PY_BLUE,
                    foreground="#ffffff", bordercolor=Theme.PY_BLUE)
    style.map("Accent.TButton",
              background=[("active", Theme.PY_BLUE_L),
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
    # classic Tk scrollbars (the ones inside ScrolledText) are not ttk
    root.option_add("*Scrollbar.background", Theme.FIELD)
    root.option_add("*Scrollbar.troughColor", Theme.PANEL)
    root.option_add("*Scrollbar.activeBackground", Theme.BORDER)
    root.option_add("*Scrollbar.highlightBackground", Theme.PANEL)
    root.option_add("*Scrollbar.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.background", Theme.FIELD)
    root.option_add("*TCombobox*Listbox.foreground", Theme.FG)
    root.option_add("*TCombobox*Listbox.selectBackground", Theme.ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", Theme.BG)

    # lists (projects / equipment / sensors) -----------------------------------
    style.configure("Treeview", background=Theme.CARD,
                    fieldbackground=Theme.CARD, foreground=Theme.FG,
                    bordercolor=Theme.BORDER, borderwidth=0, rowheight=28)
    style.map("Treeview",
              background=[("selected", Theme.PY_BLUE)],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=Theme.FIELD,
                    foreground=Theme.MUTED, relief="flat",
                    font=(base_font.actual("family"), 9, "bold"),
                    padding=(6, 6))
    style.map("Treeview.Heading", background=[("active", Theme.BORDER)])


LIDAR_MODES = ["512x10", "512x20", "1024x10", "1024x20", "2048x10"]
TIMESTAMP_MODES = [
    "TIME_FROM_INTERNAL_OSC",
    "TIME_FROM_SYNC_PULSE_IN",
    "TIME_FROM_PTP_1588",
]

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
EQUIPMENT_TYPES = ["Vehicle", "Drone / UAV", "Robot / AMR", "Mast / Tripod",
                   "Rail / Gantry", "Building / Fixed", "Lab bench", "Other"]

# (field name, plot title, colormap)
FIELD_SPECS = [
    ("RANGE", "Range [mm]", "viridis"),
    ("SIGNAL", "Signal", "magma"),
    ("REFLECTIVITY", "Reflectivity", "gray"),
    ("NEAR_IR", "Near-IR (ambient)", "cividis"),
]
FIELD_TITLES = {name: title for name, title, _ in FIELD_SPECS}

# ------------------------------------------------------------------ store ----
STORE_PATH = os.path.join(os.path.expanduser("~"), ".ouster_projects.json")
LEGACY_SETTINGS_PATH = os.path.join(os.path.expanduser("~"),
                                    ".ouster_lidar_gui.json")

# per-sensor configuration kept in the project tree and pushed on demand
DEFAULT_SENSOR_CONFIG = {
    "lidar_mode": "1024x10",
    "timestamp_mode": TIMESTAMP_MODES[0],
    "operating_mode": OPERATING_MODES[0],
    "signal_multiplier": SIGNAL_MULTIPLIERS[0],
    "udp_profile": UNCHANGED,
    "az_start": "0",
    "az_end": "360",
    "lidar_port": "7502",
    "imu_port": "7503",
    "persist": False,
}
DEFAULT_SENSOR_NETWORK = {
    "static_ip": "",     # e.g. 192.168.1.50/24  ("" = DHCP / link-local)
    "gateway": "",
}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


class Store:
    """The project tree, persisted as a single JSON file.

    Layout::

        {"version": 2,
         "projects": [{"id", "name", "site", "notes", "created",
                       "equipment": [{"id", "name", "type", "serial",
                                      "notes", "created",
                                      "sensors": [{"id", "name", "host",
                                                   "model", "notes",
                                                   "created", "last_seen",
                                                   "config": {...},
                                                   "network": {...}}]}]}]}
    """

    def __init__(self, path: str = STORE_PATH):
        self.path = path
        self.data = self._load()
        self._migrate_legacy()

    # -- io -----------------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 2)
        if not isinstance(data.get("projects"), list):
            data["projects"] = []
        if not isinstance(data.get("app"), dict):
            data["app"] = {}
        for project in data["projects"]:
            project.setdefault("equipment", [])
            for equipment in project["equipment"]:
                equipment.setdefault("sensors", [])
                for sensor in equipment["sensors"]:
                    self.normalize_sensor(sensor)
        return data

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"Could not save {self.path}: {e}", file=sys.stderr)

    def _migrate_legacy(self):
        """Import sensors from the v1 single-sensor settings file, once."""
        if self.data["projects"] or self.data["app"].get("legacy_imported"):
            return
        try:
            with open(LEGACY_SETTINGS_PATH, encoding="utf-8") as f:
                legacy = json.load(f)
        except Exception:
            return
        if not isinstance(legacy, dict):
            return

        entries = []
        profiles = legacy.get("profiles")
        if isinstance(profiles, dict):
            entries.extend((name, values) for name, values
                           in profiles.items() if isinstance(values, dict))
        if not entries and legacy.get("host"):
            entries.append((str(legacy["host"]), legacy))
        if not entries:
            return

        project = self.add_project({"name": "Imported",
                                    "site": "",
                                    "notes": "Imported from "
                                             f"{LEGACY_SETTINGS_PATH}"})
        equipment = self.add_equipment(project, {"name": "Imported equipment",
                                                 "type": "Other",
                                                 "serial": "", "notes": ""})
        for name, values in entries:
            sensor = self.add_sensor(equipment, {
                "name": name,
                "host": str(values.get("host", "")),
                "model": "", "notes": ""})
            for key in DEFAULT_SENSOR_CONFIG:
                if key in values:
                    sensor["config"][key] = values[key]
        self.data["app"]["legacy_imported"] = True
        self.save()

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def normalize_sensor(sensor: dict) -> dict:
        cfg = sensor.get("config")
        if not isinstance(cfg, dict):
            cfg = {}
        sensor["config"] = {**copy.deepcopy(DEFAULT_SENSOR_CONFIG), **cfg}
        net = sensor.get("network")
        if not isinstance(net, dict):
            net = {}
        sensor["network"] = {**copy.deepcopy(DEFAULT_SENSOR_NETWORK), **net}
        sensor.setdefault("last_seen", "")
        return sensor

    @staticmethod
    def find(items: list, item_id: str):
        for item in items or []:
            if item.get("id") == item_id:
                return item
        return None

    @property
    def projects(self) -> list:
        return self.data["projects"]

    # -- create -------------------------------------------------------------
    def add_project(self, values: dict) -> dict:
        project = {"id": new_id(), "created": now_stamp(), "equipment": []}
        project.update(values)
        self.projects.append(project)
        self.save()
        return project

    def add_equipment(self, project: dict, values: dict) -> dict:
        equipment = {"id": new_id(), "created": now_stamp(), "sensors": []}
        equipment.update(values)
        project.setdefault("equipment", []).append(equipment)
        self.save()
        return equipment

    def add_sensor(self, equipment: dict, values: dict) -> dict:
        sensor = {"id": new_id(), "created": now_stamp()}
        sensor.update(values)
        self.normalize_sensor(sensor)
        equipment.setdefault("sensors", []).append(sensor)
        self.save()
        return sensor

    # -- delete -------------------------------------------------------------
    def delete(self, container: list, item: dict):
        try:
            container.remove(item)
        except ValueError:
            pass
        self.save()

    # -- stats --------------------------------------------------------------
    @staticmethod
    def project_counts(project: dict):
        equipment = project.get("equipment", [])
        sensors = sum(len(e.get("sensors", [])) for e in equipment)
        return len(equipment), sensors


# ---------------------------------------------------------------- dialogs ----
class FormDialog(tk.Toplevel):
    """Small modal form. `fields` is a list of dicts:

        {"key", "label", "kind": entry|combo|text|check,
         "values": [...], "hint": "...", "required": bool}
    """

    def __init__(self, parent, title, fields, initial=None, ok_text="Save"):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=Theme.BG)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self._fields = fields
        self._vars = {}
        self._texts = {}

        initial = initial or {}
        body = ttk.Frame(self, style="Panel.TFrame", padding=14)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 6))

        first_widget = None
        for spec in fields:
            key, kind = spec["key"], spec.get("kind", "entry")
            ttk.Label(body, text=spec["label"],
                      style="Muted.TLabel").pack(anchor=tk.W, pady=(6, 0))
            value = initial.get(key, spec.get("default", ""))
            if kind == "text":
                widget = scrolledtext.ScrolledText(
                    body, height=4, width=44, font=("TkDefaultFont", 9),
                    bg=Theme.FIELD, fg=Theme.FG, insertbackground=Theme.FG,
                    relief=tk.FLAT, borderwidth=0, highlightthickness=1,
                    highlightbackground=Theme.BORDER, wrap=tk.WORD)
                widget.insert("1.0", str(value or ""))
                widget.pack(fill=tk.X, pady=2)
                self._texts[key] = widget
            elif kind == "check":
                var = tk.BooleanVar(value=bool(value))
                widget = ttk.Checkbutton(body, text=spec.get("hint", ""),
                                         variable=var, style="TCheckbutton")
                widget.pack(anchor=tk.W, pady=2)
                self._vars[key] = var
            elif kind == "combo":
                var = tk.StringVar(value=str(value))
                widget = ttk.Combobox(body, textvariable=var,
                                      values=spec.get("values", []), width=42)
                widget.pack(fill=tk.X, pady=2)
                self._vars[key] = var
            else:
                var = tk.StringVar(value=str(value))
                widget = ttk.Entry(body, textvariable=var, width=44)
                widget.pack(fill=tk.X, pady=2)
                self._vars[key] = var
            if spec.get("hint") and kind != "check":
                ttk.Label(body, text=spec["hint"],
                          style="Hint.TLabel").pack(anchor=tk.W)
            if first_widget is None:
                first_widget = widget

        btns = ttk.Frame(self, style="TFrame")
        btns.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(btns, text="Cancel",
                   command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text=ok_text, style="Accent.TButton",
                   command=self._ok).pack(side=tk.RIGHT, padx=6)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        if first_widget is not None:
            first_widget.focus_set()
        self.update_idletasks()
        self._center(parent)
        self.grab_set()
        parent.wait_window(self)

    def _center(self, parent):
        try:
            x = parent.winfo_rootx() + (parent.winfo_width()
                                        - self.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height()
                                        - self.winfo_height()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass

    def _values(self) -> dict:
        values = {key: var.get() for key, var in self._vars.items()}
        for key, widget in self._texts.items():
            values[key] = widget.get("1.0", tk.END).strip()
        for key, value in list(values.items()):
            if isinstance(value, str):
                values[key] = value.strip()
        return values

    def _ok(self):
        values = self._values()
        for spec in self._fields:
            if spec.get("required") and not values.get(spec["key"]):
                messagebox.showerror(self.title(),
                                     f"'{spec['label']}' is required.",
                                     parent=self)
                return
        self.result = values
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ----------------------------------------------------------- sdk helpers -----
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


def config_to_dict(cfg) -> dict:
    """Read a SensorConfig into the flat dict this app stores per sensor.

    Values the sensor does not report are left out, so the caller can keep
    whatever it already had for those keys.
    """
    out = {}

    def text(value):
        return None if value is None else str(value)

    mapping = (("lidar_mode", "lidar_mode"),
               ("timestamp_mode", "timestamp_mode"),
               ("operating_mode", "operating_mode"))
    for key, attr in mapping:
        value = text(getattr(cfg, attr, None))
        if value:
            out[key] = value

    profile = text(getattr(cfg, "udp_profile_lidar", None))
    if profile:
        out["udp_profile"] = profile

    mult = getattr(cfg, "signal_multiplier", None)
    if mult is not None:
        # 1.0 -> "1" so it matches the combobox entries
        out["signal_multiplier"] = (f"{mult:g}" if isinstance(mult, float)
                                    else str(mult))

    window = getattr(cfg, "azimuth_window", None)
    if window and len(window) == 2:
        out["az_start"] = f"{window[0] / 1000:g}"
        out["az_end"] = f"{window[1] / 1000:g}"

    for key, attr in (("lidar_port", "udp_port_lidar"),
                      ("imu_port", "udp_port_imu")):
        port = getattr(cfg, attr, None)
        if port:
            out[key] = str(port)
    return out


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
        root.title(f"Ouster Lidar Fleet Manager  v{__version__}  ·  "
                   "Powered by Python")
        root.geometry("1320x880")
        root.minsize(1000, 640)
        apply_theme(root)
        enable_dark_title_bar(root)

        self.store = Store()

        # current position in the tree
        self.project = None
        self.equipment = None
        self.sensor = None

        # streaming / child processes
        self.reader = None
        self.frame_queue = queue.Queue(maxsize=4)
        self.record_proc = None
        self.viz_proc = None
        self.last_frame_status = {}

        # dashboard widgets (recreated every time the dashboard is shown)
        self.log_lines = deque(maxlen=500)
        self.log_widget = None
        self.info_var = None
        self.canvas = None
        self.fig = None
        self.axes = {}
        self.image_artists = {}
        self.view_buttons = {}
        self.view_field = None
        self.last_images = {}
        self.last_frame_id = 0
        self.cfg_vars = {}

        self._build_shell()
        self._poll_queue()

        self.log(f"Ouster Lidar Fleet Manager v{__version__} ready.")
        self.log(f"Project database: {self.store.path}")
        if not HAVE_OUSTER:
            self.log("WARNING: ouster-sdk is not installed "
                     f"({OUSTER_IMPORT_ERROR}).")
            self.log("Install it with:  pip install ouster-sdk")

        self.show_projects()

    # ------------------------------------------------------------- shell ---
    def _build_shell(self):
        # Blank (transparent) window icon - hides Tk's default feather icon
        self.icon_img = tk.PhotoImage(width=16, height=16)
        self.root.iconphoto(True, self.icon_img)
        ttk.Frame(self.root, style="HeaderStrip.TFrame",
                  height=3).pack(fill=tk.X)

        # breadcrumb / back bar
        bar = ttk.Frame(self.root, style="Crumb.TFrame",
                        padding=(12, 8, 12, 4))
        bar.pack(fill=tk.X)
        self.back_btn = ttk.Button(bar, text="←  Back", width=9,
                                   command=self._go_back, state=tk.DISABLED)
        self.back_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.crumb_bar = ttk.Frame(bar, style="Crumb.TFrame")
        self.crumb_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bar, text="?  Help",
                   command=self.on_help).pack(side=tk.RIGHT)

        self.body = ttk.Frame(self.root, padding=(10, 2, 10, 6))
        self.body.pack(fill=tk.BOTH, expand=True)

        status = ttk.Frame(self.root, style="Panel.TFrame")
        status.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(status, textvariable=self.status_var,
                  style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(status, text="oT", style="Status.TLabel").pack(side=tk.RIGHT)

    def _set_crumbs(self, crumbs, back=None):
        """crumbs: list of (text, callback|None); the last one is 'here'."""
        for child in self.crumb_bar.winfo_children():
            child.destroy()
        for i, (text, callback) in enumerate(crumbs):
            if i:
                ttk.Label(self.crumb_bar, text="  ›  ",
                          style="Crumb.TLabel").pack(side=tk.LEFT)
            last = i == len(crumbs) - 1
            label = ttk.Label(self.crumb_bar, text=text,
                              style="CrumbHere.TLabel" if last
                              else "CrumbLink.TLabel",
                              font=CRUMB_FONT if last
                              else tkfont.nametofont("TkDefaultFont"))
            label.pack(side=tk.LEFT)
            if not last and callback is not None:
                label.configure(cursor="hand2")
                label.bind("<Button-1>", lambda e, cb=callback: cb())
        self._back_target = back
        self.back_btn.configure(state=tk.DISABLED if back is None
                                else tk.NORMAL)

    def _go_back(self):
        if getattr(self, "_back_target", None) is not None:
            self._back_target()

    def _clear_body(self):
        """Leave the current screen: stop streaming and drop its widgets."""
        if self.reader is not None:
            self.on_stop_stream()
        self.log_widget = None
        self.info_var = None
        self.canvas = None
        self.fig = None
        self.axes = {}
        self.image_artists = {}
        self.view_buttons = {}
        self.last_images = {}
        self.cfg_vars = {}
        for child in self.body.winfo_children():
            child.destroy()

    # ------------------------------------------------------- generic list ---
    def _build_list_screen(self, title, subtitle, columns, rows, actions,
                           on_open, empty_hint):
        """Shared layout for the projects / equipment / sensors screens.

        columns: list of (key, heading, width)
        rows:    list of (item, {key: text})
        actions: list of (label, callback, style) shown in the toolbar
        """
        head = ttk.Frame(self.body, style="TFrame")
        head.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(head, text=title, style="Title.TLabel",
                  font=TITLE_FONT).pack(anchor=tk.W)
        if subtitle:
            ttk.Label(head, text=subtitle,
                      style="Subtitle.TLabel").pack(anchor=tk.W, pady=(2, 0))

        toolbar = ttk.Frame(self.body, style="TFrame")
        toolbar.pack(fill=tk.X, pady=8)
        for label, callback, style in actions:
            ttk.Button(toolbar, text=label, command=callback,
                       style=style or "TButton").pack(side=tk.LEFT,
                                                      padx=(0, 6))

        card = ttk.Frame(self.body, style="Panel.TFrame", padding=1)
        card.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(card, columns=[c[0] for c in columns],
                            show="headings", selectmode="browse")
        for key, heading, width in columns:
            tree.heading(key, text=heading, anchor=tk.W)
            tree.column(key, width=width, anchor=tk.W,
                        stretch=(key == columns[0][0]))
        vbar = ttk.Scrollbar(card, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._tree = tree
        self._tree_items = {}
        for item, values in rows:
            iid = item["id"]
            self._tree_items[iid] = item
            tree.insert("", tk.END, iid=iid,
                        values=[values.get(c[0], "") for c in columns])
        if rows:
            tree.selection_set(rows[0][0]["id"])
        else:
            ttk.Label(card, text=empty_hint, style="Muted.TLabel").place(
                relx=0.5, rely=0.45, anchor=tk.CENTER)

        tree.bind("<Double-1>", lambda e: on_open())
        tree.bind("<Return>", lambda e: on_open())
        return tree

    def _selected(self):
        """The item selected in the current list screen, or None."""
        tree = getattr(self, "_tree", None)
        if tree is None:
            return None
        selection = tree.selection()
        if not selection:
            return None
        return self._tree_items.get(selection[0])

    def _require_selection(self, what):
        item = self._selected()
        if item is None:
            messagebox.showinfo(what, f"Select a {what.lower()} in the list "
                                      "first.")
        return item

    # ---------------------------------------------------------- projects ----
    def show_projects(self):
        self._clear_body()
        self.project = self.equipment = self.sensor = None
        self._set_crumbs([("Projects", None)], back=None)

        rows = []
        for project in self.store.projects:
            n_equipment, n_sensors = Store.project_counts(project)
            rows.append((project, {
                "name": project.get("name", ""),
                "site": project.get("site", ""),
                "equipment": str(n_equipment),
                "sensors": str(n_sensors),
                "created": project.get("created", ""),
            }))

        self._build_list_screen(
            "Projects",
            "Pick a project to open its equipment, or create a new one.",
            [("name", "Project", 260), ("site", "Site / customer", 220),
             ("equipment", "Equipment", 90), ("sensors", "Sensors", 80),
             ("created", "Created", 150)],
            rows,
            [("Open", self.open_project, "Accent.TButton"),
             ("New project", self.new_project, None),
             ("Edit", self.edit_project, None),
             ("Delete", self.delete_project, None)],
            self.open_project,
            "No projects yet - click 'New project' to create the first one.")
        self.status_var.set(f"{len(self.store.projects)} project(s).")

    _PROJECT_FIELDS = [
        {"key": "name", "label": "Project name", "required": True,
         "hint": "e.g. 'Highway survey 2026' or 'Lab R&D'"},
        {"key": "site", "label": "Site / customer"},
        {"key": "notes", "label": "Notes", "kind": "text"},
    ]

    def new_project(self):
        dialog = FormDialog(self.root, "New project", self._PROJECT_FIELDS,
                            ok_text="Create")
        if dialog.result:
            project = self.store.add_project(dialog.result)
            self.log(f"Project '{project['name']}' created.")
            self.show_projects()

    def edit_project(self):
        project = self._require_selection("Project")
        if project is None:
            return
        dialog = FormDialog(self.root, "Edit project", self._PROJECT_FIELDS,
                            initial=project)
        if dialog.result:
            project.update(dialog.result)
            self.store.save()
            self.log(f"Project '{project['name']}' updated.")
            self.show_projects()

    def delete_project(self):
        project = self._require_selection("Project")
        if project is None:
            return
        n_equipment, n_sensors = Store.project_counts(project)
        if not messagebox.askyesno(
                "Delete project",
                f"Delete project '{project.get('name')}' with its "
                f"{n_equipment} equipment item(s) and {n_sensors} sensor(s)?"
                "\n\nThis only removes them from this app - the sensors "
                "themselves are not touched."):
            return
        self.store.delete(self.store.projects, project)
        self.log(f"Project '{project.get('name')}' deleted.")
        self.show_projects()

    def open_project(self):
        project = self._require_selection("Project")
        if project is None:
            return
        self.project = project
        self.show_equipment()

    # --------------------------------------------------------- equipment ----
    def show_equipment(self):
        self._clear_body()
        self.equipment = self.sensor = None
        project = self.project
        self._set_crumbs([("Projects", self.show_projects),
                          (project.get("name", "Project"), None)],
                         back=self.show_projects)

        rows = []
        for equipment in project.get("equipment", []):
            rows.append((equipment, {
                "name": equipment.get("name", ""),
                "type": equipment.get("type", ""),
                "serial": equipment.get("serial", ""),
                "sensors": str(len(equipment.get("sensors", []))),
                "created": equipment.get("created", ""),
            }))

        self._build_list_screen(
            f"Equipment · {project.get('name', '')}",
            "The platform the lidars are mounted on: a vehicle, a mast, "
            "a robot ...",
            [("name", "Equipment", 260), ("type", "Type", 180),
             ("serial", "Serial / asset ID", 180),
             ("sensors", "Sensors", 80), ("created", "Created", 150)],
            rows,
            [("Open", self.open_equipment, "Accent.TButton"),
             ("New equipment", self.new_equipment, None),
             ("Edit", self.edit_equipment, None),
             ("Delete", self.delete_equipment, None)],
            self.open_equipment,
            "No equipment in this project yet - click 'New equipment'.")
        self.status_var.set(f"{len(project.get('equipment', []))} equipment "
                            f"item(s) in '{project.get('name', '')}'.")

    _EQUIPMENT_FIELDS = [
        {"key": "name", "label": "Equipment name", "required": True,
         "hint": "e.g. 'Van #3' or 'Mast - north gate'"},
        {"key": "type", "label": "Type", "kind": "combo",
         "values": EQUIPMENT_TYPES, "default": EQUIPMENT_TYPES[0]},
        {"key": "serial", "label": "Serial / asset ID"},
        {"key": "notes", "label": "Notes", "kind": "text"},
    ]

    def new_equipment(self):
        dialog = FormDialog(self.root, "New equipment",
                            self._EQUIPMENT_FIELDS, ok_text="Create")
        if dialog.result:
            equipment = self.store.add_equipment(self.project, dialog.result)
            self.log(f"Equipment '{equipment['name']}' added to "
                     f"'{self.project.get('name')}'.")
            self.show_equipment()

    def edit_equipment(self):
        equipment = self._require_selection("Equipment")
        if equipment is None:
            return
        dialog = FormDialog(self.root, "Edit equipment",
                            self._EQUIPMENT_FIELDS, initial=equipment)
        if dialog.result:
            equipment.update(dialog.result)
            self.store.save()
            self.log(f"Equipment '{equipment['name']}' updated.")
            self.show_equipment()

    def delete_equipment(self):
        equipment = self._require_selection("Equipment")
        if equipment is None:
            return
        n_sensors = len(equipment.get("sensors", []))
        if not messagebox.askyesno(
                "Delete equipment",
                f"Delete equipment '{equipment.get('name')}' with its "
                f"{n_sensors} sensor(s)?"):
            return
        self.store.delete(self.project.setdefault("equipment", []), equipment)
        self.log(f"Equipment '{equipment.get('name')}' deleted.")
        self.show_equipment()

    def open_equipment(self):
        equipment = self._require_selection("Equipment")
        if equipment is None:
            return
        self.equipment = equipment
        self.show_sensors()

    # ----------------------------------------------------------- sensors ----
    def show_sensors(self):
        self._clear_body()
        self.sensor = None
        project, equipment = self.project, self.equipment
        self._set_crumbs([("Projects", self.show_projects),
                          (project.get("name", "Project"),
                           self.show_equipment),
                          (equipment.get("name", "Equipment"), None)],
                         back=self.show_equipment)

        rows = []
        for sensor in equipment.get("sensors", []):
            Store.normalize_sensor(sensor)
            cfg = sensor["config"]
            rows.append((sensor, {
                "name": sensor.get("name", ""),
                "host": sensor.get("host", ""),
                "model": sensor.get("model", ""),
                "mode": cfg.get("lidar_mode", ""),
                "last_seen": sensor.get("last_seen") or "never",
            }))

        self._build_list_screen(
            f"Ouster sensors · {equipment.get('name', '')}",
            "Open a sensor to read data from it or push settings to it.",
            [("name", "Sensor", 220), ("host", "Hostname / IP", 240),
             ("model", "Model", 140), ("mode", "Saved lidar mode", 140),
             ("last_seen", "Last contact", 140)],
            rows,
            [("Open", self.open_sensor, "Accent.TButton"),
             ("New sensor", self.new_sensor, None),
             ("Edit", self.edit_sensor, None),
             ("Delete", self.delete_sensor, None)],
            self.open_sensor,
            "No sensors on this equipment yet - click 'New sensor'.")
        self.status_var.set(f"{len(equipment.get('sensors', []))} sensor(s) "
                            f"on '{equipment.get('name', '')}'.")

    _SENSOR_FIELDS = [
        {"key": "name", "label": "Sensor name", "required": True,
         "hint": "e.g. 'front-left OS1'"},
        {"key": "host", "label": "Hostname / IP", "required": True,
         "hint": "e.g. os-122xxxxxxxxxx.local  or  192.168.1.50"},
        {"key": "model", "label": "Model / product line",
         "hint": "optional, e.g. OS1-128 (filled in automatically after "
                 "the first connection)"},
        {"key": "notes", "label": "Notes", "kind": "text"},
    ]

    def new_sensor(self):
        dialog = FormDialog(self.root, "New Ouster sensor",
                            self._SENSOR_FIELDS, ok_text="Create")
        if dialog.result:
            sensor = self.store.add_sensor(self.equipment, dialog.result)
            self.log(f"Sensor '{sensor['name']}' ({sensor.get('host')}) "
                     f"added to '{self.equipment.get('name')}'.")
            self.show_sensors()

    def edit_sensor(self):
        sensor = self._require_selection("Sensor")
        if sensor is None:
            return
        dialog = FormDialog(self.root, "Edit sensor", self._SENSOR_FIELDS,
                            initial=sensor)
        if dialog.result:
            sensor.update(dialog.result)
            self.store.save()
            self.log(f"Sensor '{sensor['name']}' updated.")
            self.show_sensors()

    def delete_sensor(self):
        sensor = self._require_selection("Sensor")
        if sensor is None:
            return
        if not messagebox.askyesno(
                "Delete sensor",
                f"Remove sensor '{sensor.get('name')}' "
                f"({sensor.get('host')}) from this equipment?"):
            return
        self.store.delete(self.equipment.setdefault("sensors", []), sensor)
        self.log(f"Sensor '{sensor.get('name')}' deleted.")
        self.show_sensors()

    def open_sensor(self):
        sensor = self._require_selection("Sensor")
        if sensor is None:
            return
        self.sensor = Store.normalize_sensor(sensor)
        self.show_dashboard()

    # --------------------------------------------------------- dashboard ----
    def show_dashboard(self):
        self._clear_body()
        sensor = self.sensor
        self._set_crumbs([("Projects", self.show_projects),
                          (self.project.get("name", "Project"),
                           self.show_equipment),
                          (self.equipment.get("name", "Equipment"),
                           self.show_sensors),
                          (sensor.get("name", "Sensor"), None)],
                         back=self.show_sensors)

        main = ttk.Frame(self.body, style="TFrame")
        main.pack(fill=tk.BOTH, expand=True)

        # scrollable left panel (the controls can be taller than the window)
        left_container = ttk.Frame(main, width=352)
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

        self._build_connection_panel(left)
        self._build_config_panel(left)
        self._build_stream_panel(left)
        self._build_record_panel(left)
        self._build_log_panel(left)
        self._build_viz_panel(right)

        self.status_var.set(f"Sensor '{sensor.get('name')}' "
                            f"({sensor.get('host')}) - "
                            f"{self.project.get('name')} / "
                            f"{self.equipment.get('name')}")

    def _build_connection_panel(self, parent):
        sensor = self.sensor
        conn = ttk.LabelFrame(parent, text="  SENSOR CONNECTION  ", padding=10)
        conn.pack(fill=tk.X, pady=4)
        ttk.Label(conn, text="Hostname / IP:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.host_var = tk.StringVar(value=sensor.get("host", ""))
        ttk.Entry(conn, textvariable=self.host_var).pack(
            fill=tk.X, pady=(3, 0))
        ttk.Label(conn, text="stored in the project; edit it and press "
                             "'Save host to project'", style="Hint.TLabel",
                  wraplength=300, justify=tk.LEFT).pack(anchor=tk.W,
                                                        pady=(0, 3))
        ttk.Button(conn, text="Save host to project",
                   command=self.on_save_host).pack(fill=tk.X, pady=(0, 4))
        row = ttk.Frame(conn, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Button(row, text="Web dashboard",
                   command=self.on_get_info).pack(side=tk.LEFT, expand=True,
                                                  fill=tk.X, padx=(0, 3))
        ttk.Button(row, text="Get Status",
                   command=self.on_get_status).pack(side=tk.LEFT, expand=True,
                                                    fill=tk.X, padx=(3, 0))
        row2 = ttk.Frame(conn, style="Panel.TFrame")
        row2.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(row2, text="Reinitialize",
                   command=self.on_reinit).pack(side=tk.LEFT, expand=True,
                                                fill=tk.X, padx=(0, 3))
        ttk.Button(row2, text="Network / IP...",
                   command=self.on_network).pack(side=tk.LEFT, expand=True,
                                                 fill=tk.X, padx=(3, 0))

    def _build_config_panel(self, parent):
        cfg_values = self.sensor["config"]
        cfg = ttk.LabelFrame(parent, text="  SENSOR CONFIGURATION  ",
                             padding=10)
        cfg.pack(fill=tk.X, pady=4)

        def combo(label, key, values):
            ttk.Label(cfg, text=label, style="Muted.TLabel").pack(anchor=tk.W)
            var = tk.StringVar(value=str(cfg_values.get(key, values[0])))
            ttk.Combobox(cfg, textvariable=var, values=values,
                         state="readonly").pack(fill=tk.X, pady=3)
            self.cfg_vars[key] = var

        combo("Lidar mode:", "lidar_mode", LIDAR_MODES)
        combo("Timestamp mode:", "timestamp_mode", TIMESTAMP_MODES)
        combo("Operating mode:", "operating_mode", OPERATING_MODES)
        combo("Signal multiplier:", "signal_multiplier", SIGNAL_MULTIPLIERS)
        combo("UDP data profile:", "udp_profile", UDP_PROFILES)

        az = ttk.Frame(cfg, style="Panel.TFrame")
        az.pack(fill=tk.X, pady=3)
        ttk.Label(az, text="Azimuth window (deg):",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=4,
                                             sticky=tk.W)
        ttk.Label(az, text="start", style="Muted.TLabel").grid(row=1, column=0)
        self.cfg_vars["az_start"] = tk.StringVar(
            value=str(cfg_values.get("az_start", "0")))
        ttk.Entry(az, textvariable=self.cfg_vars["az_start"],
                  width=6).grid(row=1, column=1, padx=(2, 8))
        ttk.Label(az, text="end", style="Muted.TLabel").grid(row=1, column=2)
        self.cfg_vars["az_end"] = tk.StringVar(
            value=str(cfg_values.get("az_end", "360")))
        ttk.Entry(az, textvariable=self.cfg_vars["az_end"],
                  width=6).grid(row=1, column=3, padx=2)

        ports = ttk.Frame(cfg, style="Panel.TFrame")
        ports.pack(fill=tk.X, pady=3)
        ttk.Label(ports, text="Lidar port:",
                  style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W,
                                             pady=1)
        self.cfg_vars["lidar_port"] = tk.StringVar(
            value=str(cfg_values.get("lidar_port", "7502")))
        ttk.Entry(ports, textvariable=self.cfg_vars["lidar_port"],
                  width=8).grid(row=0, column=1, padx=6, pady=1)
        ttk.Label(ports, text="IMU port:",
                  style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W,
                                             pady=1)
        self.cfg_vars["imu_port"] = tk.StringVar(
            value=str(cfg_values.get("imu_port", "7503")))
        ttk.Entry(ports, textvariable=self.cfg_vars["imu_port"],
                  width=8).grid(row=1, column=1, padx=6, pady=1)

        self.cfg_vars["persist"] = tk.BooleanVar(
            value=bool(cfg_values.get("persist", False)))
        ttk.Checkbutton(cfg, text="Persist (keep after reboot)",
                        variable=self.cfg_vars["persist"],
                        style="TCheckbutton").pack(anchor=tk.W, pady=(6, 0))

        ttk.Button(cfg, text="⤓  Pull from sensor",
                   command=self.on_pull_config).pack(fill=tk.X, pady=(8, 3))
        ttk.Button(cfg, text="⤒  Push to sensor", style="Accent.TButton",
                   command=self.on_push_config).pack(fill=tk.X, pady=3)
        ttk.Button(cfg, text="Save to project (no sensor access)",
                   command=lambda: self.on_save_config(True)).pack(fill=tk.X,
                                                                   pady=3)
        ttk.Label(cfg, text="Pull reads the live config into the form; "
                            "Push writes the form to the sensor. Both save "
                            "to the project.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

    def _build_stream_panel(self, parent):
        stream = ttk.LabelFrame(parent, text="  LIVE STREAM  ", padding=10)
        stream.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(stream, text="▶  Start Stream",
                                    style="Accent.TButton",
                                    command=self.on_start_stream)
        self.start_btn.pack(fill=tk.X, pady=3)
        self.stop_btn = ttk.Button(stream, text="■  Stop Stream",
                                   command=self.on_stop_stream,
                                   state=tk.DISABLED)
        self.stop_btn.pack(fill=tk.X, pady=3)
        ttk.Button(stream, text="Open 3D Viewer (point cloud)",
                   command=self.on_open_3d).pack(fill=tk.X, pady=3)

    def _build_record_panel(self, parent):
        rec = ttk.LabelFrame(parent, text="  RECORD / PLAYBACK  ", padding=10)
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

    def _build_log_panel(self, parent):
        logf = ttk.LabelFrame(parent, text="  LOG  ", padding=6)
        logf.pack(fill=tk.X, pady=4)
        self.log_widget = scrolledtext.ScrolledText(
            logf, height=8, state=tk.DISABLED, font=("monospace", 8),
            bg=Theme.LOG_BG, fg=Theme.LOG_FG, insertbackground=Theme.LOG_FG,
            relief=tk.FLAT, borderwidth=0, highlightthickness=0)
        self.log_widget.pack(fill=tk.X)
        # replay what happened before this panel existed
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, "".join(self.log_lines))
        self.log_widget.see(tk.END)
        self.log_widget.configure(state=tk.DISABLED)

    def _build_viz_panel(self, parent):
        sensor = self.sensor
        info = ttk.LabelFrame(parent, text="  SENSOR METADATA  ", padding=8)
        info.pack(fill=tk.X)
        self.info_var = tk.StringVar(
            value=f"{sensor.get('name', '')}  ·  {sensor.get('host', '')}\n"
                  "Not connected - use 'Pull from sensor' or 'Start Stream'.")
        ttk.Label(info, textvariable=self.info_var, style="Info.TLabel",
                  justify=tk.LEFT).pack(anchor=tk.W)

        viz = ttk.LabelFrame(parent, text="  2D FIELD IMAGES (DESTAGGERED)  ",
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

    # ------------------------------------------------- sensor record I/O ----
    def on_save_host(self):
        host = self.host_var.get().strip()
        if not host:
            messagebox.showerror("Save host",
                                 "Please enter the sensor hostname or IP.")
            return
        self.sensor["host"] = host
        self.store.save()
        self.log(f"Host saved: {host}")

    def _collect_config(self):
        """Read the form into a config dict, or None if a value is invalid."""
        values = {}
        for key, var in self.cfg_vars.items():
            values[key] = var.get()
        try:
            int(values["lidar_port"])
            int(values["imu_port"])
        except (ValueError, KeyError):
            messagebox.showerror("Invalid port", "Ports must be integers.")
            return None
        try:
            az_start = float(values["az_start"])
            az_end = float(values["az_end"])
            if not (0 <= az_start <= 360 and 0 <= az_end <= 360):
                raise ValueError
        except (ValueError, KeyError):
            messagebox.showerror("Invalid azimuth window",
                                 "Azimuth start/end must be numbers "
                                 "between 0 and 360.")
            return None
        return values

    def on_save_config(self, announce=False):
        values = self._collect_config()
        if values is None:
            return None
        self.sensor["config"].update(values)
        self.sensor["host"] = self.host_var.get().strip()
        self.store.save()
        if announce:
            self.log(f"Configuration saved to project for "
                     f"'{self.sensor.get('name')}' (sensor not contacted).")
        return values

    def _apply_config_to_form(self, values: dict):
        for key, value in values.items():
            var = self.cfg_vars.get(key)
            if var is None:
                continue
            try:
                var.set(value)
            except Exception:
                pass

    def on_pull_config(self):
        """Read the live configuration from the sensor into the project."""
        if not self._require_sdk():
            return
        host = self._host()
        if not host:
            messagebox.showerror("Pull from sensor",
                                 "Please enter the sensor hostname or IP.")
            return
        self.log(f"Reading configuration from {host} ...")

        def work():
            try:
                cfg = get_config(host)
            except Exception as e:
                self.log(f"ERROR reading config: {e}")
                return
            values = config_to_dict(cfg)
            self.log(f"Configuration read from {host}:\n{cfg}")
            self.root.after(0, lambda: self._finish_pull(values))

        threading.Thread(target=work, daemon=True).start()

    def _finish_pull(self, values):
        # keep valid combobox options only; anything unexpected is logged
        clean = {}
        allowed = {"lidar_mode": LIDAR_MODES,
                   "timestamp_mode": TIMESTAMP_MODES,
                   "operating_mode": OPERATING_MODES,
                   "signal_multiplier": SIGNAL_MULTIPLIERS,
                   "udp_profile": UDP_PROFILES}
        for key, value in values.items():
            options = allowed.get(key)
            if options is not None and value not in options:
                self.log(f"  note: sensor reports {key}={value}, which is "
                         "not in the list - keeping the stored value.")
                continue
            clean[key] = value
        self._apply_config_to_form(clean)
        self.sensor["config"].update(clean)
        self.sensor["last_seen"] = now_stamp()
        self.store.save()
        self.log("Configuration pulled into the project.")

    def on_push_config(self):
        """Write the project's configuration to the sensor."""
        if not self._require_sdk():
            return
        values = self.on_save_config()
        if values is None:
            return
        host = self._host()
        if not host:
            messagebox.showerror("Push to sensor",
                                 "Please enter the sensor hostname or IP.")
            return
        persist = bool(values["persist"])
        if not messagebox.askyesno(
                "Push configuration",
                f"Write this configuration to {host}?\n\n"
                f"  lidar mode      : {values['lidar_mode']}\n"
                f"  timestamp mode  : {values['timestamp_mode']}\n"
                f"  operating mode  : {values['operating_mode']}\n"
                f"  signal multiplier: {values['signal_multiplier']}\n"
                f"  azimuth window  : {values['az_start']} - "
                f"{values['az_end']} deg\n"
                f"  UDP profile     : {values['udp_profile']}\n"
                f"  UDP ports       : {values['lidar_port']} / "
                f"{values['imu_port']}\n"
                f"  persist         : {persist}\n\n"
                "The sensor will reinitialize and stop sending data for a "
                "few seconds."):
            return
        if self.reader is not None:
            self.on_stop_stream()

        def work():
            try:
                cfg = ouster_core.SensorConfig()
                cfg.lidar_mode = parse_lidar_mode(values["lidar_mode"])
                cfg.timestamp_mode = getattr(ouster_core.TimestampMode,
                                             values["timestamp_mode"])
                cfg.operating_mode = getattr(ouster_core.OperatingMode,
                                             values["operating_mode"])
                cfg.signal_multiplier = float(values["signal_multiplier"])
                # azimuth window in millidegrees
                cfg.azimuth_window = (int(float(values["az_start"]) * 1000),
                                      int(float(values["az_end"]) * 1000))
                if values["udp_profile"] != UNCHANGED:
                    cfg.udp_profile_lidar = getattr(
                        ouster_core.UDPProfileLidar, values["udp_profile"])
                cfg.udp_port_lidar = int(values["lidar_port"])
                cfg.udp_port_imu = int(values["imu_port"])
                set_config(host, cfg, persist=persist, udp_dest_auto=True)
                self.sensor["last_seen"] = now_stamp()
                self.store.save()
                self.log(
                    f"Configuration pushed to {host}: "
                    f"mode={values['lidar_mode']}, "
                    f"ts={values['timestamp_mode']}, "
                    f"op={values['operating_mode']}, "
                    f"signal_mult={values['signal_multiplier']}, "
                    f"azimuth=({values['az_start']},{values['az_end']})deg, "
                    f"profile={values['udp_profile']}, "
                    f"ports={values['lidar_port']}/{values['imu_port']}, "
                    f"persist={persist}")
            except Exception as e:
                self.log(f"ERROR pushing configuration: {e}")

        self.log("Pushing configuration (sensor will reinitialize)...")
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------- helpers --
    def log(self, msg: str):
        line = time.strftime("[%H:%M:%S] ") + msg + "\n"
        self.log_lines.append(line)

        def append():
            self.status_var.set(msg.splitlines()[0][:160])
            widget = self.log_widget
            if widget is None:
                return
            try:
                widget.configure(state=tk.NORMAL)
                widget.insert(tk.END, line)
                widget.see(tk.END)
                widget.configure(state=tk.DISABLED)
            except tk.TclError:      # panel was destroyed meanwhile
                self.log_widget = None
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
        if getattr(self, "host_var", None) is not None:
            return self.host_var.get().strip()
        return (self.sensor or {}).get("host", "")

    # ------------------------------------------------------------- actions --
    def on_get_info(self):
        """Open the sensor's built-in web dashboard in the default browser."""
        host = self._host()
        if not host:
            messagebox.showerror("Web dashboard",
                                 "Please enter the sensor hostname or IP.")
            return
        url = host if host.startswith(("http://", "https://")) \
            else f"http://{host}"
        self.log(f"Opening sensor web page: {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            self.log(f"ERROR opening browser: {e}")
            messagebox.showerror("Web dashboard",
                                 f"Could not open the browser:\n{e}")

    def _show_metadata(self, info):
        if self.info_var is None:
            return
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
            # remember what the sensor said about itself
            if self.sensor is not None:
                changed = False
                if str(info.prod_line) and not self.sensor.get("model"):
                    self.sensor["model"] = str(info.prod_line)
                    changed = True
                if str(info.sn) and not self.sensor.get("serial"):
                    self.sensor["serial"] = str(info.sn)
                    changed = True
                self.sensor["last_seen"] = now_stamp()
                self.store.save()
                if changed:
                    self.log("Model / serial filled in from the sensor.")
        except Exception:
            text = str(info)
        self.info_var.set(text)
        self.log("Sensor metadata received.")

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
        """View / change the sensor's IP configuration, stored per sensor."""
        if not self._require_sdk():
            return
        if SensorHttp is None:
            messagebox.showerror("Network",
                                 "This ouster-sdk version does not expose "
                                 "the sensor HTTP API.")
            return
        host = self._host()
        network = self.sensor["network"]

        win = tk.Toplevel(self.root)
        win.title(f"Network / IP  ·  {self.sensor.get('name')}  ·  {host}")
        win.geometry("580x560")
        win.configure(bg=Theme.BG)
        win.transient(self.root)

        cur = ttk.LabelFrame(win, text="  CURRENT NETWORK CONFIG (SENSOR)  ",
                             padding=8)
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
                    try:
                        cfg_text.configure(state=tk.NORMAL)
                        cfg_text.delete("1.0", tk.END)
                        cfg_text.insert(tk.END, txt)
                        cfg_text.configure(state=tk.DISABLED)
                    except tk.TclError:
                        pass                      # window already closed
                self.root.after(0, show)
            threading.Thread(target=work, daemon=True).start()

        refresh()

        setf = ttk.LabelFrame(win, text="  STATIC IP STORED IN THE PROJECT  ",
                              padding=8)
        setf.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(setf, text="IP / CIDR (e.g. 192.168.1.50/24):",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2,
                                             sticky=tk.W)
        ip_var = tk.StringVar(value=network.get("static_ip", ""))
        ttk.Entry(setf, textvariable=ip_var, width=30).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Label(setf, text="Gateway (optional):",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2,
                                             sticky=tk.W)
        gw_var = tk.StringVar(value=network.get("gateway", ""))
        ttk.Entry(setf, textvariable=gw_var, width=30).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=2)

        def save_to_project():
            network["static_ip"] = ip_var.get().strip()
            network["gateway"] = gw_var.get().strip()
            self.store.save()
            self.log(f"Network settings saved to project for "
                     f"'{self.sensor.get('name')}' (sensor not contacted).")

        def run_action(desc, fn, confirm):
            if not messagebox.askyesno("Change sensor IP", confirm,
                                       parent=win):
                return
            if self.reader is not None:
                self.on_stop_stream()

            def work():
                try:
                    fn()
                    self.sensor["last_seen"] = now_stamp()
                    self.store.save()
                    self.log(desc + " - done. The sensor is applying the new "
                             "network settings; reconnect with the new "
                             "address.")
                except Exception as e:
                    self.log(f"ERROR ({desc}): {e}")
                self.root.after(1500, refresh)
            self.log(desc + " ...")
            threading.Thread(target=work, daemon=True).start()

        def push_static():
            ip = ip_var.get().strip()
            gw = gw_var.get().strip()
            if not ip:
                messagebox.showerror("Push static IP",
                                     "Please enter an IP / CIDR.", parent=win)
                return
            save_to_project()
            run_action(
                f"Pushing static IP {ip}",
                (lambda: SensorHttp.create(host).set_static_ip(ip, gw)) if gw
                else (lambda: SensorHttp.create(host).set_static_ip(ip)),
                f"Set the sensor's static IP to:\n  {ip}"
                + (f"  (gateway {gw})" if gw else "")
                + "\n\nWARNING: you will lose the current connection and must "
                  "reconnect using the NEW address. Continue?")

        ttk.Button(setf, text="Save to project",
                   command=save_to_project).grid(row=4, column=0, sticky=tk.W,
                                                 pady=(8, 0))
        ttk.Button(setf, text="⤒  Push static IP to sensor",
                   style="Accent.TButton",
                   command=push_static).grid(row=4, column=1, sticky=tk.W,
                                             padx=6, pady=(8, 0))

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
        if self.sensor is not None:
            self.sensor["last_seen"] = now_stamp()
            self.store.save()
        win = tk.Toplevel(self.root)
        win.title(f"Sensor Status  ·  {host}")
        win.geometry("580x640")
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

    def on_start_stream(self, source_url=None, is_file=False):
        if not self._require_sdk():
            return
        if self.reader is not None:
            self.log("Stream already running.")
            return
        if not is_file:
            self.on_save_config()
        url = source_url or self._host()
        if not url:
            messagebox.showerror("Start stream",
                                 "Please enter the sensor hostname or IP.")
            return
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
        for name in ("start_btn", "stop_btn"):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.configure(state=tk.NORMAL if name == "start_btn"
                                 else tk.DISABLED)
            except tk.TclError:      # dashboard was torn down
                setattr(self, name, None)

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

    _IMU_JSON_SCHEMA = json.dumps({
        "type": "object",
        "properties": {
            "timestamp": {"type": "number"},
            "linear_acceleration": {"type": "object", "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "z": {"type": "number"}}},
            "angular_velocity": {"type": "object", "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "z": {"type": "number"}}}}}).encode()

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
            with open(out_path, "wb") as fh:
                writer = McapWriter(fh)
                writer.start()
                # point-cloud channel (protobuf foxglove.PointCloud)
                fds = build_file_descriptor_set(
                    PointCloud).SerializeToString()
                pc_schema = writer.register_schema(
                    name="foxglove.PointCloud", encoding="protobuf",
                    data=fds)
                pc_chan = writer.register_channel(
                    topic="/ouster/points", message_encoding="protobuf",
                    schema_id=pc_schema)

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
                        writer.add_message(
                            channel_id=pc_chan, log_time=ts,
                            data=msg.SerializeToString(),
                            publish_time=ts, sequence=n)
                        n += 1
                        if n % 50 == 0:
                            self.log(f"  ...{n} point-cloud frames written")
                try:
                    src.close()
                except Exception:
                    pass

                n_imu = self._export_imu(writer, in_path)
                writer.finish()

            note = (f"{n} point-cloud frames"
                    + (f" and {n_imu} IMU samples" if n_imu else ""))
            self.log(f"MCAP export complete: {note} -> {out_path}")
            self.root.after(0, lambda: messagebox.showinfo(
                "Export to MCAP",
                f"Done. Wrote {note} to:\n{out_path}\n\n"
                "Open it in Foxglove: add a 3D panel for /ouster/points"
                + (", and a Plot panel for /ouster/imu." if n_imu else ".")))
        except Exception as e:
            # bind the text now: `e` is out of scope by the time the
            # scheduled callback runs on the Tk thread
            err = str(e)
            self.log(f"ERROR exporting MCAP: {err}")
            self.root.after(0, lambda: messagebox.showerror(
                "Export to MCAP", f"Export failed:\n{err}"))

    def _export_imu(self, writer, in_path):
        """Append the sensor's IMU samples (accel + gyro) as JSON messages.

        IMU packets are only available from PCAP sources; for OSF we simply
        skip IMU and keep the point-cloud export.
        """
        if open_packet_source is None:
            return 0
        try:
            packets = open_packet_source(in_path)
        except Exception:
            self.log("No IMU packets in this source (skipping IMU).")
            return 0
        imu_schema = writer.register_schema(
            name="ouster.Imu", encoding="jsonschema",
            data=self._IMU_JSON_SCHEMA)
        imu_chan = writer.register_channel(
            topic="/ouster/imu", message_encoding="json",
            schema_id=imu_schema)
        n = 0
        try:
            for item in packets:
                pkt = item[1] if isinstance(item, (list, tuple)) else item
                if not isinstance(pkt, ouster_core.ImuPacket):
                    continue
                try:
                    acc = [float(v) for v in pkt.accel]
                    gyr = [float(v) for v in pkt.gyro]
                    ts = int(getattr(pkt, "sys_ts", 0)
                             or getattr(pkt, "timestamp", 0) or n * 10**7)
                except Exception:
                    continue
                m = {"timestamp": ts / 1e9,
                     "linear_acceleration": {"x": acc[0], "y": acc[1],
                                             "z": acc[2]},
                     "angular_velocity": {"x": gyr[0], "y": gyr[1],
                                          "z": gyr[2]}}
                writer.add_message(channel_id=imu_chan, log_time=ts,
                                   data=json.dumps(m).encode(),
                                   publish_time=ts, sequence=n)
                n += 1
        except Exception as e:
            self.log(f"IMU export stopped early: {e}")
        finally:
            try:
                packets.close()
            except Exception:
                pass
        if n:
            self.log(f"  ...{n} IMU samples written")
        return n

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
        win.geometry("860x660")
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
            sensor_name = (self.sensor or {}).get("name", "recording")
            safe = "".join(c if c.isalnum() or c in "-_" else "_"
                           for c in sensor_name)
            path = filedialog.asksaveasfilename(
                title="Save recording as",
                defaultextension=".pcap",
                initialfile=f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.pcap",
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
    def _style_axis(self, ax, title):
        ax.set_facecolor(Theme.BG)
        ax.set_title(title, fontsize=9, color=Theme.FG, loc="left")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(Theme.BORDER)

    def _build_axes(self):
        """(Re)create the subplots for the current view (all 4 or one)."""
        if self.fig is None:
            return
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
        if self.canvas is None:              # not on the dashboard screen
            return
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
        if self.sensor is not None and self.cfg_vars:
            try:
                self.on_save_config()
            except Exception:
                pass
        self.store.save()
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
