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


STATE_COLORS = {"STANDBY": TEXT_COLOR, "ACTIVE": ACCENT_COLOR, "BROADCAST": BROADCAST_COLOR}


class IntercomClient:
    def __init__(self, root):
        self.root = root
        self.root.geometry("300x150")
        self.root.configure(fg_color=BG_COLOR)
        self.root.overrideredirect(True)

        try:
            keyboard.add_hotkey("f4", self.toggle_visibility)
        except Exception:
            pass

        self.manager_ip = self.get_manager_ip()
        self.state = "STANDBY"
        self.peers = set()
        self.running = True

        self.audio = AudioEngine(self.tx_targets, self.rx_accept)

        self.build_topbar()
        self.add_watermark()
        self.build_ui()

        threading.Thread(target=self.ctrl_loop, daemon=True).start()
        self.audio.start()

    def get_manager_ip(self):
        if not os.path.exists("manager.txt"):
            with open("manager.txt", "w") as f:
                f.write("192.168.1.100\n")
        try:
            with open("manager.txt", "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        return line
        except OSError:
            pass
        return "192.168.1.100"

    def toggle_visibility(self):
        if self.root.winfo_viewable():
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            self.root.after(100, lambda: self.root.attributes("-topmost", False))

    def build_topbar(self):
        topbar = ctk.CTkFrame(self.root, height=30, fg_color=BG_COLOR, corner_radius=0)
        topbar.pack(side="top", fill="x")

        def start_move(e):
            self._dx = e.x
            self._dy = e.y

        def do_move(e):
            self.root.geometry(f"+{self.root.winfo_x() + e.x - self._dx}+{self.root.winfo_y() + e.y - self._dy}")

        topbar.bind("<ButtonPress-1>", start_move)
        topbar.bind("<B1-Motion>", do_move)

        ctk.CTkButton(topbar, text="Quit", width=50, height=25, fg_color=QUIT_COLOR, text_color=TEXT_COLOR, corner_radius=0, command=self.on_quit).pack(side="right", padx=5, pady=2)
        ctk.CTkButton(topbar, text="Help", width=50, height=25, fg_color=BTN_COLOR, text_color=TEXT_COLOR, corner_radius=0, command=self.on_help).pack(side="right", padx=5, pady=2)

    def add_watermark(self):
        ctk.CTkLabel(self.root, text="oT", font=("Arial", 10), text_color="#888888").place(relx=0.99, rely=0.99, anchor="se")

    def build_ui(self):
        self.status_lbl = ctk.CTkLabel(self.root, text="STANDBY", font=("Arial", 22, "bold"), text_color=TEXT_COLOR)
        self.status_lbl.pack(expand=True)

    def update_status(self, text, color):
        self.root.after(0, lambda: self.status_lbl.configure(text=text, text_color=color))

    def apply_state(self, state, peers):
        if state not in STATE_COLORS:
            state = "STANDBY"
        self.peers = set(peers)
        if state != self.state:
            self.state = state
            self.audio.clear()
            self.update_status(state, STATE_COLORS[state])

    def ctrl_loop(self):
        while self.running:
            conn = None
            try:
                conn = socket.create_connection((self.manager_ip, CTRL_PORT), timeout=5)
                conn.settimeout(None)
                stream = conn.makefile("r")
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    self.apply_state(msg.get("state", "STANDBY"), msg.get("peers", []))
            except OSError:
                pass
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
            self.apply_state("STANDBY", [])
            if self.running:
                time.sleep(2.0)

    def tx_targets(self):
        if self.state == "ACTIVE":
            return list(self.peers)
        return []

    def rx_accept(self, ip):
        if self.state == "BROADCAST":
            return True
        return self.state == "ACTIVE" and ip in self.peers

    def on_quit(self):
        self.running = False
        self.audio.stop()
        self.root.destroy()
        os._exit(0)

    def on_help(self):
        pass


if __name__ == "__main__":
    root = ctk.CTk()
    app = IntercomClient(root)
    root.mainloop()
