#!/usr/bin/env python3
"""CPU Load Generator — native Python GUI (tkinter).

Generates a controllable CPU load for a chosen duration and shows live
CPU usage and temperature. Pure Python standard library — no
dependencies (on Debian/Ubuntu, tkinter comes from the python3-tk
package; on Windows, installing psutil enables CPU-usage readings).

Usage:
    python3 cpu_load_gui.py
"""

import csv
import ctypes as ct
import glob
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import threading
import time

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError:
    sys.exit("tkinter is missing. On Debian/Ubuntu run:  sudo apt install python3-tk")


# ---------------------------------------------------------------- load engine

def _worker(target, stop_flag):
    """Busy-loop for target% of each 100ms cycle, sleep the rest."""
    period = 0.1
    while not stop_flag.is_set():
        t = min(100.0, max(0.0, target.value))
        if t <= 0:
            time.sleep(period)
            continue
        busy_until = time.perf_counter() + period * t / 100.0
        while time.perf_counter() < busy_until:
            pass
        rest = period * (100.0 - t) / 100.0
        if rest > 0:
            time.sleep(rest)


class LoadEngine:
    def __init__(self):
        self.cpu_count = os.cpu_count() or 1
        self._target = mp.Value("d", 50.0)
        self._stop = mp.Event()
        self._procs = []
        self._lock = threading.Lock()
        self._until = None
        self._timer = None

    @property
    def running(self):
        return bool(self._procs)

    @property
    def workers(self):
        return len(self._procs)

    @property
    def target(self):
        return self._target.value

    @property
    def remaining(self):
        """Seconds until auto-stop, or None if unlimited / not running."""
        if self._until is None or not self._procs:
            return None
        return max(0.0, self._until - time.time())

    def set_target(self, pct):
        self._target.value = min(100.0, max(0.0, float(pct)))

    def start(self, workers, duration=0):
        with self._lock:
            self._stop_locked()
            workers = min(self.cpu_count * 2, max(1, int(workers)))
            self._stop.clear()
            for _ in range(workers):
                p = mp.Process(target=_worker, args=(self._target, self._stop), daemon=True)
                p.start()
                self._procs.append(p)
            if duration and duration > 0:
                self._until = time.time() + duration
                self._timer = threading.Timer(duration, self.stop)
                self._timer.daemon = True
                self._timer.start()

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._until = None
        if not self._procs:
            return
        self._stop.set()
        for p in self._procs:
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()
        self._procs = []


# ------------------------------------------------------------------ gpu load

_CL_DEVICE_TYPE_GPU = 4
_CL_DEVICE_TYPE_ALL = 0xFFFFFFFF
_CL_MEM_READ_WRITE = 1
_CL_DEVICE_NAME = 0x102B

# The stress kernel renders a plasma animation frame — every pixel runs a
# tunable amount of trig math, so the visible animation IS the load.
_CL_KERNEL = b"""
__kernel void frame(__global uchar *img, const float t, const int iters,
                    const int W, const int H) {
    int gid = get_global_id(0);
    if (gid >= W * H) return;
    float u = (gid % W) / (float)W * 2.0f - 1.0f;
    float v = (gid / W) / (float)H * 2.0f - 1.0f;
    float x = u, y = v, acc = 0.0f;
    for (int i = 0; i < iters; i++) {
        float fi = (float)i;
        acc += sin(x * (3.0f + fi * 0.021f) + t)
             + cos(y * (4.0f + fi * 0.017f) - t * 1.3f);
        float nx = x * 0.9998f - y * 0.02f + 0.004f * sin(t + fi * 0.1f);
        y = x * 0.02f + y * 0.9998f + 0.004f * cos(t * 0.7f + fi * 0.13f);
        x = nx;
    }
    float w1 = 0.5f + 0.5f * sin(acc * 0.5f + t);
    float w2 = 0.5f + 0.5f * sin(acc * 0.5f + t + 2.094f);
    float w3 = 0.5f + 0.5f * sin(acc * 0.5f + t + 4.188f);
    float vig = clamp(1.25f - (u * u + v * v), 0.0f, 1.0f);
    img[gid * 3 + 0] = (uchar)(255.0f * w1 * vig);
    img[gid * 3 + 1] = (uchar)(255.0f * w2 * vig * 0.85f);
    img[gid * 3 + 2] = (uchar)(255.0f * w3 * vig * 0.55f);
}
"""


def pick_gpu_device(names):
    """Index of the preferred device: discrete GPUs beat integrated ones."""
    best, best_score = 0, None
    for i, name in enumerate(names):
        n = name.lower()
        score = 0
        for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla",
                  "radeon", "instinct", "arc"):
            if k in n:
                score += 2
        for k in ("intel", "uhd", "iris", "hd graphics"):
            if k in n:
                score -= 1
        if best_score is None or score > best_score:
            best, best_score = i, score
    return best


