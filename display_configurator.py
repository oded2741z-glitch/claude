import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
import os
import winsound
import ctypes
from ctypes import wintypes
import re

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
    "GPU_BG": "#1A2E35",     
    "LINK_SRC": "#FFFFFF",
    "SELECTED": "#2A6B56"  
}

FILE = "displays_map.txt"
TARGETS_FILE = "targets.txt"
CONFIG_FILE = "config.txt"
CELL_W, CELL_H = 150, 90

# --- WINDOWS API FOR MONITOR DETECTION ---
user32 = ctypes.windll.user32

try:
    HMONITOR = wintypes.HMONITOR
    HDC = wintypes.HDC
except AttributeError:
    HMONITOR = wintypes.HANDLE
    HDC = wintypes.HANDLE

MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HMONITOR, HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32)
    ]

def get_local_monitors():
    monitors = []
    def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        res = user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi))
        if res:
            is_primary = bool(mi.dwFlags & 1)
            name = mi.szDevice
            rect = mi.rcMonitor
            monitors.append({
                "name": name,
                "x": rect.left,
                "y": rect.top,
                "w": rect.right - rect.left,
                "h": rect.bottom - rect.top,
                "primary": is_primary
            })
        return True
    
    cb = MONITORENUMPROC(callback)
    user32.EnumDisplayMonitors(None, None, cb, 0)
    return monitors

