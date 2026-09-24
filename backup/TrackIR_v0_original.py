# ======================================================
# PROJECT: HEAD ON SMARTVIEW 360 - LITE VERSION 
# (Includes FOV Control, Config, RTSP History, PiP, Modes, Topmost & QA Fixes)
# ======================================================

import os
import sys

if "TCL_LIBRARY" in os.environ: del os.environ["TCL_LIBRARY"]
if "TK_LIBRARY" in os.environ: del os.environ["TK_LIBRARY"]

import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np
import ctypes
from ctypes import wintypes
import winreg
import math

# --- GUI DESIGN STYLE ---
BG_COLOR = "#121212"       
BTN_COLOR = "#333333"      
QUIT_COLOR = "#FF0000"      
TEXT_COLOR = "#FFFFFF"     
ACCENT_COLOR = "#FFFFFF"   

FONT_BOLD = ("Segoe UI", 9, "bold") 
CONFIG_FILE = "config.txt"

# ==========================================
# MODULE 1: TRACKIR CORE
# ==========================================
NP_MAX_ROTATION = 180; NP_MAX_VALUE = 16383; NP_MAX_TRANSLATION = 50 
NPPitch = 2; NPYaw = 4; NPRoll = 1; NPX = 16; NPY = 32; NPZ = 64
class TRACKIRDATA(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_ushort), ("FrameSignature", ctypes.c_ushort), ("IOData", ctypes.c_ulong), ("Roll", ctypes.c_float), ("Pitch", ctypes.c_float), ("Yaw", ctypes.c_float), ("X", ctypes.c_float), ("Y", ctypes.c_float), ("Z", ctypes.c_float), ("Reserved1", ctypes.c_float), ("Reserved2", ctypes.c_float), ("Reserved3", ctypes.c_float), ("Reserved4", ctypes.c_float), ("Reserved5", ctypes.c_float), ("Reserved6", ctypes.c_float), ("Reserved7", ctypes.c_float), ("Reserved8", ctypes.c_float), ("Reserved9", ctypes.c_float)]

class TrackIRManager:
    def __init__(self, window_id):
        self.connected = False; self.dll = None
        try:
            dll_name = "NPClient64.dll" if ctypes.sizeof(ctypes.c_void_p)*8==64 else "NPClient.dll"
            if os.path.exists(dll_name): self.dll = ctypes.WinDLL(os.path.abspath(dll_name))
            else:
                reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER); key = winreg.OpenKey(reg, r'Software\NaturalPoint\NATURALPOINT\NPClient Location'); path = winreg.QueryValueEx(key, "Path")[0]; self.dll = ctypes.WinDLL(os.path.join(path, dll_name))
            self.dll.NP_RegisterWindowHandle(wintypes.HWND(window_id)); self.dll.NP_RequestData(ctypes.c_ushort(NPPitch|NPYaw|NPRoll|NPZ)); self.dll.NP_RegisterProgramProfileID(ctypes.c_ushort(1000))
            if self.dll.NP_StartDataTransmission() == 0: self.dll.NP_StartCursor(); self.connected = True
        except: pass
    def get_data(self):
        if not self.connected: return 0,0,0
        try:
            tir = TRACKIRDATA(); 
            if self.dll.NP_GetData(ctypes.pointer(tir)) == 0: return (tir.Yaw/NP_MAX_VALUE)*NP_MAX_ROTATION, (tir.Pitch/NP_MAX_VALUE)*NP_MAX_ROTATION, (tir.Z/NP_MAX_VALUE)*NP_MAX_TRANSLATION
        except: pass
        return 0,0,0
    def close(self):
        if self.connected: 
            try: self.dll.NP_StopCursor(); self.dll.NP_StopDataTransmission(); self.dll.NP_UnregisterWindowHandle()
            except: pass

