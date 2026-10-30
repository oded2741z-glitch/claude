import json
import queue
import socket
import sys
import threading
import time
import tkinter as tk

PORT = 50555
BG = "#121212"
PANEL = "#1e1e1e"
CTRL = "#333333"
FG = "#ffffff"
MUTED = "#888888"
ACCENT = "#ff6600"


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def fmt(seconds):
    cs = int(seconds * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"
    return f"{m:02d}:{s:02d}.{c:02d}"


def send(conn, obj):
    try:
        conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))
    except OSError:
        pass


def dark_title_bar(root):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


class Watch:
    def __init__(self):
        self.running = False
        self.base = 0.0
        self.started = 0.0

    def elapsed(self):
        if self.running:
            return self.base + time.monotonic() - self.started
        return self.base

    def start(self):
        if not self.running:
            self.started = time.monotonic()
            self.running = True

    def stop(self):
        if self.running:
            self.base = self.elapsed()
            self.running = False

    def reset(self):
        self.running = False
        self.base = 0.0

    def snapshot(self):
        return {"running": self.running, "elapsed": self.elapsed()}

    def load(self, data):
        self.base = float(data["elapsed"])
        self.running = bool(data["running"])
        self.started = time.monotonic()


class App:
    def __init__(self, root):
        self.root = root
        self.watch = Watch()
        self.events = queue.Queue()
        self.mode = None
        self.server_sock = None
        self.clients = []
        self.conn = None
        self.host_ip = ""

        root.title("Stopwatch")
        root.configure(bg=BG)
        root.minsize(420, 280)
        self.icon = tk.PhotoImage(width=16, height=16)
        root.iconphoto(True, self.icon)

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(header, text="Stopwatch", bg=BG, fg=ACCENT, font=("Segoe UI", 14, "bold")).pack(side="left")

        self.conn_row = tk.Frame(root, bg=BG)
        self.conn_row.pack(fill="x", padx=12, pady=(0, 6))
        self.ip_entry = tk.Entry(
            self.conn_row, bg=CTRL, fg=FG, insertbackground=FG, relief="flat",
            highlightthickness=1, highlightbackground=BG, highlightcolor=ACCENT,
        )
        self.ip_entry.insert(0, "192.168.1.")
        self.ip_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.button(self.conn_row, "Join", self.join).pack(side="left", padx=(6, 0))
        self.button(self.conn_row, "Host", self.host).pack(side="left", padx=(6, 0))

        self.panel = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=BG)
        self.panel.pack(fill="both", expand=True, padx=12)
        self.time_label = tk.Label(self.panel, text=fmt(0), bg=PANEL, fg=FG, font=("Consolas", 48))
        self.time_label.pack(expand=True)

        controls = tk.Frame(root, bg=BG)
        controls.pack(fill="x", padx=12, pady=10)
        for i, (text, cmd) in enumerate((("Start", "start"), ("Stop", "stop"), ("Reset", "reset"))):
            controls.columnconfigure(i, weight=1)
            self.button(controls, text, lambda c=cmd: self.action(c)).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0), ipady=6
            )

        self.status = tk.Label(root, text="Not connected", bg=BG, fg=MUTED, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 8))

        root.protocol("WM_DELETE_WINDOW", self.close)
        dark_title_bar(root)
        self.poll()

    def button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, bg=CTRL, fg=FG,
            activebackground=CTRL, activeforeground=ACCENT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BG, padx=14, pady=4,
        )

    def host(self):
        if self.mode:
            return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", PORT))
            s.listen()
        except OSError as e:
            self.status.config(text=f"Cannot host: {e}")
            return
        self.server_sock = s
        self.mode = "host"
        self.host_ip = local_ip()
        self.conn_row.pack_forget()
        self.update_host_status()
        threading.Thread(target=self.accept_loop, daemon=True).start()

    def accept_loop(self):
        while True:
            try:
                conn, _ = self.server_sock.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.events.put(("join", conn, None))
            threading.Thread(target=self.read_loop, args=(conn,), daemon=True).start()

    def join(self):
        if self.mode:
            return
        ip = self.ip_entry.get().strip()
        if not ip:
            return
        self.status.config(text=f"Connecting to {ip}...")
        threading.Thread(target=self.connect, args=(ip,), daemon=True).start()

    def connect(self, ip):
        try:
            conn = socket.create_connection((ip, PORT), timeout=3)
            conn.settimeout(None)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            self.events.put(("failed", None, str(e)))
            return
        self.events.put(("connected", conn, ip))
        self.read_loop(conn)

    def read_loop(self, conn):
        try:
            with conn.makefile("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        self.events.put(("msg", conn, json.loads(line)))
                    except ValueError:
                        pass
        except OSError:
            pass
        self.events.put(("lost", conn, None))

    def update_host_status(self):
        self.status.config(text=f"Hosting on {self.host_ip}  |  {len(self.clients)} connected")

    def action(self, cmd):
        if self.mode == "client":
            send(self.conn, {"cmd": cmd})
        else:
            self.apply(cmd)

    def apply(self, cmd):
        if cmd == "start":
            self.watch.start()
        elif cmd == "stop":
            self.watch.stop()
        elif cmd == "reset":
            self.watch.reset()
        else:
            return
        snap = self.watch.snapshot()
        for c in list(self.clients):
            send(c, snap)

    def poll(self):
        while True:
            try:
                kind, conn, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "join":
                self.clients.append(conn)
                send(conn, self.watch.snapshot())
                self.update_host_status()
            elif kind == "connected":
                if self.mode:
                    conn.close()
                    continue
                self.mode = "client"
                self.conn = conn
                self.conn_row.pack_forget()
                self.status.config(text=f"Connected to {data}")
            elif kind == "failed":
                if not self.mode:
                    self.status.config(text=f"Connection failed: {data}")
            elif kind == "msg":
                if self.mode == "host" and conn in self.clients and isinstance(data, dict):
                    self.apply(data.get("cmd"))
                elif self.mode == "client" and conn is self.conn:
                    try:
                        self.watch.load(data)
                    except (KeyError, TypeError, ValueError):
                        pass
            elif kind == "lost":
                if conn in self.clients:
                    self.clients.remove(conn)
                    conn.close()
                    self.update_host_status()
                elif conn is self.conn:
                    conn.close()
                    self.conn = None
                    self.mode = None
                    self.conn_row.pack(fill="x", padx=12, pady=(0, 6), before=self.panel)
                    self.status.config(text="Disconnected")
        self.time_label.config(text=fmt(self.watch.elapsed()))
        self.root.after(30, self.poll)

    def close(self):
        for c in self.clients + ([self.conn] if self.conn else []):
            try:
                c.close()
            except OSError:
                pass
        if self.server_sock:
            try:
                self.server_sock.close()
            except OSError:
                pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
