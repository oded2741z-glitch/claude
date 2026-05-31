import tkinter as tk
from tkinter import messagebox
import threading

try:
    import smbclient
    from smbclient import register_session, reset_connection_cache
    SMB_AVAILABLE = True
    SMB_IMPORT_ERROR = ""
except Exception as _exc:
    SMB_AVAILABLE = False
    SMB_IMPORT_ERROR = str(_exc)


BG = "#121212"
ACCENT = "#FF6B00"
TEXT = "#FFFFFF"
BTN_BG = "#333333"
BTN_HOVER = "#444444"
QUIT_RED = "#A02020"
QUIT_HOVER = "#C03030"
FIELD_BG = "#1E1E1E"


class RemoteShareBrowser:
    def __init__(self, root):
        self.root = root
        self.server = ""
        self.share = ""
        self.username = ""
        self.password = ""
        self.current_path = ""
        self.connected = False
        self._drag_x = 0
        self._drag_y = 0
        self._build_ui()

    def _build_ui(self):
        self.root.title("Remote Share Browser")
        self.root.overrideredirect(True)
        self.root.geometry("900x600+200+100")
        self.root.configure(bg=ACCENT)

        outer = tk.Frame(self.root, bg=ACCENT)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        title_bar = tk.Frame(inner, bg=BG, height=40)
        title_bar.pack(fill="x", side="top")
        title_bar.pack_propagate(False)

        title = tk.Label(
            title_bar, text="Remote Share Browser", bg=BG, fg=ACCENT,
            font=("Segoe UI", 12, "bold")
        )
        title.pack(side="left", padx=12)

        for widget in (title, title_bar):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

        quit_btn = tk.Button(
            title_bar, text="Quit", command=self._quit,
            bg=QUIT_RED, fg=TEXT, relief="flat", bd=0,
            activebackground=QUIT_HOVER, activeforeground=TEXT,
            font=("Segoe UI", 9), width=8, cursor="hand2"
        )
        quit_btn.pack(side="right", padx=(0, 8), pady=6)

        help_btn = tk.Button(
            title_bar, text="Help", command=self._show_help,
            bg=BTN_BG, fg=TEXT, relief="flat", bd=0,
            activebackground=BTN_HOVER, activeforeground=TEXT,
            font=("Segoe UI", 9), width=8, cursor="hand2"
        )
        help_btn.pack(side="right", padx=4, pady=6)

        conn_frame = tk.Frame(inner, bg=BG)
        conn_frame.pack(fill="x", padx=12, pady=(4, 8))

        self._make_label(conn_frame, "Server:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.server_entry = self._make_entry(conn_frame, 20)
        self.server_entry.grid(row=0, column=1, padx=(0, 12))

        self._make_label(conn_frame, "Share:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.share_entry = self._make_entry(conn_frame, 15)
        self.share_entry.grid(row=0, column=3, padx=(0, 12))

        self._make_label(conn_frame, "User:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        self.user_entry = self._make_entry(conn_frame, 20)
        self.user_entry.grid(row=1, column=1, padx=(0, 12), pady=(6, 0))

        self._make_label(conn_frame, "Password:").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(6, 0))
        self.pass_entry = self._make_entry(conn_frame, 15, show="*")
        self.pass_entry.grid(row=1, column=3, padx=(0, 12), pady=(6, 0))

        self.connect_btn = tk.Button(
            conn_frame, text="Connect", command=self._connect,
            bg=BTN_BG, fg=TEXT, relief="flat", bd=0,
            activebackground=BTN_HOVER, activeforeground=TEXT,
            font=("Segoe UI", 9), width=10, cursor="hand2"
        )
        self.connect_btn.grid(row=0, column=4, rowspan=2, padx=8, sticky="ns")

        path_frame = tk.Frame(inner, bg=BG)
        path_frame.pack(fill="x", padx=12, pady=(0, 6))

        self.up_btn = tk.Button(
            path_frame, text="Up", command=self._go_up,
            bg=BTN_BG, fg=TEXT, relief="flat", bd=0,
            activebackground=BTN_HOVER, activeforeground=TEXT,
            font=("Segoe UI", 9), width=8, state="disabled", cursor="hand2"
        )
        self.up_btn.pack(side="left")

        self.refresh_btn = tk.Button(
            path_frame, text="Refresh", command=self._refresh,
            bg=BTN_BG, fg=TEXT, relief="flat", bd=0,
            activebackground=BTN_HOVER, activeforeground=TEXT,
            font=("Segoe UI", 9), width=8, state="disabled", cursor="hand2"
        )
        self.refresh_btn.pack(side="left", padx=(6, 0))

        self.path_var = tk.StringVar(value="(not connected)")
        path_lbl = tk.Label(
            path_frame, textvariable=self.path_var, bg=BG, fg=TEXT,
            font=("Consolas", 10), anchor="w"
        )
        path_lbl.pack(side="left", fill="x", expand=True, padx=10)

        list_frame = tk.Frame(inner, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        self.listbox = tk.Listbox(
            list_frame, bg=FIELD_BG, fg=TEXT,
            selectbackground=ACCENT, selectforeground=BG,
            font=("Consolas", 10), relief="flat",
            highlightthickness=1, highlightbackground=BTN_BG,
            highlightcolor=ACCENT, bd=0, activestyle="none"
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", self._on_double_click)
        self.listbox.bind("<Return>", self._on_double_click)

        sb = tk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=sb.set)

        status_frame = tk.Frame(inner, bg=BG, height=22)
        status_frame.pack(fill="x", side="bottom")
        status_frame.pack_propagate(False)

        self.status_var = tk.StringVar(value="Ready")
        status_lbl = tk.Label(
            status_frame, textvariable=self.status_var, bg=BG, fg=TEXT,
            font=("Segoe UI", 8), anchor="w"
        )
        status_lbl.pack(side="left", padx=12)

        watermark = tk.Label(
            status_frame, text="oT", bg=BG, fg=ACCENT,
            font=("Segoe UI", 9, "italic")
        )
        watermark.pack(side="right", padx=8)

    def _make_label(self, parent, text):
        return tk.Label(parent, text=text, bg=BG, fg=TEXT, font=("Segoe UI", 9))

    def _make_entry(self, parent, width, show=None):
        return tk.Entry(
            parent, bg=FIELD_BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=2, width=width, font=("Consolas", 10),
            show=show or ""
        )

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _do_drag(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _quit(self):
        try:
            if SMB_AVAILABLE and self.connected:
                reset_connection_cache()
        except Exception:
            pass
        self.root.destroy()

    def _show_help(self):
        msg = (
            "Remote Share Browser\n\n"
            "1. Enter Server (hostname or IP), Share name, User and Password.\n"
            "2. Click Connect.\n"
            "3. Double-click a folder to enter it.\n"
            "4. Use 'Up' to go to the parent folder.\n"
            "5. 'Refresh' reloads the current folder.\n\n"
            "Requires: pip install smbprotocol"
        )
        messagebox.showinfo("Help", msg, parent=self.root)

    def _set_status(self, text):
        self.status_var.set(text)

    def _connect(self):
        if not SMB_AVAILABLE:
            messagebox.showerror(
                "Missing dependency",
                "smbprotocol is not installed.\n"
                "Run: pip install smbprotocol\n\n"
                f"Details: {SMB_IMPORT_ERROR}",
                parent=self.root,
            )
            return
        server = self.server_entry.get().strip()
        share = self.share_entry.get().strip()
        user = self.user_entry.get().strip()
        password = self.pass_entry.get()
        if not server or not share:
            messagebox.showerror("Error", "Server and Share are required", parent=self.root)
            return
        self._set_status("Connecting...")
        self.connect_btn.configure(state="disabled")
        t = threading.Thread(
            target=self._do_connect,
            args=(server, share, user, password),
            daemon=True,
        )
        t.start()

    def _do_connect(self, server, share, user, password):
        try:
            if user:
                register_session(server, username=user, password=password)
            else:
                register_session(server)
            self.server = server
            self.share = share
            self.username = user
            self.password = password
            self.current_path = ""
            self.connected = True
            self.root.after(0, self._refresh)
            self.root.after(0, lambda: self._set_status("Connected"))
        except Exception as exc:
            err = str(exc)
            self.root.after(0, lambda: messagebox.showerror(
                "Connection failed", err, parent=self.root))
            self.root.after(0, lambda: self._set_status("Connection failed"))
        finally:
            self.root.after(0, lambda: self.connect_btn.configure(state="normal"))

    def _unc(self, sub=""):
        base = f"\\\\{self.server}\\{self.share}"
        if sub:
            base += "\\" + sub.replace("/", "\\")
        return base

    def _refresh(self):
        if not self.connected:
            return
        self.listbox.delete(0, tk.END)
        self._set_status("Loading...")
        try:
            entries = list(smbclient.scandir(self._unc(self.current_path)))
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self.root)
            self._set_status("Error")
            return
        entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        for e in entries:
            prefix = "[DIR] " if e.is_dir() else "      "
            self.listbox.insert(tk.END, prefix + e.name)
        display = f"\\\\{self.server}\\{self.share}"
        if self.current_path:
            display += "\\" + self.current_path.replace("/", "\\")
        self.path_var.set(display)
        self.up_btn.configure(state="normal" if self.current_path else "disabled")
        self.refresh_btn.configure(state="normal")
        self._set_status(f"{len(entries)} item(s)")

    def _on_double_click(self, event):
        sel = self.listbox.curselection()
        if not sel:
            return
        item = self.listbox.get(sel[0])
        if not item.startswith("[DIR]"):
            return
        name = item[6:].strip()
        if not self.current_path:
            self.current_path = name
        else:
            self.current_path = self.current_path + "/" + name
        self._refresh()

    def _go_up(self):
        if not self.current_path:
            return
        if "/" in self.current_path:
            self.current_path = self.current_path.rsplit("/", 1)[0]
        else:
            self.current_path = ""
        self._refresh()


def main():
    root = tk.Tk()
    RemoteShareBrowser(root)
    root.mainloop()


if __name__ == "__main__":
    main()