# ==========================================
# MODULE 2: LENS ENGINE
# ==========================================
class LensEngine:
    def __init__(self, width, height, fov=70, mode="Standard"):
        self.width = width; self.height = height; self.fov = fov; self.mode = mode
        self.cached_xyz = None; self._precompute_grid()
    def update_fov(self, new_fov): 
        if abs(self.fov - new_fov) > 0.1: self.fov = new_fov; self._precompute_grid()
    def _precompute_grid(self):
        f = 0.5 * self.width / math.tan(0.5 * self.fov * np.pi / 180); cx, cy = self.width / 2, self.height / 2
        x, y = np.meshgrid(np.arange(self.width), np.arange(self.height)); X, Y = (x - cx), (y - cy); Z = np.full_like(x, f)
        norm = np.sqrt(X**2 + Y**2 + Z**2)
        self.cached_xyz = np.stack([X/norm, Y/norm, Z/norm], axis=-1)
    def get_maps(self, yaw_deg, pitch_deg, src_w, src_h, view_mode="360"):
        y, p = np.radians(yaw_deg), np.radians(-pitch_deg)
        Ry = np.array([[np.cos(y), 0, -np.sin(y)], [0, 1, 0], [np.sin(y), 0, np.cos(y)]])
        Rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
        rotated = np.einsum('ij,klj->kli', Ry @ Rx, self.cached_xyz)
        X, Y, Z = rotated[..., 0], rotated[..., 1], rotated[..., 2]
        if self.mode == "Fisheye":
            r_3d = np.sqrt(X**2 + Y**2); theta = np.arctan2(r_3d, Z)
            scale = src_h / 3.14159; r_2d = scale * theta
            with np.errstate(divide='ignore', invalid='ignore'):
                map_x = (r_2d * (X / r_3d)) + (src_w / 2); map_y = (r_2d * (Y / r_3d)) + (src_h / 2)
            map_x[r_3d == 0] = src_w / 2; map_y[r_3d == 0] = src_h / 2
        else:
            if view_mode == "180":
                map_x = ((np.arctan2(X, Z) / np.pi) + 0.5) * src_w
            elif view_mode == "120":
                map_x = ((np.arctan2(X, Z) / ((2/3) * np.pi)) + 0.5) * src_w
            else: # 360
                map_x = ((np.arctan2(X, Z) / (2 * np.pi)) + 0.5) * src_w
            map_y = ((np.arcsin(Y) / np.pi) + 0.5) * src_h
        return map_x.astype(np.float32), map_y.astype(np.float32)

