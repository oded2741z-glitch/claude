import json
import os
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
MODES = ("stopwatch", "countdown")
ADDR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "addresses.txt")


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def load_addresses():
    result = {}
    try:
        with open(ADDR_FILE, encoding="utf-8") as f:
            for line in f:
                name, sep, ip = line.partition("=")
                if sep and name.strip() and ip.strip():
                    result[name.strip()] = ip.strip()
    except OSError:
        pass
    return result


def save_addresses(addresses):
    with open(ADDR_FILE, "w", encoding="utf-8") as f:
        for name, ip in addresses.items():
            f.write(f"{name}={ip}\n")


def fmt(seconds):
    cs = int(seconds * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"
    return f"{m:02d}:{s:02d}.{c:02d}"


def fmt_duration(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def parse_duration(text):
    parts = text.strip().split(":")
    try:
        if len(parts) == 1:
            m, s = int(parts[0]), 0
        elif len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
        else:
            return None
    except ValueError:
        return None
    if m < 0 or s < 0 or s > 59 or m * 60 + s == 0:
        return None
    return float(m * 60 + s)


def send(conn, obj):
    try:
        conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))
    except OSError:
        pass


def beep(root):
    if sys.platform == "win32":
        try:
            import winsound
        except ImportError:
            root.bell()
            return

        def run():
            for _ in range(3):
                winsound.Beep(1000, 300)
                time.sleep(0.15)

        threading.Thread(target=run, daemon=True).start()
    else:
        root.bell()


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

    def load(self, elapsed, running):
        self.base = float(elapsed)
        self.running = bool(running)
        self.started = time.monotonic()


