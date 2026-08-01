import customtkinter as ctk
import socket
import threading
import queue
import time
import contextlib
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
RESCAN_WAIT = 2.0
RETRY_DELAY = 0.5
# A live output stream calls back every CHUNK/SAMPLE_RATE (~23 ms). Going this
# long without one means the device is gone, whatever the backend reports.
OUT_STALL_TIMEOUT = 1.0


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
        self.active_peer_allowed_ips = []
        self.is_calling = False
        self.peers = {}
        self.contact_buttons = {}

        # Learned NAT endpoints: the address a peer's packets actually came
        # from, which behind a router is not the configured ip:port.
        self.dynamic_endpoints = {"ctrl": {}, "audio": {}}

        self._audio_queue = queue.Queue(maxsize=10)

        # Audio device gate. A PortAudio rescan may only run while no stream is
        # open, so streams are counted and a pending rescan makes both the
        # capture and the playback side release theirs first.
        self._pa_gate = threading.Condition()
        self._open_streams = 0
        self._rescan_pending = False
        self._mic_ok = True
        self._out_ok = True
        self._last_out_callback = 0.0

        # Both sockets are bound and are also used for sending. That matters
        # for NAT: the peer learns our source port, so it has to be the port we
        # actually listen on, otherwise its replies land on a dead ephemeral
        # port and audio ends up one-way.
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

    # ---------------------------------------------------------------- UI ----

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

        self.device_status_label = ctk.CTkLabel(self.root, text="", font=("Consolas", 10),
                                                text_color=COLORS["BTN_QUIT_HOVER"], anchor="w")
        self.device_status_label.pack(side="bottom", fill="x", padx=10)

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

    # ---------------------------------------------------------- CONTACTS ----

    def _load_contacts(self):
        self.peers.clear()
        if not os.path.exists(IP_LIST_FILE):
            with open(IP_LIST_FILE, "w", encoding="utf-8") as f:
                f.write("# Name, send-to IP, [extra IPs this peer may arrive from]\n")
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

                name, send_ip = parts[0], parts[1]
                # Every address after the name counts as this peer: a contact
                # can be reachable at a LAN address and arrive from a WAN one.
                allowed_ips = [p for p in parts[1:] if p]
                self.peers[send_ip] = {"name": name, "allowed_ips": allowed_ips}
                if self.my_ip in allowed_ips:
                    self.my_name = name

        for send_ip in self.peers:
            self._add_contact_button(send_ip)

    def _is_me(self, send_ip):
        return self.my_ip in self.peers.get(send_ip, {}).get("allowed_ips", [])

    def _add_contact_button(self, send_ip):
        if send_ip in self.contact_buttons:
            return

        name = self.peers[send_ip]["name"]
        if self._is_me(send_ip):
            btn = ctk.CTkButton(self.sidebar, text=f"{name} (Me)\n{send_ip}", corner_radius=0, height=45,
                                fg_color=COLORS["ME_BTN"], text_color="#777777", font=("Consolas", 11),
                                state="disabled")
        else:
            btn = ctk.CTkButton(self.sidebar, text=f"{name}\n{send_ip}", corner_radius=0, height=45,
                                fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                                text_color=COLORS["TEXT_WHITE"], font=("Consolas", 11),
                                command=lambda i=send_ip: self.select_peer(i))

        btn.pack(fill="x", pady=2, padx=2)
        self.contact_buttons[send_ip] = btn

    def _match_peer(self, ip):
        # Map a source address back onto the contact it belongs to.
        for send_ip, p_data in self.peers.items():
            if ip in p_data["allowed_ips"]:
                return send_ip
        return ip

    def select_peer(self, send_ip, name=None):
        if self.is_calling:
            return

        # An unknown caller has no contact entry yet; add one so the UI and the
        # endpoint bookkeeping stay in sync.
        if send_ip not in self.peers:
            self.peers[send_ip] = {"name": name or send_ip, "allowed_ips": [send_ip]}
            self._add_contact_button(send_ip)

        for btn_ip, btn in self.contact_buttons.items():
            if not self._is_me(btn_ip):
                btn.configure(fg_color=COLORS["BTN_BASE"], text_color=COLORS["TEXT_WHITE"])

        self.active_peer_ip = send_ip
        self.active_peer_name = self.peers[send_ip]["name"]
        self.active_peer_allowed_ips = self.peers[send_ip]["allowed_ips"]

        self.contact_buttons[send_ip].configure(fg_color=COLORS["ACCENT"], text_color=COLORS["BG_MAIN"])
        self.call_btn.configure(text=f"START CALL WITH {self.active_peer_name.upper()}")

    # -------------------------------------------------------- NET / CALL ----

    def _resolve_target(self, kind):
        # Prefer the address the peer was last heard from, which is what makes
        # this work through a router; fall back to the configured one.
        default_port = CTRL_PORT if kind == "ctrl" else AUDIO_PORT
        return self.dynamic_endpoints[kind].get(self.active_peer_ip,
                                                (self.active_peer_ip, default_port))

    def _learn_endpoint(self, kind, addr):
        send_ip = self._match_peer(addr[0])
        self.dynamic_endpoints[kind][send_ip] = addr
        return send_ip

    def toggle_call(self):
        if not self.active_peer_ip or self._is_me(self.active_peer_ip):
            return

        if not self.is_calling:
            self._send_ctrl(f"CALL:{self.my_name}".encode("utf-8"))
            self._start_call()
        else:
            self.end_call()

    def _start_call(self):
        self._rescan_devices()
        self._drain_audio_queue()
        self._set_device_status(mic=True, out=True)
        self.is_calling = True
        self.call_btn.configure(text="END CALL", fg_color=COLORS["BTN_QUIT_HOVER"], text_color=COLORS["TEXT_WHITE"], hover_color="#CC0000")
        threading.Thread(target=self._transmit_audio, daemon=True).start()

    def end_call(self, notify=True):
        if notify and self.is_calling and self.active_peer_ip:
            self._send_ctrl(b"END")

        self.is_calling = False
        self._drain_audio_queue()
        self._set_device_status(mic=True, out=True)
        btn_text = f"START CALL WITH {self.active_peer_name.upper()}" if self.active_peer_name else "START CALL"
        self.call_btn.configure(text=btn_text, fg_color=COLORS["BTN_BASE"], text_color=COLORS["TEXT_WHITE"], hover_color=COLORS["ACCENT"])

    def _send_ctrl(self, payload):
        if not self.active_peer_ip:
            return
        try:
            self.ctrl_sock.sendto(payload, self._resolve_target("ctrl"))
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

            send_ip = self._learn_endpoint("ctrl", addr)
            if msg.startswith("CALL:"):
                # split(":", 1) keeps names that contain a colon, and the
                # default args freeze this packet's values for the callback.
                caller_name = msg.split(":", 1)[1].strip() or send_ip
                self.root.after(0, lambda i=send_ip, n=caller_name: self.handle_incoming_call(i, n))
            elif msg == "END":
                self.root.after(0, lambda: self.end_call(notify=False))

    def handle_incoming_call(self, send_ip, caller_name):
        if self.is_calling:
            return
        self.select_peer(send_ip, caller_name)
        self._start_call()

    # -------------------------------------------------------- AUDIO GATE ----

    @contextlib.contextmanager
    def _pa_stream_slot(self):
        # Held for the lifetime of an open PortAudio stream. A rescan waits for
        # every slot to be released before re-initializing.
        with self._pa_gate:
            while self._rescan_pending and self.running_event.is_set():
                self._pa_gate.wait(0.1)
            self._open_streams += 1
        try:
            yield
        finally:
            with self._pa_gate:
                self._open_streams -= 1
                self._pa_gate.notify_all()

    def _rescan_devices(self):
        # PortAudio only scans devices once, so a microphone or a pair of
        # headphones connected later stays invisible until it is
        # re-initialized -- which is only safe with no stream open.
        with self._pa_gate:
            if self._rescan_pending:
                # Another thread is already doing it; wait it out.
                deadline = time.monotonic() + RESCAN_WAIT
                while self._rescan_pending and time.monotonic() < deadline:
                    self._pa_gate.wait(0.1)
                return
            self._rescan_pending = True

        try:
            with self._pa_gate:
                deadline = time.monotonic() + RESCAN_WAIT
                while self._open_streams > 0 and time.monotonic() < deadline:
                    self._pa_gate.wait(0.1)
                streams_open = self._open_streams

            # Skip rather than tear PortAudio down under a live stream; the
            # caller retries shortly.
            if streams_open == 0:
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
        finally:
            with self._pa_gate:
                self._rescan_pending = False
                self._pa_gate.notify_all()

    def _set_device_status(self, mic=None, out=None):
        if mic is not None:
            self._mic_ok = mic
        if out is not None:
            self._out_ok = out

        if not self._mic_ok and not self._out_ok:
            text = "AUDIO DEVICES LOST - RECONNECTING..."
        elif not self._mic_ok:
            text = "MIC NOT FOUND - RECONNECTING..."
        elif not self._out_ok:
            text = "HEADPHONES LOST - RECONNECTING..."
        else:
            text = ""

        try:
            self.root.after(0, lambda t=text: self.device_status_label.configure(text=t))
        except Exception:
            pass

    # ------------------------------------------------------------- AUDIO ----

    def _transmit_audio(self):
        # Reopened in a loop so unplugging the microphone mid-call does not
        # silently kill capture: the stream errors out, devices are rescanned,
        # and transmission resumes once a microphone is back.
        while self.is_calling and self.running_event.is_set():
            try:
                with self._pa_stream_slot(), sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                                            dtype='float32') as stream:
                    self._set_device_status(mic=True)
                    while (self.is_calling and self.running_event.is_set()
                           and self.active_peer_ip and not self._rescan_pending):
                        if not stream.active:
                            raise sd.PortAudioError("input device lost")
                        data, _ = stream.read(CHUNK)
                        try:
                            self.audio_sock.sendto(data.tobytes(), self._resolve_target("audio"))
                        except OSError:
                            return
            except Exception:
                self._set_device_status(mic=False)
                if not (self.is_calling and self.running_event.is_set()):
                    return
                self._rescan_devices()
                time.sleep(RETRY_DELAY)

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

            self._learn_endpoint("audio", addr)
            if self.is_calling and addr[0] in self.active_peer_allowed_ips:
                try:
                    self._audio_queue.put_nowait(data)
                except queue.Full:
                    pass

    def _audio_playback(self):
        def audio_callback(outdata, frames, time_info, status):
            self._last_out_callback = time.monotonic()
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

        # Same treatment as capture: the output stream is reopened whenever it
        # dies, so headphones can be unplugged and reconnected mid-call.
        while self.running_event.is_set():
            if not self.is_calling:
                time.sleep(0.1)
                continue
            try:
                self._last_out_callback = time.monotonic()
                with self._pa_stream_slot(), sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                                             dtype='float32', blocksize=CHUNK,
                                                             callback=audio_callback) as stream:
                    self._set_device_status(out=True)
                    while (self.is_calling and self.running_event.is_set()
                           and not self._rescan_pending):
                        # Unplugged headphones do not always surface as an
                        # error or an inactive stream, so a stalled callback
                        # counts as a dead device too.
                        if not stream.active:
                            raise sd.PortAudioError("output device lost")
                        if time.monotonic() - self._last_out_callback > OUT_STALL_TIMEOUT:
                            raise sd.PortAudioError("output device stalled")
                        time.sleep(0.05)
            except Exception:
                self._set_device_status(out=False)
                if not (self.is_calling and self.running_event.is_set()):
                    continue
                self._drain_audio_queue()
                self._rescan_devices()
                time.sleep(RETRY_DELAY)

    # ------------------------------------------------------------- MISC -----

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
        help_win.geometry("320x290")
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
            "* Mic and headphones may be plugged or\n"
            "  unplugged during a call; audio resumes\n"
            "  on its own.\n"
            "* Replies follow the address a peer was last\n"
            "  heard from, so peers behind a router work.\n"
            "* 'ip_list.txt': Name, send-IP, extra-IPs...\n"
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
