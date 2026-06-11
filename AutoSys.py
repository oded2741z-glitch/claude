import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os, subprocess, threading, time, shutil, sys, ctypes, platform, ipaddress
from concurrent.futures import ThreadPoolExecutor

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

try: import send2trash
except ImportError: send2trash = None
try: 
    import pygetwindow as gw
    ENFORCER_AVAILABLE = True
except ImportError: 
    gw = None; ENFORCER_AVAILABLE = False

# ==========================================
#        CUSTOM INPUT DIALOG (STYLED)
# ==========================================
class CustomInputDialog(tk.Toplevel):
    def __init__(self, parent, title, prompt):
        super().__init__(parent)
        self.result = None
        self.overrideredirect(True)
        self.configure(bg='#121212', highlightthickness=1, highlightbackground="#FFA500")
        w, h = 350, 180
        x = parent.winfo_x() + (parent.winfo_width() - w) // 2
        y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        
        header = tk.Frame(self, bg="#121212"); header.pack(fill="x")
        tk.Label(header, text=f"  {title}", bg="#121212", fg="#FFA500", font=("Arial", 10, "bold")).pack(side="left", pady=5)
        tk.Button(header, text="X", bg="#CC0303", fg="white", bd=0, font=("Arial", 9, "bold"), width=4, command=self.destroy).pack(side="right")
        
        body = tk.Frame(self, bg="#121212"); body.pack(fill="both", expand=True, padx=20, pady=10)
        tk.Label(body, text=prompt, bg="#121212", fg="white", font=("Arial", 9)).pack(pady=(0, 10))
        self.entry = tk.Entry(body, bg="#333333", fg="white", insertbackground="white", bd=0); self.entry.pack(fill="x", ipady=4); self.entry.focus_set()
        
        btn_f = tk.Frame(body, bg="#121212"); btn_f.pack(pady=15)
        tk.Button(btn_f, text="OK", width=10, bg="#333333", fg="white", bd=0, font=("Arial", 9, "bold"), command=self.on_ok).pack(side="left", padx=5)
        tk.Button(btn_f, text="CANCEL", width=10, bg="#333333", fg="white", bd=0, font=("Arial", 9, "bold"), command=self.destroy).pack(side="left", padx=5)
        
        self.bind('<Return>', lambda e: self.on_ok()); self.bind('<Escape>', lambda e: self.destroy())
        self.grab_set(); self.wait_window()
        
    def on_ok(self): self.result = self.entry.get(); self.destroy()

# ==========================================
#           WINDOW ENFORCER
# ==========================================
class EnforcerWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.app_title = "SYSTEM_ENFORCER_V2.1"
        self.overrideredirect(True); self.configure(bg="#121212")
        x = master.winfo_x() - 455; y = master.winfo_y()
        self.geometry(f"450x600+{x}+{y}")
        self.main_frame = tk.Frame(self, bg="#121212", highlightbackground="#FFA500", highlightthickness=1, bd=0); self.main_frame.pack(fill="both", expand=True)
        header = tk.Frame(self.main_frame, bg="#121212"); header.pack(fill="x", padx=10, pady=5)
        tk.Button(header, text="REFRESH", bg="#333333", fg="#FFFFFF", font=("Consolas", 8), bd=0, command=self.refresh_list).pack(side="left")
        tk.Button(header, text="close", bg="#CC0303", fg="#FFFFFF", font=("Consolas", 8, "bold"), bd=0, command=self.destroy, padx=10).pack(side="right")
        tk.Label(self.main_frame, text=self.app_title, bg="#121212", fg="#FFA500", font=("Consolas", 12, "bold")).pack(pady=5)
        self.listbox = tk.Listbox(self.main_frame, bg="#121212", fg="#FFFFFF", selectbackground="#333333", selectforeground="#FFA500", font=("Consolas", 10), bd=1, relief="flat")
        self.listbox.pack(fill="both", expand=True, padx=10, pady=5); self.listbox.bind("<ButtonRelease-1>", self.on_item_click)
        self.log_box = tk.Text(self.main_frame, bg="#000000", fg="#FFA500", font=("Consolas", 9), height=5, bd=0); self.log_box.pack(fill="x", padx=10, pady=5)
        self.main_frame.bind("<Button-1>", self.start_drag); self.main_frame.bind("<B1-Motion>", self.on_drag)
        self.refresh_list()
    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        if ENFORCER_AVAILABLE:
            for win in gw.getAllWindows():
                if win.title.strip() and "ENFORCER" not in win.title and "AutoSys" not in win.title:
                    st = "MIN" if win.isMinimized else "ACTV"
                    self.listbox.insert(tk.END, f"[{st}] {win.title}")
    def on_item_click(self, event):
        sel = self.listbox.curselection()
        if not sel: return
        title = self.listbox.get(sel[0]).split("] ", 1)[1]
        try:
            target = gw.getWindowsWithTitle(title)[0]
            if target.isMinimized: target.restore(); target.activate()
            else: target.minimize()
            self.after(200, self.refresh_list)
        except: pass
    def start_drag(self, e): self.x, self.y = e.x, e.y
    def on_drag(self, e): self.geometry(f"+{self.winfo_x() + e.x - self.x}+{self.winfo_y() + e.y - self.y}")

