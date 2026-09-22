import os
import queue
import subprocess
import sys
import threading
import tkinter as tk

TARGET = "8.8.8.8"

BG_BAR = "#121212"
BG_PANEL = "#1e1e1e"
BG_CTRL = "#333333"
FG = "#ffffff"
FG_MUTED = "#888888"
ACCENT = "#ff6600"
DANGER = "#ff0000"


class PingApp:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.lines = queue.Queue()

        root.title("Ping 8.8.8.8")
        root.configure(bg=BG_BAR)
        root.geometry("640x420")
        root.minsize(420, 260)
        self.icon = tk.PhotoImage(width=1, height=1)
        root.iconphoto(True, self.icon)

        bar = tk.Frame(root, bg=BG_BAR)
        bar.pack(fill="x", padx=8, pady=8)

        tk.Label(bar, text="Ping " + TARGET, bg=BG_BAR, fg=ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(side="left")

        self.quit_btn = self.make_button(bar, "Quit", self.quit, DANGER)
        self.quit_btn.pack(side="right", padx=(6, 0))
        self.stop_btn = self.make_button(bar, "Stop", self.stop, FG)
        self.stop_btn.pack(side="right", padx=(6, 0))
        self.start_btn = self.make_button(bar, "Start", self.start, FG)
        self.start_btn.pack(side="right")

        panel = tk.Frame(root, bg=BG_BAR)
        panel.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.output = tk.Text(panel, bg=BG_PANEL, fg=FG, insertbackground=FG,
                              relief="flat", bd=0, highlightthickness=1,
                              highlightbackground=BG_BAR, highlightcolor=ACCENT,
                              font=("Consolas", 10), wrap="none", state="disabled")
        scroll = tk.Scrollbar(panel, command=self.output.yview, bg=BG_CTRL,
                              troughcolor=BG_BAR, relief="flat", bd=0,
                              highlightthickness=0, activebackground=BG_CTRL)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)

        self.status = tk.Label(root, text="Stopped", bg=BG_BAR, fg=FG_MUTED,
                               anchor="w")
        self.status.pack(fill="x", padx=8, pady=(0, 6))

        self.update_buttons()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(100, self.poll)

    def make_button(self, parent, text, command, fg):
        return tk.Button(parent, text=text, command=command, bg=BG_CTRL, fg=fg,
                         activebackground=BG_CTRL, activeforeground=ACCENT,
                         disabledforeground=FG_MUTED, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=BG_BAR,
                         padx=14, pady=4, cursor="hand2")

    def update_buttons(self):
        running = self.proc is not None
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def start(self):
        if self.proc is not None:
            return
        if os.name == "nt":
            cmd = ["ping", "-t", TARGET]
            flags = subprocess.CREATE_NO_WINDOW
        else:
            cmd = ["ping", TARGET]
            flags = 0
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True,
                                         errors="replace", bufsize=1,
                                         creationflags=flags)
        except OSError as e:
            self.append("Error: " + str(e) + "\n")
            return
        self.status.configure(text="Running", fg=ACCENT)
        self.update_buttons()
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc):
        for line in proc.stdout:
            self.lines.put(line)
        proc.wait()
        self.lines.put(None)

    def poll(self):
        try:
            while True:
                line = self.lines.get_nowait()
                if line is None:
                    self.proc = None
                    self.status.configure(text="Stopped", fg=FG_MUTED)
                    self.update_buttons()
                elif line.strip():
                    self.append(line)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def append(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def quit(self):
        self.stop()
        self.root.destroy()


def dark_title_bar(root):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


if __name__ == "__main__":
    root = tk.Tk()
    PingApp(root)
    dark_title_bar(root)
    root.mainloop()