class DisplayConfigurator:
    def __init__(self, root):
        self.root = root
        self.root.geometry("1200x700")
        self.root.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        self.root.overrideredirect(True)
        
        self.rows, self.cols = 6, 5
        self.connections = set() 
        self.link_source = None  

        self.win_w_var = ctk.StringVar(value="1250")
        self.win_h_var = ctk.StringVar(value="750")

        try:
            import keyboard
            keyboard.add_hotkey("f8", self.toggle_visibility)
        except:
            self.root.bind_all("<F8>", self.toggle_visibility)

        self._setup_ui()
        self._load_data()
        self._load_targets()

    def _setup_ui(self):
        self.header = ctk.CTkFrame(self.root, height=45, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        self.header.pack(side="top", fill="x")
        ctk.CTkLabel(self.header, text="SYSTEM CONFIGURATOR", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=20)
        
        ctk.CTkButton(self.header, text="Quit", width=60, height=30, command=self.on_close, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=10)
        ctk.CTkButton(self.header, text="Help", width=60, height=30, command=self.show_help, fg_color=COLORS["BTN_BASE"], text_color="white", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=5)

        def start_move(e): self.x, self.y = e.x, e.y
        def do_move(e): self.root.geometry(f"+{self.root.winfo_x() + e.x - self.x}+{self.root.winfo_y() + e.y - self.y}")
        self.header.bind("<ButtonPress-1>", start_move); self.header.bind("<B1-Motion>", do_move)

        self.tabview = ctk.CTkTabview(self.root, fg_color="transparent", segmented_button_selected_color=COLORS["ACCENT"], segmented_button_selected_hover_color=COLORS["SELECTED"], text_color="white")
        self.tabview.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.tab_disp = self.tabview.add("Displays Map")
        self.tab_tgt = self.tabview.add("Stream Targets")
        self.tab_set = self.tabview.add("Settings")

        self._setup_displays_tab(self.tab_disp)
        self._setup_targets_tab(self.tab_tgt)
        self._setup_settings_tab(self.tab_set)

        ctk.CTkLabel(self.root, text="oT", font=("Consolas", 10), text_color="#333333").place(relx=0.99, rely=0.99, anchor="se")

    def _setup_displays_tab(self, parent_frame):
        ctrl = ctk.CTkFrame(parent_frame, fg_color="transparent")
        ctrl.pack(fill="x", padx=10, pady=(10, 5))
        
        row1 = ctk.CTkFrame(ctrl, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 5))
        
        self.scan_btn = ctk.CTkButton(row1, text="DETECT MONITORS", command=self.detect_displays, fg_color=COLORS["BTN_BASE"], corner_radius=0, width=150, text_color="white", font=("Consolas", 11, "bold"))
        self.scan_btn.pack(side="left", padx=5)
        
        ctk.CTkLabel(row1, text="Automatically loads physical screens connected to this PC.", text_color="#888888", font=("Consolas", 10)).pack(side="left", padx=10)

        row2 = ctk.CTkFrame(ctrl, fg_color="transparent")
        row2.pack(fill="x", pady=(5, 0))
        
        ctk.CTkLabel(row2, text="Type:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=(0, 2))
        self.man_type = ctk.CTkComboBox(row2, width=80, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", dropdown_fg_color="#222222", button_color=COLORS["BTN_BASE"], button_hover_color=COLORS["ACCENT"], values=["Screen", "GPU"])
        self.man_type.set("Screen")
        self.man_type.pack(side="left", padx=2)

        ctk.CTkLabel(row2, text="Lbl:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.man_lbl = ctk.CTkEntry(row2, width=80, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="Alias")
        self.man_lbl.pack(side="left", padx=2)
        
        ctk.CTkLabel(row2, text="Info 1:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.man_port = ctk.CTkEntry(row2, width=90, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="Port/Model")
        self.man_port.pack(side="left", padx=2)
        
        ctk.CTkLabel(row2, text="Info 2:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.man_os = ctk.CTkEntry(row2, width=90, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="OS ID/Ports#")
        self.man_os.pack(side="left", padx=2)
        
        ctk.CTkLabel(row2, text="Res:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=2)
        self.man_res = ctk.CTkEntry(row2, width=90, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="1920x1080")
        self.man_res.pack(side="left", padx=2)
        
        self.man_prim = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row2, text="Primary", variable=self.man_prim, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 11), width=60, corner_radius=0).pack(side="left", padx=15)

        ctk.CTkButton(row2, text="+ ADD MANUAL", command=self.add_manual, fg_color=COLORS["BTN_BASE"], corner_radius=0, width=100, text_color="white", font=("Consolas", 11, "bold")).pack(side="left", padx=5)

        footer = ctk.CTkFrame(parent_frame, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=10, pady=10)
        
        size_frame = ctk.CTkFrame(footer, fg_color="transparent")
        size_frame.pack(side="left", padx=(0, 20))
        ctk.CTkLabel(size_frame, text="Dash W:", text_color="white", font=("Consolas", 12, "bold")).pack(side="left", padx=2)
        ctk.CTkEntry(size_frame, textvariable=self.win_w_var, width=50, corner_radius=0, fg_color="#202020", border_width=0, text_color="white").pack(side="left")
        ctk.CTkLabel(size_frame, text="H:", text_color="white", font=("Consolas", 12, "bold")).pack(side="left", padx=(10, 2))
        ctk.CTkEntry(size_frame, textvariable=self.win_h_var, width=50, corner_radius=0, fg_color="#202020", border_width=0, text_color="white").pack(side="left")

        ctk.CTkButton(footer, text="VISUAL EDITOR", command=self.open_visual_editor, height=45, fg_color=COLORS["BTN_BASE"], text_color="white", font=("Consolas", 14, "bold"), corner_radius=0, hover_color=COLORS["ACCENT"]).pack(side="left", padx=5)
        ctk.CTkButton(footer, text="CLEAR ALL", command=self.clear_selected, height=45, fg_color="#552222", text_color="white", corner_radius=0, font=("Consolas", 12, "bold")).pack(side="left", padx=15)
        ctk.CTkButton(footer, text="SAVE CONFIG", command=self.save_data, height=45, fg_color=COLORS["ACCENT"], text_color="black", font=("Consolas", 14, "bold"), corner_radius=0).pack(side="right", padx=5)

        main_body = ctk.CTkFrame(parent_frame, fg_color="transparent")
        main_body.pack(fill="both", expand=True, padx=10, pady=5)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#181818", foreground="white", fieldbackground="#181818", borderwidth=0, font=("Consolas", 11), rowheight=25)
        style.map("Treeview", background=[('selected', COLORS["ACCENT"])])

        self.tree = ttk.Treeview(main_body, columns=("Label", "Type", "Info1", "Info2", "Resolution", "Offset", "Primary", "Row", "Col", "Span"), show="headings")
        self.tree.heading("Label", text="Label")
        self.tree.heading("Type", text="Type")
        self.tree.heading("Info1", text="Port / Model")
        self.tree.heading("Info2", text="OS ID / Ports#")
        self.tree.heading("Resolution", text="Resolution")
        self.tree.heading("Offset", text="Offset")
        self.tree.heading("Primary", text="Primary?")
        self.tree.heading("Row", text="R")
        self.tree.heading("Col", text="C")
        self.tree.heading("Span", text="S")
        
        self.tree.column("Label", width=100)
        self.tree.column("Type", width=70, anchor="center")
        self.tree.column("Info1", width=90)
        self.tree.column("Info2", width=120)
        self.tree.column("Resolution", width=90, anchor="center")
        self.tree.column("Offset", width=90, anchor="center")
        self.tree.column("Primary", width=60, anchor="center")
        self.tree.column("Row", width=40, anchor="center")
        self.tree.column("Col", width=40, anchor="center")
        self.tree.column("Span", width=40, anchor="center")
        
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_tree_double_click)

    def detect_displays(self):
        for i in self.tree.get_children(): 
            if self.tree.item(i)['values'][1] != "GPU": 
                self.tree.delete(i)
                
        current_labels = [str(self.tree.item(i)['values'][0]) for i in self.tree.get_children()]
        self.connections = {conn for conn in self.connections if conn[0] in current_labels and conn[1] in current_labels}
        
        monitors = get_local_monitors()
        
        if not monitors:
            self.tree.insert("", "end", values=("Screen 1", "Screen", "UNKNOWN", "DISPLAY1", "1920x1080", "X:0 Y:0", "YES", 0, 0, 1))
            
        for idx, m in enumerate(monitors):
            label = f"Screen {idx + 1}"
            port = "UNKNOWN"
            resolution = f"{m['w']}x{m['h']}"
            offset = f"X:{m['x']} Y:{m['y']}" 
            is_primary = "YES" if m['primary'] else "NO"
            
            self.tree.insert("", "end", values=(label, "Screen", port, m['name'], resolution, offset, is_primary, 0, idx, 1))
        
        self.save_data()

    def add_manual(self):
        dev_type = self.man_type.get()
        if dev_type == "GPU":
            lbl = self.man_lbl.get().strip() or "GPU 1"
            info1 = self.man_port.get().strip() or "RTX Model"
            info2 = self.man_os.get().strip() or "4 Ports"
            res, prim = "N/A", "N/A"
        else:
            lbl = self.man_lbl.get().strip() or "New Screen"
            info1 = self.man_port.get().strip() or "UNKNOWN"
            info2 = self.man_os.get().strip() or "DISPLAY_X"
            res = self.man_res.get().strip() or "1920x1080"
            prim = "YES" if self.man_prim.get() else "NO"
            
        self.tree.insert("", "end", values=(lbl, dev_type, info1, info2, res, "X:0 Y:0", prim, 0, 0, 1))
        self.man_lbl.delete(0, 'end'); self.man_port.delete(0, 'end')
        self.man_os.delete(0, 'end'); self.man_res.delete(0, 'end')
        self.save_data()

    def clear_selected(self):
        sel = self.tree.selection()
        if sel: 
            name_to_delete = str(self.tree.item(sel[0])['values'][0])
            self.connections = {conn for conn in self.connections if name_to_delete not in conn}
            self.tree.delete(sel[0])
        else:
            for i in self.tree.get_children(): self.tree.delete(i)
            self.connections.clear()
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
        is_gpu = str(values[1]) == "GPU"
        
        popup = ctk.CTkToplevel(self.root)
        popup.geometry("340x330")
        popup.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        popup.overrideredirect(True)
        popup.geometry(f"+{event.x_root}+{event.y_root}")
        
        p_header = ctk.CTkFrame(popup, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        p_header.pack(side="top", fill="x")
        title_txt = "EDIT GPU NODE" if is_gpu else "EDIT DISPLAY NODE"
        ctk.CTkLabel(p_header, text=title_txt, font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(p_header, text="Quit", width=30, height=30, command=popup.destroy, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
        
        def p_start(e): popup.x, popup.y = e.x, e.y
        def p_move(e): popup.geometry(f"+{popup.winfo_x() + e.x - popup.x}+{popup.winfo_y() + e.y - popup.y}")
        p_header.bind("<ButtonPress-1>", p_start); p_header.bind("<B1-Motion>", p_move)
        
        cont = ctk.CTkFrame(popup, fg_color="transparent")
        cont.pack(fill="both", expand=True, padx=20, pady=10)
        
        ctk.CTkLabel(cont, text="ALIAS (LABEL):", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        name_var = ctk.StringVar(value=values[0])
        ctk.CTkEntry(cont, textvariable=name_var, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0).pack(fill="x", pady=(0, 10))
        
        lbl_info1 = "MODEL (e.g. RTX 4090):" if is_gpu else "INPUT PORT (e.g. HDMI 1):"
        ctk.CTkLabel(cont, text=lbl_info1, font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        port_var = ctk.StringVar(value=values[2])
        ctk.CTkEntry(cont, textvariable=port_var, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0).pack(fill="x", pady=(0, 15))
        
        if is_gpu:
            ctk.CTkLabel(cont, text=f"Ports: {values[3]}", font=("Consolas", 10), text_color="#888888", justify="left").pack(anchor="w", pady=(0, 15))
        else:
            ctk.CTkLabel(cont, text=f"OS ID: {values[3]}\nResolution: {values[4]}\nOffset: {values[5]}", font=("Consolas", 10), text_color="#888888", justify="left").pack(anchor="w", pady=(0, 15))

        def apply_changes():
            new_name = name_var.get().strip()
            new_port = port_var.get().strip()
            old_name = str(values[0])
            
            if new_name != old_name:
                new_conns = set()
                for n1, n2 in self.connections:
                    c1 = new_name if n1 == old_name else n1
                    c2 = new_name if n2 == old_name else n2
                    new_conns.add(tuple(sorted([c1, c2])))
                self.connections = new_conns
                
            self.tree.item(item, values=(new_name, values[1], new_port, values[3], values[4], values[5], values[6], values[7], values[8], values[9]))
            if hasattr(self, 'canvas') and self.canvas.winfo_exists(): self.draw_grid()
            self.save_data(); popup.destroy()
            
        ctk.CTkButton(cont, text="SAVE", height=35, command=apply_changes, fg_color=COLORS["ACCENT"], text_color="black", font=("Consolas", 11, "bold"), corner_radius=0).pack(fill="x")

    def open_visual_editor(self):
        self.vb = ctk.CTkToplevel(self.root); self.vb.geometry("1100x700"); self.vb.overrideredirect(True)
        self.vb.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        self.link_source = None
        
        top = ctk.CTkFrame(self.vb, height=50, fg_color=COLORS["BG_MAIN"], corner_radius=0); top.pack(fill="x")
        ctk.CTkLabel(top, text="SCREEN LAYOUT EDITOR", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=15)
        ctk.CTkLabel(top, text="[ SHIFT + Click: Link Data Flow ]  [ Right-Click: Edit ]", font=("Consolas", 10), text_color="#888888").pack(side="left", padx=10)
        
        ctk.CTkLabel(top, text="Grid R:", text_color="white").pack(side="left", padx=(20,2))
        ctk.CTkButton(top, text="+", width=30, corner_radius=0, command=lambda: self._update_grid(r=1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left")
        ctk.CTkButton(top, text="-", width=30, corner_radius=0, command=lambda: self._update_grid(r=-1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left", padx=2)
        ctk.CTkLabel(top, text="Grid C:", text_color="white").pack(side="left", padx=(20,2))
        ctk.CTkButton(top, text="+", width=30, corner_radius=0, command=lambda: self._update_grid(c=1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left")
        ctk.CTkButton(top, text="-", width=30, corner_radius=0, command=lambda: self._update_grid(c=-1), fg_color=COLORS["BTN_BASE"], text_color="white").pack(side="left", padx=2)

        ctk.CTkButton(top, text="DONE", command=self.vb.destroy, fg_color=COLORS["BTN_BASE"], text_color="white", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=10)
        self.canvas = tk.Canvas(self.vb, bg="#000000", highlightthickness=0); self.canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.draw_grid()

    def _update_grid(self, r=0, c=0):
        self.rows = max(1, self.rows + r); self.cols = max(1, self.cols + c)
        self.draw_grid(); self.save_data()

    def draw_grid(self):
        self.canvas.delete("all")
        for r in range(self.rows):
            for c in range(self.cols):
                self.canvas.create_rectangle(c*CELL_W, r*CELL_H, (c+1)*CELL_W, (r+1)*CELL_H, outline=COLORS["GRID_LINE"], dash=(2,2))
        
        scr_data = {}
        for item in self.tree.get_children():
            v = self.tree.item(item)['values']
            lbl = str(v[0])
            r, c, s = int(v[7]), int(v[8]), int(v[9])
            x1, y1 = c * CELL_W + 10, r * CELL_H + 10
            x2, y2 = c * CELL_W + (CELL_W * s) - 10, r * CELL_H + CELL_H - 10
            scr_data[lbl] = {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'cx': (x1 + x2) / 2, 'cy': (y1 + y2) / 2, 'item': item, 'v': v}

        for n1, n2 in self.connections:
            if n1 in scr_data and n2 in scr_data:
                d1, d2 = scr_data[n1], scr_data[n2]
                is_d1_gpu = d1['v'][1] == "GPU"
                is_d2_gpu = d2['v'][1] == "GPU"
                
                if is_d1_gpu and not is_d2_gpu: src, tgt = d1, d2
                elif is_d2_gpu and not is_d1_gpu: src, tgt = d2, d1
                else: src, tgt = (d1, d2) if d1['cx'] <= d2['cx'] else (d2, d1)

                sx_l, sy_l = src['x2'], src['cy']
                ex_l, ey_l = tgt['x1'], tgt['cy']
                offset = max(abs(ex_l - sx_l) * 0.6, 60)
                
                self.canvas.create_line(sx_l, sy_l, sx_l + offset, sy_l, ex_l - offset, ey_l, ex_l, ey_l, fill="#FFFFFF", width=3, smooth=True, splinesteps=36, arrow=tk.LAST, arrowshape=(12, 14, 5))
        
        for lbl, d in scr_data.items():
            tag = f"btn_{d['item']}"; v = d['v']
            dev_type = str(v[1])
            is_link_src = (self.link_source == lbl)
            is_primary = (str(v[6]) == "YES")
            
            outline_color, outline_width = (COLORS["LINK_SRC"], 2) if is_link_src else (COLORS["ACCENT"], 1)
            
            if dev_type == "GPU":
                bg_color = COLORS["GPU_BG"]
                display_text = f"[GPU]\n{v[0]}\n{v[2]}\n({v[3]})"
            else:
                bg_color = COLORS["SELECTED"] if is_primary else COLORS["CELL_BG"]
                display_text = f"{v[0]}\n[{v[2]}]\n{v[4]}"
                if is_primary: display_text += "\n[PRIMARY]"

            self.canvas.create_rectangle(d['x1'], d['y1'], d['x2'], d['y2'], fill=bg_color, outline=outline_color, width=outline_width, tags=tag)
            
            port_r = 4
            if dev_type == "GPU":
                num_ports = 4
                match = re.search(r'\d+', str(v[3]))
                if match: num_ports = max(1, min(int(match.group()), 8))
                spacing = (d['y2'] - d['y1']) / (num_ports + 1)
                for p in range(1, num_ports + 1):
                    py = d['y1'] + (spacing * p)
                    self.canvas.create_oval(d['x2']-port_r, py-port_r, d['x2']+port_r, py+port_r, fill="#444", outline=COLORS["ACCENT"], tags=tag)
            else:
                self.canvas.create_oval(d['x1']-port_r, d['cy']-port_r, d['x1']+port_r, d['cy']+port_r, fill="#444", outline=COLORS["ACCENT"], tags=tag)
            
            self.canvas.create_text(d['cx'], d['cy'], text=display_text, fill="white", font=("Consolas", 10, "bold"), justify="center", tags=tag)
            
            self.canvas.tag_bind(tag, "<Button-1>", lambda e, i=d['item']: self.start_drag(e, i))
            self.canvas.tag_bind(tag, "<B1-Motion>", self.do_drag)
            self.canvas.tag_bind(tag, "<ButtonRelease-1>", self.stop_drag)
            self.canvas.tag_bind(tag, "<Shift-Button-1>", lambda e, i=d['item']: self.on_shift_click(e, i))
            self.canvas.tag_bind(tag, "<Button-3>", lambda e, i=d['item']: self.open_visual_edit_popup(e, i))

    def on_shift_click(self, e, item):
        clicked_lbl = str(self.tree.item(item)['values'][0])
        if self.link_source is None: self.link_source = clicked_lbl; self.draw_grid()
        else:
            if self.link_source != clicked_lbl:
                link_tuple = tuple(sorted([self.link_source, clicked_lbl]))
                if link_tuple in self.connections: self.connections.remove(link_tuple)
                else: self.connections.add(link_tuple)
            self.link_source = None; self.draw_grid(); self.save_data()

    def start_drag(self, e, i): self.drag_data = {"i": i, "x": e.x, "y": e.y}
    def do_drag(self, e): dx, dy = e.x-self.drag_data["x"], e.y-self.drag_data["y"]; self.canvas.move(f"btn_{self.drag_data['i']}", dx, dy); self.drag_data["x"], self.drag_data["y"] = e.x, e.y
    def stop_drag(self, e):
        c = self.canvas.coords(f"btn_{self.drag_data['i']}"); col, row = int((c[0]+(CELL_W/2))//CELL_W), int((c[1]+(CELL_H/2))//CELL_H)
        v = list(self.tree.item(self.drag_data['i'])['values']); v[7], v[8] = max(0, min(row, self.rows-1)), max(0, min(col, self.cols-1))
        self.tree.item(self.drag_data['i'], values=v); self.draw_grid(); self.save_data()

    def _load_data(self):
        if not os.path.exists(FILE): return
        for i in self.tree.get_children(): self.tree.delete(i)
        self.connections.clear()
        
        with open(FILE, "r") as f:
            for line in f:
                l = line.strip()
                if not l: continue
                if "CONFIG: SIZE" in l:
                    p = l.split(",")
                    if len(p) >= 3:
                        self.win_w_var.set(p[1].strip())
                        self.win_h_var.set(p[2].strip())
                elif "CONFIG: GRID" in l: 
                    p = l.split(",")
                    if len(p) >= 3:
                        self.rows, self.cols = int(p[1]), int(p[2])
                elif "CONFIG: LINK" in l:
                    p = [x.strip() for x in l.split(",")]
                    if len(p) >= 3: self.connections.add(tuple(sorted([p[1], p[2]])))
                elif not l.startswith("CONFIG"):
                    p = [x.strip() for x in l.split(",")]
                    if len(p) == 9:
                        try: self.tree.insert("", "end", values=(p[0], "Screen", p[1], p[2], p[3], p[4], p[5], int(p[6]), int(p[7]), int(p[8])))
                        except: pass
                    elif len(p) >= 10:
                        try: self.tree.insert("", "end", values=(p[0], p[1], p[2], p[3], p[4], p[5], p[6], int(p[7]), int(p[8]), int(p[9])))
                        except: pass

    def save_data(self):
        with open(FILE, "w") as f:
            f.write(f"CONFIG: SIZE, {self.win_w_var.get()}, {self.win_h_var.get()}\n")
            f.write(f"CONFIG: GRID, {self.rows}, {self.cols}\n")
            for c1, c2 in self.connections: f.write(f"CONFIG: LINK, {c1}, {c2}\n")
            f.write("\n")
            for i in self.tree.get_children(): 
                v = self.tree.item(i)['values']
                f.write(f"{v[0]}, {v[1]}, {v[2]}, {v[3]}, {v[4]}, {v[5]}, {v[6]}, {v[7]}, {v[8]}, {v[9]}\n")
        try: winsound.MessageBeep()
        except: pass

    # ==========================================
    # TAB 2: STREAM TARGETS 
    # ==========================================
    def _setup_targets_tab(self, parent_frame):
        ctk.CTkLabel(parent_frame, text="STREAM TARGETS MANAGEMENT (targets.txt)", font=("Consolas", 14, "bold"), text_color=COLORS["ACCENT"]).pack(pady=10)

        top_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        top_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(top_frame, text="Target Name:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=(0, 5))
        self.tgt_name = ctk.CTkEntry(top_frame, width=150, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="e.g., Camera 1")
        self.tgt_name.pack(side="left", padx=5)
        
        ctk.CTkLabel(top_frame, text="Stream URL:", text_color="white", font=("Consolas", 12)).pack(side="left", padx=(15, 5))
        self.tgt_url = ctk.CTkEntry(top_frame, width=400, corner_radius=0, fg_color="#202020", border_width=0, text_color="white", placeholder_text="http://...")
        self.tgt_url.pack(side="left", padx=5)
        
        ctk.CTkButton(top_frame, text="+ ADD / UPDATE", command=self.add_target, fg_color=COLORS["BTN_BASE"], corner_radius=0, width=120, text_color="white", font=("Consolas", 11, "bold")).pack(side="left", padx=10)

        body_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        body_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.target_tree = ttk.Treeview(body_frame, columns=("Name", "URL"), show="headings")
        self.target_tree.heading("Name", text="Target Name")
        self.target_tree.heading("URL", text="Stream URL")
        self.target_tree.column("Name", width=200)
        self.target_tree.column("URL", width=600)
        self.target_tree.pack(side="left", fill="both", expand=True)
        self.target_tree.bind("<Double-1>", self.on_target_double_click)
        
        footer = ctk.CTkFrame(parent_frame, fg_color="transparent")
        footer.pack(fill="x", padx=10, pady=10)
        
        ctk.CTkButton(footer, text="DELETE SELECTED", command=self.delete_target, height=35, fg_color="#552222", text_color="white", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="left")

    def _load_targets(self):
        for i in self.target_tree.get_children(): self.target_tree.delete(i)
        if not os.path.exists(TARGETS_FILE): return
        with open(TARGETS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "|" in line:
                    name, url = line.strip().split("|", 1)
                    self.target_tree.insert("", "end", values=(name, url))

    def add_target(self):
        name = self.tgt_name.get().strip()
        url = self.tgt_url.get().strip()
        if not name or not url: return
        
        for i in self.target_tree.get_children():
            if self.target_tree.item(i)['values'][0] == name:
                self.target_tree.item(i, values=(name, url))
                self.save_targets()
                return
        
        self.target_tree.insert("", "end", values=(name, url))
        self.tgt_name.delete(0, 'end')
        self.tgt_url.delete(0, 'end')
        self.save_targets()

    def delete_target(self):
        sel = self.target_tree.selection()
        if sel:
            self.target_tree.delete(sel[0])
            self.save_targets()

    def save_targets(self):
        with open(TARGETS_FILE, "w", encoding="utf-8") as f:
            for i in self.target_tree.get_children():
                v = self.target_tree.item(i)['values']
                f.write(f"{v[0]}|{v[1]}\n")
        try: winsound.MessageBeep()
        except: pass
        
    def on_target_double_click(self, event):
        sel = self.target_tree.selection()
        if sel:
            v = self.target_tree.item(sel[0])['values']
            self.tgt_name.delete(0, 'end')
            self.tgt_name.insert(0, str(v[0]))
            self.tgt_url.delete(0, 'end')
            self.tgt_url.insert(0, str(v[1]))

    # ==========================================
    # TAB 3: SETTINGS (config.txt)
    # ==========================================
    def _setup_settings_tab(self, parent_frame):
        ctk.CTkLabel(parent_frame, text="GLOBAL VIEWER SETTINGS (config.txt)", font=("Consolas", 14, "bold"), text_color=COLORS["ACCENT"]).pack(pady=10)

        self.app_config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            self.app_config[k] = v
            except: pass

        self.cfg_show_header = ctk.BooleanVar(value=(self.app_config.get("show_header", "True") == "True"))
        self.cfg_show_animation = ctk.BooleanVar(value=(self.app_config.get("show_animation", "True") == "True"))
        
        self.cfg_stretch = ctk.BooleanVar(value=(self.app_config.get("stretch_video") == "True"))
        self.cfg_single = ctk.BooleanVar(value=(self.app_config.get("single_screen") == "True"))
        self.cfg_target_disp = ctk.StringVar(value=self.app_config.get("target_display", "0"))
        self.cfg_def_res = ctk.StringVar(value=self.app_config.get("default_resolution", "Default"))

        form_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        form_frame.pack(fill="both", expand=True, padx=20, pady=10)

        ctk.CTkCheckBox(form_frame, text="Show Viewer Header / Resize Grip (Shows top resize bar)", variable=self.cfg_show_header, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 12)).pack(anchor="w", pady=10)
        ctk.CTkCheckBox(form_frame, text="Show Loading Animation (Animated Spinner vs. Plain Text)", variable=self.cfg_show_animation, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 12)).pack(anchor="w", pady=10)
        ctk.CTkCheckBox(form_frame, text="Stretch Video to Fill Screens (Ignore Aspect Ratio)", variable=self.cfg_stretch, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 12)).pack(anchor="w", pady=10)
        ctk.CTkCheckBox(form_frame, text="Single Screen Mode (Hide splits, show only Screen 1)", variable=self.cfg_single, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 12)).pack(anchor="w", pady=10)

        disp_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        disp_frame.pack(fill="x", pady=10)
        ctk.CTkLabel(disp_frame, text="Target Display Index (0 = Primary):", font=("Consolas", 12), text_color="white").pack(side="left")
        ctk.CTkEntry(disp_frame, textvariable=self.cfg_target_disp, width=50, corner_radius=0, fg_color="#202020", border_width=0, text_color="white").pack(side="left", padx=10)

        res_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        res_frame.pack(fill="x", pady=10)
        ctk.CTkLabel(res_frame, text="Global Default Resolution:", font=("Consolas", 12), text_color="white").pack(side="left")
        
        res_options = ["Default", "800x600", "1024x768", "1280x720", "1920x1080"]
        ctk.CTkOptionMenu(res_frame, variable=self.cfg_def_res, values=res_options, width=120, fg_color="#202020", button_color=COLORS["BTN_BASE"], button_hover_color=COLORS["ACCENT"]).pack(side="left", padx=10)

        ctk.CTkButton(parent_frame, text="SAVE SETTINGS", command=self.save_settings, height=45, fg_color=COLORS["ACCENT"], text_color="black", font=("Consolas", 14, "bold"), corner_radius=0).pack(side="bottom", pady=20)

    def save_settings(self):
        self.app_config["show_header"] = str(self.cfg_show_header.get())
        self.app_config["show_animation"] = str(self.cfg_show_animation.get())
        self.app_config["stretch_video"] = str(self.cfg_stretch.get())
        self.app_config["single_screen"] = str(self.cfg_single.get())
        self.app_config["target_display"] = self.cfg_target_disp.get()
        self.app_config["default_resolution"] = self.cfg_def_res.get()
        self.app_config.pop("hide_controls", None)

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                for k, v in self.app_config.items():
                    f.write(f"{k}={v}\n")
            winsound.MessageBeep()
        except: pass

    def toggle_visibility(self, event=None):
        if self.root.winfo_viewable(): self.root.withdraw()
        else: self.root.deiconify(); self.root.attributes("-topmost", True); self.root.after(100, lambda: self.root.attributes("-topmost", False))

    def show_help(self):
        pop = ctk.CTkToplevel(self.root)
        pop.geometry("450x250")
        pop.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 225
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 125
        pop.geometry(f"+{x}+{y}")
        hdr = ctk.CTkFrame(pop, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="HELP - CONTROLLER", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="Quit", width=40, height=30, command=pop.destroy, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right")
        help_text = "- Double-click a row to Edit its details.\n- Use 'Visual Editor' to map connections & layout.\n- Shift+Click in Editor to link source to display.\n- Switch to 'Stream Targets' tab to manage URLs.\n- Configure global rules in 'Settings' tab.\n- Press F8 anytime to show/hide this window."
        ctk.CTkLabel(pop, text=help_text, font=("Consolas", 11), text_color="white", justify="left").pack(pady=20, padx=20, anchor="w")

    def on_close(self):
        self.destroy()
        os._exit(0)

if __name__ == "__main__":
    app = DisplayController()
    app.mainloop()