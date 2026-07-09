#!/usr/bin/env python3
"""CPU Load Generator — native Python GUI (tkinter).

Generates a controllable CPU load for a chosen duration and shows live
CPU usage and temperature. Pure Python standard library — no
dependencies (on Debian/Ubuntu, tkinter comes from the python3-tk
package).

Usage:
    python3 cpu_load_gui.py
"""

import glob
import multiprocessing as mp
import os
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


# ------------------------------------------------------- cpu & temp sampling

_CPU_SENSOR_HINTS = ("pkg", "cpu", "core", "x86", "soc", "k10temp", "zenpower", "acpitz")


def read_cpu_temp(sys_root="/sys", psutil_mod=None):
    """CPU temperature in °C, or None if no sensor is available."""
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


class CpuSampler:
    """Background thread sampling CPU usage (via /proc/stat on Linux,
    psutil elsewhere if installed) and CPU temperature."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.total = None          # overall usage %, or None if unavailable
        self.cores = []            # per-core usage %
        self.temp = None           # CPU temperature °C, or None if no sensor
        self._prev = None
        self._psutil = None
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
            try:
                self.temp = read_cpu_temp(psutil_mod=self._psutil)
            except Exception:
                self.temp = None
            time.sleep(self.interval)

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


# ------------------------------------------------------------ hebrew for tk

# Tk has no bidi support on X11/Windows, so Hebrew strings render in logical
# (reversed) order. Convert them to visual order ourselves: reverse the whole
# string (mirroring brackets), then flip LTR runs (latin/digits) back.
# On macOS (aqua) the native text engine handles bidi, so leave text as-is.

_TK_NEEDS_BIDI = True
_MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{"}


def H(s):
    if not _TK_NEEDS_BIDI or not any("֐" <= c <= "׿" for c in s):
        return s
    rev = [_MIRROR.get(c, c) for c in reversed(s)]
    is_ltr = lambda c: c.isascii() and c.isalnum()
    out, i, n = [], 0, len(rev)
    while i < n:
        if is_ltr(rev[i]):
            j = i
            while j < n and (is_ltr(rev[j]) or
                             (rev[j] in ".:%-" and j + 1 < n and is_ltr(rev[j + 1]))):
                j += 1
            out.extend(rev[i:j][::-1])
            i = j
        else:
            out.append(rev[i])
            i += 1
    return "".join(out)


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
    def __init__(self, root, engine, sampler):
        self.root, self.engine, self.sampler = root, engine, sampler
        self.history = []                      # (timestamp, cpu %)
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
        tk.Label(head, text=H("מחולל עומס CPU"), font=self.f_title, bg=PAGE, fg=INK).pack(side="right")
        tk.Label(head, text="  " + H("כלי בדיקת עומס למעבד"), font=self.f_label,
                 bg=PAGE, fg=MUTED).pack(side="right")
        self.status = tk.Label(head, text="● " + H("מנוחה"), font=self.f_label, bg=PAGE, fg=MUTED)
        self.status.pack(side="left")

        body = tk.Frame(outer, bg=PAGE)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        # controls card (right, RTL) ----------------------------------------
        ctl = self._card(body)
        ctl.grid(row=0, column=1, sticky="ns", padx=(12, 0))

        self._ctl_header(ctl, H("בקרת עומס")).pack(anchor="e", padx=16, pady=(14, 2))

        self.target_lbl = tk.Label(ctl, text="50%", font=self.f_big, bg=CARD, fg=INK)
        self.target_lbl.pack(anchor="e", padx=16)
        self._ctl_header(ctl, H("עוצמת עומס")).pack(anchor="e", padx=16, pady=(8, 0))
        self.target = Slider(ctl, command=self._on_target, width=240)
        self.target.pack(fill="x", padx=16)
        self.target.set(50)

        self._ctl_header(ctl, H("תהליכי עומס (ליבות)")).pack(anchor="e", padx=16, pady=(14, 4))
        self.workers = self._spin_row(ctl, 1, engine.cpu_count * 2, engine.cpu_count,
                                      H(f"מתוך {engine.cpu_count} ליבות"))

        self._ctl_header(ctl, H("משך ריצה (0:00 = ללא הגבלה)")).pack(anchor="e", padx=16, pady=(14, 4))
        dur = tk.Frame(ctl, bg=CARD)
        dur.pack(anchor="e", padx=16)
        tk.Label(dur, text=H("שניות"), font=self.f_small, bg=CARD, fg=MUTED).pack(side="left", padx=(4, 10))
        self.dur_sec = self._spin(dur, 0, 59, 0)
        self.dur_sec.pack(side="left")
        tk.Label(dur, text=H("דקות"), font=self.f_small, bg=CARD, fg=MUTED).pack(side="left", padx=(4, 10))
        self.dur_min = self._spin(dur, 0, 599, 5)
        self.dur_min.pack(side="left")

        self.go = tk.Button(
            ctl, text="▶  " + H("התחל עומס"), font=self.f_btn, command=self._toggle,
            bg=BLUE, fg="white", activebackground="#5598e7", activeforeground="white",
            relief="flat", bd=0, padx=10, pady=10, cursor="hand2")
        self.go.pack(fill="x", padx=16, pady=(18, 6))

        hint = "\n".join(H(line) for line in (
            "כל תהליך עובד במחזוריות של 100 מילישניות —",
            "עסוק לפי אחוז היעד וישן בשאר הזמן.",
            "אפשר לשנות את העוצמה תוך כדי ריצה.",
        ))
        tk.Label(ctl, justify="right", font=self.f_small, bg=CARD, fg=MUTED,
                 text=hint).pack(padx=16, pady=(4, 14))

        # dashboard (left) ---------------------------------------------------
        dash = tk.Frame(body, bg=PAGE)
        dash.grid(row=0, column=0, sticky="nsew")
        dash.columnconfigure((0, 1, 2), weight=1, uniform="tiles")
        dash.rowconfigure(1, weight=1)

        self.tile_cpu = self._tile(dash, 2, H("שימוש כולל ב-CPU"))
        self.tile_temp = self._tile(dash, 1, H("טמפרטורת CPU"))
        self.tile_left = self._tile(dash, 0, H("זמן נותר"))

        chart_card = self._card(dash)
        chart_card.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        tk.Label(chart_card, text=H("שימוש ב-CPU — 60 השניות האחרונות"),
                 font=self.f_label, bg=CARD, fg=MUTED).pack(anchor="e", padx=14, pady=(10, 2))
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

    def _spin_row(self, parent, lo, hi, val, caption):
        row = tk.Frame(parent, bg=CARD)
        row.pack(anchor="e", padx=16)
        tk.Label(row, text=caption, font=self.f_small, bg=CARD, fg=MUTED).pack(side="left", padx=(0, 10))
        sp = self._spin(row, lo, hi, val)
        sp.pack(side="left")
        return sp

    def _tile(self, parent, col, caption):
        card = self._card(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 2 else 0, 0 if col == 0 else 12))
        tk.Label(card, text=caption, font=self.f_small, bg=CARD, fg=MUTED).pack(anchor="e", padx=14, pady=(10, 0))
        val = tk.Label(card, text="—", font=(self.f_big[0], 22, "bold"), bg=CARD, fg=INK)
        val.pack(anchor="e", padx=14, pady=(0, 10))
        return val

    # -- actions --------------------------------------------------------------
    def _on_target(self, v):
        self.target_lbl.config(text=f"{int(float(v))}%")
        self.engine.set_target(float(v))

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

    def _toggle(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self.engine.set_target(self.target.get())
            self.engine.start(self._workers_n(), self._duration_s())
        self._paint_state()

    def _paint_state(self):
        if self.engine.running:
            self.go.config(text="■  " + H("עצור עומס"), bg=RED, activebackground="#e66767")
            self.status.config(text="● " + H("עומס פעיל"), fg=GREEN)
        else:
            self.go.config(text="▶  " + H("התחל עומס"), bg=BLUE, activebackground="#5598e7")
            self.status.config(text="● " + H("מנוחה"), fg=MUTED)

    # -- refresh loop ----------------------------------------------------------
    def _tick(self):
        s = self.sampler
        now = time.time()
        if s.total is not None:
            self.history.append((now, s.total))
            self.history = [(t, v) for t, v in self.history if t >= now - WINDOW_S - 2]

        self.tile_cpu.config(text="—" if s.total is None else f"{s.total:.0f}%")
        if s.temp is None:
            self.tile_temp.config(text="—", fg=INK)
        else:
            color = RED if s.temp >= 85 else AMBER if s.temp >= 70 else INK
            self.tile_temp.config(text=f"{s.temp:.0f}°C", fg=color)

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
                          text=H("עכשיו") if sec == 0 else f"-{sec}s")
        c.create_line(pl, y(0), w - pr, y(0), fill=BASELINE)

        pts = [(t, v) for t, v in self.history if t >= t0]
        if len(pts) > 1:
            line = [coord for t, v in pts for coord in (x(t), y(v))]
            area = [x(pts[0][0]), y(0)] + line + [x(pts[-1][0]), y(0)]
            c.create_polygon(*area, fill=BLUE_DIM, outline="")
            c.create_line(*line, fill=BLUE, width=2, joinstyle="round")
            lx, ly = x(pts[-1][0]), y(pts[-1][1])
            c.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill=BLUE, outline=CARD, width=2)

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
            c.create_text(cx + col_w - gap, cy, text=H(f"ליבה {i}"), anchor="e",
                          fill=MUTED, font=self.f_small)
            bar_x1 = cx + pct_w + gap
            bar_x2 = cx + col_w - label_w - gap
            if bar_x2 - bar_x1 > 30:
                c.create_rectangle(bar_x1, cy - 4, bar_x2, cy + 4, fill=CARD2, outline="")
                fill_w = (bar_x2 - bar_x1) * min(100.0, v) / 100.0
                c.create_rectangle(bar_x2 - fill_w, cy - 4, bar_x2, cy + 4, fill=BLUE, outline="")
            c.create_text(cx + pct_w, cy, text=f"{v:.0f}%", anchor="e",
                          fill=INK2, font=self.f_small)


def main():
    global _TK_NEEDS_BIDI
    engine = LoadEngine()
    sampler = CpuSampler()
    root = tk.Tk()
    # macOS renders bidi text natively; X11/Windows Tk does not
    _TK_NEEDS_BIDI = root.tk.call("tk", "windowingsystem") != "aqua"
    App(root, engine, sampler)
    try:
        root.mainloop()
    finally:
        engine.stop()


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
