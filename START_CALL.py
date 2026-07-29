import customtkinter as ctk
import socket
import struct
import threading
import queue
import collections
import time
import sounddevice as sd
import numpy as np
import os
import keyboard

ctk.set_appearance_mode("Dark")

COLORS = {
    "BG_MAIN": "#121212",
    "ACCENT": "#389379",
    "TEXT_WHITE": "#FFFFFF",
    "BTN_BASE": "#333333",
    "BTN_QUIT_HOVER": "#FF0000",
    "ME_BTN": "#252525"
}

IP_LIST_FILE = "ip_list.txt"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 5000
SAMPLE_RATE, CHUNK, CHANNELS = 44100, 1024, 1

# Wire protocol (must match server.py): 4-byte big-endian length + payload.
# Payload byte 0 is the message type, the rest is the data.
MSG_REGISTER = 1
MSG_CALL = 2
MSG_END = 3
MSG_AUDIO = 4
MSG_INCOMING_CALL = 5
MSG_CALL_ENDED = 6


class LiteIntercomApp:
    def __init__(self, root):
        self.root = root

        self.win_width = 280
        self.win_height = 450

        self.root.geometry(f"{self.win_width}x{self.win_height}+0+0")
        self.root.configure(fg_color=COLORS["BG_MAIN"])
        self.root.overrideredirect(True)
        self.root.configure(highlightbackground=COLORS["ACCENT"], highlightthickness=1)

        try:
            keyboard.add_hotkey("f4", lambda: self.root.after(0, self.toggle_window_visibility))
        except Exception:
            pass

        self.my_name = socket.gethostname()
        self.server_host = DEFAULT_SERVER_HOST
        self.server_port = DEFAULT_SERVER_PORT

        self.running_event = threading.Event()
        self.running_event.set()

        self.always_on_top_var = ctk.BooleanVar(value=False)
        self.ghost_mode_var = ctk.BooleanVar(value=False)

        self.active_peer_name = None
        self.is_calling = False
        self.contact_buttons = {}

        # Networking / audio buffers.
        self.sock = None
        self.send_lock = threading.Lock()
        self.play_buffer = collections.deque(maxlen=15)   # incoming audio chunks
        self.mic_queue = queue.Queue(maxsize=15)           # outgoing audio chunks

        self._setup_ui()
        self._load_contacts()

        threading.Thread(target=self._network_thread, daemon=True).start()

    def _setup_ui(self):
        self.title_bar = ctk.CTkFrame(self.root, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        self.title_bar.pack(side="top", fill="x")

        self.title_label = ctk.CTkLabel(self.title_bar, text="LITE INTERCOM", font=("Consolas", 14, "bold"), text_color=COLORS["BTN_QUIT_HOVER"])
        self.title_label.pack(side="left", padx=10)

        ctk.CTkButton(self.title_bar, text="QUIT", width=45, height=25, corner_radius=0,
                      fg_color=COLORS["BTN_BASE"], hover_color=COLORS["BTN_QUIT_HOVER"],
                      text_color=COLORS["TEXT_WHITE"], font=("Consolas", 11, "bold"),
                      command=self.on_close).pack(side="right", padx=5, pady=5)

        ctk.CTkButton(self.title_bar, text="HELP", width=45, height=25, corner_radius=0,
                      fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                      text_color=COLORS["TEXT_WHITE"], font=("Consolas", 11, "bold"),
                      command=self.show_help).pack(side="right", padx=5, pady=5)

        self.title_bar.bind("<ButtonPress-1>", self.start_move)
        self.title_bar.bind("<B1-Motion>", self.do_move)
        self.title_label.bind("<ButtonPress-1>", self.start_move)
        self.title_label.bind("<B1-Motion>", self.do_move)

        self.settings_frame = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        self.settings_frame.pack(side="top", fill="x", padx=10, pady=(5, 5))

        self.topmost_cb = ctk.CTkCheckBox(self.settings_frame, text="ALWAYS ON TOP", variable=self.always_on_top_var,
                                          fg_color=COLORS["ACCENT"], text_color=COLORS["TEXT_WHITE"],
                                          font=("Consolas", 10), command=self.toggle_topmost,
                                          corner_radius=0, checkbox_width=14, checkbox_height=14, border_width=1)
        self.topmost_cb.pack(side="left", padx=(0, 10))

        self.ghost_cb = ctk.CTkCheckBox(self.settings_frame, text="GHOST MODE", variable=self.ghost_mode_var,
                                        fg_color=COLORS["ACCENT"], text_color=COLORS["TEXT_WHITE"],
                                        font=("Consolas", 10), command=self.toggle_ghost,
                                        corner_radius=0, checkbox_width=14, checkbox_height=14, border_width=1)
        self.ghost_cb.pack(side="left")

        self.sidebar = ctk.CTkScrollableFrame(self.root, fg_color="transparent", corner_radius=0)
        self.sidebar.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        self.call_btn = ctk.CTkButton(self.root, text="START CALL", height=45, corner_radius=0,
                                      fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                                      text_color=COLORS["TEXT_WHITE"], font=("Consolas", 14, "bold"),
                                      command=self.toggle_call)
        self.call_btn.pack(side="bottom", fill="x", padx=10, pady=(5, 20))

        ctk.CTkLabel(self.root, text="oT", font=("Consolas", 10, "bold"), text_color=COLORS["ACCENT"]).place(relx=0.98, rely=0.99, anchor="se")

    def start_move(self, e):
        self.x, self.y = e.x, e.y

    def do_move(self, e):
        self.root.geometry(f"+{self.root.winfo_x() + e.x - self.x}+{self.root.winfo_y() + e.y - self.y}")

    def toggle_topmost(self):
        self.root.attributes("-topmost", self.always_on_top_var.get())

    def toggle_ghost(self):
        alpha_value = 0.6 if self.ghost_mode_var.get() else 1.0
        self.root.attributes("-alpha", alpha_value)

    def _load_contacts(self):
        if not os.path.exists(IP_LIST_FILE):
            with open(IP_LIST_FILE, "w", encoding="utf-8") as f:
                f.write("CONFIG: SERVER, 127.0.0.1, 5000\n")
                f.write("CONFIG: NAME, Me\n")
                f.write("CONFIG: TITLE, LITE INTERCOM\n")
                f.write("# Add one contact name per line (the name each side registers with):\n")
                f.write("Alice\n")
                f.write("Bob\n")

        contacts = []
        with open(IP_LIST_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.upper().startswith("CONFIG:"):
                    parts = [p.strip() for p in line[len("CONFIG:"):].split(",")]
                    key = parts[0].upper() if parts else ""
                    if key == "TITLE" and len(parts) >= 2:
                        self.title_label.configure(text=parts[1])
                    elif key == "NAME" and len(parts) >= 2:
                        self.my_name = parts[1]
                    elif key == "SERVER" and len(parts) >= 2:
                        self.server_host = parts[1]
                        if len(parts) >= 3 and parts[2].isdigit():
                            self.server_port = int(parts[2])
                    continue

                # A contact line: the first comma-separated field is the name.
                name = line.split(",")[0].strip()
                if name:
                    contacts.append(name)

        for name in contacts:
            if name == self.my_name:
                continue
            btn = ctk.CTkButton(self.sidebar, text=name, corner_radius=0, height=45,
                                fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                                text_color=COLORS["TEXT_WHITE"], font=("Consolas", 12),
                                command=lambda n=name: self.select_peer(n))
            btn.pack(fill="x", pady=2, padx=2)
            self.contact_buttons[name] = btn

    def select_peer(self, name):
        if self.is_calling:
            return

        for btn in self.contact_buttons.values():
            btn.configure(fg_color=COLORS["BTN_BASE"], text_color=COLORS["TEXT_WHITE"])

        self.active_peer_name = name
        if name in self.contact_buttons:
            self.contact_buttons[name].configure(fg_color=COLORS["ACCENT"], text_color=COLORS["BG_MAIN"])
        self.call_btn.configure(text=f"START CALL WITH {name.upper()}")

    def toggle_call(self):
        if not self.active_peer_name:
            return

        if not self.is_calling:
            if not self.sock:
                self.call_btn.configure(text="NOT CONNECTED")
                self.root.after(1500, self._reset_call_button)
                return
            self._start_call_ui()
            self._send_frame(MSG_CALL, self.active_peer_name)
            self._begin_audio()
        else:
            self.end_call()

    def handle_incoming_call(self, caller_name):
        if not self.is_calling:
            self.select_peer(caller_name)
            self._start_call_ui()
            self._begin_audio()

    def _start_call_ui(self):
        self.call_btn.configure(text="END CALL", fg_color=COLORS["BTN_QUIT_HOVER"],
                                text_color=COLORS["TEXT_WHITE"], hover_color="#CC0000")

    def _reset_call_button(self):
        btn_text = f"START CALL WITH {self.active_peer_name.upper()}" if self.active_peer_name else "START CALL"
        self.call_btn.configure(text=btn_text, fg_color=COLORS["BTN_BASE"],
                                text_color=COLORS["TEXT_WHITE"], hover_color=COLORS["ACCENT"])

    def end_call(self):
        was_calling = self.is_calling
        self.is_calling = False
        if was_calling:
            self._send_frame(MSG_END)
        self.play_buffer.clear()
        self._drain_queue(self.mic_queue)
        self._reset_call_button()

    def _begin_audio(self):
        self._refresh_audio_devices()
        self.play_buffer.clear()
        self._drain_queue(self.mic_queue)
        self.is_calling = True
        threading.Thread(target=self._audio_engine, daemon=True).start()
        threading.Thread(target=self._mic_sender, daemon=True).start()

    # ---- networking ----------------------------------------------------

    def _send_frame(self, mtype, data=b""):
        if isinstance(data, str):
            data = data.encode("utf-8")
        payload = bytes([mtype]) + data
        frame = struct.pack(">I", len(payload)) + payload
        with self.send_lock:
            if self.sock:
                try:
                    self.sock.sendall(frame)
                except Exception:
                    pass

    def _recv_exact(self, n):
        data = b""
        while len(data) < n:
            if not self.sock:
                return None
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _recv_frame(self):
        header = self._recv_exact(4)
        if not header:
            return None, None
        (length,) = struct.unpack(">I", header)
        if length <= 0 or length > 10_000_000:
            return None, None
        payload = self._recv_exact(length)
        if payload is None:
            return None, None
        return payload[0], payload[1:]

    def _set_connected(self, ok):
        self.title_label.configure(text_color=COLORS["ACCENT"] if ok else COLORS["BTN_QUIT_HOVER"])

    def _network_thread(self):
        while self.running_event.is_set():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(10)
                s.connect((self.server_host, self.server_port))
                try:
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
                s.settimeout(None)
                self.sock = s
                self._send_frame(MSG_REGISTER, self.my_name)
                self.root.after(0, lambda: self._set_connected(True))

                while self.running_event.is_set():
                    mtype, data = self._recv_frame()
                    if mtype is None:
                        break
                    if mtype == MSG_INCOMING_CALL:
                        caller = data.decode("utf-8", "ignore")
                        self.root.after(0, lambda c=caller: self.handle_incoming_call(c))
                    elif mtype == MSG_CALL_ENDED:
                        self.root.after(0, self.end_call)
                    elif mtype == MSG_AUDIO:
                        self.play_buffer.append(data)
            except Exception:
                pass
            finally:
                self.sock = None
                self.root.after(0, lambda: self._set_connected(False))
                if self.is_calling:
                    self.root.after(0, self.end_call)

            if self.running_event.is_set():
                time.sleep(3)

    # ---- audio ---------------------------------------------------------

    def _refresh_audio_devices(self):
        # PortAudio only scans audio devices once, so devices plugged in after
        # startup are invisible until it is re-initialized. Safe to call only
        # while no stream is open (i.e. before a call starts).
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass

    def _drain_queue(self, q):
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    def _audio_engine(self):
        # A single full-duplex stream handles both the microphone and the
        # speaker, so there is no race between two separate streams and the
        # current default device (e.g. freshly plugged headphones) is used.
        def callback(indata, outdata, frames, time_info, status):
            # Capture microphone -> outgoing queue (converted to int16 to
            # roughly halve the bandwidth over the network).
            try:
                if self.is_calling:
                    samples = np.clip(indata[:, 0], -1.0, 1.0)
                    payload = (samples * 32767.0).astype(np.int16).tobytes()
                    try:
                        self.mic_queue.put_nowait(payload)
                    except queue.Full:
                        pass
            except Exception:
                pass

            # Play the next received chunk, or silence if none is buffered.
            try:
                if self.play_buffer:
                    data = self.play_buffer.popleft()
                    chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    outdata.fill(0)
                    n = min(len(chunk), frames)
                    outdata[:n, 0] = chunk[:n]
                else:
                    outdata.fill(0)
            except Exception:
                outdata.fill(0)

        try:
            with sd.Stream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32',
                           blocksize=CHUNK, callback=callback):
                while self.is_calling and self.running_event.is_set():
                    sd.sleep(100)
        except Exception:
            pass

    def _mic_sender(self):
        while self.is_calling and self.running_event.is_set():
            try:
                data = self.mic_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._send_frame(MSG_AUDIO, data)

    def toggle_window_visibility(self):
        if self.root.winfo_viewable():
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            if not self.always_on_top_var.get():
                self.root.after(100, lambda: self.root.attributes("-topmost", False))

    def show_help(self):
        help_win = ctk.CTkToplevel(self.root)
        help_win.geometry("300x270")
        help_win.overrideredirect(True)
        help_win.configure(fg_color=COLORS["BG_MAIN"], highlightthickness=1, highlightbackground=COLORS["ACCENT"])

        bar = ctk.CTkFrame(help_win, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        bar.pack(fill="x")
        ctk.CTkLabel(bar, text="SYSTEM HELP", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(bar, text="X", width=30, height=25, corner_radius=0, command=help_win.destroy,
                      fg_color=COLORS["BTN_BASE"], hover_color=COLORS["BTN_QUIT_HOVER"]).pack(side="right", padx=5)

        txt = ctk.CTkTextbox(help_win, fg_color=COLORS["BG_MAIN"], text_color=COLORS["TEXT_WHITE"], font=("Consolas", 11), corner_radius=0)
        txt.pack(fill="both", expand=True, padx=10, pady=10)

        help_content = (
            "--- LITE INTERCOM HELP ---\n\n"
            "* The title turns GREEN when connected to the server.\n"
            "* Select a target from the list.\n"
            "* Click START CALL to open the audio stream.\n"
            "* Toggle ALWAYS ON TOP to keep window above.\n"
            "* Toggle GHOST MODE for transparency.\n"
            "* Press F4 anywhere to hide/show window.\n"
            "* Edit 'ip_list.txt' to set the server and contacts.\n"
        )
        txt.insert("1.0", help_content)
        txt.configure(state="disabled")

    def on_close(self):
        self.running_event.clear()
        self.is_calling = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)


if __name__ == "__main__":
    root = ctk.CTk()
    app = LiteIntercomApp(root)
    root.mainloop()