class GpuBurner:
    """Generates GPU load through OpenCL, which ships with GPU drivers on
    Windows/Linux/macOS — no Python packages needed. Renders a plasma
    animation (FurMark-style: the animation is the load) at up to FPS
    frames per second; the intensity slider sets the math per pixel and
    the window size sets the pixel count, so enlarging the animation
    window raises GPU load. Unavailable if no OpenCL GPU."""

    FRAME_W = 320                # initial frame size
    FRAME_H = 320
    MAX_W = 2560
    MAX_H = 1440
    FPS = 25

    def __init__(self, device_type=_CL_DEVICE_TYPE_GPU, device_index=None):
        self.available = False
        self.reason = None
        self.device_name = None
        self.devices = []            # names of all detected GPU devices
        self.device_index = 0
        self.intensity = 50.0
        self.frame = None            # latest rendered frame as binary PPM
        self._frame_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._t = 0.0
        self._size = (self.FRAME_W, self.FRAME_H)
        self._last_pub = 0.0
        try:
            self._init_cl(device_type, device_index)
            self.available = True
        except Exception as e:
            self.reason = str(e) or "OpenCL initialization failed"

    def _init_cl(self, device_type, device_index=None):
        if IS_WINDOWS:
            names = ["OpenCL.dll"]
        elif sys.platform == "darwin":
            names = ["/System/Library/Frameworks/OpenCL.framework/OpenCL"]
        else:
            names = ["libOpenCL.so.1", "libOpenCL.so"]
        cl = None
        for n in names:
            try:
                cl = ct.CDLL(n)
                break
            except OSError:
                continue
        if cl is None:
            raise RuntimeError("no OpenCL driver found")
        # handle-returning functions must be declared or 64-bit pointers truncate
        for fn in ("clCreateContext", "clCreateCommandQueue",
                   "clCreateProgramWithSource", "clCreateKernel", "clCreateBuffer"):
            getattr(cl, fn).restype = ct.c_void_p

        n = ct.c_uint(0)
        if cl.clGetPlatformIDs(0, None, ct.byref(n)) != 0 or not n.value:
            raise RuntimeError("no OpenCL platforms")
        plats = (ct.c_void_p * n.value)()
        cl.clGetPlatformIDs(n, plats, None)

        # collect every GPU device across all platforms (e.g. Intel + NVIDIA)
        found = []
        for p in plats:
            dn = ct.c_uint(0)
            if cl.clGetDeviceIDs(ct.c_void_p(p), ct.c_ulonglong(device_type),
                                 0, None, ct.byref(dn)) == 0 and dn.value:
                devs = (ct.c_void_p * dn.value)()
                cl.clGetDeviceIDs(ct.c_void_p(p), ct.c_ulonglong(device_type), dn, devs, None)
                for d in devs:
                    namebuf = ct.create_string_buffer(256)
                    cl.clGetDeviceInfo(ct.c_void_p(d), _CL_DEVICE_NAME,
                                       ct.c_size_t(256), namebuf, None)
                    name = namebuf.value.decode(errors="replace").strip() or "GPU"
                    found.append((d, name))
        if not found:
            raise RuntimeError("no OpenCL GPU device")

        names = []
        for _, name in found:      # disambiguate identical twins for the picker
            unique, i = name, 2
            while unique in names:
                unique = f"{name} #{i}"
                i += 1
            names.append(unique)
        self.devices = names
        if device_index is not None and 0 <= device_index < len(found):
            idx = device_index
        else:
            idx = pick_gpu_device(names)
        self.device_index = idx
        dev = found[idx][0]
        self.device_name = names[idx]

        err = ct.c_int(0)
        devarr = (ct.c_void_p * 1)(dev)
        ctx = cl.clCreateContext(None, 1, devarr, None, None, ct.byref(err))
        if not ctx or err.value != 0:
            raise RuntimeError(f"clCreateContext failed ({err.value})")
        queue = cl.clCreateCommandQueue(ct.c_void_p(ctx), ct.c_void_p(dev),
                                        ct.c_ulonglong(0), ct.byref(err))
        if not queue or err.value != 0:
            raise RuntimeError(f"clCreateCommandQueue failed ({err.value})")

        src = ct.c_char_p(_CL_KERNEL)
        length = ct.c_size_t(len(_CL_KERNEL))
        prog = cl.clCreateProgramWithSource(ct.c_void_p(ctx), 1, ct.byref(src),
                                            ct.byref(length), ct.byref(err))
        if not prog or err.value != 0:
            raise RuntimeError("clCreateProgramWithSource failed")
        if cl.clBuildProgram(ct.c_void_p(prog), 1, devarr, b"", None, None) != 0:
            raise RuntimeError("OpenCL kernel build failed")
        kern = cl.clCreateKernel(ct.c_void_p(prog), b"frame", ct.byref(err))
        if not kern or err.value != 0:
            raise RuntimeError("clCreateKernel failed")

        nbytes = self.MAX_W * self.MAX_H * 3   # one buffer big enough for any size
        membuf = cl.clCreateBuffer(ct.c_void_p(ctx), ct.c_ulonglong(_CL_MEM_READ_WRITE),
                                   ct.c_size_t(nbytes), None, ct.byref(err))
        if not membuf or err.value != 0:
            raise RuntimeError("clCreateBuffer failed")
        mem_handle = ct.c_void_p(membuf)
        kern_p = ct.c_void_p(kern)
        if cl.clSetKernelArg(kern_p, 0, ct.c_size_t(ct.sizeof(ct.c_void_p)),
                             ct.byref(mem_handle)) != 0:
            raise RuntimeError("clSetKernelArg failed")

        self._cl, self._queue, self._kern = cl, queue, kern
        self._mem = mem_handle          # keep alive
        self._host = (ct.c_ubyte * nbytes)()

    def set_size(self, w, h):
        """Set the rendered frame size; more pixels = more GPU work."""
        w = int(max(160, min(self.MAX_W, w)))
        h = int(max(160, min(self.MAX_H, h)))
        cw, ch = self._size
        if abs(w - cw) >= 8 or abs(h - ch) >= 8:
            self._size = (w, h)

    def _launch(self, iters, w, h, readback=True):
        """Run one compute burst on the GPU; optionally read the frame back."""
        cl = self._cl
        kern_p = ct.c_void_p(self._kern)
        q_p = ct.c_void_p(self._queue)
        args = ((1, ct.c_float(self._t)), (2, ct.c_int(iters)),
                (3, ct.c_int(w)), (4, ct.c_int(h)))
        for idx, val in args:
            if cl.clSetKernelArg(kern_p, idx, ct.c_size_t(4), ct.byref(val)) != 0:
                raise RuntimeError("clSetKernelArg failed")
        gsz = ct.c_size_t(w * h)
        if cl.clEnqueueNDRangeKernel(q_p, kern_p, 1, None, ct.byref(gsz),
                                     None, 0, None, None) != 0:
            raise RuntimeError("clEnqueueNDRangeKernel failed")
        if readback:
            if cl.clEnqueueReadBuffer(q_p, self._mem, 1, ct.c_size_t(0),
                                      ct.c_size_t(w * h * 3), self._host,
                                      0, None, None) != 0:
                raise RuntimeError("clEnqueueReadBuffer failed")
        cl.clFinish(q_p)
        if readback:
            self._t += 0.06
            now = time.perf_counter()
            if now - self._last_pub > 0.09:   # publish at GUI cadence
                self._last_pub = now
                header = b"P6\n%d %d\n255\n" % (w, h)
                with self._frame_lock:
                    self.frame = header + bytes(self._host[:w * h * 3])

    def get_frame(self):
        with self._frame_lock:
            return self.frame

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if not self.available or self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self):
        # Busy-budget model: intensity sets the fraction of each frame
        # interval the GPU spends computing. The first burst of an interval
        # renders the visible frame; extra bursts keep the GPU busy until
        # the budget is used. Burst size auto-tunes toward ~10ms, so the
        # slider stays accurate on anything from an iGPU to a high-end card.
        interval = 1.0 / self.FPS
        chunk = 0.010
        iters = 64
        while not self._stop.is_set():
            t = min(100.0, max(0.0, self.intensity))
            if t <= 0:
                time.sleep(0.1)
                continue
            w, h = self._size
            budget = interval * t / 100.0
            t0 = time.perf_counter()
            readback = True
            while not self._stop.is_set():
                k0 = time.perf_counter()
                try:
                    self._launch(iters, w, h, readback)
                except Exception as e:
                    self.available, self.reason = False, str(e)
                    return
                readback = False
                dt = time.perf_counter() - k0
                if dt > 0:   # steer bursts toward `chunk`, damped against noise
                    factor = max(0.5, min(2.0, chunk / dt))
                    iters = max(8, min(1 << 17, int(iters * factor) or 8))
                if time.perf_counter() - t0 >= budget:
                    break
            busy = time.perf_counter() - t0
            # Idle long enough that busy/(busy+idle) == intensity, even when a
            # single minimal burst overshoots the budget (slow devices): the
            # fps drops but the average load stays honest.
            idle_needed = busy * (100.0 - t) / t if t < 100 else 0.0
            rest = max(interval - busy, idle_needed)
            if rest > 0:
                time.sleep(rest)


def find_nvidia_smi():
    exe = shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")
    if exe:
        return exe
    if IS_WINDOWS:
        p = os.path.expandvars(r"%SystemRoot%\System32\nvidia-smi.exe")
        if os.path.exists(p):
            return p
    return None


