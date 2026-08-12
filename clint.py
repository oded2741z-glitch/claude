import socket
import json
import threading
import time
import subprocess
import sys
import os
import tkinter as tk
from tkinter import scrolledtext, messagebox
import sounddevice as sd
import numpy as np

# --- Audio Settings ---
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 512
DTYPE = 'int16'

# --- Protocol ---
AUDIO_MAGIC = b'\x01'        # קידומת לכל חבילת אודיו
PUNCH_MSG = b'\x02PUNCH'     # חבילת ניקוב NAT
RECV_BUFFER = 65535          # UDP חותך חבילות גדולות מהבאפר בשקט - לכן מקסימום
PUNCH_DURATION = 2.0         # שניות של ניקוב לפני מעבר לאודיו
PUNCH_INTERVAL = 0.1
SIGNALLING_TIMEOUT = 15.0    # זמן מקסימלי להמתנה לעמית מהשרת
AUDIO_SOCK_TIMEOUT = 0.5     # מאפשר ל-RX להתעורר ולבדוק אם ביקשו לעצור


class IntercomGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("P2P NAT Intercom")
        self.root.geometry("460x480")
        self.root.resizable(False, False)

        # Connection variables
        self.sock = None
        self.is_running = False
        self.rx_thread = None
        self.tx_thread = None
        self._lock = threading.Lock()   # מגן על is_running מול לחיצות/תרדים מקבילים

        # Local server process variable
        self.server_process = None

        self._create_widgets()

        # Safe close protocol
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _create_widgets(self):
        # Local Server Frame
        server_frame = tk.LabelFrame(self.root, text=" Local Signalling Server ", padx=10, pady=8)
        server_frame.pack(fill="x", padx=15, pady=5)

        self.server_btn = tk.Button(server_frame, text="START LOCAL SERVER (server.py)",
                                    font=("Arial", 9, "bold"), bg="#5bc0de", fg="white",
                                    activebackground="#31b0d5", activeforeground="white",
                                    width=38, pady=5, command=self.toggle_local_server)
        self.server_btn.pack(pady=2)

        # Connection Settings Frame
        config_frame = tk.LabelFrame(self.root, text=" Connection Settings ", padx=10, pady=10)
        config_frame.pack(fill="x", padx=15, pady=5)

        tk.Label(config_frame, text="Server IP:").grid(row=0, column=0, sticky="w", pady=5)
        self.server_ip_entry = tk.Entry(config_frame, width=18)
        self.server_ip_entry.insert(0, "192.168.1.11")
        self.server_ip_entry.grid(row=0, column=1, pady=5, padx=5)

        tk.Label(config_frame, text="Port:").grid(row=0, column=2, sticky="w", pady=5)
        self.server_port_entry = tk.Entry(config_frame, width=7)
        self.server_port_entry.insert(0, "9999")
        self.server_port_entry.grid(row=0, column=3, pady=5, padx=5)

        tk.Label(config_frame, text="My ID:").grid(row=1, column=0, sticky="w", pady=5)
        self.my_id_entry = tk.Entry(config_frame, width=18)
        self.my_id_entry.insert(0, "node_A")
        self.my_id_entry.grid(row=1, column=1, pady=5, padx=5)

        # Connection Status
        self.status_label = tk.Label(self.root, text="STATUS: DISCONNECTED", font=("Arial", 10, "bold"), fg="gray")
        self.status_label.pack(pady=5)

        # Intercom Toggle Button
        self.toggle_btn = tk.Button(self.root, text="START INTERCOM", font=("Arial", 11, "bold"),
                                    bg="#389379", fg="white", activebackground="#2d7762", activeforeground="white",
                                    width=25, pady=8, command=self.toggle_intercom)
        self.toggle_btn.pack(pady=5)

        # Logs Window
        log_frame = tk.LabelFrame(self.root, text=" System Logs ", padx=5, pady=5)
        log_frame.pack(fill="both", expand=True, padx=15, pady=10)

        self.log_area = scrolledtext.ScrolledText(log_frame, height=8, font=("Consolas", 9), state="disabled")
        self.log_area.pack(fill="both", expand=True)

    def log(self, msg):
        """Thread-safe logging to the GUI text area"""
        def append():
            self.log_area.config(state="normal")
            self.log_area.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.log_area.see("end")
            self.log_area.config(state="disabled")
        self.root.after(0, append)

    def update_status(self, text, color):
        """Thread-safe status update"""
        def update():
            self.status_label.config(text=f"STATUS: {text}", fg=color)
        self.root.after(0, update)

    # --- Local Server Management ---
    def toggle_local_server(self):
        if self.server_process is None or self.server_process.poll() is not None:
            self.start_local_server()
        else:
            self.stop_local_server()

    def start_local_server(self):
        server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
        if not os.path.exists(server_path):
            messagebox.showerror("Error", f"Could not find 'server.py' in:\n{os.path.dirname(server_path)}")
            return

        try:
            self.server_process = subprocess.Popen([sys.executable, server_path],
                                                   stderr=subprocess.PIPE)
            self._set_server_btn_running(True)
            self.log("Local Signalling Server (server.py) STARTED.")
            # השרת עלול למות מיד (פורט תפוס) - בודקים אחרי חצי שנייה
            self.root.after(500, self._verify_server_alive)
        except Exception as e:
            self.server_process = None
            self.log(f"Failed to start server: {e}")

    def _verify_server_alive(self):
        proc = self.server_process
        if proc is None or proc.poll() is None:
            return

        err = b""
        try:
            err = proc.stderr.read() or b""
        except Exception:
            pass

        detail = err.decode('utf-8', 'replace').strip().splitlines()
        detail = detail[-1] if detail else f"exit code {proc.returncode}"
        self.log(f"Local Signalling Server FAILED: {detail}")
        self.server_process = None
        self._set_server_btn_running(False)

    def _set_server_btn_running(self, running):
        if running:
            self.server_btn.config(text="STOP LOCAL SERVER (server.py)",
                                   bg="#f0ad4e", activebackground="#ec971f")
        else:
            self.server_btn.config(text="START LOCAL SERVER (server.py)",
                                   bg="#5bc0de", activebackground="#31b0d5")

    def stop_local_server(self):
        proc = self.server_process
        if proc is None or proc.poll() is not None:
            self.server_process = None
            return

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # לא נענה ל-terminate - הורגים כדי לא להשאיר את הפורט תפוס
            proc.kill()
            proc.wait(timeout=2)

        self.server_process = None
        self._set_server_btn_running(False)
        self.log("Local Signalling Server STOPPED.")

    # --- Intercom Connection Management ---
    def toggle_intercom(self):
        if not self.is_running:
            self.start_connection()
        else:
            self.stop_connection()

    def start_connection(self):
        server_ip = self.server_ip_entry.get().strip()
        try:
            server_port = int(self.server_port_entry.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Port must be a number.")
            return

        my_id = self.my_id_entry.get().strip()
        if not my_id:
            messagebox.showerror("Error", "Please enter a valid ID.")
            return

        with self._lock:
            if self.is_running:   # מגן מפני לחיצה כפולה מהירה
                return
            self.is_running = True

        self.toggle_btn.config(text="DISCONNECT", bg="#d9534f", activebackground="#c9302c")
        self.server_ip_entry.config(state="disabled")
        self.server_port_entry.config(state="disabled")
        self.my_id_entry.config(state="disabled")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(AUDIO_SOCK_TIMEOUT)

        threading.Thread(target=self._connection_flow, args=(server_ip, server_port, my_id),
                         daemon=True).start()

    def _await_peer(self, server_ip, server_port, my_id):
        """מחכה לתשובת השרת. מתעלם מחבילות שאינן פרטי עמית (PUNCH מוקדם וכו')"""
        msg = json.dumps({"type": "register", "id": my_id}).encode('utf-8')
        self.sock.sendto(msg, (server_ip, server_port))
        last_register = time.time()
        deadline = last_register + SIGNALLING_TIMEOUT

        while self.is_running and time.time() < deadline:
            try:
                data, _ = self.sock.recvfrom(RECV_BUFFER)
            except socket.timeout:
                # רישום חוזר - הודעת הרישום עצמה עלולה ללכת לאיבוד ב-UDP
                if time.time() - last_register > 3.0:
                    self.sock.sendto(msg, (server_ip, server_port))
                    last_register = time.time()
                continue
            except ConnectionResetError:
                continue

            try:
                info = json.loads(data.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            if isinstance(info, dict) and "peer_ip" in info and "peer_port" in info:
                return (info["peer_ip"], int(info["peer_port"]))

        return None

    def _connection_flow(self, server_ip, server_port, my_id):
        try:
            self.update_status("REGISTERING...", "#f0ad4e")
            self.log(f"Registering as '{my_id}' on {server_ip}:{server_port}...")

            self.update_status("WAITING FOR PEER...", "#f0ad4e")
            self.log("Waiting for matching peer from server...")

            peer_addr = self._await_peer(server_ip, server_port, my_id)
            if peer_addr is None:
                if self.is_running:
                    self.log("Error: no peer assigned (signalling timed out).")
                    self.stop_connection()
                return

            self.log(f"Peer Target Assigned -> IP: {peer_addr[0]}, Port: {peer_addr[1]}")

            self.update_status("PUNCHING NAT...", "#0275d8")
            self.log("Sending UDP Hole Punch packets...")
            punch_until = time.time() + PUNCH_DURATION
            while self.is_running and time.time() < punch_until:
                self.sock.sendto(PUNCH_MSG, peer_addr)
                time.sleep(PUNCH_INTERVAL)

            if not self.is_running:
                return

            self.update_status("CONNECTED (P2P AUDIO)", "#5cb85c")
            self.log("NAT hole punched! Live audio streaming active.")

            self.rx_thread = threading.Thread(target=self._audio_receiver, daemon=True)
            self.tx_thread = threading.Thread(target=self._audio_sender, args=(peer_addr,), daemon=True)

            self.rx_thread.start()
            self.tx_thread.start()

        except Exception as e:
            if self.is_running:
                self.log(f"Connection error: {e}")
                self.stop_connection()

    def _audio_receiver(self):
        try:
            with sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE) as stream:
                while self.is_running:
                    try:
                        data, _ = self.sock.recvfrom(RECV_BUFFER)
                    except socket.timeout:
                        continue
                    except ConnectionResetError:
                        # Ignore WinError 10054 (temporary drop)
                        continue
                    except OSError:
                        break  # הסוקט נסגר - יציאה נקייה

                    # רק חבילות עם קידומת אודיו מנוגנות (PUNCH/JSON מסוננים)
                    payload = data[1:]
                    if data[:1] != AUDIO_MAGIC or not payload:
                        continue
                    if len(payload) % (2 * CHANNELS) != 0:
                        continue  # חבילה קטומה - מנגנים אותה יגרום לרעש/קריסה

                    audio_data = np.frombuffer(payload, dtype=np.int16)
                    stream.write(audio_data.reshape(-1, CHANNELS))
        except Exception as e:
            if self.is_running:
                self.log(f"Audio RX Error: {e}")

    def _audio_sender(self, peer_addr):
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                                blocksize=CHUNK_SIZE) as stream:
                while self.is_running:
                    data, _ = stream.read(CHUNK_SIZE)
                    try:
                        self.sock.sendto(AUDIO_MAGIC + data.tobytes(), peer_addr)
                    except ConnectionResetError:
                        continue
                    except OSError:
                        break  # הסוקט נסגר - יציאה נקייה
        except Exception as e:
            if self.is_running:
                self.log(f"Audio TX Error: {e}")

    def stop_connection(self):
        with self._lock:
            if not self.is_running:
                return
            self.is_running = False

        # קודם מחכים שהתרדים יסיימו, ורק אז סוגרים את הסוקט.
        # סגירה מתחת לרגליים של recvfrom/sendto משאירה את כרטיס הקול תקוע.
        current = threading.current_thread()
        for t in (self.rx_thread, self.tx_thread):
            if t is not None and t.is_alive() and t is not current:
                t.join(timeout=2.0)
        self.rx_thread = None
        self.tx_thread = None

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        def reset_gui():
            try:
                self.toggle_btn.config(text="START INTERCOM", bg="#389379", activebackground="#2d7762")
                self.server_ip_entry.config(state="normal")
                self.server_port_entry.config(state="normal")
                self.my_id_entry.config(state="normal")
                self.status_label.config(text="STATUS: DISCONNECTED", fg="gray")
                self.log("Intercom stopped.")
            except tk.TclError:
                pass  # החלון נסגר לפני שה-callback רץ

        self.root.after(0, reset_gui)

    def on_close(self):
        """Ensure clean exit by closing connections and local server"""
        self.stop_connection()
        self.stop_local_server()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = IntercomGUI(root)
    root.mainloop()
