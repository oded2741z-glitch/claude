import contextlib
import ctypes
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import sounddevice as sd

SETTINGS_FILE: str = "settings_gui.txt"
DEFAULT_SAMPLE_RATE: int = 24000
ALLOWED_RATES: Tuple[int, ...] = (16000, 24000, 48000)
RATE_LABELS: Dict[int, str] = {16000: "16k Narrow", 24000: "24k Balanced", 48000: "48k Wide"}
CHANNELS: int = 1
DTYPE: str = "int16"
BUFFER_SIZE: int = 65535
TIMEOUT_SECS: float = 3.0

AUDIO_TAG: bytes = b"\x01"
PUNCH_TAG: bytes = b"\x02"
PUNCH_PACKET: bytes = PUNCH_TAG + b"PUNCH"
P2P_TAGS: Tuple[bytes, bytes] = (AUDIO_TAG, PUNCH_TAG)

PUNCH_DURATION: float = 2.0
PUNCH_INTERVAL: float = 0.1
HEARTBEAT_INTERVAL: float = 1.0
SERVER_TIMEOUT: float = 3.0
REPORT_REPEATS: int = 3
AUDIO_QUEUE_MAX: int = 64
STREAM_CLOSE_WAIT: float = 5.0
UI_TICK_MS: int = 200
LOG_MAX_LINES: int = 400

DEFAULT_DEVICE: str = "Default"
NOISE_KEYWORDS: Tuple[str, ...] = (
    "sound mapper", "primary sound", "stereo mix", "wave out mix", "what u hear",
)


