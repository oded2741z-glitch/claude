import customtkinter as ctk
import socket
import threading
import queue
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
AUDIO_PORT = 6000
CTRL_PORT = 5001
SAMPLE_RATE, CHUNK, CHANNELS = 44100, 1024, 1
SOCK_TIMEOUT = 0.5


def get_local_ip():
    # gethostbyname(gethostname()) often returns 127.0.0.1 or the wrong NIC,
    # so ask the routing table which address would reach the outside world.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


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

        self.my_ip = get_local_ip()
        self.my_name = "Me"
        self.running_event = threading.Event()
        self.running_event.set()

        self.always_on_top_var = ctk.BooleanVar(value=False)
        self.ghost_mode_var = ctk.BooleanVar(value=False)

        self.x, self.y = 0, 0
        self.active_peer_ip = None
        self.active_peer_name = None
        self.is_calling = False
        self.peers = {}
        self.contact_buttons = {}

        self._audio_queue = queue.Queue(maxsize=10)

        # Device hot-plug coordination. A PortAudio rescan may only run while
        # no stream is open, so the capture side asks for one and waits for
        # the playback side to release its output stream first.
        self._rescan_request = threading.Event()
        self._output_idle = threading.Event()
        self._output_idle.set()
        self._mic_ok = None

        self.ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ctrl_sock.bind(("0.0.0.0", CTRL_PORT))
        self.ctrl_sock.settimeout(SOCK_TIMEOUT)

        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.audio_sock.bind(("0.0.0.0", AUDIO_PORT))
        self.audio_sock.settimeout(SOCK_TIMEOUT)

        self._setup_ui()
        self._load_contacts()

        threading.Thread(target=self._control_listener, daemon=True).start()
        threading.Thread(target=self._audio_receiver, daemon=True).start()
        threading.Thread(target=self._audio_playback, daemon=True).start()

    def _setup_ui(self):
        self.title_bar = ctk.CTkFrame(self.root, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        self.title_bar.pack(side="top", fill="x")

        self.title_label = ctk.CTkLabel(self.title_bar, text="LITE INTERCOM", font=("Consolas", 14, "bold"), text_color=COLORS["ACCENT"])
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

        self.mic_status_label = ctk.CTkLabel(self.root, text="", font=("Consolas", 10),
                                             text_color=COLORS["BTN_QUIT_HOVER"], anchor="w")
        self.mic_status_label.pack(side="bottom", fill="x", padx=10)

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
                f.write("Local_Test, 127.0.0.1\n")

        with open(IP_LIST_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.startswith("CONFIG: TITLE"):
                    # split(",", 1) so a title containing commas survives intact.
                    parts = line.split(",", 1)
                    if len(parts) > 1 and parts[1].strip():
                        self.title_label.configure(text=parts[1].strip())
                    continue

                if line.startswith("CONFIG"):
                    continue

                parts = [x.strip() for x in line.split(",")]
                # A malformed line used to raise IndexError and kill startup.
                if len(parts) < 2 or not parts[0] or not parts[1]:
                    continue

                name, ip = parts[0], parts[1]
                self.peers[ip] = name
                if ip == self.my_ip:
                    self.my_name = name

        for ip, name in self.peers.items():
            self._add_contact_button(ip, name)

    def _add_contact_button(self, ip, name):
        if ip in self.contact_buttons:
            return

        if ip == self.my_ip:
            btn = ctk.CTkButton(self.sidebar, text=f"{name} (Me)\n{ip}", corner_radius=0, height=45,
                                fg_color=COLORS["ME_BTN"], text_color="#777777", font=("Consolas", 11),
                                state="disabled")
        else:
            btn = ctk.CTkButton(self.sidebar, text=f"{name}\n{ip}", corner_radius=0, height=45,
                                fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                                text_color=COLORS["TEXT_WHITE"], font=("Consolas", 11),
                                command=lambda i=ip, n=name: self.select_peer(i, n))

        btn.pack(fill="x", pady=2, padx=2)
        self.contact_buttons[ip] = btn

    def select_peer(self, ip, name):
        if self.is_calling:
            return

        for btn_ip, btn in self.contact_buttons.items():
            if btn_ip != self.my_ip:
                btn.configure(fg_color=COLORS["BTN_BASE"], text_color=COLORS["TEXT_WHITE"])

        self.active_peer_ip = ip
        self.active_peer_name = name

        # An unknown caller has no button yet; add one so the UI stays in sync.
        if ip not in self.contact_buttons:
            self.peers.setdefault(ip, name)
            self._add_contact_button(ip, name)

        self.contact_buttons[ip].configure(fg_color=COLORS["ACCENT"], text_color=COLORS["BG_MAIN"])
        self.call_btn.configure(text=f"START CALL WITH {name.upper()}")

    def toggle_call(self):
        if not self.active_peer_ip or self.active_peer_ip == self.my_ip:
            return

        if not self.is_calling:
            self._start_call()
        else:
            self.end_call()

    def _start_call(self):
        self._rescan_devices()
        self._drain_audio_queue()
        self._set_mic_status(None)
        self.is_calling = True
        self.call_btn.configure(text="END CALL", fg_color=COLORS["BTN_QUIT_HOVER"], text_color=COLORS["TEXT_WHITE"], hover_color="#CC0000")
        threading.Thread(target=self._transmit_audio, daemon=True).start()

    def end_call(self, notify=True):
        if notify and self.is_calling and self.active_peer_ip:
            self._send_ctrl(b"END")

        self.is_calling = False
        self._drain_audio_queue()
        self._set_mic_status(None)
        btn_text = f"START CALL WITH {self.active_peer_name.upper()}" if self.active_peer_name else "START CALL"
        self.call_btn.configure(text=btn_text, fg_color=COLORS["BTN_BASE"], text_color=COLORS["TEXT_WHITE"], hover_color=COLORS["ACCENT"])

    def _send_ctrl(self, payload):
        if not self.active_peer_ip:
            return
        try:
            self.ctrl_sock.sendto(payload, (self.active_peer_ip, CTRL_PORT))
        except OSError:
            pass

    def _drain_audio_queue(self):
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                return

    def _control_listener(self):
        while self.running_event.is_set():
            try:
                data, addr = self.ctrl_sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                # Socket closed on shutdown, or a transient error: back off
                # instead of spinning at 100% CPU.
                if not self.running_event.is_set():
                    return
                time.sleep(0.1)
                continue

            try:
                msg = data.decode("utf-8", errors="replace")
            except Exception:
                continue

            ip = addr[0]
            if msg.startswith("CALL:"):
                # split(":", 1) keeps names that contain a colon, and the
                # default args freeze this packet's values for the callback.
                caller_name = msg.split(":", 1)[1].strip() or ip
                self.root.after(0, lambda i=ip, n=caller_name: self.handle_incoming_call(i, n))
            elif msg == "END":
                self.root.after(0, lambda: self.end_call(notify=False))

    def handle_incoming_call(self, ip, caller_name):
        if self.is_calling:
            return
        # Prefer the name from the contact list over the one on the wire.
        self.select_peer(ip, self.peers.get(ip, caller_name))
        self._start_call()

    def _rescan_devices(self):
        # PortAudio only scans audio devices once, so a microphone plugged in
        # later is invisible until it is re-initialized. Re-initializing while
        # a stream is open corrupts that stream, so park the output stream
        # first and reopen it afterwards.
        self._rescan_request.set()
        try:
            deadline = time.monotonic() + 2.0
            while not self._output_idle.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
        finally:
            self._rescan_request.clear()

    def _set_mic_status(self, ok):
        if ok == self._mic_ok:
            return
        self._mic_ok = ok
        text = "" if ok is not False else "MIC NOT FOUND - RECONNECTING..."
        try:
            self.root.after(0, lambda t=text: self.mic_status_label.configure(text=t))
        except Exception:
            pass

    def _transmit_audio(self):
        # Reopened in a loop so unplugging the microphone mid-call does not
        # silently kill capture: the stream errors out, the devices are
        # rescanned, and transmission resumes when a microphone is back.
        while self.is_calling and self.running_event.is_set():
            try:
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32') as stream:
                    self._set_mic_status(True)
                    while self.is_calling and self.running_event.is_set() and self.active_peer_ip:
                        data, _ = stream.read(CHUNK)
                        try:
                            self.audio_sock.sendto(data.tobytes(), (self.active_peer_ip, AUDIO_PORT))
                        except OSError:
                            return
            except Exception:
                self._set_mic_status(False)
                if not (self.is_calling and self.running_event.is_set()):
                    return
                self._rescan_devices()
                time.sleep(0.5)

    def _audio_receiver(self):
        # Receiving happens here rather than inside the playback callback:
        # blocking socket I/O in a realtime audio callback causes dropouts.
        while self.running_event.is_set():
            try:
                data, addr = self.audio_sock.recvfrom(CHUNK * 4 + 64)
            except socket.timeout:
                continue
            except OSError:
                if not self.running_event.is_set():
                    return
                time.sleep(0.1)
                continue

            if self.is_calling and addr[0] == self.active_peer_ip:
                try:
                    self._audio_queue.put_nowait(data)
                except queue.Full:
                    pass

    def _audio_playback(self):
        def audio_callback(outdata, frames, time_info, status):
            if not self.is_calling:
                outdata.fill(0)
                return
            try:
                raw = self._audio_queue.get_nowait()
            except queue.Empty:
                outdata.fill(0)
                return

            audio_chunk = np.frombuffer(raw, dtype='float32')
            # Guard the reshape: a truncated datagram would otherwise raise
            # inside the callback.
            if audio_chunk.size == frames * CHANNELS:
                outdata[:] = audio_chunk.reshape(-1, CHANNELS)
            else:
                outdata.fill(0)

        # The output stream is opened per call (not once at startup), so each
        # call picks up the current default output device after a rescan. It is
        # also released whenever a rescan is pending, then reopened.
        while self.running_event.is_set():
            if not self.is_calling or self._rescan_request.is_set():
                self._output_idle.set()
                time.sleep(0.1)
                continue

            self._output_idle.clear()
            try:
                with sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32',
                                     blocksize=CHUNK, callback=audio_callback):
                    while (self.is_calling and self.running_event.is_set()
                           and not self._rescan_request.is_set()):
                        time.sleep(0.05)
            except Exception:
                self._output_idle.set()
                time.sleep(0.5)

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
        help_win.geometry("300x250")
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
            "* Select a target from the list.\n"
            "* Click START CALL to open audio stream.\n"
            "* Toggle ALWAYS ON TOP to keep window above.\n"
            "* Toggle GHOST MODE for transparency.\n"
            "* Press F4 anywhere to hide/show window.\n"
            "* Mic may be plugged/unplugged during a call;\n"
            "  capture resumes on its own.\n"
            "* Edit 'ip_list.txt' to change contacts.\n"
        )
        txt.insert("1.0", help_content)
        txt.configure(state="disabled")

    def on_close(self):
        self.running_event.clear()
        self.is_calling = False
        for sock in (self.ctrl_sock, self.audio_sock):
            try:
                sock.close()
            except OSError:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    root = ctk.CTk()
    app = LiteIntercomApp(root)
    root.mainloop()
