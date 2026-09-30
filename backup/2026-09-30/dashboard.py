import customtkinter as ctk
import tkinter as tk
import subprocess
import threading
import socket
import os
import concurrent.futures
import hmac
import hashlib
import time
import base64
import io
import re
import csv
from PIL import Image

ctk.set_appearance_mode("Dark")

COLORS = {
    "BG_MAIN": "#121212",
    "ACCENT": "#389379",      
    "TEXT_WHITE": "#FFFFFF",
    "BTN_BASE": "#333333",    
    "QUIT_BTN": "#aa2222",
    "OFFLINE": "#551111",     
    "SELECTED": "#1F538D",    
    "GRID_LINE": "#1A1A1A",
    "CELL_BG": "#222222",
    "LINK_COLOR": "#FF6600",
    "HEALTH_1G": "#00E676",   
    "HEALTH_100M": "#FFD600", 
    "HEALTH_ERR": "#E53935"   
}

FILE = "network_map.txt"
MAC_FILE = "mac_list.txt"
AGENT_PORT = 5555
SECRET_KEY = b"oT_Secr3tK3y_2026!"
CELL_W, CELL_H = 150, 90

class PsToolsDashboard:
    def __init__(self, root):
        self.root = root
        self.grid_rows, self.grid_cols = 6, 5
        self.nodes = {}
        self.connections = set()
        self.node_status = {} 
        self.ip_coords = {}   
        self.mac_addresses = {}
        
        # הגדרות SNMP מתוך הקובץ
        self.snmp_ver = "v2c"
        self.snmp_user = "public"
        self.snmp_pass = ""
        
        self.target_ip = None
        self.target_name = None
        self.tooltip_win = None
        
        self.win_w = "1250"
        self.win_h = "750"
        self.is_hidden = False

        self._load_data_quiet() 
        
        self.root.geometry(f"{self.win_w}x{self.win_h}")
        self.root.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        self.root.overrideredirect(True)

        try:
            import keyboard
            keyboard.add_hotkey("f4", lambda: self.root.after(0, self.toggle_visibility))
        except:
            self.root.bind_all("<F4>", self.toggle_visibility)

        self._load_mac_list()
        self._setup_ui()
        for warning in self.load_warnings: self.log(warning)
        self._draw_graph()
        self.running = True

    def _setup_ui(self):
        self.title_bar = ctk.CTkFrame(self.root, height=40, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        self.title_bar.pack(side="top", fill="x")
        ctk.CTkLabel(self.title_bar, text="PSTOOLS - DASHBOARD V3.0", font=("Consolas", 14, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=20)
        
        ctk.CTkButton(self.title_bar, text="Quit", width=60, height=30, corner_radius=0, fg_color=COLORS["QUIT_BTN"], hover_color="red", command=self.on_close, font=("Consolas", 11, "bold")).pack(side="right", padx=5, pady=5)
        ctk.CTkButton(self.title_bar, text="Help", width=60, height=30, corner_radius=0, fg_color=COLORS["BTN_BASE"], command=self.show_help, font=("Consolas", 11, "bold")).pack(side="right", padx=5, pady=5)

        def start_move(e): self.x, self.y = e.x, e.y
        def do_move(e): self.root.geometry(f"+{self.root.winfo_x() + e.x - self.x}+{self.root.winfo_y() + e.y - self.y}")
        self.title_bar.bind("<ButtonPress-1>", start_move); self.title_bar.bind("<B1-Motion>", do_move)

        ctrl_panel = ctk.CTkFrame(self.root, fg_color="transparent")
        ctrl_panel.pack(fill="x", padx=10, pady=5)

        power_frame = tk.LabelFrame(ctrl_panel, text="Power Control", bg=COLORS["BG_MAIN"], fg=COLORS["ACCENT"], bd=1, font=("Consolas", 10))
        power_frame.pack(side="left", padx=5, fill="y")
        
        p_ops = [("Wake", self.handle_wake_single), ("Shutdown", self.handle_shutdown_single), ("Restart", self.handle_restart_single)]
        for i, (t, c) in enumerate(p_ops):
            ctk.CTkButton(power_frame, text=t, command=c, corner_radius=0, width=80, height=30, fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"], font=("Consolas", 11, "bold")).grid(row=0, column=i, padx=5, pady=5)

        p_all = [("Wake All", self.handle_wake_all), ("Shutdown All", self.handle_shutdown_all), ("Restart All", self.handle_restart_all)]
        for i, (t, c) in enumerate(p_all):
            ctk.CTkButton(power_frame, text=t, command=c, corner_radius=0, width=80, height=30, fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"], font=("Consolas", 11, "bold")).grid(row=1, column=i, padx=5, pady=5)

        tool_frame = tk.LabelFrame(ctrl_panel, text="Tools", bg=COLORS["BG_MAIN"], fg=COLORS["ACCENT"], bd=1, font=("Consolas", 10))
        tool_frame.pack(side="left", padx=15, fill="y")
        
        t_ops = [("Ping", self.cmd_ping), ("Ping All", self.ping_all), 
                 ("Shares", self.cmd_shares), ("Screen", self.cmd_screenshot)]
        
        for i, (t, c) in enumerate(t_ops):
            ctk.CTkButton(tool_frame, text=t, command=c, corner_radius=0, width=70, height=30, fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"], font=("Consolas", 11, "bold")).grid(row=i//4, column=i%4, padx=5, pady=5)

        group_frame = tk.LabelFrame(ctrl_panel, text="Group Filter", bg=COLORS["BG_MAIN"], fg=COLORS["ACCENT"], bd=1, font=("Consolas", 10))
        group_frame.pack(side="left", padx=10, fill="y")
        
        self.group_mode_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(group_frame, text="Apply 'All' to Group:", variable=self.group_mode_var, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 11, "bold"), corner_radius=0).pack(side="left", padx=(10, 5), pady=15)
        
        unique_groups = sorted(list(set(n["group"] for n in self.nodes.values())))
        self.group_cb_var = ctk.StringVar(value=unique_groups[0] if unique_groups else "General")
        self.group_cb = ctk.CTkComboBox(group_frame, variable=self.group_cb_var, values=unique_groups, width=120, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", dropdown_fg_color="#222222", button_color=COLORS["BTN_BASE"], button_hover_color=COLORS["ACCENT"])
        self.group_cb.pack(side="left", padx=(5, 10), pady=15)

        self.target_lbl = ctk.CTkLabel(ctrl_panel, text="TARGET: NONE", font=("Consolas", 14, "bold"), text_color="white", justify="right")
        self.target_lbl.pack(side="right", fill="x", expand=True, padx=20, anchor="e")

        main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        left_frame = ctk.CTkFrame(main_frame, fg_color="transparent", width=350)
        left_frame.pack(side="left", fill="y", padx=(0, 10))
        left_frame.pack_propagate(False)

        tk.Label(left_frame, text="TERMINAL LOG", bg=COLORS["BG_MAIN"], fg=COLORS["ACCENT"], font=("Consolas", 11, "bold")).pack(anchor="w", pady=(0, 5))
        
        self.log_box = ctk.CTkTextbox(left_frame, fg_color="#000000", text_color=COLORS["ACCENT"], font=("Consolas", 10), corner_radius=0, border_width=1, border_color="#222222")
        self.log_box.pack(fill="both", expand=True)

        btn_clear = ctk.CTkButton(left_frame, text="CLEAR LOG", command=lambda: self.log_box.delete("1.0", "end"), corner_radius=0, height=30, fg_color=COLORS["BTN_BASE"], font=("Consolas", 11, "bold"))
        btn_clear.pack(fill="x", pady=(5, 0))

        self.grid_frame = ctk.CTkFrame(main_frame, fg_color="transparent", corner_radius=0)
        self.grid_frame.pack(side="left", fill="both", expand=True)
        
        self.canvas = tk.Canvas(self.grid_frame, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        
        ctk.CTkLabel(self.root, text="oT", font=("Consolas", 10), text_color="#333333").place(relx=0.99, rely=0.99, anchor="se")

    def _show_tooltip(self, event, text):
        self._hide_tooltip() 
        x = event.x_root + 15
        y = event.y_root + 15
        
        self.tooltip_win = tk.Toplevel(self.root)
        self.tooltip_win.wm_overrideredirect(True)
        self.tooltip_win.wm_geometry(f"+{x}+{y}")
        self.tooltip_win.attributes("-topmost", True)
        
        lbl = tk.Label(self.tooltip_win, text=text, justify='left',
                       background=COLORS["BG_MAIN"], foreground="white", 
                       highlightbackground=COLORS["ACCENT"], highlightthickness=1,
                       font=("Consolas", 10))
        lbl.pack(ipadx=10, ipady=5)

    def _hide_tooltip(self, event=None):
        if self.tooltip_win:
            self.tooltip_win.destroy()
            self.tooltip_win = None

    def show_help(self):
        pop = ctk.CTkToplevel(self.root)
        pop.geometry("400x250")
        pop.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 200
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 125
        pop.geometry(f"+{x}+{y}")
        
        hdr = ctk.CTkFrame(pop, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="HELP", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="Quit", width=40, height=30, command=pop.destroy, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
        
        help_text = "DASHBOARD V3.0 HOTKEYS & USAGE:\n\n- Press F4 to hide/show the Dashboard window.\n- Click a node on the grid to set as TARGET.\n- Double-Click a Switch to open its Health Panel.\n- In Switch Panel, click 'LIVE HEALTH' for SNMP.\n- Passwords required for critical power actions."
        ctk.CTkLabel(pop, text=help_text, font=("Consolas", 11), text_color="white", justify="left").pack(pady=20, padx=20, anchor="w")

    def _get_snmp_auth_data(self):
        """ מחזיר אובייקט אימות בהתאם לגרסה שנשמרה בקובץ ההגדרות """
        from pysnmp.hlapi import CommunityData, UsmUserData
        if self.snmp_ver == "v3":
            if self.snmp_pass:
                return UsmUserData(self.snmp_user, authKey=self.snmp_pass)
            else:
                return UsmUserData(self.snmp_user)
        else:
            return CommunityData(self.snmp_user)

    def show_switch_panel(self, ip, data):
        pop = ctk.CTkToplevel(self.root)
        pop.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        pop.is_open = True
        
        pop.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        
        w, h = 920, 310
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        pop.geometry(f"{w}x{h}+{x}+{y}")
        
        hdr = ctk.CTkFrame(pop, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text=f"HARDWARE PANEL - {data['label']} ({ip})", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        
        def close_panel():
            self._hide_tooltip()
            pop.is_open = False
            pop.destroy()
            
        ctk.CTkButton(hdr, text="Quit", width=40, height=30, command=close_panel, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
        ctk.CTkButton(hdr, text="Help", width=40, height=30, command=self.show_help, fg_color=COLORS["BTN_BASE"], corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=5)
        
        def p_start(e): pop.x, pop.y = e.x, e.y
        def p_move(e): pop.geometry(f"+{pop.winfo_x() + e.x - pop.x}+{pop.winfo_y() + e.y - pop.y}")
        hdr.bind("<ButtonPress-1>", p_start); hdr.bind("<B1-Motion>", p_move)

        canvas = tk.Canvas(pop, bg="#111111", highlightthickness=1, highlightbackground="#333333")
        canvas.pack(fill="both", expand=True, padx=20, pady=(15, 5))
        
        footer = ctk.CTkFrame(pop, fg_color="transparent", height=40)
        footer.pack(fill="x", padx=20, pady=(0, 10))
        
        btn_scan = ctk.CTkButton(footer, text="LIVE HEALTH CHECK (SNMP)", fg_color="#1F538D", hover_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 11, "bold"), corner_radius=0, width=200, height=30)
        btn_scan.pack(side="left")

        legend_frame = tk.Frame(footer, bg=COLORS["BG_MAIN"])
        legend_frame.pack(side="right", pady=5)
        tk.Label(legend_frame, text="■ 1Gbps UP", fg=COLORS["HEALTH_1G"], bg=COLORS["BG_MAIN"], font=("Consolas", 10, "bold")).pack(side="left", padx=5)
        tk.Label(legend_frame, text="■ 10/100M UP", fg=COLORS["HEALTH_100M"], bg=COLORS["BG_MAIN"], font=("Consolas", 10, "bold")).pack(side="left", padx=5)
        tk.Label(legend_frame, text="■ ERROR", fg=COLORS["HEALTH_ERR"], bg=COLORS["BG_MAIN"], font=("Consolas", 10, "bold")).pack(side="left", padx=5)
        tk.Label(legend_frame, text="■ DOWN", fg="#444444", bg=COLORS["BG_MAIN"], font=("Consolas", 10, "bold")).pack(side="left", padx=5)

        connected_ips = []
        for c_ip1, c_ip2 in self.connections:
            if c_ip1 == ip and c_ip2 in self.nodes: connected_ips.append(c_ip2)
            elif c_ip2 == ip and c_ip1 in self.nodes: connected_ips.append(c_ip1)
        connected_ips.sort()
        
        cols = 24
        start_x = 30
        start_y = 35
        port_w, port_h = 24, 24
        gap_x, gap_y = 4, 4
        group_gap = 14
        
        port_index = 0
        port_rects = {} 
        
        for c in range(cols):
            group = c // 4
            x = start_x + (c * (port_w + gap_x)) + (group * group_gap)
            
            for r in range(2):
                y = start_y + (r * (port_h + gap_y))
                is_active = port_index < len(connected_ips)
                
                inner_color = COLORS["ACCENT"] if is_active else "#1a1a1a"
                border_color = COLORS["ACCENT"] if is_active else "#444444"
                
                port_tag = f"port_{port_index}"
                outer_id = canvas.create_rectangle(x, y, x+port_w, y+port_h, fill="#000000", outline=border_color, width=1, tags=port_tag)
                inner_id = canvas.create_rectangle(x+5, y+5, x+port_w-5, y+port_h-3, fill=inner_color, outline="", tags=port_tag)
                
                port_rects[port_index] = {"outer": outer_id, "inner": inner_id, "tag": port_tag, "is_active_config": is_active}
                
                if is_active:
                    conn_ip = connected_ips[port_index]
                    conn_name = self.nodes[conn_ip]['label']
                    conn_wall = self.nodes[conn_ip].get('wall', '')
                    t_text = f"TARGET: {conn_name}\nIP: [{conn_ip}]"
                    if conn_wall: t_text += f"\nWall: {conn_wall}"
                    t_text += "\n\nHealth: Not Scanned"
                    
                    canvas.tag_bind(port_tag, "<Enter>", lambda e, t=t_text: self._show_tooltip(e, t))
                    canvas.tag_bind(port_tag, "<Leave>", self._hide_tooltip)
                else:
                    canvas.tag_bind(port_tag, "<Enter>", lambda e, p=port_index+1: self._show_tooltip(e, f"Port {p}\nStatus: Unknown"))
                    canvas.tag_bind(port_tag, "<Leave>", self._hide_tooltip)
                
                port_num = (c * 2) + 1 if r == 0 else (c * 2) + 2
                text_y = y - 10 if r == 0 else y + port_h + 10
                canvas.create_text(x + port_w/2, text_y, text=str(port_num), fill="#888888", font=("Consolas", 8))
                port_index += 1

        def run_health_scan():
            btn_scan.configure(text="SCANNING...", fg_color="#D4A017", text_color="black", state="disabled")
            
            def scan_thread():
                snmp_success = False
                real_port_data = {}
                
                try:
                    from pysnmp.hlapi import SnmpEngine, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, nextCmd
                    auth_data = self._get_snmp_auth_data()
                    
                    for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
                        SnmpEngine(), auth_data, UdpTransportTarget((ip, 161), timeout=1.5, retries=0),
                        ContextData(), ObjectType(ObjectIdentity('1.3.6.1.2.1.2.2.1.8')), lexicographicMode=False
                    ):
                        if errorIndication or errorStatus:
                            break 
                        snmp_success = True
                        for varBind in varBinds:
                            p_num = int(varBind[0].prettyPrint().split('.')[-1])
                            if p_num <= 48: real_port_data[p_num] = {'status': int(varBind[1]), 'speed': 0}
                            
                    if snmp_success:
                        for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
                            SnmpEngine(), auth_data, UdpTransportTarget((ip, 161), timeout=1.5, retries=0),
                            ContextData(), ObjectType(ObjectIdentity('1.3.6.1.2.1.2.2.1.5')), lexicographicMode=False
                        ):
                            if not (errorIndication or errorStatus):
                                for varBind in varBinds:
                                    p_num = int(varBind[0].prettyPrint().split('.')[-1])
                                    if p_num in real_port_data: real_port_data[p_num]['speed'] = int(varBind[1])
                except Exception:
                    pass

                self.root.after(0, lambda: apply_health_data(snmp_success, real_port_data))

            threading.Thread(target=scan_thread, daemon=True).start()

        def apply_health_data(snmp_success, real_port_data):
            if not getattr(pop, 'is_open', False): return
            
            if not snmp_success:
                self.log(f"[V3.0] No SNMP response from {ip}. Port health unknown.")
                btn_scan.configure(text="NO SNMP RESPONSE", fg_color=COLORS["HEALTH_ERR"], text_color="white")
            else:
                self.log(f"[V3.0] SNMP Live Health Scan Complete for {ip}.")
                btn_scan.configure(text="SCAN COMPLETE", fg_color=COLORS["ACCENT"])

            for p_idx, rects in port_rects.items():
                port_num = p_idx + 1
                data = real_port_data.get(port_num) if snmp_success else None

                if data is None:
                    status_txt, speed_txt = "UNKNOWN (No SNMP data)", "N/A"
                else:
                    if data['status'] == 1:
                        speed = data['speed'] / 1000000
                        if speed >= 1000:
                            new_fill, new_outline, status_txt, speed_txt = COLORS["HEALTH_1G"], COLORS["HEALTH_1G"], "UP (Active)", "1 Gbps"
                        else:
                            new_fill, new_outline, status_txt, speed_txt = COLORS["HEALTH_100M"], COLORS["HEALTH_100M"], "UP (Degraded)", f"{int(speed)} Mbps"
                    else:
                        new_fill, new_outline, status_txt, speed_txt = "#1a1a1a", "#444444", "DOWN", "N/A"

                    canvas.itemconfig(rects["inner"], fill=new_fill)
                    canvas.itemconfig(rects["outer"], outline=new_outline)

                if rects["is_active_config"]:
                    conn_ip = connected_ips[p_idx]
                    conn_name = self.nodes[conn_ip]['label']
                    conn_wall = self.nodes[conn_ip].get('wall', '')
                    t_text = f"TARGET: {conn_name}\nIP: [{conn_ip}]"
                    if conn_wall: t_text += f"\nWall: {conn_wall}"
                    t_text += f"\n\n--- HEALTH ---\nStatus: {status_txt}\nSpeed: {speed_txt}"
                else:
                    t_text = f"Port {port_num}\nStatus: {status_txt}"
                    
                canvas.tag_bind(rects["tag"], "<Enter>", lambda e, t=t_text: self._show_tooltip(e, t))

            self.root.after(3000, lambda: btn_scan.configure(text="LIVE HEALTH CHECK (SNMP)", fg_color="#1F538D", text_color="white", state="normal") if getattr(pop, 'is_open', False) else None)

        btn_scan.configure(command=run_health_scan)

    def log(self, msg):
        self.log_box.insert("end", f"> {msg}\n")
        self.log_box.see("end")

    def _load_mac_list(self):
        self.mac_addresses.clear()
        if not os.path.exists(MAC_FILE): return
        try:
            with open(MAC_FILE, 'r') as f:
                for line in f:
                    parts = line.replace(',', ' ').split()
                    if len(parts) >= 2:
                        ip = parts[0].strip()
                        mac = parts[1].strip()
                        self.mac_addresses[ip] = mac
        except: pass

    def get_mac_from_ip(self, ip):
        try:
            arp_result = subprocess.check_output(["arp", "-a", ip]).decode(errors="ignore")
            match = re.search(r"([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})", arp_result)
            if match: return match.group(0)
        except: pass
        return None

    def send_magic_packet(self, mac):
        try:
            mac_clean = re.sub(r'[^a-fA-F0-9]', '', str(mac))
            if len(mac_clean) != 12: return False
            mac_bytes = bytes.fromhex(mac_clean)
            packet = b'\xff' * 6 + mac_bytes * 16
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(packet, ('255.255.255.255', 9))
            return True
        except: return False

    def verify_and_execute(self, action_name, action_cmd, require_target=False):
        if require_target and not self.target_ip:
            self.log(f"ERROR: Cannot execute '{action_name}'. No target selected.")
            return
        def on_confirm(): action_cmd()
        def on_password_success(): self.show_confirm_dialog(action_name, on_confirm)
        self.show_password_dialog(on_password_success)

    def show_password_dialog(self, success_callback):
        pop = ctk.CTkToplevel(self.root)
        pop.geometry("350x200")
        pop.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 175
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 100
        pop.geometry(f"+{x}+{y}")
        
        hdr = ctk.CTkFrame(pop, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="SECURITY VERIFICATION", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="Quit", width=30, height=30, command=pop.destroy, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
        
        ctk.CTkLabel(pop, text="ENTER ADMIN PASSWORD:", font=("Consolas", 12, "bold"), text_color="white").pack(pady=(20, 10))
        
        pw_entry = ctk.CTkEntry(pop, show="*", width=200, corner_radius=0, fg_color="#202020", border_width=0, text_color="white")
        pw_entry.pack(pady=5)
        pw_entry.focus()
        
        err_lbl = ctk.CTkLabel(pop, text="", text_color="red", font=("Consolas", 10))
        err_lbl.pack()
        
        def check_pw(event=None):
            if pw_entry.get() == "oT": pop.destroy(); success_callback()
            else: err_lbl.configure(text="ACCESS DENIED"); pw_entry.delete(0, 'end')
                
        pw_entry.bind("<Return>", check_pw)
        ctk.CTkButton(pop, text="VERIFY", command=check_pw, fg_color=COLORS["ACCENT"], text_color="black", font=("Consolas", 12, "bold"), corner_radius=0, width=150).pack(pady=10)

    def show_confirm_dialog(self, action_name, confirm_callback):
        pop = ctk.CTkToplevel(self.root)
        pop.geometry("400x200")
        pop.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 200
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 100
        pop.geometry(f"+{x}+{y}")
        
        hdr = ctk.CTkFrame(pop, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="ACTION CONFIRMATION", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="Quit", width=30, height=30, command=pop.destroy, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
        
        ctk.CTkLabel(pop, text=f"WARNING: You are about to execute:\n\n[ {action_name} ]\n\nAre you sure you want to proceed?", font=("Consolas", 12, "bold"), text_color="white", justify="center").pack(pady=(20, 20))
        
        btn_frame = ctk.CTkFrame(pop, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20)
        
        def execute(): pop.destroy(); confirm_callback()
            
        ctk.CTkButton(btn_frame, text="CANCEL", command=pop.destroy, fg_color=COLORS["BTN_BASE"], text_color="white", font=("Consolas", 12, "bold"), corner_radius=0, width=120).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="EXECUTE", command=execute, fg_color=COLORS["QUIT_BTN"], text_color="white", font=("Consolas", 12, "bold"), corner_radius=0, width=120).pack(side="right", padx=10)

    def _load_data_quiet(self):
        self.load_warnings = []
        if not os.path.exists(FILE): return
        self.nodes.clear()
        self.connections.clear()
        with open(FILE, "r", newline="") as f:
            for line_no, row in enumerate(csv.reader(f, skipinitialspace=True), 1):
                p = [x.strip() for x in row]
                if not p or not p[0]: continue
                try:
                    if p[0] == "CONFIG: SIZE":
                        if len(p) >= 3:
                            self.win_w = str(int(p[1]))
                            self.win_h = str(int(p[2]))
                    elif p[0] == "CONFIG: GRID":
                        self.grid_rows, self.grid_cols = int(p[1]), int(p[2])
                    elif p[0] == "CONFIG: LINK":
                        if len(p) >= 3:
                            self.connections.add(tuple(sorted([p[1], p[2]])))
                    elif p[0] == "CONFIG: SNMP":
                        if len(p) >= 4:
                            self.snmp_ver, self.snmp_user, self.snmp_pass = p[1], p[2], p[3]
                    elif not p[0].startswith("CONFIG"):
                        label, ip = p[0], p[1]
                        if not ip: raise ValueError("missing IP")
                        r = int(p[2]) if len(p) > 2 else 0
                        c = int(p[3]) if len(p) > 3 else 0
                        s = int(p[4]) if len(p) > 4 else 1
                        dev_type = p[5] if len(p) > 5 else "PC"
                        ping_en = p[6] if len(p) > 6 else "True"
                        grp = p[7] if len(p) > 7 else "General"
                        wall = p[8] if len(p) > 8 else ""
                        self.nodes[ip] = {"label": label, "r": r, "c": c, "s": s, "type": dev_type, "ping_en": ping_en, "group": grp, "wall": wall}
                        self.node_status[ip] = None
                except (IndexError, ValueError):
                    self.load_warnings.append(f"WARNING: Skipped invalid line {line_no} in {FILE}: {','.join(row)}")

    def _draw_graph(self):
        self.canvas.delete("all")
        for r in range(self.grid_rows):
            for c in range(self.grid_cols):
                self.canvas.create_rectangle(c*CELL_W, r*CELL_H, (c+1)*CELL_W, (r+1)*CELL_H, outline=COLORS["GRID_LINE"], dash=(2,2))
        
        self.ip_coords.clear()
        for ip, data in self.nodes.items():
            r, c, s = data["r"], data["c"], data["s"]
            x1, y1 = c * CELL_W + 10, r * CELL_H + 10
            x2, y2 = c * CELL_W + (CELL_W * s) - 10, r * CELL_H + CELL_H - 10
            self.ip_coords[ip] = {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'cx': (x1 + x2) / 2, 'cy': (y1 + y2) / 2}

        for ip1, ip2 in self.connections:
            if ip1 in self.ip_coords and ip2 in self.ip_coords:
                d1, d2 = self.ip_coords[ip1], self.ip_coords[ip2]
                if d1['cx'] < d2['cx']: sx, sy, ex, ey, dir_s, dir_e = d1['x2'], d1['cy'], d2['x1'], d2['cy'], 1, -1
                elif d1['cx'] > d2['cx']: sx, sy, ex, ey, dir_s, dir_e = d1['x1'], d1['cy'], d2['x2'], d2['cy'], -1, 1
                else: sx, sy, ex, ey, dir_s, dir_e = d1['x2'], d1['cy'], d2['x2'], d2['cy'], 1, 1

                offset = max(abs(ex - sx) * 0.5, 50)
                if d1['cx'] == d2['cx']: offset = max(abs(ey - sy) * 0.4, 50)

                self.canvas.create_line(
                    sx, sy, sx + (offset * dir_s), sy, ex + (offset * dir_e), ey, ex, ey,
                    fill=COLORS["LINK_COLOR"], width=3, smooth=True, splinesteps=36, arrow=tk.LAST, arrowshape=(12, 14, 5)
                )

        for ip, data in self.nodes.items():
            d = self.ip_coords[ip]; node_tag = f"node_{ip}"
            self.canvas.create_rectangle(d['x1'], d['y1'], d['x2'], d['y2'], fill=COLORS["CELL_BG"], outline=COLORS["ACCENT"], width=1, tags=(f"bg_{ip}", node_tag))
            
            if data['type'] == "Switch":
                switch_width = d['x2'] - d['x1']
                num_ports = data['s'] * 4 
                port_spacing = switch_width / (num_ports + 1)
                
                strip_y1 = d['y2'] - 22
                self.canvas.create_rectangle(d['x1']+2, strip_y1, d['x2']-2, d['y2']-2, fill="#111111", outline="#333333", tags=node_tag)
                
                for p in range(1, num_ports + 1):
                    px = d['x1'] + (p * port_spacing)
                    self.canvas.create_rectangle(px-5, d['y2']-18, px+5, d['y2']-6, fill="#222222", outline=COLORS["ACCENT"], tags=node_tag)
                    self.canvas.create_oval(px-1, d['y2']-21, px+1, d['y2']-19, fill=COLORS["ACCENT"], outline="", tags=node_tag)
                    self.canvas.create_line(px-2, d['y2']-12, px+2, d['y2']-12, fill="#555555", tags=node_tag)
            else:
                port_r = 4
                self.canvas.create_oval(d['x1']-port_r, d['cy']-port_r, d['x1']+port_r, d['cy']+port_r, fill="#444444", outline=COLORS["ACCENT"], tags=node_tag)
                self.canvas.create_oval(d['x2']-port_r, d['cy']-port_r, d['x2']+port_r, d['cy']+port_r, fill="#444444", outline=COLORS["ACCENT"], tags=node_tag)
            
            display_text = f"{data['label']}\n{ip}\n[{data['type']}] | {data['group']}"
            if data.get('wall'): display_text += f"\nWall: {data['wall']}"
            if data['ping_en'] == "False": display_text += "\n(No Ping)"
                
            y_text_offset = -10 if data['type'] == "Switch" else 0
            self.canvas.create_text(d['cx'], d['cy'] + y_text_offset, text=display_text, fill="white", font=("Consolas", 10, "bold"), justify="center", tags=(f"txt_{ip}", node_tag))
            
            self.canvas.tag_bind(node_tag, "<Button-1>", lambda e, i=ip, n=data['label']: self.select_target(i, n))
            
            if data['type'] == "Switch":
                self.canvas.tag_bind(node_tag, "<Double-Button-1>", lambda e, i=ip, d_info=data: self.show_switch_panel(i, d_info))
            
            self._refresh_btn_visuals(ip)

    def select_target(self, ip, name):
        if self.target_ip == ip:
            self.target_ip, self.target_name = None, None
            self.target_lbl.configure(text="TARGET: NONE")
        else:
            self.target_ip, self.target_name = ip, name
            self.target_lbl.configure(text=f"TARGET:\n{name}\n{ip}")
        for n_ip in self.nodes.keys(): self._refresh_btn_visuals(n_ip)

    def _refresh_btn_visuals(self, ip):
        status = self.node_status.get(ip)
        if status is None: base_bg, base_fg = COLORS["BTN_BASE"], "white"
        elif status is True: base_bg, base_fg = COLORS["ACCENT"], "black"
        else: base_bg, base_fg = COLORS["OFFLINE"], "white"
            
        if self.target_ip == ip:
            self.canvas.itemconfig(f"bg_{ip}", fill=COLORS["SELECTED"], outline=base_bg, width=2)
            self.canvas.itemconfig(f"txt_{ip}", fill="white")
        else:
            self.canvas.itemconfig(f"bg_{ip}", fill=base_bg, outline=COLORS["ACCENT"], width=1)
            self.canvas.itemconfig(f"txt_{ip}", fill=base_fg)

    def _reset_node_visual(self, ip):
        self.node_status[ip] = None
        self._refresh_btn_visuals(ip)

    def _get_bulk_ips(self):
        if self.group_mode_var.get():
            grp = self.group_cb_var.get()
            return [ip for ip, data in self.nodes.items() if data['group'] == grp]
        return list(self.nodes.keys())

    def _get_bulk_action_name(self, base_action):
        if self.group_mode_var.get(): return f"{base_action} GROUP: {self.group_cb_var.get()}"
        return f"{base_action} ALL NODES"

    def handle_shutdown_single(self): self.verify_and_execute("SHUTDOWN TARGET", lambda: self.cmd_power("/s"), require_target=True)
    def handle_restart_single(self): self.verify_and_execute("RESTART TARGET", lambda: self.cmd_power("/r"), require_target=True)
    def handle_wake_single(self): self.verify_and_execute("WAKE TARGET", self.cmd_wake, require_target=True)
    def handle_shutdown_all(self): self.verify_and_execute(self._get_bulk_action_name("SHUTDOWN"), lambda: self.cmd_power_all("/s"))
    def handle_restart_all(self): self.verify_and_execute(self._get_bulk_action_name("RESTART"), lambda: self.cmd_power_all("/r"))
    def handle_wake_all(self): self.verify_and_execute(self._get_bulk_action_name("WAKE"), self.cmd_wake_all)

    def cmd_screenshot(self):
        if not self.target_ip:
            self.log("ERROR: No target selected for Screenshot.")
            return
        self.log(f"Requesting Screenshot from {self.target_ip}...")
        threading.Thread(target=self._send_to_agent, args=(self.target_ip, "SCREENSHOT"), daemon=True).start()

    def cmd_power(self, flag):
        cmd = "SHUTDOWN" if flag == "/s" else "RESTART"
        self.log(f"Sending {cmd} to {self.target_ip}...")
        threading.Thread(target=self._send_to_agent, args=(self.target_ip, cmd), daemon=True).start()

    def cmd_power_all(self, flag):
        ips = self._get_bulk_ips()
        cmd = "SHUTDOWN" if flag == "/s" else "RESTART"
        target_name = f"Group: {self.group_cb_var.get()}" if self.group_mode_var.get() else "All Nodes"
        self.log(f"Sending {cmd} to {len(ips)} nodes ({target_name})...")
        for ip in ips: threading.Thread(target=self._send_to_agent, args=(ip, cmd), daemon=True).start()

    def cmd_wake(self):
        if not self.target_ip: return
        ip = self.target_ip
        mac = self.mac_addresses.get(ip) or self.get_mac_from_ip(ip)
        if not mac:
            self.log(f"ERROR: MAC address unknown for {ip}. Add to 'mac_list.txt'.")
            return
        self.log(f"Sending Magic Packet to {ip} ({mac})...")
        if self.send_magic_packet(mac): self.log(f"SUCCESS: Wake packet sent.")
        else: self.log(f"FAILED to send packet to {ip}.")

    def cmd_wake_all(self):
        ips = self._get_bulk_ips()
        target_name = f"Group: {self.group_cb_var.get()}" if self.group_mode_var.get() else "All Nodes"
        self.log(f"Sending Wake Packets to {len(ips)} nodes ({target_name})...")
        def run():
            count = 0
            for ip in ips:
                mac = self.mac_addresses.get(ip) or self.get_mac_from_ip(ip)
                if mac:
                    self.send_magic_packet(mac)
                    self.root.after(0, self.log, f"Woke {ip} ({mac})")
                    count += 1
                else: self.root.after(0, self.log, f"Skipped {ip} - No MAC address found.")
            self.root.after(0, self.log, f"Wake All complete. Sent to {count} nodes.")
        threading.Thread(target=run, daemon=True).start()

    def cmd_ping(self):
        if not self.target_ip: return
        ip = self.target_ip
        self.log(f"--- Pinging {ip} ---")
        CREATE_NO_WINDOW = 0x08000000 
        def run():
            res = subprocess.run(["ping", "-n", "1", "-w", "500", ip], stdout=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            self.node_status[ip] = (res.returncode == 0)
            self.root.after(0, lambda: self._refresh_btn_visuals(ip))
            self.root.after(5000, lambda: self._reset_node_visual(ip))
            p = subprocess.Popen(["ping", "-n", "4", ip], stdout=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW)
            for line in p.stdout: self.root.after(0, self.log, line.strip())
        threading.Thread(target=run, daemon=True).start()

    def ping_all(self):
        ips = self._get_bulk_ips()
        target_name = f"Group: {self.group_cb_var.get()}" if self.group_mode_var.get() else "All Nodes"
        self.log(f"--- Ping Started ({target_name}) ---")
        CREATE_NO_WINDOW = 0x08000000 
        def run():
            def ping_node(ip):
                if self.nodes[ip]['ping_en'] == "False": return
                res = subprocess.run(["ping", "-n", "1", "-w", "500", ip], stdout=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
                status_bool = (res.returncode == 0)
                self.node_status[ip] = status_bool
                self.root.after(0, lambda: self._refresh_btn_visuals(ip))
                self.root.after(5000, lambda: self._reset_node_visual(ip))
                status_text = "UP" if status_bool else "DOWN"
                self.root.after(0, self.log, f"[{status_text}] {ip}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as exe:
                for ip in ips: exe.submit(ping_node, ip)
        threading.Thread(target=run, daemon=True).start()

    def cmd_shares(self):
        if not self.target_ip: return
        self.log(f"--- Opening Shares: \\\\{self.target_ip} ---")
        subprocess.Popen(f'explorer \\\\{self.target_ip}')

    def _send_to_agent(self, ip, command):
        try:
            timestamp = str(int(time.time()))
            msg_to_sign = f"{timestamp}:{command}"
            signature = hmac.new(SECRET_KEY, msg_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
            payload = f"{timestamp}|{signature}|{command}"

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(20.0) 
                s.connect((ip, AGENT_PORT))
                s.sendall(payload.encode('utf-8'))
                s.shutdown(socket.SHUT_WR)
                
                response_data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk: break
                    response_data += chunk
                
                response = response_data.decode('utf-8')
                
                if command == "SCREENSHOT":
                    if response.startswith("ERROR"):
                        self.root.after(0, self.log, f"Screenshot Error from [{ip}]: {response}")
                    else:
                        self.root.after(0, self.log, f"Screenshot received! Opening viewer...")
                        self.root.after(0, lambda: self.show_screenshot_viewer(ip, response))
                else:
                    self.root.after(0, self.log, f"Agent [{ip}]: {response}")
                    
        except Exception as e:
            self.root.after(0, self.log, f"Failed to reach Agent on {ip}: {e}")

    def show_screenshot_viewer(self, ip, base64_str):
        try:
            img_data = base64.b64decode(base64_str)
            img = Image.open(io.BytesIO(img_data))
            
            pop = ctk.CTkToplevel(self.root)
            pop.geometry("1000x650")
            pop.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
            pop.overrideredirect(True)
            
            hdr = ctk.CTkFrame(pop, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
            hdr.pack(fill="x")
            ctk.CTkLabel(hdr, text=f"REMOTE SCREENSHOT: {ip}", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
            
            ctk.CTkButton(hdr, text="Quit", width=40, height=30, command=pop.destroy, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
            
            def save_img():
                try:
                    filename = f"Screen_{ip}_{int(time.time())}.jpg"
                    img.save(filename)
                    self.log(f"Screenshot saved locally as: {filename}")
                except Exception as e:
                    self.log(f"Error saving image: {e}")
            
            ctk.CTkButton(hdr, text="SAVE TO DISK", width=120, height=30, command=save_img, fg_color=COLORS["SELECTED"], text_color="white", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=10)
            
            def p_start(e): pop.x, pop.y = e.x, e.y
            def p_move(e): pop.geometry(f"+{pop.winfo_x() + e.x - pop.x}+{pop.winfo_y() + e.y - pop.y}")
            hdr.bind("<ButtonPress-1>", p_start); hdr.bind("<B1-Motion>", p_move)

            img_w, img_h = img.size
            ratio = min(960/img_w, 580/img_h)
            new_w, new_h = int(img_w*ratio), int(img_h*ratio)
            
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(new_w, new_h))
            img_lbl = ctk.CTkLabel(pop, text="", image=ctk_img)
            img_lbl.pack(expand=True, pady=10)
            
        except Exception as e:
            self.log(f"Failed to display screenshot: {e}")

    def toggle_visibility(self, event=None):
        if self.root.winfo_viewable(): self.root.withdraw()
        else: self.root.deiconify(); self.root.attributes("-topmost", True); self.root.after(100, lambda: self.root.attributes("-topmost", False))

    def on_close(self):
        self.running = False; self.root.destroy(); os._exit(0)

if __name__ == "__main__":
    root = ctk.CTk()
    PsToolsDashboard(root)
    root.mainloop()