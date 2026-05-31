import ipaddress
import tkinter as tk
from tkinter import messagebox

BG = "#121212"
ACCENT = "#FF6B00"
FG = "#FFFFFF"
BTN_BG = "#333333"
RED = "#C0392B"

APP_TITLE = "IP Checker"


class IPCheckerApp:
    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.configure(bg=ACCENT)
        self.width = 420
        self.height = 280
        self._center_window()

        self.outer = tk.Frame(self.root, bg=ACCENT)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        self.container = tk.Frame(self.outer, bg=BG)
        self.container.pack(fill="both", expand=True)

        self._drag_x = 0
        self._drag_y = 0

        self._build_title_bar()
        self._build_body()
        self._build_watermark()

    def _center_window(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - self.width) // 2
        y = (sh - self.height) // 2
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _build_title_bar(self):
        bar = tk.Frame(self.container, bg=BG, height=36)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        title = tk.Label(
            bar, text=APP_TITLE, bg=BG, fg=ACCENT,
            font=("Segoe UI", 11, "bold"),
        )
        title.pack(side="left", padx=10)

        quit_btn = tk.Button(
            bar, text="Quit", bg=RED, fg=FG, activebackground=RED,
            activeforeground=FG, bd=0, relief="flat",
            font=("Segoe UI", 9, "bold"), width=7,
            command=self._on_quit,
        )
        quit_btn.pack(side="right", padx=(0, 8), pady=4)

        help_btn = tk.Button(
            bar, text="Help", bg=BTN_BG, fg=FG, activebackground=BTN_BG,
            activeforeground=FG, bd=0, relief="flat",
            font=("Segoe UI", 9), width=7,
            command=self._on_help,
        )
        help_btn.pack(side="right", padx=(0, 6), pady=4)

        for w in (bar, title):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._do_drag)

    def _build_body(self):
        body = tk.Frame(self.container, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=10)

        lbl = tk.Label(
            body, text="Enter IP address:", bg=BG, fg=FG,
            font=("Segoe UI", 10),
        )
        lbl.pack(anchor="w", pady=(8, 4))

        self.entry = tk.Entry(
            body, bg="#1E1E1E", fg=FG, insertbackground=FG,
            relief="flat", font=("Consolas", 11),
            highlightthickness=1, highlightbackground=BTN_BG,
            highlightcolor=ACCENT,
        )
        self.entry.pack(fill="x", ipady=6)
        self.entry.bind("<Return>", lambda e: self._check())

        check_btn = tk.Button(
            body, text="Check", bg=BTN_BG, fg=FG,
            activebackground=BTN_BG, activeforeground=FG,
            bd=0, relief="flat", font=("Segoe UI", 10, "bold"),
            command=self._check,
        )
        check_btn.pack(fill="x", pady=12, ipady=6)

        self.result_var = tk.StringVar(value="")
        self.result = tk.Label(
            body, textvariable=self.result_var, bg=BG, fg=ACCENT,
            font=("Consolas", 11, "bold"), justify="left", anchor="w",
        )
        self.result.pack(fill="x", pady=(4, 0))

    def _build_watermark(self):
        wm = tk.Label(
            self.container, text="oT", bg=BG, fg="#555555",
            font=("Segoe UI", 8, "italic"),
        )
        wm.place(relx=1.0, rely=1.0, x=-8, y=-6, anchor="se")

    def _check(self):
        value = self.entry.get().strip()
        if not value:
            self.result.configure(fg=ACCENT)
            self.result_var.set("Please enter an IP address.")
            return
        try:
            ip = ipaddress.ip_address(value)
            version = "IPv4" if ip.version == 4 else "IPv6"
            self.result.configure(fg="#2ECC71")
            self.result_var.set(f"Valid  -  {version}")
        except ValueError:
            self.result.configure(fg=RED)
            self.result_var.set("Invalid IP address")

    def _on_help(self):
        messagebox.showinfo(
            "Help",
            "Enter an IPv4 or IPv6 address and press Check.\n"
            "The app reports whether the address is valid\n"
            "and its version (IPv4 or IPv6).",
        )

    def _on_quit(self):
        self.root.destroy()

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event):
        x = self.root.winfo_pointerx() - self._drag_x
        y = self.root.winfo_pointery() - self._drag_y
        self.root.geometry(f"+{x}+{y}")


def main():
    root = tk.Tk()
    root.title(APP_TITLE)
    IPCheckerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
