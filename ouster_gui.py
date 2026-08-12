#!/usr/bin/env python3
"""
Sensor Fleet Manager - Projects / Equipment / Sensors
=====================================================
A Tkinter application that organizes sensors into a simple three-level
hierarchy and lets you talk to each one:

    Project  ->  Equipment  ->  Sensor  ->  Sensor dashboard

  * Projects    - a site, a customer, a research program ...
  * Equipment   - the vehicle / mast / robot the sensors are mounted on
  * Sensors     - one entry per physical device, of one of four types:
                    - Ouster lidar, reached by hostname / IP (ouster-sdk)
                    - Camera: USB (0, /dev/video0) or network
                      (rtsp://..., http://.../video.mjpg), through OpenCV,
                      with the device's own IP / MTU / bitrate / GOP over
                      ONVIF and extra live views for other monitors
                    - Arbe imaging radar: a ROS 2 PointCloud2 topic or a
                      recorded cloud
                    - Inertial (IMU / INS): an ASCII serial unit, a ROS 2
                      sensor_msgs/Imu topic, or a recorded log

Everything is stored locally in ~/.ouster_projects.json, so the tree, the
per-sensor configuration and the per-sensor network settings survive
restarts and can be copied between machines.

From an Ouster sensor's dashboard you can:

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

From a camera's dashboard you can:

  * READ FROM THE CAMERA   - probe it, pull its current settings
    (resolution, FPS, FOURCC, brightness, contrast, saturation, gain,
    exposure), watch a live preview, take snapshots and replay video files.
  * WRITE TO THE CAMERA    - push the stored settings, on the running
    capture when a preview is open, and read back what the camera kept.
  * Record the live image to MP4 / AVI.

From a radar's or an inertial sensor's dashboard you can read its live
data (point cloud / acceleration, rate and orientation traces), record it,
replay recordings, and write settings back - ROS 2 parameters for the
radar, ROS parameters or serial commands for the inertial unit.

Tested with ouster-sdk 1.0.0 (also compatible with the older
`ouster.sdk.client` API < 1.0), OpenCV 4.x / 5.x and pyserial 3.5. Every
backend is optional; only the matching sensor type needs it.

Run:  python3 ouster_gui.py
"""

import copy
import json
import math
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

__version__ = "2.4.0"

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

# --- pyserial (optional: only needed for serial IMU / INS sensors) -----------
try:
    import serial as pyserial
    HAVE_SERIAL = True
    SERIAL_IMPORT_ERROR = None
except Exception as _e:
    pyserial = None
    HAVE_SERIAL = False
    SERIAL_IMPORT_ERROR = _e

# --- OpenCV (optional: only needed for the camera sensors) -------------------
try:
    import cv2
    HAVE_CV2 = True
    CV2_IMPORT_ERROR = None
except Exception as _e:
    cv2 = None
    HAVE_CV2 = False
    CV2_IMPORT_ERROR = _e

# --- ONVIF (optional: network-camera settings - IP, MTU, bitrate, GOP) -------
try:
    from onvif import ONVIFCamera as _OnvifClient
    HAVE_ONVIF = True
    ONVIF_IMPORT_ERROR = None
except Exception as _e:
    _OnvifClient = None
    HAVE_ONVIF = False
    ONVIF_IMPORT_ERROR = _e

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

# --- sensor kinds -----------------------------------------------------------
# Every sensor in the tree carries a "kind"; the dashboard is built from it.
KIND_OUSTER = "ouster"
KIND_CAMERA = "camera"
KIND_ARBE = "arbe"
KIND_IMU = "imu"
KIND_LABELS = {KIND_OUSTER: "Ouster lidar",
               KIND_CAMERA: "Camera",
               KIND_ARBE: "Arbe radar",
               KIND_IMU: "Inertial (IMU / INS)"}
KIND_KEYS = {label: key for key, label in KIND_LABELS.items()}
KIND_ADDRESS_LABELS = {
    KIND_OUSTER: "Hostname / IP",
    KIND_CAMERA: "Camera source",
    KIND_ARBE: "ROS 2 node / recording",
    KIND_IMU: "Serial port / recording",
}

CAMERA_BACKENDS = ["auto", "v4l2", "ffmpeg", "gstreamer", "dshow",
                   "avfoundation"]

# --- Arbe imaging radar -----------------------------------------------------
# Arbe's driver publishes its detections as a ROS 2 PointCloud2; the same
# points can also come from a recording, so the dashboard is usable without
# the radar attached.
ARBE_SOURCES = ["ROS 2 topic", "Recording file"]
ARBE_COLOR_BY = ["doppler", "snr", "power", "range", "height"]
ARBE_QOS = ["best_effort", "reliable"]
# PointCloud2 datatype enum -> numpy
PC2_DTYPES = {1: np.int8, 2: np.uint8, 3: np.int16, 4: np.uint16,
              5: np.int32, 6: np.uint32, 7: np.float32, 8: np.float64}
# field names an Arbe / generic radar cloud may use for the same quantity
RADAR_ALIASES = {
    "x": ("x", "X"),
    "y": ("y", "Y"),
    "z": ("z", "Z"),
    "doppler": ("doppler", "velocity", "radial_velocity", "vel", "v",
                "doppler_velocity", "range_rate"),
    "snr": ("snr", "SNR", "snr_db"),
    "power": ("power", "intensity", "rcs", "amplitude", "magnitude"),
    "range": ("range", "r", "distance"),
}
CAMERA_FOURCCS = [UNCHANGED, "MJPG", "YUYV", "H264", "GREY", "RGB3"]
# Network-camera settings live on the device, not in the local capture, and
# ONVIF is the standard that covers exactly these four.
ONVIF_ENCODINGS = [UNCHANGED, "H264", "H265", "JPEG"]
DHCP = "dhcp"
# (config key, OpenCV property name, label) - blank in the form = don't touch
CAMERA_PROPS = [
    ("width", "CAP_PROP_FRAME_WIDTH", "Width"),
    ("height", "CAP_PROP_FRAME_HEIGHT", "Height"),
    ("fps", "CAP_PROP_FPS", "FPS"),
    ("brightness", "CAP_PROP_BRIGHTNESS", "Brightness"),
    ("contrast", "CAP_PROP_CONTRAST", "Contrast"),
    ("saturation", "CAP_PROP_SATURATION", "Saturation"),
    ("gain", "CAP_PROP_GAIN", "Gain"),
    ("exposure", "CAP_PROP_EXPOSURE", "Exposure"),
]

# --- inertial sensors (IMU / INS) -------------------------------------------
# Serial units stream ASCII lines; the layout says which column is what, so
# one dashboard covers NMEA-style and plain CSV output alike.
IMU_SOURCES = ["Serial port", "ROS 2 topic", "Recording file"]
IMU_BAUDS = ["9600", "19200", "38400", "57600", "115200", "230400",
             "460800", "921600"]
IMU_LINE_ENDINGS = {"CRLF": "\r\n", "LF": "\n", "CR": "\r", "none": ""}
IMU_COLUMNS = ["t", "ax", "ay", "az", "gx", "gy", "gz",
               "mx", "my", "mz", "roll", "pitch", "yaw"]
