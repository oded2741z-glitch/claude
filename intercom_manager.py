import customtkinter as ctk
import socket
import os
import keyboard
import sounddevice as sd
import numpy as np
import threading
import queue
import struct
import json
import random
import time

BG_COLOR = "#2E2E2E"
ACCENT_COLOR = "#389379"
TEXT_COLOR = "#FFFFFF"
BTN_COLOR = "#333333"
QUIT_COLOR = "#FF0000"
BROADCAST_COLOR = "#FF9900"

CTRL_PORT = 5001
AUDIO_PORT = 6000
SAMPLE_RATE = 16000
BLOCK = 320
CHANNELS = 1
DTYPE = "int16"
PKT_HEADER = struct.Struct("!II")
PKT_SIZE = PKT_HEADER.size + BLOCK * 2
JITTER_TARGET = 3
JITTER_MAX = 25
SENDER_TIMEOUT = 50


class JitterBuffer:
    def __init__(self):
        self.pending = {}
        self.next_seq = None
        self.idle = 0

    def push(self, seq, chunk):
        if self.next_seq is not None and seq < self.next_seq:
            return
        if len(self.pending) >= JITTER_MAX:
            self.pending.clear()
            self.next_seq = None
        self.pending[seq] = chunk

    def pop(self):
        if self.next_seq is None:
            if len(self.pending) < JITTER_TARGET:
                self.idle += 1
                return None
            self.next_seq = min(self.pending)
        chunk = self.pending.pop(self.next_seq, None)
        if chunk is None:
            if len(self.pending) > JITTER_TARGET * 2:
                self.next_seq = min(self.pending)
                chunk = self.pending.pop(self.next_seq)
            else:
                self.next_seq += 1
                self.idle += 1
                return None
        self.next_seq += 1
        self.idle = 0
        return chunk

    def dead(self):
        return self.idle > SENDER_TIMEOUT and not self.pending


