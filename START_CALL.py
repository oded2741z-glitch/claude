import customtkinter as ctk
import socket
import threading
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

        self.my_ip = socket.gethostbyname(socket.gethostname())
        self.my_name = "Me"
        self.running_event = threading.Event()
        self.running_event.set()
        
        self.always_on_top_var = ctk.BooleanVar(value=False)
        self.ghost_mode_var = ctk.BooleanVar(value=False)

        self.active_peer_ip = None
        self.active_peer_name = None
        self.audio_peer_ip = None
        self.audio_send_target = None
        self.is_calling = False
        self.peers = {}
        self.contact_buttons = {}

        # One shared UDP socket for both sending and receiving audio, bound to
        # AUDIO_PORT. Sending from the same port we listen on lets return audio
        # traverse NAT and stateful firewalls (they only open a return path for
        # the port that originated the traffic).
        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.audio_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.audio_sock.bind(("0.0.0.0", AUDIO_PORT))
        self.audio_sock.settimeout(0.1)

        self._setup_ui()
        self._load_contacts()

        threading.Thread(target=self._control_listener, daemon=True).start()
        threading.Thread(target=self._audio_listener, daemon=True).start()

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
            with open(IP_LIST_FILE, "w") as f:
                f.write("Local_Test, 127.0.0.1\n")

        with open(IP_LIST_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CONFIG: TITLE"):
                    try:
                        new_title = line.split(",")[1].strip()
                        self.title_label.configure(text=new_title)
                    except IndexError:
                        pass
                
                elif line.strip() and not line.startswith(("#", "CONFIG")):
                    parts = [x.strip() for x in line.split(",")]
                    name, ip = parts[0], parts[1]
                    self.peers[ip] = name
                    if ip == self.my_ip: self.my_name = name

        for ip, name in self.peers.items():
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
        if self.is_calling: return
        
        for btn_ip, btn in self.contact_buttons.items():
            if btn_ip != self.my_ip:
                btn.configure(fg_color=COLORS["BTN_BASE"], text_color=COLORS["TEXT_WHITE"])
            
        self.active_peer_ip = ip
        self.active_peer_name = name
        # An incoming call may come from someone who is not in ip_list.txt, so
        # there is no button to highlight. That must not abort the call setup.
        if ip in self.contact_buttons:
            self.contact_buttons[ip].configure(fg_color=COLORS["ACCENT"], text_color=COLORS["BG_MAIN"])
        self.call_btn.configure(text=f"START CALL WITH {name.upper()}")

    def toggle_call(self):
        if not self.active_peer_ip or self.active_peer_ip == self.my_ip:
            return

        if not self.is_calling:
            self._refresh_audio_devices()
            self.audio_peer_ip = None
            self.audio_send_target = (self.active_peer_ip, AUDIO_PORT)
            self.is_calling = True
            self.call_btn.configure(text="END CALL", fg_color=COLORS["BTN_QUIT_HOVER"], text_color=COLORS["TEXT_WHITE"], hover_color="#CC0000")

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(f"CALL:{self.my_name}".encode(), (self.active_peer_ip, CTRL_PORT))
            s.close()
            
            threading.Thread(target=self._transmit_audio, daemon=True).start()
        else:
            self.end_call()

    def end_call(self):
        if self.is_calling and self.active_peer_ip:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(b"END", (self.active_peer_ip, CTRL_PORT))
            s.close()
            
        self.is_calling = False
        self.audio_send_target = None
        btn_text = f"START CALL WITH {self.active_peer_name.upper()}" if self.active_peer_name else "START CALL"
        self.call_btn.configure(text=btn_text, fg_color=COLORS["BTN_BASE"], text_color=COLORS["TEXT_WHITE"], hover_color=COLORS["ACCENT"])

    def _control_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", CTRL_PORT))
        while self.running_event.is_set():
            try:
                data, addr = s.recvfrom(1024)
                msg = data.decode()
                ip = addr[0]
                
                if msg.startswith("CALL:"):
                    caller_name = msg.split(":")[1]
                    self.root.after(0, lambda: self.handle_incoming_call(ip, caller_name))
                elif msg == "END":
                    self.root.after(0, self.end_call)
            except: pass

    def handle_incoming_call(self, ip, caller_name):
        if not self.is_calling:
            self.select_peer(ip, caller_name)
            self._refresh_audio_devices()
            self.audio_peer_ip = None
            self.audio_send_target = (self.active_peer_ip, AUDIO_PORT)
            self.is_calling = True
            self.call_btn.configure(text="END CALL", fg_color=COLORS["BTN_QUIT_HOVER"], text_color=COLORS["TEXT_WHITE"], hover_color="#CC0000")
            threading.Thread(target=self._transmit_audio, daemon=True).start()

    def _refresh_audio_devices(self):
        # PortAudio only scans audio devices once, so devices plugged in after
        # startup are invisible until it is re-initialized. Safe to call only
        # while no stream is open (i.e. before a call starts).
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass

    def _transmit_audio(self):
        # Send from the shared audio socket (bound to AUDIO_PORT) so the return
        # path is opened on that port. self.audio_send_target is updated to the
        # peer's real source address once their audio arrives (NAT-safe).

        # Punch the return path open before touching the microphone: opening the
        # input stream takes time and can fail outright (device busy), and until
        # something goes out of AUDIO_PORT the firewall drops the peer's audio
        # as unsolicited - which is exactly what makes a call go one-way.
        target = self.audio_send_target
        if target:
            for _ in range(3):
                try:
                    self.audio_sock.sendto(b"\x00" * 4, target)
                except Exception:
                    break

        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32') as stream:
                while self.is_calling and self.running_event.is_set():
                    data, _ = stream.read(CHUNK)
                    target = self.audio_send_target
                    if target:
                        try:
                            self.audio_sock.sendto(data.tobytes(), target)
                        except Exception:
                            pass
        except Exception:
            pass

    def _audio_listener(self):
        def audio_callback(outdata, frames, time, status):
            try:
                data, addr = self.audio_sock.recvfrom(4096)
                if not self.is_calling:
                    outdata.fill(0)
                    return
                # Priming packets carry no audio, but their source address is
                # still worth learning (see below).
                is_priming = len(data) <= 4
                # Learn the peer's real source address from the first audio
                # packet of the call, then accept audio only from that same
                # source. This works even through NAT, where the source
                # address differs from the one listed in ip_list.txt.
                if self.audio_peer_ip is None:
                    self.audio_peer_ip = addr[0]
                if addr[0] == self.audio_peer_ip:
                    # Reply to the exact address+port the audio came from so
                    # return audio traverses the peer's NAT / firewall.
                    self.audio_send_target = addr
                    if is_priming:
                        outdata.fill(0)
                        return
                    audio_chunk = np.frombuffer(data, dtype='float32')
                    n = min(len(audio_chunk), frames)
                    outdata[:n, 0] = audio_chunk[:n]
                    if n < frames:
                        outdata[n:, 0] = 0
                else:
                    outdata.fill(0)
            except:
                outdata.fill(0)

        # The output stream is opened per call (not once at startup), so each
        # call picks up the current default output device after the refresh.
        while self.running_event.is_set():
            if not self.is_calling:
                sd.sleep(100)
                continue
            try:
                with sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32', blocksize=CHUNK, callback=audio_callback):
                    while self.is_calling and self.running_event.is_set():
                        sd.sleep(100)
            except Exception:
                while self.is_calling and self.running_event.is_set():
                    sd.sleep(100)

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
            "* Edit 'ip_list.txt' to change contacts.\n"
        )
        txt.insert("1.0", help_content)
        txt.configure(state="disabled")

    def on_close(self):
        self.running_event.clear()
        self.root.destroy()
        os._exit(0)

if __name__ == "__main__":
    root = ctk.CTk()
    app = LiteIntercomApp(root)
    root.mainloop()