def read_gpu_stats(nvsmi, sys_root="/sys"):
    """(utilization %, temperature °C) — either may be None."""
    if nvsmi:
        flags = 0x08000000 if IS_WINDOWS else 0
        try:
            out = subprocess.run(
                [nvsmi, "--query-gpu=utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, creationflags=flags,
            ).stdout.strip()
            if out:
                util_s, temp_s = (x.strip() for x in out.splitlines()[0].split(",")[:2])
                util = float(util_s) if util_s.replace(".", "", 1).isdigit() else None
                temp = float(temp_s) if temp_s.replace(".", "", 1).isdigit() else None
                return util, temp
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    for card in glob.glob(sys_root + "/class/drm/card*/device"):   # AMD on Linux
        util = temp = None
        try:
            with open(card + "/gpu_busy_percent") as f:
                util = float(f.read().strip())
        except (OSError, ValueError):
            continue
        for t in glob.glob(card + "/hwmon/hwmon*/temp*_input"):
            try:
                with open(t) as f:
                    temp = int(f.read().strip()) / 1000.0
                break
            except (OSError, ValueError):
                pass
        return util, temp
    return None, None


# ------------------------------------------------------- cpu & temp sampling

_CPU_SENSOR_HINTS = ("pkg", "cpu", "core", "x86", "soc", "k10temp", "zenpower", "acpitz")


def read_cpu_temp(sys_root="/sys", psutil_mod=None):
    """CPU temperature in °C from Linux sensors, or None if unavailable."""
    candidates = []  # (is_cpu_sensor, temp)
    for zone in glob.glob(sys_root + "/class/thermal/thermal_zone*"):
        try:
            with open(zone + "/temp") as f:
                t = int(f.read().strip()) / 1000.0
            with open(zone + "/type") as f:
                kind = f.read().strip().lower()
        except (OSError, ValueError):
            continue
        if 0 < t < 150:
            candidates.append((any(h in kind for h in _CPU_SENSOR_HINTS), t))
    for path in glob.glob(sys_root + "/class/hwmon/hwmon*/temp*_input"):
        try:
            with open(path) as f:
                t = int(f.read().strip()) / 1000.0
            with open(os.path.dirname(path) + "/name") as f:
                kind = f.read().strip().lower()
        except (OSError, ValueError):
            continue
        if 0 < t < 150:
            candidates.append((any(h in kind for h in _CPU_SENSOR_HINTS), t))
    if psutil_mod and not candidates:
        try:
            for kind, entries in (psutil_mod.sensors_temperatures() or {}).items():
                for e in entries:
                    if e.current and 0 < e.current < 150:
                        candidates.append(
                            (any(h in kind.lower() for h in _CPU_SENSOR_HINTS), e.current))
        except (AttributeError, OSError):
            pass
    if not candidates:
        return None
    cpu_only = [t for is_cpu, t in candidates if is_cpu]
    return max(cpu_only) if cpu_only else max(t for _, t in candidates)


# Windows (and WSL) fallback: ask WMI through PowerShell. Tries
# LibreHardwareMonitor / OpenHardwareMonitor sensors first (accurate, if the
# app is running), then the ACPI thermal zone (often requires administrator
# rights and is not exposed on every machine). The CPU query is the original
# proven one (match by sensor Name), only excluding 'GPU'-named sensors so a
# "GPU Core" reading never lands in the CPU slot; GPU is matched additively.
_PS_TEMP_SCRIPT = (
    "$c=$null;$g=$null;"
    "foreach($ns in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor'){"
    "try{$s=Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop|"
    "Where-Object{$_.SensorType -eq 'Temperature'};"
    "if($s){"
    "$cp=$s|Where-Object{$_.Name -match 'CPU|Package|Core' -and $_.Name -notmatch 'GPU'}|"
    "Sort-Object Value -Descending|Select-Object -First 1;"
    "$gp=$s|Where-Object{$_.Name -match 'GPU' -or $_.Identifier -match 'gpu'}|"
    "Sort-Object Value -Descending|Select-Object -First 1;"
    "if($cp){$c=$cp.Value};if($gp){$g=$gp.Value};"
    "if($null -ne $c){break}}}catch{}};"
    "if($null -eq $c){"
    "try{$z=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
    "-ErrorAction Stop|Sort-Object CurrentTemperature -Descending|Select-Object -First 1;"
    "if($z){$c=($z.CurrentTemperature/10)-273.15}}catch{}};"
    "'CPU='+$(if($null -ne $c){[math]::Round($c,1)}else{''});"
    "'GPU='+$(if($null -ne $g){[math]::Round($g,1)}else{''})"
)


IS_WINDOWS = os.name == "nt"


def find_powershell():
    if IS_WINDOWS:
        return shutil.which("powershell") or shutil.which("pwsh")
    return shutil.which("powershell.exe")  # running under WSL


def is_windows_admin():
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def _program_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_hw_monitor():
    """LibreHardwareMonitor/OpenHardwareMonitor exe shipped next to the app."""
    if not IS_WINDOWS:
        return None
    base = _program_dir()
    candidates = [os.path.join(base, "LibreHardwareMonitor.exe"),
                  os.path.join(base, "OpenHardwareMonitor.exe")]
    for pattern in ("*HardwareMonitor*", "*hardwaremonitor*"):
        for sub in glob.glob(os.path.join(base, pattern)):
            candidates.append(os.path.join(sub, "LibreHardwareMonitor.exe"))
            candidates.append(os.path.join(sub, "OpenHardwareMonitor.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _set_hw_monitor_minimized(exe):
    """Set 'Start Minimized' + 'Minimize To Tray' in the monitor's own
    config file (Libre/OpenHardwareMonitor persist options there), so it
    comes up quietly in the tray instead of opening a window."""
    cfg = os.path.splitext(exe)[0] + ".config"
    try:
        import xml.etree.ElementTree as ET
        if os.path.exists(cfg):
            tree = ET.parse(cfg)
            root = tree.getroot()
            apps = root.find("appSettings")
            if apps is None:
                apps = ET.SubElement(root, "appSettings")
        else:
            root = ET.Element("configuration")
            apps = ET.SubElement(root, "appSettings")
            tree = ET.ElementTree(root)
        for key in ("startMinMenuItem", "minTrayMenuItem"):
            node = next((a for a in apps.findall("add") if a.get("key") == key), None)
            if node is None:
                node = ET.SubElement(apps, "add")
                node.set("key", key)
            node.set("value", "true")
        tree.write(cfg, encoding="utf-8", xml_declaration=True)
    except Exception:
        pass    # worst case it opens a window once


def launch_hw_monitor(exe):
    """Start the hardware monitor minimized to the tray; the shell handles
    its UAC elevation (it needs admin for the sensor driver)."""
    _set_hw_monitor_minimized(exe)
    try:
        rc = ct.windll.shell32.ShellExecuteW(
            None, "open", exe, None, os.path.dirname(exe), 6)  # 6 = SW_MINIMIZE
        return rc > 32
    except (OSError, AttributeError):
        return False


def apply_dark_title_bar(win):
    """Ask Windows for a dark title bar (no-op elsewhere / on old builds)."""
    if not IS_WINDOWS:
        return
    try:
        win.update_idletasks()
        hwnd = ct.windll.user32.GetParent(win.winfo_id())
        val = ct.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (19 pre-20H1)
            if ct.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr,
                                                      ct.byref(val), 4) == 0:
                break
        # nudge a repaint so the bar recolors immediately
        win.attributes("-alpha", 0.99)
        win.attributes("-alpha", 1.0)
    except (OSError, AttributeError, tk.TclError):
        pass


def relaunch_as_admin():
    """Restart this script elevated (UAC prompt). True if launch succeeded."""
    if not IS_WINDOWS:
        return False
    try:
        if getattr(sys, "frozen", False):      # built exe: relaunch itself
            exe, params = sys.executable, ""
        else:                                  # script: relaunch via python
            exe = sys.executable
            params = '"%s"' % os.path.abspath(sys.argv[0])
        rc = ct.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32  # ShellExecute returns >32 on success
    except (OSError, AttributeError):
        return False


def read_temps_lhm_web(port=8085, timeout=1.5):
    """(cpu_temp, gpu_temp) in °C from LibreHardwareMonitor's built-in web
    server (Options → Remote Web Server → Run). Pure stdlib, no WMI, no
    admin. Either may be None. This is the most reliable source when WMI is
    unavailable."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/data.json" % port, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return None, None

    cpu, gpu = [], []

    def num(v):
        try:
            return float(str(v).split()[0].replace(",", "."))
        except (ValueError, IndexError):
            return None

    # 'intel'/'amd' are NOT used as CPU hints — they also name GPUs
    # (Intel UHD/Iris/Arc, AMD Radeon), which would misfile a GPU as CPU.
    gpu_hw = ("gpu", "nvidia", "geforce", "radeon", "quadro", "graphics",
              "uhd", "iris", "vega", "firepro", "gtx", "rtx", " arc")
    cpu_hw = ("cpu", "core i", "core(tm)", "ryzen", "processor", "xeon",
              "pentium", "celeron", "threadripper", "athlon")

    def walk(node, ctx):
        text = str(node.get("Text", "")).lower()
        if any(k in text for k in gpu_hw):
            ctx = "gpu"
        elif any(k in text for k in cpu_hw):
            ctx = "cpu"
        val = node.get("Value")
        if val and "°" in str(val):
            t = num(val)
            if t is not None and 0 < t < 150:
                if "gpu" in text:
                    which = "gpu"
                elif "cpu" in text or "package" in text:
                    which = "cpu"
                else:
                    which = ctx
                if which == "gpu":
                    gpu.append(t)
                elif which == "cpu":
                    cpu.append(t)
        for ch in node.get("Children", []) or []:
            walk(ch, ctx)

    try:
        walk(data, None)
    except Exception:
        return None, None
    return (max(cpu) if cpu else None, max(gpu) if gpu else None)


def read_temps_powershell(exe):
    """(cpu_temp, gpu_temp) in °C from LibreHardwareMonitor / OpenHardware-
    Monitor (CPU falls back to the ACPI zone) via one PowerShell call.
    Either may be None."""
    flags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
    cpu = gpu = None
    try:
        out = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", _PS_TEMP_SCRIPT],
            capture_output=True, text=True, timeout=10, creationflags=flags,
        ).stdout
        for line in out.splitlines():
            key, sep, raw = line.strip().partition("=")
            if not sep or key not in ("CPU", "GPU"):
                continue
            raw = raw.strip().replace(",", ".")
            val = None
            if raw:
                try:
                    v = float(raw)
                    val = v if 0 < v < 150 else None
                except ValueError:
                    val = None
            if key == "CPU":
                cpu = val
            else:
                gpu = val
    except (OSError, subprocess.SubprocessError):
        pass
    return cpu, gpu


class CpuSampler:
    """Background thread sampling CPU usage (via /proc/stat on Linux,
    psutil elsewhere if installed) and CPU temperature."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.total = None          # overall usage %, or None if unavailable
        self.cores = []            # per-core usage %
        self.temp = None           # CPU temperature °C, or None if no sensor
        self.gpu_util = None       # GPU utilization %, or None
        self.gpu_temp = None       # GPU temperature °C, or None
        self._prev = None
        self._psutil = None
        self._ps_exe = find_powershell()
        self._temp_period = interval   # slowed down once PowerShell is needed
        self._temp_ts = 0.0
        self._nvsmi = find_nvidia_smi()
        self._gpu_period = 2.0         # nvidia-smi is a subprocess; poll gently
        self._gpu_ts = 0.0
        self._lhm_gpu_temp = None      # GPU temp from LibreHardwareMonitor
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            pass
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        while True:
            try:
                self._sample()
            except Exception:
                self.total, self.cores = None, []
            self._sample_temp()
            self._sample_gpu()
            time.sleep(self.interval)

    def _sample_gpu(self):
        now = time.time()
        if now - self._gpu_ts < self._gpu_period:
            return
        self._gpu_ts = now
        try:
            util, temp = read_gpu_stats(self._nvsmi)
        except Exception:
            util, temp = None, None
        if temp is None:                     # LibreHardwareMonitor fallback
            temp = self._lhm_gpu_temp
        self.gpu_util, self.gpu_temp = util, temp

    def _sample_temp(self):
        now = time.time()
        if now - self._temp_ts < self._temp_period:
            return
        self._temp_ts = now
        try:
            t = read_cpu_temp(psutil_mod=self._psutil)   # Linux sysfs
            if t is None:
                # LibreHardwareMonitor web server first (no WMI, no admin),
                # then WMI via PowerShell.
                cw, gw = read_temps_lhm_web()
                if cw is not None or gw is not None:
                    t = cw
                    if gw is not None:
                        self._lhm_gpu_temp = gw
                    self._temp_period = 2.0
                elif self._ps_exe:
                    t, gw = read_temps_powershell(self._ps_exe)
                    if gw is not None:
                        self._lhm_gpu_temp = gw
                    self._temp_period = 4.0   # PowerShell is slow; poll gently
            self.temp = t
        except Exception:
            self.temp = None

    def _sample(self):
        if not os.path.exists("/proc/stat"):
            if self._psutil:
                self.cores = self._psutil.cpu_percent(percpu=True)
                self.total = sum(self.cores) / max(1, len(self.cores))
            return
        with open("/proc/stat") as f:
            lines = [l.split() for l in f if l.startswith("cpu")]
        now = {}
        for parts in lines:
            vals = [int(v) for v in parts[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            now[parts[0]] = (sum(vals), idle)
        if self._prev:
            usages = {}
            for key, (tot, idle) in now.items():
                ptot, pidle = self._prev.get(key, (tot, idle))
                dt, di = tot - ptot, idle - pidle
                usages[key] = 100.0 * (dt - di) / dt if dt > 0 else 0.0
            self.total = usages.get("cpu")
            self.cores = [usages[k] for k in sorted(
                (k for k in usages if k != "cpu"), key=lambda k: int(k[3:]))]
        self._prev = now


# ------------------------------------------------------------------- palette

PAGE = "#0d0d0d"
CARD = "#1a1a19"
CARD2 = "#242423"
INK = "#ffffff"
INK2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
BASELINE = "#383835"
BLUE = "#3987e5"
BLUE_DIM = "#16283f"   # area fill under the chart line
AQUA = "#199e70"       # GPU series
RED = "#d03b3b"
GREEN = "#0ca30c"
AMBER = "#fab219"

WINDOW_S = 60          # chart history window, seconds
TICK_MS = 500          # GUI refresh interval

LOG_CHOICES = (("Off", 0), ("Every 1 min", 60),
               ("Every 5 min", 300), ("Every 10 min", 600))
LOG_FORMATS = (("Text (.txt)", "txt"), ("CSV (.csv)", "csv"), ("Excel (.xlsx)", "xlsx"))
CSV_HEADER = ["Time", "CPU %", "CPU Temp (C)", "GPU %", "GPU Temp (C)",
              "Load", "CPU Target %", "Workers", "GPU Intensity %", "Seconds Left"]


def new_log_path(fmt="txt"):
    """A fresh timestamped log file next to the program."""
    ext = {"csv": "csv", "xlsx": "xlsx"}.get(fmt, "txt")
    return os.path.join(_program_dir(),
                        time.strftime("cpu_load_log_%Y-%m-%d_%H-%M-%S." + ext))


def write_xlsx(path, rows):
    """Write rows (list of lists) as a minimal .xlsx workbook — pure stdlib
    (an .xlsx is just a zip of XML parts). int/float values become numeric
    cells; everything else becomes an inline string; blanks stay empty."""
    import zipfile
    from xml.sax.saxutils import escape

    def col(c):
        s = ""
        c += 1
        while c:
            c, r = divmod(c - 1, 26)
            s = chr(65 + r) + s
        return s

    body = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, val in enumerate(row):
            ref = col(ci) + str(ri)
            if val is None or val == "":
                cells.append('<c r="%s"/>' % ref)
            elif isinstance(val, bool):
                cells.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, val))
            elif isinstance(val, (int, float)):
                cells.append('<c r="%s"><v>%s</v></c>' % (ref, val))
            else:
                cells.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s'
                             '</t></is></c>' % (ref, escape(str(val))))
        body.append('<row r="%d">%s</row>' % (ri, "".join(cells)))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>%s</sheetData></worksheet>' % "".join(body))
    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Log" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def open_in_file_manager(path):
    """Open a file (or folder) with the OS default application."""
    try:
        if IS_WINDOWS:
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------- gui

class Slider(tk.Canvas):
    """Flat 0-100 slider drawn on a canvas (tk.Scale can't be styled well)."""

    def __init__(self, parent, command, **kw):
        super().__init__(parent, height=28, bg=CARD, highlightthickness=0, **kw)
        self.command = command
        self.value = 50
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)

    def get(self):
        return self.value

    def set(self, v):
        self.value = min(100, max(0, int(round(float(v)))))
        self._draw()
        self.command(self.value)

    def _drag(self, e):
        pad = 12
        w = max(1, self.winfo_width() - 2 * pad)
        self.set((e.x - pad) / w * 100)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 40:
            return
        pad, y = 12, 14
        x = pad + (w - 2 * pad) * self.value / 100
        self.create_line(pad, y, w - pad, y, fill=CARD2, width=6, capstyle="round")
        if x > pad:
            self.create_line(pad, y, x, y, fill=BLUE, width=6, capstyle="round")
        self.create_oval(x - 8, y - 8, x + 8, y + 8, fill=INK, outline=BLUE, width=3)