# ==========================================
#           NETWORK SCANNER
# ==========================================
class NetworkScanner:
    def __init__(self, master, app_path):
        self.master = master; self.app_path = app_path; self.networks_file = os.path.join(app_path, "networks.txt")
        master.configure(bg='#121212'); self.stop_scan = False; self.scanning = False   
        self.networks = self.load_networks()
        tk.Label(master, text="Select network to scan:", bg='#121212', fg='#FFA500', font=("Arial", 10, "bold")).pack(pady=5)
        self.network_var = tk.StringVar(); self.network_combo = ttk.Combobox(master, textvariable=self.network_var, values=self.networks, width=47)
        self.network_combo.pack(pady=5)
        if self.networks: self.network_combo.current(0)
        else: self.network_combo.set("No networks available")
        btn_f = tk.Frame(master, bg='#121212'); btn_f.pack(pady=5)
        tk.Button(btn_f, text="Scan", command=self.start_scan, bg="#333333", width=10, bd=0, fg="white", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_f, text="Stop", command=self.stop_scanning, bg="#CC0303", width=10, bd=0, fg="white", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_f, text="Add Network", command=self.add_network, bg="#333333", width=15, bd=0, fg="white", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        self.progress = ttk.Progressbar(master, orient="horizontal", length=450, mode="determinate"); self.progress.pack(pady=5)
        self.tree = ttk.Treeview(master, columns=("ip",), show="headings", height=15); self.tree.heading("ip", text="Active IPs"); self.tree.column("ip", width=480, anchor="center"); self.tree.pack(pady=10)
    def load_networks(self):
        if not os.path.exists(self.networks_file): return ["192.168.1.0/24"]
        try:
            with open(self.networks_file, "r") as f: return [line.strip() for line in f if line.strip()]
        except: return []
    def save_network(self, network):
        with open(self.networks_file, "a") as f: f.write(network + "\n")
        self.networks.append(network); self.network_combo["values"] = self.networks; self.network_combo.set(network)
    def add_network(self):
        dialog = CustomInputDialog(self.master, "Add Network", "Enter network (e.g., 192.168.2.0/24):")
        if not dialog.result: return
        net = dialog.result.strip()
        try:
            if "/" in net: ipaddress.IPv4Network(net, strict=False)
            else: ipaddress.IPv4Address(net)
        except ValueError:
            messagebox.showerror("Invalid Network", f"'{net}' is not a valid IPv4 network or address.")
            return
        self.save_network(net)
    def start_scan(self):
        if self.scanning: return
        self.scanning = True; self.stop_scan = False; self.tree.delete(*self.tree.get_children())
        try:
            net = self.network_var.get()
            ips = list(ipaddress.IPv4Network(net, strict=False).hosts()) if "/" in net else [ipaddress.IPv4Address(net)]
        except: ips = []
        self.progress.config(maximum=len(ips), value=0)
        def worker(ip):
            if not self.stop_scan:
                if subprocess.run(["ping", "-n", "1", "-w", "200", str(ip)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).returncode == 0:
                    self.master.after(0, lambda: self.tree.insert("", "end", values=(str(ip),)))
            # Always advance the progress bar so it completes even when stopped
            self.master.after(0, lambda: self.progress.step(1))
        def run_scan():
            try:
                with ThreadPoolExecutor(max_workers=50) as ex:
                    list(ex.map(worker, ips))
            finally:
                self.scanning = False
        threading.Thread(target=run_scan, daemon=True).start()
    def stop_scanning(self): self.stop_scan = True

# ==========================================
#           AUTOSYS MAIN APP
# ==========================================
class AutoSysApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True); self.configure(bg='#121212', highlightthickness=1, highlightbackground="#FFA500")
        sw = self.winfo_screenwidth(); self.full_height, self.collapsed_height = 700, 40
        self.geometry(f'300x{self.collapsed_height}+{sw-300}+0'); self.is_collapsed = True
        self.bind('<Button-1>', self.start_drag); self.bind('<B1-Motion>', self.do_drag); self.bind_all('<F4>', self.toggle_ghost_mode); self.is_ghost = False

        self.application_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        self.db_file = os.path.join(self.application_path, "ips.txt")
        self.prog_file = os.path.join(self.application_path, "programs.txt")
        
        self.global_startup = os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), r"Microsoft\Windows\Start Menu\Programs\Startup")
        self.user_startup = self.get_real_user_startup()
        self.startup_path_script = os.path.join(self.global_startup, 'AutoSys_Loader.bat')
        
        self.use_user_startup_var = tk.BooleanVar(value=False)
        self.keep_pinging = False
        self._main_status_timer = None
        self.btn_style = {"bg": "#333333", "fg": "white", "bd": 0, "font": ("Arial", 9, "bold"), "cursor": "hand2"}

        self.setup_style()
        self.setup_ui()
        self.load_ips()
        self.refresh_startup_list()
        self.load_programs()
        
        self.update_idletasks()
        
        self.after(1500, self.sys_mtk_refresh)

    def get_real_user_startup(self):
        try:
            ps_script = (
                "$process = Get-WmiObject Win32_Process -Filter \"Name='explorer.exe'\" | Select-Object -First 1;"
                "if ($process) {"
                "    $sid = $process.GetOwnerSid().Sid;"
                "    $appdata = (Get-ItemProperty \"Registry::HKEY_USERS\\$sid\\Volatile Environment\" -Name APPDATA -ErrorAction SilentlyContinue).APPDATA;"
                "    if ($appdata) { Write-Output \"$appdata\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\" }"
                "}"
            )
            cmd = ["powershell", "-NoProfile", "-Command", ps_script]
            output = subprocess.check_output(cmd, creationflags=CREATE_NO_WINDOW).decode('mbcs', errors='ignore').strip()
            if output and os.path.exists(output):
                return output
        except: pass
            
        try:
            users_dir = r"C:\Users"
            if os.path.exists(users_dir):
                ignore_list = ["public", "default", "administrator", "all users", "default user"]
                valid_users = []
                for user in os.listdir(users_dir):
                    if user.lower() not in ignore_list and not user.lower().startswith("admin"):
                        test_path = os.path.join(users_dir, user, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup")
                        if os.path.exists(test_path):
                            valid_users.append((test_path, os.path.getmtime(os.path.join(users_dir, user))))
                
                if valid_users:
                    valid_users.sort(key=lambda x: x[1], reverse=True)
                    return valid_users[0][0]
        except: pass
        return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")

    def start_drag(self, e): self.x, self.y = e.x, e.y
    def do_drag(self, e): self.geometry(f"+{self.winfo_x() + e.x - self.x}+{self.winfo_y() + e.y - self.y}")
    def toggle_ghost_mode(self, e=None):
        if not self.is_ghost: self.withdraw(); self.is_ghost = True
        else: self.deiconify(); self.attributes('-topmost', True); self.after(200, lambda: self.attributes('-topmost', False)); self.is_ghost = False

    # --- UI SETUP ---
    def setup_style(self):
        s = ttk.Style()
        s.theme_use('default')
        s.configure('TNotebook', background='#121212', borderwidth=0)
        s.configure('TNotebook.Tab', background='#333333', foreground='white', padding=[5, 2], font=("Arial", 8, "bold"))
        s.map('TNotebook.Tab', background=[('selected', '#121212')], foreground=[('selected', '#FFA500')])
        s.configure("Treeview", background="#333333", foreground="white", fieldbackground="#333333", borderwidth=0)
        s.configure("Treeview.Heading", background="#121212", foreground="#FFA500", font=("Arial", 9, "bold"))

    def setup_ui(self):
        tk.Label(self, text="AutoSys", bg="#121212", fg="#FFA500", font=("Arial", 10, "bold")).place(x=10, y=8)
        tk.Button(self, text="Quit", command=self.destroy, width=6, bg="#CC0303", fg="white", bd=0, font=("Arial", 9, "bold"), cursor="hand2").place(x=235, y=5, height=28)
        tk.Button(self, text="Help", command=self.show_detailed_help, width=6, **self.btn_style).place(x=175, y=5, height=28)
        
        self.collapse_btn = tk.Button(self, text="▼", bg="#121212", fg="#FFA500", bd=0, font=("Arial", 14, "bold"), command=self.toggle_collapse)
        self.collapse_btn.place(x=142, y=0, width=30, height=30)
        
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, pady=(40, 0))
        
        self.tab_main = tk.Frame(self.notebook, bg='#121212'); self.notebook.add(self.tab_main, text="Main")
        self.tab_startup = tk.Frame(self.notebook, bg='#121212'); self.notebook.add(self.tab_startup, text="Startup")
        self.tab_system = tk.Frame(self.notebook, bg='#121212'); self.notebook.add(self.tab_system, text="System")
        self.tab_prog = tk.Frame(self.notebook, bg='#121212'); self.notebook.add(self.tab_prog, text="Programs")
        
        self.setup_main_tab()
        self.setup_startup_tab()
        self.setup_system_tab()
        self.setup_programs_tab()
        
        self.ot_lbl = tk.Label(self, text="oT", bg="#121212", fg="#333333", font=("Arial", 8, "bold"))
        self.ot_lbl.place(relx=0.98, rely=0.99, anchor="se")

    def toggle_collapse(self):
        if not self.is_collapsed: 
            self.geometry(f'300x40')
            self.collapse_btn.config(text="▼")
            self.is_collapsed = True
        else: 
            self.geometry(f'300x700')
            self.collapse_btn.config(text="▲")
            self.is_collapsed = False

    # ================= NETWORK SCANNER OPENER =================
    def open_scanner_window(self): 
        sw = tk.Toplevel(self)
        sw.overrideredirect(True)
        sw.configure(bg='#121212', highlightthickness=1, highlightbackground="#FFA500")
        sw.geometry(f"520x550+{self.winfo_x()-525}+{self.winfo_y()}")
        h = tk.Frame(sw, bg="#121212")
        h.pack(fill="x")
        tk.Label(h, text=" LAN IP Scanner", bg="#121212", fg="#FFA500", font=("Arial", 10, "bold")).pack(side="left")
        tk.Button(h, text="X", bg="#CC0303", fg="white", bd=0, command=sw.destroy).pack(side="right")
        NetworkScanner(sw, self.application_path)

    # ================= MAIN TAB LOGIC =================
    def setup_main_tab(self):
        f = tk.Frame(self.tab_main, bg='#121212')
        f.pack(pady=5, fill="x", padx=15)
        self.ping_btn = tk.Button(f, text="Ping All", command=self.start_ping_threads, **self.btn_style)
        self.ping_btn.pack(side="left", fill="x", expand=True, padx=2)
        self.stop_btn = tk.Button(f, text="Stop", command=self.stop_all_pings, width=8, **self.btn_style)
        self.stop_btn.pack(side="right")
        tk.Button(self.tab_main, text="Open folders", command=self.open_selected_folders_thread, **self.btn_style).pack(pady=2, padx=15, fill="x")
        
        self.status_label = tk.Label(self.tab_main, text="Status: Ready", bg="#121212", fg="white", font=("Arial", 9))
        self.status_label.pack(pady=10)
        
        # אזור העריכה (IP, Name וכפתורים)
        inf = tk.Frame(self.tab_main, bg='#121212')
        inf.pack(side="bottom", fill="x", padx=15, pady=5)
        
        row1 = tk.Frame(inf, bg='#121212')
        row1.pack(fill="x", pady=(0, 5))
        tk.Label(row1, text="IP:", bg="#121212", fg="#FFA500", font=("Arial", 9, "bold")).pack(side="left")
        self.ip_entry = tk.Entry(row1, bg="#333333", fg="white", bd=0, width=15)
        self.ip_entry.pack(side="left", padx=(2, 10), ipady=5)
        tk.Label(row1, text="Name:", bg="#121212", fg="#FFA500", font=("Arial", 9, "bold")).pack(side="left")
        self.name_entry = tk.Entry(row1, bg="#333333", fg="white", bd=0)
        self.name_entry.pack(side="left", fill="x", expand=True, ipady=5)

        row2 = tk.Frame(inf, bg='#121212')
        row2.pack(fill="x")
        tk.Button(row2, text="Add", command=self.add_ip, **self.btn_style).pack(side="left", fill="x", expand=True, padx=(0, 2), ipady=2)
        tk.Button(row2, text="Edit", command=self.edit_ip, **self.btn_style).pack(side="left", fill="x", expand=True, padx=2, ipady=2)
        tk.Button(row2, text="Del", command=self.del_ip, bg="#CC0303", fg="white", bd=0, font=("Arial", 9, "bold"), cursor="hand2").pack(side="left", fill="x", expand=True, padx=(2, 0), ipady=2)
        
        self.open_cmd_var = tk.BooleanVar()
        tk.Checkbutton(self.tab_main, text="Open External CMD Ping", variable=self.open_cmd_var, bg="#121212", fg="white", selectcolor="#333333").pack(anchor="w", padx=15)
        
        self.ip_listbox = tk.Listbox(self.tab_main, bg="#121212", fg="white", bd=0, font=("Consolas", 9), selectmode=tk.MULTIPLE, selectbackground="#121212", selectforeground="#FFA500", highlightthickness=0, activestyle="none")
        self.ip_listbox.pack(fill="both", expand=True, padx=15, pady=5)
        
        self.ip_listbox.bind('<<ListboxSelect>>', self.on_ip_select)

    def set_main_status(self, text, color):
        self.status_label.config(text=text, fg=color)
        if self._main_status_timer: self.after_cancel(self._main_status_timer)
        self._main_status_timer = self.after(7000, lambda: self.status_label.config(text="Status: Ready", fg="white"))

    def start_ping_threads(self):
        self.keep_pinging = True
        idx = self.ip_listbox.curselection()
        self.ip_listbox.selection_clear(0, tk.END)
        for i in idx:
            threading.Thread(target=self.ping_loop, args=(i,), daemon=True).start()
            if self.open_cmd_var.get():
                ip = self.get_pure_ip(self.ip_listbox.get(i))
                subprocess.Popen(f'start cmd /k ping {ip} -t', shell=True)
        self.stop_btn.config(bg="#CC0303", activebackground="#B22222")

    def ping_loop(self, i):
        t = self.get_pure_ip(self.ip_listbox.get(i))
        while self.keep_pinging:
            try:
                res = subprocess.run(["ping", "-n", "1", "-w", "500", t], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
                if res.returncode == 0 and "TTL=" in res.stdout.upper():
                    c = "#228B22"
                else:
                    c = "#B22222"
            except Exception:
                c = "#B22222"
                
            if self.keep_pinging: 
                try: self.after(0, lambda idx=i, color=c: self.ip_listbox.itemconfig(idx, bg=color, fg="white"))
                except: pass
            time.sleep(2)

    def stop_all_pings(self):
        self.keep_pinging = False
        self.stop_btn.config(bg=self.btn_style["bg"])
        for i in range(self.ip_listbox.size()): self.ip_listbox.itemconfig(i, bg="#121212")

    def open_selected_folders_thread(self): 
        threading.Thread(target=self.open_selected_folders, daemon=True).start()

    def open_selected_folders(self):
        idx = self.ip_listbox.curselection()
        if not idx: self.after(0, lambda: self.set_main_status("Select IPs first!", "#FF3333")); return
        for i in idx:
            ip = self.get_pure_ip(self.ip_listbox.get(i))
            if subprocess.run(['ping', '-n', '1', '-w', '500', ip], stdout=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).returncode == 0:
                subprocess.Popen(['explorer', f"\\\\{ip}"])
                self.after(0, lambda t=ip: self.set_main_status(f"Opened: {t}", "#FFA500"))

    def get_pure_ip(self, text): 
        clean_text = text.replace("[X] ", "").replace("[ ] ", "") if text else ""
        return clean_text.split('-')[0].split('|')[0].strip()
        
    def save_ips(self):
        with open(self.db_file, "w") as f:
            for i in range(self.ip_listbox.size()):
                text = self.ip_listbox.get(i).replace("[X] ", "").replace("[ ] ", "")
                f.write(text + "\n")

    def update_checkmarks(self):
        sel = self.ip_listbox.curselection()
        for i in range(self.ip_listbox.size()):
            text = self.ip_listbox.get(i)
            clean_text = text.replace("[X] ", "").replace("[ ] ", "")
            new_text = f"[X] {clean_text}" if i in sel else f"[ ] {clean_text}"
            
            if text != new_text:
                bg = self.ip_listbox.itemcget(i, "bg")
                fg = self.ip_listbox.itemcget(i, "fg")
                self.ip_listbox.delete(i)
                self.ip_listbox.insert(i, new_text)
                self.ip_listbox.itemconfig(i, bg=bg, fg=fg)
                if i in sel:
                    self.ip_listbox.selection_set(i)

    def on_ip_select(self, event=None):
        sel = self.ip_listbox.curselection()
        if len(sel) == 1:
            val = self.ip_listbox.get(sel[0]).replace("[X] ", "").replace("[ ] ", "")
            parts = val.split(' - ', 1)
            
            self.ip_entry.delete(0, tk.END)
            self.ip_entry.insert(0, parts[0].strip())
            
            self.name_entry.delete(0, tk.END)
            if len(parts) > 1:
                self.name_entry.insert(0, parts[1].strip())
        
        self.after(10, self.update_checkmarks)

    def add_ip(self): 
        ip = self.ip_entry.get().strip()
        name = self.name_entry.get().strip()
        if ip: 
            entry_text = f"{ip} - {name}" if name else ip
            self.ip_listbox.insert(tk.END, f"[ ] {entry_text}")
            self.save_ips()
            self.ip_entry.delete(0, tk.END)
            self.name_entry.delete(0, tk.END)

    def edit_ip(self):
        sel = self.ip_listbox.curselection()
        if len(sel) == 1:
            ip = self.ip_entry.get().strip()
            name = self.name_entry.get().strip()
            if ip:
                entry_text = f"[ ] {ip} - {name}" if name else f"[ ] {ip}"
                idx = sel[0]
                bg = self.ip_listbox.itemcget(idx, "bg")
                fg = self.ip_listbox.itemcget(idx, "fg")
                self.ip_listbox.delete(idx)
                self.ip_listbox.insert(idx, entry_text)
                if bg: self.ip_listbox.itemconfig(idx, bg=bg)
                if fg: self.ip_listbox.itemconfig(idx, fg=fg)
                self.ip_listbox.selection_set(idx)
                self.save_ips()
                self.update_checkmarks()
        else:
            self.set_main_status("Select exactly ONE item to edit!", "#FF3333")

    def del_ip(self):
        sel = self.ip_listbox.curselection()
        if sel:
            for idx in reversed(sel):
                self.ip_listbox.delete(idx)
            self.save_ips()
            self.ip_entry.delete(0, tk.END)
            self.name_entry.delete(0, tk.END)

    # ================= STARTUP TAB LOGIC =================
    def setup_startup_tab(self):
        tk.Label(self.tab_startup, text="STARTUP MANAGER", bg="#121212", fg="#FFA500", font=("Arial", 10, "bold")).pack(pady=5)
        
        tk.Checkbutton(self.tab_startup, text="Target User Startup (Instead of Global)", variable=self.use_user_startup_var, command=self.refresh_startup_list, bg="#121212", fg="white", selectcolor="#333333", activebackground="#121212", activeforeground="white").pack(anchor="w", padx=15, pady=(0, 10))

        self.startup_tree = ttk.Treeview(self.tab_startup, columns=("name", "type"), show="headings", height=10)
        self.startup_tree.heading("name", text="Name")
        self.startup_tree.heading("type", text="Type")
        self.startup_tree.column("name", width=200)
        self.startup_tree.pack(fill="both", expand=True, padx=15)
        
        f_btns = tk.Frame(self.tab_startup, bg="#121212")
        f_btns.pack(fill="x", padx=15, pady=10)
        tk.Button(f_btns, text="Refresh", command=self.refresh_startup_list, width=12, **self.btn_style).grid(row=0, column=0, padx=2)
        tk.Button(f_btns, text="Add File", command=self.add_startup_manual, width=15, **self.btn_style).grid(row=0, column=1, padx=2)
        tk.Button(f_btns, text="Delete", command=self.del_startup, bg="#CC0303", fg="white", width=12, bd=0, font=("Arial", 9, "bold")).grid(row=0, column=2, padx=2)
        f_btns.columnconfigure((0,1,2), weight=1)
        
        f_opt = tk.Frame(self.tab_startup, bg="#121212")
        f_opt.pack(fill="x", padx=15)
        self.delay_v = tk.BooleanVar()
        tk.Checkbutton(f_opt, text="Add Delay (sec):", variable=self.delay_v, bg="#121212", fg="white", selectcolor="#333333").pack(side="left")
        self.delay_ent = tk.Entry(f_opt, width=5, bg="#333333", fg="white", bd=0, justify="center")
        self.delay_ent.insert(0, "10")
        self.delay_ent.pack(side="left", padx=5)
        
        f_sh = tk.Frame(self.tab_startup, bg="#121212")
        f_sh.pack(fill="x", padx=15, pady=5)
        tk.Button(f_sh, text="Shortcut", command=self.add_lnk, **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(f_sh, text="Admin Shortcut", command=self.add_admin_lnk, **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(f_sh, text="BAT File", command=self.add_bat, **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        
        f_open = tk.Frame(self.tab_startup, bg="#121212")
        f_open.pack(fill="x", padx=15, pady=5)
        tk.Button(f_open, text="GLOBAL FOLDER", command=lambda: os.startfile(self.global_startup), **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(f_open, text="USER FOLDER", command=lambda: os.startfile(self.user_startup), **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)

    def get_current_startup_folder(self):
        return self.user_startup if self.use_user_startup_var.get() else self.global_startup

    def refresh_startup_list(self):
        self.startup_tree.delete(*self.startup_tree.get_children())
        folder = self.get_current_startup_folder()
        if os.path.exists(folder): 
            for f in os.listdir(folder):
                if f.lower() != "desktop.ini":
                    self.startup_tree.insert("", "end", values=(f, os.path.splitext(f)[1]))

    def add_startup_manual(self): 
        f = filedialog.askopenfilename()
        if f: 
            shutil.copy2(f, self.get_current_startup_folder())
            self.refresh_startup_list()

    def del_startup(self): 
        s = self.startup_tree.selection()
        if s: 
            os.remove(os.path.join(self.get_current_startup_folder(), self.startup_tree.item(s[0])['values'][0]))
            self.refresh_startup_list()
    
    def add_lnk(self, as_admin=False):
        f = filedialog.askopenfilename()
        if f:
            n = os.path.splitext(os.path.basename(f))[0]
            suf = "_Admin" if as_admin else ""
            lnk = os.path.join(self.get_current_startup_folder(), f"{n}{suf}.lnk")
            
            ps_cmd = (
                f'$WshShell = New-Object -comObject WScript.Shell;'
                f'$Shortcut = $WshShell.CreateShortcut("{lnk}");'
                f'$Shortcut.TargetPath = "{f}";'
                f'$Shortcut.WorkingDirectory = "{os.path.dirname(f)}";'
                f'$Shortcut.Save();'
            )
            
            if as_admin:
                ps_cmd += (
                    f'$bytes = [System.IO.File]::ReadAllBytes("{lnk}");'
                    f'$bytes[21] = $bytes[21] -bor 0x20;'
                    f'[System.IO.File]::WriteAllBytes("{lnk}", $bytes);'
                )
                
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], creationflags=CREATE_NO_WINDOW)
                self.refresh_startup_list()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to create shortcut:\n{e}")

    def add_admin_lnk(self): 
        self.add_lnk(as_admin=True)

    def add_bat(self):
        f = filedialog.askopenfilename()
        if f:
            n = os.path.splitext(os.path.basename(f))[0]
            with open(os.path.join(self.get_current_startup_folder(), f"{n}_START.bat"), "w", encoding="utf-8") as b:
                b.write("@echo off\n")
                if self.delay_v.get() and self.delay_ent.get().isdigit(): 
                    b.write(f"timeout /t {self.delay_ent.get()}\n")
                b.write(f'start "" "{os.path.normpath(f)}"\n')
            self.refresh_startup_list()

    # ================= SYSTEM TAB LOGIC =================
    def setup_system_tab(self):
        tk.Label(self.tab_system, text="SYSTEM ADMIN TOOLS", bg="#121212", fg="#FFA500", font=("Arial", 10, "bold")).pack(pady=10)
        
        tk.Button(self.tab_system, text="OPEN NETWORK SCANNER", command=self.open_scanner_window, **self.btn_style).pack(fill="x", padx=15, pady=(0, 10))
        
        qs = tk.LabelFrame(self.tab_system, text=" Quick Shortcuts ", bg="#121212", fg="#cccccc", bd=1)
        qs.pack(fill="x", padx=15, pady=10)
        tools = [("CMD", "start cmd"), ("Task Manager", "taskmgr"), ("Services", "services.msc"), ("Registry", "regedit"), ("Gpedit", "gpedit.msc"), ("Msconfig", "msconfig"), ("Control Panel", "control")]
        for n, c in tools: tk.Button(qs, text=n, command=lambda cmd=c: subprocess.Popen(cmd, shell=True), **self.btn_style).pack(fill="x", padx=10, pady=3)
        
        tk.Button(self.tab_system, text="Show System Info", command=self.sys_show_info_custom, **self.btn_style).pack(fill="x", padx=15, pady=15)
        
        mtk = tk.LabelFrame(self.tab_system, text=" Network Adapters & MTU ", bg="#121212", fg="#cccccc", bd=1)
        mtk.pack(fill="both", expand=True, padx=15, pady=10)
        
        self.mtk_tree = ttk.Treeview(mtk, columns=("n", "s", "m"), show="headings", height=8)
        self.mtk_tree.heading("n", text="Interface")
        self.mtk_tree.heading("s", text="Status")
        self.mtk_tree.heading("m", text="IPv4 MTU")
        self.mtk_tree.column("n", width=120)
        self.mtk_tree.column("s", width=60, anchor="center")
        self.mtk_tree.column("m", width=60, anchor="center")
        self.mtk_tree.pack(fill="both", expand=True, padx=5, pady=5)
        
        r1 = tk.Frame(mtk, bg="#121212")
        r1.pack(fill="x", padx=5, pady=5)
        tk.Button(r1, text="Refresh", command=self.sys_mtk_refresh, **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(r1, text="Open NCPA", command=lambda: subprocess.Popen("ncpa.cpl", shell=True), **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        
        r2 = tk.Frame(mtk, bg="#121212")
        r2.pack(fill="x", padx=5, pady=(0,5))
        tk.Button(r2, text="Enable", command=lambda: self.mtk_action("enable"), **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(r2, text="Disable", command=lambda: self.mtk_action("disable"), **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(r2, text="Set MTU", command=self.set_mtu, **self.btn_style).pack(side="left", fill="x", expand=True, padx=2)

    def sys_mtk_refresh(self):
        for item in self.mtk_tree.get_children(): self.mtk_tree.delete(item)
        def task():
            try:
                cmd = [
                    "powershell", "-NoProfile", "-Command",
                    "Get-NetAdapter | ForEach-Object { "
                    "$name = $_.Name; "
                    "$status = $_.Status; "
                    "$mtu = (Get-NetIPInterface -InterfaceAlias $name -AddressFamily IPv4 -ErrorAction SilentlyContinue).NlMtu; "
                    "if (-not $mtu) { $mtu = $_.MtuSize }; "
                    "$name + '|' + $status + '|' + $mtu "
                    "}"
                ]
                output = subprocess.check_output(cmd, creationflags=CREATE_NO_WINDOW).decode('mbcs', errors='ignore').strip()
                for line in output.splitlines():
                    if line.strip():
                        parts = line.split('|')
                        if len(parts) >= 3: 
                            self.after(0, lambda n=parts[0], s=parts[1], m=parts[2]: self.mtk_tree.insert("", "end", values=(n, s, m)))
            except: pass
        threading.Thread(target=task, daemon=True).start()

    def mtk_action(self, action):
        sel = self.mtk_tree.selection()
        if sel: 
            name = self.mtk_tree.item(sel[0])["values"][0]
            subprocess.run(f'netsh interface set interface "{name}" admin={action}', shell=True, creationflags=CREATE_NO_WINDOW)
            self.sys_mtk_refresh()

    def set_mtu(self):
        sel = self.mtk_tree.selection()
        if sel:
            name = self.mtk_tree.item(sel[0])["values"][0]
            v = CustomInputDialog(self, "Set MTU", f"New IPv4 MTU for:\n{name} (Min: 352)")
            if v.result and not (v.result.isdigit() and int(v.result) >= 352):
                messagebox.showerror("Invalid MTU", "MTU must be a number of at least 352.")
                return
            if v.result and v.result.isdigit() and int(v.result) >= 352:
                def update_task():
                    try:
                        cmd = f'powershell -NoProfile -Command "netsh interface ipv4 set subinterface \'{name}\' mtu={v.result} store=persistent"'
                        subprocess.run(cmd, shell=True, creationflags=CREATE_NO_WINDOW)
                        time.sleep(0.5)
                        self.sys_mtk_refresh()
                    except: pass
                threading.Thread(target=update_task, daemon=True).start()

    def sys_show_info_custom(self):
        u = platform.uname()
        txt = f"System: {u.system}\nNode Name: {u.node}\nRelease: {u.release}\nVersion: {u.version}\nMachine: {u.machine}\nProcessor: {u.processor}"
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.configure(bg='#121212', highlightthickness=1, highlightbackground="#FFA500")
        win.geometry(f"350x400+{self.winfo_x() - 25}+{self.winfo_y() + 100}")
        tk.Label(win, text="System Info", bg="#121212", fg="#FFA500", font=("Arial", 14, "bold")).pack(pady=20)
        tk.Label(win, text=txt, bg="#121212", fg="white", font=("Consolas", 10), justify="center", wraplength=330).pack(expand=True, fill="both", padx=10)
        tk.Button(win, text="CLOSE", bg="#CC0303", fg="white", bd=0, font=("Arial", 12, "bold"), command=win.destroy).pack(fill="x", side="bottom", pady=20, padx=20)

    # ================= PROGRAMS LOGIC =================
    def setup_programs_tab(self):
        tk.Label(self.tab_prog, text="CUSTOM PROGRAM LAUNCHER", bg="#121212", fg="#FFA500", font=("Arial", 10, "bold")).pack(pady=10)
        
        add_f = tk.LabelFrame(self.tab_prog, text=" Add New Program ", bg="#121212", fg="white", bd=1)
        add_f.pack(fill="x", padx=15, pady=5)
        
        tk.Label(add_f, text="Name:", bg="#121212", fg="white", font=("Arial", 8)).pack(anchor="w", padx=5)
        self.prog_name_ent = tk.Entry(add_f, bg="#333333", fg="white", bd=0)
        self.prog_name_ent.pack(fill="x", padx=5, pady=2, ipady=3)
        
        tk.Label(add_f, text="Path:", bg="#121212", fg="white", font=("Arial", 8)).pack(anchor="w", padx=5)
        f_p = tk.Frame(add_f, bg="#121212")
        f_p.pack(fill="x", padx=5, pady=2)
        
        self.prog_path_ent = tk.Entry(f_p, bg="#333333", fg="white", bd=0)
        self.prog_path_ent.pack(side="left", fill="x", expand=True, ipady=3)
        tk.Button(f_p, text="...", command=lambda: self.prog_path_ent.insert(0, filedialog.askopenfilename()), **self.btn_style).pack(side="right", padx=2)
        
        tk.Button(add_f, text="ADD PROGRAM", command=self.add_prog_manual, bg="#FFA500", fg="black", font=("Arial", 9, "bold")).pack(fill="x", padx=5, pady=10)
        
        list_f = tk.LabelFrame(self.tab_prog, text=" Your Programs ", bg="#121212", fg="#cccccc", bd=1)
        list_f.pack(fill="both", expand=True, padx=15, pady=10)
        
        self.prog_canvas = tk.Canvas(list_f, bg="#121212", bd=0, highlightthickness=0)
        sc = ttk.Scrollbar(list_f, orient="vertical", command=self.prog_canvas.yview)
        self.prog_frame = tk.Frame(self.prog_canvas, bg="#121212")
        
        self.prog_frame.bind("<Configure>", lambda e: self.prog_canvas.configure(scrollregion=self.prog_canvas.bbox("all")))
        self.prog_canvas.create_window((0,0), window=self.prog_frame, anchor="nw", width=250)
        self.prog_canvas.configure(yscrollcommand=sc.set)
        
        self.prog_canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        sc.pack(side="right", fill="y")
        
        tk.Button(self.tab_prog, text="WINDOW ENFORCER", command=lambda: EnforcerWindow(self), **self.btn_style).pack(side="bottom", fill="x", padx=15, pady=10)

    def load_programs(self):
        for w in self.prog_frame.winfo_children(): w.destroy()
        if os.path.exists(self.prog_file):
            with open(self.prog_file) as f:
                for l in f:
                    if "|" in l:
                        n, p = l.strip().split("|", 1)
                        btn = tk.Button(self.prog_frame, text=n, command=lambda path=p: os.startfile(path), **self.btn_style)
                        btn.pack(fill="x", pady=2)
                        btn.bind("<Button-3>", lambda e, name=n, path=p: self.del_program(name, path))

    def add_prog_manual(self):
        n, p = self.prog_name_ent.get().strip(), self.prog_path_ent.get().strip()
        if n and p:
            with open(self.prog_file, "a") as f: f.write(f"{n}|{p}\n")
            self.load_programs()
            self.prog_name_ent.delete(0,tk.END)
            self.prog_path_ent.delete(0,tk.END)

    def del_program(self, name, path):
        lines = []
        if os.path.exists(self.prog_file):
            with open(self.prog_file, "r") as f: lines = f.readlines()
        with open(self.prog_file, "w") as f:
            for line in lines:
                if line.strip() != f"{name}|{path}": f.write(line)
        self.load_programs()

    # ================= HELP WINDOW LOGIC =================
    def show_detailed_help(self):
        hw = tk.Toplevel(self)
        hw.overrideredirect(True)
        hw.configure(bg='#121212', highlightbackground="#FFA500", highlightthickness=1)
        hw.geometry(f"340x550+{self.winfo_x() - 20}+{self.winfo_y() + 50}")
        header = tk.Frame(hw, bg="#FFA500"); header.pack(fill="x")
        tk.Label(header, text="AutoSys Guide", bg="#FFA500", fg="black", font=("Arial", 12, "bold"), pady=10).pack()
        content = tk.Frame(hw, bg="#121212"); content.pack(fill="both", expand=True, padx=15, pady=10)
        
        lines = [
            ("MAIN TAB:", "#FFA500"), ("- Add IPs to list (Name support)", "white"), ("- 'Open External CMD Ping': Visible Pings", "white"), 
            ("SYSTEM TAB:", "#FFA500"), ("- Quick Access to TaskMgr, Regedit...", "white"), ("- Open CMD / Gpedit / Msconfig", "white"), ("- MTK: Enable/Disable Network Adapters\n", "white"), 
            ("STARTUP TAB:", "#FFA500"), ("- Manage programs starting with Windows", "white"), ("- Create Shortcuts/BAT (with Delay)\n", "white"), 
            ("PROGRAMS TAB:", "#FFA500"), ("- Add shortcut buttons to any EXE/File", "white"), ("- Right-click button to delete\n", "white"), 
            ("HOTKEYS:", "#FFA500"), ("F4: Toggle Ghost Mode (Hide/Show)", "white")
        ]
        
        for text, color in lines: 
            tk.Label(content, text=text, bg="#121212", fg=color, font=("Consolas", 9, "bold" if color=="#FFA500" else "normal"), anchor="w", justify="left").pack(fill="x")
            
        tk.Frame(hw, bg="#333333", height=1).pack(fill="x", padx=15, pady=5)
        self.startup_var = tk.BooleanVar(value=os.path.exists(self.startup_path_script))
        tk.Checkbutton(hw, text="Run AutoSys on Windows Startup", variable=self.startup_var, command=self.on_startup_checkbox_click, bg="#121212", fg="white", selectcolor="#333333", activebackground="#121212", activeforeground="white", font=("Arial", 9, "bold")).pack(anchor="w", padx=15, pady=5)
        tk.Button(hw, text="CLOSE HELP", bg="#CC0303", fg="white", bd=0, font=("Arial", 10, "bold"), command=hw.destroy).pack(fill="x", padx=15, pady=(5,0), ipady=5)
        tk.Label(hw, text="Created by oT", bg="#121212", fg="#555555", font=("Arial", 8)).pack(pady=(5,10))
        
        def start_move(e): hw.x = e.x; hw.y = e.y
        def do_move(e): hw.geometry(f"+{hw.winfo_x() + e.x - hw.x}+{hw.winfo_y() + e.y - hw.y}")
        header.bind('<Button-1>', start_move)
        header.bind('<B1-Motion>', do_move)

    def on_startup_checkbox_click(self):
        if self.startup_var.get():
            try:
                s = os.path.abspath(sys.argv[0])
                with open(self.startup_path_script, "w") as f:
                    if getattr(sys, 'frozen', False):
                        f.write(f'@echo off\nstart "" "{sys.executable}"')
                    else:
                        f.write(f'@echo off\nstart "" "{sys.executable.replace("python.exe", "pythonw.exe")}" "{s}"')
            except: pass
        else:
            try: os.remove(self.startup_path_script)
            except: pass


    # ================= Loading Logic =================
    def load_ips(self):
        if os.path.exists(self.db_file): 
            for l in open(self.db_file):
                if l.strip(): self.ip_listbox.insert(tk.END, f"[ ] {l.strip()}")
        else:
            with open(self.db_file, "w") as f:
                f.write("192.168.1.100 - Camera\n10.0.0.5 - Server Room\n")
            for line in ["192.168.1.100 - Camera", "10.0.0.5 - Server Room"]: 
                self.ip_listbox.insert(tk.END, f"[ ] {line}")

def run_as_admin():
    script = os.path.abspath(sys.argv[0])
    params = ' '.join([f'"{script}"'] + sys.argv[1:])
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    except Exception as e:
        with open("crash_log.txt", "w") as f: f.write(str(e))
    sys.exit()

if __name__ == "__main__":
    try:
        if ctypes.windll.shell32.IsUserAnAdmin(): 
            app = AutoSysApp()
            app.mainloop()
        else: 
            run_as_admin()
    except Exception as e:
        with open("crash_log.txt", "w") as f:
            import traceback
            traceback.print_exc(file=f)