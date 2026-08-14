import socket
import json
import threading
import time
import ipaddress
import tkinter as tk
from tkinter import scrolledtext, messagebox
import sounddevice as sd
import numpy as np
import os
from typing import Optional, Tuple, Dict, Any

# --- Constants & Settings ---
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
CHUNK_SIZE: int = 512
DTYPE: str = 'int16'
BUFFER_SIZE: int = 65535          # UDP חותך בשקט חבילות גדולות מהבאפר
TIMEOUT_SECS: float = 3.0
SETTINGS_FILE: str = "settings_A.txt"

# --- P2P wire protocol ---
# חבילות סיגנלינג הן JSON ומתחילות ב-'{'. חבילות P2P מתויגות בבייט ראשון,
# כדי שלעולם לא ננגן הודעת בקרה כרעש.
AUDIO_TAG: bytes = b'\x01'
PUNCH_TAG: bytes = b'\x02'
PUNCH_PACKET: bytes = PUNCH_TAG + b'PUNCH'
P2P_TAGS: Tuple[bytes, bytes] = (AUDIO_TAG, PUNCH_TAG)

# --- Signalling server behaviour ---
CLIENT_TTL: float = 30.0          # רישום ישן לא ישמש להצמדה
MATCH_RETRIES: int = 5            # UDP לא אמין - שולחים את פרטי העמית כמה פעמים
MATCH_RETRY_DELAY: float = 0.05
PUNCH_DURATION: float = 2.0
PUNCH_INTERVAL: float = 0.1


class Theme:
    """Centralized configuration for UI colors and fonts."""
    BG: str = "#121212"
    FG: str = "#FFFFFF"
    BTN_BG: str = "#333333"
    ACCENT: str = "#ff7300"
    QUIT: str = "#ff0000"
    ENTRY_BG: str = "#222222"
    LOG_BG: str = "#1e1e1e"
    LOG_FG: str = "#FFFFFF"
    DIVIDER: str = "#444444"
    DISCONNECTED: str = "#888888"
    CONNECTED: str = "#00ff00"
    WAITING: str = "#f0ad4e"
    PUNCHING: str = "#0275d8"

    FONT_TITLE: Tuple[str, int, str] = ("Consolas", 11, "bold")
    FONT_LABEL: Tuple[str, int, str] = ("Arial", 10, "bold")
    FONT_ENTRY: Tuple[str, int] = ("Arial", 9)
    FONT_LOG: Tuple[str, int] = ("Consolas", 9)


HELP_TEXT: str = (
    "P2P Intercom\n\n"
    "1. Start the internal signalling server on one machine (or point both\n"
    "   clients at an external one).\n"
    "2. Set Server IP / Port / My ID on both sides - the two IDs must differ.\n"
    "3. Press START INTERCOM on both. The server matches the first two\n"
    "   clients it sees and hands each one the other's address.\n\n"
    "External IP: only relevant when the signalling server is NOT on the\n"
    "public internet. The port is never translated, so this requires a\n"
    "static port forward on the router. On a single LAN, tick Local Mode.\n\n"
    "Hole punching works with cone NAT. Symmetric NAT needs a relay (TURN)."
)


class IntercomGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root: tk.Tk = root
        self.root.geometry("480x640")
        self.root.configure(bg=Theme.BG)
        self.root.overrideredirect(True)

        # Threading & Concurrency Flags
        self.audio_lock: threading.Lock = threading.Lock()
        self.last_refresh_time: float = 0.0
        self._open_streams: int = 0          # כמה streams פתוחים כרגע (מוגן ע"י audio_lock)

        self.sock: Optional[socket.socket] = None
        self.is_running: bool = False
        self.rx_thread: Optional[threading.Thread] = None
        self.tx_thread: Optional[threading.Thread] = None
        self._stop_lock: threading.Lock = threading.Lock()

        # Peer address handling
        self.peer_lock: threading.Lock = threading.Lock()
        self.assigned_peer: Optional[Tuple[str, int]] = None
        self.locked_peer: Optional[Tuple[str, int]] = None
        self.tx_peer: Optional[Tuple[str, int]] = None

        # Signalling target (נשמר כדי לדווח ניתוק בסגירה)
        self.signalling_addr: Optional[Tuple[str, int]] = None
        self.my_id: str = ""

        self.server_thread: Optional[threading.Thread] = None
        self.server_stop_event: threading.Event = threading.Event()
        self._rewrite_warned: bool = False

        # Window Drag Coordinates
        self._offset_x: int = 0
        self._offset_y: int = 0

        self._create_widgets()
        self.load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def start_move(self, event: tk.Event) -> None:
        """Records initial coordinates for borderless window dragging."""
        self._offset_x = event.x
        self._offset_y = event.y

    def do_move(self, event: tk.Event) -> None:
        """Calculates and applies new window position during a drag event."""
        x: int = self.root.winfo_x() + event.x - self._offset_x
        y: int = self.root.winfo_y() + event.y - self._offset_y
        self.root.geometry(f"+{x}+{y}")

    def _create_widgets(self) -> None:
        """Initializes and packs all Tkinter widgets using the Theme class."""
        # --- Custom Title Bar ---
        title_bar = tk.Frame(self.root, bg=Theme.BG)
        title_bar.pack(fill="x", pady=5, padx=5)
        title_bar.bind("<ButtonPress-1>", self.start_move)
        title_bar.bind("<B1-Motion>", self.do_move)

        title_lbl = tk.Label(title_bar, text="Code Injection - Intercom Server", font=Theme.FONT_TITLE,
                             bg=Theme.BG, fg=Theme.ACCENT)
        title_lbl.pack(side="left", padx=10)
        title_lbl.bind("<ButtonPress-1>", self.start_move)
        title_lbl.bind("<B1-Motion>", self.do_move)

        tk.Button(title_bar, text="Quit", bg=Theme.QUIT, fg=Theme.FG, font=Theme.FONT_LABEL,
                  relief="flat", width=6, command=self.on_close, activebackground="#cc0000",
                  activeforeground=Theme.FG).pack(side="right", padx=2)

        tk.Button(title_bar, text="Help", bg=Theme.BTN_BG, fg=Theme.FG, font=Theme.FONT_LABEL,
                  relief="flat", width=6, command=self.show_help, activebackground=Theme.DIVIDER,
                  activeforeground=Theme.FG).pack(side="right", padx=2)

        tk.Frame(self.root, bg=Theme.DIVIDER, height=1).pack(fill="x", padx=10, pady=5)

        # --- Integrated Signalling Server ---
        tk.Label(self.root, text="Integrated Signalling Server", font=Theme.FONT_LABEL, bg=Theme.BG, fg=Theme.FG).pack(anchor="w", padx=15, pady=(10, 0))
        server_frame = tk.Frame(self.root, bg=Theme.BG)
        server_frame.pack(fill="x", padx=20, pady=5)

        tk.Label(server_frame, text="External IP:", bg=Theme.BG, fg=Theme.FG, font=Theme.FONT_ENTRY).grid(row=0, column=0, sticky="w", pady=5)
        self.ext_ip_entry = tk.Entry(server_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG,
                                     relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=20)
        self.ext_ip_entry.grid(row=0, column=1, padx=10, pady=5, sticky="w")

        self.local_mode_var = tk.BooleanVar(value=False)
        tk.Checkbutton(server_frame, text="Use Local Mode (Ignore External IP)", variable=self.local_mode_var,
                       command=self.save_settings, bg=Theme.BG, fg=Theme.FG, selectcolor=Theme.BTN_BG,
                       activebackground=Theme.BG, activeforeground=Theme.FG).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

        self.server_btn = tk.Button(server_frame, text="START INTERNAL SERVER", font=("Arial", 9, "bold"),
                                    bg=Theme.BTN_BG, fg=Theme.FG, relief="flat", width=35, pady=4,
                                    command=self.toggle_local_server, activebackground=Theme.DIVIDER, activeforeground=Theme.FG)
        self.server_btn.grid(row=2, column=0, columnspan=2, pady=10)

        tk.Frame(self.root, bg=Theme.DIVIDER, height=1).pack(fill="x", padx=10, pady=5)

        # --- Connection Settings ---
        tk.Label(self.root, text="Connection Settings", font=Theme.FONT_LABEL, bg=Theme.BG, fg=Theme.FG).pack(anchor="w", padx=15, pady=(5, 0))
        config_frame = tk.Frame(self.root, bg=Theme.BG)
        config_frame.pack(fill="x", padx=20, pady=5)

        tk.Label(config_frame, text="Server IP:", bg=Theme.BG, fg=Theme.FG).grid(row=0, column=0, sticky="w", pady=5)
        self.server_ip_entry = tk.Entry(config_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG, relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=18)
        self.server_ip_entry.grid(row=0, column=1, pady=5, padx=5)

        tk.Label(config_frame, text="Port:", bg=Theme.BG, fg=Theme.FG).grid(row=0, column=2, sticky="w", pady=5, padx=(10, 0))
        self.server_port_entry = tk.Entry(config_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG, relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=7)
        self.server_port_entry.grid(row=0, column=3, pady=5, padx=5)

        tk.Label(config_frame, text="My ID:", bg=Theme.BG, fg=Theme.FG).grid(row=1, column=0, sticky="w", pady=5)
        self.my_id_entry = tk.Entry(config_frame, bg=Theme.ENTRY_BG, fg=Theme.FG, insertbackground=Theme.FG, relief="flat", highlightthickness=1, highlightbackground=Theme.DIVIDER, width=18)
        self.my_id_entry.grid(row=1, column=1, pady=5, padx=5)

        self.status_label = tk.Label(self.root, text="STATUS: DISCONNECTED", font=Theme.FONT_LABEL, bg=Theme.BG, fg=Theme.DISCONNECTED)
        self.status_label.pack(pady=10)

        # --- Main Toggle Button ---
        btn_border = tk.Frame(self.root, bg=Theme.FG, padx=1, pady=1)
        btn_border.pack(pady=5)
        self.toggle_btn = tk.Button(btn_border, text="START INTERCOM", font=("Consolas", 12, "bold"),
                                    bg=Theme.ACCENT, fg="#000000", activebackground="#e66a00", activeforeground="#000000",
                                    relief="flat", width=42, pady=6, command=self.toggle_intercom)
        self.toggle_btn.pack()

        # --- System Logs ---
        log_frame = tk.Frame(self.root, bg=Theme.BG)
        log_frame.pack(fill="both", expand=True, padx=15, pady=15)
        self.log_area = scrolledtext.ScrolledText(log_frame, height=8, font=Theme.FONT_LOG, bg=Theme.LOG_BG, fg=Theme.LOG_FG,
                                                  insertbackground=Theme.FG, relief="flat", highlightthickness=1,
                                                  highlightbackground=Theme.DIVIDER, state="disabled")
        self.log_area.pack(fill="both", expand=True)

        # Configure color tags for logs
        self.log_area.tag_config("white", foreground="#FFFFFF")
        self.log_area.tag_config("red", foreground="#FF0000")
        self.log_area.tag_config("green", foreground="#00FF00")
        self.log_area.tag_config("yellow", foreground="#FFFF00")
        self.log_area.tag_config("purple", foreground="#a020f0")

        tk.Label(self.root, text="oT", font=("Arial", 9, "bold"), bg=Theme.BG, fg=Theme.DIVIDER).pack(side="bottom", anchor="e", padx=10, pady=5)

    def show_help(self) -> None:
        messagebox.showinfo("Help", HELP_TEXT)

    def log(self, msg: str, color_tag: str = "white") -> None:
        """Thread-safe UI logging injection with color support."""
        def append() -> None:
            try:
                self.log_area.config(state="normal")
                self.log_area.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", color_tag)
                self.log_area.see("end")
                self.log_area.config(state="disabled")
            except tk.TclError:
                pass  # החלון נסגר לפני שה-callback רץ
        self.root.after(0, append)

    def update_status(self, text: str, color: str) -> None:
        """Thread-safe status label update."""
        def apply() -> None:
            try:
                self.status_label.config(text=f"STATUS: {text}", fg=color)
            except tk.TclError:
                pass
        self.root.after(0, apply)

    def save_settings(self) -> None:
        """Serializes current UI entries into a localized JSON config."""
        data: Dict[str, Any] = {
            "ext_ip": self.ext_ip_entry.get().strip(),
            "local_mode": self.local_mode_var.get(),
            "ip": self.server_ip_entry.get().strip(),
            "port": self.server_port_entry.get().strip(),
            "my_id": self.my_id_entry.get().strip()
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            self.log(f"Error saving settings: {e}")

    def load_settings(self) -> None:
        """Parses the JSON settings file and populates UI elements."""
        if not os.path.exists(SETTINGS_FILE):
            self.ext_ip_entry.insert(0, "10.20.1.200")
            self.server_ip_entry.insert(0, "127.0.0.1")
            self.server_port_entry.insert(0, "9999")
            self.my_id_entry.insert(0, "node_A")
            return

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)

            for entry, key, default in [
                (self.ext_ip_entry, "ext_ip", "10.20.1.200"),
                (self.server_ip_entry, "ip", "127.0.0.1"),
                (self.server_port_entry, "port", "9999"),
                (self.my_id_entry, "my_id", "node_A")
            ]:
                entry.delete(0, tk.END)
                entry.insert(0, str(data.get(key, default)))

            self.local_mode_var.set(data.get("local_mode", False))
        except (OSError, json.JSONDecodeError) as e:
            self.log(f"Error loading settings: {e}")

    # ------------------------------------------------------------------
    # Signalling server
    # ------------------------------------------------------------------
    def toggle_local_server(self) -> None:
        if self.server_thread is None or not self.server_thread.is_alive():
            self.start_local_server()
        else:
            self.stop_local_server()

    def start_local_server(self) -> None:
        try:
            server_port: int = int(self.server_port_entry.get().strip())
        except ValueError:
            self.log("Failed to start server: Invalid port number.", "red")
            return

        ext_ip: str = self.ext_ip_entry.get().strip()
        use_local: bool = self.local_mode_var.get()

        self.server_stop_event.clear()
        self._rewrite_warned = False
        # הכפתור מתעדכן רק אחרי ש-bind באמת הצליח
        self.server_btn.config(text="STARTING SERVER...", bg=Theme.BTN_BG, fg=Theme.FG)
        self.server_thread = threading.Thread(target=self._run_internal_server,
                                              args=(server_port, ext_ip, use_local), daemon=True)
        self.server_thread.start()

    def stop_local_server(self) -> None:
        self.server_stop_event.set()
        if self.server_thread and self.server_thread.is_alive():
            # לסוקט השרת יש timeout של שנייה - נותנים לו מספיק זמן לשחרר את הפורט
            self.server_thread.join(timeout=2.5)
        self.server_thread = None
        self._mark_server_stopped()
        self.log("Internal Signalling Server STOPPED.")

    def _mark_server_started(self, port: int) -> None:
        self.server_btn.config(text="STOP INTERNAL SERVER", bg=Theme.ACCENT, fg="#000000")
        self.log(f"Internal Signalling Server STARTED on port {port}.", "green")

    def _mark_server_stopped(self) -> None:
        try:
            self.server_btn.config(text="START INTERNAL SERVER", bg=Theme.BTN_BG, fg=Theme.FG)
        except tk.TclError:
            pass

    @staticmethod
    def _parse_json_dict(data: bytes) -> Optional[Dict[str, Any]]:
        """מפענח הודעת סיגנלינג. מחזיר None לכל דבר שאינו JSON dict תקין."""
        try:
            msg = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None  # חבילת אודיו / PUNCH / זבל
        return msg if isinstance(msg, dict) else None

    def _visible_ip(self, ip: str, ext_ip: str, use_local: bool) -> str:
        """בוחר את הכתובת שתדווח לעמית. מכסה את כל הטווחים הפרטיים, לא רק 192.168."""
        if use_local or not ext_ip:
            return ip
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if not (parsed.is_private or parsed.is_loopback):
            return ip
        if not self._rewrite_warned:
            self._rewrite_warned = True
            self.log("[Server] Rewriting private IPs to External IP. Note: the PORT is "
                     "not translated, so this needs a static port forward.", "yellow")
        return ext_ip

    def _purge_stale(self, clients: Dict[str, Tuple[Tuple[str, int], float]], now: float) -> None:
        for cid in [c for c, (_, ts) in clients.items() if now - ts > CLIENT_TTL]:
            del clients[cid]
            self.log(f"[Server] {cid}: registration expired.", "yellow")

    def _handle_signalling(self, server_sock: socket.socket,
                           clients: Dict[str, Tuple[Tuple[str, int], float]],
                           data: bytes, addr: Tuple[str, int],
                           ext_ip: str, use_local: bool) -> None:
        msg = self._parse_json_dict(data)
        if msg is None:
            return

        client_id = msg.get("id")
        if not isinstance(client_id, str) or not client_id.strip():
            return
        client_id = client_id.strip()

        # קבלת דיווח ניתוק מהלקוח
        if msg.get("status") == "disconnected":
            if clients.pop(client_id, None) is not None:
                self.log(f"[Server] {client_id}: Headphones disconnected !!!", "red")
            return

        now = time.time()
        self._purge_stale(clients, now)

        # דיווח התחברות מהלקוח
        if client_id not in clients:
            self.log(f"[Server] {client_id}: Headphones connected !!!", "purple")
        clients[client_id] = (addr, now)

        if len(clients) == 2:
            (peer1_id, (addr1, _)), (peer2_id, (addr2, _)) = clients.items()
            self.log(f"[Server] Matched {peer1_id} <---> {peer2_id}.", "green")

            ip1 = self._visible_ip(addr1[0], ext_ip, use_local)
            ip2 = self._visible_ip(addr2[0], ext_ip, use_local)

            reply1 = json.dumps({"peer_ip": ip2, "peer_port": addr2[1]}).encode('utf-8')
            reply2 = json.dumps({"peer_ip": ip1, "peer_port": addr1[1]}).encode('utf-8')
            for _ in range(MATCH_RETRIES):
                server_sock.sendto(reply1, addr1)
                server_sock.sendto(reply2, addr2)
                time.sleep(MATCH_RETRY_DELAY)

            clients.clear()

    def _run_internal_server(self, port: int, ext_ip: str, use_local: bool) -> None:
        """UDP Signalling server for peer registration and matching."""
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server_sock.bind(('0.0.0.0', port))
            server_sock.settimeout(1.0)
        except OSError as e:
            self.log(f"[Server] Bind Error: {e}", "red")
            self.root.after(0, self._mark_server_stopped)
            return

        self.root.after(0, self._mark_server_started, port)
        clients: Dict[str, Tuple[Tuple[str, int], float]] = {}

        with server_sock:
            while not self.server_stop_event.is_set():
                try:
                    data, addr = server_sock.recvfrom(BUFFER_SIZE)
                except socket.timeout:
                    self._purge_stale(clients, time.time())
                    continue
                except ConnectionResetError:
                    continue  # Windows WinError 10054
                except OSError as e:
                    self.log(f"[Server] Socket error: {e}", "red")
                    break

                # חבילה פגומה אחת לא מפילה את השרת
                try:
                    self._handle_signalling(server_sock, clients, data, addr, ext_ip, use_local)
                except Exception as e:
                    self.log(f"[Server] Error handling packet: {e}", "red")

        self.root.after(0, self._mark_server_stopped)

    # ------------------------------------------------------------------
    # Audio hardware
    # ------------------------------------------------------------------
    def _safe_refresh_hardware(self) -> None:
        """Restarts the PortAudio bindings. Never runs while a stream is open."""
        if not self.is_running:
            return
        with self.audio_lock:
            # אתחול PortAudio בזמן ש-stream פתוח בתרד אחר מקריס את התהליך ברמת ה-C
            if self._open_streams > 0:
                return
            current_time = time.time()
            if current_time - self.last_refresh_time > TIMEOUT_SECS:
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
                self.last_refresh_time = current_time

    def _stream_opened(self) -> None:
        with self.audio_lock:
            self._open_streams += 1

    def _stream_closed(self) -> None:
        with self.audio_lock:
            self._open_streams -= 1

    # ------------------------------------------------------------------
    # Intercom connection
    # ------------------------------------------------------------------
    def toggle_intercom(self) -> None:
        if not self.is_running:
            self.start_connection()
        else:
            self.stop_connection()

    def start_connection(self) -> None:
        self.save_settings()
        try:
            server_port: int = int(self.server_port_entry.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Port must be a valid number.")
            return

        server_ip = self.server_ip_entry.get().strip()
        my_id = self.my_id_entry.get().strip()
        if not my_id:
            messagebox.showerror("Error", "Please enter a valid ID.")
            return

        with self._stop_lock:
            if self.is_running:      # מגן מפני לחיצה כפולה מהירה
                return
            self.is_running = True

        self.last_refresh_time = 0.0
        self.my_id = my_id
        self.signalling_addr = (server_ip, server_port)
        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None

        self.toggle_btn.config(text="DISCONNECT", bg=Theme.QUIT, fg=Theme.FG)
        for entry in (self.server_ip_entry, self.server_port_entry, self.my_id_entry, self.ext_ip_entry):
            entry.config(state="disabled")

        self._safe_refresh_hardware()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)

        threading.Thread(target=self._connection_flow, args=(server_ip, server_port, my_id),
                         daemon=True).start()

    def _await_peer(self, server_ip: str, server_port: int, my_id: str) -> Optional[Tuple[str, int]]:
        """ממתין לפרטי העמית. מתעלם מכל חבילה שאינה תשובת סיגנלינג תקינה."""
        msg: bytes = json.dumps({"id": my_id}).encode('utf-8')
        while self.is_running and self.sock:
            try:
                self.sock.sendto(msg, (server_ip, server_port))
            except (AttributeError, OSError):
                return None

            try:
                data, _ = self.sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except ConnectionResetError:
                time.sleep(1)
                continue
            except (AttributeError, OSError):
                return None

            # חבילת אודיו מעמית שהקדים אותנו לא מפילה את החיבור
            info = self._parse_json_dict(data)
            if not info or "peer_ip" not in info or "peer_port" not in info:
                continue
            try:
                return (str(info["peer_ip"]), int(info["peer_port"]))
            except (TypeError, ValueError):
                continue
        return None

    def _connection_flow(self, server_ip: str, server_port: int, my_id: str) -> None:
        try:
            if not self.sock:
                return

            self.update_status("WAITING FOR PEER...", Theme.WAITING)
            self.log("Waiting for matching peer from server...")
            self.log("Please wait 5 seconds...", "yellow")

            peer_addr = self._await_peer(server_ip, server_port, my_id)
            if not self.is_running or not peer_addr:
                self.stop_connection()
                return

            with self.peer_lock:
                self.assigned_peer = peer_addr
                self.tx_peer = peer_addr

            self.log(f"Peer Target Assigned -> IP: {peer_addr[0]}, Port: {peer_addr[1]}", "green")
            self.update_status("PUNCHING NAT...", Theme.PUNCHING)

            punch_until = time.time() + PUNCH_DURATION
            while self.is_running and time.time() < punch_until:
                try:
                    self.sock.sendto(PUNCH_PACKET, peer_addr)
                except (AttributeError, OSError):
                    break
                time.sleep(PUNCH_INTERVAL)

            if not self.is_running:
                return

            self.update_status("CONNECTED (P2P AUDIO)", Theme.CONNECTED)
            self.log("NAT hole punched! Live audio streaming active.", "green")

            self.rx_thread = threading.Thread(target=self._audio_receiver, daemon=True)
            self.tx_thread = threading.Thread(target=self._audio_sender, daemon=True)
            self.rx_thread.start()
            self.tx_thread.start()

        except Exception as e:
            # בלי זה חריגה כאן משאירה את ה-GUI תקוע ב-WAITING FOR PEER לנצח
            if self.is_running:
                self.log(f"Connection error: {e}", "red")
                self.stop_connection()

    def _peer_audio(self, addr: Tuple[str, int], data: bytes) -> Optional[bytes]:
        """מאמת שהחבילה הגיעה מהעמית ומחזיר payload רק לחבילות אודיו."""
        tag, payload = data[:1], data[1:]
        if tag not in P2P_TAGS:
            return None  # JSON מהשרת או זבל - לעולם לא מתנגן

        with self.peer_lock:
            if self.locked_peer is None:
                # ננעלים על הכתובת שממנה הגיעה החבילה המתויגת הראשונה. זה גם
                # מכסה מצב שבו ה-NAT הקצה פורט שונה מזה שהשרת דיווח.
                self.locked_peer = addr
                self.tx_peer = addr
                if addr != self.assigned_peer:
                    self.log(f"Peer reached us from {addr[0]}:{addr[1]} (assigned "
                             f"{self.assigned_peer}). Locking on.", "yellow")
            elif addr != self.locked_peer:
                return None  # מונע הזרקת אודיו מכל מקור אחר ברשת

        if tag != AUDIO_TAG or not payload:
            return None
        if len(payload) % (2 * CHANNELS) != 0:
            return None  # חבילה קטומה
        return payload

    def _audio_receiver(self) -> None:
        last_data_time: float = time.time()
        while self.is_running:
            restart_needed = False
            try:
                with sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE) as stream:
                    self._stream_opened()
                    try:
                        while self.is_running:
                            sock = self.sock
                            if sock is None:
                                break
                            try:
                                data, addr = sock.recvfrom(BUFFER_SIZE)
                            except socket.timeout:
                                if time.time() - last_data_time > TIMEOUT_SECS:
                                    if self.is_running:
                                        self.log("Peer disconnected (3 seconds timeout).")
                                        self.root.after(0, self.stop_connection)
                                    return
                                continue
                            except ConnectionResetError:
                                if self.is_running:
                                    self.log("Peer disconnected unexpectedly.")
                                    self.root.after(0, self.stop_connection)
                                return
                            except (AttributeError, OSError):
                                return  # הסוקט נסגר - יציאה נקייה, בלי הודעת חומרה מטעה

                            last_data_time = time.time()
                            payload = self._peer_audio(addr, data)
                            if payload is None:
                                continue
                            try:
                                stream.write(np.frombuffer(payload, dtype=np.int16).reshape(-1, CHANNELS))
                            except sd.PortAudioError:
                                if self.is_running:
                                    self.log("Output device error. Reconnecting...")
                                restart_needed = True
                                break
                    finally:
                        self._stream_closed()
            except sd.PortAudioError:
                restart_needed = True

            if not restart_needed or not self.is_running:
                return
            time.sleep(2)
            self._safe_refresh_hardware()

    def _audio_sender(self) -> None:
        while self.is_running:
            restart_needed = False
            try:
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE, blocksize=CHUNK_SIZE) as stream:
                    self._stream_opened()
                    try:
                        while self.is_running:
                            try:
                                data, _ = stream.read(CHUNK_SIZE)
                            except sd.PortAudioError:
                                if self.is_running:
                                    self.log("Input device disconnected. Reconnecting...")
                                restart_needed = True
                                break

                            sock, peer = self.sock, self.tx_peer
                            if sock is None or peer is None:
                                return
                            try:
                                sock.sendto(AUDIO_TAG + data.tobytes(), peer)
                            except ConnectionResetError:
                                continue
                            except (AttributeError, OSError):
                                return  # הסוקט נסגר - יציאה נקייה
                    finally:
                        self._stream_closed()
            except sd.PortAudioError:
                restart_needed = True

            if not restart_needed or not self.is_running:
                return
            time.sleep(2)
            self._safe_refresh_hardware()

    def stop_connection(self) -> None:
        with self._stop_lock:
            if not self.is_running:
                return
            self.is_running = False

        # דיווח ניתוק לשרת כדי שישחרר את הסלוט מיד
        if self.sock and self.signalling_addr and self.my_id:
            try:
                self.sock.sendto(json.dumps({"id": self.my_id, "status": "disconnected"}).encode('utf-8'),
                                 self.signalling_addr)
            except OSError:
                pass

        # קודם ממתינים לתרדי האודיו, ורק אז סוגרים את הסוקט. סגירה מתחת
        # לרגליהם משאירה את כרטיס הקול תפוס ומייצרת חריגות מטעות.
        current = threading.current_thread()
        for t in (self.rx_thread, self.tx_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=2.5)
        self.rx_thread = None
        self.tx_thread = None

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            finally:
                self.sock = None

        with self.peer_lock:
            self.assigned_peer = None
            self.locked_peer = None
            self.tx_peer = None

        def reset_gui() -> None:
            try:
                self.toggle_btn.config(text="START INTERCOM", bg=Theme.ACCENT, fg="#000000")
                for entry in (self.server_ip_entry, self.server_port_entry, self.my_id_entry, self.ext_ip_entry):
                    entry.config(state="normal")
                self.status_label.config(text="STATUS: DISCONNECTED", fg=Theme.DISCONNECTED)
                self.log("Intercom stopped.", "red")
            except tk.TclError:
                pass

        self.root.after(0, reset_gui)

    def on_close(self) -> None:
        self.stop_connection()
        self.stop_local_server()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = IntercomGUI(root)
    root.mainloop()