def chunk_for(rate: int) -> int:
    return max(256, rate // 32)


def clamp_gain(value: Any) -> float:
    try:
        return max(0.0, min(2.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def apply_gain(block: np.ndarray, factor: float) -> np.ndarray:
    if factor == 1.0:
        return block
    scaled = block.astype(np.int32) * factor
    return np.clip(scaled, -32768, 32767).astype(np.int16)


class Theme:
    BG: str = "#121212"
    PANEL: str = "#1e1e1e"
    BTN: str = "#333333"
    FG: str = "#ffffff"
    MUTED: str = "#888888"
    ACCENT: str = "#ff6600"
    DANGER: str = "#ff0000"

    FONT_TITLE: Tuple[str, int, str] = ("Consolas", 13, "bold")
    FONT_LABEL: Tuple[str, int, str] = ("Arial", 10, "bold")
    FONT_TEXT: Tuple[str, int] = ("Arial", 9)
    FONT_LOG: Tuple[str, int] = ("Consolas", 9)
    FONT_BIG: Tuple[str, int, str] = ("Consolas", 13, "bold")
    FONT_MARK: Tuple[str, int] = ("Arial", 7)


STATUS: Dict[str, Tuple[str, str]] = {
    "idle": ("IDLE", Theme.MUTED),
    "ringing": ("RINGING THE SERVER...", Theme.ACCENT),
    "connecting": ("CONNECTING...", Theme.FG),
    "in_call": ("IN CALL", Theme.ACCENT),
    "ending": ("ENDING...", Theme.MUTED),
}


class IntercomClientGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()
        self.root.title("Intercom Client")
        self.root.geometry("500x800")
        self.root.minsize(460, 720)
        self.root.configure(bg=Theme.BG)
        self._clear_icon()

        self.sock: Optional[socket.socket] = None
        self.shutdown_event = threading.Event()
        self.calling: bool = False
        self.in_call: bool = False
        self.state: str = "idle"
        self._closing: bool = False
        self._remote_close: bool = False

        self.audio_lock = threading.Lock()
        self._open_streams: int = 0

        self.peer_lock = threading.Lock()
        self.assigned_peer: Optional[Tuple[str, int]] = None
        self.locked_peer: Optional[Tuple[str, int]] = None
        self.tx_peer: Optional[Tuple[str, int]] = None
        self.peer_id: str = ""

        self._peer_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._audio_q: "queue.Queue[bytes]" = queue.Queue(maxsize=AUDIO_QUEUE_MAX)

        self._last_ack: float = 0.0
        self._server_online: bool = False
        self._last_rx: float = 0.0
        self._call_started: float = 0.0
        self._events: deque = deque(maxlen=300)
        self._save_job: Optional[str] = None
        self._ui_state: str = ""

        self.call_thread: Optional[threading.Thread] = None
        self.rx_audio_thread: Optional[threading.Thread] = None
        self.tx_audio_thread: Optional[threading.Thread] = None
        self.input_index: Optional[int] = None
        self.output_index: Optional[int] = None

        self.server_ip: str = "192.168.1.11"
        self.server_port: int = 9999
        self.my_id: str = "node_B"
        self.sample_rate: int = DEFAULT_SAMPLE_RATE
        self.chunk_size: int = chunk_for(DEFAULT_SAMPLE_RATE)
        self.gain: float = 1.0
        self.level: float = 1.0
        self.input_name: str = DEFAULT_DEVICE
        self.output_name: str = DEFAULT_DEVICE
        self._load_settings()

        self._build()
        self._refresh_devices()
        self._dark_titlebar()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._signal_loop, daemon=True).start()

        self.log(f"Client [{self.my_id}] -> server {self.server_ip}:{self.server_port}")
        self._ui_tick()
        self.root.deiconify()

    def _clear_icon(self) -> None:
        try:
            self._blank_icon = tk.PhotoImage(width=1, height=1)
            self.root.iconphoto(True, self._blank_icon)
        except Exception:
            pass

    def _dark_titlebar(self) -> None:
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
            for attr in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
                if result == 0:
                    break
        except Exception:
            pass

    def _label(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(parent, text=text, font=Theme.FONT_TEXT, bg=Theme.BG, fg=Theme.FG)

    def _entry(self, parent: tk.Widget, width: int) -> tk.Entry:
        return tk.Entry(parent, width=width, bg=Theme.PANEL, fg=Theme.FG,
                        insertbackground=Theme.FG, relief="flat", highlightthickness=1,
                        highlightbackground=Theme.BTN, highlightcolor=Theme.ACCENT,
                        disabledbackground=Theme.PANEL, disabledforeground=Theme.MUTED,
                        font=Theme.FONT_TEXT)

    def _option_menu(self, parent: tk.Widget, var: tk.StringVar) -> tk.OptionMenu:
        menu = tk.OptionMenu(parent, var, DEFAULT_DEVICE)
        menu.config(bg=Theme.BTN, fg=Theme.FG, activebackground=Theme.BTN,
                    activeforeground=Theme.ACCENT, disabledforeground=Theme.MUTED,
                    highlightthickness=0, relief="flat", font=Theme.FONT_TEXT,
                    anchor="w", width=30)
        menu["menu"].config(bg=Theme.PANEL, fg=Theme.FG, activebackground=Theme.ACCENT,
                            activeforeground=Theme.BG, font=Theme.FONT_TEXT)
        return menu

    def _scale(self, parent: tk.Widget, command) -> tk.Scale:
        return tk.Scale(parent, from_=0, to=200, orient="horizontal", showvalue=False,
                        command=command, bg=Theme.BG, fg=Theme.FG, troughcolor=Theme.BTN,
                        highlightthickness=0, sliderrelief="flat", activebackground=Theme.ACCENT)

    def _build(self) -> None:
        tk.Label(self.root, text="Intercom Client", font=Theme.FONT_TITLE, bg=Theme.BG,
                 fg=Theme.ACCENT).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Frame(self.root, bg=Theme.BTN, height=1).pack(fill="x", padx=16, pady=(8, 10))

        conn = tk.Frame(self.root, bg=Theme.BG)
        conn.pack(fill="x", padx=16)
        self.ip_entry = self._entry(conn, 18)
        self.port_entry = self._entry(conn, 7)
        self.id_entry = self._entry(conn, 18)
        self._label(conn, "Server IP:").grid(row=0, column=0, sticky="w", pady=4)
        self.ip_entry.grid(row=0, column=1, sticky="w", padx=6, pady=4)
        self._label(conn, "Port:").grid(row=0, column=2, sticky="w", padx=(10, 0), pady=4)
        self.port_entry.grid(row=0, column=3, sticky="w", padx=6, pady=4)
        self._label(conn, "My ID:").grid(row=1, column=0, sticky="w", pady=4)
        self.id_entry.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        self.ip_entry.insert(0, self.server_ip)
        self.port_entry.insert(0, str(self.server_port))
        self.id_entry.insert(0, self.my_id)
        for entry in (self.ip_entry, self.port_entry, self.id_entry):
            entry.bind("<FocusOut>", lambda _e: self._apply_fields())
            entry.bind("<Return>", lambda _e: self._apply_fields())

        tk.Frame(self.root, bg=Theme.BTN, height=1).pack(fill="x", padx=16, pady=10)

        audio = tk.Frame(self.root, bg=Theme.BG)
        audio.pack(fill="x", padx=16)
        audio.columnconfigure(1, weight=1)

        self.mic_var = tk.StringVar(value=self.input_name)
        self.spk_var = tk.StringVar(value=self.output_name)
        self._label(audio, "Microphone:").grid(row=0, column=0, sticky="w", pady=3)
        self.mic_menu = self._option_menu(audio, self.mic_var)
        self.mic_menu.grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        self._label(audio, "Speaker:").grid(row=1, column=0, sticky="w", pady=3)
        self.spk_menu = self._option_menu(audio, self.spk_var)
        self.spk_menu.grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        self.refresh_btn = tk.Button(audio, text="Refresh devices", font=Theme.FONT_TEXT,
                                     bg=Theme.BTN, fg=Theme.FG, relief="flat",
                                     activebackground=Theme.BTN, activeforeground=Theme.ACCENT,
                                     disabledforeground=Theme.MUTED, command=self._refresh_devices)
        self.refresh_btn.grid(row=2, column=1, sticky="w", padx=6, pady=(2, 8))

        self._label(audio, "Quality:").grid(row=3, column=0, sticky="w", pady=3)
        rates = tk.Frame(audio, bg=Theme.BG)
        rates.grid(row=3, column=1, sticky="w", padx=6, pady=3)
        self.rate_var = tk.IntVar(value=self.sample_rate)
        self.rate_buttons: List[tk.Radiobutton] = []
        for rate in ALLOWED_RATES:
            button = tk.Radiobutton(rates, text=RATE_LABELS[rate], value=rate,
                                    variable=self.rate_var, command=self._on_rate,
                                    bg=Theme.BG, fg=Theme.FG, selectcolor=Theme.BTN,
                                    activebackground=Theme.BG, activeforeground=Theme.ACCENT,
                                    disabledforeground=Theme.MUTED, highlightthickness=0,
                                    font=Theme.FONT_TEXT)
            button.pack(side="left", padx=(0, 8))
            self.rate_buttons.append(button)

        self.gain_lbl = self._label(audio, "")
        self.gain_lbl.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.gain_scale = self._scale(audio, self._on_gain)
        self.gain_scale.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.level_lbl = self._label(audio, "")
        self.level_lbl.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.level_scale = self._scale(audio, self._on_level)
        self.level_scale.grid(row=7, column=0, columnspan=2, sticky="ew")
        self.gain_scale.set(int(round(self.gain * 100)))
        self.level_scale.set(int(round(self.level * 100)))
        self._update_vol_labels()

        self.status_lbl = tk.Label(self.root, text="STATUS: IDLE", font=Theme.FONT_LABEL,
                                   bg=Theme.BG, fg=Theme.MUTED)
        self.status_lbl.pack(pady=(14, 6))
        self.call_btn = tk.Button(self.root, text="CALL", font=Theme.FONT_BIG, bg=Theme.ACCENT,
                                  fg=Theme.BG, activebackground=Theme.ACCENT,
                                  activeforeground=Theme.BG, disabledforeground=Theme.MUTED,
                                  relief="flat", width=30, pady=8, command=self.toggle_call)
        self.call_btn.pack(pady=(0, 10))

        self.rows: Dict[str, Tuple[tk.Canvas, int, tk.Label]] = {}
        for key, title in (("SERVER", "SERVER CONNECTION"), ("CALL", "CALL")):
            card = tk.Frame(self.root, bg=Theme.PANEL, padx=10, pady=6)
            card.pack(fill="x", padx=16, pady=3)
            head = tk.Frame(card, bg=Theme.PANEL)
            head.pack(fill="x")
            led = tk.Canvas(head, width=14, height=14, bg=Theme.PANEL, highlightthickness=0)
            led.pack(side="left")
            dot = led.create_oval(2, 2, 12, 12, fill=Theme.BTN, outline="")
            tk.Label(head, text=title, font=Theme.FONT_LABEL, bg=Theme.PANEL,
                     fg=Theme.FG).pack(side="left", padx=(6, 0))
            detail = tk.Label(card, text="-", font=Theme.FONT_LOG, bg=Theme.PANEL,
                              fg=Theme.MUTED, anchor="w")
            detail.pack(fill="x", padx=(20, 0), pady=(2, 0))
            self.rows[key] = (led, dot, detail)

        tk.Label(self.root, text="oT", font=Theme.FONT_MARK, bg=Theme.BG,
                 fg=Theme.BTN).pack(side="bottom", anchor="e", padx=10, pady=4)
        self.log_text = tk.Text(self.root, height=6, bg=Theme.PANEL, fg=Theme.FG,
                                font=Theme.FONT_LOG, relief="flat", highlightthickness=0,
                                wrap="word", state="disabled", insertbackground=Theme.FG)
        self.log_text.pack(fill="both", expand=True, padx=16, pady=(6, 2))

    def log(self, msg: str) -> None:
        self._events.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _load_settings(self) -> None:
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        self.server_ip = str(data.get("ip", self.server_ip)).strip() or self.server_ip
        try:
            self.server_port = int(data.get("port", self.server_port))
        except (TypeError, ValueError):
            pass
        self.my_id = str(data.get("my_id", self.my_id)).strip() or self.my_id
        try:
            rate = int(data.get("sample_rate", self.sample_rate))
        except (TypeError, ValueError):
            rate = DEFAULT_SAMPLE_RATE
        self.sample_rate = rate if rate in ALLOWED_RATES else DEFAULT_SAMPLE_RATE
        self.chunk_size = chunk_for(self.sample_rate)
        self.gain = clamp_gain(data.get("gain", self.gain))
        self.level = clamp_gain(data.get("level", self.level))
        self.input_name = str(data.get("input_device", self.input_name)) or DEFAULT_DEVICE
        self.output_name = str(data.get("output_device", self.output_name)) or DEFAULT_DEVICE

    def _save_settings(self) -> None:
        self._save_job = None
        data = {
            "ip": self.server_ip,
            "port": str(self.server_port),
            "my_id": self.my_id,
            "sample_rate": self.sample_rate,
            "gain": round(self.gain, 2),
            "level": round(self.level, 2),
            "input_device": self.input_name,
            "output_device": self.output_name,
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            self.log(f"Could not save {SETTINGS_FILE}: {e}")

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.root.after(500, self._save_settings)

    def _apply_fields(self) -> bool:
        ip = self.ip_entry.get().strip()
        my_id = self.id_entry.get().strip()
        try:
            port = int(self.port_entry.get().strip())
            if not 0 < port < 65536:
                raise ValueError
        except ValueError:
            self.log("Port must be a number between 1 and 65535.")
            return False
        if not ip or not my_id:
            self.log("Server IP and My ID cannot be empty.")
            return False
        if (ip, port, my_id) != (self.server_ip, self.server_port, self.my_id):
            self.server_ip, self.server_port, self.my_id = ip, port, my_id
            self._last_ack = 0.0
            self._save_settings()
            self.log(f"Target set to {ip}:{port} as {my_id}.")
        return True

    def _on_rate(self) -> None:
        rate = self.rate_var.get()
        if rate not in ALLOWED_RATES:
            rate = DEFAULT_SAMPLE_RATE
            self.rate_var.set(rate)
        self.sample_rate = rate
        self.chunk_size = chunk_for(rate)
        self._schedule_save()
        self.log(f"Audio quality set to {RATE_LABELS[rate]} - must match the server.")

    def _on_gain(self, value: str) -> None:
        self.gain = clamp_gain(int(float(value)) / 100.0)
        self._update_vol_labels()
        self._schedule_save()

    def _on_level(self, value: str) -> None:
        self.level = clamp_gain(int(float(value)) / 100.0)
        self._update_vol_labels()
        self._schedule_save()

    def _update_vol_labels(self) -> None:
        self.gain_lbl.config(text=f"Microphone gain: {int(round(self.gain * 100))}%")
        self.level_lbl.config(text=f"Speaker level: {int(round(self.level * 100))}%")

    @staticmethod
    def _device_list() -> List[Dict[str, Any]]:
        try:
            devices = [dict(d, index=i) for i, d in enumerate(sd.query_devices())]
        except Exception:
            return []
        try:
            api = int(sd.default.hostapi)
            same_api = [d for d in devices if d.get("hostapi") == api]
            if same_api:
                devices = same_api
        except Exception:
            pass
        return [d for d in devices
                if not any(k in str(d.get("name", "")).lower() for k in NOISE_KEYWORDS)]

    @staticmethod
    def _names(devices: List[Dict[str, Any]], key: str) -> List[str]:
        names: List[str] = []
        for d in devices:
            name = str(d.get("name", ""))
            if d.get(key, 0) > 0 and name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _resolve(devices: List[Dict[str, Any]], name: str, key: str) -> Optional[int]:
        if name == DEFAULT_DEVICE:
            return None
        for d in devices:
            if str(d.get("name", "")) == name and d.get(key, 0) > 0:
                return int(d["index"])
        return -1

    def _set_menu(self, option_menu: tk.OptionMenu, var: tk.StringVar, names: List[str]) -> None:
        menu = option_menu["menu"]
        menu.delete(0, "end")
        for name in [DEFAULT_DEVICE] + names:
            menu.add_command(label=name, command=lambda v=name, target=var: self._pick_device(target, v))

    def _pick_device(self, var: tk.StringVar, name: str) -> None:
        var.set(name)
        self.input_name = self.mic_var.get()
        self.output_name = self.spk_var.get()
        self._schedule_save()

    def _refresh_devices(self) -> None:
        if self.state != "idle":
            return
        with self.audio_lock:
            if self._open_streams == 0:
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
        devices = self._device_list()
        inputs = self._names(devices, "max_input_channels")
        outputs = self._names(devices, "max_output_channels")
        self._set_menu(self.mic_menu, self.mic_var, inputs)
        self._set_menu(self.spk_menu, self.spk_var, outputs)
        for var, names, kind in ((self.mic_var, inputs, "Microphone"),
                                 (self.spk_var, outputs, "Speaker")):
            if var.get() != DEFAULT_DEVICE and var.get() not in names:
                self.log(f"{kind} '{var.get()}' not found - using {DEFAULT_DEVICE}.")
                var.set(DEFAULT_DEVICE)
        self.input_name = self.mic_var.get()
        self.output_name = self.spk_var.get()
        self.log(f"Audio devices: {len(inputs)} microphone(s), {len(outputs)} speaker(s).")

    @contextlib.contextmanager
    def _counted_stream(self, factory):
        with self.audio_lock:
            self._open_streams += 1
        try:
            with factory() as stream:
                yield stream
        finally:
            with self.audio_lock:
                self._open_streams -= 1

    @staticmethod
    def _parse_json_dict(data: bytes) -> Optional[Dict[str, Any]]:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return msg if isinstance(msg, dict) else None

    def _send_json(self, payload: Dict[str, Any], repeats: int = 1) -> None:
        sock = self.sock
        if sock is None:
            return
        data = json.dumps(payload).encode("utf-8")
        target = (self.server_ip, self.server_port)
        for _ in range(max(1, repeats)):
            try:
                sock.sendto(data, target)
            except (AttributeError, OSError):
                return

    def _report_hp(self, repeats: int = 1) -> None:
        self._send_json({"id": self.my_id, "status": "hp", "headphones": self.calling}, repeats)

    def _from_server(self, addr: Tuple[str, int]) -> bool:
        if addr[1] != self.server_port:
            return False
        if addr[0] == self.server_ip:
            return True
        try:
            return addr[0] == socket.gethostbyname(self.server_ip)
        except OSError:
            return False

    def _signal_loop(self) -> None:
        while not self.shutdown_event.is_set():
            self._report_hp(1)
            online = self._last_ack > 0 and time.time() - self._last_ack <= SERVER_TIMEOUT
            if online != self._server_online:
                self._server_online = online
                state = "connected" if online else "not responding"
                self.log(f"Server {state} ({self.server_ip}:{self.server_port}).")
            self.shutdown_event.wait(HEARTBEAT_INTERVAL)

    def _rx_loop(self) -> None:
        while not self.shutdown_event.is_set():
            sock = self.sock
            if sock is None:
                self.shutdown_event.wait(0.2)
                continue
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except (AttributeError, OSError):
                if self.shutdown_event.is_set():
                    return
                self.shutdown_event.wait(0.5)
                continue
            if not data:
                continue

            if data[:1] == b"{":
                info = self._parse_json_dict(data)
                if info is None:
                    continue
                if info.get("cmd") == "shutdown":
                    if self._from_server(addr):
                        self.log("Shutdown command received from the server.")
                        self._remote_close = True
                    else:
                        self.log(f"Ignored shutdown command from {addr[0]}:{addr[1]}.")
                    continue
                self._last_ack = time.time()
                if "peer_ip" in info and "peer_port" in info:
                    self._peer_q.put(info)
                continue

            payload = self._peer_audio(addr, data)
            if payload is None:
                continue
            try:
                self._audio_q.put_nowait(payload)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self._audio_q.get_nowait()
                with contextlib.suppress(queue.Full):
                    self._audio_q.put_nowait(payload)

    def _peer_audio(self, addr: Tuple[str, int], data: bytes) -> Optional[bytes]:
        tag, payload = data[:1], data[1:]
        if tag not in P2P_TAGS:
            return None
        with self.peer_lock:
            if self.locked_peer is None:
                if self.assigned_peer is None:
                    return None
                self.locked_peer = addr
                self.tx_peer = addr
            elif addr != self.locked_peer:
                return None
        self._last_rx = time.time()
        if tag != AUDIO_TAG or not payload:
            return None
        if len(payload) % (2 * CHANNELS) != 0:
            return None
        return payload

    @staticmethod
    def _drain(q: "queue.Queue") -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def toggle_call(self) -> None:
        if self.state == "idle":
            self.start_call()
        elif self.state in ("ringing", "connecting", "in_call"):
            self.hang_up()

    def start_call(self) -> None:
        if not self._apply_fields():
            return
        devices = self._device_list()
        in_idx = self._resolve(devices, self.mic_var.get(), "max_input_channels")
        out_idx = self._resolve(devices, self.spk_var.get(), "max_output_channels")
        if in_idx == -1 or out_idx == -1:
            self.log("Selected audio device is not available - press Refresh devices.")
            return
        try:
            sd.check_input_settings(device=in_idx, samplerate=self.sample_rate,
                                    channels=CHANNELS, dtype=DTYPE)
            sd.check_output_settings(device=out_idx, samplerate=self.sample_rate,
                                     channels=CHANNELS, dtype=DTYPE)
        except Exception as e:
            self.log(f"Audio device cannot run at {self.sample_rate} Hz: {e}")
            return

        self.input_index, self.output_index = in_idx, out_idx
        self._save_settings()
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None
        self.peer_id = ""
        self._drain(self._audio_q)
        self._drain(self._peer_q)

        self.calling = True
        self.state = "ringing"
        self._report_hp(REPORT_REPEATS)
        if not self._server_online:
            self.log("Server is not answering yet - it will ring as soon as it does.")
        self.log("Ringing the server...")
        self.call_thread = threading.Thread(target=self._call_flow, daemon=True)
        self.call_thread.start()

    def hang_up(self) -> None:
        if not self.calling:
            return
        self.calling = False
        self.in_call = False
        self.state = "ending"
        self._report_hp(REPORT_REPEATS)
        self.log("Hung up.")

    def _await_peer(self) -> Optional[Tuple[str, int]]:
        last_send = 0.0
        while self.calling and not self.shutdown_event.is_set():
            now = time.time()
            if now - last_send >= HEARTBEAT_INTERVAL:
                self._send_json({"id": self.my_id})
                last_send = now
            try:
                info = self._peer_q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                peer = (str(info["peer_ip"]), int(info["peer_port"]))
            except (KeyError, TypeError, ValueError):
                continue
            peer_id = info.get("peer_id")
            self.peer_id = peer_id.strip() if isinstance(peer_id, str) else ""
            peer_rate = info.get("sample_rate")
            if isinstance(peer_rate, int) and peer_rate != self.sample_rate:
                self.log(f"WARNING: server quality is {peer_rate} Hz, mine is "
                         f"{self.sample_rate} Hz - audio will sound distorted.")
            return peer
        return None

    def _call_flow(self) -> None:
        try:
            peer = self._await_peer()
            if peer is None or not self.calling:
                return
            with self.peer_lock:
                self.assigned_peer = peer
                self.tx_peer = peer
            self.state = "connecting"
            who = self.peer_id or "server"
            self.log(f"Answered by {who} ({peer[0]}:{peer[1]}). Connecting...")

            punch_until = time.time() + PUNCH_DURATION
            while self.calling and time.time() < punch_until:
                sock = self.sock
                if sock is None:
                    return
                try:
                    sock.sendto(PUNCH_PACKET, peer)
                except OSError:
                    return
                time.sleep(PUNCH_INTERVAL)
            if not self.calling:
                return

            self._last_rx = time.time()
            self.in_call = True
            self.rx_audio_thread = threading.Thread(target=self._audio_player, daemon=True)
            self.tx_audio_thread = threading.Thread(target=self._audio_sender, daemon=True)
            self.rx_audio_thread.start()
            self.tx_audio_thread.start()
            self._call_started = time.time()
            self.state = "in_call"
            self.log("Call connected.")

            while (self.calling and self.in_call and not self.shutdown_event.is_set()
                   and self.rx_audio_thread.is_alive() and self.tx_audio_thread.is_alive()):
                time.sleep(0.25)
        except Exception as e:
            self.log(f"Call error: {e}")
        finally:
            self._end_call()

    def _end_call(self) -> None:
        ended_by_other_side = self.calling
        self.in_call = False
        current = threading.current_thread()
        for t in (self.rx_audio_thread, self.tx_audio_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=STREAM_CLOSE_WAIT)
                if t.is_alive():
                    self.log("Audio device is still closing.")
        self.rx_audio_thread = None
        self.tx_audio_thread = None
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None
        self.peer_id = ""
        self._drain(self._audio_q)
        self._drain(self._peer_q)
        self._call_started = 0.0
        if ended_by_other_side:
            self.calling = False
            self._report_hp(REPORT_REPEATS)
            self.log("Call ended.")
        self.state = "idle"

    def _audio_player(self) -> None:
        try:
            with self._counted_stream(
                    lambda: sd.OutputStream(device=self.output_index, samplerate=self.sample_rate,
                                            channels=CHANNELS, dtype=DTYPE)) as stream:
                while self.in_call and not self.shutdown_event.is_set():
                    try:
                        payload = self._audio_q.get(timeout=0.25)
                    except queue.Empty:
                        if time.time() - self._last_rx > TIMEOUT_SECS:
                            self.log("No audio from the other side for 3 seconds.")
                            self.in_call = False
                            break
                        continue
                    block = apply_gain(np.frombuffer(payload, dtype=np.int16), self.level)
                    stream.write(block.reshape(-1, CHANNELS))
        except Exception as e:
            if self.in_call:
                self.log(f"Speaker error: {e}")
                self.in_call = False

    def _audio_sender(self) -> None:
        try:
            with self._counted_stream(
                    lambda: sd.InputStream(device=self.input_index, samplerate=self.sample_rate,
                                           channels=CHANNELS, dtype=DTYPE,
                                           blocksize=self.chunk_size)) as stream:
                while self.in_call and not self.shutdown_event.is_set():
                    data, _ = stream.read(self.chunk_size)
                    sock, peer = self.sock, self.tx_peer
                    if sock is None or peer is None:
                        break
                    if self.gain != 1.0:
                        data = apply_gain(data.reshape(-1), self.gain)
                    try:
                        sock.sendto(AUDIO_TAG + data.tobytes(), peer)
                    except ConnectionResetError:
                        continue
                    except (AttributeError, OSError):
                        break
        except Exception as e:
            if self.in_call:
                self.log(f"Microphone error: {e}")
                self.in_call = False

    def _set_row(self, key: str, led_color: str, text: str, text_color: str) -> None:
        led, dot, detail = self.rows[key]
        led.itemconfig(dot, fill=led_color)
        detail.config(text=text, fg=text_color)

    def _refresh_rows(self) -> None:
        target = f"{self.server_ip}:{self.server_port}"
        if self._server_online:
            self._set_row("SERVER", Theme.ACCENT, f"connected  ·  {target}", Theme.FG)
        elif self._last_ack == 0.0:
            self._set_row("SERVER", Theme.MUTED, f"trying {target}...", Theme.MUTED)
        else:
            self._set_row("SERVER", Theme.DANGER, f"no response from {target}", Theme.DANGER)

        state = self.state
        if state == "idle":
            self._set_row("CALL", Theme.BTN, "idle  ·  press CALL to ring the server", Theme.MUTED)
        elif state == "ringing":
            blink = Theme.ACCENT if int(time.time() * 2) % 2 == 0 else Theme.BTN
            self._set_row("CALL", blink, "ringing the server  ·  waiting for an answer", Theme.FG)
        elif state == "connecting":
            self._set_row("CALL", Theme.MUTED, "connecting...", Theme.FG)
        elif state == "in_call":
            secs = int(time.time() - self._call_started) if self._call_started else 0
            who = self.peer_id or "server"
            self._set_row("CALL", Theme.ACCENT, f"{who}  ·  {secs // 60:02d}:{secs % 60:02d}", Theme.FG)
        else:
            self._set_row("CALL", Theme.MUTED, "ending...", Theme.MUTED)

    def _apply_state(self) -> None:
        state = self.state
        if state == self._ui_state:
            return
        self._ui_state = state
        idle = state == "idle"
        if idle:
            self.call_btn.config(text="CALL", bg=Theme.ACCENT, fg=Theme.BG,
                                 activebackground=Theme.ACCENT, activeforeground=Theme.BG,
                                 state="normal")
        elif state == "ending":
            self.call_btn.config(text="ENDING...", bg=Theme.BTN, fg=Theme.MUTED, state="disabled")
        else:
            self.call_btn.config(text="HANG UP", bg=Theme.DANGER, fg=Theme.FG,
                                 activebackground=Theme.DANGER, activeforeground=Theme.FG,
                                 state="normal")
        lock = "normal" if idle else "disabled"
        for widget in (self.ip_entry, self.port_entry, self.id_entry, self.refresh_btn,
                       self.mic_menu, self.spk_menu, *self.rate_buttons):
            widget.config(state=lock)
        text, color = STATUS.get(state, ("", Theme.MUTED))
        self.status_lbl.config(text=f"STATUS: {text}", fg=color)

    def _flush_log(self) -> None:
        lines: List[str] = []
        while self._events:
            try:
                lines.append(self._events.popleft())
            except IndexError:
                break
        if not lines:
            return
        self.log_text.config(state="normal")
        for line in lines:
            self.log_text.insert("end", line + "\n")
        count = int(self.log_text.index("end-1c").split(".")[0])
        if count > LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{count - LOG_MAX_LINES}.0")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _ui_tick(self) -> None:
        if self._closing:
            return
        if self._remote_close:
            self.on_close()
            return
        try:
            self._flush_log()
            self._refresh_rows()
            self._apply_state()
            self.root.after(UI_TICK_MS, self._ui_tick)
        except tk.TclError:
            return

    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.calling = False
        self.in_call = False
        self._send_json({"id": self.my_id, "status": "disconnected"}, REPORT_REPEATS)
        self.shutdown_event.set()
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_settings()
        thread = self.call_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=STREAM_CLOSE_WAIT)
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    IntercomClientGUI(root)
    root.mainloop()
