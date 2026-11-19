import ctypes
import ipaddress
import json
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

BG_BAR = "#121212"
BG_PANEL = "#1e1e1e"
BG_CTRL = "#333333"
FG = "#ffffff"
FG_MUTED = "#888888"
ACCENT = "#ff6600"
DANGER = "#ff0000"

DATA_FILE = Path(__file__).resolve().parent / "addresses.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    try:
        script = str(Path(__file__).resolve())
        params = " ".join(f'"{a}"' for a in [script] + sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return rc > 32
    except Exception:
        return False


def decode(data):
    try:
        return data.decode("oem", errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def get_adapters():
    try:
        result = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True,
            creationflags=NO_WINDOW,
        )
    except Exception:
        return []
    names = []
    started = False
    for line in decode(result.stdout).splitlines():
        if line.strip().startswith("---"):
            started = True
            continue
        if started and line.strip():
            parts = line.split(None, 3)
            if len(parts) == 4:
                names.append(parts[3].strip())
    return names


def load_addresses():
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict) and d.get("ip")]
    except Exception:
        return []


def save_addresses(addresses):
    DATA_FILE.write_text(json.dumps(addresses, indent=2), encoding="utf-8")


def valid_ip(value):
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def valid_mask(value):
    if not valid_ip(value):
        return False
    try:
        ipaddress.IPv4Network(f"0.0.0.0/{value}")
        return True
    except ValueError:
        return False