# (group title, y label, the columns drawn on that axis)
IMU_PLOT_GROUPS = [
    ("Acceleration", "m/s²", ("ax", "ay", "az")),
    ("Angular rate", "deg/s", ("gx", "gy", "gz")),
    ("Orientation", "deg", ("roll", "pitch", "yaw")),
    ("Magnetometer", "µT", ("mx", "my", "mz")),
]
IMU_TRACE_COLORS = {"ax": "#ff6b6b", "ay": "#51cf66", "az": "#4dabf7",
                    "gx": "#ff6b6b", "gy": "#51cf66", "gz": "#4dabf7",
                    "roll": "#ff6b6b", "pitch": "#51cf66", "yaw": "#4dabf7",
                    "mx": "#ff6b6b", "my": "#51cf66", "mz": "#4dabf7"}

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
# per-camera settings; "" means "leave whatever the camera is using"
DEFAULT_CAMERA_CONFIG = {
    "backend": "auto",
    "fourcc": UNCHANGED,
    "width": "1280",
    "height": "720",
    "fps": "30",
    "brightness": "",
    "contrast": "",
    "saturation": "",
    "gain": "",
    "exposure": "",
    # device-side settings, applied over ONVIF (network cameras only)
    "onvif_port": "80",
    "onvif_user": "admin",
    "net_ip": "",            # "dhcp", or an address in CIDR form
    "net_gateway": "",
    "mtu": "",
    "bitrate": "",           # kbit/s
    "gop": "",               # frames between key frames
    "encoding": UNCHANGED,
}
# per-radar settings
DEFAULT_ARBE_CONFIG = {
    "source_type": ARBE_SOURCES[0],
    "topic": "/arbe/rviz/pointcloud",
    "domain_id": "0",
    "qos": ARBE_QOS[0],
    "node": "/arbe_driver",       # ROS 2 node that owns the parameters
    "color_by": "doppler",
    "max_range": "150",
    "min_snr": "",
    "parameters": "",             # "name: value" lines, pushed with ros2 param
}
# per-IMU settings
DEFAULT_IMU_CONFIG = {
    "source_type": IMU_SOURCES[0],
    "port": "/dev/ttyUSB0",
    "baud": "115200",
    "line_ending": "CRLF",
    "layout": "t,ax,ay,az,gx,gy,gz",
    "topic": "/imu/data",
    "domain_id": "0",
    "qos": "best_effort",
    "node": "/imu_driver",
    "commands": "",          # sent line by line on push
    "window": "600",         # samples kept on screen
}
DEFAULT_CONFIG_BY_KIND = {
    KIND_OUSTER: DEFAULT_SENSOR_CONFIG,
    KIND_CAMERA: DEFAULT_CAMERA_CONFIG,
    KIND_ARBE: DEFAULT_ARBE_CONFIG,
    KIND_IMU: DEFAULT_IMU_CONFIG,
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
        # worker threads save too (after a push / pull), so serialize writes
        self._save_lock = threading.Lock()
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
        with self._save_lock:
            tmp = f"{self.path}.{os.getpid()}.tmp"
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
        # records written before cameras existed are Ouster lidars
        kind = sensor.get("kind") or KIND_OUSTER
        if kind not in DEFAULT_CONFIG_BY_KIND:
            kind = KIND_OUSTER
        sensor["kind"] = kind
        cfg = sensor.get("config")
        if not isinstance(cfg, dict):
            cfg = {}
        defaults = DEFAULT_CONFIG_BY_KIND[kind]
        sensor["config"] = {**copy.deepcopy(defaults), **cfg}
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
            elif kind == "password":
                var = tk.StringVar(value=str(value))
                widget = ttk.Entry(body, textvariable=var, width=44, show="•")
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


def cv_source(source):
    """'0' -> device index 0; anything else is a URL or a file path."""
    text = str(source).strip()
    return int(text) if text.isdigit() else text


def fourcc_to_text(value) -> str:
    """Decode the packed integer CAP_PROP_FOURCC into e.g. 'MJPG'."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return ""
    if code <= 0:
        return ""
    text = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return text.strip().strip("\x00")


class OnvifCamera:
    """The ONVIF calls behind a network camera's IP / MTU / bitrate / GOP.

    Only the handful of operations this app needs, wrapped so the SOAP
    layer stays in one place: `factory` is the ONVIFCamera class, which
    tests replace with a stand-in.
    """

    def __init__(self, host, port=80, user="", password="", factory=None):
        if factory is None:
            if not HAVE_ONVIF:
                raise RuntimeError("the onvif-zeep package is not installed")
            factory = _OnvifClient
        # a source may be a URL; ONVIF talks to the host itself
        host = str(host).strip()
        for prefix in ("rtsp://", "http://", "https://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
                break
        host = host.split("@")[-1].split("/")[0].split(":")[0]
        self.host = host
        self.camera = factory(host, int(port or 80), user, password)
        self.device = self.camera.create_devicemgmt_service()
        self.media = self.camera.create_media_service()

    # -- video encoder --------------------------------------------------------
    def encoder_config(self):
        profiles = self.media.GetProfiles()
        if not profiles:
            raise RuntimeError("the camera reports no media profiles")
        config = getattr(profiles[0], "VideoEncoderConfiguration", None)
        if config is None:
            raise RuntimeError("the camera's first profile has no video "
                               "encoder configuration")
        return config

    @staticmethod
    def _gov_section(config):
        """The H264 / H265 / MPEG4 block that carries GovLength (the GOP)."""
        for key in ("H264", "H265", "MPEG4"):
            section = getattr(config, key, None)
            if section is not None:
                return section
        return None

    def network_interface(self):
        interfaces = self.device.GetNetworkInterfaces() or []
        for interface in interfaces:
            if getattr(interface, "Enabled", True):
                return interface
        return interfaces[0] if interfaces else None

    # -- read ------------------------------------------------------------------
    def read_settings(self) -> dict:
        out = {}
        config = self.encoder_config()
        encoding = getattr(config, "Encoding", None)
        if encoding:
            out["encoding"] = str(encoding)
        resolution = getattr(config, "Resolution", None)
        if resolution is not None:
            if getattr(resolution, "Width", None):
                out["width"] = str(int(resolution.Width))
            if getattr(resolution, "Height", None):
                out["height"] = str(int(resolution.Height))
        rate = getattr(config, "RateControl", None)
        if rate is not None:
            if getattr(rate, "BitrateLimit", None):
                out["bitrate"] = str(int(rate.BitrateLimit))
            if getattr(rate, "FrameRateLimit", None):
                out["fps"] = f"{float(rate.FrameRateLimit):g}"
        section = self._gov_section(config)
        if section is not None and getattr(section, "GovLength", None):
            out["gop"] = str(int(section.GovLength))

        interface = self.network_interface()
        if interface is not None:
            info = getattr(interface, "Info", None)
            if info is not None and getattr(info, "MTU", None):
                out["mtu"] = str(int(info.MTU))
            out.update(self._read_ipv4(interface))
        gateway = self._read_gateway()
        if gateway:
            out["net_gateway"] = gateway
        return out

    @staticmethod
    def _read_ipv4(interface) -> dict:
        ipv4 = getattr(interface, "IPv4", None)
        config = getattr(ipv4, "Config", None) if ipv4 is not None else None
        if config is None:
            return {}
        if getattr(config, "DHCP", False):
            return {"net_ip": DHCP}
        manual = getattr(config, "Manual", None) or []
        for entry in manual:
            address = getattr(entry, "Address", None)
            if not address:
                continue
            prefix = getattr(entry, "PrefixLength", None)
            return {"net_ip": f"{address}/{int(prefix)}" if prefix
                    else str(address)}
        return {}

    def _read_gateway(self) -> str:
        try:
            gateway = self.device.GetNetworkDefaultGateway()
        except Exception:
            return ""
        address = getattr(gateway, "IPv4Address", None)
        if isinstance(address, (list, tuple)):
            address = address[0] if address else None
        return str(address) if address else ""

    # -- write -----------------------------------------------------------------
    def write_settings(self, values: dict) -> list:
        """Apply the non-empty settings; returns a log line per operation."""
        report = []
        report.extend(self._write_encoder(values))
        report.extend(self._write_network(values))
        return report

    def _write_encoder(self, values) -> list:
        bitrate = str(values.get("bitrate", "")).strip()
        gop = str(values.get("gop", "")).strip()
        encoding = str(values.get("encoding", "")).strip()
        if not (bitrate or gop or (encoding and encoding != UNCHANGED)):
            return []
        config = self.encoder_config()
        changed = []
        if encoding and encoding != UNCHANGED:
            config.Encoding = encoding
            changed.append(f"encoding={encoding}")
        if bitrate:
            rate = getattr(config, "RateControl", None)
            if rate is None:
                return ["the camera exposes no rate control, bitrate skipped"]
            rate.BitrateLimit = int(float(bitrate))
            changed.append(f"bitrate={bitrate} kbit/s")
        if gop:
            section = self._gov_section(config)
            if section is None:
                return changed + ["the camera exposes no GOP setting, "
                                  "skipped"]
            section.GovLength = int(float(gop))
            changed.append(f"GOP={gop}")
        request = self.media.create_type("SetVideoEncoderConfiguration")
        request.Configuration = config
        request.ForcePersistence = True
        self.media.SetVideoEncoderConfiguration(request)
        return [f"encoder: {', '.join(changed)}"]

    def _write_network(self, values) -> list:
        ip = str(values.get("net_ip", "")).strip()
        mtu = str(values.get("mtu", "")).strip()
        gateway = str(values.get("net_gateway", "")).strip()
        report = []
        if ip or mtu:
            interface = self.network_interface()
            if interface is None:
                return ["the camera reports no network interface"]
            settings = {"Enabled": True}
            changed = []
            if mtu:
                settings["MTU"] = int(float(mtu))
                changed.append(f"MTU={mtu}")
            if ip:
                settings["IPv4"] = self._ipv4_request(ip)
                changed.append(f"IPv4={ip}")
            self.device.SetNetworkInterfaces({
                "InterfaceToken": getattr(interface, "token", None),
                "NetworkInterface": settings})
            report.append(f"network: {', '.join(changed)}")
        if gateway:
            self.device.SetNetworkDefaultGateway({"IPv4Address": [gateway]})
            report.append(f"gateway: {gateway}")
        return report

    @staticmethod
    def _ipv4_request(ip: str) -> dict:
        if ip.lower() == DHCP:
            return {"Enabled": True, "DHCP": True}
        address, _, prefix = ip.partition("/")
        return {"Enabled": True, "DHCP": False,
                "Manual": [{"Address": address.strip(),
                            "PrefixLength": int(prefix or 24)}]}


def valid_cidr(text: str) -> bool:
    """'192.168.1.64/24' or a bare IPv4 address."""
    address, _, prefix = str(text).partition("/")
    parts = address.strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255
                                  for p in parts):
        return False
    if prefix and not (prefix.isdigit() and 0 <= int(prefix) <= 32):
        return False
    return True


class CameraReader(threading.Thread):
    """Background thread that reads frames from a camera (USB device index,
    RTSP / HTTP URL) or a video file and pushes RGB images into a queue.

    The same thread owns the capture for its whole life, so settings
    changes, snapshots and recording are handled here through small
    requests instead of a second process touching the device.
    """

    def __init__(self, source, out_queue: queue.Queue, log_fn,
                 props=None, backend="auto", is_file: bool = False,
                 loop: bool = False):
        super().__init__(daemon=True)
        self.source = source
        self.out_queue = out_queue
        self.log = log_fn
        self.backend = backend
        self.is_file = is_file
        self.loop = loop
        self.info = {}
        self._props = dict(props or {})
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending_props = None
        self._snapshot_path = None
        self._record_request = None     # a path to start, False to stop
        self._recording = False

    # -- requests from the UI thread ----------------------------------------
    def stop(self):
        self._stop_event.set()

    def apply_props(self, props: dict):
        with self._lock:
            self._pending_props = dict(props)

    def snapshot(self, path: str):
        with self._lock:
            self._snapshot_path = path

    def start_recording(self, path: str):
        with self._lock:
            self._record_request = path

    def stop_recording(self):
        with self._lock:
            self._record_request = False

    @property
    def recording(self) -> bool:
        return self._recording

    # -- capture helpers -----------------------------------------------------
    _BACKEND_APIS = {"v4l2": "CAP_V4L2", "ffmpeg": "CAP_FFMPEG",
                     "gstreamer": "CAP_GSTREAMER", "dshow": "CAP_DSHOW",
                     "avfoundation": "CAP_AVFOUNDATION"}

    def open_capture(self):
        api = getattr(cv2, self._BACKEND_APIS.get(self.backend, "CAP_ANY"), 0)
        cap = cv2.VideoCapture(cv_source(self.source), api)
        if not cap.isOpened():
            raise RuntimeError(f"could not open camera source "
                               f"'{self.source}'")
        return cap

    @staticmethod
    def set_props(cap, props: dict) -> dict:
        """Apply the non-empty settings; return what the camera kept."""
        fourcc = str(props.get("fourcc", "")).strip()
        if fourcc and fourcc != UNCHANGED and len(fourcc) == 4:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        for key, prop, _label in CAMERA_PROPS:
            value = str(props.get(key, "")).strip()
            if not value:
                continue
            try:
                cap.set(getattr(cv2, prop), float(value))
            except (ValueError, AttributeError):
                continue
        return CameraReader.read_props(cap)

    @staticmethod
    def read_props(cap) -> dict:
        """Read the settings this app knows about back off the capture."""
        out = {}
        for key, prop, _label in CAMERA_PROPS:
            try:
                value = cap.get(getattr(cv2, prop))
            except AttributeError:
                continue
            if value is None:
                continue
            # OpenCV reports -1 for a property the backend does not expose;
            # exposure is excluded because -1 is a real value there
            if value == -1 and key not in ("exposure",):
                continue
            if key in ("width", "height"):
                out[key] = str(int(round(value)))
            else:
                out[key] = f"{value:g}"
        code = fourcc_to_text(cap.get(cv2.CAP_PROP_FOURCC))
        if code:
            out["fourcc"] = code
        return out

    def describe(self, cap) -> dict:
        width = int(round(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0))
        height = int(round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        return {"source": str(self.source), "backend": self.backend,
                "width": width, "height": height, "fps": float(fps),
                "fourcc": fourcc_to_text(cap.get(cv2.CAP_PROP_FOURCC)),
                "frames": int(frames) if self.is_file else 0}

    # -- thread body ---------------------------------------------------------
    def run(self):
        try:
            first = True
            while not self._stop_event.is_set():
                self._play_once(first)
                first = False
                if not (self.is_file and self.loop):
                    break
                if self._stop_event.is_set():
                    break
                self.log("Looping video...")
            self.log("Camera stopped.")
        except Exception as e:
            self.out_queue.put(("error", str(e)))
        finally:
            self.out_queue.put(("stopped", None))

    def _play_once(self, announce=True):
        cap = None
        writer = None
        try:
            if announce:
                self.log(f"Opening camera: {self.source} ...")
            cap = self.open_capture()
            self.set_props(cap, self._props)
            self.info = self.describe(cap)
            self.out_queue.put(("camera_info", self.info))
            if announce:
                self.log(f"Camera opened: {self.info['width']}x"
                         f"{self.info['height']} @ {self.info['fps']:g} fps"
                         + (f" [{self.info['fourcc']}]"
                            if self.info["fourcc"] else ""))
            # video files are paced by their own frame rate
            delay = (1.0 / self.info["fps"]
                     if self.is_file and self.info["fps"] > 0 else 0.0)

            n = 0
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                n += 1
                writer = self._service_requests(cap, frame, writer)
                self._publish(frame, n)
                if delay:
                    time.sleep(delay)
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
                self._recording = False
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def _service_requests(self, cap, frame, writer):
        with self._lock:
            props, self._pending_props = self._pending_props, None
            snap, self._snapshot_path = self._snapshot_path, None
            record, self._record_request = self._record_request, None

        if props is not None:
            kept = self.set_props(cap, props)
            self.info = self.describe(cap)
            self.out_queue.put(("camera_info", self.info))
            self.out_queue.put(("camera_props", kept))

        if snap:
            try:
                cv2.imwrite(snap, frame)
                self.log(f"Snapshot saved -> {snap}")
            except Exception as e:
                self.log(f"ERROR saving snapshot: {e}")

        if record is False and writer is not None:
            writer.release()
            writer = None
            self._recording = False
            self.log("Recording stopped.")
        elif isinstance(record, str) and writer is None:
            try:
                height, width = frame.shape[:2]
                fps = self.info.get("fps") or 0.0
                if fps <= 0:
                    fps = 25.0
                code = "mp4v" if record.lower().endswith(".mp4") else "MJPG"
                writer = cv2.VideoWriter(record,
                                         cv2.VideoWriter_fourcc(*code),
                                         fps, (width, height))
                if not writer.isOpened():
                    raise RuntimeError("the video writer could not be opened "
                                       "(try a .avi file name)")
                self._recording = True
                self.log(f"Recording started -> {record} "
                         f"({width}x{height} @ {fps:g} fps, {code})")
            except Exception as e:
                writer = None
                self._recording = False
                self.log(f"ERROR starting recording: {e}")

        if writer is not None:
            try:
                writer.write(frame)
            except Exception as e:
                self.log(f"ERROR writing video frame: {e}")
        return writer

    def _publish(self, frame, index):
        rgb = np.ascontiguousarray(frame[:, :, ::-1])   # BGR -> RGB
        # keep only the freshest frame, but never drop info/error events
        pending = []
        try:
            while True:
                old = self.out_queue.get_nowait()
                if old[0] != "camera":
                    pending.append(old)
        except queue.Empty:
            pass
        for ev in pending:
            self.out_queue.put(ev)
        self.out_queue.put(("camera", rgb, index, self._recording))


def parse_pointcloud2(msg) -> dict:
    """Decode a ROS PointCloud2 into {field name: 1-D array}.

    Written against the message layout rather than the ROS Python packages,
    so it works with rclpy messages, rosbag readers and plain stand-ins.
    """
    dtype = []
    for field in msg.fields:
        np_type = PC2_DTYPES.get(int(field.datatype))
        if np_type is None:
            raise ValueError(f"unsupported PointCloud2 datatype "
                             f"{field.datatype} on field '{field.name}'")
        count = int(getattr(field, "count", 1) or 1)
        dtype.append((field.name, np_type, (count,) if count > 1 else ()))
    point_step = int(msg.point_step)
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    n_points = len(raw) // point_step
    raw = raw[:n_points * point_step].reshape(n_points, point_step)

    out = {}
    for (name, np_type, shape), field in zip(dtype, msg.fields):
        width = np.dtype(np_type).itemsize * (shape[0] if shape else 1)
        offset = int(field.offset)
        chunk = raw[:, offset:offset + width].copy()
        values = chunk.view(np_type)
        out[name] = values.reshape(n_points, -1)[:, 0] if shape else \
            values.reshape(n_points)
    if getattr(msg, "is_bigendian", False):
        out = {k: v.byteswap().view(v.dtype.newbyteorder())
               for k, v in out.items()}
    return out


def normalize_radar_fields(fields: dict) -> dict:
    """Map a cloud's own field names onto x/y/z/doppler/snr/power/range."""
    lower = {str(k).lower(): np.asarray(v).reshape(-1)
             for k, v in fields.items()}
    out = {}
    for key, aliases in RADAR_ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower:
                out[key] = lower[alias.lower()].astype(np.float64)
                break
    if not {"x", "y"} <= set(out):
        raise ValueError("the point cloud has no x / y fields "
                         f"(found: {', '.join(sorted(lower)) or 'nothing'})")
    if "z" not in out:
        out["z"] = np.zeros_like(out["x"])
    if "range" not in out:
        out["range"] = np.sqrt(out["x"] ** 2 + out["y"] ** 2 + out["z"] ** 2)
    return out


def radar_stats(points: dict) -> dict:
    """Small summary of one radar frame, shown in the info panel."""
    n = len(points.get("x", ()))
    stats = {"points": n}
    if not n:
        return stats
    stats["max_range"] = float(np.max(points["range"]))
    for key in ("doppler", "snr", "power"):
        if key in points and len(points[key]):
            stats[f"{key}_min"] = float(np.min(points[key]))
            stats[f"{key}_max"] = float(np.max(points[key]))
    return stats


def load_radar_recording(path: str) -> list:
    """Read a recorded radar cloud into a list of per-frame dicts.

    Supports .npz / .npy (structured), .csv / .txt (header row) and ascii
    .pcd - the formats radar clouds are usually dumped to.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".npz":
        with np.load(path) as data:
            fields = {k: data[k] for k in data.files}
    elif ext == ".npy":
        array = np.load(path)
        if array.dtype.names is None:
            raise ValueError(".npy must hold a structured array with named "
                             "columns (x, y, z, doppler ...)")
        fields = {name: array[name] for name in array.dtype.names}
    elif ext in (".csv", ".txt"):
        array = np.genfromtxt(path, delimiter=",", names=True)
        if array.dtype.names is None:
            raise ValueError("the CSV needs a header row naming the columns")
        fields = {name: array[name] for name in array.dtype.names}
    elif ext == ".pcd":
        fields = _load_ascii_pcd(path)
    else:
        raise ValueError(f"unsupported recording format '{ext}' "
                         "(use .npz, .npy, .csv or .pcd)")

    frame_ids = None
    for key in list(fields):
        if str(key).lower() in ("frame", "frame_id", "frame_index"):
            frame_ids = np.asarray(fields.pop(key)).reshape(-1)
            break
    points = normalize_radar_fields(fields)
    if frame_ids is None or len(np.unique(frame_ids)) <= 1:
        return [points]
    frames = []
    for value in np.unique(frame_ids):
        mask = frame_ids == value
        frames.append({k: v[mask] for k, v in points.items()})
    return frames


def _load_ascii_pcd(path: str) -> dict:
    """Minimal ascii PCD reader (enough for radar detection dumps)."""
    names, rows = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        in_data = False
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if in_data:
                rows.append([float(v) for v in line.split()])
                continue
            key, _, rest = line.partition(" ")
            key = key.upper()
            if key == "FIELDS":
                names = rest.split()
            elif key == "DATA":
                if rest.strip() != "ascii":
                    raise ValueError("only ascii .pcd files are supported")
                in_data = True
    if not names or not rows:
        raise ValueError("the .pcd file has no ascii point data")
    table = np.asarray(rows, dtype=np.float64)
    return {name: table[:, i] for i, name in enumerate(names)
            if i < table.shape[1]}


class ArbeReader(threading.Thread):
    """Background thread that feeds radar frames into a queue.

    Two sources, both producing the same {x, y, z, doppler, snr, ...}
    dict per frame:

      * "ROS 2 topic"    - subscribes to the driver's PointCloud2 with
                           rclpy (imported here, so ROS is only needed
                           when this source is actually used)
      * "Recording file" - replays a .npz / .npy / .csv / .pcd dump
    """

    def __init__(self, source: str, out_queue: queue.Queue, log_fn,
                 is_file: bool = False, loop: bool = False,
                 domain_id: str = "", qos: str = "best_effort",
                 rate: float = 10.0):
        super().__init__(daemon=True)
        self.source = source
        self.out_queue = out_queue
        self.log = log_fn
        self.is_file = is_file
        self.loop = loop
        self.domain_id = str(domain_id or "").strip()
        self.qos = qos
        self.rate = rate
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            if self.is_file:
                self._play_file()
            else:
                self._play_ros2()
            self.log("Radar stream ended.")
        except Exception as e:
            self.out_queue.put(("error", str(e)))
        finally:
            self.out_queue.put(("stopped", None))

    def _publish(self, points, index):
        pending = []
        try:
            while True:
                old = self.out_queue.get_nowait()
                if old[0] != "radar":
                    pending.append(old)
        except queue.Empty:
            pass
        for ev in pending:
            self.out_queue.put(ev)
        self.out_queue.put(("radar", points, index, radar_stats(points)))

    def _play_file(self):
        self.log(f"Loading radar recording: {self.source} ...")
        frames = load_radar_recording(self.source)
        self.log(f"Loaded {len(frames)} frame(s), "
                 f"{sum(len(f['x']) for f in frames)} points total.")
        delay = 1.0 / self.rate if self.rate > 0 else 0.1
        index = 0
        while not self._stop_event.is_set():
            for points in frames:
                if self._stop_event.is_set():
                    break
                index += 1
                self._publish(points, index)
                time.sleep(delay)
            if not self.loop:
                break
            self.log("Looping recording...")

    def _play_ros2(self):
        if self.domain_id:
            os.environ["ROS_DOMAIN_ID"] = self.domain_id
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import (QoSProfile, QoSReliabilityPolicy,
                                   QoSHistoryPolicy)
            from sensor_msgs.msg import PointCloud2
        except Exception as e:
            raise RuntimeError(
                "ROS 2 (rclpy + sensor_msgs) is not importable - source a "
                f"ROS 2 environment before starting this app ({e})")

        self.log(f"Subscribing to {self.source} "
                 f"(ROS_DOMAIN_ID={self.domain_id or 'default'}, "
                 f"{self.qos}) ...")
        rclpy.init(args=None)
        node = Node("sensor_fleet_manager_arbe")
        profile = QoSProfile(
            depth=5, history=QoSHistoryPolicy.KEEP_LAST,
            reliability=(QoSReliabilityPolicy.BEST_EFFORT
                         if self.qos == "best_effort"
                         else QoSReliabilityPolicy.RELIABLE))
        counter = {"n": 0}

        def on_msg(msg):
            try:
                points = normalize_radar_fields(parse_pointcloud2(msg))
            except Exception as e:
                self.log(f"ERROR decoding PointCloud2: {e}")
                return
            counter["n"] += 1
            self._publish(points, counter["n"])

        node.create_subscription(PointCloud2, self.source, on_msg, profile)
        try:
            while not self._stop_event.is_set():
                rclpy.spin_once(node, timeout_sec=0.1)
        finally:
            node.destroy_node()
            try:
                rclpy.shutdown()
            except Exception:
                pass


def split_imu_line(line: str) -> list:
    """Tokenize one ASCII line from an inertial unit.

    Handles plain CSV, whitespace-separated output and NMEA-style
    sentences ("$VNYMR,+000.1,...*6A"): the talker word and the checksum
    are dropped, so only the payload columns are left.
    """
    text = str(line).strip()
    if not text:
        return []
    if text.startswith(("$", "#", "!")):
        text = text[1:]
        star = text.rfind("*")
        if star > 0:
            text = text[:star]
    text = text.replace(";", ",").replace("\t", ",")
    parts = [p for chunk in text.split(",") for p in chunk.split()]
    if parts and not _is_number(parts[0]):
        parts = parts[1:]           # a leading sentence / talker id
    return parts


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def parse_imu_line(line: str, layout) -> dict:
    """Map one line's columns onto named IMU fields, or {} if it isn't data.

    `layout` is the per-device column list, e.g. "t,ax,ay,az,gx,gy,gz";
    a "-" column is skipped.
    """
    if isinstance(layout, str):
        layout = [c.strip() for c in layout.split(",")]
    parts = split_imu_line(line)
    if not parts:
        return {}
    sample = {}
    for name, text in zip(layout, parts):
        if not name or name == "-":
            continue
        if not _is_number(text):
            return {}               # a status line, not a data line
        sample[name] = float(text)
    return sample if sample else {}


def suggest_imu_layout(lines) -> str:
    """Guess a column layout from raw lines, for the 'Pull' button."""
    columns = [split_imu_line(ln) for ln in lines]
    counts = [len(parts) for parts in columns
              if parts and all(_is_number(p) for p in parts)]
    if not counts:
        return ""
    width = max(set(counts), key=counts.count)
    guess = ["t", "ax", "ay", "az", "gx", "gy", "gz",
             "mx", "my", "mz", "roll", "pitch", "yaw"]
    if width <= 6:                  # no timestamp column
        guess = guess[1:]
    return ",".join((guess[i] if i < len(guess) else "-")
                    for i in range(width))


def quaternion_to_euler(x, y, z, w) -> tuple:
    """(roll, pitch, yaw) in degrees from a ROS orientation quaternion."""
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.asin(sinp)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return (math.degrees(roll), math.degrees(pitch), math.degrees(yaw))


def imu_sample_from_ros(msg) -> dict:
    """sensor_msgs/Imu -> the same flat dict the serial parser produces."""
    sample = {}
    accel = getattr(msg, "linear_acceleration", None)
    if accel is not None:
        sample.update(ax=float(accel.x), ay=float(accel.y),
                      az=float(accel.z))
    gyro = getattr(msg, "angular_velocity", None)
    if gyro is not None:            # ROS publishes rad/s, the plot is deg/s
        sample.update(gx=math.degrees(float(gyro.x)),
                      gy=math.degrees(float(gyro.y)),
                      gz=math.degrees(float(gyro.z)))
    q = getattr(msg, "orientation", None)
    if q is not None and any((q.x, q.y, q.z, q.w)):
        roll, pitch, yaw = quaternion_to_euler(float(q.x), float(q.y),
                                               float(q.z), float(q.w))
        sample.update(roll=roll, pitch=pitch, yaw=yaw)
    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    if stamp is not None:
        sample["t"] = (float(getattr(stamp, "sec", 0))
                       + float(getattr(stamp, "nanosec", 0)) / 1e9)
    return sample


def load_imu_recording(path: str) -> list:
    """Read a recorded IMU log into a list of sample dicts."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".npz":
        with np.load(path) as data:
            columns = {k: np.asarray(data[k]).reshape(-1) for k in data.files}
    elif ext in (".csv", ".txt", ".log"):
        array = np.genfromtxt(path, delimiter=",", names=True)
        if array.dtype.names is None:
            raise ValueError("the CSV needs a header row naming the columns "
                             "(t,ax,ay,az,gx,gy,gz ...)")
        columns = {name: np.atleast_1d(array[name])
                   for name in array.dtype.names}
    else:
        raise ValueError(f"unsupported recording format '{ext}' "
                         "(use .csv or .npz)")
    keep = {k: v for k, v in columns.items() if k in IMU_COLUMNS}
    if not keep:
        raise ValueError("no IMU columns found - expected some of "
                         + ", ".join(IMU_COLUMNS))
    length = min(len(v) for v in keep.values())
    return [{k: float(v[i]) for k, v in keep.items()} for i in range(length)]


class ImuReader(threading.Thread):
    """Background thread that feeds inertial samples into a queue.

    Three sources, all producing the same flat sample dict:

      * "Serial port"    - ASCII lines through pyserial, split by the
                           device's column layout
      * "ROS 2 topic"    - sensor_msgs/Imu through rclpy
      * "Recording file" - a .csv / .npz log, replayed at its own rate
    """

    BATCH_SECONDS = 0.05        # publish at most 20 batches per second

    def __init__(self, config: dict, out_queue: queue.Queue, log_fn,
                 loop: bool = False):
        super().__init__(daemon=True)
        self.config = dict(config)
        self.out_queue = out_queue
        self.log = log_fn
        self.loop = loop
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._record_request = None     # a path to start, False to stop
        self._recording = False
        self._batch = []
        self._last_flush = 0.0
        self.count = 0

    def stop(self):
        self._stop_event.set()

    def start_recording(self, path: str):
        with self._lock:
            self._record_request = path

    def stop_recording(self):
        with self._lock:
            self._record_request = False

    @property
    def recording(self) -> bool:
        return self._recording

    # -- publishing ----------------------------------------------------------
    def _emit(self, sample: dict, force=False):
        if sample:
            self.count += 1
            self._batch.append(sample)
        now = time.monotonic()
        if not self._batch:
            return
        if not force and now - self._last_flush < self.BATCH_SECONDS:
            return
        batch, self._batch = self._batch, []
        self._last_flush = now
        pending = []
        try:
            while True:
                old = self.out_queue.get_nowait()
                if old[0] != "imu":
                    pending.append(old)
                else:
                    batch = old[1] + batch      # keep dropped samples
        except queue.Empty:
            pass
        for ev in pending:
            self.out_queue.put(ev)
        self.out_queue.put(("imu", batch, self.count))

    def _service_recording(self, sample, writer):
        with self._lock:
            request, self._record_request = self._record_request, None
        if request is False and writer is not None:
            writer[0].close()
            self._recording = False
            self.log("IMU recording stopped.")
            writer = None
        elif isinstance(request, str) and writer is None:
            try:
                columns = [c for c in IMU_COLUMNS if c in sample]
                handle = open(request, "w", encoding="utf-8")
                handle.write(",".join(columns) + "\n")
                writer = (handle, columns)
                self._recording = True
                self.log(f"IMU recording started -> {request} "
                         f"({', '.join(columns)})")
            except Exception as e:
                writer = None
                self._recording = False
                self.log(f"ERROR starting IMU recording: {e}")
        if writer is not None:
            handle, columns = writer
            handle.write(",".join(f"{sample.get(c, float('nan')):.6g}"
                                  for c in columns) + "\n")
        return writer

    # -- thread body ---------------------------------------------------------
    def run(self):
        writer = None
        try:
            source = self.config.get("source_type", IMU_SOURCES[0])
            if source == "Serial port":
                writer = self._play_serial()
            elif source == "ROS 2 topic":
                writer = self._play_ros2()
            else:
                writer = self._play_file()
            self.log("IMU stream ended.")
        except Exception as e:
            self.out_queue.put(("error", str(e)))
        finally:
            self._emit({}, force=True)
            if isinstance(writer, tuple):
                try:
                    writer[0].close()
                except Exception:
                    pass
            self._recording = False
            self.out_queue.put(("stopped", None))

    def _play_serial(self):
        if not HAVE_SERIAL:
            raise RuntimeError("pyserial is not installed - "
                               "pip install pyserial")
        port = self.config.get("port", "")
        baud = int(self.config.get("baud") or 115200)
        layout = self.config.get("layout", "")
        self.log(f"Opening {port} at {baud} baud ...")
        writer = None
        skipped = 0
        with pyserial.Serial(port, baud, timeout=0.2) as link:
            self.out_queue.put(("imu_info", {"source": port, "baud": baud,
                                             "layout": layout}))
            self.log("Serial port open, reading ...")
            while not self._stop_event.is_set():
                try:
                    raw = link.readline().decode("ascii", "replace")
                except Exception as e:
                    self.log(f"ERROR reading serial: {e}")
                    break
                if not raw.strip():
                    self._emit({})           # flush whatever is pending
                    continue
                sample = parse_imu_line(raw, layout)
                if not sample:
                    skipped += 1
                    if skipped in (1, 50, 500):
                        self.log(f"Skipping non-data line: {raw.strip()[:80]}")
                    continue
                writer = self._service_recording(sample, writer)
                self._emit(sample)
        return writer

    def _play_file(self):
        path = self.config.get("port", "")
        self.log(f"Loading IMU recording: {path} ...")
        samples = load_imu_recording(path)
        self.log(f"Loaded {len(samples)} sample(s).")
        writer = None
        # replay at the log's own rate when it carries timestamps
        stamps = [s["t"] for s in samples if "t" in s]
        step = 0.01
        if len(stamps) > 1:
            span = stamps[-1] - stamps[0]
            if span > 0:
                step = min(0.1, span / (len(stamps) - 1))
        while not self._stop_event.is_set():
            for sample in samples:
                if self._stop_event.is_set():
                    break
                writer = self._service_recording(sample, writer)
                self._emit(sample)
                time.sleep(step)
            if not self.loop:
                break
            self.log("Looping recording...")
        return writer

    def _play_ros2(self):
        domain = str(self.config.get("domain_id", "")).strip()
        if domain:
            os.environ["ROS_DOMAIN_ID"] = domain
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import (QoSProfile, QoSReliabilityPolicy,
                                   QoSHistoryPolicy)
            from sensor_msgs.msg import Imu
        except Exception as e:
            raise RuntimeError(
                "ROS 2 (rclpy + sensor_msgs) is not importable - source a "
                f"ROS 2 environment before starting this app ({e})")

        topic = self.config.get("topic", "")
        self.log(f"Subscribing to {topic} ...")
        rclpy.init(args=None)
        node = Node("sensor_fleet_manager_imu")
        profile = QoSProfile(
            depth=50, history=QoSHistoryPolicy.KEEP_LAST,
            reliability=(QoSReliabilityPolicy.BEST_EFFORT
                         if self.config.get("qos") == "best_effort"
                         else QoSReliabilityPolicy.RELIABLE))
        state = {"writer": None}

        def on_msg(msg):
            sample = imu_sample_from_ros(msg)
            if not sample:
                return
            state["writer"] = self._service_recording(sample,
                                                      state["writer"])
            self._emit(sample)

        node.create_subscription(Imu, topic, on_msg, profile)
        try:
            while not self._stop_event.is_set():
                rclpy.spin_once(node, timeout_sec=0.1)
        finally:
            node.destroy_node()
            try:
                rclpy.shutdown()
            except Exception:
                pass
        return state["writer"]


class CameraViewWindow:
    """A detached live view of the camera, for a second monitor.

    It draws from the same capture thread as the dashboard - opening one
    does not touch the device again.
    """

    def __init__(self, app, title: str):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title(title)
        self.win.geometry("960x600")
        self.win.configure(bg=Theme.BG)
        self.fig = Figure(figsize=(8, 5), dpi=90, tight_layout=True,
                          facecolor=Theme.BG)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.win)
        widget = self.canvas.get_tk_widget()
        widget.configure(bg=Theme.BG, highlightthickness=0)
        widget.pack(fill=tk.BOTH, expand=True)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.artist = None
        self._fullscreen = False

        bar = ttk.Frame(self.win, style="Panel.TFrame")
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status = tk.StringVar(value="waiting for frames...")
        ttk.Label(bar, textvariable=self.status,
                  style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(bar, text="F11 / double-click: full screen  ·  Esc: exit",
                  style="Status.TLabel").pack(side=tk.RIGHT)

        for sequence in ("<F11>", "<Double-Button-1>"):
            self.win.bind(sequence, lambda e: self.toggle_fullscreen())
        self.win.bind("<Escape>", lambda e: self.set_fullscreen(False))
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def toggle_fullscreen(self):
        self.set_fullscreen(not self._fullscreen)

    def set_fullscreen(self, value: bool):
        self._fullscreen = bool(value)
        try:
            self.win.attributes("-fullscreen", self._fullscreen)
        except tk.TclError:
            pass

    def show(self, rgb, caption: str):
        try:
            if self.artist is None or \
                    self.artist.get_array().shape[:2] != rgb.shape[:2]:
                self.ax.clear()
                self.ax.set_axis_off()
                self.artist = self.ax.imshow(rgb)
            else:
                self.artist.set_data(rgb)
            self.status.set(caption)
            self.canvas.draw_idle()
        except tk.TclError:
            self.close()

    def close(self):
        if self.win is None:
            return
        window, self.win = self.win, None
        try:
            self.app.view_windows.remove(self)
        except (ValueError, AttributeError):
            pass
        try:
            window.destroy()
        except tk.TclError:
            pass


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
        root.title(f"Sensor Fleet Manager  v{__version__}  ·  "
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
        self._log_pending = deque()      # written by worker threads
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
        self.cam_ax = None
        self.cam_artist = None
        self.radar_ax = None
        self.radar_scatter = None
        self.radar_colorbar = None
        self.last_radar = None
        self.last_radar_index = 0
        self.param_text = None
        self.imu_axes = None
        self.imu_lines = {}
        self.imu_buffer = {}
        self.imu_shown = ()
        self.imu_rate = None
        self.view_windows = []          # detached camera views
        self.last_camera_frame = None
        self._onvif_passwords = {}      # sensor id -> password, this session

        self._build_shell()
        self._poll_queue()

        self.log(f"Sensor Fleet Manager v{__version__} ready.")
        self.log(f"Project database: {self.store.path}")
        if not HAVE_OUSTER:
            self.log("NOTE: ouster-sdk is not installed "
                     f"({OUSTER_IMPORT_ERROR}); Ouster lidar sensors are "
                     "read-only. Install it with:  pip install ouster-sdk")
        if not HAVE_CV2:
            self.log("NOTE: OpenCV is not installed "
                     f"({CV2_IMPORT_ERROR}); camera sensors are read-only. "
                     "Install it with:  pip install opencv-python")
        if not HAVE_ONVIF:
            self.log("NOTE: onvif-zeep is not installed "
                     f"({ONVIF_IMPORT_ERROR}); network-camera settings "
                     "(IP / MTU / bitrate / GOP) are read-only. Install it "
                     "with:  pip install onvif-zeep")
        if not HAVE_SERIAL:
            self.log("NOTE: pyserial is not installed "
                     f"({SERIAL_IMPORT_ERROR}); serial inertial sensors are "
                     "read-only. Install it with:  pip install pyserial")

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
        self.cam_ax = None
        self.cam_artist = None
        self.radar_ax = None
        self.radar_scatter = None
        self.radar_colorbar = None
        self.last_radar = None
        self.param_text = None
        self.imu_axes = None
        self.imu_lines = {}
        self.imu_buffer = {}
        self.imu_shown = ()
        self._close_view_windows()
        self.last_camera_frame = None
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
            "The platform the sensors are mounted on: a vehicle, a mast, "
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
            if sensor["kind"] == KIND_CAMERA:
                summary = "x".join(v for v in (cfg.get("width"),
                                               cfg.get("height")) if v)
                if cfg.get("fps"):
                    summary += f" @ {cfg['fps']} fps"
            elif sensor["kind"] == KIND_IMU:
                source = cfg.get("source_type", "")
                summary = (f"{cfg.get('baud', '')} baud"
                           if source == "Serial port" else
                           cfg.get("topic", "") if source == "ROS 2 topic"
                           else "recording")
            elif sensor["kind"] == KIND_ARBE:
                summary = (cfg.get("topic", "")
                           if cfg.get("source_type") == ARBE_SOURCES[0]
                           else "recording")
            else:
                summary = cfg.get("lidar_mode", "")
            rows.append((sensor, {
                "name": sensor.get("name", ""),
                "kind": KIND_LABELS.get(sensor["kind"], sensor["kind"]),
                "host": sensor.get("host", ""),
                "model": sensor.get("model", ""),
                "mode": summary,
                "last_seen": sensor.get("last_seen") or "never",
            }))

        self._build_list_screen(
            f"Sensors · {equipment.get('name', '')}",
            "Lidars, cameras, radars and inertial units. Open one to read "
            "data from it or push settings to it.",
            [("name", "Sensor", 190), ("kind", "Type", 145),
             ("host", "Address / source", 220),
             ("model", "Model", 130), ("mode", "Saved settings", 140),
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
        {"key": "kind_label", "label": "Sensor type", "kind": "combo",
         "values": list(KIND_LABELS.values()),
         "default": KIND_LABELS[KIND_OUSTER],
         "hint": "the type decides which dashboard the sensor opens"},
        {"key": "name", "label": "Sensor name", "required": True,
         "hint": "e.g. 'front-left OS1' or 'cabin camera'"},
        {"key": "host", "label": "Address / source", "required": True,
         "hint": "Ouster: os-122xxxxxxxxxx.local or 192.168.1.50  ·  "
                 "camera: 0 for the first USB camera, or an RTSP / HTTP URL"
                 "  ·  Arbe radar: the path of a recording (the ROS 2 topic "
                 "is set on the dashboard)"},
        {"key": "model", "label": "Model / product line",
         "hint": "optional, e.g. OS1-128 (filled in automatically after "
                 "the first connection to an Ouster sensor)"},
        {"key": "notes", "label": "Notes", "kind": "text"},
    ]

    @staticmethod
    def _split_kind(values: dict) -> tuple:
        """Pull the sensor-type combobox out of a form result."""
        label = values.pop("kind_label", "")
        return KIND_KEYS.get(label, KIND_OUSTER), values

    def new_sensor(self):
        dialog = FormDialog(self.root, "New sensor", self._SENSOR_FIELDS,
                            ok_text="Create")
        if not dialog.result:
            return
        kind, values = self._split_kind(dialog.result)
        values["kind"] = kind
        sensor = self.store.add_sensor(self.equipment, values)
        self.log(f"{KIND_LABELS[kind]} '{sensor['name']}' "
                 f"({sensor.get('host')}) added to "
                 f"'{self.equipment.get('name')}'.")
        self.show_sensors()

    def edit_sensor(self):
        sensor = self._require_selection("Sensor")
        if sensor is None:
            return
        initial = dict(sensor)
        initial["kind_label"] = KIND_LABELS.get(sensor.get("kind"),
                                                KIND_LABELS[KIND_OUSTER])
        dialog = FormDialog(self.root, "Edit sensor", self._SENSOR_FIELDS,
                            initial=initial)
        if not dialog.result:
            return
        kind, values = self._split_kind(dialog.result)
        if kind != sensor.get("kind"):
            # a different kind needs a different config block
            sensor["config"] = {}
            self.log(f"Sensor type changed to {KIND_LABELS[kind]}; its "
                     "settings were reset to the defaults.")
        sensor.update(values)
        sensor["kind"] = kind
        Store.normalize_sensor(sensor)
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

        if self.sensor_kind() == KIND_CAMERA:
            self._build_camera_connection_panel(left)
            self._build_camera_config_panel(left)
            self._build_camera_device_panel(left)
            self._build_camera_stream_panel(left)
            self._build_log_panel(left)
            self._build_camera_viz_panel(right)
        elif self.sensor_kind() == KIND_ARBE:
            self._build_arbe_connection_panel(left)
            self._build_arbe_config_panel(left)
            self._build_arbe_stream_panel(left)
            self._build_log_panel(left)
            self._build_arbe_viz_panel(right)
        elif self.sensor_kind() == KIND_IMU:
            self._build_imu_connection_panel(left)
            self._build_imu_config_panel(left)
            self._build_imu_stream_panel(left)
            self._build_log_panel(left)
            self._build_imu_viz_panel(right)
        else:
            self._build_connection_panel(left)
            self._build_config_panel(left)
            self._build_stream_panel(left)
            self._build_record_panel(left)
            self._build_log_panel(left)
            self._build_viz_panel(right)

        self.status_var.set(f"{KIND_LABELS[self.sensor_kind()]} "
                            f"'{sensor.get('name')}' "
                            f"({sensor.get('host')}) - "
                            f"{self.project.get('name')} / "
                            f"{self.equipment.get('name')}")

    def sensor_kind(self) -> str:
        return (self.sensor or {}).get("kind", KIND_OUSTER)

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
        # replay what happened before this panel existed; everything is on
        # screen now, so nothing is left pending
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, "".join(self.log_lines))
        self.log_widget.see(tk.END)
        self.log_widget.configure(state=tk.DISABLED)
        self._log_pending.clear()

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

    # ---------------------------------------------------- camera dashboard --
    def _build_camera_connection_panel(self, parent):
        sensor = self.sensor
        conn = ttk.LabelFrame(parent, text="  CAMERA CONNECTION  ", padding=10)
        conn.pack(fill=tk.X, pady=4)
        ttk.Label(conn, text="Camera source:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.host_var = tk.StringVar(value=sensor.get("host", ""))
        ttk.Entry(conn, textvariable=self.host_var).pack(
            fill=tk.X, pady=(3, 0))
        ttk.Label(conn, text="0 = first USB camera · rtsp://user:pass@host/"
                             "stream · http://host/video.mjpg · /dev/video0",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 3))
        ttk.Button(conn, text="Save source to project",
                   command=self.on_save_host).pack(fill=tk.X, pady=(0, 4))
        row = ttk.Frame(conn, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Button(row, text="Probe camera",
                   command=self.on_probe_camera).pack(side=tk.LEFT,
                                                      expand=True, fill=tk.X,
                                                      padx=(0, 3))
        ttk.Button(row, text="Open in browser",
                   command=self.on_get_info).pack(side=tk.LEFT, expand=True,
                                                  fill=tk.X, padx=(3, 0))
        ttk.Label(conn, text="'Open in browser' only makes sense for an IP "
                             "camera's web page.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W)

    def _build_camera_config_panel(self, parent):
        cfg_values = self.sensor["config"]
        cfg = ttk.LabelFrame(parent, text="  CAMERA SETTINGS  ", padding=10)
        cfg.pack(fill=tk.X, pady=4)

        ttk.Label(cfg, text="Capture backend:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["backend"] = tk.StringVar(
            value=str(cfg_values.get("backend", "auto")))
        ttk.Combobox(cfg, textvariable=self.cfg_vars["backend"],
                     values=CAMERA_BACKENDS,
                     state="readonly").pack(fill=tk.X, pady=3)
        ttk.Label(cfg, text="Pixel format (FOURCC):",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["fourcc"] = tk.StringVar(
            value=str(cfg_values.get("fourcc", UNCHANGED)))
        ttk.Combobox(cfg, textvariable=self.cfg_vars["fourcc"],
                     values=CAMERA_FOURCCS).pack(fill=tk.X, pady=3)

        grid = ttk.Frame(cfg, style="Panel.TFrame")
        grid.pack(fill=tk.X, pady=3)
        for i, (key, _prop, label) in enumerate(CAMERA_PROPS):
            ttk.Label(grid, text=f"{label}:",
                      style="Muted.TLabel").grid(row=i, column=0, sticky=tk.W,
                                                 pady=1)
            self.cfg_vars[key] = tk.StringVar(
                value=str(cfg_values.get(key, "")))
            ttk.Entry(grid, textvariable=self.cfg_vars[key],
                      width=10).grid(row=i, column=1, padx=6, pady=1,
                                     sticky=tk.W)
        ttk.Label(cfg, text="Leave a box empty to keep whatever the camera "
                            "is already using.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        ttk.Button(cfg, text="⤓  Pull from camera",
                   command=self.on_pull_camera).pack(fill=tk.X, pady=(8, 3))
        ttk.Button(cfg, text="⤒  Push to camera", style="Accent.TButton",
                   command=self.on_push_camera).pack(fill=tk.X, pady=3)
        ttk.Button(cfg, text="Save to project (no camera access)",
                   command=lambda: self.on_save_camera_config(True)).pack(
            fill=tk.X, pady=3)
        ttk.Label(cfg, text="Cameras are free to ignore a setting; after a "
                            "push the values the camera actually kept are "
                            "written back into the form.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

    def _build_camera_device_panel(self, parent):
        cfg_values = self.sensor["config"]
        dev = ttk.LabelFrame(parent, text="  NETWORK CAMERA (ONVIF)  ",
                             padding=10)
        dev.pack(fill=tk.X, pady=4)
        ttk.Label(dev, text="Settings that live on the camera itself, not "
                            "in the local capture.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 4))

        grid = ttk.Frame(dev, style="Panel.TFrame")
        grid.pack(fill=tk.X)
        rows = [("onvif_port", "ONVIF port:", 8),
                ("onvif_user", "User:", 14),
                ("net_ip", "IP / CIDR:", 18),
                ("net_gateway", "Gateway:", 18),
                ("mtu", "MTU:", 8),
                ("bitrate", "Bitrate [kbit/s]:", 10),
                ("gop", "GOP [frames]:", 8)]
        for i, (key, label, width) in enumerate(rows):
            ttk.Label(grid, text=label,
                      style="Muted.TLabel").grid(row=i, column=0, sticky=tk.W,
                                                 pady=1)
            self.cfg_vars[key] = tk.StringVar(
                value=str(cfg_values.get(key, "")))
            ttk.Entry(grid, textvariable=self.cfg_vars[key],
                      width=width).grid(row=i, column=1, padx=6, pady=1,
                                        sticky=tk.W)
        ttk.Label(dev, text="Encoding:",
                  style="Muted.TLabel").pack(anchor=tk.W, pady=(4, 0))
        self.cfg_vars["encoding"] = tk.StringVar(
            value=str(cfg_values.get("encoding", UNCHANGED)))
        ttk.Combobox(dev, textvariable=self.cfg_vars["encoding"],
                     values=ONVIF_ENCODINGS,
                     state="readonly").pack(fill=tk.X, pady=3)
        ttk.Label(dev, text="IP / CIDR accepts 'dhcp' to switch back to "
                            "DHCP. Leave a box empty to leave that setting "
                            "alone. The password is asked for when needed "
                            "and never written to the project file.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))
        ttk.Button(dev, text="⤓  Pull from camera (ONVIF)",
                   command=self.on_pull_onvif).pack(fill=tk.X, pady=(8, 3))
        ttk.Button(dev, text="⤒  Push to camera (ONVIF)",
                   style="Accent.TButton",
                   command=self.on_push_onvif).pack(fill=tk.X, pady=3)
        ttk.Button(dev, text="Forget stored password",
                   command=self.on_forget_onvif_password).pack(fill=tk.X,
                                                               pady=3)

    def _build_camera_stream_panel(self, parent):
        live = ttk.LabelFrame(parent, text="  LIVE VIEW / RECORDING  ",
                              padding=10)
        live.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(live, text="▶  Start Preview",
                                    style="Accent.TButton",
                                    command=self.on_start_preview)
        self.start_btn.pack(fill=tk.X, pady=3)
        self.stop_btn = ttk.Button(live, text="■  Stop Preview",
                                   command=self.on_stop_stream,
                                   state=tk.DISABLED)
        self.stop_btn.pack(fill=tk.X, pady=3)
        ttk.Button(live, text="📷  Snapshot...",
                   command=self.on_snapshot).pack(fill=tk.X, pady=3)
        self.record_btn = ttk.Button(live, text="●  Start Recording",
                                     command=self.on_toggle_camera_record)
        self.record_btn.pack(fill=tk.X, pady=3)
        ttk.Button(live, text="▶  Play video file...",
                   command=self.on_open_video).pack(fill=tk.X, pady=3)
        ttk.Button(live, text="🗗  Open on another screen",
                   command=self.on_open_second_view).pack(fill=tk.X, pady=3)
        ttk.Label(live, text="opens the same live image in its own window - "
                             "drag it to a second monitor and press F11",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(live, text="Loop playback (repeat)",
                        variable=self.loop_var,
                        style="TCheckbutton").pack(anchor=tk.W, pady=(2, 0))

    def _build_camera_viz_panel(self, parent):
        sensor = self.sensor
        info = ttk.LabelFrame(parent, text="  CAMERA INFO  ", padding=8)
        info.pack(fill=tk.X)
        self.info_var = tk.StringVar(
            value=f"{sensor.get('name', '')}  ·  source "
                  f"{sensor.get('host', '')}\n"
                  "Not connected - use 'Probe camera' or 'Start Preview'.")
        ttk.Label(info, textvariable=self.info_var, style="Info.TLabel",
                  justify=tk.LEFT).pack(anchor=tk.W)

        viz = ttk.LabelFrame(parent, text="  LIVE IMAGE  ", padding=6)
        viz.pack(fill=tk.BOTH, expand=True, pady=6)
        self.fig = Figure(figsize=(8, 6), dpi=90, tight_layout=True,
                          facecolor=Theme.PANEL)
        self.canvas = FigureCanvasTkAgg(self.fig, master=viz)
        widget = self.canvas.get_tk_widget()
        widget.configure(bg=Theme.PANEL, highlightthickness=0)
        widget.pack(fill=tk.BOTH, expand=True)
        self.cam_ax = self.fig.add_subplot(111)
        self._style_axis(self.cam_ax, "No image yet")
        self.cam_artist = None
        self.canvas.draw_idle()

    # ------------------------------------------------------ imu dashboard --
    def _build_imu_connection_panel(self, parent):
        cfg_values = self.sensor["config"]
        conn = ttk.LabelFrame(parent, text="  INERTIAL CONNECTION  ",
                              padding=10)
        conn.pack(fill=tk.X, pady=4)

        ttk.Label(conn, text="Data source:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["source_type"] = tk.StringVar(
            value=str(cfg_values.get("source_type", IMU_SOURCES[0])))
        ttk.Combobox(conn, textvariable=self.cfg_vars["source_type"],
                     values=IMU_SOURCES,
                     state="readonly").pack(fill=tk.X, pady=3)

        ttk.Label(conn, text="Serial port / recording path:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["port"] = tk.StringVar(
            value=str(cfg_values.get("port", "")))
        ttk.Entry(conn, textvariable=self.cfg_vars["port"]).pack(fill=tk.X,
                                                                 pady=3)
        ttk.Label(conn, text="e.g. /dev/ttyUSB0, COM4, or the path of a "
                             ".csv / .npz log",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W)
        row = ttk.Frame(conn, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Baud:",
                  style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.cfg_vars["baud"] = tk.StringVar(
            value=str(cfg_values.get("baud", "115200")))
        ttk.Combobox(row, textvariable=self.cfg_vars["baud"],
                     values=IMU_BAUDS, width=10).grid(row=0, column=1, padx=6)
        ttk.Label(row, text="Line ending:",
                  style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W,
                                             pady=(3, 0))
        self.cfg_vars["line_ending"] = tk.StringVar(
            value=str(cfg_values.get("line_ending", "CRLF")))
        ttk.Combobox(row, textvariable=self.cfg_vars["line_ending"],
                     values=list(IMU_LINE_ENDINGS), state="readonly",
                     width=10).grid(row=1, column=1, padx=6, pady=(3, 0))

        ttk.Label(conn, text="ROS 2 topic (sensor_msgs/Imu):",
                  style="Muted.TLabel").pack(anchor=tk.W, pady=(6, 0))
        self.cfg_vars["topic"] = tk.StringVar(
            value=str(cfg_values.get("topic", "")))
        ttk.Entry(conn, textvariable=self.cfg_vars["topic"]).pack(fill=tk.X,
                                                                  pady=3)
        ros = ttk.Frame(conn, style="Panel.TFrame")
        ros.pack(fill=tk.X, pady=2)
        ttk.Label(ros, text="ROS_DOMAIN_ID:",
                  style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.cfg_vars["domain_id"] = tk.StringVar(
            value=str(cfg_values.get("domain_id", "0")))
        ttk.Entry(ros, textvariable=self.cfg_vars["domain_id"],
                  width=8).grid(row=0, column=1, padx=6)
        ttk.Label(ros, text="QoS:",
                  style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W,
                                             pady=(3, 0))
        self.cfg_vars["qos"] = tk.StringVar(
            value=str(cfg_values.get("qos", ARBE_QOS[0])))
        ttk.Combobox(ros, textvariable=self.cfg_vars["qos"], values=ARBE_QOS,
                     state="readonly", width=12).grid(row=1, column=1, padx=6,
                                                      pady=(3, 0))
        ttk.Label(conn, text="Driver node (for ROS parameters):",
                  style="Muted.TLabel").pack(anchor=tk.W, pady=(6, 0))
        self.cfg_vars["node"] = tk.StringVar(
            value=str(cfg_values.get("node", "")))
        ttk.Entry(conn, textvariable=self.cfg_vars["node"]).pack(fill=tk.X,
                                                                 pady=3)
        brow = ttk.Frame(conn, style="Panel.TFrame")
        brow.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(brow, text="Browse log...",
                   command=self.on_browse_imu_file).pack(side=tk.LEFT,
                                                         expand=True,
                                                         fill=tk.X,
                                                         padx=(0, 3))
        ttk.Button(brow, text="List serial ports",
                   command=self.on_list_serial_ports).pack(side=tk.LEFT,
                                                           expand=True,
                                                           fill=tk.X,
                                                           padx=(3, 0))
        ttk.Button(conn, text="Save connection to project",
                   command=lambda: self.on_save_imu_config(True)).pack(
            fill=tk.X, pady=(4, 0))
        # the sensor record's address mirrors the port, like the other kinds
        self.host_var = self.cfg_vars["port"]

    def _build_imu_config_panel(self, parent):
        cfg_values = self.sensor["config"]
        cfg = ttk.LabelFrame(parent, text="  DATA LAYOUT & COMMANDS  ",
                             padding=10)
        cfg.pack(fill=tk.X, pady=4)
        ttk.Label(cfg, text="Serial column layout:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["layout"] = tk.StringVar(
            value=str(cfg_values.get("layout", "")))
        ttk.Entry(cfg, textvariable=self.cfg_vars["layout"]).pack(fill=tk.X,
                                                                  pady=3)
        ttk.Label(cfg, text="comma-separated, one name per column: "
                            + ", ".join(IMU_COLUMNS)
                            + "  ·  use '-' to skip a column",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W)
        ttk.Label(cfg, text="Commands sent on push (one per line):",
                  style="Muted.TLabel").pack(anchor=tk.W, pady=(6, 0))
        self.param_text = scrolledtext.ScrolledText(
            cfg, height=6, font=("monospace", 9), wrap=tk.NONE,
            bg=Theme.FIELD, fg=Theme.FG, insertbackground=Theme.FG,
            relief=tk.FLAT, borderwidth=0, highlightthickness=1,
            highlightbackground=Theme.BORDER)
        self.param_text.pack(fill=tk.X, pady=3)
        self.param_text.insert("1.0", str(cfg_values.get("commands", "")))
        ttk.Label(cfg, text="Serial: each line is written to the port with "
                            "the chosen line ending, and the reply is "
                            "logged. ROS 2: each 'name: value' line is "
                            "written with 'ros2 param set'.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W)
        ttk.Button(cfg, text="⤓  Pull from device",
                   command=self.on_pull_imu).pack(fill=tk.X, pady=(8, 3))
        ttk.Button(cfg, text="⤒  Push to device", style="Accent.TButton",
                   command=self.on_push_imu).pack(fill=tk.X, pady=3)
        ttk.Button(cfg, text="Save to project (no device access)",
                   command=lambda: self.on_save_imu_config(True)).pack(
            fill=tk.X, pady=3)

    def _build_imu_stream_panel(self, parent):
        cfg_values = self.sensor["config"]
        live = ttk.LabelFrame(parent, text="  LIVE DATA  ", padding=10)
        live.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(live, text="▶  Start Stream",
                                    style="Accent.TButton",
                                    command=self.on_start_imu)
        self.start_btn.pack(fill=tk.X, pady=3)
        self.stop_btn = ttk.Button(live, text="■  Stop Stream",
                                   command=self.on_stop_stream,
                                   state=tk.DISABLED)
        self.stop_btn.pack(fill=tk.X, pady=3)
        self.record_btn = ttk.Button(live, text="●  Start Recording",
                                     command=self.on_toggle_imu_record)
        self.record_btn.pack(fill=tk.X, pady=3)
        row = ttk.Frame(live, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Samples shown:",
                  style="Muted.TLabel").pack(side=tk.LEFT)
        self.cfg_vars["window"] = tk.StringVar(
            value=str(cfg_values.get("window", "600")))
        ttk.Entry(row, textvariable=self.cfg_vars["window"],
                  width=8).pack(side=tk.LEFT, padx=6)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(live, text="Loop playback (repeat)",
                        variable=self.loop_var,
                        style="TCheckbutton").pack(anchor=tk.W, pady=(2, 0))

    def _build_imu_viz_panel(self, parent):
        sensor = self.sensor
        info = ttk.LabelFrame(parent, text="  LATEST SAMPLE  ", padding=8)
        info.pack(fill=tk.X)
        self.info_var = tk.StringVar(
            value=f"{sensor.get('name', '')}  ·  "
                  f"{sensor['config'].get('port', '')}\n"
                  "Not streaming - use 'Start Stream'.")
        ttk.Label(info, textvariable=self.info_var, style="Info.TLabel",
                  justify=tk.LEFT).pack(anchor=tk.W)

        viz = ttk.LabelFrame(parent, text="  INERTIAL TRACES  ", padding=6)
        viz.pack(fill=tk.BOTH, expand=True, pady=6)
        self.fig = Figure(figsize=(8, 6), dpi=90, tight_layout=True,
                          facecolor=Theme.PANEL)
        self.canvas = FigureCanvasTkAgg(self.fig, master=viz)
        widget = self.canvas.get_tk_widget()
        widget.configure(bg=Theme.PANEL, highlightthickness=0)
        widget.pack(fill=tk.BOTH, expand=True)
        self.imu_axes = {}
        self.imu_lines = {}
        self.imu_buffer = {}
        self.imu_shown = ()
        self.imu_rate = None
        ax = self.fig.add_subplot(111)
        self._style_imu_axis(ax, "Waiting for samples", "")
        self.canvas.draw_idle()

    def _style_imu_axis(self, ax, title, ylabel):
        ax.set_facecolor(Theme.BG)
        ax.set_title(title, fontsize=9, color=Theme.FG, loc="left")
        ax.set_ylabel(ylabel, color=Theme.MUTED, fontsize=8)
        ax.tick_params(colors=Theme.MUTED, labelsize=7)
        ax.grid(True, color=Theme.BORDER, linewidth=0.5, alpha=0.6)
        for spine in ax.spines.values():
            spine.set_color(Theme.BORDER)

    # -------------------------------------------------------- imu actions ---
    def _require_serial(self) -> bool:
        if not HAVE_SERIAL:
            messagebox.showerror(
                "pyserial missing",
                "Serial inertial sensors need the pyserial package "
                f"({SERIAL_IMPORT_ERROR}).\n\n"
                "Install it with:\n    pip install pyserial")
            return False
        return True

    def _collect_imu_config(self):
        values = {key: var.get().strip()
                  for key, var in self.cfg_vars.items()}
        if getattr(self, "param_text", None) is not None:
            values["commands"] = self.param_text.get("1.0", tk.END).strip()
        for key, label in (("baud", "Baud"), ("window", "Samples shown"),
                           ("domain_id", "ROS_DOMAIN_ID")):
            text = values.get(key, "")
            if not text:
                continue
            try:
                if int(float(text)) <= 0 and key != "domain_id":
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid value",
                                     f"'{label}' must be a positive number.")
                return None
        layout = [c.strip() for c in values.get("layout", "").split(",")
                  if c.strip()]
        unknown = [c for c in layout if c not in IMU_COLUMNS and c != "-"]
        if unknown:
            messagebox.showerror(
                "Invalid layout",
                "Unknown column name(s): " + ", ".join(unknown)
                + "\n\nUse: " + ", ".join(IMU_COLUMNS) + " or '-' to skip.")
            return None
        return values

    def on_save_imu_config(self, announce=False):
        values = self._collect_imu_config()
        if values is None:
            return None
        self.sensor["config"].update(values)
        self.sensor["host"] = values.get("port", "")
        self.store.save()
        if announce:
            self.log(f"Inertial settings saved to project for "
                     f"'{self.sensor.get('name')}' (device not contacted).")
        return values

    def on_browse_imu_file(self):
        path = filedialog.askopenfilename(
            title="Open IMU log",
            filetypes=[("IMU logs", "*.csv *.npz *.txt *.log"),
                       ("All files", "*")])
        if path:
            self.cfg_vars["port"].set(path)
            self.cfg_vars["source_type"].set("Recording file")
            self.on_save_imu_config()

    def on_list_serial_ports(self):
        if not self._require_serial():
            return
        try:
            from serial.tools import list_ports
            ports = list(list_ports.comports())
        except Exception as e:
            self.log(f"ERROR listing serial ports: {e}")
            return
        if not ports:
            self.log("No serial ports found.")
            return
        for port in ports:
            self.log(f"  {port.device}  -  {port.description}")

    def on_start_imu(self):
        if self.reader is not None:
            self.log("IMU stream already running.")
            return
        values = self.on_save_imu_config()
        if values is None:
            return
        source = values.get("source_type", IMU_SOURCES[0])
        if source == "Serial port" and not self._require_serial():
            return
        if source in ("Serial port", "Recording file") and \
                not values.get("port"):
            messagebox.showerror("Start stream",
                                 "Enter the serial port or the log path.")
            return
        if source == "ROS 2 topic" and not values.get("topic"):
            messagebox.showerror("Start stream",
                                 "Enter the sensor_msgs/Imu topic.")
            return
        self.imu_buffer = {}
        self.imu_shown = ()
        self.imu_rate = None
        self.reader = ImuReader(values, self.frame_queue, self.log,
                                loop=self.loop_var.get())
        self.reader.start()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def on_toggle_imu_record(self):
        reader = self.reader if isinstance(self.reader, ImuReader) else None
        if reader is not None and reader.recording:
            reader.stop_recording()
            self.record_btn.configure(text="●  Start Recording")
            return
        if reader is None:
            messagebox.showinfo("Recording",
                                "Start the stream first - samples are "
                                "written as they arrive.")
            return
        name = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (self.sensor or {}).get("name", "imu"))
        path = filedialog.asksaveasfilename(
            title="Save IMU log as", defaultextension=".csv",
            initialfile=f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        reader.start_recording(path)
        self.record_btn.configure(text="■  Stop Recording")

    def on_pull_imu(self):
        """Serial: sample raw lines and suggest a layout.
        ROS 2: read the driver's parameters."""
        values = self.on_save_imu_config()
        if values is None:
            return
        if values.get("source_type") == "ROS 2 topic":
            node = values.get("node", "").strip()
            if not node:
                messagebox.showerror("Pull from device",
                                     "Enter the driver's ROS 2 node name.")
                return
            self.log(f"Reading parameters from {node} ...")
            domain = values.get("domain_id", "")

            def ros_work():
                ok, out = self._ros2("param", "dump", node, domain=domain)
                if not ok:
                    self.log(f"ERROR reading parameters: {out}")
                    return
                lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
                self.frame_queue.put(("imu_params", "\n".join(lines)))

            threading.Thread(target=ros_work, daemon=True).start()
            return

        if not self._require_serial():
            return
        if self.reader is not None:
            messagebox.showinfo("Pull from device",
                                "Stop the stream first - the port can only "
                                "be open once.")
            return
        port = values.get("port", "")
        baud = int(values.get("baud") or 115200)
        self.log(f"Listening to {port} for a couple of seconds ...")

        def work():
            lines = []
            try:
                with pyserial.Serial(port, baud, timeout=0.3) as link:
                    deadline = time.time() + 2.0
                    while time.time() < deadline and len(lines) < 40:
                        raw = link.readline().decode("ascii", "replace")
                        if raw.strip():
                            lines.append(raw.strip())
            except Exception as e:
                self.log(f"ERROR reading from {port}: {e}")
                return
            if not lines:
                self.log("No data received - check the baud rate and wiring.")
                return
            for raw in lines[:5]:
                self.log(f"  {raw[:100]}")
            suggestion = suggest_imu_layout(lines)
            self.log(f"{len(lines)} line(s) read"
                     + (f"; suggested layout: {suggestion}"
                        if suggestion else "; could not guess a layout"))
            if suggestion:
                self.frame_queue.put(("imu_layout", suggestion))

        threading.Thread(target=work, daemon=True).start()

    def _finish_imu_pull(self, kind: str, text: str):
        if kind == "layout":
            self.cfg_vars["layout"].set(text)
            self.sensor["config"]["layout"] = text
            self.log(f"Layout set to '{text}' - check it against the "
                     "device's manual before trusting the plots.")
        else:
            if getattr(self, "param_text", None) is not None:
                self.param_text.delete("1.0", tk.END)
                self.param_text.insert("1.0", text)
            self.sensor["config"]["commands"] = text
            self.log(f"Parameters pulled into the project "
                     f"({len(text.splitlines())} line(s)).")
        self.sensor["last_seen"] = now_stamp()
        self.store.save()

    def on_push_imu(self):
        """Serial: write the command lines to the port.
        ROS 2: 'ros2 param set' per line."""
        values = self.on_save_imu_config()
        if values is None:
            return
        commands = [ln.strip() for ln in
                    values.get("commands", "").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
        if not commands:
            messagebox.showerror("Push to device",
                                 "No commands to send - add one per line.")
            return

        if values.get("source_type") == "ROS 2 topic":
            node = values.get("node", "").strip()
            params = self.parse_param_lines(values.get("commands", ""))
            if not node or not params:
                messagebox.showerror(
                    "Push to device",
                    "Enter the driver's ROS 2 node name and 'name: value' "
                    "lines.")
                return
            if not messagebox.askyesno(
                    "Push parameters",
                    f"Write {len(params)} parameter(s) to {node}?\n\n"
                    + "\n".join(f"  {n} = {v}" for n, v in params[:20])):
                return
            domain = values.get("domain_id", "")

            def ros_work():
                for name, value in params:
                    ok, out = self._ros2("param", "set", node, name, value,
                                         domain=domain)
                    self.log(f"  {name} = {value}  ->  "
                             + (out.strip() or "set" if ok
                                else f"ERROR: {out}"))

            self.log(f"Pushing {len(params)} parameter(s) to {node} ...")
            threading.Thread(target=ros_work, daemon=True).start()
            return

        if not self._require_serial():
            return
        if self.reader is not None:
            messagebox.showinfo("Push to device",
                                "Stop the stream first - the port can only "
                                "be open once.")
            return
        port = values.get("port", "")
        baud = int(values.get("baud") or 115200)
        ending = IMU_LINE_ENDINGS.get(values.get("line_ending", "CRLF"),
                                      "\r\n")
        if not messagebox.askyesno(
                "Push commands",
                f"Send {len(commands)} command(s) to {port} at {baud} baud?"
                "\n\n" + "\n".join(f"  {c}" for c in commands[:20])
                + ("\n  ..." if len(commands) > 20 else "")):
            return
        self.log(f"Sending {len(commands)} command(s) to {port} ...")

        def work():
            try:
                with pyserial.Serial(port, baud, timeout=0.5) as link:
                    for command in commands:
                        link.write((command + ending).encode("ascii",
                                                             "replace"))
                        link.flush()
                        time.sleep(0.15)
                        reply = link.readline().decode("ascii",
                                                       "replace").strip()
                        self.log(f"  {command}  ->  "
                                 + (reply[:100] if reply else "(no reply)"))
                self.sensor["last_seen"] = now_stamp()
                self.store.save()
                self.log("All commands sent.")
            except Exception as e:
                self.log(f"ERROR sending commands: {e}")

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------- imu drawing ---
    def _imu_window(self) -> int:
        var = self.cfg_vars.get("window")
        try:
            return max(20, int(float(var.get())))
        except (AttributeError, ValueError):
            return 600

    def _draw_imu(self, samples: list, total: int):
        if self.canvas is None or self.imu_axes is None:
            return
        window = self._imu_window()
        for sample in samples:
            for key, value in sample.items():
                buffer = self.imu_buffer.get(key)
                if buffer is None or buffer.maxlen != window:
                    buffer = deque(buffer or (), maxlen=window)
                    self.imu_buffer[key] = buffer
                buffer.append(value)

        groups = [g for g in IMU_PLOT_GROUPS
                  if any(c in self.imu_buffer for c in g[2])]
        shown = tuple(g[0] for g in groups)
        if not groups:
            return
        if shown != self.imu_shown:
            self._build_imu_axes(groups)
            self.imu_shown = shown

        times = list(self.imu_buffer.get("t", ()))
        for title, _ylabel, columns in groups:
            ax = self.imu_axes[title]
            for column in columns:
                values = list(self.imu_buffer.get(column, ()))
                if not values:
                    continue
                if times and len(times) >= len(values):
                    base = times[len(times) - len(values):]
                    x = [t - base[0] for t in base]
                else:
                    x = list(range(len(values)))
                line = self.imu_lines.get(column)
                if line is not None:
                    line.set_data(x, values)
            ax.relim()
            ax.autoscale_view()
        self.canvas.draw_idle()
        self._show_imu_stats(total, samples[-1] if samples else {})

    def _build_imu_axes(self, groups):
        self.fig.clear()
        self.imu_axes = {}
        self.imu_lines = {}
        for i, (title, ylabel, columns) in enumerate(groups):
            ax = self.fig.add_subplot(len(groups), 1, i + 1)
            self._style_imu_axis(ax, title, ylabel)
            for column in columns:
                if column not in self.imu_buffer:
                    continue
                self.imu_lines[column], = ax.plot(
                    [], [], linewidth=1.0, label=column,
                    color=IMU_TRACE_COLORS.get(column, Theme.FG))
            legend = ax.legend(loc="upper right", fontsize=7, ncol=3,
                               facecolor=Theme.PANEL, edgecolor=Theme.BORDER)
            for text in legend.get_texts():
                text.set_color(Theme.FG)
            self.imu_axes[title] = ax

    def _show_imu_stats(self, total, latest):
        if self.info_var is None:
            return
        times = list(self.imu_buffer.get("t", ()))
        if len(times) > 1 and times[-1] > times[0]:
            self.imu_rate = (len(times) - 1) / (times[-1] - times[0])
        lines = [f"Samples      : {total}"
                 + (f"   ({self.imu_rate:.1f} Hz)"
                    if self.imu_rate else "")]
        for title, _ylabel, columns in IMU_PLOT_GROUPS:
            present = [c for c in columns if c in latest]
            if not present:
                continue
            lines.append(f"{title:<13}: "
                         + "  ".join(f"{c}={latest[c]:+8.3f}"
                                     for c in present))
        if self.reader is not None and getattr(self.reader, "recording",
                                               False):
            lines.append("● recording")
        self.info_var.set("\n".join(lines))

    # ----------------------------------------------------- arbe dashboard --
    def _build_arbe_connection_panel(self, parent):
        cfg_values = self.sensor["config"]
        conn = ttk.LabelFrame(parent, text="  RADAR CONNECTION  ", padding=10)
        conn.pack(fill=tk.X, pady=4)

        ttk.Label(conn, text="Data source:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["source_type"] = tk.StringVar(
            value=str(cfg_values.get("source_type", ARBE_SOURCES[0])))
        ttk.Combobox(conn, textvariable=self.cfg_vars["source_type"],
                     values=ARBE_SOURCES,
                     state="readonly").pack(fill=tk.X, pady=3)

        ttk.Label(conn, text="PointCloud2 topic:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["topic"] = tk.StringVar(
            value=str(cfg_values.get("topic", "")))
        ttk.Entry(conn, textvariable=self.cfg_vars["topic"]).pack(fill=tk.X,
                                                                  pady=3)
        ttk.Label(conn, text="e.g. /arbe/rviz/pointcloud - the topic the "
                             "Arbe driver publishes detections on",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W)

        row = ttk.Frame(conn, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="ROS_DOMAIN_ID:",
                  style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W,
                                             pady=1)
        self.cfg_vars["domain_id"] = tk.StringVar(
            value=str(cfg_values.get("domain_id", "0")))
        ttk.Entry(row, textvariable=self.cfg_vars["domain_id"],
                  width=8).grid(row=0, column=1, padx=6, pady=1)
        ttk.Label(row, text="QoS:",
                  style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W,
                                             pady=1)
        self.cfg_vars["qos"] = tk.StringVar(
            value=str(cfg_values.get("qos", ARBE_QOS[0])))
        ttk.Combobox(row, textvariable=self.cfg_vars["qos"], values=ARBE_QOS,
                     state="readonly", width=12).grid(row=1, column=1, padx=6,
                                                      pady=1)

        ttk.Label(conn, text="Driver node (for parameters):",
                  style="Muted.TLabel").pack(anchor=tk.W, pady=(6, 0))
        self.cfg_vars["node"] = tk.StringVar(
            value=str(cfg_values.get("node", "")))
        ttk.Entry(conn, textvariable=self.cfg_vars["node"]).pack(fill=tk.X,
                                                                 pady=3)
        # the sensor's address field doubles as the recording path
        self.host_var = tk.StringVar(value=self.sensor.get("host", ""))
        ttk.Label(conn, text="Recording file (used by the 'Recording file' "
                             "source):", style="Muted.TLabel").pack(
            anchor=tk.W, pady=(6, 0))
        ttk.Entry(conn, textvariable=self.host_var).pack(fill=tk.X, pady=3)
        ttk.Button(conn, text="Browse recording...",
                   command=self.on_browse_radar_file).pack(fill=tk.X, pady=2)
        ttk.Button(conn, text="Save connection to project",
                   command=lambda: self.on_save_arbe_config(True)).pack(
            fill=tk.X, pady=(2, 0))

    def _build_arbe_config_panel(self, parent):
        cfg_values = self.sensor["config"]
        cfg = ttk.LabelFrame(parent, text="  RADAR PARAMETERS  ", padding=10)
        cfg.pack(fill=tk.X, pady=4)
        ttk.Label(cfg, text="One 'name: value' per line. Pull reads them "
                            "from the driver with 'ros2 param dump', push "
                            "writes each one with 'ros2 param set'.",
                  style="Hint.TLabel", wraplength=300,
                  justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 4))
        self.param_text = scrolledtext.ScrolledText(
            cfg, height=7, font=("monospace", 9), wrap=tk.NONE,
            bg=Theme.FIELD, fg=Theme.FG, insertbackground=Theme.FG,
            relief=tk.FLAT, borderwidth=0, highlightthickness=1,
            highlightbackground=Theme.BORDER)
        self.param_text.pack(fill=tk.X)
        self.param_text.insert("1.0", str(cfg_values.get("parameters", "")))
        ttk.Button(cfg, text="⤓  Pull from radar",
                   command=self.on_pull_arbe).pack(fill=tk.X, pady=(8, 3))
        ttk.Button(cfg, text="⤒  Push to radar", style="Accent.TButton",
                   command=self.on_push_arbe).pack(fill=tk.X, pady=3)
        ttk.Button(cfg, text="Save to project (no radar access)",
                   command=lambda: self.on_save_arbe_config(True)).pack(
            fill=tk.X, pady=3)

        view = ttk.LabelFrame(parent, text="  VIEW  ", padding=10)
        view.pack(fill=tk.X, pady=4)
        ttk.Label(view, text="Colour points by:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cfg_vars["color_by"] = tk.StringVar(
            value=str(cfg_values.get("color_by", "doppler")))
        box = ttk.Combobox(view, textvariable=self.cfg_vars["color_by"],
                           values=ARBE_COLOR_BY, state="readonly")
        box.pack(fill=tk.X, pady=3)
        box.bind("<<ComboboxSelected>>", lambda e: self._redraw_radar())
        grid = ttk.Frame(view, style="Panel.TFrame")
        grid.pack(fill=tk.X, pady=3)
        ttk.Label(grid, text="Max range [m]:",
                  style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W,
                                             pady=1)
        self.cfg_vars["max_range"] = tk.StringVar(
            value=str(cfg_values.get("max_range", "150")))
        ttk.Entry(grid, textvariable=self.cfg_vars["max_range"],
                  width=8).grid(row=0, column=1, padx=6, pady=1)
        ttk.Label(grid, text="Min SNR:",
                  style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W,
                                             pady=1)
        self.cfg_vars["min_snr"] = tk.StringVar(
            value=str(cfg_values.get("min_snr", "")))
        ttk.Entry(grid, textvariable=self.cfg_vars["min_snr"],
                  width=8).grid(row=1, column=1, padx=6, pady=1)
        ttk.Button(view, text="Apply view",
                   command=self._redraw_radar).pack(fill=tk.X, pady=(6, 0))

    def _build_arbe_stream_panel(self, parent):
        live = ttk.LabelFrame(parent, text="  LIVE RADAR  ", padding=10)
        live.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(live, text="▶  Start Stream",
                                    style="Accent.TButton",
                                    command=self.on_start_radar)
        self.start_btn.pack(fill=tk.X, pady=3)
        self.stop_btn = ttk.Button(live, text="■  Stop Stream",
                                   command=self.on_stop_stream,
                                   state=tk.DISABLED)
        self.stop_btn.pack(fill=tk.X, pady=3)
        ttk.Button(live, text="Save current frame (.csv)...",
                   command=self.on_save_radar_frame).pack(fill=tk.X, pady=3)
        ttk.Button(live, text="List ROS 2 topics",
                   command=self.on_list_ros_topics).pack(fill=tk.X, pady=3)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(live, text="Loop playback (repeat)",
                        variable=self.loop_var,
                        style="TCheckbutton").pack(anchor=tk.W, pady=(2, 0))

    def _build_arbe_viz_panel(self, parent):
        sensor = self.sensor
        info = ttk.LabelFrame(parent, text="  RADAR FRAME  ", padding=8)
        info.pack(fill=tk.X)
        self.info_var = tk.StringVar(
            value=f"{sensor.get('name', '')}  ·  "
                  f"{sensor['config'].get('topic', '')}\n"
                  "Not streaming - use 'Start Stream'.")
        ttk.Label(info, textvariable=self.info_var, style="Info.TLabel",
                  justify=tk.LEFT).pack(anchor=tk.W)

        viz = ttk.LabelFrame(parent, text="  DETECTIONS (BIRD'S-EYE VIEW)  ",
                             padding=6)
        viz.pack(fill=tk.BOTH, expand=True, pady=6)
        self.fig = Figure(figsize=(8, 6), dpi=90, tight_layout=True,
                          facecolor=Theme.PANEL)
        self.canvas = FigureCanvasTkAgg(self.fig, master=viz)
        widget = self.canvas.get_tk_widget()
        widget.configure(bg=Theme.PANEL, highlightthickness=0)
        widget.pack(fill=tk.BOTH, expand=True)
        self.radar_ax = self.fig.add_subplot(111)
        self.radar_scatter = None
        self.radar_colorbar = None
        self.last_radar = None
        self._style_radar_axis()
        self.canvas.draw_idle()

    def _style_radar_axis(self):
        ax = self.radar_ax
        ax.set_facecolor(Theme.BG)
        ax.tick_params(colors=Theme.MUTED, labelsize=8)
        ax.set_xlabel("lateral y [m]", color=Theme.MUTED, fontsize=8)
        ax.set_ylabel("forward x [m]", color=Theme.MUTED, fontsize=8)
        ax.grid(True, color=Theme.BORDER, linewidth=0.5, alpha=0.6)
        for spine in ax.spines.values():
            spine.set_color(Theme.BORDER)

    # ------------------------------------------------------- arbe actions ---
    def _collect_arbe_config(self):
        values = {key: var.get().strip()
                  for key, var in self.cfg_vars.items()}
        if getattr(self, "param_text", None) is not None:
            values["parameters"] = self.param_text.get("1.0", tk.END).strip()
        for key, label in (("max_range", "Max range"), ("min_snr", "Min SNR"),
                           ("domain_id", "ROS_DOMAIN_ID")):
            text = values.get(key, "")
            if not text:
                continue
            try:
                float(text)
            except ValueError:
                messagebox.showerror("Invalid value",
                                     f"'{label}' must be a number, or empty.")
                return None
        return values

    def on_save_arbe_config(self, announce=False):
        values = self._collect_arbe_config()
        if values is None:
            return None
        self.sensor["config"].update(values)
        self.sensor["host"] = self.host_var.get().strip()
        self.store.save()
        if announce:
            self.log(f"Radar settings saved to project for "
                     f"'{self.sensor.get('name')}' (radar not contacted).")
        return values

    def on_browse_radar_file(self):
        path = filedialog.askopenfilename(
            title="Open radar recording",
            filetypes=[("Radar clouds", "*.npz *.npy *.csv *.pcd *.txt"),
                       ("All files", "*")])
        if path:
            self.host_var.set(path)
            self.cfg_vars["source_type"].set("Recording file")
            self.on_save_arbe_config()

    def on_start_radar(self):
        if self.reader is not None:
            self.log("Radar stream already running.")
            return
        values = self.on_save_arbe_config()
        if values is None:
            return
        is_file = values["source_type"] == "Recording file"
        source = self.host_var.get().strip() if is_file else values["topic"]
        if not source:
            messagebox.showerror(
                "Start stream",
                "Pick a recording file." if is_file
                else "Enter the PointCloud2 topic to subscribe to.")
            return
        self.reader = ArbeReader(source, self.frame_queue, self.log,
                                 is_file=is_file,
                                 loop=is_file and self.loop_var.get(),
                                 domain_id=values.get("domain_id", ""),
                                 qos=values.get("qos", "best_effort"))
        self.reader.start()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def _ros2(self, *args, domain="", timeout=15):
        """Run a ros2 CLI command; returns (ok, output).

        Called from worker threads, so the domain id is passed in rather
        than read off a Tk variable here.
        """
        env = dict(os.environ)
        if str(domain).strip():
            env["ROS_DOMAIN_ID"] = str(domain).strip()
        try:
            done = subprocess.run(["ros2", *args], capture_output=True,
                                  text=True, timeout=timeout, env=env)
        except FileNotFoundError:
            return False, ("the 'ros2' command is not on PATH - source a "
                           "ROS 2 environment before starting this app")
        except subprocess.TimeoutExpired:
            return False, f"'ros2 {' '.join(args)}' timed out"
        if done.returncode != 0:
            return False, (done.stderr or done.stdout or "").strip()
        return True, done.stdout

    def on_list_ros_topics(self):
        self.log("Listing ROS 2 topics ...")
        domain = self.cfg_vars["domain_id"].get().strip()

        def work():
            ok, out = self._ros2("topic", "list", domain=domain)
            if not ok:
                self.log(f"ERROR listing topics: {out}")
                return
            topics = [t for t in out.splitlines() if t.strip()]
            self.log(f"{len(topics)} topic(s): " + ", ".join(topics[:40]))

        threading.Thread(target=work, daemon=True).start()

    def on_pull_arbe(self):
        """Read the driver's parameters with 'ros2 param dump'."""
        values = self.on_save_arbe_config()
        if values is None:
            return
        node = values.get("node", "").strip()
        if not node:
            messagebox.showerror("Pull from radar",
                                 "Enter the driver's ROS 2 node name, "
                                 "e.g. /arbe_driver.")
            return
        self.log(f"Reading parameters from {node} ...")
        domain = values.get("domain_id", "")

        def work():
            ok, out = self._ros2("param", "dump", node, domain=domain)
            if not ok:
                self.log(f"ERROR reading parameters: {out}")
                return
            lines = [ln.rstrip() for ln in out.splitlines()
                     if ln.strip() and not ln.strip().startswith("#")]
            self.frame_queue.put(("radar_params", "\n".join(lines)))

        threading.Thread(target=work, daemon=True).start()

    def _finish_arbe_pull(self, text: str):
        if getattr(self, "param_text", None) is not None:
            self.param_text.delete("1.0", tk.END)
            self.param_text.insert("1.0", text)
        self.sensor["config"]["parameters"] = text
        self.sensor["last_seen"] = now_stamp()
        self.store.save()
        self.log(f"Parameters pulled into the project "
                 f"({len(text.splitlines())} line(s)).")

    @staticmethod
    def parse_param_lines(text: str) -> list:
        """'name: value' / 'name=value' lines -> [(name, value)]."""
        out = []
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.endswith(":"):
                continue                      # blank, comment or YAML header
            for sep in (":", "="):
                name, found, value = line.partition(sep)
                if found:
                    name, value = name.strip(), value.strip()
                    if name and value:
                        out.append((name, value))
                    break
        return out

    def on_push_arbe(self):
        """Write each parameter to the driver with 'ros2 param set'."""
        values = self.on_save_arbe_config()
        if values is None:
            return
        node = values.get("node", "").strip()
        params = self.parse_param_lines(values.get("parameters", ""))
        if not node:
            messagebox.showerror("Push to radar",
                                 "Enter the driver's ROS 2 node name, "
                                 "e.g. /arbe_driver.")
            return
        if not params:
            messagebox.showerror("Push to radar",
                                 "No parameters to push - add lines like\n\n"
                                 "    framerate: 10\n    tx_power: 3")
            return
        listing = "\n".join(f"  {n} = {v}" for n, v in params[:20])
        if not messagebox.askyesno(
                "Push parameters",
                f"Write {len(params)} parameter(s) to {node}?\n\n{listing}"
                + ("\n  ..." if len(params) > 20 else "")):
            return
        self.log(f"Pushing {len(params)} parameter(s) to {node} ...")
        domain = values.get("domain_id", "")

        def work():
            failed = 0
            for name, value in params:
                ok, out = self._ros2("param", "set", node, name, value,
                                     domain=domain)
                if ok:
                    self.log(f"  {name} = {value}  ->  "
                             f"{out.strip() or 'set'}")
                else:
                    failed += 1
                    self.log(f"  ERROR setting {name}: {out}")
            if failed:
                self.log(f"{len(params) - failed} parameter(s) set, "
                         f"{failed} failed.")
            else:
                self.sensor["last_seen"] = now_stamp()
                self.store.save()
                self.log("All parameters set.")

        threading.Thread(target=work, daemon=True).start()

    def on_save_radar_frame(self):
        points = self.last_radar
        if not points:
            messagebox.showinfo("Save frame",
                                "No radar frame has been received yet.")
            return
        name = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (self.sensor or {}).get("name", "radar"))
        path = filedialog.asksaveasfilename(
            title="Save current frame", defaultextension=".csv",
            initialfile=f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV", "*.csv"), ("NumPy archive", "*.npz")])
        if not path:
            return
        try:
            keys = [k for k in ("x", "y", "z", "doppler", "snr", "power",
                                "range") if k in points]
            if path.lower().endswith(".npz"):
                np.savez_compressed(path, **{k: points[k] for k in keys})
            else:
                table = np.column_stack([points[k] for k in keys])
                np.savetxt(path, table, delimiter=",", header=",".join(keys),
                           comments="", fmt="%.6g")
            self.log(f"Frame saved ({len(points['x'])} points) -> {path}")
        except Exception as e:
            self.log(f"ERROR saving frame: {e}")
            messagebox.showerror("Save frame", f"Could not save:\n{e}")

    def _radar_view_limits(self):
        def number(key, default=None):
            var = self.cfg_vars.get(key)
            text = var.get().strip() if var is not None else ""
            try:
                return float(text)
            except ValueError:
                return default
        return number("max_range", 150.0), number("min_snr")

    def _draw_radar(self, points: dict, index: int, stats: dict):
        if self.canvas is None or getattr(self, "radar_ax", None) is None:
            return
        self.last_radar = points
        self.last_radar_index = index
        max_range, min_snr = self._radar_view_limits()
        color_by = self.cfg_vars["color_by"].get()

        x, y = points["x"], points["y"]
        mask = np.ones(len(x), dtype=bool)
        if max_range:
            mask &= points["range"] <= max_range
        if min_snr is not None and "snr" in points:
            mask &= points["snr"] >= min_snr
        x, y = x[mask], y[mask]
        values = points.get(color_by)
        if values is None or len(values) != len(mask):
            values = points["range"]
            shown_by = "range"
        else:
            shown_by = color_by
        values = values[mask]
        # doppler reads as approaching / receding, so centre it on zero
        if shown_by == "doppler" and len(values):
            span = float(np.max(np.abs(values))) or 1.0
            cmap, vmin, vmax = "coolwarm", -span, span
        else:
            cmap, vmin, vmax = "viridis", None, None

        self.radar_ax.clear()
        self._style_radar_axis()
        self.radar_scatter = self.radar_ax.scatter(
            y, x, c=values, s=6, cmap=cmap, vmin=vmin, vmax=vmax,
            linewidths=0)
        limit = max_range or 150.0
        # keep the view no wider than it needs to be, but never past the
        # range limit, so the scene fills the panel
        lateral = limit
        if len(y):
            lateral = min(limit, max(20.0, float(np.max(np.abs(y))) * 1.15))
        self.radar_ax.set_xlim(lateral, -lateral)     # +y is to the left
        self.radar_ax.set_ylim(-limit * 0.05, limit)
        self.radar_ax.set_aspect("equal", adjustable="box")
        self.radar_ax.set_title(
            f"Frame {index}  ·  {len(x)} of {stats.get('points', 0)} points  "
            f"·  colour = {shown_by}",
            fontsize=9, color=Theme.FG, loc="left")
        if self.radar_colorbar is not None:
            try:
                self.radar_colorbar.remove()
            except Exception:
                pass
        self.radar_colorbar = self.fig.colorbar(
            self.radar_scatter, ax=self.radar_ax, shrink=0.85)
        self.radar_colorbar.ax.tick_params(colors=Theme.MUTED, labelsize=7)
        self.radar_colorbar.outline.set_edgecolor(Theme.BORDER)
        self.canvas.draw_idle()
        self._show_radar_stats(index, stats, len(x))

    def _redraw_radar(self):
        if self.last_radar:
            self._draw_radar(self.last_radar,
                             getattr(self, "last_radar_index", 0),
                             radar_stats(self.last_radar))

    def _show_radar_stats(self, index, stats, shown):
        if self.info_var is None:
            return
        lines = [f"Frame        : {index}",
                 f"Detections   : {stats.get('points', 0)} "
                 f"({shown} shown)"]
        if "max_range" in stats:
            lines.append(f"Max range    : {stats['max_range']:.1f} m")
        if "doppler_min" in stats:
            lines.append(f"Doppler      : {stats['doppler_min']:.2f} .. "
                         f"{stats['doppler_max']:.2f} m/s")
        if "snr_min" in stats:
            lines.append(f"SNR          : {stats['snr_min']:.1f} .. "
                         f"{stats['snr_max']:.1f}")
        self.info_var.set("\n".join(lines))

    # ----------------------------------------------------- camera actions ---
    def _require_cv2(self) -> bool:
        if not HAVE_CV2:
            messagebox.showerror(
                "OpenCV missing",
                "Camera sensors need the OpenCV Python package "
                f"({CV2_IMPORT_ERROR}).\n\n"
                "Install it with:\n    pip install opencv-python")
            return False
        return True

    def _collect_camera_config(self):
        """Read the camera form, or None if a value is not a number."""
        values = {key: var.get().strip()
                  for key, var in self.cfg_vars.items()}
        for key, _prop, label in CAMERA_PROPS:
            text = values.get(key, "")
            if not text:
                continue
            try:
                float(text)
            except ValueError:
                messagebox.showerror(
                    "Invalid camera setting",
                    f"'{label}' must be a number, or empty to leave it "
                    "unchanged.")
                return None
        fourcc = values.get("fourcc", "")
        if fourcc and fourcc != UNCHANGED and len(fourcc) != 4:
            messagebox.showerror(
                "Invalid pixel format",
                "FOURCC codes are exactly four characters, e.g. MJPG.")
            return None
        # device-side (ONVIF) settings
        for key, label, low, high in (("onvif_port", "ONVIF port", 1, 65535),
                                      ("mtu", "MTU", 576, 9216),
                                      ("bitrate", "Bitrate", 1, 200000),
                                      ("gop", "GOP", 1, 1000)):
            text = values.get(key, "")
            if not text:
                continue
            try:
                number = int(float(text))
                if not low <= number <= high:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid camera setting",
                    f"'{label}' must be a whole number between {low} and "
                    f"{high}, or empty to leave it unchanged.")
                return None
        ip = values.get("net_ip", "")
        if ip and ip.lower() != DHCP and not valid_cidr(ip):
            messagebox.showerror(
                "Invalid IP address",
                "Use an address like 192.168.1.64/24, or 'dhcp' to let the "
                "camera get its address automatically.")
            return None
        gateway = values.get("net_gateway", "")
        if gateway and not valid_cidr(gateway):
            messagebox.showerror("Invalid gateway",
                                 "The gateway must be an IPv4 address, "
                                 "e.g. 192.168.1.1.")
            return None
        return values

    def on_save_camera_config(self, announce=False):
        values = self._collect_camera_config()
        if values is None:
            return None
        self.sensor["config"].update(values)
        self.sensor["host"] = self.host_var.get().strip()
        self.store.save()
        if announce:
            self.log(f"Camera settings saved to project for "
                     f"'{self.sensor.get('name')}' (camera not opened).")
        return values

    def _camera_backend(self) -> str:
        var = self.cfg_vars.get("backend")
        return var.get() if var is not None else "auto"

    def on_probe_camera(self):
        """Open the camera briefly and report what it is."""
        if not self._require_cv2():
            return
        source = self.host_var.get().strip()
        if not source:
            messagebox.showerror("Probe camera",
                                 "Please enter a camera source.")
            return
        self.log(f"Probing camera {source} ...")
        backend = self._camera_backend()

        def work():
            reader = CameraReader(source, self.frame_queue, self.log,
                                  backend=backend)
            cap = None
            try:
                cap = reader.open_capture()
                info = reader.describe(cap)
            except Exception as e:
                self.log(f"ERROR probing camera: {e}")
                return
            finally:
                if cap is not None:
                    cap.release()
            self.log(f"Camera reachable: {info['width']}x{info['height']} @ "
                     f"{info['fps']:g} fps"
                     + (f" [{info['fourcc']}]" if info["fourcc"] else ""))
            self.frame_queue.put(("camera_info", info))

        threading.Thread(target=work, daemon=True).start()

    def on_pull_camera(self):
        """Read the camera's current settings into the form and project."""
        if not self._require_cv2():
            return
        source = self.host_var.get().strip()
        if not source:
            messagebox.showerror("Pull from camera",
                                 "Please enter a camera source.")
            return
        if self.reader is not None and isinstance(self.reader, CameraReader):
            # the preview owns the device; ask the reader for a read-back
            self.reader.apply_props({})
            self.log("Reading settings from the running preview ...")
            return
        self.log(f"Reading settings from camera {source} ...")
        backend = self._camera_backend()

        def work():
            reader = CameraReader(source, self.frame_queue, self.log,
                                  backend=backend)
            cap = None
            try:
                cap = reader.open_capture()
                props = CameraReader.read_props(cap)
                info = reader.describe(cap)
            except Exception as e:
                self.log(f"ERROR reading camera settings: {e}")
                return
            finally:
                if cap is not None:
                    cap.release()
            self.frame_queue.put(("camera_info", info))
            self.frame_queue.put(("camera_props", props))

        threading.Thread(target=work, daemon=True).start()

    def _finish_camera_pull(self, props: dict):
        for key, value in props.items():
            var = self.cfg_vars.get(key)
            if var is not None:
                var.set(value)
        self.sensor["config"].update(props)
        self.sensor["last_seen"] = now_stamp()
        self.store.save()
        self.log("Camera settings read back into the project: "
                 + ", ".join(f"{k}={v}" for k, v in sorted(props.items())))

    def on_push_camera(self):
        """Write the project's settings to the camera."""
        if not self._require_cv2():
            return
        values = self.on_save_camera_config()
        if values is None:
            return
        source = self.host_var.get().strip()
        if not source:
            messagebox.showerror("Push to camera",
                                 "Please enter a camera source.")
            return
        if self.reader is not None and isinstance(self.reader, CameraReader):
            # apply on the live capture, so the preview shows the result
            self.reader.apply_props(values)
            self.log("Settings sent to the running preview.")
            return
        self.log(f"Pushing settings to camera {source} ...")
        backend = self._camera_backend()

        def work():
            reader = CameraReader(source, self.frame_queue, self.log,
                                  backend=backend)
            cap = None
            try:
                cap = reader.open_capture()
                CameraReader.set_props(cap, values)
                # a capture usually only commits settings once it streams
                cap.read()
                kept = CameraReader.read_props(cap)
                info = reader.describe(cap)
            except Exception as e:
                self.log(f"ERROR pushing camera settings: {e}")
                return
            finally:
                if cap is not None:
                    cap.release()
            self.frame_queue.put(("camera_info", info))
            self.frame_queue.put(("camera_push", values, kept))

        threading.Thread(target=work, daemon=True).start()

    def _finish_camera_push(self, wanted: dict, kept: dict):
        ignored = []
        for key, value in wanted.items():
            if not value or key not in kept:
                continue
            try:
                same = abs(float(value) - float(kept[key])) < 1e-6
            except ValueError:
                same = str(value) == str(kept[key])
            if not same:
                ignored.append(f"{key}: asked {value}, got {kept[key]}")
        for key, value in kept.items():
            var = self.cfg_vars.get(key)
            if var is not None:
                var.set(value)
        self.sensor["config"].update(kept)
        self.sensor["last_seen"] = now_stamp()
        self.store.save()
        if ignored:
            self.log("Camera did not accept some settings -> "
                     + "; ".join(ignored))
        self.log("Camera settings pushed; the form now shows what the "
                 "camera kept.")

    def _show_camera_info(self, info: dict):
        if self.sensor is not None:
            self.sensor["last_seen"] = now_stamp()
            self.store.save()
        if self.info_var is None:
            return
        lines = [
            f"Source       : {info.get('source', '')}",
            f"Backend      : {info.get('backend', '')}",
            f"Resolution   : {info.get('width', 0)} x {info.get('height', 0)}",
            f"Frame rate   : {info.get('fps', 0.0):g} fps",
        ]
        if info.get("fourcc"):
            lines.append(f"Pixel format : {info['fourcc']}")
        if info.get("frames"):
            lines.append(f"Frames       : {info['frames']}")
        self.info_var.set("\n".join(lines))

    def on_start_preview(self, source_url=None, is_file=False):
        if not self._require_cv2():
            return
        if self.reader is not None:
            self.log("Preview already running.")
            return
        values = None if is_file else self.on_save_camera_config()
        if values is None and not is_file:
            return
        source = source_url or self.host_var.get().strip()
        if not source:
            messagebox.showerror("Start preview",
                                 "Please enter a camera source.")
            return
        self.reader = CameraReader(
            source, self.frame_queue, self.log,
            props=values or {}, backend=self._camera_backend(),
            is_file=is_file, loop=is_file and self.loop_var.get())
        self.reader.start()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def on_open_video(self):
        if not self._require_cv2():
            return
        path = filedialog.askopenfilename(
            title="Open video file",
            filetypes=[("Video files", "*.mp4 *.avi *.mkv *.mov *.webm"),
                       ("All files", "*")])
        if path:
            self.on_stop_stream()
            self.on_start_preview(source_url=path, is_file=True)

    def on_snapshot(self):
        if not self._require_cv2():
            return
        name = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (self.sensor or {}).get("name", "camera"))
        path = filedialog.asksaveasfilename(
            title="Save snapshot as", defaultextension=".png",
            initialfile=f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.png",
            filetypes=[("PNG image", "*.png"), ("JPEG image", "*.jpg")])
        if not path:
            return
        if self.reader is not None and isinstance(self.reader, CameraReader):
            self.reader.snapshot(path)      # grabbed by the capture thread
            return
        source = self.host_var.get().strip()
        backend = self._camera_backend()
        values = self.on_save_camera_config() or {}

        def work():
            reader = CameraReader(source, self.frame_queue, self.log,
                                  backend=backend)
            cap = None
            try:
                cap = reader.open_capture()
                CameraReader.set_props(cap, values)
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("no frame received")
                cv2.imwrite(path, frame)
                self.log(f"Snapshot saved -> {path}")
            except Exception as e:
                self.log(f"ERROR saving snapshot: {e}")
            finally:
                if cap is not None:
                    cap.release()

        threading.Thread(target=work, daemon=True).start()

    def on_toggle_camera_record(self):
        if not self._require_cv2():
            return
        reader = self.reader if isinstance(self.reader, CameraReader) else None
        if reader is not None and reader.recording:
            reader.stop_recording()
            self.record_btn.configure(text="●  Start Recording")
            return
        name = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (self.sensor or {}).get("name", "camera"))
        path = filedialog.asksaveasfilename(
            title="Save recording as", defaultextension=".mp4",
            initialfile=f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.mp4",
            filetypes=[("MP4 video", "*.mp4"), ("AVI video", "*.avi")])
        if not path:
            return
        if reader is None:
            # recording needs the capture thread; start the preview first
            self.log("Starting the preview so the camera can be recorded ...")
            self.on_start_preview()
            reader = self.reader if isinstance(self.reader,
                                               CameraReader) else None
            if reader is None:
                return
        reader.start_recording(path)
        self.record_btn.configure(text="■  Stop Recording")

    def on_open_second_view(self):
        """Mirror the live image into its own window (second monitor)."""
        name = (self.sensor or {}).get("name", "camera")
        window = CameraViewWindow(
            self, f"{name}  ·  view {len(self.view_windows) + 2}")
        self.view_windows.append(window)
        self.log(f"Opened an extra view window for '{name}' "
                 f"({len(self.view_windows)} open). Drag it to another "
                 "screen and press F11 for full screen.")
        if self.last_camera_frame is not None:
            window.show(*self.last_camera_frame)

    def _close_view_windows(self):
        for window in list(self.view_windows):
            window.close()
        self.view_windows = []

    def _draw_camera(self, rgb, index, recording=False):
        title = f"Frame {index}  ·  {rgb.shape[1]}x{rgb.shape[0]}"
        if recording:
            title += "   ● REC"
        self.last_camera_frame = (rgb, title)
        for window in list(self.view_windows):
            window.show(rgb, title)
        if self.canvas is None or getattr(self, "cam_ax", None) is None:
            return
        if self.cam_artist is None or \
                self.cam_artist.get_array().shape[:2] != rgb.shape[:2]:
            self.cam_ax.clear()
            self.cam_artist = self.cam_ax.imshow(rgb)
        else:
            self.cam_artist.set_data(rgb)
        self._style_axis(self.cam_ax, title)
        self.canvas.draw_idle()

    # ------------------------------------------------------- onvif actions ---
    def _require_onvif(self) -> bool:
        if not HAVE_ONVIF:
            messagebox.showerror(
                "onvif-zeep missing",
                "Network-camera settings need the onvif-zeep package "
                f"({ONVIF_IMPORT_ERROR}).\n\n"
                "Install it with:\n    pip install onvif-zeep")
            return False
        return True

    def _onvif_password(self, user: str):
        """Ask for the camera password once per session; never stored."""
        sensor_id = (self.sensor or {}).get("id", "")
        if sensor_id in self._onvif_passwords:
            return self._onvif_passwords[sensor_id]
        dialog = FormDialog(
            self.root, "Camera password",
            [{"key": "password", "label": f"Password for '{user}' on "
                                          f"{self._host()}",
              "kind": "password",
              "hint": "kept in memory for this session only"}],
            ok_text="Connect")
        if not dialog.result:
            return None
        password = dialog.result.get("password", "")
        self._onvif_passwords[sensor_id] = password
        return password

    def on_forget_onvif_password(self):
        sensor_id = (self.sensor or {}).get("id", "")
        if self._onvif_passwords.pop(sensor_id, None) is None:
            self.log("No password was being held for this camera.")
        else:
            self.log("Camera password forgotten.")

    def _onvif_connect_args(self):
        """(host, port, user, password) or None if the user cancelled."""
        values = self.on_save_camera_config()
        if values is None:
            return None
        host = self.host_var.get().strip()
        if not host or host.isdigit():
            messagebox.showerror(
                "ONVIF",
                "ONVIF settings apply to network cameras. Set the camera "
                "source to its address or RTSP URL first.")
            return None
        user = values.get("onvif_user", "")
        password = self._onvif_password(user)
        if password is None:
            return None
        return host, values.get("onvif_port", "80"), user, password, values

    def on_pull_onvif(self):
        if not self._require_onvif():
            return
        args = self._onvif_connect_args()
        if args is None:
            return
        host, port, user, password, _values = args
        self.log(f"Reading settings from {host} over ONVIF ...")

        def work():
            try:
                camera = OnvifCamera(host, port, user, password)
                settings = camera.read_settings()
            except Exception as e:
                self.log(f"ERROR reading over ONVIF: {e}")
                return
            self.frame_queue.put(("onvif_props", settings))

        threading.Thread(target=work, daemon=True).start()

    def _finish_onvif_pull(self, settings: dict):
        for key, value in settings.items():
            var = self.cfg_vars.get(key)
            if var is not None:
                var.set(value)
        self.sensor["config"].update(settings)
        self.sensor["last_seen"] = now_stamp()
        self.store.save()
        self.log("ONVIF settings pulled into the project: "
                 + ", ".join(f"{k}={v}" for k, v in sorted(settings.items())))

    def on_push_onvif(self):
        if not self._require_onvif():
            return
        args = self._onvif_connect_args()
        if args is None:
            return
        host, port, user, password, values = args
        wanted = {k: values.get(k, "") for k in
                  ("net_ip", "net_gateway", "mtu", "bitrate", "gop",
                   "encoding")}
        listed = [f"  {k} = {v}" for k, v in wanted.items()
                  if v and v != UNCHANGED]
        if not listed:
            messagebox.showerror("Push to camera (ONVIF)",
                                 "Nothing to push - fill in at least one of "
                                 "IP, gateway, MTU, bitrate, GOP or "
                                 "encoding.")
            return
        warning = ("\n\nWARNING: changing the IP drops the current "
                   "connection - reconnect on the new address."
                   if wanted["net_ip"] else "")
        if not messagebox.askyesno(
                "Push to camera (ONVIF)",
                f"Write these settings to {host}?\n\n"
                + "\n".join(listed) + warning):
            return
        self.log(f"Pushing settings to {host} over ONVIF ...")

        def work():
            try:
                camera = OnvifCamera(host, port, user, password)
                report = camera.write_settings(wanted)
            except Exception as e:
                self.log(f"ERROR pushing over ONVIF: {e}")
                return
            for line in report or ["nothing was applied"]:
                self.log(f"  {line}")
            self.sensor["last_seen"] = now_stamp()
            self.store.save()
            self.log("ONVIF push complete.")

        threading.Thread(target=work, daemon=True).start()

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
        """Record a line. Safe from any thread: nothing here touches Tk -
        the UI picks the line up on the next _poll_queue tick."""
        line = time.strftime("[%H:%M:%S] ") + msg + "\n"
        self.log_lines.append(line)
        self._log_pending.append((line, msg.splitlines()[0][:160]))

    def _flush_log(self):
        """Move pending log lines into the widget (UI thread only)."""
        while self._log_pending:
            line, status = self._log_pending.popleft()
            self.status_var.set(status)
            widget = self.log_widget
            if widget is None:
                continue
            try:
                widget.configure(state=tk.NORMAL)
                widget.insert(tk.END, line)
                widget.see(tk.END)
                widget.configure(state=tk.DISABLED)
            except tk.TclError:      # panel was destroyed meanwhile
                self.log_widget = None

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
        if host.isdigit():          # a USB camera index has no web page
            messagebox.showinfo(
                "Open in browser",
                f"'{host}' is a local camera device, not a network address, "
                "so it has no web page.")
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
            # a camera recording lives inside the capture thread
            button = getattr(self, "record_btn", None)
            if button is not None and self.sensor_kind() in (KIND_CAMERA,
                                                             KIND_IMU):
                try:
                    button.configure(text="●  Start Recording")
                except tk.TclError:
                    pass
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
        self._flush_log()
        try:
            while True:
                item = self.frame_queue.get_nowait()
                kind = item[0]
                if kind == "frame":
                    self._draw_frame(item[1], item[2])
                    if len(item) > 3:
                        self.last_frame_status = item[3]
                elif kind == "camera":
                    self._draw_camera(item[1], item[2],
                                      item[3] if len(item) > 3 else False)
                elif kind == "camera_info":
                    self._show_camera_info(item[1])
                elif kind == "camera_props":
                    self._finish_camera_pull(item[1])
                elif kind == "camera_push":
                    self._finish_camera_push(item[1], item[2])
                elif kind == "onvif_props":
                    self._finish_onvif_pull(item[1])
                elif kind == "radar":
                    self._draw_radar(item[1], item[2], item[3])
                elif kind == "radar_params":
                    self._finish_arbe_pull(item[1])
                elif kind == "imu":
                    self._draw_imu(item[1], item[2])
                elif kind == "imu_info":
                    self.log(f"Device: {item[1]}")
                elif kind == "imu_layout":
                    self._finish_imu_pull("layout", item[1])
                elif kind == "imu_params":
                    self._finish_imu_pull("params", item[1])
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
                if self.sensor_kind() == KIND_CAMERA:
                    self.on_save_camera_config()
                elif self.sensor_kind() == KIND_ARBE:
                    self.on_save_arbe_config()
                elif self.sensor_kind() == KIND_IMU:
                    self.on_save_imu_config()
                else:
                    self.on_save_config()
            except Exception:
                pass
        self.store.save()
        self.on_stop_stream()
        self._close_view_windows()
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
