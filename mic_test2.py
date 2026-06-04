"""
mic_test2.py — CLAP / AST Acoustic Threat Monitor (dual-mic direction edition)
"""
import warnings, time, os, json
warnings.filterwarnings("ignore")

import threading, queue, math
import numpy as np
import tkinter as tk
import tkinter.ttk as ttk
import sounddevice as sd
import torch
from transformers import (ClapModel, ClapProcessor,
                           ASTForAudioClassification, ASTFeatureExtractor)

try:
    import scipy.signal as _ss
    def _resample(audio, from_sr, to_sr):
        if from_sr == to_sr:
            return audio
        n = int(len(audio) * to_sr / from_sr)
        return _ss.resample(audio, n).astype(np.float32)
except ImportError:
    def _resample(audio, from_sr, to_sr):
        if from_sr == to_sr:
            return audio
        if from_sr < to_sr:
            return audio.astype(np.float32)
        step = max(1, from_sr // to_sr)
        return audio[::step].astype(np.float32)

# ── config ─────────────────────────────────────────────────────────────────
SAMPLE_RATE = 48000
WINDOW_SEC  = 1.0
HOP_SEC     = 0.25
THRESHOLD   = 0.45
ENERGY_GATE = 0.003
CLAP_MARGIN = 0.15   # threat must beat the best neutral label by this much

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "mic_settings.json")
SPEED_SOUND = 343.0   # m/s at ~20 °C

THREAT_LABELS = [
    "M16 assault rifle firing",
    "AK-47 Kalashnikov firing",
    "Glock 9mm pistol gunshot",
    "shotgun blast",
    "sniper rifle shot",
    "automatic machine gun burst",
    "explosion or loud blast",
]
NEUTRAL_LABELS = ["background noise or silence", "people talking or music"]
ALL_LABELS     = THREAT_LABELS + NEUTRAL_LABELS

# ── available models ──────────────────────────────────────────────────────────
_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

MODELS = {
    "CLAP": {"hf_id": "laion/clap-htsat-unfused",                "local": os.path.join(_MODELS_DIR, "clap"), "sr": 48000, "size": "~600 MB"},
    "AST":  {"hf_id": "MIT/ast-finetuned-audioset-10-10-0.4593", "local": os.path.join(_MODELS_DIR, "ast"),  "sr": 16000, "size": "~87 MB"},
}
DEFAULT_MODEL = "CLAP"

def _model_path(name: str) -> str:
    """Return local folder if it exists, otherwise the HuggingFace model ID."""
    local = MODELS[name]["local"]
    if os.path.isdir(local) and os.path.exists(os.path.join(local, "config.json")):
        return local
    return MODELS[name]["hf_id"]

# AudioSet keyword matching used to identify threat classes in AST output
_AST_THREAT_KW = ("gun", "shot", "firearm", "weapon", "explosion", "blast",
                   "artillery", "machine gun", "burst", "bang", "bomb",
                   "grenade", "rifle", "pistol", "shotgun", "sniper")

# ── palette ─────────────────────────────────────────────────────────────────
C = dict(
    bg        = "#0b0c10",
    panel     = "#13151a",
    border    = "#1e2230",
    green     = "#00e676",
    green_dk  = "#004d26",
    green_act = "#006633",
    red       = "#ff1744",
    red_dk    = "#4a0010",
    orange    = "#ff6d00",
    blue      = "#2979ff",
    blue_dk   = "#0a1a4a",
    text      = "#ffffff",
    dim       = "#ffffff",
    mid       = "#e0e0e0",
)


def lerp_color(a: str, b: str, t: float) -> str:
    """Linearly interpolate between two hex colours; t=0 returns a, t=1 returns b."""
    ra = [int(a[i:i+2], 16) for i in (1, 3, 5)]
    rb = [int(b[i:i+2], 16) for i in (1, 3, 5)]
    r  = [int(ra[i] + (rb[i] - ra[i]) * t) for i in range(3)]
    return "#{:02x}{:02x}{:02x}".format(*r)