class App:
    def __init__(self, root, engine, sampler, gpu=None):
        self.root, self.engine, self.sampler = root, engine, sampler
        self.gpu = gpu or GpuBurner()
        self.history = []                      # (timestamp, cpu %)
        self.gpu_hist = []                     # (timestamp, gpu %)
        root.title("CPU Load Generator")
        root.configure(bg=PAGE)
        self._ctl = None               # the control column (set below)
        self._blank_icon = tk.PhotoImage(width=16, height=16)   # hides the Tk feather
        root.iconphoto(True, self._blank_icon)
        root.after(150, lambda: apply_dark_title_bar(root))

        base = "Segoe UI" if "Segoe UI" in tkfont.families() else "DejaVu Sans"
        self.f_title = (base, 16, "bold")
        self.f_label = (base, 10)
        self.f_small = (base, 9)
        self.f_big = (base, 30, "bold")
        self.f_mid = (base, 13, "bold")
        self.f_btn = (base, 12, "bold")

        outer = tk.Frame(root, bg=PAGE)
        outer.pack(fill="both", expand=True, padx=18, pady=14)

        # header ------------------------------------------------------------
        head = tk.Frame(outer, bg=PAGE)
        head.pack(fill="x", pady=(0, 12))
        tk.Label(head, text="CPU Load Generator", font=self.f_title, bg=PAGE, fg=INK).pack(side="left")
        tk.Label(head, text="  processor stress-testing tool", font=self.f_label,
                 bg=PAGE, fg=MUTED).pack(side="left")
        self.status = tk.Label(head, text="● idle", font=self.f_label, bg=PAGE, fg=MUTED)
        self.status.pack(side="right")

        body = tk.Frame(outer, bg=PAGE)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # controls card (left) ----------------------------------------------
        ctl = self._card(body)
        ctl.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        self._ctl = ctl

        self._ctl_header(ctl, "LOAD CONTROL").pack(anchor="w", padx=16, pady=(14, 2))

        self.target_lbl = tk.Label(ctl, text="50%", font=self.f_big, bg=CARD, fg=INK)
        self.target_lbl.pack(anchor="w", padx=16)
        self._ctl_header(ctl, "Load intensity").pack(anchor="w", padx=16, pady=(8, 0))
        self.target = Slider(ctl, command=self._on_target, width=240)
        self.target.pack(fill="x", padx=16)
        self.target.set(50)

        self._ctl_header(ctl, "Worker processes (cores)").pack(anchor="w", padx=16, pady=(14, 4))
        row = tk.Frame(ctl, bg=CARD)
        row.pack(anchor="w", padx=16)
        self.workers = self._spin(row, 1, engine.cpu_count * 2, engine.cpu_count)
        self.workers.pack(side="left")
        tk.Label(row, text=f"of {engine.cpu_count} cores", font=self.f_small,
                 bg=CARD, fg=MUTED).pack(side="left", padx=(10, 0))

        self._ctl_header(ctl, "Run duration  (0:00 = unlimited)").pack(anchor="w", padx=16, pady=(14, 4))
        dur = tk.Frame(ctl, bg=CARD)
        dur.pack(anchor="w", padx=16)
        self.dur_min = self._spin(dur, 0, 599, 5)
        self.dur_min.pack(side="left")
        tk.Label(dur, text="min", font=self.f_small, bg=CARD, fg=MUTED).pack(side="left", padx=(4, 10))
        self.dur_sec = self._spin(dur, 0, 59, 0)
        self.dur_sec.pack(side="left")
        tk.Label(dur, text="sec", font=self.f_small, bg=CARD, fg=MUTED).pack(side="left", padx=(4, 0))

        self._ctl_header(ctl, "Log to file").pack(anchor="w", padx=16, pady=(14, 4))
        self._log_interval = 0
        self._log_last = 0.0
        self._log_path = None            # current/most recent session log file
        self._log_active = False
        self._log_format = "txt"
        self._xlsx_rows = []             # accumulated rows for the .xlsx format
        lrow = tk.Frame(ctl, bg=CARD)
        lrow.pack(fill="x", padx=16)
        self.log_var = tk.StringVar(value=LOG_CHOICES[0][0])
        lom = tk.OptionMenu(lrow, self.log_var, *(c[0] for c in LOG_CHOICES),
                            command=self._on_log_change)
        lom.configure(bg=CARD2, fg=INK2, activebackground=CARD2, activeforeground=INK,
                      relief="flat", bd=0, highlightthickness=1,
                      highlightbackground=GRID, font=self.f_small, anchor="w",
                      cursor="hand2")
        lom["menu"].configure(bg=CARD2, fg=INK, font=self.f_small,
                              activebackground=BLUE, activeforeground="#ffffff")
        lom.pack(side="left", fill="x", expand=True)
        tk.Button(lrow, text="Open log", font=self.f_small, command=self._open_log,
                  bg=CARD2, fg=INK2, activebackground="#2e2e2c", activeforeground=INK,
                  relief="flat", bd=0, padx=10, cursor="hand2",
                  highlightthickness=1, highlightbackground=GRID
                  ).pack(side="left", padx=(8, 0), fill="y")
        self.logfmt_var = tk.StringVar(value=LOG_FORMATS[0][0])
        fom = tk.OptionMenu(ctl, self.logfmt_var, *(c[0] for c in LOG_FORMATS),
                            command=self._on_log_format_change)
        fom.configure(bg=CARD2, fg=INK2, activebackground=CARD2, activeforeground=INK,
                      relief="flat", bd=0, highlightthickness=1,
                      highlightbackground=GRID, font=self.f_small, anchor="w",
                      cursor="hand2")
        fom["menu"].configure(bg=CARD2, fg=INK, font=self.f_small,
                              activebackground=BLUE, activeforeground="#ffffff")
        fom.pack(anchor="w", fill="x", padx=16, pady=(6, 0))
        self.log_lbl = tk.Label(ctl, text="", font=self.f_small, bg=CARD, fg=MUTED)
        self.log_lbl.pack(anchor="w", padx=16)

        self._ctl_header(ctl, "GPU load (OpenCL)").pack(anchor="w", padx=16, pady=(14, 4))
        if self.gpu.available:
            grow = tk.Frame(ctl, bg=CARD)
            grow.pack(anchor="w", fill="x", padx=16)
            self.gpu_on = tk.BooleanVar(value=False)
            tk.Checkbutton(grow, text="Enable", variable=self.gpu_on, font=self.f_small,
                           bg=CARD, fg=INK2, activebackground=CARD,
                           activeforeground=INK, selectcolor=CARD2,
                           command=self._on_gpu_toggle).pack(side="left")
            self.gpu_lbl = tk.Label(grow, text="50%", font=self.f_mid, bg=CARD, fg=INK)
            self.gpu_lbl.pack(side="right")
            self.gpu_slider = Slider(ctl, command=self._on_gpu_target, width=240)
            self.gpu_slider.pack(fill="x", padx=16)
            self.gpu_slider.set(50)
            if len(self.gpu.devices) > 1:
                self.gpu_dev_var = tk.StringVar(value=self.gpu.device_name)
                om = tk.OptionMenu(ctl, self.gpu_dev_var, *self.gpu.devices,
                                   command=self._on_gpu_device)
                om.configure(bg=CARD2, fg=INK2, activebackground=CARD2,
                             activeforeground=INK, relief="flat", bd=0,
                             highlightthickness=1, highlightbackground=GRID,
                             font=self.f_small, anchor="w", cursor="hand2")
                om["menu"].configure(bg=CARD2, fg=INK, font=self.f_small,
                                     activebackground=BLUE, activeforeground="#ffffff")
                om.pack(anchor="w", fill="x", padx=16)
            else:
                tk.Label(ctl, text=(self.gpu.device_name or "GPU")[:34],
                         font=self.f_small, bg=CARD, fg=MUTED).pack(anchor="w", padx=16)
        else:
            self.gpu_on = tk.BooleanVar(value=False)
            tk.Label(ctl, text="No OpenCL GPU detected", font=self.f_small,
                     bg=CARD, fg=MUTED).pack(anchor="w", padx=16)

        self.go = tk.Button(
            ctl, text="▶  Start load", font=self.f_btn, command=self._toggle,
            bg=BLUE, fg="white", activebackground="#5598e7", activeforeground="white",
            relief="flat", bd=0, padx=10, pady=10, cursor="hand2")
        self.go.pack(fill="x", padx=16, pady=(18, 6))

        tk.Label(ctl, justify="left", font=self.f_small, bg=CARD, fg=MUTED,
                 text="Each worker runs a 100 ms duty cycle —\n"
                      "busy for the target percentage, sleeping\n"
                      "for the rest. Intensity can be changed\n"
                      "while the load is running."
                 ).pack(anchor="w", padx=16, pady=(4, 14))

        # dashboard (right) --------------------------------------------------
        dash = tk.Frame(body, bg=PAGE)
        dash.grid(row=0, column=1, sticky="nsew")
        dash.columnconfigure((0, 1, 2, 3, 4), weight=1, uniform="tiles")
        dash.rowconfigure(1, weight=1)

        self.tile_cpu = self._tile(dash, 0, "CPU USAGE")
        self.tile_temp = self._tile(dash, 1, "CPU TEMP")
        self.tile_gpu = self._tile(dash, 2, "GPU USAGE")
        self.tile_gpu_temp = self._tile(dash, 3, "GPU TEMP")
        self.tile_left = self._tile(dash, 4, "TIME LEFT")

        # shown in the temperature tile when Windows exposes no sensor
        self.temp_hint = tk.Label(self.tile_temp.master, font=(base, 8, "underline"),
                                  bg=CARD, fg=BLUE, cursor="hand2")
        self.temp_hint.bind("<Button-1>", lambda e: self._temp_hint_click())
        self._temp_hint_mode = None   # None | "starting" | "elevate" | "lhm"
        self._hwmon_started_at = None
        root.after(4000, self._autostart_hw_monitor)

        chart_card = self._card(dash)
        chart_card.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(12, 0))
        chead = tk.Frame(chart_card, bg=CARD)
        chead.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(chead, text="Usage — last 60 seconds",
                 font=self.f_label, bg=CARD, fg=MUTED).pack(side="left")
        self.legend_gpu = tk.Label(chead, text="— GPU", font=self.f_small, bg=CARD, fg=AQUA)
        self.legend_cpu = tk.Label(chead, text="— CPU", font=self.f_small, bg=CARD, fg=BLUE)
        self._legend_shown = False
        self.chart = tk.Canvas(chart_card, bg=CARD, highlightthickness=0, height=210)
        self.chart.pack(fill="both", expand=True, padx=14)
        self.bars = tk.Canvas(chart_card, bg=CARD, highlightthickness=0,
                              height=max(46, 24 * ((engine.cpu_count + 1) // 2)))
        self.bars.pack(fill="x", padx=14, pady=(6, 12))

        # temperature bar chart (right of the usage chart)
        temp_card = self._card(dash)
        temp_card.grid(row=1, column=4, sticky="nsew", padx=(12, 0), pady=(12, 0))
        tk.Label(temp_card, text="Temperature", font=self.f_label,
                 bg=CARD, fg=MUTED).pack(anchor="w", padx=12, pady=(10, 2))
        self.tchart = tk.Canvas(temp_card, bg=CARD, highlightthickness=0)
        self.tchart.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._anim_win = None
        self._anim_lbl = None
        self._anim_img = None
        self._anim_closed = False    # user closed the window during this run
        self._fit_window()
        self._tick()
        self._anim_tick()

    def _fit_window(self):
        """Open tall enough for the whole control column (which grows with
        core count and the extra panels), then center on screen."""
        self.root.update_idletasks()
        need_h = self._ctl.winfo_reqheight() + 28 * 2 + 40   # padding + title bar
        h = max(660, need_h)
        w = 1080
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        h = min(h, sh - 60)                                  # never taller than screen
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.root.minsize(880, min(h, 620))

    # -- widget helpers ------------------------------------------------------
    def _card(self, parent):
        return tk.Frame(parent, bg=CARD, highlightbackground=GRID, highlightthickness=1)

    def _ctl_header(self, parent, text):
        return tk.Label(parent, text=text, font=self.f_label, bg=CARD, fg=INK2)

    def _spin(self, parent, lo, hi, val):
        var = tk.StringVar(value=str(val))
        sp = tk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=4,
                        font=self.f_mid, justify="center", bg=CARD2, fg=INK,
                        buttonbackground=CARD2, relief="flat",
                        insertbackground=INK, highlightthickness=1,
                        highlightbackground=GRID, highlightcolor=BLUE)
        sp.var = var
        return sp

    def _tile(self, parent, col, caption):
        card = self._card(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
        tk.Label(card, text=caption, font=self.f_small, bg=CARD, fg=MUTED).pack(anchor="w", padx=14, pady=(10, 0))
        val = tk.Label(card, text="—", font=(self.f_big[0], 22, "bold"), bg=CARD, fg=INK)
        val.pack(anchor="w", padx=14, pady=(0, 10))
        return val

    # -- actions --------------------------------------------------------------
    def _on_target(self, v):
        self.target_lbl.config(text=f"{int(float(v))}%")
        self.engine.set_target(float(v))

    def _on_gpu_target(self, v):
        self.gpu_lbl.config(text=f"{int(float(v))}%")
        self.gpu.intensity = float(v)

    # -- stats logging ----------------------------------------------------------
    def _on_log_change(self, choice):
        self._log_interval = dict(LOG_CHOICES).get(choice, 0)
        if self._log_interval and not self._log_active:
            self._start_log_session()
        elif not self._log_interval and self._log_active:
            self._end_log_session()

    def _on_log_format_change(self, choice):
        fmt = dict(LOG_FORMATS).get(choice, "txt")
        if fmt == self._log_format:
            return
        if self._log_active:          # close current file, then restart in new format
            self._end_log_session()
            self._log_format = fmt
            self._start_log_session()
        else:
            self._log_format = fmt

    def _start_log_session(self):
        """Begin a new timestamped log file in the current format."""
        self._log_path = new_log_path(self._log_format)
        self._log_active = True
        if self._log_format == "csv":
            self._write_csv(CSV_HEADER)
        elif self._log_format == "xlsx":
            self._xlsx_rows = [list(CSV_HEADER)]
            self._write_xlsx()
        else:
            self._write_log("--- logging started (%s) ---" % self.log_var.get().lower())
        self._log_last = 0.0              # first sample on the next tick

    def _end_log_session(self):
        if not self._log_active:
            return
        if self._log_format == "txt":
            self._write_log("--- logging stopped ---")
        self._log_active = False

    def _open_log(self):
        """Open the current (or most recent) log file; its folder if none."""
        target = self._log_path
        if not target or not os.path.exists(target):
            target = os.path.dirname(self._log_path or new_log_path(self._log_format))
        if not open_in_file_manager(target):
            self.log_lbl.config(text="cannot open: " + target)

    def _log_write(self, writer, newline=None):
        """Run writer(file) on the log path, falling back to the home dir."""
        for path in (self._log_path, os.path.join(os.path.expanduser("~"),
                                                  os.path.basename(self._log_path))):
            try:
                with open(path, "a", encoding="utf-8", newline=newline) as f:
                    writer(f)
                self._log_path = path
                shown = path if len(path) <= 36 else "…" + path[-35:]
                self.log_lbl.config(text="→ " + shown)
                return
            except OSError:
                continue
        self.log_lbl.config(text="cannot write log file")

    def _write_log(self, text):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " | " + text + "\n"
        self._log_write(lambda f: f.write(line))

    def _write_csv(self, row):
        self._log_write(lambda f: csv.writer(f).writerow(row), newline="")

    def _write_xlsx(self):
        """Rewrite the whole .xlsx (zip files can't be appended to)."""
        for path in (self._log_path, os.path.join(os.path.expanduser("~"),
                                                  os.path.basename(self._log_path))):
            try:
                write_xlsx(path, self._xlsx_rows)
                self._log_path = path
                shown = path if len(path) <= 36 else "…" + path[-35:]
                self.log_lbl.config(text="→ " + shown)
                return
            except OSError:
                continue
        self.log_lbl.config(text="cannot write log file")

    def _maybe_log(self, now):
        if not self._log_interval or now - self._log_last < self._log_interval:
            return
        self._log_last = now
        s = self.sampler
        running = self.engine.running
        rem = self.engine.remaining
        if self._log_format in ("csv", "xlsx"):
            num = lambda v: "" if v is None else round(v)
            row = [
                time.strftime("%Y-%m-%d %H:%M:%S"),
                num(s.total), num(s.temp), num(s.gpu_util), num(s.gpu_temp),
                "ON" if running else "off",
                int(self.engine.target) if running else "",
                self.engine.workers if running else "",
                int(self.gpu.intensity) if (running and self.gpu.running) else "",
                int(rem) if (running and rem is not None) else "",
            ]
            if self._log_format == "csv":
                self._write_csv(row)
            else:
                self._xlsx_rows.append(row)
                self._write_xlsx()
            return
        fmt = lambda v, u: "—" if v is None else "%.0f%s" % (v, u)
        text = "CPU %s %s | GPU %s %s" % (
            fmt(s.total, "%"), fmt(s.temp, "°C"),
            fmt(s.gpu_util, "%"), fmt(s.gpu_temp, "°C"))
        if running:
            text += " | load ON: target %d%%, workers %d" % (
                int(self.engine.target), self.engine.workers)
            if self.gpu.running:
                text += ", GPU intensity %d%%" % int(self.gpu.intensity)
            if rem is not None:
                text += ", %ds left" % int(rem)
        else:
            text += " | load off"
        self._write_log(text)

    def _on_gpu_device(self, name):
        """Switch the burner to another GPU (e.g. onboard Intel -> NVIDIA)."""
        try:
            idx = self.gpu.devices.index(name)
        except ValueError:
            return
        if idx == self.gpu.device_index:
            return
        was_running = self.gpu.running
        old = self.gpu
        old.stop()
        new = GpuBurner(device_index=idx)
        if not new.available:      # switch failed: keep the working burner
            self.gpu_dev_var.set(old.device_name or "GPU")
            if was_running:
                old.start()
            return
        new.intensity = old.intensity
        new.set_size(*old._size)
        self.gpu = new
        self._destroy_anim()       # reopen with the new device in the title
        if was_running:
            new.start()

    def _on_gpu_toggle(self):
        if not self.engine.running:
            return
        if self.gpu_on.get():
            self.gpu.start()
        else:
            self.gpu.stop()

    def _duration_s(self):
        try:
            return max(0, int(self.dur_min.var.get() or 0)) * 60 + \
                   max(0, int(self.dur_sec.var.get() or 0))
        except ValueError:
            return 0

    def _workers_n(self):
        try:
            return int(self.workers.var.get() or 1)
        except ValueError:
            return self.engine.cpu_count

    def _autostart_hw_monitor(self):
        """If no CPU temp is readable but a LibreHardwareMonitor copy sits
        next to the program, start it — its WMI sensors appear within
        seconds and the temperature tile fills in automatically."""
        if not IS_WINDOWS or self.sampler.temp is not None:
            return
        exe = find_hw_monitor()
        if exe and launch_hw_monitor(exe):
            self._hwmon_started_at = time.time()

    def _update_temp_hint(self, temp):
        """Offer a fix in the temperature tile when Windows shows no sensor."""
        if temp is not None or not IS_WINDOWS:
            mode = None
        elif (self._hwmon_started_at is not None and
              time.time() - self._hwmon_started_at < 40):
            mode = "starting"
        elif not is_windows_admin():
            mode = "elevate"
        else:
            mode = "lhm"
        if mode == self._temp_hint_mode:
            return
        self._temp_hint_mode = mode
        if mode is None:
            self.temp_hint.pack_forget()
        else:
            texts = {"starting": "starting LibreHardwareMonitor…",
                     "elevate": "click to restart as Administrator",
                     "lhm": "no sensor — install LibreHardwareMonitor"}
            self.temp_hint.config(text=texts[mode],
                                  cursor="hand2" if mode == "elevate" else "arrow")
            self.temp_hint.pack(anchor="w", padx=14, pady=(0, 8))

    def _temp_hint_click(self):
        if self._temp_hint_mode == "elevate" and relaunch_as_admin():
            self.engine.stop()
            self.root.destroy()
            sys.exit(0)

    def _toggle(self):
        if self.engine.running:
            self.engine.stop()
            self.gpu.stop()
        else:
            self.engine.set_target(self.target.get())
            self.engine.start(self._workers_n(), self._duration_s())
            if self.gpu_on.get() and self.gpu.available:
                self._anim_closed = False
                self.gpu.start()
        self._paint_state()

    # -- gpu animation window ---------------------------------------------------
    def _open_anim(self):
        win = tk.Toplevel(self.root)
        win.title("GPU Stress Animation — " + (self.gpu.device_name or "GPU")[:40])
        win.configure(bg=PAGE)
        win.minsize(200, 240)
        win.geometry("%dx%d+%d+%d" % (
            self.gpu.FRAME_W + 20, self.gpu.FRAME_H + 46,
            self.root.winfo_rootx() + self.root.winfo_width() + 16,
            self.root.winfo_rooty()))
        self._anim_lbl = tk.Label(win, bg=PAGE, bd=0)
        self._anim_lbl.pack(fill="both", expand=True, padx=10, pady=(10, 2))
        tk.Label(win, text="rendered on the GPU — the intensity slider sets the load",
                 font=self.f_small, bg=PAGE, fg=MUTED).pack(pady=(0, 6))
        win.protocol("WM_DELETE_WINDOW", self._close_anim_by_user)
        win.bind("<Configure>", self._on_anim_resize)
        self._anim_win = win
        win.after(150, lambda: apply_dark_title_bar(win))

    def _on_anim_resize(self, e):
        if e.widget is self._anim_win:
            self.gpu.set_size(e.width - 20, e.height - 46)

    def _close_anim_by_user(self):
        self._anim_closed = True     # load keeps running, window stays closed
        self._destroy_anim()

    def _destroy_anim(self):
        if self._anim_win is not None:
            self._anim_win.destroy()
        self._anim_win, self._anim_lbl, self._anim_img = None, None, None

    def _anim_tick(self):
        if self.gpu.running:
            if self._anim_win is None and not self._anim_closed:
                self._open_anim()
            if self._anim_lbl is not None:
                frame = self.gpu.get_frame()
                if frame:
                    try:
                        img = tk.PhotoImage(data=frame)
                        self._anim_lbl.configure(image=img)
                        self._anim_img = img   # keep a reference or tk drops it
                    except tk.TclError:
                        pass
        elif self._anim_win is not None:
            self._destroy_anim()
        self.root.after(80, self._anim_tick)

    def _paint_state(self):
        if self.engine.running:
            self.go.config(text="■  Stop load", bg=RED, activebackground="#e66767")
            self.status.config(text="● load active", fg=GREEN)
        else:
            self.go.config(text="▶  Start load", bg=BLUE, activebackground="#5598e7")
            self.status.config(text="● idle", fg=MUTED)

    # -- refresh loop ----------------------------------------------------------
    def _tick(self):
        s = self.sampler
        now = time.time()
        if s.total is not None:
            self.history.append((now, s.total))
            self.history = [(t, v) for t, v in self.history if t >= now - WINDOW_S - 2]
        if s.gpu_util is not None:
            self.gpu_hist.append((now, s.gpu_util))
            self.gpu_hist = [(t, v) for t, v in self.gpu_hist if t >= now - WINDOW_S - 2]

        # the duration timer stops the CPU engine; follow it with the GPU
        if self.gpu.running and not self.engine.running:
            self.gpu.stop()

        self.tile_cpu.config(text="—" if s.total is None else f"{s.total:.0f}%")
        self.tile_gpu.config(text="—" if s.gpu_util is None else f"{s.gpu_util:.0f}%")
        if s.gpu_temp is None:
            self.tile_gpu_temp.config(text="—", fg=INK)
        else:
            color = RED if s.gpu_temp >= 85 else AMBER if s.gpu_temp >= 70 else INK
            self.tile_gpu_temp.config(text=f"{s.gpu_temp:.0f}°C", fg=color)
        if self.gpu_hist and not self._legend_shown:
            self._legend_shown = True
            self.legend_gpu.pack(side="right")
            self.legend_cpu.pack(side="right", padx=(0, 10))
        if s.temp is None:
            self.tile_temp.config(text="—", fg=INK)
        else:
            color = RED if s.temp >= 85 else AMBER if s.temp >= 70 else INK
            self.tile_temp.config(text=f"{s.temp:.0f}°C", fg=color)
        self._update_temp_hint(s.temp)

        rem = self.engine.remaining
        if not self.engine.running:
            self.tile_left.config(text="—")
        elif rem is None:
            self.tile_left.config(text="∞")
        else:
            m, sec = divmod(int(rem + 0.5), 60)
            h, m = divmod(m, 60)
            self.tile_left.config(text=f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}")

        self._maybe_log(now)
        self._paint_state()
        self._draw_chart(now)
        self._draw_bars(s.cores)
        self._draw_temp_bars(s.temp, s.gpu_temp)
        self.root.after(TICK_MS, self._tick)

    # -- drawing ---------------------------------------------------------------
    def _draw_chart(self, now):
        c = self.chart
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        pl, pr, pt, pb = 34, 10, 8, 20
        pw, ph = w - pl - pr, h - pt - pb
        t0 = now - WINDOW_S
        x = lambda t: pl + pw * (t - t0) / WINDOW_S
        y = lambda v: pt + ph * (1 - v / 100.0)

        for v in (0, 25, 50, 75, 100):
            c.create_line(pl, y(v), w - pr, y(v), fill=GRID)
            c.create_text(pl - 7, y(v), text=str(v), anchor="e", fill=MUTED, font=self.f_small)
        for sec in (60, 45, 30, 15, 0):
            c.create_text(x(now - sec), h - pb + 4, anchor="n", fill=MUTED, font=self.f_small,
                          text="now" if sec == 0 else f"-{sec}s")
        c.create_line(pl, y(0), w - pr, y(0), fill=BASELINE)

        pts = [(t, v) for t, v in self.history if t >= t0]
        if len(pts) > 1:
            line = [coord for t, v in pts for coord in (x(t), y(v))]
            area = [x(pts[0][0]), y(0)] + line + [x(pts[-1][0]), y(0)]
            c.create_polygon(*area, fill=BLUE_DIM, outline="")
            c.create_line(*line, fill=BLUE, width=2, joinstyle="round")
            lx, ly = x(pts[-1][0]), y(pts[-1][1])
            c.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill=BLUE, outline=CARD, width=2)

        gpts = [(t, v) for t, v in self.gpu_hist if t >= t0]
        if len(gpts) > 1:
            line = [coord for t, v in gpts for coord in (x(t), y(v))]
            c.create_line(*line, fill=AQUA, width=2, joinstyle="round")
            lx, ly = x(gpts[-1][0]), y(gpts[-1][1])
            c.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill=AQUA, outline=CARD, width=2)

    def _draw_bars(self, cores):
        c = self.bars
        c.delete("all")
        w = c.winfo_width()
        if w < 50 or not cores:
            return
        cols = 2 if len(cores) > 1 else 1
        col_w = w // cols
        row_h = 24
        for i, v in enumerate(cores):
            cx = (i % cols) * col_w
            cy = (i // cols) * row_h + row_h // 2
            label_w, pct_w, gap = 52, 40, 10
            c.create_text(cx + gap, cy, text=f"Core {i}", anchor="w",
                          fill=MUTED, font=self.f_small)
            bar_x1 = cx + gap + label_w
            bar_x2 = cx + col_w - pct_w - gap
            if bar_x2 - bar_x1 > 30:
                c.create_rectangle(bar_x1, cy - 4, bar_x2, cy + 4, fill=CARD2, outline="")
                fill_w = (bar_x2 - bar_x1) * min(100.0, v) / 100.0
                c.create_rectangle(bar_x1, cy - 4, bar_x1 + fill_w, cy + 4, fill=BLUE, outline="")
            c.create_text(cx + col_w - gap, cy, text=f"{v:.0f}%", anchor="e",
                          fill=INK2, font=self.f_small)

    def _draw_temp_bars(self, cpu_temp, gpu_temp):
        c = self.tchart
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 40 or h < 40:
            return
        pl, pr, pt, pb = 30, 8, 18, 20
        ph = h - pt - pb
        ymax = 100.0
        y = lambda v: pt + ph * (1 - min(v, ymax) / ymax)

        for v in (0, 25, 50, 75, 100):        # scale + gridlines
            c.create_line(pl, y(v), w - pr, y(v), fill=GRID)
            c.create_text(pl - 6, y(v), text=str(v), anchor="e", fill=MUTED, font=self.f_small)
        for thr, col in ((70, AMBER), (85, RED)):   # warning thresholds
            c.create_line(pl, y(thr), w - pr, y(thr), fill=col, dash=(3, 3))

        items = (("CPU", cpu_temp), ("GPU", gpu_temp))
        slot = (w - pl - pr) / len(items)
        bw = min(56, slot * 0.5)
        for i, (name, temp) in enumerate(items):
            cx = pl + slot * (i + 0.5)
            x0, x1 = cx - bw / 2, cx + bw / 2
            c.create_rectangle(x0, pt, x1, y(0), fill=CARD2, outline="")   # track
            if temp is None:
                c.create_text(cx, y(0) - 12, text="—", fill=MUTED, font=self.f_mid)
            else:
                col = RED if temp >= 85 else AMBER if temp >= 70 else GREEN
                ty = y(temp)
                c.create_rectangle(x0, ty, x1, y(0), fill=col, outline="")
                c.create_text(cx, ty - 10, text="%.0f°C" % temp, fill=INK,
                              font=(self.f_mid[0], 11, "bold"))
            c.create_text(cx, h - pb + 4, text=name, anchor="n", fill=INK2, font=self.f_small)


def main():
    engine = LoadEngine()
    sampler = CpuSampler()
    root = tk.Tk()
    App(root, engine, sampler)
    try:
        root.mainloop()
    finally:
        engine.stop()


if __name__ == "__main__":
    # Required for frozen builds (PyInstaller etc.): worker processes
    # re-execute this entry point, and without freeze_support() each one
    # would open another copy of the GUI.
    mp.freeze_support()
    mp.set_start_method("spawn", force=True)
    main()
