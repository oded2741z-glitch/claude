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
  * Publish live ROS 2 topics (sensor_msgs/PointCloud2) while streaming
    or playing back, for RViz2 / any ROS 2 node (requires ROS 2 + rclpy)
  * Open USB / IP cameras (OpenCV) in floating live-view windows, take
    snapshots, and optionally publish frames as ROS 2 sensor_msgs/Image
  * Camera-Lidar Fusion: overlay the live point cloud on a camera image
    (colored by depth) to tune the extrinsic calibration, save/load the
    calibration as JSON, and publish a colored point cloud to ROS 2
  * Built-in checkerboard intrinsics calibration wizard: capture views of
    a printed chessboard and cv2.calibrateCamera fills fx/fy/cx/cy + the
    distortion coefficients automatically

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

__version__ = "1.4.0"

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

# --- ROS 2 (optional: only needed for live topic publishing) -----------------
try:
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import PointCloud2, PointField
    from sensor_msgs.msg import Image as RosImage
    HAVE_ROS = True
    ROS_IMPORT_ERROR = None
except Exception as _e:
    HAVE_ROS = False
    ROS_IMPORT_ERROR = _e

# --- OpenCV (optional: only needed for the CAMERAS panel) ---------------------
try:
    import cv2
    HAVE_CV2 = True
    CV2_IMPORT_ERROR = None
except Exception as _e:
    cv2 = None
    HAVE_CV2 = False
    CV2_IMPORT_ERROR = _e

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

# --- camera-lidar fusion geometry --------------------------------------------
# lidar frame (x forward, y left, z up) -> camera optical frame
# (x right, y down, z forward)
LIDAR_TO_OPTICAL = np.array([[0., -1., 0.],
                             [0., 0., -1.],
                             [1., 0., 0.]])

# calibration parameters, in UI / JSON order
CALIB_KEYS = ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3",
              "tx", "ty", "tz", "yaw", "pitch", "roll")


