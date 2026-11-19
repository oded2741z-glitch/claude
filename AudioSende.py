import tkinter as tk
from tkinter import ttk
import threading
import socket
import struct
import os
import time
import json
import pyaudiowpatch as pyaudio
import pystray
from PIL import Image, ImageDraw
import keyboard

# --- Constants & Colors ---
BG_COLOR = "#121212"
ACCENT_COLOR = "#389379"
BTN_BG = "#333333"
TEXT_COLOR = "#FFFFFF"
QUIT_COLOR = "#ff3333"
FONT_MAIN = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI", 12, "bold")

# --- Audio Settings ---
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
SAMPLE_BYTES = 2
PORT = 5000
CMD_PORT = 5001
TIMEOUT = 2.0
RECV_BUFFER = 65536
HEADER = struct.Struct("!I")

class AudioStreamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Streamer")
        self.root.geometry("450x170")
        self.root.overrideredirect(True)
        self.root.configure(bg=BG_COLOR)
        self.center_window()

        self.is_sending = False
        self.is_requesting = False
        self.requested_ip = None
        self.send_generation = 0
        self.sender_thread = None
        self.send_lock = threading.Lock()
        self.audio = pyaudio.PyAudio()

        self.settings_file = "settings.json"
        self.settings = self.load_settings()
        self.sender_only = bool(self.settings.get("sender_only", False))

        self.setup_ui()
        self.setup_tray()

        # Global Hotkeys using the 'keyboard' module
        keyboard.add_hotkey('f4', self.safe_toggle_window)
        keyboard.add_hotkey('f2', self.safe_toggle_request)

        self.is_hidden = False

        if self.settings.get("start_in_tray", False):
            self.root.withdraw()
            self.is_hidden = True

        # Threads
        self.receiver_thread = threading.Thread(target=self.auto_receiver_task, daemon=True)
        self.receiver_thread.start()

        self.cmd_thread = threading.Thread(target=self.control_listener_task, daemon=True)
        self.cmd_thread.start()

    def safe_toggle_window(self):
        self.root.after(0, self.toggle_window)

    def safe_toggle_request(self):
        if self.sender_only:
            return # Disabled in Sender Only mode
        self.root.after(0, self.toggle_request)

    def load_settings(self):
        default_settings = {"start_in_tray": False, "sender_only": False}
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    default_settings.update(data)
            except Exception:
                pass
        return default_settings

    def save_settings(self):
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump({
                    "start_in_tray": self.tray_var.get(),
                    "sender_only": self.sender_only_var.get()
                }, f)
        except Exception as e:
            self.log(f"Error saving settings: {e}")

    def on_sender_only_toggle(self):
        self.sender_only = self.sender_only_var.get()
        self.save_settings()
        # If active request is running, stop it when switching to Sender Only
        if self.sender_only and self.is_requesting:
            self.send_stop_request()
        self.update_request_buttons(self.is_requesting)

    def center_window(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def toggle_window(self, event=None):
        if self.is_hidden:
            self.root.deiconify()
            self.root.attributes('-topmost', True)
            self.root.after(100, lambda: self.root.attributes('-topmost', False))
            self.is_hidden = False
        else:
            self.root.withdraw()
            self.is_hidden = True

    def load_ips_from_file(self):
        filename = "ips.txt"
        name_to_ip = {}

        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if '-' in line:
                        parts = line.split('-', 1)
                    elif ',' in line:
                        parts = line.split(',', 1)
                    else:
                        parts = [line]

                    ip = parts[0].strip()
                    if len(parts) > 1 and parts[1].strip():
                        tag = parts[1].strip()
                        name_to_ip[tag] = ip
                    else:
                        name_to_ip[ip] = ip

        if not name_to_ip:
            name_to_ip = {"Localhost": "127.0.0.1"}
            with open(filename, "w", encoding="utf-8") as f:
                f.write("127.0.0.1 - Localhost\n")

        return name_to_ip

    def setup_ui(self):
        self.main_frame = tk.Frame(self.root, bg=BG_COLOR, highlightbackground=ACCENT_COLOR, highlightthickness=1)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.title_bar = tk.Frame(self.main_frame, bg=BG_COLOR)
        self.title_bar.pack(fill=tk.X, pady=2, padx=5)
        self.title_bar.bind("<ButtonPress-1>", self.start_move)
        self.title_bar.bind("<B1-Motion>", self.on_move)

        self.title_label = tk.Label(self.title_bar, text="Audio Streamer", bg=BG_COLOR, fg=ACCENT_COLOR, font=FONT_TITLE)
        self.title_label.pack(side=tk.LEFT, padx=10)
        self.title_label.bind("<ButtonPress-1>", self.start_move)
        self.title_label.bind("<B1-Motion>", self.on_move)

        self.quit_btn = tk.Button(self.title_bar, text="Quit", bg=QUIT_COLOR, fg=TEXT_COLOR, font=FONT_MAIN, relief=tk.FLAT, bd=0, command=self.quit_app)
        self.quit_btn.pack(side=tk.RIGHT, padx=5)

        self.help_btn = tk.Button(self.title_bar, text="Help", bg=BTN_BG, fg=TEXT_COLOR, font=FONT_MAIN, relief=tk.FLAT, bd=0, command=self.show_help)
        self.help_btn.pack(side=tk.RIGHT, padx=5)

        self.controls_frame = tk.Frame(self.main_frame, bg=BG_COLOR)
        self.controls_frame.pack(fill=tk.X, pady=5, padx=20)

        tk.Label(self.controls_frame, text="Target Device:", bg=BG_COLOR, fg=TEXT_COLOR, font=FONT_MAIN).grid(row=0, column=0, sticky=tk.W, pady=2)

        self.name_to_ip = self.load_ips_from_file()
        display_names = list(self.name_to_ip.keys())

        self.ip_var = tk.StringVar()
        if display_names:
            self.ip_var.set(display_names[0])

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Dark.TCombobox", fieldbackground=BTN_BG, background=BTN_BG, foreground=TEXT_COLOR, borderwidth=0, arrowcolor=ACCENT_COLOR)
        style.map('Dark.TCombobox', fieldbackground=[('readonly', BTN_BG)], selectbackground=[('readonly', ACCENT_COLOR)], selectforeground=[('readonly', TEXT_COLOR)])

        self.ip_combobox = ttk.Combobox(self.controls_frame, textvariable=self.ip_var, values=display_names, font=FONT_MAIN, style="Dark.TCombobox")
        self.ip_combobox.grid(row=0, column=1, padx=10, pady=2, sticky=tk.EW)

        # Checkboxes Frame
        self.chk_frame = tk.Frame(self.controls_frame, bg=BG_COLOR)
        self.chk_frame.grid(row=1, column=1, sticky=tk.W, padx=6, pady=2)

        self.tray_var = tk.BooleanVar(value=self.settings.get("start_in_tray", False))
        self.chk_tray = tk.Checkbutton(
            self.chk_frame, text="Start in Tray", variable=self.tray_var,
            bg=BG_COLOR, fg=TEXT_COLOR, selectcolor=BTN_BG, activebackground=BG_COLOR, activeforeground=TEXT_COLOR,
            command=self.save_settings
        )
        self.chk_tray.pack(side=tk.LEFT, padx=(0, 10))

        self.sender_only_var = tk.BooleanVar(value=self.sender_only)
        self.chk_sender = tk.Checkbutton(
            self.chk_frame, text="Sender Only", variable=self.sender_only_var,
            bg=BG_COLOR, fg=TEXT_COLOR, selectcolor=BTN_BG, activebackground=BG_COLOR, activeforeground=TEXT_COLOR,
            command=self.on_sender_only_toggle
        )
        self.chk_sender.pack(side=tk.LEFT)

        self.actions_frame = tk.Frame(self.main_frame, bg=BG_COLOR)
        self.actions_frame.pack(fill=tk.X, pady=10, padx=20)

        self.btn_request = tk.Button(self.actions_frame, text="Request Audio", bg=BTN_BG, fg=TEXT_COLOR, font=FONT_MAIN, relief=tk.FLAT, command=self.send_start_request)
        self.btn_request.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)

        self.btn_stop_req = tk.Button(self.actions_frame, text="Stop Audio", bg=BTN_BG, fg=TEXT_COLOR, font=FONT_MAIN, relief=tk.FLAT, state=tk.DISABLED, command=self.send_stop_request)
        self.btn_stop_req.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)

        self.signature = tk.Label(self.main_frame, text="oT", bg=BG_COLOR, fg=ACCENT_COLOR, font=("Segoe UI", 9, "bold"))
        self.signature.pack(side=tk.BOTTOM, anchor=tk.SE, padx=5, pady=2)

        # Apply initial state for buttons
        self.update_request_buttons(self.is_requesting)

    # --- System Tray Logic ---
    def create_tray_icon_image(self):
        image = Image.new('RGB', (64, 64), color=BG_COLOR)
        dc = ImageDraw.Draw(image)
        dc.rectangle((16, 16, 48, 48), fill=ACCENT_COLOR)
        return image

    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show/Hide", self.on_tray_toggle, default=True),
            pystray.MenuItem("Quit", self.on_tray_quit)
        )
        self.icon = pystray.Icon("AudioStreamer", self.create_tray_icon_image(), "Audio Streamer", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def on_tray_toggle(self, icon, item):
        self.safe_toggle_window()

    def on_tray_quit(self, icon, item):
        self.root.after(0, self.quit_app)

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def on_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")

    def log(self, message):
        print(message)

    def show_help(self):
        help_window = tk.Toplevel(self.root)
        help_window.title("Help")
        help_window.geometry("300x190")
        help_window.overrideredirect(True)
        help_window.configure(bg=BG_COLOR)

        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 95
        help_window.geometry(f"+{x}+{y}")

        frame = tk.Frame(help_window, bg=BG_COLOR, highlightbackground=ACCENT_COLOR, highlightthickness=1)
        frame.pack(fill=tk.BOTH, expand=True)

        close_btn = tk.Button(frame, text="Quit", bg=QUIT_COLOR, fg=TEXT_COLOR, font=FONT_MAIN, relief=tk.FLAT, command=help_window.destroy)
        close_btn.pack(anchor=tk.NE, padx=5, pady=5)

        info = "Audio Streamer Usage:\n\nSelect a device from the dropdown.\nClick 'Request Audio' or press F2 (Global) to pull stream.\nCheck 'Sender Only' to disable requesting.\nF4 (Global) toggles window visibility."
        tk.Label(frame, text=info, bg=BG_COLOR, fg=TEXT_COLOR, font=FONT_MAIN, justify=tk.LEFT, wraplength=280).pack(pady=10, padx=10)

    def quit_app(self):
        self.is_sending = False
        try:
            keyboard.unhook_all()
        except:
            pass
        if hasattr(self, 'icon'):
            self.icon.stop()
        os._exit(0)

    def update_request_buttons(self, is_requesting):
        if self.sender_only:
            self.btn_request.config(state=tk.DISABLED)
            self.btn_stop_req.config(state=tk.DISABLED)
        else:
            state_req = tk.DISABLED if is_requesting else tk.NORMAL
            state_stop = tk.NORMAL if is_requesting else tk.DISABLED
            self.btn_request.config(state=state_req)
            self.btn_stop_req.config(state=state_stop)

    # --- ACTIVE CLIENT (Requester) LOGIC ---
    def send_start_request(self, event=None):
        if self.sender_only:
            return

        selected_name = self.ip_var.get().strip()
        if not selected_name:
            self.log("Error: Target Device is required.")
            return
        target_ip = self.name_to_ip.get(selected_name, selected_name)

        self.log(f"Requesting audio from {target_ip}...")
        self.send_command(target_ip, "START")
        self.requested_ip = target_ip
        self.is_requesting = True
        self.update_request_buttons(True)

    def send_stop_request(self, event=None):
        target_ip = self.requested_ip
        if not target_ip:
            selected_name = self.ip_var.get().strip()
            target_ip = self.name_to_ip.get(selected_name, selected_name)

        self.log(f"Sending stop command to {target_ip}...")
        self.send_command(target_ip, "STOP")
        self.requested_ip = None
        self.is_requesting = False
        self.update_request_buttons(False)

    def toggle_request(self, event=None):
        if self.sender_only:
            return

        if self.is_requesting:
            self.send_stop_request()
        else:
            self.send_start_request()

    def send_command(self, ip, cmd):
        try:
            cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            cmd_socket.sendto(cmd.encode('utf-8'), (ip, CMD_PORT))
            cmd_socket.close()
        except Exception as e:
            self.log(f"Command Error: {e}")

    # --- PASSIVE SERVER (Control Listener) LOGIC ---
    def control_listener_task(self):
        cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            cmd_socket.bind(('0.0.0.0', CMD_PORT))
        except Exception as e:
            self.log(f"Could not bind command port {CMD_PORT}.")
            return

        self.log(f"Listening for commands on port {CMD_PORT}...")
        while True:
            try:
                data, addr = cmd_socket.recvfrom(1024)
                command = data.decode('utf-8').strip()
                requester_ip = addr[0]

                if command == "START":
                    self.log(f"Received START command from {requester_ip}")
                    self.start_sending_to(requester_ip)
                elif command == "STOP":
                    self.log(f"Received STOP command from {requester_ip}")
                    self.stop_sending()
            except Exception as e:
                self.log(f"Control Listener Error: {e}")

    def start_sending_to(self, target_ip):
        with self.send_lock:
            self.send_generation += 1
            generation = self.send_generation
            previous_thread = self.sender_thread
            self.is_sending = True
            self.sender_thread = threading.Thread(target=self.sender_task, args=(target_ip, generation, previous_thread), daemon=True)
            self.sender_thread.start()

    def stop_sending(self):
        with self.send_lock:
            self.send_generation += 1
            self.is_sending = False
        self.log("Streaming stopped by remote command.")

    def is_current_sender(self, generation):
        return self.is_sending and generation == self.send_generation

    def to_stereo(self, data, channels):
        if channels == CHANNELS:
            return data
        if channels == 1:
            return b''.join(data[i:i + SAMPLE_BYTES] * 2 for i in range(0, len(data), SAMPLE_BYTES))
        frame_size = channels * SAMPLE_BYTES
        stereo_size = CHANNELS * SAMPLE_BYTES
        return b''.join(data[i:i + stereo_size] for i in range(0, len(data), frame_size))

    # --- SENDER LOGIC (Triggered remotely) ---
    def sender_task(self, target_ip, generation, previous_thread):
        if previous_thread and previous_thread.is_alive():
            previous_thread.join(timeout=TIMEOUT)
        if not self.is_current_sender(generation):
            return

        self.log(f"Streaming audio to {target_ip}...")
        stream = None
        udp_socket = None
        try:
            try:
                wasapi_info = self.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            except OSError:
                self.log("Error: WASAPI is not supported.")
                return

            default_speakers = self.audio.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            loopback_device = None

            if not default_speakers["isLoopbackDevice"]:
                for loopback in self.audio.get_loopback_device_info_generator():
                    if default_speakers["name"] in loopback["name"]:
                        loopback_device = loopback
                        break
            else:
                loopback_device = default_speakers

            if not loopback_device:
                self.log("Error: Could not find system loopback.")
                return

            channels = int(loopback_device["maxInputChannels"])
            rate = int(loopback_device["defaultSampleRate"])
            header = HEADER.pack(rate)

            stream = self.audio.open(
                format=FORMAT,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=CHUNK
            )

            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            while self.is_current_sender(generation):
                data = stream.read(CHUNK, exception_on_overflow=False)
                udp_socket.sendto(header + self.to_stereo(data, channels), (target_ip, PORT))

        except Exception as e:
            self.log(f"Sender Error: {str(e)}")
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if udp_socket:
                udp_socket.close()
            with self.send_lock:
                if generation == self.send_generation:
                    self.is_sending = False
            self.log("Stream task ended.")

    # --- AUTO RECEIVER LOGIC ---
    def close_playback(self, stream):
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def auto_receiver_task(self):
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_socket.bind(('0.0.0.0', PORT))
        except Exception as e:
            self.log("Could not bind receiver port.")
            return

        udp_socket.settimeout(0.5)

        stream = None
        current_rate = None
        last_receive_time = 0
        is_playing = False

        while True:
            # If Sender Only mode is active, completely ignore incoming audio streams
            if self.sender_only:
                if is_playing:
                    self.close_playback(stream)
                    stream = None
                    current_rate = None
                    is_playing = False
                try:
                    udp_socket.recvfrom(RECV_BUFFER) # Drain buffer
                except OSError:
                    pass
                continue

            try:
                packet, addr = udp_socket.recvfrom(RECV_BUFFER)
                if len(packet) <= HEADER.size:
                    continue
                rate = HEADER.unpack_from(packet)[0]
                data = packet[HEADER.size:]
                current_time = time.time()

                if is_playing and rate != current_rate:
                    self.close_playback(stream)
                    stream = None
                    is_playing = False

                if not is_playing:
                    self.log("Incoming audio detected. Playing...")
                    try:
                        stream = self.audio.open(format=FORMAT, channels=CHANNELS, rate=rate, output=True, frames_per_buffer=CHUNK)
                        current_rate = rate
                        is_playing = True
                    except Exception as e:
                        self.log("Error opening playback stream.")

                if stream:
                    stream.write(data)

                last_receive_time = current_time

            except socket.timeout:
                if is_playing and (time.time() - last_receive_time > TIMEOUT):
                    self.log("Stream ended. Returning to standby.")
                    self.close_playback(stream)
                    stream = None
                    current_rate = None
                    is_playing = False
            except Exception as e:
                if "10054" not in str(e):
                    pass

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioStreamerApp(root)
    root.mainloop()