class App(tk.Tk):
    def __init__(self, admin):
        super().__init__()
        self.admin = admin
        self.addresses = load_addresses()
        self.busy = False
        self.action_buttons = []

        self.title("IP Switcher")
        self.configure(bg=BG_BAR)
        self.geometry("760x620")
        self.minsize(640, 520)
        self.blank_icon = tk.PhotoImage(width=16, height=16)
        self.iconphoto(True, self.blank_icon)

        self.setup_style()
        self.build_ui()
        self.refresh_list()
        self.refresh_adapters()

        self.update_idletasks()
        self.dark_title_bar()

        if not self.admin:
            self.write("Not running as administrator. Changing adapter settings will fail.\n\n")

    def dark_title_bar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)
            res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
            )
            if res != 0:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(value), ctypes.sizeof(value)
                )
        except Exception:
            pass

    def setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=BG_PANEL,
            fieldbackground=BG_PANEL,
            foreground=FG,
            bordercolor=BG_BAR,
            borderwidth=0,
            rowheight=24,
        )
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", FG)])
        style.configure(
            "Treeview.Heading",
            background=BG_CTRL,
            foreground=FG,
            relief="flat",
            bordercolor=BG_BAR,
        )
        style.map("Treeview.Heading", background=[("active", BG_CTRL)])
        style.configure(
            "TCombobox",
            fieldbackground=BG_CTRL,
            background=BG_CTRL,
            foreground=FG,
            arrowcolor=FG,
            bordercolor=BG_BAR,
            lightcolor=BG_CTRL,
            darkcolor=BG_CTRL,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_CTRL)],
            selectbackground=[("readonly", BG_CTRL)],
            selectforeground=[("readonly", FG)],
            foreground=[("readonly", FG)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=BG_CTRL,
            troughcolor=BG_BAR,
            bordercolor=BG_BAR,
            arrowcolor=FG,
            lightcolor=BG_CTRL,
            darkcolor=BG_CTRL,
        )
        self.option_add("*TCombobox*Listbox.background", BG_CTRL)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", FG)

    def make_button(self, parent, text, command, danger=False, action=True):
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG_CTRL,
            fg=DANGER if danger else FG,
            activebackground=ACCENT,
            activeforeground=FG,
            disabledforeground=FG_MUTED,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BG_BAR,
            padx=12,
            pady=5,
            cursor="hand2",
        )
        if action:
            self.action_buttons.append(btn)
        return btn

    def make_label(self, parent, text, bg=BG_PANEL):
        return tk.Label(parent, text=text, bg=bg, fg=FG)

    def make_entry(self, parent, width):
        return tk.Entry(
            parent,
            width=width,
            bg=BG_CTRL,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BG_BAR,
            highlightcolor=ACCENT,
        )

    def build_ui(self):
        header = tk.Label(
            self, text="IP Switcher", bg=BG_BAR, fg=ACCENT, font=("Segoe UI", 13, "bold"), anchor="w"
        )
        header.pack(fill="x", padx=10, pady=(8, 4))

        adapter_bar = tk.Frame(self, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)
        adapter_bar.pack(fill="x", padx=10, pady=4)
        self.make_label(adapter_bar, "Adapter:").pack(side="left", padx=(8, 6), pady=8)
        self.adapter_var = tk.StringVar()
        self.adapter_box = ttk.Combobox(
            adapter_bar, textvariable=self.adapter_var, state="readonly", width=40
        )
        self.adapter_box.pack(side="left", pady=8)
        self.adapter_box.bind("<<ComboboxSelected>>", lambda e: self.show_config())
        self.make_button(adapter_bar, "Refresh", self.refresh_adapters).pack(side="left", padx=6)
        self.make_button(adapter_bar, "Show Config", self.show_config).pack(side="left")

        list_frame = tk.Frame(self, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)
        list_frame.pack(fill="both", expand=True, padx=10, pady=4)

        columns = ("name", "ip", "mask", "gateway")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse", height=7)
        for col, title, width in (
            ("name", "Name", 180),
            ("ip", "IP Address", 140),
            ("mask", "Subnet Mask", 140),
            ("gateway", "Gateway", 140),
        ):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        add_frame = tk.Frame(self, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)
        add_frame.pack(fill="x", padx=10, pady=4)
        self.name_entry = self.make_entry(add_frame, 18)
        self.ip_entry = self.make_entry(add_frame, 15)
        self.mask_entry = self.make_entry(add_frame, 15)
        self.gw_entry = self.make_entry(add_frame, 15)
        self.mask_entry.insert(0, "255.255.255.0")
        for i, (text, entry) in enumerate(
            (
                ("Name", self.name_entry),
                ("IP", self.ip_entry),
                ("Mask", self.mask_entry),
                ("Gateway", self.gw_entry),
            )
        ):
            self.make_label(add_frame, text).grid(row=0, column=i, padx=6, pady=(6, 0), sticky="w")
            entry.grid(row=1, column=i, padx=6, pady=(2, 8), sticky="we")
        self.make_button(add_frame, "Add", self.add_address).grid(row=1, column=4, padx=4, pady=(2, 8))
        self.make_button(add_frame, "Delete", self.delete_address, danger=True).grid(
            row=1, column=5, padx=(4, 8), pady=(2, 8)
        )

        action_bar = tk.Frame(self, bg=BG_BAR)
        action_bar.pack(fill="x", padx=10, pady=4)
        self.make_button(action_bar, "Apply Static IP", self.apply_static).pack(side="left", padx=(0, 6))
        self.make_button(action_bar, "Set DHCP", self.set_dhcp).pack(side="left", padx=6)
        self.make_button(action_bar, "Ping", self.ping).pack(side="left", padx=6)
        self.make_button(action_bar, "Clear Output", self.clear_output, action=False).pack(side="left", padx=6)
        self.make_button(action_bar, "Quit", self.destroy, danger=True, action=False).pack(side="right")

        out_frame = tk.Frame(self, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)
        out_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.output = tk.Text(
            out_frame,
            bg=BG_PANEL,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            bd=0,
            height=10,
            font=("Consolas", 10),
            state="disabled",
            wrap="word",
        )
        out_scroll = ttk.Scrollbar(out_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=out_scroll.set)
        self.output.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        out_scroll.pack(side="right", fill="y")

    def write(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        for btn in self.action_buttons:
            btn.configure(state="disabled" if busy else "normal")

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, a in enumerate(self.addresses):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(a.get("name", ""), a.get("ip", ""), a.get("mask", ""), a.get("gateway", "")),
            )

    def refresh_adapters(self):
        names = get_adapters()
        self.adapter_box["values"] = names
        current = self.adapter_var.get()
        if current not in names:
            self.adapter_var.set(names[0] if names else "")
        if not names:
            self.write("No network adapters found.\n")

    def selected_address(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("IP Switcher", "Select an address from the list.", parent=self)
            return None
        return self.addresses[int(sel[0])]

    def selected_adapter(self):
        name = self.adapter_var.get()
        if not name:
            messagebox.showwarning("IP Switcher", "Select a network adapter.", parent=self)
            return None
        return name

    def add_address(self):
        name = self.name_entry.get().strip()
        ip = self.ip_entry.get().strip()
        mask = self.mask_entry.get().strip()
        gw = self.gw_entry.get().strip()
        if not valid_ip(ip):
            messagebox.showerror("IP Switcher", "Invalid IP address.", parent=self)
            return
        if not valid_mask(mask):
            messagebox.showerror("IP Switcher", "Invalid subnet mask.", parent=self)
            return
        if gw and not valid_ip(gw):
            messagebox.showerror("IP Switcher", "Invalid gateway.", parent=self)
            return
        self.addresses.append({"name": name, "ip": ip, "mask": mask, "gateway": gw})
        try:
            save_addresses(self.addresses)
        except Exception as e:
            messagebox.showerror("IP Switcher", f"Could not save list:\n{e}", parent=self)
        self.refresh_list()
        self.name_entry.delete(0, "end")
        self.ip_entry.delete(0, "end")
        self.gw_entry.delete(0, "end")

    def delete_address(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("IP Switcher", "Select an address to delete.", parent=self)
            return
        a = self.addresses[int(sel[0])]
        if not messagebox.askyesno("IP Switcher", f"Delete {a['ip']}?", parent=self):
            return
        del self.addresses[int(sel[0])]
        try:
            save_addresses(self.addresses)
        except Exception as e:
            messagebox.showerror("IP Switcher", f"Could not save list:\n{e}", parent=self)
        self.refresh_list()

    def run_steps(self, steps):
        if self.busy:
            return
        self.set_busy(True)

        def work():
            for title, args in steps:
                self.after(0, self.write, f"> {title}\n")
                try:
                    proc = subprocess.Popen(
                        args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        creationflags=NO_WINDOW,
                    )
                    for line in iter(proc.stdout.readline, b""):
                        text = decode(line).replace("\r", "")
                        if text.strip():
                            self.after(0, self.write, text)
                    proc.wait()
                except Exception as e:
                    self.after(0, self.write, f"Error: {e}\n")
                self.after(0, self.write, "\n")
            self.after(0, self.set_busy, False)

        threading.Thread(target=work, daemon=True).start()

    def config_step(self, adapter):
        return (f"Current config of {adapter}", ["netsh", "interface", "ipv4", "show", "config", adapter])

    def show_config(self):
        adapter = self.selected_adapter()
        if adapter:
            self.run_steps([self.config_step(adapter)])

    def apply_static(self):
        adapter = self.selected_adapter()
        if not adapter:
            return
        a = self.selected_address()
        if not a:
            return
        args = ["netsh", "interface", "ipv4", "set", "address", adapter, "static", a["ip"], a["mask"]]
        if a.get("gateway"):
            args.append(a["gateway"])
        self.run_steps([(f"Set {adapter} to {a['ip']}", args), self.config_step(adapter)])

    def set_dhcp(self):
        adapter = self.selected_adapter()
        if not adapter:
            return
        args = ["netsh", "interface", "ipv4", "set", "address", adapter, "dhcp"]
        self.run_steps([(f"Set {adapter} to DHCP", args), self.config_step(adapter)])

    def ping(self):
        a = self.selected_address()
        if not a:
            return
        self.run_steps([(f"Ping {a['ip']}", ["ping", "-n", "4", a["ip"]])])


def main():
    admin = is_admin()
    if not admin and sys.platform == "win32":
        if relaunch_as_admin():
            return
    App(admin).mainloop()


if __name__ == "__main__":
    main()