class App:
    def __init__(self, root):
        self.root = root
        self.watch = Watch()
        self.timer_mode = "stopwatch"
        self.duration = 300.0
        self.alarmed = False
        self.events = queue.Queue()
        self.mode = None
        self.server_sock = None
        self.clients = []
        self.conn = None
        self.host_ip = ""
        self.addresses = load_addresses()

        root.title("Stopwatch")
        root.configure(bg=BG)
        root.minsize(380, 0)
        self.icon = tk.PhotoImage(width=16, height=16)
        root.iconphoto(True, self.icon)

        self.panel = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=BG)
        self.panel.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.time_label = tk.Label(self.panel, text=fmt(0), bg=PANEL, fg=FG, font=("Consolas", 36))
        self.time_label.pack(expand=True, pady=12)

        controls = tk.Frame(root, bg=BG)
        controls.pack(fill="x", padx=8, pady=6)
        for i, (text, cmd) in enumerate((("Start", "start"), ("Stop", "stop"), ("Reset", "reset"))):
            controls.columnconfigure(i, weight=1)
            self.button(controls, text, lambda c=cmd: self.press(c)).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0), ipady=4
            )

        self.options = tk.Frame(root, bg=BG)
        self.options.pack(fill="x", padx=8)
        self.mode_row = tk.Frame(self.options, bg=BG)
        self.mode_row.pack(fill="x", pady=(0, 6))
        self.mode_buttons = {}
        for i, name in enumerate(MODES):
            b = self.button(self.mode_row, name.capitalize(), lambda n=name: self.action("mode", n))
            b.pack(side="left", padx=(0 if i == 0 else 4, 0))
            self.mode_buttons[name] = b
        self.duration_entry = self.entry(self.mode_row, width=7, justify="center")
        self.duration_entry.insert(0, fmt_duration(self.duration))
        self.duration_entry.bind("<Return>", lambda e: self.commit_duration())
        self.duration_entry.bind("<FocusOut>", lambda e: self.commit_duration())

        self.conn_row = tk.Frame(self.options, bg=BG)
        self.conn_row.pack(fill="x", pady=(0, 6))
        ip_row = tk.Frame(self.conn_row, bg=BG)
        ip_row.pack(fill="x")
        self.address_var = tk.StringVar(value="Saved")
        self.address_menu = tk.OptionMenu(ip_row, self.address_var, "")
        self.address_menu.config(
            bg=CTRL, fg=FG, activebackground=CTRL, activeforeground=ACCENT,
            disabledforeground=MUTED, relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BG, width=10,
        )
        self.address_menu["menu"].config(
            bg=CTRL, fg=FG, activebackground=ACCENT, activeforeground=FG, relief="flat", bd=0
        )
        self.address_menu.pack(side="left", padx=(0, 4))
        self.refresh_addresses()
        self.ip_entry = self.entry(ip_row, width=14)
        self.ip_entry.insert(0, "192.168.1.")
        self.ip_entry.pack(side="left", fill="x", expand=True, ipady=4)
        btn_row = tk.Frame(self.conn_row, bg=BG)
        btn_row.pack(fill="x", pady=(4, 0))
        for i, (text, command) in enumerate((("Save", self.save_address), ("Join", self.join), ("Host", self.host))):
            btn_row.columnconfigure(i, weight=1)
            self.button(btn_row, text, command).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))

        self.status_row = tk.Frame(root, bg=BG)
        self.status_row.pack(fill="x", padx=8, pady=(0, 6))
        self.status = tk.Label(self.status_row, text="Not connected", bg=BG, fg=MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.options_button = self.button(self.status_row, "", self.toggle_options)
        self.options_button.config(pady=0)
        self.options_button.pack(side="right")
        self.set_options(True)

        self.refresh_controls()
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        root.bind_all("<Control-c>", lambda e: self.close())
        root.bind_all("<Control-C>", lambda e: self.close())
        root.bind_all("<F2>", lambda e: self.toggle_options())
        dark_title_bar(root)
        self.poll()

    def button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, bg=CTRL, fg=FG,
            activebackground=CTRL, activeforeground=ACCENT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BG, padx=10, pady=4,
        )

    def entry(self, parent, **kw):
        return tk.Entry(
            parent, bg=CTRL, fg=FG, insertbackground=FG, relief="flat",
            highlightthickness=1, highlightbackground=BG, highlightcolor=ACCENT, **kw
        )

    def set_options(self, visible):
        self.options_visible = visible
        if visible:
            self.options.pack(fill="x", padx=8, before=self.status_row)
        else:
            self.options.pack_forget()
        self.options_button.config(text="Options \u25b4" if visible else "Options \u25be")

    def toggle_options(self):
        self.set_options(not self.options_visible)

    def refresh_addresses(self):
        menu = self.address_menu["menu"]
        menu.delete(0, "end")
        for name in self.addresses:
            menu.add_command(label=name, command=lambda n=name: self.pick_address(n))
        self.address_menu.config(state="normal" if self.addresses else "disabled")

    def pick_address(self, name):
        self.address_var.set(name)
        self.ip_entry.delete(0, "end")
        self.ip_entry.insert(0, self.addresses[name])

    def save_address(self):
        ip = self.ip_entry.get().strip()
        if not ip:
            return
        current = next((n for n, a in self.addresses.items() if a == ip), "")
        dialog = tk.Toplevel(self.root)
        dialog.title("Save Address")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        tk.Label(dialog, text=f"Name for {ip}", bg=BG, fg=FG).pack(anchor="w", padx=12, pady=(10, 4))
        name_entry = self.entry(dialog, width=28)
        name_entry.insert(0, current)
        name_entry.pack(fill="x", padx=12, ipady=4)
        row = tk.Frame(dialog, bg=BG)
        row.pack(fill="x", padx=12, pady=10)

        def ok():
            name = name_entry.get().strip().replace("=", "")
            if not name:
                name_entry.focus_set()
                return
            updated = dict(self.addresses)
            updated[name] = ip
            try:
                save_addresses(updated)
            except OSError as e:
                self.status.config(text=f"Cannot save: {e}")
                dialog.destroy()
                return
            self.addresses = updated
            self.refresh_addresses()
            self.address_var.set(name)
            dialog.destroy()

        self.button(row, "Cancel", dialog.destroy).pack(side="right")
        self.button(row, "Save", ok).pack(side="right", padx=(0, 6))
        name_entry.bind("<Return>", lambda e: ok())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dark_title_bar(dialog)
        dialog.grab_set()
        name_entry.focus_set()

    def refresh_controls(self):
        for name, b in self.mode_buttons.items():
            b.config(fg=ACCENT if name == self.timer_mode else FG)
        if self.timer_mode == "countdown":
            self.duration_entry.pack(side="right", ipady=4)
        else:
            self.duration_entry.pack_forget()
        if self.root.focus_get() is not self.duration_entry:
            self.duration_entry.delete(0, "end")
            self.duration_entry.insert(0, fmt_duration(self.duration))

    def commit_duration(self):
        value = parse_duration(self.duration_entry.get())
        if value is None:
            self.duration_entry.delete(0, "end")
            self.duration_entry.insert(0, fmt_duration(self.duration))
            return
        if value != self.duration:
            self.action("duration", value)

    def press(self, cmd):
        if cmd == "start" and self.timer_mode == "countdown":
            self.commit_duration()
        self.action(cmd)

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
        self.set_options(False)
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

    def snapshot(self):
        return {
            "running": self.watch.running,
            "elapsed": self.watch.elapsed(),
            "mode": self.timer_mode,
            "duration": self.duration,
        }

    def load(self, data):
        if data["mode"] not in MODES:
            return
        duration = float(data["duration"])
        self.watch.load(data["elapsed"], data["running"])
        self.timer_mode = data["mode"]
        self.duration = duration
        self.refresh_controls()

    def action(self, cmd, value=None):
        if self.mode == "client":
            send(self.conn, {"cmd": cmd, "value": value})
        else:
            self.apply(cmd, value)

    def apply(self, cmd, value=None):
        if cmd == "start":
            if self.timer_mode == "countdown" and self.watch.elapsed() >= self.duration:
                return
            self.watch.start()
        elif cmd == "stop":
            self.watch.stop()
        elif cmd == "reset":
            self.watch.reset()
        elif cmd == "mode":
            if value not in MODES or value == self.timer_mode:
                return
            self.watch.reset()
            self.timer_mode = value
        elif cmd == "duration":
            try:
                value = float(value)
            except (TypeError, ValueError):
                return
            if value <= 0 or self.watch.running or self.watch.elapsed() > 0:
                self.refresh_controls()
                return
            self.duration = value
        else:
            return
        self.refresh_controls()
        snap = self.snapshot()
        for c in list(self.clients):
            send(c, snap)

    def update_display(self):
        if self.timer_mode == "countdown":
            remaining = max(0.0, self.duration - self.watch.elapsed())
            finished = self.watch.running and remaining <= 0
            self.time_label.config(text=fmt(remaining))
        else:
            finished = False
            self.time_label.config(text=fmt(self.watch.elapsed()))
        if finished and not self.alarmed:
            beep(self.root)
        self.alarmed = finished
        color = ACCENT if finished and int(time.monotonic() * 2) % 2 == 0 else PANEL
        self.panel.config(bg=color)
        self.time_label.config(bg=color)

    def poll(self):
        while True:
            try:
                kind, conn, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "join":
                self.clients.append(conn)
                send(conn, self.snapshot())
                self.update_host_status()
            elif kind == "connected":
                if self.mode:
                    conn.close()
                    continue
                self.mode = "client"
                self.conn = conn
                self.conn_row.pack_forget()
                self.set_options(False)
                self.status.config(text=f"Connected to {data}")
            elif kind == "failed":
                if not self.mode:
                    self.status.config(text=f"Connection failed: {data}")
            elif kind == "msg":
                if self.mode == "host" and conn in self.clients and isinstance(data, dict):
                    self.apply(data.get("cmd"), data.get("value"))
                elif self.mode == "client" and conn is self.conn:
                    try:
                        self.load(data)
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
                    self.conn_row.pack(fill="x", pady=(0, 6))
                    self.set_options(True)
                    self.status.config(text="Disconnected")
        self.update_display()
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