class App(tk.Tk):

    # ── animation constants ───────────────────────────────────────────────────
    _PULSE_PEAK     = 10
    _PULSE_TOTAL    = 28
    _PULSE_INTERVAL = 22

    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.geometry("820x900")
        self.configure(bg=C["border"])
        self.attributes("-topmost", True)

        self.model     = None
        self.processor = None
        self._cuda_available = torch.cuda.is_available()
        self.device    = "cuda" if self._cuda_available else "cpu"
        self.running   = False
        self.stream    = None
        self.stream2   = None
        self._q        = queue.Queue(maxsize=4)
        self._avg_rms  = 0.0
        self._bar_score = 0.0
        self._bar_label = ""
        self._last_decay_score    = -1.0
        self._alert_until         = 0.0
        self._alert_blink_running = False
        self._is_hidden           = False
        self._blink_state         = False
        self._drag_x = self._drag_y = 0

        self._win_samples  = int(SAMPLE_RATE * WINDOW_SEC)
        self._hop_samples  = int(SAMPLE_RATE * HOP_SEC)
        self._ring         = np.zeros(self._win_samples, dtype=np.float32)
        self._ring2        = np.zeros(self._win_samples, dtype=np.float32)
        self._hop_buf      = []
        self._hop_buf_len  = 0
        self._hop_buf2     = []
        self._hop_buf2_len = 0

        # model selection
        self._model_name     = DEFAULT_MODEL
        self._ast_threat_idx = []

        # persisted settings (mics, separation, threshold, direction)
        self._settings = self._load_settings()

        # dual-mic direction
        self._dir_mode    = bool(self._settings.get("dir_mode", False))
        self._calibrating = False
        self._mic_sep_m   = float(self._settings.get("sep_m", 0.5))
        self._max_delay   = self._mic_sep_m / SPEED_SOUND
        self._threshold   = float(self._settings.get("threshold", THRESHOLD))

        self._mic_devices  = self._get_mic_devices()
        self._mic1_idx     = self._match_device(self._settings.get("mic1_name"), 0)
        self._mic2_idx     = self._match_device(self._settings.get("mic2_name"),
                                                min(1, len(self._mic_devices) - 1))
        self._sel_dev_id   = self._mic_devices[self._mic1_idx][0]
        self._sel_dev2_id  = self._mic_devices[self._mic2_idx][0]

        self._build_ui()
        if self._dir_mode:
            self._apply_dir_mode_ui()       # restore saved DIRECTION MODE state
        self._decay_tick()
        self._blink_tick()
        threading.Thread(target=self._load_model, daemon=True).start()
        threading.Thread(target=self._worker,     daemon=True).start()

    # ── device list ──────────────────────────────────────────────────────────
    def _get_mic_devices(self):
        mics, loop = [], []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] < 1:
                continue
            n, low = d["name"], d["name"].lower()
            if any(x in low for x in ("stereo mix", "what u hear", "loopback", "wave out")):
                loop.append((i, f"  {n}  [loopback]"))
            else:
                mics.append((i, f"  {n}"))
        return (mics + loop) or [(-1, "  Default")]

    # ── settings persistence ───────────────────────────────────────────────────
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self):
        data = {
            "mic1_name":  self._mic_devices[self._mic_combo.current()][1].strip()
                          if hasattr(self, "_mic_combo") else None,
            "mic2_name":  self._mic_devices[self._mic2_combo.current()][1].strip()
                          if hasattr(self, "_mic2_combo") else None,
            "sep_m":      self._mic_sep_m,
            "threshold":  self._threshold,
            "dir_mode":   self._dir_mode,
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._write_log(f"Settings save error: {e}", "alert")

    def _match_device(self, name, default_idx):
        """Find a device index by saved name; fall back to default_idx."""
        if name:
            for i, d in enumerate(self._mic_devices):
                if d[1].strip() == name:
                    return i
        return max(0, min(default_idx, len(self._mic_devices) - 1))

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = tk.Frame(self, bg=C["bg"], padx=1, pady=1)
        root.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # title bar
        tb = tk.Frame(root, bg=C["panel"], height=38)
        tb.pack(fill=tk.X)
        tb.pack_propagate(False)

        self._dot = tk.Label(tb, text="◉", font=("Segoe UI", 12),
                             fg=C["dim"], bg=C["panel"])
        self._dot.pack(side=tk.LEFT, padx=(12, 6), pady=6)

        tk.Label(tb, text="ACOUSTIC THREAT MONITOR",
                 font=("Consolas", 11, "bold"),
                 fg=C["mid"], bg=C["panel"]).pack(side=tk.LEFT, pady=8)

        tk.Button(tb, text="✕", font=("Segoe UI", 11),
                  bg=C["panel"], fg=C["mid"],
                  activebackground=C["red"], activeforeground="#fff",
                  bd=0, relief=tk.FLAT, width=3,
                  command=self.destroy).pack(side=tk.RIGHT, pady=4, padx=4)

        tk.Button(tb, text="–", font=("Segoe UI", 11),
                  bg=C["panel"], fg=C["mid"],
                  activebackground=C["border"], activeforeground="#fff",
                  bd=0, relief=tk.FLAT, width=3,
                  command=self._hide).pack(side=tk.RIGHT, pady=4)

        tb.bind("<Button-1>",    lambda e: self._drag_start(e))
        tb.bind("<B1-Motion>",   lambda e: self._drag_move(e))
        self._dot.bind("<Button-1>",  lambda e: self._drag_start(e))
        self._dot.bind("<B1-Motion>", lambda e: self._drag_move(e))

        # status panel
        self._status_panel = tk.Frame(root, bg=C["panel"],
                                      highlightbackground=C["border"],
                                      highlightthickness=1)
        self._status_panel.pack(fill=tk.X, padx=16, pady=(12, 6))

        self._status_var = tk.StringVar(value="LOADING MODEL…")
        self._status_lbl = tk.Label(self._status_panel,
                                    textvariable=self._status_var,
                                    font=("Consolas", 22, "bold"),
                                    fg=C["dim"], bg=C["panel"],
                                    anchor="center", pady=18)
        self._status_lbl.pack(fill=tk.X)

        self._sub_var = tk.StringVar(value="")
        tk.Label(self._status_panel, textvariable=self._sub_var,
                 font=("Consolas", 9), fg=C["mid"], bg=C["panel"],
                 anchor="center", pady=4).pack(fill=tk.X)

        self._status_children = list(self._status_panel.winfo_children())

        # confidence / gauge row
        gauge_row = tk.Frame(root, bg=C["bg"])
        gauge_row.pack(fill=tk.X, padx=16, pady=(4, 6))

        self._gauge_canvas = tk.Canvas(gauge_row, width=100, height=100,
                                        bg=C["bg"], bd=0, highlightthickness=0)
        self._gauge_canvas.pack(side=tk.LEFT, padx=(0, 14))
        self._gauge_arc = self._gauge_canvas.create_arc(
            10, 10, 90, 90, start=220, extent=0,
            style=tk.ARC, outline=C["green"], width=8)
        self._gauge_bg  = self._gauge_canvas.create_arc(
            10, 10, 90, 90, start=220, extent=-260,
            style=tk.ARC, outline=C["border"], width=8)
        self._gauge_pct = self._gauge_canvas.create_text(
            50, 50, text="0%", fill=C["dim"], font=("Consolas", 14, "bold"))
        self._gauge_tag = self._gauge_canvas.create_text(
            50, 75, text="CLEAR", fill=C["dim"], font=("Consolas", 7))

        bar_col = tk.Frame(gauge_row, bg=C["bg"])
        bar_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(bar_col, text="CONFIDENCE", font=("Consolas", 7),
                 fg=C["dim"], bg=C["bg"], anchor="w").pack(fill=tk.X)

        self._seg_canvas = tk.Canvas(bar_col, height=28,
                                      bg=C["bg"], bd=0, highlightthickness=0)
        self._seg_canvas.pack(fill=tk.X, pady=(2, 4))
        self._seg_canvas.bind("<Configure>", lambda e: self._redraw_segs())
        self._segs = []

        self._bar_label_var = tk.StringVar(value="")
        tk.Label(bar_col, textvariable=self._bar_label_var,
                 font=("Consolas", 9), fg=C["mid"], bg=C["bg"],
                 anchor="w").pack(fill=tk.X)

        # waveform
        wave_frame = tk.Frame(root, bg=C["border"])
        wave_frame.pack(fill=tk.X, padx=16, pady=(0, 6))
        tk.Label(wave_frame, text=" AUDIO INPUT ",
                 font=("Consolas", 7), fg=C["dim"],
                 bg=C["border"]).pack(anchor="nw")
        self._wave_canvas = tk.Canvas(wave_frame, height=64,
                                       bg=C["panel"], bd=0, highlightthickness=0)
        self._wave_canvas.pack(fill=tk.X)
        self._wave_mid  = self._wave_canvas.create_line(
            0, 32, 820, 32, fill=C["border"], width=1)
        self._wave_line = self._wave_canvas.create_line(
            0, 32, 820, 32, fill=C["green"], width=1.5, smooth=True)

        # detection log
        log_frame = tk.Frame(root, bg=C["bg"])
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 6))
        tk.Label(log_frame, text=" DETECTION LOG ",
                 font=("Consolas", 7), fg=C["dim"], bg=C["bg"],
                 anchor="w").pack(fill=tk.X)
        self._log = tk.Text(log_frame, height=6,
                            bg=C["panel"], fg=C["green"],
                            font=("Consolas", 9), bd=0,
                            highlightbackground=C["border"],
                            highlightthickness=1,
                            insertbackground=C["green"],
                            state=tk.DISABLED)
        self._log.tag_config("alert", foreground=C["red"])
        self._log.tag_config("info",  foreground=C["mid"])
        self._log.pack(fill=tk.BOTH, expand=True)

        # debug line
        self._debug_var = tk.StringVar()
        tk.Label(root, textvariable=self._debug_var,
                 font=("Consolas", 8), fg=C["dim"], bg=C["bg"],
                 anchor="w").pack(fill=tk.X, padx=16)

        # MIC 1 selector
        mic_row = tk.Frame(root, bg=C["bg"])
        mic_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Label(mic_row, text="MIC 1", font=("Consolas", 8),
                 fg=C["green"], bg=C["bg"], width=6).pack(side=tk.LEFT)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("T.TCombobox",
                         fieldbackground=C["panel"], background=C["panel"],
                         foreground=C["text"], selectbackground=C["panel"],
                         selectforeground=C["green"], arrowcolor=C["mid"])
        style.map("T.TCombobox",
                  fieldbackground=[("readonly", C["panel"])],
                  foreground=[("readonly", C["text"])])

        mic_names = [d[1] for d in self._mic_devices]
        self._mic_combo = ttk.Combobox(mic_row, values=mic_names,
                                        state="readonly", style="T.TCombobox",
                                        font=("Consolas", 9))
        self._mic_combo.current(self._mic1_idx)
        self._mic_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        self._mic_combo.bind("<<ComboboxSelected>>", self._on_mic_change)

        # direction mode panel (hidden until DIR ON)
        self._dir_frame = tk.Frame(root, bg=C["bg"])

        dir_top = tk.Frame(self._dir_frame, bg=C["bg"])
        dir_top.pack(fill=tk.X, pady=(2, 0))
        tk.Label(dir_top, text="MIC 2", font=("Consolas", 8),
                 fg=C["dim"], bg=C["bg"], width=6).pack(side=tk.LEFT)
        mic2_names = [d[1] for d in self._mic_devices]
        self._mic2_combo = ttk.Combobox(dir_top, values=mic2_names,
                                         state="readonly", style="T.TCombobox",
                                         font=("Consolas", 9))
        self._mic2_combo.current(self._mic2_idx)
        self._mic2_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        self._mic2_combo.bind("<<ComboboxSelected>>", self._on_mic2_change)

        sep_row = tk.Frame(self._dir_frame, bg=C["bg"])
        sep_row.pack(fill=tk.X, pady=(2, 4))
        tk.Label(sep_row, text="SEP(m)", font=("Consolas", 8),
                 fg=C["dim"], bg=C["bg"], width=6).pack(side=tk.LEFT)
        self._sep_var = tk.StringVar(value=f"{self._mic_sep_m:g}")
        sep_entry = tk.Entry(sep_row, textvariable=self._sep_var, width=6,
                             bg=C["panel"], fg=C["green"], insertbackground=C["green"],
                             font=("Consolas", 9), bd=0,
                             highlightbackground=C["border"], highlightthickness=1)
        sep_entry.pack(side=tk.LEFT, padx=(0, 16), ipady=3)
        sep_entry.bind("<Return>",   self._on_sep_change)
        sep_entry.bind("<FocusOut>", self._on_sep_change)

        self._cal_btn = tk.Button(sep_row, text="🎯 CALIBRATE",
                                   font=("Consolas", 8, "bold"),
                                   bg=C["border"], fg=C["orange"],
                                   activebackground="#3a2000",
                                   bd=0, relief=tk.FLAT, padx=8,
                                   command=self._start_calibration)
        self._cal_btn.pack(side=tk.LEFT, ipady=2)
        self._cal_status_var = tk.StringVar(value="")
        tk.Label(sep_row, textvariable=self._cal_status_var,
                 font=("Consolas", 8), fg=C["orange"],
                 bg=C["bg"]).pack(side=tk.LEFT, padx=8)

        # compass canvas
        self._compass_canvas = tk.Canvas(self._dir_frame, width=780, height=100,
                                          bg=C["panel"], bd=0,
                                          highlightbackground=C["border"],
                                          highlightthickness=1)
        self._compass_canvas.pack(fill=tk.X, pady=(0, 4))
        self._compass_canvas.bind("<Configure>",
            lambda e: (self._build_compass_static(), self._draw_compass(None)))
        self.after(100, self._build_compass_static)

        # MODEL selector
        model_row = tk.Frame(root, bg=C["bg"])
        model_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Label(model_row, text="MODEL", font=("Consolas", 8),
                 fg=C["dim"], bg=C["bg"], width=6).pack(side=tk.LEFT)

        _model_colors = {
            "CLAP": (C["green"],  C["green_dk"]),
            "AST":  (C["orange"], "#3a1a00"),
        }
        self._model_btns = {}
        for name in MODELS:
            col, col_dk = _model_colors[name]
            active = (name == self._model_name)
            lbl = f"{name}  {MODELS[name]['size']}"
            btn = tk.Button(model_row, text=lbl,
                            font=("Consolas", 9, "bold"),
                            bg=col_dk if active else C["panel"],
                            fg=col    if active else C["mid"],
                            activebackground=col_dk,
                            bd=0, relief=tk.FLAT,
                            command=lambda n=name: self._set_model(n))
            btn.pack(side=tk.LEFT, ipady=3, padx=(0, 4))
            self._model_btns[name] = btn

        self._model_status = tk.Label(model_row, text="", font=("Consolas", 8),
                                       fg=C["mid"], bg=C["bg"])
        self._model_status.pack(side=tk.LEFT, padx=8)

        # DEVICE selector
        dev_row = tk.Frame(root, bg=C["bg"])
        dev_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Label(dev_row, text="DEVICE", font=("Consolas", 8),
                 fg=C["dim"], bg=C["bg"], width=6).pack(side=tk.LEFT)

        gpu_label = "GPU  [CUDA]" if self._cuda_available else "GPU  [N/A]"
        self._cpu_btn = tk.Button(dev_row, text="CPU",
                                   font=("Consolas", 9, "bold"),
                                   bg=C["green_dk"] if self.device == "cpu" else C["panel"],
                                   fg=C["green"]    if self.device == "cpu" else C["mid"],
                                   activebackground=C["green_dk"],
                                   bd=0, relief=tk.FLAT, width=7,
                                   command=lambda: self._set_device("cpu"))
        self._cpu_btn.pack(side=tk.LEFT, ipady=3, padx=(0, 2))
        self._gpu_btn = tk.Button(dev_row, text=gpu_label,
                                   font=("Consolas", 9, "bold"),
                                   bg=C["blue_dk"] if self.device == "cuda" else C["panel"],
                                   fg=C["blue"]    if self.device == "cuda" else C["mid"],
                                   activebackground=C["blue_dk"],
                                   bd=0, relief=tk.FLAT, width=12,
                                   state=tk.NORMAL if self._cuda_available else tk.DISABLED,
                                   command=lambda: self._set_device("cuda"))
        self._gpu_btn.pack(side=tk.LEFT, ipady=3)
        self._dev_status = tk.Label(dev_row, text="", font=("Consolas", 8),
                                     fg=C["mid"], bg=C["bg"])
        self._dev_status.pack(side=tk.LEFT, padx=8)

        # SENSITIVITY (detection threshold) slider
        sens_row = tk.Frame(root, bg=C["bg"])
        sens_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Label(sens_row, text="SENS", font=("Consolas", 8),
                 fg=C["dim"], bg=C["bg"], width=6).pack(side=tk.LEFT)
        self._sens_scale = tk.Scale(
            sens_row, from_=0.10, to=0.90, resolution=0.05,
            orient=tk.HORIZONTAL, showvalue=False,
            bg=C["bg"], fg=C["green"], troughcolor=C["panel"],
            highlightthickness=0, bd=0, sliderrelief=tk.FLAT,
            activebackground=C["green"], command=self._on_sens_change)
        self._sens_scale.set(self._threshold)
        self._sens_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._sens_var = tk.StringVar(value=f"{self._threshold:.0%}")
        tk.Label(sens_row, textvariable=self._sens_var, width=6,
                 font=("Consolas", 9, "bold"), fg=C["green"],
                 bg=C["bg"]).pack(side=tk.LEFT, padx=(8, 0))

        # direction mode toggle
        self._dir_btn = tk.Button(root, text="◈  DIRECTION MODE: OFF",
                                  font=("Consolas", 10, "bold"),
                                  bg=C["panel"], fg=C["dim"],
                                  activebackground=C["border"],
                                  bd=0, relief=tk.FLAT,
                                  command=self._toggle_dir)
        self._dir_btn.pack(fill=tk.X, padx=16, pady=(4, 2), ipady=5)

        # start / stop
        self._btn = tk.Button(root, text="▶   START MONITORING",
                              font=("Consolas", 12, "bold"),
                              bg=C["green_dk"], fg=C["green"],
                              activebackground=C["green_act"],
                              activeforeground=C["green"],
                              bd=0, relief=tk.FLAT,
                              command=self._toggle)
        self._btn.pack(fill=tk.X, padx=16, pady=(2, 12), ipady=11)

    # ── direction helpers ─────────────────────────────────────────────────────
    def _start_calibration(self):
        if not self.running or not self._dir_mode:
            self._cal_status_var.set("⚠ Start microphone first")
            return
        if self.stream2 is None:
            self._cal_status_var.set("⚠ MIC 2 not active")
            return
        if self._calibrating:
            return

        self._cal_status_var.set("🎯 Tap near MIC 1...")
        self._calibrating = True

        def _wait_for_tap():
            for _ in range(40):
                time.sleep(0.05)
                rms1 = float(np.sqrt(np.mean(self._ring[-self._hop_samples:] ** 2)))
                rms2 = float(np.sqrt(np.mean(self._ring2[-self._hop_samples:] ** 2)))
                if rms1 > 0.02 or rms2 > 0.02:
                    angle = self._calc_and_show_direction()
                    if angle is None:
                        self.after(0, lambda: self._cal_status_var.set("❌ Try again"))
                    elif angle < -10:
                        msg = f"✅ MIC 1 = LEFT  ({angle:+.0f}°)  — correct position"
                        self.after(0, lambda m=msg: self._cal_status_var.set(m))
                    elif angle > 10:
                        msg = f"↔ MIC 1 = RIGHT  ({angle:+.0f}°)  — swap MIC1↔MIC2"
                        self.after(0, lambda m=msg: self._cal_status_var.set(m))
                    else:
                        msg = f"⚠ CENTER ({angle:+.0f}°) — tap closer to MIC 1"
                        self.after(0, lambda m=msg: self._cal_status_var.set(m))
                    self._calibrating = False
                    return
            self.after(0, lambda: self._cal_status_var.set("⏱ No tap detected — try again"))
            self._calibrating = False

        threading.Thread(target=_wait_for_tap, daemon=True).start()

    def _toggle_dir(self):
        self._dir_mode = not self._dir_mode
        self._apply_dir_mode_ui()
        self._save_settings()
        if self.running:
            self._restart()

    def _apply_dir_mode_ui(self):
        if self._dir_mode:
            self._dir_frame.pack(fill=tk.X, padx=16, before=self._dir_btn)
            self._dir_btn.configure(text="◈  DIRECTION MODE: ON",
                                    fg=C["green"], bg=C["green_dk"])
        else:
            self._dir_frame.pack_forget()
            self._dir_btn.configure(text="◈  DIRECTION MODE: OFF",
                                    fg=C["dim"], bg=C["panel"])
            self._draw_compass(None)

    def _on_sens_change(self, _=None):
        self._threshold = float(self._sens_scale.get())
        self._sens_var.set(f"{self._threshold:.0%}")
        self._save_settings()

    def _on_mic2_change(self, _=None):
        idx = self._mic2_combo.current()
        self._sel_dev2_id = self._mic_devices[idx][0]
        self._save_settings()
        if self.running and self._dir_mode:
            self._restart()

    def _on_sep_change(self, _=None):
        try:
            v = float(self._sep_var.get())
            self._mic_sep_m = max(0.01, min(v, 10.0))
            self._max_delay = self._mic_sep_m / SPEED_SOUND
            self._save_settings()
        except ValueError:
            pass

    def _compass_geometry(self):
        """Return (cx, cy, R, cw) for the compass canvas."""
        try:
            cw = self._compass_canvas.winfo_width()
            if cw < 50:
                cw = 780
        except Exception:
            cw = 780
        ch = 100
        cx, cy = cw // 2, ch - 10
        R = 65
        return cx, cy, R, cw

    def _build_compass_static(self):
        """Draw the fixed arc, tick marks and MIC labels of the compass."""
        self._compass_canvas.delete("static")
        cx, cy, R, _ = self._compass_geometry()

        self._compass_canvas.create_arc(
            cx - R, cy - R, cx + R, cy + R,
            start=0, extent=180, style=tk.ARC,
            outline=C["border"], width=3, tags="static")

        for deg in (-90, -60, -30, 0, 30, 60, 90):
            rad = math.radians(90 - deg)
            tx  = cx + (R + 12) * math.cos(rad)
            ty  = cy - (R + 12) * math.sin(rad)
            self._compass_canvas.create_text(
                tx, ty, text=f"{abs(deg)}°",
                font=("Consolas", 7), fill=C["mid"], tags="static")
            ix = cx + (R - 4) * math.cos(rad)
            iy = cy - (R - 4) * math.sin(rad)
            ox = cx + (R + 4) * math.cos(rad)
            oy = cy - (R + 4) * math.sin(rad)
            self._compass_canvas.create_line(
                ix, iy, ox, oy, fill=C["mid"], width=1, tags="static")

        self._compass_canvas.create_text(
            cx - R - 32, cy - 10, text="MIC 1",
            font=("Consolas", 8, "bold"), fill=C["green"], tags="static")
        self._compass_canvas.create_text(
            cx - R - 32, cy + 6, text="← LEFT",
            font=("Consolas", 7), fill=C["mid"], tags="static")
        self._compass_canvas.create_text(
            cx + R + 32, cy - 10, text="MIC 2",
            font=("Consolas", 8, "bold"), fill=C["blue"], tags="static")
        self._compass_canvas.create_text(
            cx + R + 32, cy + 6, text="RIGHT →",
            font=("Consolas", 7), fill=C["mid"], tags="static")

        self._compass_canvas.create_line(
            cx, cy, cx, cy - R + 6,
            fill=C["dim"], width=3, arrow=tk.LAST,
            arrowshape=(10, 12, 4), tags="needle")
        self._compass_canvas.create_text(
            cx, cy - R - 16, text="—",
            font=("Consolas", 11, "bold"), fill=C["dim"], tags="needle")

    def _draw_compass(self, angle_deg):
        """Update the compass needle to angle_deg (−90…+90) or None to reset."""
        cx, cy, R, _ = self._compass_geometry()
        self._compass_canvas.delete("needle")

        if angle_deg is None:
            self._compass_canvas.create_line(
                cx, cy, cx, cy - R + 6,
                fill=C["dim"], width=3, arrow=tk.LAST,
                arrowshape=(10, 12, 4), tags="needle")
            self._compass_canvas.create_text(
                cx, cy - R - 14, text="—",
                font=("Consolas", 11, "bold"), fill=C["dim"], tags="needle")
            return

        clipped = max(-90, min(90, angle_deg))
        rad  = math.radians(90 - clipped)
        nx   = cx + (R - 6) * math.cos(rad)
        ny   = cy - (R - 6) * math.sin(rad)
        col  = C["red"] if abs(clipped) > 10 else C["green"]
        side = "LEFT" if clipped < -5 else ("RIGHT" if clipped > 5 else "CENTER")

        self._compass_canvas.create_line(
            cx, cy, nx, ny,
            fill=col, width=3, arrow=tk.LAST,
            arrowshape=(10, 12, 4), tags="needle")
        self._compass_canvas.create_text(
            cx, cy - R - 14,
            text=f"{clipped:+.0f}°  {side}",
            font=("Consolas", 11, "bold"), fill=col, tags="needle")

    @staticmethod
    def _gcc_phat(sig1, sig2, fs, max_delay_s):
        """GCC-PHAT — returns time delay in seconds (positive = sig1 leads)."""
        raw_n  = len(sig1) + len(sig2) - 1
        n      = 1 << (raw_n - 1).bit_length()
        F1     = np.fft.rfft(sig1, n=n)
        F2     = np.fft.rfft(sig2, n=n)
        R      = F1 * np.conj(F2)
        denom  = np.abs(R)
        R      = np.where(denom > 1e-10, R / denom, 0)
        cc     = np.fft.irfft(R, n=n)
        max_k  = int(max_delay_s * fs) + 1
        cc     = np.concatenate([cc[-max_k:], cc[:max_k + 1]])
        delay  = (np.argmax(np.abs(cc)) - max_k) / fs
        return float(delay)

    def _calc_and_show_direction(self):
        """Compute arrival direction from both ring buffers and update compass."""
        try:
            tau   = self._gcc_phat(self._ring, self._ring2,
                                   SAMPLE_RATE, self._max_delay)
            ratio = max(-1.0, min(1.0, tau * SPEED_SOUND / self._mic_sep_m))
            angle = math.degrees(math.asin(ratio))
            self.after(0, self._draw_compass, angle)
            return angle
        except Exception:
            return None

    # ── visual helpers ────────────────────────────────────────────────────────
    def _redraw_segs(self):
        cw  = self._seg_canvas.winfo_width()
        ch  = 28
        n   = 20
        gap = 3
        sw  = (cw - gap * (n - 1)) / n
        self._seg_canvas.delete("all")
        self._segs = []
        for i in range(n):
            x0 = i * (sw + gap)
            r  = self._seg_canvas.create_rectangle(
                x0, 0, x0 + sw, ch,
                fill=C["border"], outline="", tags="seg")
            self._segs.append(r)
        self._update_segs(self._bar_score)

    def _update_segs(self, score):
        n   = len(self._segs)
        lit = int(round(score * n))
        for i, r in enumerate(self._segs):
            if i < lit:
                t     = i / max(n - 1, 1)
                color = lerp_color(C["green"], C["red"], t)
                self._seg_canvas.itemconfig(r, fill=color)
            else:
                self._seg_canvas.itemconfig(r, fill=C["border"])

    def _update_gauge(self, score, level_text, color):
        extent = int(-260 * score)
        self._gauge_canvas.itemconfig(self._gauge_arc,  extent=extent, outline=color)
        self._gauge_canvas.itemconfig(self._gauge_pct,  text=f"{score:.0%}", fill=color)
        self._gauge_canvas.itemconfig(self._gauge_tag,  text=level_text, fill=color)

    def _decay_tick(self):
        if self._bar_score > 0.008:
            self._bar_score *= 0.84
        else:
            self._bar_score = 0.0
            self._bar_label = ""

        s = self._bar_score
        if   s >= 0.60: lvl, col = "CRITICAL", C["red"]
        elif s >= 0.40: lvl, col = "HIGH",     C["orange"]
        elif s >= 0.20: lvl, col = "LOW",      C["green"]
        else:           lvl, col = "CLEAR",    C["dim"]

        if s != self._last_decay_score:
            self._update_segs(s)
            self._update_gauge(s, lvl, col)
            self._last_decay_score = s

        self._bar_label_var.set(self._bar_label[:36])
        self.after(80, self._decay_tick)

    def _blink_tick(self):
        if self.running:
            self._blink_state = not self._blink_state
            self._dot.configure(fg=C["green"] if self._blink_state else C["green_dk"])
        else:
            self._dot.configure(fg=C["dim"])
        self.after(600, self._blink_tick)

    def _draw_wave(self, data):
        cw  = self._wave_canvas.winfo_width()
        ch  = 64
        pts = []
        for i, v in enumerate(data):
            pts.extend([i * cw / max(len(data), 1), ch / 2 - v * 180])
        if len(pts) >= 4:
            col = C["red"] if time.time() < self._alert_until else C["green"]
            self._wave_canvas.coords(self._wave_line, *pts)
            self._wave_canvas.itemconfig(self._wave_line, fill=col)

    # ── drag / hide ──────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x, e.y

    def _drag_move(self, e):
        self.geometry(f"+{self.winfo_x()+e.x-self._drag_x}"
                      f"+{self.winfo_y()+e.y-self._drag_y}")

    def _hide(self):
        self.withdraw()
        self._is_hidden = True

    def _show(self):
        self.deiconify()
        self._is_hidden = False
        self.attributes("-topmost", True)

    # ── model loading ─────────────────────────────────────────────────────────
    def _load_model(self, name=None):
        if name is None:
            name = self._model_name
        cfg  = MODELS[name]
        path = _model_path(name)
        src  = "local" if path != cfg["hf_id"] else "HuggingFace"
        try:
            self._write_log(f"Loading {name}  ({cfg['size']})  [{src}]…", "info")

            if name == "CLAP":
                self.processor = ClapProcessor.from_pretrained(path)
                self.model     = ClapModel.from_pretrained(path).to(self.device)
                self.model.eval()
                dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)
                with torch.no_grad():
                    i = self.processor(text=ALL_LABELS, audio=dummy,
                                       return_tensors="pt", padding=True,
                                       sampling_rate=SAMPLE_RATE)
                    self.model(**{k: v.to(self.device) for k, v in i.items()})

            elif name == "AST":
                self.processor = ASTFeatureExtractor.from_pretrained(path)
                self.model     = ASTForAudioClassification.from_pretrained(path).to(self.device)
                self.model.eval()
                self._ast_threat_idx = [
                    int(i) for i, lbl in self.model.config.id2label.items()
                    if any(kw in lbl.lower() for kw in _AST_THREAT_KW)
                ]
                self._write_log(
                    f"AST: {len(self._ast_threat_idx)} threat classes matched", "info")
                dummy = np.zeros(cfg["sr"], dtype=np.float32)
                with torch.no_grad():
                    i = self.processor(dummy, sampling_rate=cfg["sr"],
                                       return_tensors="pt")
                    self.model(**{k: v.to(self.device) for k, v in i.items()})

            self._model_name = name
            self._write_log(f"{name} ready  [{self.device.upper()}]", "info")
            self.after(0, self._start)   # auto-start after model is ready

        except Exception as e:
            self._write_log(f"Load error ({name}): {e}", "alert")

    # ── model switching ───────────────────────────────────────────────────────
    def _set_model(self, new_name):
        if new_name == self._model_name and self.model is not None:
            return

        was_running = self.running
        if was_running:
            self._stop()

        self.model     = None
        self.processor = None
        for btn in self._model_btns.values():
            btn.configure(state=tk.DISABLED)
        self._model_status.configure(text="loading…")
        self._status_var.set("LOADING MODEL…")
        self._status_lbl.configure(fg=C["dim"])

        def _do():
            self._load_model(new_name)
            def _done():
                self._model_status.configure(text="")
                _col = {"CLAP": (C["green"], C["green_dk"]),
                        "AST":  (C["orange"], "#3a1a00")}
                for n, btn in self._model_btns.items():
                    col, col_dk = _col[n]
                    active = (n == self._model_name)
                    btn.configure(state=tk.NORMAL,
                                  bg=col_dk if active else C["panel"],
                                  fg=col    if active else C["mid"])
            self.after(0, _done)

        threading.Thread(target=_do, daemon=True).start()

    # ── inference ─────────────────────────────────────────────────────────────
    def _worker(self):
        while True:
            item = self._q.get()
            if item is None:
                break
            if self.model is None or not self.running:
                continue
            audio, _rms = item
            try:
                label, score, dbg = self._run_inference(audio)
                self.after(0, self._debug_var.set, dbg)
                if label and score >= self._threshold:
                    self._bar_score = score
                    self._bar_label = label
                    self.after(0, self._fire_alert, label, score)
                elif label:
                    self._bar_score = score
                    self._bar_label = label
                else:
                    self._bar_score = 0.0
                    self._bar_label = ""
            except Exception as exc:
                self.after(0, self._write_log, f"Inference error: {exc}", "alert")

    def _run_inference(self, audio: np.ndarray):
        """Dispatch to active backend. Returns (label, score, dbg_str)."""
        if self._model_name == "CLAP":
            return self._infer_clap(audio)
        if self._model_name == "AST":
            return self._infer_ast(audio)
        return "", 0.0, ""

    def _infer_clap(self, audio: np.ndarray):
        with torch.no_grad():
            inp  = self.processor(text=ALL_LABELS, audio=audio,
                                   return_tensors="pt", padding=True,
                                   sampling_rate=SAMPLE_RATE)
            prob = self.model(**{k: v.to(self.device) for k, v in inp.items()}) \
                       .logits_per_audio.softmax(dim=-1)[0].cpu().numpy()

        n        = len(THREAT_LABELS)
        top3_idx = np.argsort(prob)[-3:][::-1]
        dbg      = "  ·  ".join(
            f"{ALL_LABELS[i][:20]}: {prob[i]:.0%}" for i in top3_idx)
        best = int(np.argmax(prob))
        if best < n:
            # Reject unless the threat clearly beats the best neutral label.
            best_neutral = float(np.max(prob[n:]))
            if float(prob[best]) - best_neutral < CLAP_MARGIN:
                return "", 0.0, dbg
            return THREAT_LABELS[best], float(prob[best]), dbg
        return "", 0.0, dbg

    def _infer_ast(self, audio: np.ndarray):
        ast_sr  = MODELS["AST"]["sr"]
        audio16 = _resample(audio, SAMPLE_RATE, ast_sr)
        with torch.no_grad():
            inp  = self.processor(audio16, sampling_rate=ast_sr, return_tensors="pt")
            prob = self.model(**{k: v.to(self.device) for k, v in inp.items()}) \
                       .logits.softmax(dim=-1)[0].cpu().numpy()

        id2lbl = self.model.config.id2label
        top3   = np.argsort(prob)[-3:][::-1]
        dbg    = "  ·  ".join(f"{id2lbl[int(i)][:22]}: {prob[i]:.0%}" for i in top3)

        if not self._ast_threat_idx:
            return "", 0.0, dbg
        t_pairs = sorted(((prob[i], id2lbl[i]) for i in self._ast_threat_idx),
                         reverse=True)
        best_score, best_label = t_pairs[0]
        return best_label, float(best_score), dbg

    # ── mic ───────────────────────────────────────────────────────────────────
    def _toggle(self):
        if self.model is None:
            return
        if self.running:
            self._confirm_stop()
        else:
            self._start()

    def _confirm_stop(self):
        """Dark-themed confirmation dialog shown before stopping the stream."""
        dlg = tk.Toplevel(self)
        dlg.overrideredirect(True)
        dlg.configure(bg=C["border"])
        dlg.attributes("-topmost", True)

        self.update_idletasks()
        dw, dh = 340, 140
        cx = self.winfo_x() + (self.winfo_width()  - dw) // 2
        cy = self.winfo_y() + (self.winfo_height() - dh) // 2
        dlg.geometry(f"{dw}x{dh}+{cx}+{cy}")

        inner = tk.Frame(dlg, bg=C["panel"])
        inner.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        tk.Label(inner, text="STOP MONITORING?",
                 font=("Consolas", 13, "bold"),
                 fg=C["red"], bg=C["panel"], pady=18).pack()

        btn_row = tk.Frame(inner, bg=C["panel"])
        btn_row.pack(pady=(0, 16))

        def _yes():
            dlg.destroy()
            self._stop()

        tk.Button(btn_row, text="■  STOP",
                  font=("Consolas", 10, "bold"),
                  bg=C["red_dk"], fg=C["red"],
                  activebackground="#6a0020", activeforeground=C["red"],
                  bd=0, relief=tk.FLAT, width=10,
                  command=_yes).pack(side=tk.LEFT, padx=(0, 10), ipady=6)

        tk.Button(btn_row, text="CANCEL",
                  font=("Consolas", 10, "bold"),
                  bg=C["border"], fg=C["mid"],
                  activebackground=C["panel"], activeforeground=C["mid"],
                  bd=0, relief=tk.FLAT, width=10,
                  command=dlg.destroy).pack(side=tk.LEFT, ipady=6)

        dlg.grab_set()
        dlg.focus_set()

    def _on_mic_change(self, _=None):
        idx = self._mic_combo.current()
        self._sel_dev_id = self._mic_devices[idx][0]
        self._save_settings()
        if self.running:
            self._restart()

    def _restart(self, delay_ms=200):
        """Stop the current stream and restart after a short debounce."""
        self._stop()
        self.after(delay_ms, self._start)

    def _set_device(self, new_device):
        if new_device == self.device:
            return
        if new_device == "cuda" and not self._cuda_available:
            return
        if self.model is None:
            self._write_log("Model not loaded yet.", "info")
            return

        was_running = self.running
        if was_running:
            self._stop()

        self._dev_status.configure(text="moving…")
        self._cpu_btn.configure(state=tk.DISABLED)
        self._gpu_btn.configure(state=tk.DISABLED)

        def _move():
            try:
                self.model  = self.model.to(new_device)
                self.device = new_device
                self._write_log(f"Device → {new_device.upper()}", "info")
                def _done():
                    self._dev_status.configure(text="")
                    self._cpu_btn.configure(
                        state=tk.NORMAL,
                        bg=C["green_dk"] if self.device == "cpu"  else C["panel"],
                        fg=C["green"]    if self.device == "cpu"  else C["mid"])
                    self._gpu_btn.configure(
                        state=tk.NORMAL if self._cuda_available else tk.DISABLED,
                        bg=C["blue_dk"] if self.device == "cuda" else C["panel"],
                        fg=C["blue"]    if self.device == "cuda" else C["mid"])
                    if was_running:
                        self._start()
                self.after(0, _done)
            except Exception as e:
                self._write_log(f"Device switch error: {e}", "alert")
                def _err():
                    self._dev_status.configure(text="error")
                    self._cpu_btn.configure(state=tk.NORMAL)
                    if self._cuda_available:
                        self._gpu_btn.configure(state=tk.NORMAL)
                self.after(0, _err)

        threading.Thread(target=_move, daemon=True).start()

    def _start(self):
        self.running       = True
        self._ring[:]      = 0
        self._ring2[:]     = 0
        self._hop_buf      = []
        self._hop_buf_len  = 0
        self._hop_buf2     = []
        self._hop_buf2_len = 0
        self._btn.configure(text="■   STOP MONITORING",
                            bg=C["red_dk"], fg=C["red"],
                            activebackground="#6a0020")
        self._status_var.set("LISTENING")
        self._status_lbl.configure(fg=C["green"])
        self._sub_var.set(f"[{self._model_name}]")
        blocksize = int(SAMPLE_RATE * 0.05)
        try:
            self.stream = sd.InputStream(
                device=self._sel_dev_id if self._sel_dev_id >= 0 else None,
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                blocksize=blocksize, callback=self._audio_cb)
            self.stream.start()
            dev = self._mic_devices[self._mic_combo.current()][1].strip()
            self._write_log(f"MIC 1: {dev}  [{self._model_name}]", "info")
        except Exception as e:
            self.stream = None
            self._write_log(f"Mic error: {e}", "alert")
            self.running = False
            self._btn.configure(text="▶   START MONITORING",
                                bg=C["green_dk"], fg=C["green"],
                                activebackground=C["green_act"])
            self._status_var.set("STANDBY")
            self._status_lbl.configure(fg=C["mid"])
            return

        if self._dir_mode:
            try:
                self.stream2 = sd.InputStream(
                    device=self._sel_dev2_id if self._sel_dev2_id >= 0 else None,
                    samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                    blocksize=blocksize, callback=self._audio_cb2)
                self.stream2.start()
                dev2 = self._mic_devices[self._mic2_combo.current()][1].strip()
                self._write_log(f"MIC 2: {dev2}  |  sep={self._mic_sep_m}m", "info")
            except Exception as e:
                self._write_log(f"Mic 2 error: {e}", "alert")

    def _stop(self):
        if not self.running:
            return
        self.running = False
        for attr in ("stream", "stream2"):
            try:
                s = getattr(self, attr, None)
                if s:
                    s.stop(); s.close()
                    setattr(self, attr, None)
            except Exception:
                pass
        if self._dir_mode:
            self._draw_compass(None)
        while not self._q.empty():
            try: self._q.get_nowait()
            except: pass
        self._ring[:]      = 0
        self._ring2[:]     = 0
        self._hop_buf      = []
        self._hop_buf_len  = 0
        self._hop_buf2     = []
        self._hop_buf2_len = 0
        self._bar_score    = 0.0
        self._bar_label    = ""
        self._btn.configure(text="▶   START MONITORING",
                            bg=C["green_dk"], fg=C["green"],
                            activebackground=C["green_act"])
        self._status_var.set("STANDBY")
        self._status_lbl.configure(fg=C["mid"])
        self.configure(bg=C["border"])
        self._sub_var.set("")
        self._wave_canvas.coords(self._wave_line, 0, 32, 820, 32)
        self._write_log("Stopped.", "info")

    # ── audio callbacks ───────────────────────────────────────────────────────
    def _audio_cb(self, indata, frames, time_info, status):
        if not self.running:
            return
        if status and (status.input_underflow or status.input_overflow):
            self.after(0, self._stop); return
        if indata is None or len(indata) == 0:
            self.after(0, self._stop); return

        chunk = indata.flatten()
        chunk = np.append(chunk[0], chunk[1:] - 0.97 * chunk[:-1])

        self.after(0, self._draw_wave, chunk[::6])

        self._hop_buf.append(chunk)
        self._hop_buf_len += len(chunk)
        if self._hop_buf_len < self._hop_samples:
            return

        new_audio         = np.concatenate(self._hop_buf)
        leftover          = new_audio[self._hop_samples:]
        new_audio         = new_audio[:self._hop_samples]
        self._hop_buf     = [leftover] if len(leftover) else []
        self._hop_buf_len = len(leftover)
        self._ring        = np.roll(self._ring, -len(new_audio))
        self._ring[-len(new_audio):] = new_audio

        rms = float(np.sqrt(np.mean(new_audio ** 2)))
        self._avg_rms = 0.9 * self._avg_rms + 0.1 * rms
        if rms < ENERGY_GATE:
            return
        try:
            self._q.put_nowait((self._ring.copy(), rms))
        except queue.Full:
            pass

    def _audio_cb2(self, indata, frames, time_info, status):
        """Second-mic callback — updates ring2 for direction estimation."""
        if not self.running or not self._dir_mode:
            return
        if status and (status.input_underflow or status.input_overflow):
            return
        if indata is None or len(indata) == 0:
            return

        chunk = indata.flatten()
        chunk = np.append(chunk[0], chunk[1:] - 0.97 * chunk[:-1])

        self._hop_buf2.append(chunk)
        self._hop_buf2_len += len(chunk)
        if self._hop_buf2_len < self._hop_samples:
            return

        new_audio          = np.concatenate(self._hop_buf2)
        leftover2          = new_audio[self._hop_samples:]
        new_audio          = new_audio[:self._hop_samples]
        self._hop_buf2     = [leftover2] if len(leftover2) else []
        self._hop_buf2_len = len(leftover2)
        self._ring2        = np.roll(self._ring2, -len(new_audio))
        self._ring2[-len(new_audio):] = new_audio

    # ── alert ─────────────────────────────────────────────────────────────────
    def _fire_alert(self, label, score, rms=0.01):
        is_burst = any(w in label.lower() for w in ("burst", "machine", "automatic"))
        col      = C["blue"] if is_burst else C["red"]

        self._alert_until = time.time() + 2.0
        self._status_var.set(f"⚠  {label.upper()}")
        self._status_lbl.configure(fg=col)

        dir_str = ""
        if self._dir_mode:
            angle = self._calc_and_show_direction()
            if angle is not None:
                side    = "LEFT" if angle < -5 else ("RIGHT" if angle > 5 else "CENTER")
                dir_str = f"  {angle:+.0f}°  {side}"

        self._sub_var.set(f"conf {score:.0%}{dir_str}  [{self._model_name}]")
        self._write_log(f"{score:.0%}  {label}{dir_str}  [{self._model_name}]", "alert")

        self._alert_blink_running = True
        self._glow_pulse(col, step=0)

    def _glow_pulse(self, col, step):
        """Single glow pulse: fast fade-in then slow fade-out."""
        PEAK     = self._PULSE_PEAK
        TOTAL    = self._PULSE_TOTAL
        INTERVAL = self._PULSE_INTERVAL

        if step > TOTAL:
            self._alert_blink_running = False
            self._status_var.set("LISTENING")
            self._sub_var.set(f"[{self._model_name}]")
            self._set_status_bg(C["panel"])
            self._status_lbl.configure(fg=C["green"])
            self.configure(bg=C["border"])
            return

        t = step / PEAK if step <= PEAK else 1.0 - (step - PEAK) / (TOTAL - PEAK)

        self._set_status_bg(lerp_color(C["bg"],     col,      t))
        self._status_lbl.configure(fg=lerp_color(C["green"], "#ffffff", t))
        self.configure(bg=lerp_color(C["border"],   col,      t))

        self.after(INTERVAL, lambda: self._glow_pulse(col, step + 1))

    def _set_status_bg(self, color):
        """Update background of the status panel and all its children."""
        self._status_panel.configure(bg=color)
        for w in self._status_children:
            try: w.configure(bg=color)
            except Exception: pass

    # ── log ───────────────────────────────────────────────────────────────────
    def _write_log(self, msg, tag=""):
        ts = time.strftime("%H:%M:%S")
        def _u():
            self._log.configure(state=tk.NORMAL)
            self._log.insert(tk.END, f"  [{ts}]  {msg}\n", tag)
            self._log.see(tk.END)
            self._log.configure(state=tk.DISABLED)
        self.after(0, _u)


if __name__ == "__main__":
    App().mainloop()