# ==========================================
# MODULE 3: MAIN APP (LITE)
# ==========================================
class Video360App:
    def __init__(self, root):
        self.root = root
        self.root.title("TrackIR Lite")
        self.root.geometry("1100x700") 
        self.root.configure(bg=ACCENT_COLOR) 
        self.root.overrideredirect(True) 
        
        self.main_frame = tk.Frame(self.root, bg=BG_COLOR, bd=1)
        self.main_frame.pack(fill="both", expand=True, padx=1, pady=1)

        self.trackir = TrackIRManager(self.root.winfo_id())
        self.input_mode = "TRACKIR" if self.trackir.connected else "MOUSE"
        
        self.cap = None; self.is_playing = False; self.tk_image = None
        self.last_main_source = None
        
        self.pip_cap = None
        self.pip_enabled = False
        self.pip_idx = -1
        self.pip_rect = [400, -1, 10] 
        self.pip_interaction = None
        self.rtsp_history = []
        
        self.view_mode = "360"
        self.is_topmost = False
        self.is_fullscreen = False

        self.view_w, self.view_h = 1280, 720; self.update_delay = 15
        self.base_fov = 70 # Default to narrower FOV for less distortion and more movement
        self.current_fov = self.base_fov
        self.lens_mode = "Standard" 
        self.lens = LensEngine(self.view_w, self.view_h, fov=self.current_fov, mode=self.lens_mode)
        
        self.yaw = 0; self.pitch = 0; self.home_yaw = 0; self.home_pitch = 0
        self.offset_yaw = 0; self.offset_pitch = 0; self.offset_x = 0; self.offset_y = 0
        self.last_mouse_x = None; self.last_mouse_y = None
        
        self.load_config()
        self.setup_ui()
        self.center_window()
        self.update_controls_visibility()
        
        if self.last_main_source is not None:
            self.load_source(self.last_main_source, silent_fail=True)
            
        self.update_loop()

    def center_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2); y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f'+{x}+{y}')

    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.saved_geometry = self.root.geometry()
            w = self.root.winfo_screenwidth()
            h = self.root.winfo_screenheight()
            self.root.geometry(f"{w}x{h}+0+0")
        else:
            if hasattr(self, 'saved_geometry'):
                self.root.geometry(self.saved_geometry)
            else:
                self.center_window()

    def load_config(self):
        self.home_yaw, self.home_pitch = 0.0, 0.0
        self.pip_idx = -1
        self.pip_rect = [400, -1, 10]
        self.rtsp_history = []
        self.last_main_source = None
        
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    lines = f.read().splitlines()
                    if len(lines) > 0 and lines[0].strip():
                        c = lines[0].split(',')
                        if len(c) >= 2: self.home_yaw, self.home_pitch = float(c[0]), float(c[1])
                        if len(c) >= 3: 
                            self.base_fov = float(c[2])
                            self.current_fov = self.base_fov
                    if len(lines) > 1 and lines[1].strip():
                        c = lines[1].split(',')
                        if len(c) >= 4:
                            self.pip_idx = int(c[0])
                            self.pip_rect = [int(c[1]), int(c[2]), int(c[3])]
                    if len(lines) > 2 and lines[2].strip():
                        self.rtsp_history = lines[2].split(',')
                    if len(lines) > 3 and lines[3].strip() and lines[3].strip() != "None":
                        val = lines[3].strip()
                        try: self.last_main_source = int(val)
                        except ValueError: self.last_main_source = val
            except: pass
            
        self.offset_yaw, self.offset_pitch = self.home_yaw, self.home_pitch
        self.lens.update_fov(self.current_fov)
        
        if self.pip_idx != -1:
            self.pip_cap = cv2.VideoCapture(self.pip_idx)
            if self.pip_cap.isOpened():
                self.pip_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) 
                self.pip_enabled = True
            else:
                self.pip_idx = -1
    
    def save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f: 
                f.write(f"{self.home_yaw},{self.home_pitch},{self.base_fov}\n")
                f.write(f"{self.pip_idx},{self.pip_rect[0]},{self.pip_rect[1]},{self.pip_rect[2]}\n")
                f.write(",".join(self.rtsp_history) + "\n")
                f.write(f"{self.last_main_source}\n")
        except: pass

    def reload_streams(self):
        if self.cap: self.cap.release()
        if self.last_main_source is not None:
            self.load_source(self.last_main_source, silent_fail=False)
            
        if self.pip_enabled and self.pip_idx != -1:
            if self.pip_cap: self.pip_cap.release()
            self.pip_cap = cv2.VideoCapture(self.pip_idx)
            if self.pip_cap.isOpened():
                self.pip_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
        if not self.trackir.connected:
            self.trackir = TrackIRManager(self.root.winfo_id())
            if self.trackir.connected:
                self.input_mode = "TRACKIR"
                self.btn_mode.config(text=f"Tech: {self.input_mode}")

    def ask_camera_index(self, title_text, callback):
        cam_win = tk.Toplevel(self.root)
        cam_win.geometry("260x140")
        cam_win.configure(bg=ACCENT_COLOR)
        cam_win.overrideredirect(True)
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        x = rx + (self.root.winfo_width() // 2) - 130
        y = ry + (self.root.winfo_height() // 2) - 70
        cam_win.geometry(f"+{x}+{y}")
        
        frame = tk.Frame(cam_win, bg=BG_COLOR, bd=1)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        
        def start_win_move(e): cam_win.ox, cam_win.oy = e.x, e.y
        def do_win_move(e): cam_win.geometry(f"+{cam_win.winfo_x()+e.x-cam_win.ox}+{cam_win.winfo_y()+e.y-cam_win.oy}")
        frame.bind("<Button-1>", start_win_move); frame.bind("<B1-Motion>", do_win_move)
        
        title = tk.Label(frame, text=title_text, fg=ACCENT_COLOR, bg=BG_COLOR, font=("Segoe UI", 11, "bold"))
        title.pack(pady=10)
        title.bind("<Button-1>", start_win_move); title.bind("<B1-Motion>", do_win_move)
        
        input_frame = tk.Frame(frame, bg=BG_COLOR)
        input_frame.pack(pady=5)
        
        tk.Label(input_frame, text="Index:", bg=BG_COLOR, fg=TEXT_COLOR, font=FONT_BOLD).pack(side="left", padx=5)
        idx_entry = tk.Entry(input_frame, width=5, bg=BTN_COLOR, fg=TEXT_COLOR, insertbackground=TEXT_COLOR, bd=0, justify="center", font=FONT_BOLD)
        idx_entry.insert(0, "0")
        idx_entry.pack(side="left", padx=5, ipady=3)
        
        def apply_idx():
            try:
                val = int(idx_entry.get())
                cam_win.destroy()
                callback(val)
            except ValueError: pass
                
        btn_frame = tk.Frame(frame, bg=BG_COLOR)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="CONNECT", bg=BTN_COLOR, fg=ACCENT_COLOR, bd=0, width=10, font=FONT_BOLD, command=apply_idx).pack(side="left", padx=5)
        tk.Button(btn_frame, text="CANCEL", bg=QUIT_COLOR, fg=TEXT_COLOR, bd=0, width=10, font=FONT_BOLD, command=cam_win.destroy).pack(side="left", padx=5)

    def open_config(self):
        cfg_win = tk.Toplevel(self.root)
        cfg_win.geometry("300x440") 
        cfg_win.configure(bg=ACCENT_COLOR)
        cfg_win.overrideredirect(True)
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        x = rx + (self.root.winfo_width() // 2) - 150; y = ry + (self.root.winfo_height() // 2) - 220
        cfg_win.geometry(f"+{x}+{y}")
        frame = tk.Frame(cfg_win, bg=BG_COLOR, bd=1)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        
        def start_win_move(e): cfg_win.ox, cfg_win.oy = e.x, e.y
        def do_win_move(e): cfg_win.geometry(f"+{cfg_win.winfo_x()+e.x-cfg_win.ox}+{cfg_win.winfo_y()+e.y-cfg_win.oy}")
        frame.bind("<Button-1>", start_win_move); frame.bind("<B1-Motion>", do_win_move)
        
        title = tk.Label(frame, text="CONFIGURATION", fg=ACCENT_COLOR, bg=BG_COLOR, font=("Segoe UI", 12, "bold"))
        title.pack(pady=10)
        title.bind("<Button-1>", start_win_move); title.bind("<B1-Motion>", do_win_move)
        
        tk.Label(frame, text="Input Lens Type:", fg=TEXT_COLOR, bg=BG_COLOR).pack(pady=2)
        lens_var = tk.StringVar(value=self.lens_mode)
        tk.OptionMenu(frame, lens_var, "Standard", "Fisheye").pack()
        
        tk.Label(frame, text="Resolution Quality:", fg=TEXT_COLOR, bg=BG_COLOR).pack(pady=5)
        res_var = tk.StringVar(value=f"{self.view_w}x{self.view_h}")
        tk.OptionMenu(frame, res_var, "1920x1080", "1280x720", "960x540").pack()
        
        tk.Label(frame, text="Base FOV (Zoom):", fg=TEXT_COLOR, bg=BG_COLOR).pack(pady=(10,0))
        fov_scale = tk.Scale(frame, from_=30, to=120, orient=tk.HORIZONTAL, bg=BG_COLOR, fg=TEXT_COLOR, troughcolor=BTN_COLOR, highlightthickness=0)
        fov_scale.set(self.base_fov); fov_scale.pack(pady=2)

        tk.Label(frame, text="Target FPS:", fg=TEXT_COLOR, bg=BG_COLOR).pack(pady=(10,0))
        fps_scale = tk.Scale(frame, from_=15, to=60, orient=tk.HORIZONTAL, bg=BG_COLOR, fg=TEXT_COLOR, troughcolor=BTN_COLOR, highlightthickness=0)
        fps_scale.set(1000 // self.update_delay); fps_scale.pack(pady=2)
        
        def apply():
            w, h = map(int, res_var.get().split('x'))
            self.view_w, self.view_h = w, h
            self.lens_mode = lens_var.get()
            self.btn_lens.config(text=f"Lens: {self.lens_mode}")
            
            self.base_fov = fov_scale.get()
            self.current_fov = self.base_fov
            self.lens = LensEngine(w, h, fov=self.current_fov, mode=self.lens_mode)
            
            self.update_delay = 1000 // fps_scale.get()
            self.save_config()
            cfg_win.destroy()
            
        tk.Button(frame, text="APPLY", bg=BTN_COLOR, fg=ACCENT_COLOR, bd=0, command=apply, width=15).pack(pady=15)
        tk.Button(frame, text="CANCEL", bg=QUIT_COLOR, fg=TEXT_COLOR, bd=0, command=cfg_win.destroy, width=15).pack()

    def open_stream_dialog(self):
        stream_win = tk.Toplevel(self.root)
        stream_win.geometry("450x290") 
        stream_win.configure(bg=ACCENT_COLOR)
        stream_win.overrideredirect(True)
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        x = rx + (self.root.winfo_width() // 2) - 225; y = ry + (self.root.winfo_height() // 2) - 145
        stream_win.geometry(f"+{x}+{y}")
        frame = tk.Frame(stream_win, bg=BG_COLOR, bd=1)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        
        def start_win_move(e): stream_win.ox, stream_win.oy = e.x, e.y
        def do_win_move(e): stream_win.geometry(f"+{stream_win.winfo_x()+e.x-stream_win.ox}+{stream_win.winfo_y()+e.y-stream_win.oy}")
        frame.bind("<Button-1>", start_win_move); frame.bind("<B1-Motion>", do_win_move)
        
        title = tk.Label(frame, text="ENTER RTSP / HTTP URL", fg=ACCENT_COLOR, bg=BG_COLOR, font=("Segoe UI", 11, "bold"))
        title.pack(pady=10)
        title.bind("<Button-1>", start_win_move); title.bind("<B1-Motion>", do_win_move)
        
        input_frame = tk.Frame(frame, bg=BG_COLOR)
        input_frame.pack(pady=5)
        url_entry = tk.Entry(input_frame, width=35, bg=BTN_COLOR, fg=TEXT_COLOR, insertbackground=TEXT_COLOR, bd=0)
        url_entry.pack(side="left", padx=5, ipady=3)
        
        def paste_url():
            try: url_entry.delete(0, tk.END); url_entry.insert(0, self.root.clipboard_get())
            except: pass
        tk.Button(input_frame, text="PASTE", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, font=FONT_BOLD, command=paste_url).pack(side="left")
        
        if self.rtsp_history:
            hist_frame = tk.Frame(frame, bg=BG_COLOR)
            hist_frame.pack(pady=2)
            for url in reversed(self.rtsp_history[-3:]):
                def set_url(u=url):
                    url_entry.delete(0, tk.END)
                    url_entry.insert(0, u)
                display_url = url if len(url) <= 40 else url[:37] + "..."
                tk.Button(hist_frame, text=display_url, bg=BG_COLOR, fg="gray", bd=0, font=("Segoe UI", 8), cursor="hand2", command=set_url).pack(pady=1)

        def connect_url():
            url = url_entry.get().strip()
            if url: 
                if url in self.rtsp_history: self.rtsp_history.remove(url)
                self.rtsp_history.append(url)
                if len(self.rtsp_history) > 5: self.rtsp_history.pop(0)
                self.save_config()
                self.load_source(url, silent_fail=False)
            stream_win.destroy()
            
        def connect_usb():
            stream_win.destroy()
            self.ask_camera_index("MAIN USB CAMERA", lambda idx: self.load_source(idx, silent_fail=False))
            
        def connect_pip_usb():
            stream_win.destroy()
            self.ask_camera_index("PIP USB CAMERA", self._on_pip_selected)
            
        btn_action_frame = tk.Frame(frame, bg=BG_COLOR)
        btn_action_frame.pack(pady=10)
        tk.Button(btn_action_frame, text="URL", bg=BTN_COLOR, fg=ACCENT_COLOR, bd=0, width=10, command=connect_url).pack(side="left", padx=5)
        tk.Button(btn_action_frame, text="MAIN CAM", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, width=10, command=connect_usb).pack(side="left", padx=5)
        tk.Button(btn_action_frame, text="PIP CAM", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, width=10, command=connect_pip_usb).pack(side="left", padx=5)
        
        tk.Button(frame, text="CANCEL", bg=QUIT_COLOR, fg=TEXT_COLOR, bd=0, width=15, command=stream_win.destroy).pack(pady=5)

    def toggle_lens_type(self):
        self.lens_mode = "Fisheye" if self.lens_mode == "Standard" else "Standard"
        self.btn_lens.config(text=f"Lens: {self.lens_mode}")
        self.lens = LensEngine(self.view_w, self.view_h, fov=self.current_fov, mode=self.lens_mode)

    def toggle_view_mode(self):
        if self.view_mode == "360": self.view_mode = "180"
        elif self.view_mode == "180": self.view_mode = "120"
        else: self.view_mode = "360"
        self.btn_view_mode.config(text=f"Mode: {self.view_mode}")
        self.offset_yaw = 0
        self.yaw = 0

    def _on_pip_selected(self, idx):
        if self.pip_cap: self.pip_cap.release()
        self.pip_cap = cv2.VideoCapture(idx)
        if self.pip_cap.isOpened():
            self.pip_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) 
            self.pip_enabled = True
            self.pip_idx = idx
            self.btn_pip.config(text="PiP: ON")
            self.save_config()

    def toggle_pip(self):
        if not self.pip_enabled:
            if self.pip_idx != -1:
                self.pip_cap = cv2.VideoCapture(self.pip_idx)
                if self.pip_cap.isOpened():
                    self.pip_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    self.pip_enabled = True
                    self.btn_pip.config(text="PiP: ON")
                else:
                    self.ask_camera_index("PIP USB CAMERA", self._on_pip_selected)
            else:
                self.ask_camera_index("PIP USB CAMERA", self._on_pip_selected)
        else:
            self.pip_enabled = False
            self.btn_pip.config(text="PiP: OFF")
            if self.pip_cap:
                self.pip_cap.release()
                self.pip_cap = None
            self.save_config()

    def toggle_topmost(self):
        self.is_topmost = not getattr(self, 'is_topmost', False)
        self.root.attributes("-topmost", self.is_topmost)
        self.btn_topmost.config(text="Top: ON" if self.is_topmost else "Top: OFF")

    def setup_ui(self):
        self.top_bar = tk.Frame(self.main_frame, bg=BG_COLOR, height=35); self.top_bar.pack(side="top", fill="x"); self.top_bar.pack_propagate(False)
        self.top_bar.bind("<Button-1>", self.start_move); self.top_bar.bind("<B1-Motion>", self.do_move)

        tk.Label(self.top_bar, text="TRACKIR LITE", bg=BG_COLOR, fg=ACCENT_COLOR, font=FONT_BOLD).pack(side="left", padx=10)
        
        btn_quit = tk.Button(self.top_bar, text="X", bg=QUIT_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=4, font=FONT_BOLD, command=self.quit_app)
        btn_quit.pack(side="right", fill="y", padx=2, pady=2)
        
        self.btn_topmost = tk.Button(self.top_bar, text="Top: ON" if self.is_topmost else "Top: OFF", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=8, command=self.toggle_topmost)
        self.btn_topmost.pack(side="right", fill="y", padx=2, pady=2)
        
        btn_full = tk.Button(self.top_bar, text="Full", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=6, command=self.toggle_fullscreen)
        btn_full.pack(side="right", fill="y", padx=2, pady=2)
        
        btn_cfg = tk.Button(self.top_bar, text="Config", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=6, command=self.open_config)
        btn_cfg.pack(side="right", fill="y", padx=2, pady=2)

        self.btn_pip = tk.Button(self.top_bar, text="PiP: ON" if self.pip_enabled else "PiP: OFF", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=8, command=self.toggle_pip)
        self.btn_pip.pack(side="right", fill="y", padx=2, pady=2)

        self.mouse_controls_frame = tk.Frame(self.top_bar, bg=BG_COLOR)
        self.sens_z = tk.Scale(self.mouse_controls_frame, from_=0.001, to=0.05, resolution=0.001, orient=tk.HORIZONTAL, length=60, bg=BG_COLOR, fg=TEXT_COLOR, troughcolor=BTN_COLOR, highlightthickness=0, showvalue=0); self.sens_z.set(0.01); self.sens_z.pack(side="right", padx=5)
        tk.Label(self.mouse_controls_frame, text="Z:", bg=BG_COLOR, fg=ACCENT_COLOR, font=("Segoe UI", 7)).pack(side="right")
        self.sens_x = tk.Scale(self.mouse_controls_frame, from_=0.001, to=0.05, resolution=0.001, orient=tk.HORIZONTAL, length=60, bg=BG_COLOR, fg=TEXT_COLOR, troughcolor=BTN_COLOR, highlightthickness=0, showvalue=0); self.sens_x.set(0.01); self.sens_x.pack(side="right", padx=5)
        tk.Label(self.mouse_controls_frame, text="X:", bg=BG_COLOR, fg=ACCENT_COLOR, font=("Segoe UI", 7)).pack(side="right")

        self.btn_mode = tk.Button(self.top_bar, text=f"Tech: {self.input_mode}", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=12, command=self.toggle_mode); self.btn_mode.pack(side="right", fill="y", padx=2, pady=2)
        
        self.btn_view_mode = tk.Button(self.top_bar, text=f"Mode: {self.view_mode}", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=10, command=self.toggle_view_mode)
        self.btn_view_mode.pack(side="right", fill="y", padx=2, pady=2)
        
        self.btn_lens = tk.Button(self.top_bar, text=f"Lens: {self.lens_mode}", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=12, command=self.toggle_lens_type); self.btn_lens.pack(side="right", fill="y", padx=2, pady=2)

        btn_reset = tk.Button(self.top_bar, text="Reset", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=6, command=self.reset_to_home)
        btn_reset.pack(side="right", fill="y", padx=2, pady=2)
        btn_sethome = tk.Button(self.top_bar, text="Set Home", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=8, command=self.set_home_point)
        btn_sethome.pack(side="right", fill="y", padx=2, pady=2)

        self.main_area = tk.Frame(self.main_frame, bg=BG_COLOR); self.main_area.pack(fill="both", expand=True, padx=5, pady=5)
        self.canvas = tk.Canvas(self.main_area, bg="#000000", highlightthickness=0); self.canvas.pack(fill="both", expand=True)
        
        self.bottom_bar = tk.Frame(self.main_frame, bg=BG_COLOR, height=40); self.bottom_bar.pack(side="bottom", fill="x", pady=5)
        
        center_frame = tk.Frame(self.bottom_bar, bg=BG_COLOR)
        center_frame.pack(side="top")
        tk.Button(center_frame, text="Load Stream", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", padx=20, pady=5, command=self.open_stream_dialog).pack(side="left", padx=5)
        tk.Button(center_frame, text="Reload Cams", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", padx=20, pady=5, command=self.reload_streams).pack(side="left", padx=5)

        self.pitch_controls_frame = tk.Frame(self.bottom_bar, bg=BG_COLOR)
        self.btn_pitch_up = tk.Button(self.pitch_controls_frame, text="+", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=4, font=FONT_BOLD, command=lambda: self.change_pitch(5)); self.btn_pitch_up.pack(side="right", padx=5)
        self.btn_pitch_down = tk.Button(self.pitch_controls_frame, text="-", bg=BTN_COLOR, fg=TEXT_COLOR, bd=0, relief="flat", width=4, font=FONT_BOLD, command=lambda: self.change_pitch(-5)); self.btn_pitch_down.pack(side="right", padx=5)

        tk.Label(self.bottom_bar, text="oT", bg=BG_COLOR, fg=BTN_COLOR, font=("Segoe UI", 10, "bold")).place(relx=0.98, rely=0.5, anchor="e")

        self.root.bind("<F5>", lambda e: self.reset_to_home())
        self.root.bind("<F6>", lambda e: self.set_home_point())
        
        self.canvas.bind("<Motion>", self.handle_mouse_move)
        self.canvas.bind("<Button-1>", self.pip_mouse_down)
        self.canvas.bind("<B1-Motion>", self.pip_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.pip_mouse_up)
        self.canvas.bind("<Leave>", self.reset_mouse_tracking)

    def update_controls_visibility(self):
        if self.input_mode == "MOUSE": self.mouse_controls_frame.pack(side="right", fill="y", padx=5); self.pitch_controls_frame.place(relx=0.9, rely=0.5, anchor="e")
        else: self.mouse_controls_frame.pack_forget(); self.pitch_controls_frame.place_forget()

    def toggle_mode(self):
        if self.input_mode == "MOUSE":
            if self.trackir.connected: self.input_mode = "TRACKIR"; self.btn_mode.config(text="Tech: TrackIR")
            else: messagebox.showerror("Error", "TrackIR not connected.")
        else: self.input_mode = "MOUSE"; self.btn_mode.config(text="Tech: Mouse")
        self.update_controls_visibility()

    def start_move(self, event): 
        if not self.is_fullscreen:
            self.offset_x = event.x
            self.offset_y = event.y
            
    def do_move(self, event): 
        if not self.is_fullscreen:
            self.root.geometry(f"+{self.root.winfo_x()+event.x-self.offset_x}+{self.root.winfo_y()+event.y-self.offset_y}")
            
    def quit_app(self): 
        self.is_playing = False
        if self.cap: self.cap.release()
        if self.pip_cap: self.pip_cap.release()
        self.trackir.close()
        self.root.quit()
        
    def set_home_point(self): self.home_yaw, self.home_pitch = self.yaw, self.pitch; self.save_config()
    
    def reset_to_home(self):
        self.current_fov = self.base_fov; self.lens.update_fov(self.base_fov)
        if self.input_mode == "MOUSE": self.offset_yaw, self.offset_pitch = self.home_yaw, self.home_pitch
        elif self.input_mode == "TRACKIR":
            ty, tp, tz = self.trackir.get_data(); self.offset_yaw = self.home_yaw - (ty * 2); self.offset_pitch = self.home_pitch - (tp * 2)

    def load_source(self, source, silent_fail=False): 
        if self.cap: self.cap.release()
        self.cap = cv2.VideoCapture(source)
        if self.cap.isOpened(): 
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) 
            self.is_playing = True
            self.last_main_source = source
            self.save_config()
            self.reset_to_home()
        else: 
            if not silent_fail:
                messagebox.showerror("Error", "Could not open source.")

    def change_pitch(self, delta): self.offset_pitch = max(-89, min(89, self.offset_pitch + delta))
    def reset_mouse_tracking(self, event): self.last_mouse_x = None; self.last_mouse_y = None

    def handle_mouse_move(self, event):
        if self.input_mode != "MOUSE": return
        if self.last_mouse_x is None: self.last_mouse_x, self.last_mouse_y = event.x, event.y; return
        dx, dy = event.x - self.last_mouse_x, event.y - self.last_mouse_y
        self.last_mouse_x, self.last_mouse_y = event.x, event.y
        self.offset_yaw += dx * self.sens_x.get() * 5
        new_fov = self.current_fov + (dy * self.sens_z.get() * 10)
        self.current_fov = max(20, min(130, new_fov))
        self.lens.update_fov(self.current_fov)

    def pip_mouse_down(self, event):
        if not self.pip_enabled: return
        pw, px, py = self.pip_rect
        ph = int(pw * 9 / 16)
        if px <= event.x <= px + pw and py <= event.y <= py + ph:
            if px + pw - 20 <= event.x <= px + pw and py + ph - 20 <= event.y <= py + ph:
                self.pip_interaction = 'resize'
            else:
                self.pip_interaction = 'move'
            self.pip_drag_start_x = event.x
            self.pip_drag_start_y = event.y
            self.pip_orig_rect = list(self.pip_rect)

    def pip_mouse_drag(self, event):
        if not self.pip_enabled or not self.pip_interaction: return
        if self.pip_interaction == 'move':
            dx = event.x - self.pip_drag_start_x
            dy = event.y - self.pip_drag_start_y
            self.pip_rect[1] = self.pip_orig_rect[1] + dx
            self.pip_rect[2] = self.pip_orig_rect[2] + dy
        elif self.pip_interaction == 'resize':
            dx = event.x - self.pip_drag_start_x
            win_w = self.canvas.winfo_width()
            win_h = self.canvas.winfo_height()
            
            new_w = max(150, min(win_w, self.pip_orig_rect[0] + dx)) 
            if int(new_w * 9 / 16) > win_h:
                new_w = int(win_h * 16 / 9)
                
            self.pip_rect[0] = new_w

    def pip_mouse_up(self, event):
        if self.pip_interaction:
            self.pip_interaction = None
            self.save_config()

    def process_frame_and_display(self, frame, src_w, src_h):
        if self.input_mode == "TRACKIR":
            ty, tp, tz = self.trackir.get_data()
            self.yaw, self.pitch = (ty*2)+self.offset_yaw, (tp*2)+self.offset_pitch
            self.current_fov = max(30, min(130, self.base_fov + tz*1.5)); self.lens.update_fov(self.current_fov)
        else: self.yaw, self.pitch = self.offset_yaw, self.offset_pitch
        
        if self.view_mode == "180":
            limit = 90 - (self.current_fov / 2)
            if limit < 0: limit = 0
            self.yaw = max(-limit, min(limit, self.yaw))
            if self.input_mode == "MOUSE": self.offset_yaw = self.yaw
        elif self.view_mode == "120":
            limit = 60 - (self.current_fov / 2)
            if limit < 0: limit = 0
            self.yaw = max(-limit, min(limit, self.yaw))
            if self.input_mode == "MOUSE": self.offset_yaw = self.yaw
                
        map_x, map_y = self.lens.get_maps(self.yaw, self.pitch, src_w, src_h, self.view_mode)
        corrected = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)
        win_w, win_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        
        if win_w > 10:
            final_img = cv2.resize(corrected, (win_w, win_h))
            
            if self.pip_enabled and self.pip_cap:
                ret, pip_frame = self.pip_cap.read()
                if ret:
                    pw, px, py = self.pip_rect
                    ph = int(pw * 9 / 16)
                    
                    if px == -1: 
                        px = (win_w - pw) // 2
                        self.pip_rect[1] = px
                        
                    px = max(0, min(px, win_w - pw))
                    py = max(0, min(py, win_h - ph))
                    self.pip_rect[1], self.pip_rect[2] = px, py
                    
                    pip_frame_resized = cv2.resize(pip_frame, (pw, ph))
                    cv2.rectangle(pip_frame_resized, (0, 0), (pw-1, ph-1), (255, 255, 255), 2)
                    cv2.rectangle(pip_frame_resized, (pw-12, ph-12), (pw, ph), (255, 255, 255), -1)
                    
                    if px >= 0 and py >= 0 and px + pw <= win_w and py + ph <= win_h:
                        final_img[py:py+ph, px:px+pw] = pip_frame_resized
            
            img_pil = Image.fromarray(cv2.cvtColor(final_img, cv2.COLOR_BGR2RGB))
            self.tk_image = ImageTk.PhotoImage(image=img_pil)
            self.canvas.create_image(0, 0, image=self.tk_image, anchor="nw")

    def update_loop(self):
        if self.is_playing and self.cap:
            ret, frame = self.cap.read()
            if not ret: 
                if self.cap.get(cv2.CAP_PROP_FRAME_COUNT) > 0: self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0); ret, frame = self.cap.read()
                else: pass 
            if ret:
                src_h, src_w = frame.shape[:2]
                if src_w > 2500: scale = 2500 / src_w; frame = cv2.resize(frame, (0,0), fx=scale, fy=scale); src_h, src_w = frame.shape[:2]
                self.process_frame_and_display(frame, src_w, src_h)
        self.root.after(self.update_delay, self.update_loop)

if __name__ == "__main__":
    try: root = tk.Tk(); app = Video360App(root); root.protocol("WM_DELETE_WINDOW", app.quit_app); root.mainloop()
    except Exception as e: ctypes.windll.user32.MessageBoxW(0, str(e), "Error", 0x10)