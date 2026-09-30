import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
import subprocess
import threading
import ipaddress
import re
import os
import csv
import winsound

# ===== GUI CONFIGURATION & STYLE =====
ctk.set_appearance_mode("Dark")

COLORS = {
    "BG_MAIN": "#121212",
    "ACCENT": "#389379",
    "BTN_BASE": "#333333",
    "QUIT_BTN": "#aa2222",
    "TEXT_WHITE": "#FFFFFF",
    "GRID_LINE": "#1A1A1A",
    "CELL_BG": "#222222",
    "LINK_SRC": "#FFFFFF"
}

FILE = "network_map.txt"
MAC_FILE = "mac_list.txt"
CELL_W, CELL_H = 150, 90

class PsToolsConfigurator:
    def __init__(self, root):
        self.root = root
        self.root.geometry("1250x730")
        self.root.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        self.root.overrideredirect(True)
        
        self.rows, self.cols = 6, 5
        self.scanning = False
        self.connections = set() 
        self.link_source = None  

        self.win_w_var = ctk.StringVar(value="1250")
        self.win_h_var = ctk.StringVar(value="750")
        
        # הגדרות SNMP חדשות
        self.snmp_ver_var = ctk.StringVar(value="v2c")
        self.snmp_user_var = ctk.StringVar(value="public")
        self.snmp_pass_var = ctk.StringVar(value="")

        try:
            import keyboard
            keyboard.add_hotkey("f4", lambda: self.root.after(0, self.toggle_visibility))
        except:
            self.root.bind_all("<F4>", self.toggle_visibility)

        self.tree = ttk.Treeview()
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        self.header = ctk.CTkFrame(self.root, height=45, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        self.header.pack(side="top", fill="x")
        self.title_lbl = ctk.CTkLabel(self.header, text="PSTOOLS - CONFIGURATOR V3.0", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"])
        self.title_lbl.pack(side="left", padx=20)
        
        ctk.CTkButton(self.header, text="Quit", width=60, height=30, command=self.on_close, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=10)
        ctk.CTkButton(self.header, text="Help", width=60, height=30, fg_color=COLORS["BTN_BASE"], text_color="white", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=5)

        def start_move(e): self.x, self.y = e.x, e.y
        def do_move(e): self.root.geometry(f"+{self.root.winfo_x() + e.x - self.x}+{self.root.winfo_y() + e.y - self.y}")
        self.header.bind("<ButtonPress-1>", start_move); self.header.bind("<B1-Motion>", do_move)

        ctrl = ctk.CTkFrame(self.root, fg_color="transparent")
        ctrl.pack(fill="x", padx=15, pady=10)
        
        row1 = ctk.CTkFrame(ctrl, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 5))
        
        self.range_var = ctk.StringVar(value="192.168.1.0/24")
        ctk.CTkLabel(row1, text="Range:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=5)
        ctk.CTkEntry(row1, textvariable=self.range_var, width=130, corner_radius=0, fg_color="#202020", border_width=0, text_color="white").pack(side="left", padx=5)
        
        self.scan_btn = ctk.CTkButton(row1, text="SCAN NETWORK", command=self.toggle_scan, fg_color=COLORS["BTN_BASE"], corner_radius=0, width=120, text_color="white", font=("Consolas", 11, "bold"))
        self.scan_btn.pack(side="left", padx=10)

        self.automap_btn = ctk.CTkButton(row1, text="AUTO-MAP SWITCHES", command=self.auto_map_switches, fg_color="#1F538D", hover_color=COLORS["ACCENT"], corner_radius=0, width=140, text_color="white", font=("Consolas", 11, "bold"))
        self.automap_btn.pack(side="left", padx=5)

        # שורת ההגדרות החדשה ל-SNMP
        row_snmp = ctk.CTkFrame(ctrl, fg_color="transparent")
        row_snmp.pack(fill="x", pady=(0, 5))
        
        ctk.CTkLabel(row_snmp, text="SNMP Ver:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.snmp_ver_cb = ctk.CTkComboBox(row_snmp, variable=self.snmp_ver_var, values=["v2c", "v3"], width=70, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", dropdown_fg_color="#222222", button_color=COLORS["BTN_BASE"], button_hover_color=COLORS["ACCENT"])
        self.snmp_ver_cb.pack(side="left", padx=5)
        
        ctk.CTkLabel(row_snmp, text="User/Community:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        ctk.CTkEntry(row_snmp, textvariable=self.snmp_user_var, width=120, corner_radius=0, fg_color="#202020", border_width=0, text_color="white").pack(side="left", padx=5)

        ctk.CTkLabel(row_snmp, text="Pass/Auth Key:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        ctk.CTkEntry(row_snmp, textvariable=self.snmp_pass_var, show="*", width=120, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="Leave empty for v2c").pack(side="left", padx=5)

        row2 = ctk.CTkFrame(ctrl, fg_color="transparent")
        row2.pack(fill="x")
        
        ctk.CTkLabel(row2, text="IP:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.manual_ip = ctk.CTkEntry(row2, width=100, corner_radius=0, fg_color="#202020", border_width=0, text_color="white")
        self.manual_ip.pack(side="left", padx=2)

        ctk.CTkLabel(row2, text="MAC:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.manual_mac = ctk.CTkEntry(row2, width=110, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="Optional")
        self.manual_mac.pack(side="left", padx=2)
        
        ctk.CTkLabel(row2, text="Lbl:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.manual_label = ctk.CTkEntry(row2, width=80, corner_radius=0, fg_color="#202020", border_width=0, text_color="white")
        self.manual_label.pack(side="left", padx=2)
        
        ctk.CTkLabel(row2, text="Grp:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.manual_group = ctk.CTkEntry(row2, width=70, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="General")
        self.manual_group.pack(side="left", padx=2)

        ctk.CTkLabel(row2, text="Wall:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.manual_wall = ctk.CTkEntry(row2, width=50, corner_radius=0, fg_color="#202020", border_width=0, text_color="white")
        self.manual_wall.pack(side="left", padx=2)
        
        ctk.CTkLabel(row2, text="Type:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.manual_type = ctk.CTkComboBox(row2, width=75, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", dropdown_fg_color="#222222", button_color=COLORS["BTN_BASE"], button_hover_color=COLORS["ACCENT"], values=["PC", "Router", "Hub", "Switch", "Server", "Printer", "Camera"])
        self.manual_type.set("PC")
        self.manual_type.pack(side="left", padx=2)

        self.ping_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(row2, text="Ping?", variable=self.ping_var, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 11), width=60, corner_radius=0).pack(side="left", padx=10)

        ctk.CTkButton(row2, text="+ ADD", command=self.add_manual, fg_color=COLORS["BTN_BASE"], corner_radius=0, width=60, text_color="white", font=("Consolas", 11, "bold")).pack(side="left", padx=5)

        footer = ctk.CTkFrame(self.root, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=15, pady=15)
        
        size_frame = ctk.CTkFrame(footer, fg_color="transparent")
        size_frame.pack(side="left", padx=(0, 20))
        ctk.CTkLabel(size_frame, text="W:", text_color="white", font=("Consolas", 12, "bold")).pack(side="left", padx=2)
        ctk.CTkEntry(size_frame, textvariable=self.win_w_var, width=50, corner_radius=0, fg_color="#202020", border_width=0, text_color="white").pack(side="left")
        ctk.CTkLabel(size_frame, text="H:", text_color="white", font=("Consolas", 12, "bold")).pack(side="left", padx=(10, 2))
        ctk.CTkEntry(size_frame, textvariable=self.win_h_var, width=50, corner_radius=0, fg_color="#202020", border_width=0, text_color="white").pack(side="left")

        ctk.CTkButton(footer, text="VISUAL EDITOR", command=self.open_visual_editor, height=45, fg_color=COLORS["BTN_BASE"], text_color="white", font=("Consolas", 14, "bold"), corner_radius=0, hover_color=COLORS["ACCENT"]).pack(side="left", padx=5)
        ctk.CTkButton(footer, text="REMOVE SELECTED", command=self.remove_selected, height=45, fg_color="#552222", text_color="white", corner_radius=0, font=("Consolas", 12, "bold")).pack(side="left", padx=15)
        
        ctk.CTkButton(footer, text="SAVE CONFIG", command=self.save_data, height=45, fg_color=COLORS["ACCENT"], text_color="black", font=("Consolas", 14, "bold"), corner_radius=0).pack(side="right", padx=5)

        main_body = ctk.CTkFrame(self.root, fg_color="transparent")
        main_body.pack(fill="both", expand=True, padx=15, pady=5)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#181818", foreground="white", fieldbackground="#181818", borderwidth=0, font=("Consolas", 11), rowheight=25)
        style.map("Treeview", background=[('selected', COLORS["ACCENT"])])

        self.tree = ttk.Treeview(main_body, columns=("Label", "IP", "MAC", "Row", "Col", "Span", "Type", "Ping", "Group", "Wall"), show="headings")
        self.tree.heading("Label", text="Label")
        self.tree.heading("IP", text="IP Address")
        self.tree.heading("MAC", text="MAC Address")
        self.tree.heading("Row", text="Row")
        self.tree.heading("Col", text="Col")
        self.tree.heading("Span", text="Span")
        self.tree.heading("Type", text="Type")
        self.tree.heading("Ping", text="Ping?")
        self.tree.heading("Group", text="Group")
        self.tree.heading("Wall", text="Wall Point")
        
        self.tree.column("Label", width=100)
        self.tree.column("IP", width=110)
        self.tree.column("MAC", width=130)
        self.tree.column("Row", width=40, anchor="center")
        self.tree.column("Col", width=40, anchor="center")
        self.tree.column("Span", width=40, anchor="center")
        self.tree.column("Type", width=70, anchor="center")
        self.tree.column("Ping", width=50, anchor="center")
        self.tree.column("Group", width=80, anchor="center")
        self.tree.column("Wall", width=70, anchor="center")
        
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        ctk.CTkLabel(self.root, text="oT", font=("Consolas", 10), text_color="#333333").place(relx=0.99, rely=0.99, anchor="se")

    def _get_snmp_auth_data(self):
        """ מחזיר אובייקט אימות בהתאם לגרסה (v2c או v3) שנבחרה """
        from pysnmp.hlapi import CommunityData, UsmUserData
        ver = self.snmp_ver_var.get()
        user = self.snmp_user_var.get().strip()
        pwd = self.snmp_pass_var.get().strip()
        
        if ver == "v3":
            if pwd:
                return UsmUserData(user, authKey=pwd)
            else:
                return UsmUserData(user)
        else:
            return CommunityData(user)

    def auto_map_switches(self):
        try:
            import pysnmp
        except ImportError:
            self.automap_btn.configure(text="MISSING pysnmp!", fg_color=COLORS["QUIT_BTN"])
            self.root.after(3000, lambda: self.automap_btn.configure(text="AUTO-MAP SWITCHES", fg_color="#1F538D"))
            return
            
        switches = []
        for item in self.tree.get_children():
            v = self.tree.item(item)['values']
            if str(v[6]) == "Switch":
                switches.append(str(v[1]))
                
        if not switches:
            self.automap_btn.configure(text="NO SWITCHES FOUND", fg_color="#555555")
            self.root.after(2000, lambda: self.automap_btn.configure(text="AUTO-MAP SWITCHES", fg_color="#1F538D"))
            return
            
        self.automap_btn.configure(text="MAPPING...", fg_color=COLORS["ACCENT"], text_color="black")
        threading.Thread(target=self._run_auto_map, args=(switches,), daemon=True).start()

    def _run_auto_map(self, switches):
        from pysnmp.hlapi import SnmpEngine, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, nextCmd
        
        auth_data = self._get_snmp_auth_data()

        for sw_ip in switches:
            mac_to_port_oid = ObjectIdentity('1.3.6.1.2.1.17.4.3.1.2')
            mac_table = {}
            
            try:
                for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
                    SnmpEngine(),
                    auth_data, # שימוש בנתונים דינמיים
                    UdpTransportTarget((sw_ip, 161), timeout=2.0, retries=1),
                    ContextData(),
                    ObjectType(mac_to_port_oid),
                    lexicographicMode=False
                ):
                    if errorIndication or errorStatus:
                        break
                    else:
                        for varBind in varBinds:
                            oid_str = varBind[0].prettyPrint()
                            port = int(varBind[1])
                            mac_decimal = oid_str.split('.')[-6:]
                            mac_hex = ':'.join([f"{int(x):02x}" for x in mac_decimal]).upper()
                            mac_table[mac_hex] = port
            except:
                continue
                
            for item in self.tree.get_children():
                v = list(self.tree.item(item)['values'])
                mac = str(v[2]).upper().replace('-', ':')
                ip = str(v[1])
                
                if mac in mac_table and ip != sw_ip:
                    port_num = mac_table[mac]
                    
                    if len(v) < 10:
                        v.append(f"Port {port_num}")
                    else:
                        v[9] = f"Port {port_num}"
                        
                    self.root.after(0, self.tree.item, item, {'values': v})
                    self.connections.add(tuple(sorted([sw_ip, ip])))
                    
        self.root.after(0, self.save_data)
        if hasattr(self, 'canvas') and self.canvas.winfo_exists():
            self.root.after(0, self.draw_grid)
            
        self.root.after(0, lambda: self.automap_btn.configure(text="AUTO-MAP SWITCHES", fg_color="#1F538D", text_color="white"))

    def toggle_scan(self):
        if not self.scanning:
            self.scanning = True
            self.scan_btn.configure(text="STOP SCAN", fg_color=COLORS["QUIT_BTN"])
            threading.Thread(target=self._run_scan, daemon=True).start()
        else:
            self.scanning = False

    def _run_scan(self):
        try:
            net = ipaddress.ip_network(self.range_var.get(), strict=False)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as exe:
                for ip in net.hosts():
                    if not self.scanning: break
                    exe.submit(self._ping_host, str(ip))
        finally:
            self.scanning = False
            self.root.after(0, lambda: self.scan_btn.configure(text="SCAN NETWORK", fg_color=COLORS["BTN_BASE"]))

    def _guess_device_type(self, ip):
        try:
            from pysnmp.hlapi import SnmpEngine, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, getCmd
            auth_data = self._get_snmp_auth_data()
            errorIndication, errorStatus, errorIndex, varBinds = next(
                getCmd(
                    SnmpEngine(),
                    auth_data,
                    UdpTransportTarget((ip, 161), timeout=0.5, retries=0), 
                    ContextData(),
                    ObjectType(ObjectIdentity('1.3.6.1.2.1.1.1.0'))
                )
            )
            if not errorIndication and not errorStatus:
                return "Switch" 
        except:
            pass 
        return "PC"

    def _ping_host(self, ip):
        CREATE_NO_WINDOW = 0x08000000 
        if subprocess.run(["ping", "-n", "1", "-w", "150", ip], stdout=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).returncode == 0:
            mac = self._get_mac_from_arp(ip)
            dev_type = self._guess_device_type(ip)
            self.root.after(0, self._add_scanned, ip, mac, dev_type)

    def _get_mac_from_arp(self, ip):
        try:
            CREATE_NO_WINDOW = 0x08000000 
            arp_result = subprocess.check_output(["arp", "-a", ip], creationflags=CREATE_NO_WINDOW).decode(errors="ignore")
            match = re.search(r"([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})", arp_result)
            if match: return match.group(0).replace('-', ':').upper()
        except: pass
        return "UNKNOWN"

    def _add_scanned(self, ip, mac, dev_type):
        existing = [str(self.tree.item(i)['values'][1]) for i in self.tree.get_children()]
        if str(ip) not in existing:
            prefix = "SW" if dev_type == "Switch" else "PC"
            label = f"{prefix}-{str(ip).split('.')[-1]}"
            self.tree.insert("", "end", values=(label, str(ip), mac, 0, 0, 1, dev_type, "True", "General", ""))
            self.save_data()

    def add_manual(self):
        ip = self.manual_ip.get().strip()
        mac = self.manual_mac.get().strip() or "UNKNOWN"
        label = self.manual_label.get().strip()
        grp = self.manual_group.get().strip() or "General"
        wall = self.manual_wall.get().strip()
        dev_type = self.manual_type.get()
        ping_en = str(bool(self.ping_var.get()))
        
        if ip:
            self.tree.insert("", "end", values=(label if label else dev_type, ip, mac, 0, 0, 1, dev_type, ping_en, grp, wall))
            self.manual_ip.delete(0, 'end')
            self.manual_mac.delete(0, 'end')
            self.manual_label.delete(0, 'end')
            self.manual_group.delete(0, 'end')
            self.manual_wall.delete(0, 'end')
            self.manual_type.set("PC")
            self.ping_var.set(True)
            self.save_data()

    def remove_selected(self):
        sel = self.tree.selection()
        if sel: 
            ip_to_delete = str(self.tree.item(sel[0])['values'][1])
            self.connections = {conn for conn in self.connections if ip_to_delete not in conn}
            self.tree.delete(sel[0])
            self.save_data()

    def on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            class DummyEvent:
                x_root = self.root.winfo_pointerx()
                y_root = self.root.winfo_pointery()
            self.open_visual_edit_popup(DummyEvent(), item)

    def open_visual_edit_popup(self, event, item):
        values = list(self.tree.item(item, 'values'))
        
        popup = ctk.CTkToplevel(self.root)
        popup.geometry("340x550")
        popup.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        popup.overrideredirect(True)
        popup.geometry(f"+{event.x_root}+{event.y_root}")
        
        p_header = ctk.CTkFrame(popup, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        p_header.pack(side="top", fill="x")
        ctk.CTkLabel(p_header, text="EDIT NODE", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(p_header, text="Quit", width=30, height=30, command=popup.destroy, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
        
        def p_start(e): popup.x, popup.y = e.x, e.y
        def p_move(e): popup.geometry(f"+{popup.winfo_x() + e.x - popup.x}+{popup.winfo_y() + e.y - popup.y}")
        p_header.bind("<ButtonPress-1>", p_start); p_header.bind("<B1-Motion>", p_move)
        
        cont = ctk.CTkFrame(popup, fg_color="transparent")
        cont.pack(fill="both", expand=True, padx=20, pady=10)
        
        ctk.CTkLabel(cont, text="LABEL:", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        name_var = ctk.StringVar(value=values[0])
        ctk.CTkEntry(cont, textvariable=name_var, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0).pack(fill="x", pady=(0, 5))
        
        ctk.CTkLabel(cont, text="IP ADDRESS:", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        ip_var = ctk.StringVar(value=values[1])
        ctk.CTkEntry(cont, textvariable=ip_var, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0).pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(cont, text="MAC ADDRESS:", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        mac_var = ctk.StringVar(value=values[2])
        ctk.CTkEntry(cont, textvariable=mac_var, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0).pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(cont, text="GROUP:", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        grp_var = ctk.StringVar(value=values[8])
        ctk.CTkEntry(cont, textvariable=grp_var, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0).pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(cont, text="WALL POINT:", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        wall_var = ctk.StringVar(value=values[9] if len(values) > 9 else "")
        ctk.CTkEntry(cont, textvariable=wall_var, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0).pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(cont, text="TYPE:", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        type_var = ctk.StringVar(value=values[6])
        type_cb = ctk.CTkComboBox(cont, variable=type_var, values=["PC", "Router", "Hub", "Switch", "Server", "Printer", "Camera"], corner_radius=0, fg_color="#202020", border_width=0, text_color="white", dropdown_fg_color="#222222", button_color=COLORS["BTN_BASE"], button_hover_color=COLORS["ACCENT"])
        type_cb.pack(fill="x", pady=(0, 5))
        
        ping_var = ctk.BooleanVar(value=(str(values[7]) == "True"))
        ctk.CTkCheckBox(cont, text="Ping Enabled", variable=ping_var, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 11), corner_radius=0).pack(anchor="w", pady=(5, 10))
        
        def apply_changes():
            new_ip = ip_var.get().strip()
            old_ip = str(values[1])
            if new_ip != old_ip:
                new_conns = set()
                for ip1, ip2 in self.connections:
                    n1 = new_ip if ip1 == old_ip else ip1
                    n2 = new_ip if ip2 == old_ip else ip2
                    new_conns.add(tuple(sorted([n1, n2])))
                self.connections = new_conns
                
            self.tree.item(item, values=(name_var.get().strip(), new_ip, mac_var.get().strip(), values[3], values[4], values[5], type_var.get(), str(ping_var.get()), grp_var.get().strip(), wall_var.get().strip()))
            if hasattr(self, 'canvas') and self.canvas.winfo_exists(): self.draw_grid()
            self.save_data(); popup.destroy()

        def delete_station():
            ip_to_delete = str(values[1])
            self.connections = {conn for conn in self.connections if ip_to_delete not in conn}
            self.tree.delete(item)
            if hasattr(self, 'canvas') and self.canvas.winfo_exists(): self.draw_grid()
            self.save_data(); popup.destroy()
            
        btn_frame = ctk.CTkFrame(cont, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(5, 0))
        ctk.CTkButton(btn_frame, text="SAVE", height=35, command=apply_changes, fg_color=COLORS["ACCENT"], text_color="black", font=("Consolas", 11, "bold"), corner_radius=0).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(btn_frame, text="DELETE", height=35, command=delete_station, fg_color="red", text_color="white", font=("Consolas", 11, "bold"), hover_color="#8B0000", corner_radius=0).pack(side="right", fill="x", expand=True, padx=(5, 0))

    def open_visual_editor(self):
        self.vb = ctk.CTkToplevel(self.root); self.vb.geometry("1100x700"); self.vb.overrideredirect(True)
        self.vb.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        self.link_source = None
        
        top = ctk.CTkFrame(self.vb, height=50, fg_color=COLORS["BG_MAIN"], corner_radius=0); top.pack(fill="x")
        ctk.CTkLabel(top, text="NODE GRAPH EDITOR", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=15)
        ctk.CTkLabel(top, text="[ SHIFT + Click: Link ]  [ Right-Click: Edit ]", font=("Consolas", 10), text_color="#888888").pack(side="left", padx=10)
        
        ctk.CTkLabel(top, text="Rows:", text_color="white").pack(side="left", padx=(20,2))
        ctk.CTkButton(top, text="+", width=30, corner_radius=0, command=lambda: self._update_grid(r=1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left")
        ctk.CTkButton(top, text="-", width=30, corner_radius=0, command=lambda: self._update_grid(r=-1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left", padx=2)
        ctk.CTkLabel(top, text="Cols:", text_color="white").pack(side="left", padx=(20,2))
        ctk.CTkButton(top, text="+", width=30, corner_radius=0, command=lambda: self._update_grid(c=1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left")
        ctk.CTkButton(top, text="-", width=30, corner_radius=0, command=lambda: self._update_grid(c=-1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left", padx=2)

        ctk.CTkButton(top, text="DONE", command=self.vb.destroy, fg_color=COLORS["BTN_BASE"], text_color="white", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=10)
        self.canvas = tk.Canvas(self.vb, bg="#000000", highlightthickness=0); self.canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.draw_grid()

    def _update_grid(self, r=0, c=0):
        self.rows = max(1, self.rows + r); self.cols = max(1, self.cols + c); 
        self.draw_grid(); self.save_data()

    def draw_grid(self):
        self.canvas.delete("all")
        for r in range(self.rows):
            for c in range(self.cols):
                self.canvas.create_rectangle(c*CELL_W, r*CELL_H, (c+1)*CELL_W, (r+1)*CELL_H, outline=COLORS["GRID_LINE"], dash=(2,2))
        
        ip_data = {}
        for item in self.tree.get_children():
            v = self.tree.item(item)['values']
            ip = str(v[1])
            r, c, s = int(v[3]), int(v[4]), int(v[5])
            x1, y1 = c * CELL_W + 10, r * CELL_H + 10
            x2, y2 = c * CELL_W + (CELL_W * s) - 10, r * CELL_H + CELL_H - 10
            ip_data[ip] = {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'cx': (x1 + x2) / 2, 'cy': (y1 + y2) / 2, 'item': item, 'v': v}

        for ip1, ip2 in self.connections:
            if ip1 in ip_data and ip2 in ip_data:
                d1, d2 = ip_data[ip1], ip_data[ip2]
                if d1['cx'] < d2['cx']: sx_l, sy_l, ex_l, ey_l, dir_s, dir_e = d1['x2'], d1['cy'], d2['x1'], d2['cy'], 1, -1
                elif d1['cx'] > d2['cx']: sx_l, sy_l, ex_l, ey_l, dir_s, dir_e = d1['x1'], d1['cy'], d2['x2'], d2['cy'], -1, 1
                else: sx_l, sy_l, ex_l, ey_l, dir_s, dir_e = d1['x2'], d1['cy'], d2['x2'], d2['cy'], 1, 1

                offset = max(abs(ex_l - sx_l) * 0.5, 50)
                if d1['cx'] == d2['cx']: offset = max(abs(ey_l - sy_l) * 0.4, 50)
                self.canvas.create_line(sx_l, sy_l, sx_l + (offset * dir_s), sy_l, ex_l + (offset * dir_e), ey_l, ex_l, ey_l, fill="#7a7a7a", width=3, smooth=True, splinesteps=36, arrow=tk.LAST, arrowshape=(12, 14, 5))
        
        for ip, d in ip_data.items():
            tag = f"btn_{d['item']}"; v = d['v']
            is_link_src = (self.link_source == ip)
            outline_color, outline_width = (COLORS["LINK_SRC"], 2) if is_link_src else (COLORS["ACCENT"], 1)
            
            self.canvas.create_rectangle(d['x1'], d['y1'], d['x2'], d['y2'], fill=COLORS["CELL_BG"], outline=outline_color, width=outline_width, tags=tag)
            
            dev_type = v[6]
            span = int(v[5])
            
            if dev_type == "Switch":
                switch_width = d['x2'] - d['x1']
                num_ports = span * 4 
                port_spacing = switch_width / (num_ports + 1)
                
                strip_y1 = d['y2'] - 22
                self.canvas.create_rectangle(d['x1']+2, strip_y1, d['x2']-2, d['y2']-2, fill="#111111", outline="#333333", tags=tag)
                
                for p in range(1, num_ports + 1):
                    px = d['x1'] + (p * port_spacing)
                    self.canvas.create_rectangle(px-5, d['y2']-18, px+5, d['y2']-6, fill="#222222", outline=COLORS["ACCENT"], tags=tag)
                    self.canvas.create_oval(px-1, d['y2']-21, px+1, d['y2']-19, fill=COLORS["ACCENT"], outline="", tags=tag)
                    self.canvas.create_line(px-2, d['y2']-12, px+2, d['y2']-12, fill="#555555", tags=tag)
            else:
                port_r = 4
                self.canvas.create_oval(d['x1']-port_r, d['cy']-port_r, d['x1']+port_r, d['cy']+port_r, fill="#444444", outline=COLORS["ACCENT"], tags=tag)
                self.canvas.create_oval(d['x2']-port_r, d['cy']-port_r, d['x2']+port_r, d['cy']+port_r, fill="#444444", outline=COLORS["ACCENT"], tags=tag)
            
            ping_en, grp = str(v[7]), v[8]
            wall = v[9] if len(v) > 9 else ""
            display_text = f"{v[0]}\n{v[1]}\n[{dev_type}] | {grp}"
            if wall: display_text += f"\nWall: {wall}"
            if ping_en == "False": display_text += "\n(No Ping)"
            
            y_text_offset = -10 if dev_type == "Switch" else 0
            self.canvas.create_text(d['cx'], d['cy'] + y_text_offset, text=display_text, fill="white", font=("Consolas", 10, "bold"), justify="center", tags=tag)
            
            self.canvas.tag_bind(tag, "<Button-1>", lambda e, i=d['item']: self.start_drag(e, i))
            self.canvas.tag_bind(tag, "<B1-Motion>", self.do_drag)
            self.canvas.tag_bind(tag, "<ButtonRelease-1>", self.stop_drag)
            self.canvas.tag_bind(tag, "<Shift-Button-1>", lambda e, i=d['item']: self.on_shift_click(e, i))
            self.canvas.tag_bind(tag, "<Button-3>", lambda e, i=d['item']: self.open_visual_edit_popup(e, i))

    def on_shift_click(self, e, item):
        clicked_ip = str(self.tree.item(item)['values'][1])
        if self.link_source is None: self.link_source = clicked_ip; self.draw_grid()
        else:
            if self.link_source != clicked_ip:
                link_tuple = tuple(sorted([self.link_source, clicked_ip]))
                if link_tuple in self.connections: self.connections.remove(link_tuple)
                else: self.connections.add(link_tuple)
            self.link_source = None; self.draw_grid(); self.save_data()

    def start_drag(self, e, i): self.drag_data = {"i": i, "x": e.x, "y": e.y}
    def do_drag(self, e): dx, dy = e.x-self.drag_data["x"], e.y-self.drag_data["y"]; self.canvas.move(f"btn_{self.drag_data['i']}", dx, dy); self.drag_data["x"], self.drag_data["y"] = e.x, e.y
    def stop_drag(self, e):
        c = self.canvas.coords(f"btn_{self.drag_data['i']}"); col, row = int((c[0]+(CELL_W/2))//CELL_W), int((c[1]+(CELL_H/2))//CELL_H)
        v = list(self.tree.item(self.drag_data['i'])['values']); v[3], v[4] = max(0, min(row, self.rows-1)), max(0, min(col, self.cols-1))
        self.tree.item(self.drag_data['i'], values=v); self.draw_grid(); self.save_data()

    def _load_data(self):
        if not os.path.exists(FILE): return
        for i in self.tree.get_children(): self.tree.delete(i)
        self.connections.clear()
        
        mac_dict = {}
        if os.path.exists(MAC_FILE):
            try:
                with open(MAC_FILE, 'r') as fm:
                    for line in fm:
                        parts = line.replace(',', ' ').split()
                        if len(parts) >= 2:
                            mac_dict[parts[0].strip()] = parts[1].strip()
            except: pass

        skipped = 0
        with open(FILE, "r", newline="") as f:
            for row in csv.reader(f, skipinitialspace=True):
                p = [x.strip() for x in row]
                if not p or not p[0]: continue
                try:
                    if p[0] == "CONFIG: SIZE":
                        if len(p) >= 3:
                            self.win_w_var.set(p[1])
                            self.win_h_var.set(p[2])
                    elif p[0] == "CONFIG: GRID":
                        self.rows, self.cols = int(p[1]), int(p[2])
                    elif p[0] == "CONFIG: LINK":
                        if len(p) >= 3: self.connections.add(tuple(sorted([p[1], p[2]])))
                    elif p[0] == "CONFIG: SNMP":
                        if len(p) >= 4:
                            self.snmp_ver_var.set(p[1])
                            self.snmp_user_var.set(p[2])
                            self.snmp_pass_var.set(p[3])
                    elif not p[0].startswith("CONFIG"):
                        label, ip = p[0], p[1]
                        if not ip: raise ValueError("missing IP")
                        mac = mac_dict.get(ip, "UNKNOWN")
                        r = int(p[2]) if len(p) > 2 else 0
                        c = int(p[3]) if len(p) > 3 else 0
                        s = int(p[4]) if len(p) > 4 else 1
                        dev_type = p[5] if len(p) > 5 else "PC"
                        ping_en = p[6] if len(p) > 6 else "True"
                        grp = p[7] if len(p) > 7 else "General"
                        wall = p[8] if len(p) > 8 else ""

                        self.tree.insert("", "end", values=(label, ip, mac, r, c, s, dev_type, ping_en, grp, wall))
                except (IndexError, ValueError):
                    skipped += 1

        if skipped:
            self.title_lbl.configure(text=f"PSTOOLS - CONFIGURATOR V3.0  [WARNING: {skipped} invalid line(s) in {FILE} skipped]", text_color="red")

    def save_data(self):
        with open(FILE, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["CONFIG: SIZE", self.win_w_var.get(), self.win_h_var.get()])
            w.writerow(["CONFIG: GRID", self.rows, self.cols])
            w.writerow(["CONFIG: SNMP", self.snmp_ver_var.get(), self.snmp_user_var.get(), self.snmp_pass_var.get()])
            for ip1, ip2 in self.connections: w.writerow(["CONFIG: LINK", ip1, ip2])
            f.write("\n")

            macs_to_save = []
            for i in self.tree.get_children():
                v = self.tree.item(i)['values']
                wall_val = v[9] if len(v) > 9 else ""
                w.writerow([v[0], v[1], v[3], v[4], v[5], v[6], v[7], v[8], wall_val])
                
                if v[2] and v[2] != "UNKNOWN":
                    macs_to_save.append(f"{v[1]} {v[2]}\n")
        
        with open(MAC_FILE, "w") as f_mac:
            f_mac.writelines(macs_to_save)

        try: winsound.MessageBeep()
        except: pass

    def toggle_visibility(self, event=None):
        if self.root.winfo_viewable(): self.root.withdraw()
        else: self.root.deiconify(); self.root.attributes("-topmost", True); self.root.after(100, lambda: self.root.attributes("-topmost", False))

    def on_close(self): self.root.destroy()

if __name__ == "__main__":
    root = ctk.CTk()
    PsToolsConfigurator(root)
    root.mainloop()