def euler_deg_to_matrix(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """ZYX rotation matrix from angles in degrees (in the lidar frame)."""
    y, p, r = np.radians([yaw, pitch, roll])
    cy, sy = np.cos(y), np.sin(y)
    cp, sp = np.cos(p), np.sin(p)
    cr, sr = np.cos(r), np.sin(r)
    rz = np.array([[cy, -sy, 0.], [sy, cy, 0.], [0., 0., 1.]])
    ry = np.array([[cp, 0., sp], [0., 1., 0.], [-sp, 0., cp]])
    rx = np.array([[1., 0., 0.], [0., cr, -sr], [0., sr, cr]])
    return rz @ ry @ rx


def checkerboard_object_points(cols: int, rows: int,
                               square: float) -> np.ndarray:
    """3D coordinates of a checkerboard's inner corners (z=0 plane),
    spaced `square` meters apart - the reference cv2.calibrateCamera
    compares detected image corners against."""
    objp = np.zeros((cols * rows, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square
    return objp


def project_cloud(xyz, K, dist, rot, trans, img_w, img_h):
    """Project lidar-frame points into a camera image.

    rot / trans: the camera's orientation (rotation matrix, lidar frame;
    identity = facing the lidar's +X axis, upright) and position (meters,
    lidar frame). Returns (indices into xyz, u, v, depth) for the points
    that land inside a img_w x img_h image.
    """
    pts = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    cam = ((pts - trans) @ rot) @ LIDAR_TO_OPTICAL.T
    idx = np.nonzero(cam[:, 2] > 0.05)[0]     # points in front of the camera
    if idx.size == 0:
        empty = np.empty(0, int)
        return empty, empty, empty, np.empty(0)
    cam = cam[idx]
    uv, _ = cv2.projectPoints(cam.reshape(-1, 1, 3), np.zeros(3),
                              np.zeros(3), K, dist)
    uv = uv.reshape(-1, 2)
    finite = np.isfinite(uv).all(axis=1)
    uv = np.where(finite[:, None], uv, -1.0)
    u = np.round(uv[:, 0]).astype(int)
    v = np.round(uv[:, 1]).astype(int)
    ok = finite & (u >= 0) & (u < img_w) & (v >= 0) & (v < img_h)
    return idx[ok], u[ok], v[ok], cam[ok, 2]


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


class RosPublisher:
    """Publishes lidar frames as ROS 2 sensor_msgs/PointCloud2 messages
    (plus camera frames as sensor_msgs/Image, one publisher per topic).

    Runs on its own rclpy context so it can be started and stopped freely
    while the GUI lives on. Publishing needs no spinning, so no executor
    thread is required.
    """

    def __init__(self, topic: str, frame_id: str):
        self.topic = topic
        self.frame_id = frame_id
        self.n_published = 0
        self.n_images = 0
        self.n_rgb = 0
        self._img_pubs = {}
        self._cloud_pubs = {}
        self._lock = threading.Lock()
        self._closed = False
        self._context = rclpy.Context()
        rclpy.init(args=None, context=self._context)
        self._node = rclpy.create_node("ouster_lidar_gui",
                                       context=self._context)
        self._pub = self._node.create_publisher(
            PointCloud2, topic, qos_profile_sensor_data)

    def publish_points(self, pts: np.ndarray, sensor_ts_ns: int = 0):
        """pts: float32 array of shape (N, 4) -> x, y, z, intensity."""
        pts = np.ascontiguousarray(pts, dtype=np.float32)
        f32 = PointField.FLOAT32
        msg = PointCloud2()
        msg.header.frame_id = self.frame_id
        msg.fields = [
            PointField(name=n, offset=o, datatype=f32, count=1)
            for n, o in (("x", 0), ("y", 4), ("z", 8), ("intensity", 12))]
        msg.height = 1
        msg.width = int(pts.shape[0])
        msg.is_bigendian = False
        msg.point_step = 16
        msg.row_step = 16 * int(pts.shape[0])
        msg.is_dense = True
        msg.data = pts.tobytes()
        with self._lock:
            if self._closed:
                return
            self._set_stamp(msg, sensor_ts_ns)
            self._pub.publish(msg)
            self.n_published += 1

    def _set_stamp(self, msg, sensor_ts_ns):
        """Sensor timestamp when usable, else ROS clock. Call under lock."""
        if 0 < sensor_ts_ns < 2**63:
            sec, nsec = divmod(int(sensor_ts_ns), 1_000_000_000)
            if sec < 2**31:
                msg.header.stamp.sec = sec
                msg.header.stamp.nanosec = nsec
                return
        msg.header.stamp = self._node.get_clock().now().to_msg()

    def publish_points_rgb(self, topic: str, frame_id: str, xyz: np.ndarray,
                           bgr_colors: np.ndarray, sensor_ts_ns: int = 0):
        """Publish a colored cloud: xyz (N, 3) + per-point BGR uint8 colors.

        Colors are packed in the PCL convention: uint32 0x00RRGGBB stored
        in a FLOAT32 field named 'rgb' (what RViz2 / Foxglove expect).
        """
        xyz = np.ascontiguousarray(xyz, dtype=np.float32).reshape(-1, 3)
        c = np.asarray(bgr_colors, dtype=np.uint32).reshape(-1, 3)
        rgb_f = ((c[:, 2] << 16) | (c[:, 1] << 8) | c[:, 0]).astype(
            np.uint32).view(np.float32).reshape(-1, 1)
        pts = np.hstack([xyz, rgb_f])
        f32 = PointField.FLOAT32
        msg = PointCloud2()
        msg.header.frame_id = frame_id
        msg.fields = [
            PointField(name=n, offset=o, datatype=f32, count=1)
            for n, o in (("x", 0), ("y", 4), ("z", 8), ("rgb", 12))]
        msg.height = 1
        msg.width = int(pts.shape[0])
        msg.is_bigendian = False
        msg.point_step = 16
        msg.row_step = 16 * int(pts.shape[0])
        msg.is_dense = True
        msg.data = pts.tobytes()
        with self._lock:
            if self._closed:
                return
            pub = self._cloud_pubs.get(topic)
            if pub is None:
                pub = self._node.create_publisher(
                    PointCloud2, topic, qos_profile_sensor_data)
                self._cloud_pubs[topic] = pub
            self._set_stamp(msg, sensor_ts_ns)
            pub.publish(msg)
            self.n_rgb += 1

    def publish_image(self, topic: str, frame_id: str, img: np.ndarray):
        """Publish a camera frame (OpenCV BGR / BGRA / grayscale ndarray)."""
        img = np.ascontiguousarray(img)
        if img.dtype != np.uint8:
            img = img.astype(np.uint8)
        h, w = img.shape[:2]
        ch = 1 if img.ndim == 2 else int(img.shape[2])
        encoding = {1: "mono8", 3: "bgr8", 4: "bgra8"}.get(ch)
        if encoding is None:
            return
        msg = RosImage()
        msg.header.frame_id = frame_id
        msg.height = int(h)
        msg.width = int(w)
        msg.encoding = encoding
        msg.is_bigendian = 0
        msg.step = int(w) * ch
        msg.data = img.tobytes()
        with self._lock:
            if self._closed:
                return
            pub = self._img_pubs.get(topic)
            if pub is None:
                pub = self._node.create_publisher(
                    RosImage, topic, qos_profile_sensor_data)
                self._img_pubs[topic] = pub
            msg.header.stamp = self._node.get_clock().now().to_msg()
            pub.publish(msg)
            self.n_images += 1

    def close(self) -> int:
        """Tear down the node/context; returns how many clouds were sent."""
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._node.destroy_node()
                except Exception:
                    pass
                try:
                    rclpy.shutdown(context=self._context)
                except Exception:
                    pass
            return self.n_published


class ScanReader(threading.Thread):
    """Background thread that reads frames from a sensor or a recorded file
    and pushes the latest destaggered field images into a queue."""

    def __init__(self, source_url: str, out_queue: queue.Queue, log_fn,
                 is_file: bool = False, loop: bool = False,
                 ros_pub_fn=None, fusion_fn=None):
        super().__init__(daemon=True)
        self.source_url = source_url
        self.out_queue = out_queue
        self.log = log_fn
        self.is_file = is_file
        self.loop = loop
        self.ros_pub_fn = ros_pub_fn   # callable -> RosPublisher or None
        self.fusion_fn = fusion_fn     # callable -> FusionWindow or None
        self._xyzlut = None
        self._ros_err_logged = False
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
                    self._handle_cloud(frame)
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

    def _handle_cloud(self, frame):
        """Compute the frame's XYZ once and feed the active consumers:
        the ROS point-cloud publisher and/or the fusion window."""
        ros = self.ros_pub_fn() if self.ros_pub_fn is not None else None
        fus = self.fusion_fn() if self.fusion_fn is not None else None
        if ros is None and fus is None:
            return
        try:
            if self._xyzlut is None:
                info = getattr(frame, "sensor_info", None) or self.metadata
                if info is None:
                    return
                self._xyzlut = ouster_core.XYZLut(info, use_extrinsics=False)
            rng = frame.field(ouster_core.ChanField.RANGE)
            xyz = self._xyzlut(rng).astype(np.float32).reshape(-1, 3)
            try:
                inten = frame.field(ouster_core.ChanField.SIGNAL)
            except Exception:
                inten = rng
            inten = inten.astype(np.float32).reshape(-1, 1)
            mask = (rng.reshape(-1) > 0) & np.isfinite(xyz).all(1)
            ts = 0
            try:
                ts_arr = np.asarray(frame.timestamp)
                if ts_arr.size:
                    ts = int(ts_arr.max())
            except Exception:
                pass
            pts_xyz = xyz[mask]
            if ros is not None:
                ros.publish_points(np.hstack([pts_xyz, inten[mask]]), ts)
            if fus is not None:
                fus.on_cloud(pts_xyz, ts)
            self._ros_err_logged = False
        except Exception as e:
            if not self._ros_err_logged:
                self._ros_err_logged = True
                self.log(f"Cloud processing error: {e}")

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


def bgr_to_photo(bgr: np.ndarray, max_w: int, max_h: int):
    """OpenCV image -> tk.PhotoImage scaled to fit (aspect ratio kept).
    Rendered through Tk's native PPM support - no Pillow needed."""
    h, w = bgr.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        bgr = cv2.resize(bgr, (max(int(w * scale), 1),
                               max(int(h * scale), 1)),
                         interpolation=cv2.INTER_AREA)
    if bgr.ndim == 2:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    ph, pw = rgb.shape[:2]
    ppm = b"P6\n%d %d\n255\n" % (pw, ph) + np.ascontiguousarray(rgb).tobytes()
    return tk.PhotoImage(data=ppm)


class CameraReader(threading.Thread):
    """Background thread that grabs frames from a local (USB) or IP camera
    via OpenCV and hands the freshest one to the GUI - and to ROS 2, when
    image publishing is enabled for this camera."""

    def __init__(self, source, out_queue: queue.Queue, log_fn,
                 ros_pub_fn=None, ros_topic="", frame_id="camera"):
        super().__init__(daemon=True)
        self.source = source            # int device index or URL string
        self.out_queue = out_queue
        self.log = log_fn
        self.ros_pub_fn = ros_pub_fn    # callable -> RosPublisher or None
        self.ros_topic = ros_topic
        self.frame_id = frame_id
        self.ros_enabled = False        # toggled from the GUI checkbox
        self.n_frames = 0
        self._ros_err_logged = False
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        cap = None
        try:
            self.log(f"Opening camera '{self.source}' ...")
            cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                raise RuntimeError(
                    f"could not open camera '{self.source}' (device busy, "
                    "wrong index, or unreachable URL)")
            self.log(f"Camera '{self.source}' opened.")
            misses = 0
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    misses += 1
                    if misses > 25:
                        raise RuntimeError("camera stopped delivering "
                                           "frames")
                    time.sleep(0.1)
                    continue
                misses = 0
                self.n_frames += 1
                self._publish_ros(frame)
                # keep only the freshest frame for the GUI (frames are the
                # only event this loop emits, so draining drops nothing else)
                try:
                    while True:
                        self.out_queue.get_nowait()
                except queue.Empty:
                    pass
                self.out_queue.put(("frame", frame))
        except Exception as e:
            self.out_queue.put(("error", str(e)))
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            self.out_queue.put(("stopped", None))

    def _publish_ros(self, frame):
        pub = self.ros_pub_fn() if self.ros_pub_fn is not None else None
        if pub is None or not self.ros_enabled:
            return
        try:
            pub.publish_image(self.ros_topic, self.frame_id, frame)
            self._ros_err_logged = False
        except Exception as e:
            if not self._ros_err_logged:
                self._ros_err_logged = True
                self.log(f"ROS image publish error: {e}")


class CameraWindow:
    """Floating window with one camera's live view, a snapshot button and
    an optional ROS 2 image-publishing toggle."""

    counter = 0     # numbers cameras across the whole session

    def __init__(self, app, source_text: str):
        CameraWindow.counter += 1
        self.app = app
        self.n = CameraWindow.counter
        src = source_text.strip()
        source = int(src) if src.isdigit() else src
        self.ros_topic = f"/camera{self.n}/image_raw"
        self.frame_q = queue.Queue(maxsize=2)
        self.last_bgr = None
        self._photo = None              # PhotoImage ref (else Tk drops it)
        self._fps_t0 = time.time()
        self._fps_n0 = 0
        self._closed = False

        self.win = tk.Toplevel(app.root)
        self.win.title(f"Camera {self.n}  ·  {src}")
        self.win.geometry("660x560")
        self.win.configure(bg=Theme.BG)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self.video = tk.Label(self.win, bg="black")
        self.video.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        bar = ttk.Frame(self.win, style="Panel.TFrame", padding=6)
        bar.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.status_var = tk.StringVar(value="Connecting ...")
        ttk.Label(bar, textvariable=self.status_var,
                  style="Muted.TLabel").pack(side=tk.LEFT)
        self.ros_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text=f"Publish to ROS 2 ({self.ros_topic})",
                        variable=self.ros_var, command=self.on_ros_toggle,
                        style="TCheckbutton").pack(side=tk.LEFT, padx=10)
        ttk.Button(bar, text="Close",
                   command=self.close).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Snapshot...",
                   command=self.on_snapshot).pack(side=tk.RIGHT, padx=6)

        self.reader = CameraReader(
            source, self.frame_q, app.log,
            ros_pub_fn=lambda: app.ros_pub,
            ros_topic=self.ros_topic, frame_id=f"camera{self.n}")
        self.reader.start()
        self._poll()

    def on_ros_toggle(self):
        on = self.ros_var.get()
        self.reader.ros_enabled = on
        if not on:
            self.app.log(f"Camera {self.n}: ROS publishing off.")
        elif self.app.ros_pub is None:
            self.app.log(f"Camera {self.n}: will publish on "
                         f"{self.ros_topic} once ROS publishing is started "
                         "(ROS 2 TOPICS panel).")
        else:
            self.app.log(f"Camera {self.n}: publishing sensor_msgs/Image "
                         f"on {self.ros_topic}.")

    def on_snapshot(self):
        if self.last_bgr is None:
            messagebox.showinfo("Snapshot", "No frame received yet.",
                                parent=self.win)
            return
        path = filedialog.asksaveasfilename(
            title="Save snapshot as", defaultextension=".png",
            initialfile=(f"camera{self.n}_"
                         f"{time.strftime('%Y%m%d_%H%M%S')}.png"),
            filetypes=[("PNG image", "*.png"), ("JPEG image", "*.jpg")],
            parent=self.win)
        if not path:
            return
        try:
            cv2.imwrite(path, self.last_bgr)
            self.app.log(f"Snapshot saved -> {path}")
        except Exception as e:
            messagebox.showerror("Snapshot",
                                 f"Could not save the snapshot:\n{e}",
                                 parent=self.win)

    def _poll(self):
        if self._closed:
            return
        frame = None
        try:
            while True:
                kind, payload = self.frame_q.get_nowait()
                if kind == "frame":
                    frame = payload
                elif kind == "error":
                    self.app.log(f"CAMERA {self.n} ERROR: {payload}")
                    self.status_var.set(f"Error: {payload}")
                # "stopped": keep the window open so the error stays visible
        except queue.Empty:
            pass
        if frame is not None:
            self.last_bgr = frame
            self._show(frame)
        self.win.after(40, self._poll)      # ~25 fps display refresh

    def _show(self, bgr):
        h, w = bgr.shape[:2]
        self._photo = bgr_to_photo(bgr,
                                   max(self.video.winfo_width(), 64),
                                   max(self.video.winfo_height(), 64))
        self.video.configure(image=self._photo)
        now = time.time()
        if now - self._fps_t0 >= 1.0:
            n = self.reader.n_frames
            fps = (n - self._fps_n0) / (now - self._fps_t0)
            self._fps_t0, self._fps_n0 = now, n
            self.status_var.set(f"{w}x{h}  ·  {fps:.1f} fps")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.reader.stop()
        try:
            self.win.destroy()
        except Exception:
            pass
        self.app.camera_windows = [c for c in self.app.camera_windows
                                   if c is not self]


class FusionWindow:
    """Camera-Lidar Fusion: overlays the live point cloud (colored by
    depth) on a camera image so the extrinsic calibration can be tuned by
    eye, and optionally publishes the resulting colored point cloud to
    ROS 2.

    Calibration model: intrinsics fx/fy/cx/cy + distortion k1,k2,p1,p2,k3
    (OpenCV convention); extrinsics are the camera's position (tx,ty,tz,
    meters, lidar frame) and orientation (yaw/pitch/roll, degrees), where
    0/0/0 means the camera faces the lidar's +X axis, upright.
    """

    _CALIB_DEFAULTS = {"fx": 600.0, "fy": 600.0, "cx": 320.0, "cy": 240.0}
    _CALIB_LABELS = {"tx": "tx [m]", "ty": "ty [m]", "tz": "tz [m]",
                     "yaw": "yaw [°]", "pitch": "pitch [°]",
                     "roll": "roll [°]"}

    def __init__(self, app):
        self.app = app
        self._closed = False
        self.latest_cloud = None      # (xyz float32 (N,3), sensor ts ns)
        self._calib_cache = None      # (K, dist, rot, trans) numpy arrays
        self._cam_ref = None          # currently selected CameraWindow
        self._cam_list = []
        self._pub_rgb = False         # mirrored for the reader thread
        self._rgb_topic = "/ouster/points_rgb"
        self._lidar_frame = "ouster"
        self._rgb_err_logged = False
        self._photo = None
        self._wizard = None

        self.win = tk.Toplevel(app.root)
        self.win.title("Camera-Lidar Fusion")
        self.win.geometry("1150x720")
        self.win.configure(bg=Theme.BG)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        body = ttk.Frame(self.win, padding=8)
        body.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(body, width=330)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left.pack_propagate(False)
        right = ttk.Frame(body, style="Panel.TFrame")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- camera picker ---------------------------------------------------
        camf = ttk.LabelFrame(left, text="  CAMERA  ", padding=8)
        camf.pack(fill=tk.X, pady=3)
        self.cam_var = tk.StringVar()
        self.cam_combo = ttk.Combobox(camf, textvariable=self.cam_var,
                                      state="readonly", values=[])
        self.cam_combo.pack(fill=tk.X)
        ttk.Label(camf, text="Cameras opened in the CAMERAS panel appear "
                            "here.",
                  style="Hint.TLabel").pack(anchor=tk.W, pady=(3, 0))

        # --- calibration -----------------------------------------------------
        cal = ttk.LabelFrame(left, text="  CALIBRATION  ", padding=8)
        cal.pack(fill=tk.X, pady=3)
        stored = dict(self._CALIB_DEFAULTS)
        stored.update(app.settings.get("fusion_calib", {}) or {})
        self.calib_vars = {}
        for i, key in enumerate(CALIB_KEYS):
            r, c = divmod(i, 3)
            ttk.Label(cal, text=self._CALIB_LABELS.get(key, key),
                      style="Muted.TLabel").grid(row=r, column=2 * c,
                                                 sticky=tk.W, pady=1)
            var = tk.StringVar(value=str(stored.get(key, 0.0)))
            self.calib_vars[key] = var
            ttk.Entry(cal, textvariable=var, width=8).grid(
                row=r, column=2 * c + 1, padx=(2, 8), pady=1)
        ttk.Label(cal, text="yaw/pitch/roll 0/0/0 = camera facing the "
                            "lidar's +X axis,\nupright. Tune until the "
                            "overlay hugs the image edges.",
                  style="Hint.TLabel").grid(row=5, column=0, columnspan=6,
                                            sticky=tk.W, pady=(4, 2))
        btns = ttk.Frame(cal, style="Panel.TFrame")
        btns.grid(row=6, column=0, columnspan=6, sticky=tk.EW, pady=(2, 0))
        ttk.Button(btns, text="Load...",
                   command=self.on_load).pack(side=tk.LEFT, expand=True,
                                              fill=tk.X, padx=(0, 3))
        ttk.Button(btns, text="Save...",
                   command=self.on_save).pack(side=tk.LEFT, expand=True,
                                              fill=tk.X, padx=(3, 0))
        init = ttk.Frame(cal, style="Panel.TFrame")
        init.grid(row=7, column=0, columnspan=6, sticky=tk.EW, pady=(4, 0))
        ttk.Label(init, text="HFOV [°]:",
                  style="Muted.TLabel").pack(side=tk.LEFT)
        self.hfov_var = tk.StringVar(value="70")
        ttk.Entry(init, textvariable=self.hfov_var,
                  width=5).pack(side=tk.LEFT, padx=4)
        ttk.Button(init, text="Init intrinsics from camera",
                   command=self.on_init_intrinsics).pack(side=tk.LEFT,
                                                         expand=True,
                                                         fill=tk.X)
        ttk.Button(cal, text="♟  Calibrate intrinsics (checkerboard)...",
                   command=self.on_calibrate_wizard).grid(
            row=8, column=0, columnspan=6, sticky=tk.EW, pady=(4, 0))

        # --- overlay view settings -------------------------------------------
        view = ttk.LabelFrame(left, text="  OVERLAY  ", padding=8)
        view.pack(fill=tk.X, pady=3)
        row = ttk.Frame(view, style="Panel.TFrame")
        row.pack(fill=tk.X)
        ttk.Label(row, text="Max depth [m]:",
                  style="Muted.TLabel").pack(side=tk.LEFT)
        self.depth_var = tk.StringVar(value="30")
        ttk.Entry(row, textvariable=self.depth_var,
                  width=5).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(row, text="Point size:",
                  style="Muted.TLabel").pack(side=tk.LEFT)
        self.psize_var = tk.StringVar(value="2")
        ttk.Combobox(row, textvariable=self.psize_var,
                     values=["1", "2", "3"], state="readonly",
                     width=3).pack(side=tk.LEFT, padx=4)
        ttk.Label(view, text="Points are colored by distance "
                            "(near = red, far = blue).",
                  style="Hint.TLabel").pack(anchor=tk.W, pady=(3, 0))

        # --- ROS colored cloud -----------------------------------------------
        rosf = ttk.LabelFrame(left, text="  ROS 2 COLORED CLOUD  ",
                              padding=8)
        rosf.pack(fill=tk.X, pady=3)
        self.rgb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rosf, text="Publish colored cloud",
                        variable=self.rgb_var,
                        style="TCheckbutton").pack(anchor=tk.W)
        self.rgb_topic_var = tk.StringVar(value="/ouster/points_rgb")
        ttk.Entry(rosf, textvariable=self.rgb_topic_var).pack(fill=tk.X,
                                                              pady=3)
        ttk.Label(rosf, text="PointCloud2 with x,y,z,rgb - only points "
                             "inside the\ncamera image. Needs ROS "
                             "publishing ON and a stream.",
                  style="Hint.TLabel").pack(anchor=tk.W)

        # --- overlay display -------------------------------------------------
        self.view = tk.Label(right, bg="black")
        self.view.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 2))
        self.status_var = tk.StringVar(
            value="Open a camera and start a lidar stream to see the "
                  "overlay.")
        ttk.Label(right, textvariable=self.status_var,
                  style="Muted.TLabel").pack(anchor=tk.W, padx=8,
                                             pady=(0, 6))

        self._poll()

    # ------------------------------------------------------------- calib ----
    def _calib_values(self):
        """Current calibration as {key: float}, or None if a field is not
        a valid number."""
        vals = {}
        for key, var in self.calib_vars.items():
            try:
                vals[key] = float(var.get())
            except (ValueError, tk.TclError):
                return None
        return vals

    def _rebuild_calib(self):
        vals = self._calib_values()
        if vals is None:
            self.status_var.set("Invalid number in a calibration field.")
            return
        K = np.array([[vals["fx"], 0., vals["cx"]],
                      [0., vals["fy"], vals["cy"]],
                      [0., 0., 1.]])
        dist = np.array([vals["k1"], vals["k2"], vals["p1"], vals["p2"],
                         vals["k3"]])
        rot = euler_deg_to_matrix(vals["yaw"], vals["pitch"], vals["roll"])
        trans = np.array([vals["tx"], vals["ty"], vals["tz"]])
        self._calib_cache = (K, dist, rot, trans)

    def on_save(self):
        vals = self._calib_values()
        if vals is None:
            messagebox.showerror("Save calibration",
                                 "Fix the invalid calibration fields "
                                 "first.", parent=self.win)
            return
        path = filedialog.asksaveasfilename(
            title="Save calibration as", defaultextension=".json",
            initialfile="camera_lidar_calib.json",
            filetypes=[("JSON files", "*.json")], parent=self.win)
        if not path:
            return
        data = {
            "intrinsics": {k: vals[k] for k in ("fx", "fy", "cx", "cy")},
            "distortion": [vals[k] for k in ("k1", "k2", "p1", "p2", "k3")],
            "extrinsics": {
                "translation_m": [vals[k] for k in ("tx", "ty", "tz")],
                "rotation_deg": [vals[k] for k in ("yaw", "pitch", "roll")],
            },
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.app.log(f"Calibration saved -> {path}")
        except OSError as e:
            messagebox.showerror("Save calibration",
                                 f"Could not save:\n{e}", parent=self.win)

    def on_load(self):
        path = filedialog.askopenfilename(
            title="Load calibration", filetypes=[("JSON files", "*.json"),
                                                 ("All files", "*")],
            parent=self.win)
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            vals = {}
            if "intrinsics" in data:            # nested format (ours)
                vals.update(data.get("intrinsics", {}))
                dist = data.get("distortion", [])
                for i, k in enumerate(("k1", "k2", "p1", "p2", "k3")):
                    if i < len(dist):
                        vals[k] = dist[i]
                ext = data.get("extrinsics", {})
                for i, k in enumerate(("tx", "ty", "tz")):
                    t = ext.get("translation_m", [])
                    if i < len(t):
                        vals[k] = t[i]
                for i, k in enumerate(("yaw", "pitch", "roll")):
                    r = ext.get("rotation_deg", [])
                    if i < len(r):
                        vals[k] = r[i]
            else:                               # flat {key: value}
                vals.update(data)
            applied = 0
            for key in CALIB_KEYS:
                if key in vals:
                    self.calib_vars[key].set(str(float(vals[key])))
                    applied += 1
            self.app.log(f"Calibration loaded ({applied} fields) <- {path}")
        except Exception as e:
            messagebox.showerror("Load calibration",
                                 f"Could not load:\n{e}", parent=self.win)

    def on_init_intrinsics(self):
        """Rough intrinsics from the camera's frame size + a guessed HFOV."""
        frame = getattr(self._cam_ref, "last_bgr", None) \
            if self._cam_ref else None
        if frame is None:
            messagebox.showinfo(
                "Init intrinsics",
                "Select an open camera (with a live frame) first.",
                parent=self.win)
            return
        try:
            hfov = float(self.hfov_var.get())
            if not (10 <= hfov <= 170):
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Init intrinsics",
                                 "HFOV must be a number between 10 and "
                                 "170 degrees.", parent=self.win)
            return
        h, w = frame.shape[:2]
        f = (w / 2.0) / np.tan(np.radians(hfov) / 2.0)
        for key, val in (("fx", f), ("fy", f), ("cx", w / 2.0),
                         ("cy", h / 2.0), ("k1", 0.0), ("k2", 0.0),
                         ("p1", 0.0), ("p2", 0.0), ("k3", 0.0)):
            self.calib_vars[key].set(f"{val:.1f}" if key in
                                     ("fx", "fy", "cx", "cy") else "0.0")
        self.app.log(f"Intrinsics initialized from {w}x{h} frame, "
                     f"HFOV {hfov:g}° (fx=fy={f:.1f}). This is a starting "
                     "guess - calibrate with a checkerboard for accuracy.")

    def on_calibrate_wizard(self):
        """Open the checkerboard intrinsics-calibration wizard."""
        frame = getattr(self._cam_ref, "last_bgr", None) \
            if self._cam_ref else None
        if frame is None:
            messagebox.showinfo(
                "Calibrate intrinsics",
                "Open a camera (CAMERAS panel), select it above, and wait "
                "for a live frame first.", parent=self.win)
            return
        if self._wizard is not None and not self._wizard._closed:
            self._wizard.win.lift()
            return
        self._wizard = IntrinsicsWizard(self)
        self.app.log("Intrinsics wizard opened: show a printed "
                     "checkerboard to the camera, capture ~15 varied "
                     "views, then Calibrate & Apply.")

    # -------------------------------------------------------------- data ----
    def on_cloud(self, xyz, sensor_ts_ns=0):
        """Called from the ScanReader thread with each frame's points."""
        if self._closed:
            return
        self.latest_cloud = (xyz, sensor_ts_ns)
        if not self._pub_rgb:
            return
        pub = self.app.ros_pub
        calib = self._calib_cache
        cam = self._cam_ref
        frame = getattr(cam, "last_bgr", None) if cam is not None else None
        if pub is None or calib is None or frame is None:
            return
        try:
            if frame.ndim == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            idx, u, v, _d = project_cloud(xyz, *calib,
                                          frame.shape[1], frame.shape[0])
            if idx.size == 0:
                return
            pub.publish_points_rgb(self._rgb_topic, self._lidar_frame,
                                   xyz[idx], frame[v, u, :3], sensor_ts_ns)
            self._rgb_err_logged = False
        except Exception as e:
            if not self._rgb_err_logged:
                self._rgb_err_logged = True
                self.app.log(f"Colored-cloud publish error: {e}")

    # ---------------------------------------------------------- rendering ----
    def _refresh_cameras(self):
        cams = list(self.app.camera_windows)
        values = [f"Camera {c.n}  ({c.reader.source})" for c in cams]
        if list(self.cam_combo.cget("values")) != values:
            self.cam_combo.configure(values=values)
        if self.cam_var.get() not in values:
            self.cam_var.set(values[0] if values else "")
        self._cam_list = cams
        sel = self.cam_var.get()
        self._cam_ref = (cams[values.index(sel)]
                         if sel in values else None)

    def _poll(self):
        if self._closed:
            return
        self._refresh_cameras()
        self._rebuild_calib()
        self._pub_rgb = bool(self.rgb_var.get())
        self._rgb_topic = (self.rgb_topic_var.get().strip()
                           or "/ouster/points_rgb")
        self._lidar_frame = (self.app.ros_frame_var.get().strip()
                             or "ouster")
        self._render()
        self.win.after(100, self._poll)     # ~10 fps overlay refresh

    def _render(self):
        frame = getattr(self._cam_ref, "last_bgr", None) \
            if self._cam_ref else None
        if frame is None:
            self.status_var.set("Open a camera (CAMERAS panel) and select "
                                "it above to see the overlay.")
            return
        img = frame.copy()
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        n_in = n_total = 0
        cloud = self.latest_cloud
        if cloud is not None and self._calib_cache is not None:
            xyz, _ts = cloud
            n_total = xyz.shape[0]
            if n_total > 60000:                 # keep the overlay snappy
                xyz = xyz[::n_total // 60000 + 1]
            idx, u, v, depth = project_cloud(xyz, *self._calib_cache,
                                             img.shape[1], img.shape[0])
            n_in = idx.size
            if n_in:
                self._draw_points(img, u, v, depth)
        self._photo = bgr_to_photo(img,
                                   max(self.view.winfo_width(), 64),
                                   max(self.view.winfo_height(), 64))
        self.view.configure(image=self._photo)
        if cloud is None:
            self.status_var.set("Camera OK - start a lidar stream (or "
                                "playback) to overlay points.")
        else:
            self.status_var.set(f"{n_in} of {xyz.shape[0]} sampled points "
                                "land inside the image"
                                + (f"  ·  cloud size {n_total}"
                                   if n_total else ""))

    def _draw_points(self, img, u, v, depth):
        try:
            max_d = max(float(self.depth_var.get()), 0.1)
        except (ValueError, tk.TclError):
            max_d = 30.0
        try:
            psize = max(int(self.psize_var.get()), 1)
        except (ValueError, tk.TclError):
            psize = 2
        d = np.clip(depth / max_d, 0.0, 1.0)
        cmap = cv2.applyColorMap(((1.0 - d) * 255).astype(np.uint8)
                                 .reshape(-1, 1), cv2.COLORMAP_TURBO)
        colors = cmap.reshape(-1, 3)
        h, w = img.shape[:2]
        for dy in range(psize):
            for dx in range(psize):
                img[np.clip(v + dy, 0, h - 1),
                    np.clip(u + dx, 0, w - 1)] = colors

    # ------------------------------------------------------------- close ----
    def close(self):
        if self._closed:
            return
        self._closed = True
        vals = self._calib_values()
        if vals is not None:
            self.app.settings["fusion_calib"] = vals
            self.app._save_settings()
        if self.app.fusion_win is self:
            self.app.fusion_win = None
        if self._wizard is not None:
            self._wizard.close()
        try:
            self.win.destroy()
        except Exception:
            pass


class IntrinsicsWizard:
    """Checkerboard intrinsics calibration wizard.

    Shows the selected camera live with detected chessboard corners drawn
    on top; each Capture stores a sub-pixel-refined view, and
    'Calibrate & Apply' runs cv2.calibrateCamera and fills the fusion
    window's fx/fy/cx/cy + distortion fields with the result.
    """

    MIN_CAPTURES = 5
    RECOMMENDED = 15

    def __init__(self, fusion):
        self.fusion = fusion
        self.app = fusion.app
        self._closed = False
        self.captures = []            # list of (object_points, corners)
        self.img_size = None          # (w, h) of the full-res captures
        self._pattern_used = None
        self._detected = False
        self._corners_preview = None
        self._tick = 0
        self._busy = False            # calibrateCamera running
        self._photo = None

        self.win = tk.Toplevel(fusion.win)
        self.win.title("Calibrate Intrinsics  ·  Checkerboard")
        self.win.geometry("820x640")
        self.win.configure(bg=Theme.BG)
        self.win.transient(fusion.win)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        top = ttk.Frame(self.win, style="Panel.TFrame", padding=8)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(top, text="Inner corners:",
                  style="Muted.TLabel").pack(side=tk.LEFT)
        self.cols_var = tk.StringVar(value="9")
        ttk.Entry(top, textvariable=self.cols_var,
                  width=4).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(top, text="x", style="Muted.TLabel").pack(side=tk.LEFT)
        self.rows_var = tk.StringVar(value="6")
        ttk.Entry(top, textvariable=self.rows_var,
                  width=4).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(top, text="Square [mm]:",
                  style="Muted.TLabel").pack(side=tk.LEFT)
        self.square_var = tk.StringVar(value="25")
        ttk.Entry(top, textvariable=self.square_var,
                  width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(top, text="(a 10x7-squares board has 9x6 inner corners)",
                  style="Hint.TLabel").pack(side=tk.LEFT, padx=8)

        self.video = tk.Label(self.win, bg="black")
        self.video.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.status_var = tk.StringVar(
            value="Show the printed checkerboard to the camera.")
        ttk.Label(self.win, textvariable=self.status_var,
                  style="Muted.TLabel").pack(anchor=tk.W, padx=10)

        bar = ttk.Frame(self.win, style="Panel.TFrame", padding=6)
        bar.pack(fill=tk.X, padx=8, pady=(4, 8))
        self.capture_btn = ttk.Button(bar, text="📸  Capture",
                                      command=self.on_capture,
                                      state=tk.DISABLED)
        self.capture_btn.pack(side=tk.LEFT)
        self.count_var = tk.StringVar(
            value=f"0 captured (aim for {self.RECOMMENDED})")
        ttk.Label(bar, textvariable=self.count_var,
                  style="Muted.TLabel").pack(side=tk.LEFT, padx=10)
        ttk.Button(bar, text="Close",
                   command=self.close).pack(side=tk.RIGHT)
        self.calib_btn = ttk.Button(bar, text="Calibrate && Apply",
                                    command=self.on_calibrate,
                                    state=tk.DISABLED)
        self.calib_btn.pack(side=tk.RIGHT, padx=6)
        ttk.Button(bar, text="Reset",
                   command=self.on_reset).pack(side=tk.RIGHT, padx=6)

        self._poll()

    # ------------------------------------------------------------ helpers ----
    def _pattern(self):
        """(cols, rows) of inner corners, or None if the fields are bad."""
        try:
            cols = int(self.cols_var.get())
            rows = int(self.rows_var.get())
            if not (2 < cols < 30 and 2 < rows < 30):
                return None
            return (cols, rows)
        except (ValueError, tk.TclError):
            return None

    def _square_m(self):
        try:
            return max(float(self.square_var.get()), 0.1) / 1000.0
        except (ValueError, tk.TclError):
            return 0.025

    def _camera_frame(self):
        cam = self.fusion._cam_ref
        return getattr(cam, "last_bgr", None) if cam is not None else None

    # --------------------------------------------------------------- loop ----
    def _poll(self):
        if self._closed:
            return
        frame = self._camera_frame()
        if frame is None:
            self.status_var.set("No camera frame - open/select a camera "
                                "in the fusion window.")
            self.capture_btn.configure(state=tk.DISABLED)
        else:
            self._tick += 1
            preview = frame
            h, w = frame.shape[:2]
            if w > 640:                       # keep live detection fast
                preview = cv2.resize(frame, (640, max(int(h * 640 / w), 1)))
            if preview.ndim == 2:
                preview = cv2.cvtColor(preview, cv2.COLOR_GRAY2BGR)
            else:
                preview = preview.copy()
            pattern = self._pattern()
            if pattern is None:
                self.status_var.set("Invalid pattern size (2 < corners "
                                    "< 30).")
                self._detected = False
            elif self._tick % 3 == 0 and not self._busy:  # ~every 300 ms
                gray = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
                found, corners = cv2.findChessboardCorners(
                    gray, pattern,
                    flags=cv2.CALIB_CB_ADAPTIVE_THRESH
                    | cv2.CALIB_CB_NORMALIZE_IMAGE
                    | cv2.CALIB_CB_FAST_CHECK)
                self._detected = bool(found)
                self._corners_preview = corners if found else None
            if pattern is not None:
                if self._detected and self._corners_preview is not None:
                    cv2.drawChessboardCorners(preview, pattern,
                                              self._corners_preview, True)
                    if not self._busy:
                        self.status_var.set("Board detected - press "
                                            "Capture.")
                elif not self._busy:
                    self.status_var.set("No board detected - show the "
                                        "printed checkerboard to the "
                                        "camera.")
            self.capture_btn.configure(
                state=(tk.NORMAL if self._detected and not self._busy
                       else tk.DISABLED))
            self._photo = bgr_to_photo(preview,
                                       max(self.video.winfo_width(), 64),
                                       max(self.video.winfo_height(), 64))
            self.video.configure(image=self._photo)
        self.calib_btn.configure(
            state=(tk.NORMAL if len(self.captures) >= self.MIN_CAPTURES
                   and not self._busy else tk.DISABLED))
        self.win.after(100, self._poll)

    # ------------------------------------------------------------ actions ----
    def on_capture(self):
        frame = self._camera_frame()
        pattern = self._pattern()
        if frame is None or pattern is None:
            return
        if self._pattern_used is not None and pattern != self._pattern_used:
            messagebox.showerror(
                "Capture",
                "The pattern size changed since the previous captures.\n"
                "Press Reset to start over with the new size.",
                parent=self.win)
            return
        gray = (cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if frame.ndim == 3 else frame)
        size = (gray.shape[1], gray.shape[0])
        if self.img_size is not None and size != self.img_size:
            messagebox.showerror(
                "Capture",
                "The camera resolution changed since the previous "
                "captures.\nPress Reset and start over.", parent=self.win)
            return
        # full-resolution detection (no FAST_CHECK) + sub-pixel refinement
        found, corners = cv2.findChessboardCorners(
            gray, pattern, flags=cv2.CALIB_CB_ADAPTIVE_THRESH
            | cv2.CALIB_CB_NORMALIZE_IMAGE)
        if not found:
            self.status_var.set("Board not found in the full-resolution "
                                "frame - hold it steady and try again.")
            return
        corners = cv2.cornerSubPix(
            gray, corners, (11, 11), (-1, -1),
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3))
        self.captures.append(
            (checkerboard_object_points(*pattern, self._square_m()),
             corners))
        self.img_size = size
        self._pattern_used = pattern
        n = len(self.captures)
        self.count_var.set(f"{n} captured (aim for {self.RECOMMENDED})")
        self.status_var.set(
            f"Captured view {n}. Move/tilt the board and capture again - "
            "cover the image corners too.")

    def on_reset(self):
        self.captures = []
        self.img_size = None
        self._pattern_used = None
        self.count_var.set(f"0 captured (aim for {self.RECOMMENDED})")
        self.status_var.set("Captures cleared. Show the checkerboard to "
                            "the camera.")

    def on_calibrate(self):
        if len(self.captures) < self.MIN_CAPTURES or self._busy:
            return
        self._busy = True
        self.calib_btn.configure(state=tk.DISABLED)
        self.capture_btn.configure(state=tk.DISABLED)
        self.status_var.set(
            f"Calibrating from {len(self.captures)} views ...")
        obj = [o for o, _ in self.captures]
        img = [c for _, c in self.captures]
        size = self.img_size

        def work():
            try:
                rms, K, dist, _rv, _tv = cv2.calibrateCamera(
                    obj, img, size, None, None)
                self.app.root.after(0, lambda: self._apply(rms, K, dist))
            except Exception as e:
                self.app.root.after(0, lambda: self._fail(e))

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, rms, K, dist):
        self._busy = False
        if self._closed:
            return
        K = np.asarray(K)
        d = np.asarray(dist).ravel()
        vals = {"fx": K[0, 0], "fy": K[1, 1], "cx": K[0, 2], "cy": K[1, 2]}
        for i, key in enumerate(("k1", "k2", "p1", "p2", "k3")):
            vals[key] = float(d[i]) if i < d.size else 0.0
        for key, val in vals.items():
            self.fusion.calib_vars[key].set(f"{float(val):.6g}")
        quality = ("excellent" if rms < 0.5 else
                   "good" if rms < 1.0 else
                   "poor - capture more varied views and recalibrate")
        self.status_var.set(f"Done. Reprojection error {rms:.3f} px "
                            f"({quality}).")
        self.app.log(
            f"Intrinsics calibrated from {len(self.captures)} views: "
            f"fx={vals['fx']:.1f} fy={vals['fy']:.1f} "
            f"cx={vals['cx']:.1f} cy={vals['cy']:.1f}, "
            f"RMS reprojection error {rms:.3f} px.")
        messagebox.showinfo(
            "Calibrate intrinsics",
            "Calibration applied to the fusion window.\n\n"
            f"Views used: {len(self.captures)}\n"
            f"Reprojection error: {rms:.3f} px ({quality})\n\n"
            "Store it with the fusion window's Save... button.",
            parent=self.win)

    def _fail(self, e):
        self._busy = False
        if self._closed:
            return
        self.status_var.set(f"Calibration failed: {e}")
        messagebox.showerror("Calibrate intrinsics",
                             f"Calibration failed:\n{e}", parent=self.win)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.fusion._wizard is self:
            self.fusion._wizard = None
        try:
            self.win.destroy()
        except Exception:
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
        root.title(f"Ouster Digital Lidar Control  v{__version__}  ·  "
                   "Powered by Python")
        root.geometry("1280x860")
        apply_theme(root)
        enable_dark_title_bar(root)

        self.reader = None
        self.frame_queue = queue.Queue(maxsize=4)
        self.ros_pub = None
        self.camera_windows = []
        self.fusion_win = None
        self.record_proc = None
        self.viz_proc = None
        self.image_artists = {}
        self.last_frame_status = {}
        self.settings = self._load_settings()

        self._build_ui()
        self._poll_queue()

        self.log(f"Ouster Digital Lidar Control v{__version__} ready.")
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

        # --- ROS 2 topics -------------------------------------------------------
        ros = ttk.LabelFrame(left, text="  ROS 2 TOPICS  ", padding=10)
        ros.pack(fill=tk.X, pady=4)
        ttk.Label(ros, text="Point-cloud topic:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.ros_topic_var = tk.StringVar(value="/ouster/points")
        ttk.Entry(ros, textvariable=self.ros_topic_var).pack(fill=tk.X,
                                                             pady=3)
        ttk.Label(ros, text="Frame ID (TF):",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.ros_frame_var = tk.StringVar(value="ouster")
        ttk.Entry(ros, textvariable=self.ros_frame_var).pack(fill=tk.X,
                                                             pady=3)
        self.ros_btn = ttk.Button(ros, text="⇪  Start ROS Publishing",
                                  command=self.on_toggle_ros)
        self.ros_btn.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(ros, text="Publishes sensor_msgs/PointCloud2 while a "
                            "live stream\nor a playback is running "
                            "(requires ROS 2 / rclpy).",
                  style="Hint.TLabel").pack(anchor=tk.W, pady=(3, 0))

        # --- Cameras ------------------------------------------------------------
        cam = ttk.LabelFrame(left, text="  CAMERAS  ", padding=10)
        cam.pack(fill=tk.X, pady=4)
        ttk.Label(cam, text="Device index or URL:",
                  style="Muted.TLabel").pack(anchor=tk.W)
        self.cam_src_var = tk.StringVar(value="0")
        ttk.Entry(cam, textvariable=self.cam_src_var).pack(fill=tk.X, pady=3)
        ttk.Label(cam, text="0, 1, ... = USB / built-in camera   ·   "
                            "rtsp:// or http:// = IP camera",
                  style="Hint.TLabel").pack(anchor=tk.W)
        ttk.Button(cam, text="🎥  Open Camera",
                   command=self.on_open_camera).pack(fill=tk.X, pady=(6, 0))
        ttk.Label(cam, text="Each camera opens in its own window with live "
                            "view,\nsnapshots and optional ROS 2 image "
                            "publishing.",
                  style="Hint.TLabel").pack(anchor=tk.W, pady=(3, 0))
        ttk.Button(cam, text="⧉  Camera-Lidar Fusion...",
                   command=self.on_open_fusion).pack(fill=tk.X, pady=(6, 0))

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
            "ros_topic": self.ros_topic_var,
            "ros_frame": self.ros_frame_var,
            "camera_src": self.cam_src_var,
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
        # start from the stored settings so extra sections written by other
        # windows (e.g. "fusion_calib") survive a form-fields save
        data = dict(self.settings)
        for key, var in getattr(self, "_persist_vars", {}).items():
            try:
                data[key] = var.get()
            except Exception:
                pass
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.settings = data
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
                                 is_file=is_file, loop=loop,
                                 ros_pub_fn=lambda: self.ros_pub,
                                 fusion_fn=self._active_fusion)
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
            self.log(f"ERROR exporting MCAP: {e}")
            self.root.after(0, lambda: messagebox.showerror(
                "Export to MCAP", f"Export failed:\n{e}"))

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

    def on_toggle_ros(self):
        """Start / stop publishing live point clouds as ROS 2 topics."""
        if self.ros_pub is None:
            if not HAVE_ROS:
                messagebox.showerror(
                    "ROS 2 publishing",
                    "rclpy (ROS 2) is not available in this Python "
                    "environment.\n\n"
                    "Install ROS 2 (e.g. Jazzy on Ubuntu 24.04) and start "
                    "the app from a terminal where ROS is sourced:\n"
                    "    source /opt/ros/jazzy/setup.bash\n"
                    "    python3 ouster_gui.py\n\n"
                    "If you use a virtualenv, create it with\n"
                    "    python3 -m venv venv --system-site-packages\n"
                    "so it can see the ROS packages.\n\n"
                    f"Details: {ROS_IMPORT_ERROR}")
                return
            topic = self.ros_topic_var.get().strip() or "/ouster/points"
            frame_id = self.ros_frame_var.get().strip() or "ouster"
            try:
                self.ros_pub = RosPublisher(topic, frame_id)
            except Exception as e:
                self.log(f"ERROR starting ROS publisher: {e}")
                messagebox.showerror(
                    "ROS 2 publishing",
                    f"Could not start the ROS 2 publisher:\n{e}")
                return
            self._save_settings()
            self.ros_btn.configure(text="■  Stop ROS Publishing")
            self.log(f"ROS 2 publishing ON -> {topic} "
                     f"(sensor_msgs/PointCloud2, frame_id '{frame_id}').")
            self.log(f"RViz2: set Fixed Frame to '{frame_id}' and add a "
                     "PointCloud2 display on that topic.")
            if self.reader is None:
                self.log("Start a live stream or play a recording to "
                         "publish frames.")
        else:
            pub, self.ros_pub = self.ros_pub, None
            pub.close()
            self.ros_btn.configure(text="⇪  Start ROS Publishing")
            self.log(f"ROS 2 publishing OFF ({pub.n_published} point "
                     f"clouds, {pub.n_images} camera images, "
                     f"{pub.n_rgb} colored clouds published).")

    def on_open_camera(self):
        """Open a camera (USB index or IP-camera URL) in its own window."""
        if not HAVE_CV2:
            messagebox.showerror(
                "Cameras",
                "Camera support needs OpenCV. Install it with:\n\n"
                "    pip install opencv-python\n\n"
                f"Details: {CV2_IMPORT_ERROR}")
            return
        src = self.cam_src_var.get().strip()
        if not src:
            messagebox.showerror(
                "Cameras",
                "Please enter a camera device index (0, 1, ...) or an "
                "IP-camera URL (rtsp:// or http://).")
            return
        self._save_settings()
        self.camera_windows.append(CameraWindow(self, src))

    def _active_fusion(self):
        fw = self.fusion_win
        return fw if (fw is not None and not fw._closed) else None

    def on_open_fusion(self):
        """Open the Camera-Lidar Fusion / calibration window."""
        if not HAVE_CV2:
            messagebox.showerror(
                "Camera-Lidar Fusion",
                "Fusion needs OpenCV. Install it with:\n\n"
                "    pip install opencv-python\n\n"
                f"Details: {CV2_IMPORT_ERROR}")
            return
        if self._active_fusion() is not None:
            self.fusion_win.win.lift()
            return
        self.fusion_win = FusionWindow(self)
        self.log("Fusion window opened: select a camera, start a lidar "
                 "stream, then tune the calibration until the overlay "
                 "lines up with the image.")

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
        if self.fusion_win is not None:
            self.fusion_win.close()
        for cam in list(self.camera_windows):
            cam.close()
        if self.ros_pub is not None:
            pub, self.ros_pub = self.ros_pub, None
            pub.close()
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