class AudioEngine:
    def __init__(self, tx_targets, rx_accept):
        self.sender_id = random.getrandbits(32)
        self.tx_targets = tx_targets
        self.rx_accept = rx_accept
        self.seq = 0
        self.buffers = {}
        self.lock = threading.Lock()
        self.tx_queue = queue.Queue(maxsize=10)
        self.out_stream = None
        self.running = True

    def start(self):
        try:
            self.out_stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                              dtype=DTYPE, blocksize=BLOCK, callback=self._out_cb)
            self.out_stream.start()
        except Exception:
            self.out_stream = None
        threading.Thread(target=self._tx_supervisor, daemon=True).start()
        threading.Thread(target=self._tx_sender, daemon=True).start()
        threading.Thread(target=self._rx_loop, daemon=True).start()

    def clear(self):
        with self.lock:
            self.buffers.clear()

    def stop(self):
        self.running = False

    def _out_cb(self, outdata, frames, time_info, status):
        try:
            mixed = np.zeros(frames, dtype=np.int32)
            with self.lock:
                for sid in list(self.buffers.keys()):
                    buf = self.buffers[sid]
                    chunk = buf.pop()
                    if chunk is not None and len(chunk) == frames:
                        mixed += chunk
                    if buf.dead():
                        del self.buffers[sid]
            outdata[:, 0] = np.clip(mixed, -32768, 32767).astype(np.int16)
        except Exception:
            outdata.fill(0)

    def _in_cb(self, indata, frames, time_info, status):
        try:
            self.tx_queue.put_nowait(indata.copy().tobytes())
        except queue.Full:
            pass

    def _tx_supervisor(self):
        stream = None
        while self.running:
            want = bool(self.tx_targets())
            if want and stream is None:
                try:
                    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                            dtype=DTYPE, blocksize=BLOCK, callback=self._in_cb)
                    stream.start()
                except Exception:
                    stream = None
            elif not want and stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                stream = None
                while True:
                    try:
                        self.tx_queue.get_nowait()
                    except queue.Empty:
                        break
            time.sleep(0.1)

    def _tx_sender(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while self.running:
            try:
                payload = self.tx_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            targets = self.tx_targets()
            if not targets:
                continue
            packet = PKT_HEADER.pack(self.sender_id, self.seq) + payload
            self.seq += 1
            for ip in targets:
                try:
                    s.sendto(packet, (ip, AUDIO_PORT))
                except OSError:
                    pass
        s.close()

    def _rx_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", AUDIO_PORT))
        except OSError:
            return
        s.settimeout(0.5)
        while self.running:
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                continue
            if len(data) != PKT_SIZE:
                continue
            sid, seq = PKT_HEADER.unpack_from(data)
            if sid == self.sender_id:
                continue
            if not self.rx_accept(addr[0]):
                continue
            chunk = np.frombuffer(data, dtype=DTYPE, offset=PKT_HEADER.size)
            with self.lock:
                if sid not in self.buffers:
                    self.buffers[sid] = JitterBuffer()
                self.buffers[sid].push(seq, chunk)
        s.close()


class IntercomManager:
    def __init__(self, root):
        self.root = root
        self.root.configure(fg_color=BG_COLOR)
        self.root.overrideredirect(True)

        try:
            keyboard.add_hotkey("f4", self.toggle_visibility)
        except Exception:
            pass

        self.my_ip = self.get_local_ip()
        self.is_broadcasting = False
        self.groups = {}
        self.all_ips = set()
        self.active_groups = set()
        self.joined_group = None
        self.join_buttons = {}
        self.group_buttons = {}

        self.clients = {}
        self.clients_lock = threading.Lock()

        self.audio = AudioEngine(self.tx_targets, self.rx_accept)

        self.build_topbar()
        self.add_watermark()
        self.build_ui()

        threading.Thread(target=self.ctrl_server, daemon=True).start()
        self.audio.start()

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    def toggle_visibility(self):
        if self.root.winfo_viewable():
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            self.root.after(100, lambda: self.root.attributes("-topmost", False))

    def build_topbar(self):
        topbar = ctk.CTkFrame(self.root, height=40, fg_color=BG_COLOR, corner_radius=0)
        topbar.pack(side="top", fill="x")

        def start_move(e):
            self._dx = e.x
            self._dy = e.y

        def do_move(e):
            self.root.geometry(f"+{self.root.winfo_x() + e.x - self._dx}+{self.root.winfo_y() + e.y - self._dy}")

        topbar.bind("<ButtonPress-1>", start_move)
        topbar.bind("<B1-Motion>", do_move)

        ctk.CTkButton(topbar, text="Quit", width=60, fg_color=QUIT_COLOR, text_color=TEXT_COLOR, corner_radius=0, command=self.on_quit).pack(side="right", padx=5, pady=5)
        ctk.CTkButton(topbar, text="Help", width=60, fg_color=BTN_COLOR, text_color=TEXT_COLOR, corner_radius=0, command=self.on_help).pack(side="right", padx=5, pady=5)

        ctk.CTkLabel(topbar, text="Commander DASHBOARD", font=("Arial", 14, "bold"), text_color=ACCENT_COLOR).pack(side="left", padx=15)

    def add_watermark(self):
        ctk.CTkLabel(self.root, text="oT", font=("Arial", 10), text_color="#888888").place(relx=0.99, rely=0.99, anchor="se")

    def load_groups(self):
        if not os.path.exists("groups.txt"):
            with open("groups.txt", "w") as f:
                f.write("Team Alpha: 192.168.1.10, 192.168.1.11\n")
                f.write("Team Beta: 192.168.1.12, 192.168.1.13\n")
                f.write("Team Gamma: 192.168.1.14, 192.168.1.15\n")

        with open("groups.txt", "r") as f:
            lines = f.readlines()

        for line in lines:
            if ":" not in line:
                continue
            gname, ips = line.split(":", 1)
            gname = gname.strip()
            members = set(ip.strip() for ip in ips.split(",") if ip.strip())
            if not gname:
                continue
            self.groups[gname] = members
            self.all_ips |= members

    def build_ui(self):
        self.load_groups()

        win_height = 60 + (len(self.groups) * 60) + 90
        self.root.geometry(f"550x{win_height}")

        container = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        container.pack(fill="both", expand=True, padx=20, pady=10)

        broadcast_row = ctk.CTkFrame(container, fg_color=BG_COLOR, border_width=2, border_color=BROADCAST_COLOR, corner_radius=0)
        broadcast_row.pack(fill="x", pady=(0, 15))

        ctk.CTkLabel(broadcast_row, text="Commandar BROADCAST", font=("Arial", 14, "bold"), text_color=BROADCAST_COLOR).pack(side="left", padx=15, pady=12)

        self.btn_broadcast = ctk.CTkButton(broadcast_row, text="INACTIVE", width=100, fg_color=BTN_COLOR, text_color=TEXT_COLOR, corner_radius=0, command=self.toggle_broadcast)
        self.btn_broadcast.pack(side="right", padx=15, pady=12)

        for gname in self.groups:
            row = ctk.CTkFrame(container, fg_color=BG_COLOR, border_width=1, border_color=ACCENT_COLOR, corner_radius=0)
            row.pack(fill="x", pady=5)

            ctk.CTkLabel(row, text=gname, font=("Arial", 14), text_color=TEXT_COLOR).pack(side="left", padx=15, pady=12)

            btn_toggle = ctk.CTkButton(row, text="INACTIVE", width=100, fg_color=BTN_COLOR, text_color=TEXT_COLOR, corner_radius=0)
            btn_toggle.pack(side="right", padx=15, pady=12)
            btn_toggle.configure(command=lambda g=gname: self.toggle_group(g))

            btn_join = ctk.CTkButton(row, text="JOIN", width=100, fg_color=BTN_COLOR, text_color=TEXT_COLOR, corner_radius=0)
            btn_join.pack(side="right", padx=15, pady=12)
            btn_join.configure(command=lambda g=gname: self.toggle_join(g))

            self.group_buttons[gname] = btn_toggle
            self.join_buttons[gname] = btn_join

    def state_for(self, ip):
        if self.is_broadcasting:
            return "BROADCAST", []
        peers = set()
        active = False
        for gname in self.active_groups:
            members = self.groups.get(gname, set())
            if ip in members:
                active = True
                peers |= members - {ip}
        if self.joined_group and ip in self.groups.get(self.joined_group, set()):
            active = True
            peers.add(self.my_ip)
        return ("ACTIVE" if active else "STANDBY"), sorted(peers)

    def send_state(self, ip, conn):
        state, peers = self.state_for(ip)
        line = json.dumps({"state": state, "peers": peers}) + "\n"
        try:
            conn.sendall(line.encode())
        except OSError:
            self.drop_client(ip, conn)

    def push_state(self):
        with self.clients_lock:
            items = list(self.clients.items())
        for ip, conn in items:
            self.send_state(ip, conn)

    def close_conn(self, conn):
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass

    def drop_client(self, ip, conn):
        with self.clients_lock:
            if self.clients.get(ip) is conn:
                del self.clients[ip]
        self.close_conn(conn)

    def ctrl_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", CTRL_PORT))
            srv.listen(16)
        except OSError:
            return
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                continue
            ip = addr[0]
            with self.clients_lock:
                old = self.clients.get(ip)
                self.clients[ip] = conn
            if old is not None:
                self.close_conn(old)
            threading.Thread(target=self.client_reader, args=(ip, conn), daemon=True).start()
            self.send_state(ip, conn)

    def client_reader(self, ip, conn):
        try:
            while True:
                if not conn.recv(1024):
                    break
        except OSError:
            pass
        self.drop_client(ip, conn)

    def tx_targets(self):
        if self.is_broadcasting:
            return [ip for ip in self.all_ips if ip != self.my_ip]
        if self.joined_group:
            return [ip for ip in self.groups.get(self.joined_group, set()) if ip != self.my_ip]
        return []

    def rx_accept(self, ip):
        if self.is_broadcasting:
            return False
        if self.joined_group:
            return ip in self.groups.get(self.joined_group, set())
        return False

    def toggle_group(self, gname):
        if self.is_broadcasting:
            return
        btn = self.group_buttons[gname]
        if gname in self.active_groups:
            self.active_groups.discard(gname)
            btn.configure(text="INACTIVE", fg_color=BTN_COLOR, text_color=TEXT_COLOR)
        else:
            self.active_groups.add(gname)
            btn.configure(text="ACTIVE", fg_color=ACCENT_COLOR, text_color="#000000")
        self.push_state()

    def toggle_join(self, gname):
        if self.is_broadcasting:
            return
        if self.joined_group == gname:
            self.joined_group = None
        else:
            self.joined_group = gname
        for name, btn in self.join_buttons.items():
            if name == self.joined_group:
                btn.configure(text="LEAVE", fg_color=ACCENT_COLOR, text_color="#000000")
            else:
                btn.configure(text="JOIN", fg_color=BTN_COLOR, text_color=TEXT_COLOR)
        self.audio.clear()
        self.push_state()

    def toggle_broadcast(self):
        if not self.is_broadcasting:
            self.is_broadcasting = True
            self.btn_broadcast.configure(text="ACTIVE", fg_color=BROADCAST_COLOR, text_color="#000000")
            for btn in list(self.group_buttons.values()) + list(self.join_buttons.values()):
                btn.configure(state="disabled")
        else:
            self.is_broadcasting = False
            self.btn_broadcast.configure(text="INACTIVE", fg_color=BTN_COLOR, text_color=TEXT_COLOR)
            for btn in list(self.group_buttons.values()) + list(self.join_buttons.values()):
                btn.configure(state="normal")
        self.audio.clear()
        self.push_state()

    def on_quit(self):
        self.is_broadcasting = False
        self.active_groups.clear()
        self.joined_group = None
        self.push_state()
        time.sleep(0.2)
        self.root.destroy()
        os._exit(0)

    def on_help(self):
        pass


if __name__ == "__main__":
    root = ctk.CTk()
    app = IntercomManager(root)
    root.mainloop()
