#!/usr/bin/env python3
"""CPU Load Generator — native Python GUI (tkinter).

Generates a controllable CPU load for a chosen duration and shows live
CPU usage and temperature. Pure Python standard library — no
dependencies (on Debian/Ubuntu, tkinter comes from the python3-tk
package; on Windows, installing psutil enables CPU-usage readings).

Usage:
    python3 cpu_load_gui.py
"""

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

_CL_KERNEL = b"""
__kernel void burn(__global float *buf, const int iters) {
    int gid = get_global_id(0);
    float x = buf[gid] + gid * 1e-7f;
    for (int i = 0; i < iters; i++) {
        x = mad(x, 1.0000001f, 1e-7f);
        x = sin(x) * cos(x) + x * 0.5f + 0.25f;
        x = x - floor(x);
    }
    buf[gid] = x;
}
"""


class GpuBurner:
    """Generates GPU load through OpenCL, which ships with GPU drivers on
    Windows/Linux/macOS — no Python packages needed. Runs kernel bursts on
    a duty cycle, like the CPU workers. Unavailable if no OpenCL GPU."""

    GLOBAL_SIZE = 1 << 20
    TARGET_BURST = 0.03          # seconds per kernel launch

    def __init__(self, device_type=_CL_DEVICE_TYPE_GPU):
        self.available = False
        self.reason = None
        self.device_name = None
        self.intensity = 50.0
        self._stop = threading.Event()
        self._thread = None
        self._iters = 256
        try:
            self._init_cl(device_type)
            self.available = True
        except Exception as e:
            self.reason = str(e) or "OpenCL initialization failed"

    def _init_cl(self, device_type):
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

        dev = None
        for p in plats:
            dn = ct.c_uint(0)
            if cl.clGetDeviceIDs(ct.c_void_p(p), ct.c_ulonglong(device_type),
                                 0, None, ct.byref(dn)) == 0 and dn.value:
                devs = (ct.c_void_p * dn.value)()
                cl.clGetDeviceIDs(ct.c_void_p(p), ct.c_ulonglong(device_type), dn, devs, None)
                dev = devs[0]
                break
        if dev is None:
            raise RuntimeError("no OpenCL GPU device")

        namebuf = ct.create_string_buffer(256)
        cl.clGetDeviceInfo(ct.c_void_p(dev), _CL_DEVICE_NAME, ct.c_size_t(256), namebuf, None)
        self.device_name = namebuf.value.decode(errors="replace") or "GPU"

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
        kern = cl.clCreateKernel(ct.c_void_p(prog), b"burn", ct.byref(err))
        if not kern or err.value != 0:
            raise RuntimeError("clCreateKernel failed")

        membuf = cl.clCreateBuffer(ct.c_void_p(ctx), ct.c_ulonglong(_CL_MEM_READ_WRITE),
                                   ct.c_size_t(self.GLOBAL_SIZE * 4), None, ct.byref(err))
        if not membuf or err.value != 0:
            raise RuntimeError("clCreateBuffer failed")
        mem_handle = ct.c_void_p(membuf)
        if cl.clSetKernelArg(ct.c_void_p(kern), 0, ct.c_size_t(ct.sizeof(ct.c_void_p)),
                             ct.byref(mem_handle)) != 0:
            raise RuntimeError("clSetKernelArg failed")

        self._cl, self._queue, self._kern = cl, queue, kern
        self._mem = mem_handle          # keep alive

    def _launch(self, iters):
        cl = self._cl
        arg = ct.c_int(iters)
        if cl.clSetKernelArg(ct.c_void_p(self._kern), 1, ct.c_size_t(4), ct.byref(arg)) != 0:
            raise RuntimeError("clSetKernelArg failed")
        gsz = ct.c_size_t(self.GLOBAL_SIZE)
        if cl.clEnqueueNDRangeKernel(ct.c_void_p(self._queue), ct.c_void_p(self._kern),
                                     1, None, ct.byref(gsz), None, 0, None, None) != 0:
            raise RuntimeError("clEnqueueNDRangeKernel failed")
        cl.clFinish(ct.c_void_p(self._queue))

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
        period = 0.1
        while not self._stop.is_set():
            t = min(100.0, max(0.0, self.intensity))
            if t <= 0:
                time.sleep(period)
                continue
            budget = period * t / 100.0
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < budget and not self._stop.is_set():
                k0 = time.perf_counter()
                try:
                    self._launch(self._iters)
                except Exception as e:
                    self.available, self.reason = False, str(e)
                    return
                dt = time.perf_counter() - k0
                if dt > 0:   # steer launches toward TARGET_BURST seconds each
                    self._iters = max(16, min(1 << 15,
                                              int(self._iters * self.TARGET_BURST / dt) or 16))
            rest = period - (time.perf_counter() - t0)
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
# rights and is not exposed on every machine).
_PS_TEMP_SCRIPT = (
    "$t=$null;"
    "foreach($ns in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor'){"
    "try{$s=Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop|"
    "Where-Object{$_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU|Package|Core'}|"
    "Sort-Object Value -Descending|Select-Object -First 1;"
    "if($s){$t=$s.Value;break}}catch{}};"
    "if($null -eq $t){"
    "try{$z=Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
    "-ErrorAction Stop|Sort-Object CurrentTemperature -Descending|Select-Object -First 1;"
    "if($z){$t=($z.CurrentTemperature/10)-273.15}}catch{}};"
    "if($null -ne $t){[math]::Round($t,1)}"
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


def relaunch_as_admin():
    """Restart this script elevated (UAC prompt). True if launch succeeded."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        script = os.path.abspath(sys.argv[0])
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}"', None, 1)
        return rc > 32  # ShellExecute returns >32 on success
    except (OSError, AttributeError):
        return False


def read_temp_powershell(exe):
    flags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
    try:
        out = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", _PS_TEMP_SCRIPT],
            capture_output=True, text=True, timeout=10, creationflags=flags,
        ).stdout.strip()
        if not out:
            return None
        val = float(out.splitlines()[-1].replace(",", "."))
        return val if 0 < val < 150 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


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
            self.gpu_util, self.gpu_temp = read_gpu_stats(self._nvsmi)
        except Exception:
            self.gpu_util, self.gpu_temp = None, None

    def _sample_temp(self):
        now = time.time()
        if now - self._temp_ts < self._temp_period:
            return
        self._temp_ts = now
        try:
            t = read_cpu_temp(psutil_mod=self._psutil)
            if t is None and self._ps_exe:
                t = read_temp_powershell(self._ps_exe)
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
        root.geometry("1080x660")
        root.minsize(880, 560)

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
            name = self.gpu.device_name or "GPU"
            tk.Label(ctl, text=name[:34], font=self.f_small, bg=CARD, fg=MUTED
                     ).pack(anchor="w", padx=16)
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
        self._temp_hint_mode = None   # None | "elevate" | "lhm"

        chart_card = self._card(dash)
        chart_card.grid(row=1, column=0, columnspan=5, sticky="nsew", pady=(12, 0))
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

        self._tick()

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

    def _update_temp_hint(self, temp):
        """Offer a fix in the temperature tile when Windows shows no sensor."""
        if temp is not None or not IS_WINDOWS:
            mode = None
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
            self.temp_hint.config(
                text="click to restart as Administrator" if mode == "elevate"
                else "no sensor — install LibreHardwareMonitor",
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
                self.gpu.start()
        self._paint_state()

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

        self._paint_state()
        self._draw_chart(now)
        self._draw_bars(s.cores)
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
    mp.set_start_method("spawn", force=True)
    main()
