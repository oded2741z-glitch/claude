import warnings
warnings.filterwarnings("ignore") # חוסם לחלוטין את כל האזהרות המציקות כולל eventlet

import customtkinter as ctk
import tkinter as tk
import os
import time
import subprocess
import sys
import threading
import json
import copy
import eventlet
import socketio

import shared

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
SCENES_FILE = "scenes.json"
CELL_W, CELL_H = 150, 90

# ==========================================
# INTERNAL SERVER (Runs in background)
# ==========================================
server_sio = socketio.Server(cors_allowed_origins='*', async_mode='eventlet')
server_app = socketio.WSGIApp(server_sio)
room_state = {}
sid_to_target = {}
networked_viewers = {}

@server_sio.event
def connect(sid, environ):
    server_sio.emit('init_sync', {
        "state": room_state, 
        "viewers": networked_viewers
    }, to=sid)

@server_sio.event
def update_screen_state(sid, data):
    target = data.get("target")
    payload = data.get("payload")
    if target:
        if target not in room_state:
            room_state[target] = {}
        room_state[target].update(payload)
        server_sio.emit('screen_update', {"target": target, "payload": room_state[target]})

@server_sio.event
def request_snapshot(sid, data):
    server_sio.emit('execute_snapshot', data)

@server_sio.event
def announce_viewer(sid, data):
    target = data.get("target")
    layout = data.get("layout", 4)
    sid_to_target[sid] = target
    networked_viewers[target] = layout
    server_sio.emit("viewer_online", {"target": target, "layout": layout})

@server_sio.event
def disconnect(sid):
    if sid in sid_to_target:
        target = sid_to_target.pop(sid)
        if target not in sid_to_target.values():
            if target in networked_viewers:
                del networked_viewers[target]
            server_sio.emit("viewer_offline", {"target": target})

@server_sio.event
def kill_viewer(sid, data):
    server_sio.emit("kill_command", data)

def run_server():
    eventlet.wsgi.server(eventlet.listen(('0.0.0.0', 5000)), server_app, log_output=False)

threading.Thread(target=run_server, daemon=True).start()
time.sleep(0.5) 

# ==========================================
# CONTROLLER CLIENT & GUI
# ==========================================
sio = socketio.Client()

class DisplayController(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Display Room Controller")
        self.geometry("1000x750")
        self.configure(fg_color=COLORS["BG_MAIN"])
        self.overrideredirect(True)
        
        self.rows, self.cols = 6, 5
        self.connections = set()
        self.nodes = {}
        self.selected_targets = set()
        
        self.targets_dict = {"None": ""}
        self.target_names = ["None"]
        self.current_stream_target = None
        self.current_fs_index = -1
        
        self.active_viewers = {}
        self.screen_layouts = {}
        self.app_state = {}
        self.is_blackout = False

        self.scenes_data = {}
        if os.path.exists(SCENES_FILE):
            try:
                with open(SCENES_FILE, "r", encoding="utf-8") as f:
                    self.scenes_data = json.load(f)
            except Exception as e:
                print(f"Failed to load scenes: {e}")
                
        self.scene_names = list(self.scenes_data.keys())
        if not self.scene_names:
            self.scene_names = ["Scene 1", "Scene 2", "Scene 3"]

        try:
            import keyboard
            keyboard.add_hotkey("f4", self.toggle_visibility)
        except:
            self.bind_all("<F4>", self.toggle_visibility)

        self._load_targets()
        self._setup_ui()
        self._load_data()
        self.draw_graph()
        
        @sio.event
        def init_sync(data):
            self.app_state = data.get("state", {})
            viewers = data.get("viewers", {})
            for v, layout in viewers.items():
                if v not in self.active_viewers:
                    self.active_viewers[v] = "NETWORKED"
                    self.screen_layouts[v] = layout
            self.after(0, self.update_ui_from_state)
            
        @sio.event
        def screen_update(data):
            target = data.get("target")
            payload = data.get("payload")
            if target not in self.app_state:
                self.app_state[target] = {}
            self.app_state[target].update(payload)
            self.after(0, self.update_ui_from_state)
            
        @sio.event
        def viewer_online(data):
            target = data.get("target")
            layout = data.get("layout", 4)
            if target not in self.active_viewers:
                self.active_viewers[target] = "NETWORKED"
            self.screen_layouts[target] = layout
            self.after(0, self.update_viewers_ui, target)

        @sio.event
        def viewer_offline(data):
            target = data.get("target")
            if target in self.active_viewers:
                del self.active_viewers[target]
            self.after(0, self.update_viewers_ui, target)
            
        def connect_socket():
            try:
                if not sio.connected:
                    sio.connect(shared.SERVER_URL)
                    sio.wait()
            except Exception as e:
                time.sleep(3)
                connect_socket()
                
        threading.Thread(target=connect_socket, daemon=True).start()
        self.check_processes()
        self.log("[SYSTEM] Internal Server & Controller Started.")
        
        self.after(2500, self._auto_load_default)

    def _auto_load_default(self):
        config = shared.load_config()
        default_scene = config.get("default_scene", "")
        if default_scene and default_scene in self.scenes_data:
            self.scene_var.set(default_scene)
            self.log(f"[SYSTEM] Auto-loading default scene: '{default_scene}'...")
            self.load_scene()

    def is_controller_on_screen(self, lbl):
        try:
            node = self.nodes.get(lbl)
            if not node or node["type"] == "GPU": return False
            
            offset_parts = node["offset"].split()
            screen_x = int(offset_parts[0].split(":")[1])
            screen_y = int(offset_parts[1].split(":")[1])
            
            res_parts = node["res"].split("x")
            screen_w = int(res_parts[0])
            screen_h = int(res_parts[1])
            
            ctrl_x = self.winfo_x() + (self.winfo_width() // 2)
            ctrl_y = self.winfo_y() + (self.winfo_height() // 2)
            
            if screen_x <= ctrl_x <= (screen_x + screen_w) and screen_y <= ctrl_y <= (screen_y + screen_h):
                return True
        except:
            pass
        return False

    def update_viewers_ui(self, target):
        self.draw_graph()
        if self.current_stream_target == target:
            self.sync_stream_ui_from_state(target)

    def update_ui_from_state(self):
        self.draw_graph()
        if self.current_stream_target:
            self.sync_stream_ui_from_state(self.current_stream_target)

    def check_processes(self):
        changed = False
        for lbl in list(self.active_viewers.keys()):
            p = self.active_viewers[lbl]
            if p != "NETWORKED" and p.poll() is not None:
                del self.active_viewers[lbl]
                self.log(f"[SYSTEM] Viewer for '{lbl}' terminated locally.")
                changed = True
        
        if changed:
            self.draw_graph()
            if self.current_stream_target:
                self.sync_stream_ui_from_state(self.current_stream_target)
                
        self.after(2000, self.check_processes)

    def _load_targets(self):
        targets = shared.load_targets()
        self.targets_dict = {"None": ""}
        for t in targets:
            self.targets_dict[t["name"]] = t["url"]
        self.target_names = list(self.targets_dict.keys())

    def _setup_ui(self):
        self.header = ctk.CTkFrame(self, height=45, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        self.header.pack(side="top", fill="x")
        ctk.CTkLabel(self.header, text="DISPLAY ROOM - CONTROLLER (HOST)", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=20)
        
        ctk.CTkButton(self.header, text="Quit", width=60, height=30, command=self.on_close, fg_color=COLORS["QUIT_BTN"], hover_color="red", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=10)
        ctk.CTkButton(self.header, text="Help", width=60, height=30, command=self.show_help, fg_color=COLORS["BTN_BASE"], text_color="white", corner_radius=0, font=("Consolas", 11, "bold")).pack(side="right", padx=5)

        def start_move(e): self.x, self.y = e.x, e.y
        def do_move(e): self.geometry(f"+{self.winfo_x() + e.x - self.x}+{self.winfo_y() + e.y - self.y}")
        def stop_move(e): 
            self.draw_graph()
            if self.current_stream_target:
                self.sync_stream_ui_from_state(self.current_stream_target)

        self.header.bind("<ButtonPress-1>", start_move)
        self.header.bind("<B1-Motion>", do_move)
        self.header.bind("<ButtonRelease-1>", stop_move)

        main_body = ctk.CTkFrame(self, fg_color="transparent")
        main_body.pack(fill="both", expand=True, padx=15, pady=10)

        btn_font = ("Consolas", 11, "bold")

        self.right_panel = ctk.CTkFrame(main_body, fg_color="transparent")
        self.right_panel.pack(fill="both", expand=True)

        self.canvas_frame = ctk.CTkFrame(self.right_panel, fg_color="#000000", corner_radius=0, border_width=1, border_color=COLORS["GRID_LINE"])
        self.canvas_frame.pack(side="top", fill="both", expand=True, pady=(0, 10))
        self.canvas = tk.Canvas(self.canvas_frame, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.stream_panel = tk.LabelFrame(self.right_panel, text="Stream Controls", bg=COLORS["BG_MAIN"], fg=COLORS["ACCENT"], bd=1, font=("Consolas", 10))
        self.stream_panel.pack(side="bottom", fill="x")

        stream_left = ctk.CTkFrame(self.stream_panel, fg_color="transparent")
        stream_left.pack(side="left", fill="y", padx=15, pady=10)
        
        self.stream_title = ctk.CTkLabel(stream_left, text="Select a screen to edit", font=("Consolas", 12, "bold"), text_color="white")
        self.stream_title.pack(pady=(5, 10))
        
        self.btn_blackout = ctk.CTkButton(stream_left, text="BLACKOUT\nALL SCREENS", command=self.toggle_blackout, fg_color=COLORS["SELECTED"], width=240, height=35, font=btn_font, corner_radius=0)
        self.btn_blackout.pack(pady=(0, 5))

        self.btn_open_folder = ctk.CTkButton(stream_left, text="OPEN SNAPSHOTS FOLDER", command=self.open_snapshots_folder, fg_color=COLORS["BTN_BASE"], width=240, height=25, font=("Consolas", 10, "bold"), corner_radius=0)
        self.btn_open_folder.pack(pady=(0, 15))

        scene_frame = ctk.CTkFrame(stream_left, fg_color="transparent")
        scene_frame.pack(pady=(10, 0))

        ctk.CTkLabel(scene_frame, text="-- SCENES --", font=("Consolas", 11, "bold"), text_color="#888888").pack(pady=(0, 5))

        self.scene_var = ctk.StringVar(value=self.scene_names[0])
        self.scene_cb = ctk.CTkOptionMenu(scene_frame, variable=self.scene_var, values=self.scene_names, width=240, height=30, fg_color=COLORS["BTN_BASE"], button_color=COLORS["ACCENT"])
        self.scene_cb.pack(pady=(0, 5))

        btn_row = ctk.CTkFrame(scene_frame, fg_color="transparent")
        btn_row.pack(fill="x")
        
        btn_w = 56
        ctk.CTkButton(btn_row, text="LOAD", command=self.load_scene, width=btn_w, height=25, fg_color=COLORS["SELECTED"], corner_radius=0, font=("Consolas", 10, "bold")).pack(side="left", padx=(0, 2))
        ctk.CTkButton(btn_row, text="SAVE", command=self.save_scene, width=btn_w, height=25, fg_color=COLORS["BTN_BASE"], border_width=1, border_color=COLORS["SELECTED"], corner_radius=0, font=("Consolas", 10, "bold"), hover_color=COLORS["SELECTED"]).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="RENAME", command=self.rename_scene, width=btn_w, height=25, fg_color=COLORS["BTN_BASE"], corner_radius=0, font=("Consolas", 10, "bold")).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="DEL", command=self.delete_scene, width=btn_w, height=25, fg_color=COLORS["QUIT_BTN"], corner_radius=0, font=("Consolas", 10, "bold"), hover_color="red").pack(side="left", padx=(2, 0))

        stream_right = ctk.CTkFrame(self.stream_panel, fg_color="transparent")
        stream_right.pack(side="left", fill="both", expand=True, padx=10, pady=5)
        
        stream_right.grid_columnconfigure(0, weight=1)
        stream_right.grid_columnconfigure(1, weight=1)

        self.stream_vars = []
        self.stream_menus = []
        self.fs_btns = []
        self.snap_btns = []
        self.stream_cells = []
        quad_names = ["Stream 1 (Main)", "Stream 2 (Right)", "Stream 3 (Bottom L)", "Stream 4 (Bottom R)"]
        
        for i in range(4):
            r = i // 2
            c = i % 2
            cell = ctk.CTkFrame(stream_right, fg_color="transparent")
            cell.grid(row=r, column=c, padx=15, pady=5, sticky="ew")
            self.stream_cells.append(cell)

            top_row = ctk.CTkFrame(cell, fg_color="transparent")
            top_row.pack(fill="x", pady=(0, 2))
            ctk.CTkLabel(top_row, text=f"{quad_names[i]}", font=("Consolas", 10)).pack(side="left")
            
            btn_snap = ctk.CTkButton(top_row, text="snapshots", width=60, height=20, corner_radius=0, font=("Consolas", 10), fg_color=COLORS["BG_MAIN"], hover_color=COLORS["ACCENT"], border_width=1, border_color=COLORS["BTN_BASE"], command=lambda idx=i: self.take_snapshot(idx))
            btn_snap.pack(side="right")
            self.snap_btns.append(btn_snap)
            
            row_ctrl = ctk.CTkFrame(cell, fg_color="transparent")
            row_ctrl.pack(fill="x")
            var = ctk.StringVar(value="None")
            self.stream_vars.append(var)
            cb = ctk.CTkOptionMenu(row_ctrl, variable=var, values=self.target_names, command=lambda v, idx=i: self.on_stream_change(idx, v), height=28, fg_color=COLORS["BTN_BASE"], button_color=COLORS["ACCENT"], button_hover_color=COLORS["SELECTED"])
            cb.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self.stream_menus.append(cb)
            
            btn_fs = ctk.CTkButton(row_ctrl, text="⤢", width=30, height=28, command=lambda idx=i: self.on_fs_toggle(idx), fg_color=COLORS["BTN_BASE"])
            btn_fs.pack(side="right")
            self.fs_btns.append(btn_fs)

        self._disable_stream_controls()
        ctk.CTkLabel(self, text="oT", font=("Consolas", 10), text_color="#333333").place(relx=0.99, rely=0.99, anchor="se")

    def _force_sync_scene(self, saved_state):
        for target, payload in saved_state.items():
            if sio.connected:
                sio.emit("update_screen_state", {"target": target, "payload": payload})

    def _resync_single_screen(self, lbl):
        if lbl in self.app_state and sio.connected:
            sio.emit("update_screen_state", {"target": lbl, "payload": self.app_state[lbl]})

    def load_scene(self):
        scene_name = self.scene_var.get()
        if scene_name not in self.scenes_data:
            self.log(f"[WARNING] Scene '{scene_name}' is empty or not found.")
            return
            
        scene_data = self.scenes_data[scene_name]
        
        if "state" in scene_data and "layouts" in scene_data:
            saved_state = copy.deepcopy(scene_data["state"])
            saved_layouts = copy.deepcopy(scene_data["layouts"])
        else:
            saved_state = copy.deepcopy(scene_data)
            saved_layouts = {}
            
        for target in list(self.app_state.keys()):
            if target not in saved_state:
                empty_payload = {
                    "0": "", "0_name": "", "1": "", "1_name": "",
                    "2": "", "2_name": "", "3": "", "3_name": "",
                    "fullscreen": "-1", "blackout": "False"
                }
                if sio.connected:
                    sio.emit("update_screen_state", {"target": target, "payload": empty_payload})
                if target in room_state:
                    room_state[target].update(empty_payload)

        for target, payload in saved_state.items():
            if target not in room_state:
                room_state[target] = {}
            room_state[target].update(payload)
            if target not in self.app_state:
                self.app_state[target] = {}
            self.app_state[target].update(payload)
            
        old_layouts = copy.deepcopy(self.screen_layouts)
        if saved_layouts:
            self.screen_layouts = copy.deepcopy(saved_layouts)

        if not self.current_stream_target and saved_state:
            first_target = list(saved_state.keys())[0]
            self.current_stream_target = first_target
            self.selected_targets = {first_target}
        elif self.current_stream_target and self.current_stream_target not in saved_state:
            first_target = list(saved_state.keys())[0]
            self.current_stream_target = first_target
            self.selected_targets = {first_target}

        self.update_ui_from_state()

        if saved_layouts:
            for target in list(self.active_viewers.keys()):
                if target not in saved_layouts:
                    self.log(f"[SYSTEM] Closing '{target}' (Not in scene)...")
                    self._kill_viewer_local(target)

            for target, desired_layout in saved_layouts.items():
                current_layout = old_layouts.get(target)
                is_active = target in self.active_viewers
                
                if is_active and current_layout != desired_layout:
                    self.log(f"[SYSTEM] Adjusting '{target}' layout to {desired_layout}...")
                    self._kill_viewer_local(target)
                    self.after(1500, lambda t=target, l=desired_layout: self._launch_viewer_process(t, l))
                elif not is_active:
                    self.log(f"[SYSTEM] Restoring '{target}' in {desired_layout}-Screen mode...")
                    self.after(500, lambda t=target, l=desired_layout: self._launch_viewer_process(t, l))
                    
        self._force_sync_scene(saved_state)
        self.after(2000, lambda: self._force_sync_scene(saved_state))
        self.after(4000, lambda: self._force_sync_scene(saved_state))
        self.after(6000, lambda: self._force_sync_scene(saved_state))
                
        self.log(f"[SYSTEM] Scene '{scene_name}' loading initialized.")

    def save_scene(self):
        scene_name = self.scene_var.get()
        
        scene_payload = {
            "state": copy.deepcopy(self.app_state),
            "layouts": copy.deepcopy(self.screen_layouts)
        }
        self.scenes_data[scene_name] = scene_payload
        
        try:
            with open(SCENES_FILE, "w", encoding="utf-8") as f:
                json.dump(self.scenes_data, f, ensure_ascii=False, indent=4)
            self.log(f"[SYSTEM] Scene '{scene_name}' saved successfully.")
        except Exception as e:
            self.log(f"[ERROR] Failed to save scene: {e}")

    def rename_scene(self):
        old_name = self.scene_var.get()
        
        dialog = ctk.CTkToplevel(self)
        dialog.geometry("380x200")
        dialog.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        dialog.overrideredirect(True)
        dialog.attributes("-topmost", True)
        
        x = self.winfo_x() + (self.winfo_width() // 2) - 190
        y = self.winfo_y() + (self.winfo_height() // 2) - 100
        dialog.geometry(f"+{x}+{y}")
        
        hdr = ctk.CTkFrame(dialog, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="RENAME / SET DEFAULT", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        
        result = {}
        
        def on_save():
            result["new_name"] = name_entry.get()
            result["is_default"] = default_var.get()
            dialog.destroy()
            
        def on_cancel():
            dialog.destroy()
            
        ctk.CTkButton(hdr, text="X", width=40, height=30, command=on_cancel, fg_color=COLORS["QUIT_BTN"], corner_radius=0).pack(side="right")
        
        def start_move(e): dialog.x, dialog.y = e.x, e.y
        def do_move(e): dialog.geometry(f"+{dialog.winfo_x() + e.x - dialog.x}+{dialog.winfo_y() + e.y - dialog.y}")
        hdr.bind("<ButtonPress-1>", start_move)
        hdr.bind("<B1-Motion>", do_move)
        
        cont = ctk.CTkFrame(dialog, fg_color="transparent")
        cont.pack(fill="both", expand=True, padx=20, pady=15)
        
        ctk.CTkLabel(cont, text="Scene Name:", font=("Consolas", 11, "bold"), text_color="white").pack(anchor="w")
        name_entry = ctk.CTkEntry(cont, width=340, height=30, corner_radius=0, text_color="white", fg_color="#202020", border_width=0)
        name_entry.insert(0, old_name)
        name_entry.pack(fill="x", pady=(0, 15))
        
        config = shared.load_config()
        current_default = (config.get("default_scene") == old_name)
        default_var = ctk.BooleanVar(value=current_default)
        
        ctk.CTkCheckBox(cont, text="Load this scene automatically on startup", variable=default_var, fg_color=COLORS["ACCENT"], text_color="white", font=("Consolas", 11)).pack(anchor="w")
        
        ctk.CTkButton(dialog, text="SAVE", height=35, command=on_save, fg_color=COLORS["ACCENT"], text_color="black", font=("Consolas", 11, "bold"), corner_radius=0).pack(fill="x", padx=20, pady=(0, 20))
        
        self.wait_window(dialog)
        
        if "new_name" in result:
            new_name = result["new_name"].strip()
            is_default = result["is_default"]
            
            if new_name and new_name != old_name:
                if old_name in self.scenes_data:
                    self.scenes_data[new_name] = self.scenes_data.pop(old_name)
                else:
                    self.scenes_data[new_name] = {
                        "state": copy.deepcopy(self.app_state),
                        "layouts": copy.deepcopy(self.screen_layouts)
                    }
                try:
                    with open(SCENES_FILE, "w", encoding="utf-8") as f:
                        json.dump(self.scenes_data, f, ensure_ascii=False, indent=4)
                except: pass
                
                self.scene_names = list(self.scenes_data.keys())
                self.scene_cb.configure(values=self.scene_names)
                self.scene_var.set(new_name)
                self.log(f"[SYSTEM] Scene renamed to '{new_name}'.")
            else:
                new_name = old_name 
                
            if is_default:
                shared.update_config({"default_scene": new_name})
                self.log(f"[SYSTEM] '{new_name}' set as Default Auto-Load Scene.")
            elif config.get("default_scene") == old_name and not is_default:
                shared.update_config({"default_scene": ""})
                self.log(f"[SYSTEM] Auto-Load removed for '{old_name}'.")

    def delete_scene(self):
        scene_name = self.scene_var.get()
        if scene_name in self.scenes_data:
            del self.scenes_data[scene_name]
            try:
                with open(SCENES_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.scenes_data, f, ensure_ascii=False, indent=4)
            except: pass
            
            self.scene_names = list(self.scenes_data.keys())
            if not self.scene_names:
                self.scene_names = ["Scene 1"]
            
            self.scene_cb.configure(values=self.scene_names)
            self.scene_var.set(self.scene_names[0])
            
            if shared.load_config().get("default_scene") == scene_name:
                shared.update_config({"default_scene": ""})
                
            self.log(f"[SYSTEM] Scene '{scene_name}' deleted.")

    def open_snapshots_folder(self):
        folder = os.path.abspath(shared.SNAPSHOTS_DIR)
        if not os.path.exists(folder):
            os.makedirs(folder)
        try:
            os.startfile(folder)
            self.log(f"[SYSTEM] Opened snapshots folder: {folder}")
        except Exception as e:
            self.log(f"[ERROR] Could not open folder: {e}")

    def take_snapshot(self, idx):
        if not self.current_stream_target:
            self.log(f"[WARNING] Select a screen first to take a snapshot.")
            return
            
        self.log(f"[ACTION] Requesting snapshot from {self.current_stream_target} - Stream {idx+1}...")
        if sio.connected:
            sio.emit("request_snapshot", {"target": self.current_stream_target, "quad": idx})

    def toggle_blackout(self):
        self.is_blackout = not getattr(self, 'is_blackout', False)
        if self.is_blackout:
            self.btn_blackout.configure(text="RESTORE\nSCREENS", fg_color=COLORS["QUIT_BTN"])
            self.log("[SYSTEM] Global Blackout ENABLED.")
        else:
            self.btn_blackout.configure(text="BLACKOUT\nALL SCREENS", fg_color=COLORS["SELECTED"])
            self.log("[SYSTEM] Global Blackout DISABLED.")
            
        for lbl, data in self.nodes.items():
            if data["type"] != "GPU":
                if lbl not in self.app_state:
                    self.app_state[lbl] = {}
                self.app_state[lbl]['blackout'] = str(self.is_blackout)
                if sio.connected:
                    sio.emit("update_screen_state", {"target": lbl, "payload": {"blackout": str(self.is_blackout)}})

    def _disable_stream_controls(self):
        self.current_stream_target = None 
        self.stream_title.configure(text="Select a screen to edit")
        for i in range(4):
            self.stream_cells[i].grid()
            self.stream_vars[i].set("Disabled")
            self.stream_menus[i].configure(state="disabled", fg_color="#222222")
            self.fs_btns[i].configure(state="disabled", fg_color="#222222")
            self.snap_btns[i].configure(state="disabled", fg_color="#222222")

    def sync_stream_ui_from_state(self, lbl):
        if not lbl: return
        self.current_stream_target = None 
        self.stream_title.configure(text=f"Target: {lbl}")
        
        layout = self.screen_layouts.get(lbl, 4)
        screen_state = self.app_state.get(lbl, {})
        
        for i in range(4):
            if (layout == 1 and i >= 1) or (layout == 2 and i >= 2):
                self.stream_cells[i].grid_remove()
            else:
                self.stream_cells[i].grid()
                self.stream_menus[i].configure(state="normal", fg_color=COLORS["BTN_BASE"])
                self.fs_btns[i].configure(state="normal", fg_color=COLORS["BTN_BASE"])
                self.snap_btns[i].configure(state="normal", fg_color=COLORS["BG_MAIN"])
                
                url = screen_state.get(str(i), "")
                name = "None"
                for t_name, t_url in self.targets_dict.items():
                    if t_url == url and url != "":
                        name = t_name
                        break
                self.stream_vars[i].set(name)

        self.current_fs_index = int(screen_state.get("fullscreen", "-1"))
        for i, btn in enumerate(self.fs_btns):
            if (layout == 1 and i >= 1) or (layout == 2 and i >= 2): continue
            btn.configure(fg_color=COLORS["ACCENT"] if i == self.current_fs_index else COLORS["BTN_BASE"], text_color="black" if i == self.current_fs_index else "white")

        self.current_stream_target = lbl

    def emit_stream_state(self):
        if not self.current_stream_target: return
        update_dict = {}
        for i in range(4):
            target_name = self.stream_vars[i].get()
            url = self.targets_dict.get(target_name, "")
            update_dict[str(i)] = url
            update_dict[f"{i}_name"] = target_name if target_name != "None" else ""
            
        update_dict["fullscreen"] = str(self.current_fs_index)
        
        if sio.connected:
            sio.emit("update_screen_state", {"target": self.current_stream_target, "payload": update_dict})

    def on_stream_change(self, idx, value):
        self.emit_stream_state()

    def on_fs_toggle(self, idx):
        if self.current_fs_index == idx:
            self.current_fs_index = -1
        else:
            self.current_fs_index = idx
        self.emit_stream_state()

    def _kill_viewer_local(self, lbl):
        self.log(f"[SYSTEM] Sending kill command to Viewer '{lbl}'...")
        if sio.connected:
            sio.emit("kill_viewer", {"target": lbl})
            
        p = self.active_viewers.get(lbl)
        if p and p != "NETWORKED":
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
                else:
                    p.terminate()
            except Exception as e:
                self.log(f"[ERROR] Failed to kill process tree: {e}")
            
        if lbl in self.active_viewers:
            del self.active_viewers[lbl]

    def _launch_viewer_process(self, lbl, layout):
        if self.is_controller_on_screen(lbl): 
            self.log(f"[WARNING] Skipping launch on '{lbl}' to avoid overlapping controller.")
            return
            
        self.log(f"[SYSTEM] Launching Viewer for '{lbl}' in {layout}-Screen mode...")
        try:
            self.screen_layouts[lbl] = layout
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
                viewer_path = os.path.join(base_dir, "viewer.exe")
                cmd = [viewer_path, lbl, str(layout)]
            else:
                cmd = [sys.executable, "viewer.py", lbl, str(layout)]
                
            p = subprocess.Popen(cmd, **kwargs)
            self.active_viewers[lbl] = p
            self.draw_graph()
            
            if self.current_stream_target == lbl:
                self.sync_stream_ui_from_state(lbl)
                
            self.after(2000, lambda: self._resync_single_screen(lbl))
            self.after(4000, lambda: self._resync_single_screen(lbl))
            
        except Exception as e:
            self.log(f"[ERROR] Failed: {e}")

    def toggle_viewer_checkbox(self, lbl, layout=4):
        if self.is_controller_on_screen(lbl):
            self.log(f"[WARNING] Cannot launch on '{lbl}' (Controller is here).")
            return
            
        is_running = lbl in self.active_viewers
        current_layout = self.screen_layouts.get(lbl, 4)
        
        if is_running:
            if current_layout == layout:
                self.log(f"[SYSTEM] Closing '{lbl}'...")
                self._kill_viewer_local(lbl)
            else:
                self.log(f"[SYSTEM] Switching '{lbl}' layout from {current_layout} to {layout}...")
                self._kill_viewer_local(lbl)
                
                self.screen_layouts[lbl] = layout
                self.draw_graph()
                if self.current_stream_target == lbl:
                    self.sync_stream_ui_from_state(lbl)
                
                self.after(1500, lambda: self._launch_viewer_process(lbl, layout))
        else:
            self.screen_layouts[lbl] = layout
            self._launch_viewer_process(lbl, layout)

    def _load_data(self):
        if not os.path.exists(FILE): return
        self.nodes.clear()
        self.connections.clear()
        
        with open(FILE, "r") as f:
            for line in f:
                l = line.strip()
                if not l: continue
                if "CONFIG: GRID" in l: 
                    p = l.split(",")
                    if len(p) >= 3:
                        self.rows, self.cols = int(p[1]), int(p[2])
                elif "CONFIG: LINK" in l:
                    p = [x.strip() for x in l.split(",")]
                    if len(p) >= 3: 
                        self.connections.add(tuple(sorted([p[1], p[2]])))
                elif not l.startswith("CONFIG"):
                    p = [x.strip() for x in l.split(",")]
                    if len(p) >= 10:
                        lbl = p[0]
                        self.nodes[lbl] = {
                            "label": p[0], "type": p[1], "info1": p[2], "info2": p[3],
                            "res": p[4], "offset": p[5], "primary": p[6],
                            "r": int(p[7]), "c": int(p[8]), "s": int(p[9])
                        }

    def on_node_click(self, event, lbl, coords):
        if self.nodes[lbl]["type"] != "GPU":
            cb_size = 13
            cb4_x1, cb4_y1 = coords['x2'] - 18, coords['y1'] + 5
            cb4_x2, cb4_y2 = cb4_x1 + cb_size, cb4_y1 + cb_size
            
            cb2_x1, cb2_y1 = coords['x2'] - 18, coords['y1'] + 20
            cb2_x2, cb2_y2 = cb2_x1 + cb_size, cb2_y1 + cb_size

            cb1_x1, cb1_y1 = coords['x2'] - 18, coords['y1'] + 35
            cb1_x2, cb1_y2 = cb1_x1 + cb_size, cb1_y1 + cb_size

            if cb4_x1 <= event.x <= cb4_x2 and cb4_y1 <= event.y <= cb4_y2:
                if self.is_controller_on_screen(lbl):
                    self.log(f"[WARNING] Cannot launch on '{lbl}'.")
                else:
                    self.toggle_viewer_checkbox(lbl, layout=4)
                    self.selected_targets = {lbl}
                    self.sync_stream_ui_from_state(lbl)
                return
                
            if cb2_x1 <= event.x <= cb2_x2 and cb2_y1 <= event.y <= cb2_y2:
                if self.is_controller_on_screen(lbl):
                    self.log(f"[WARNING] Cannot launch on '{lbl}'.")
                else:
                    self.toggle_viewer_checkbox(lbl, layout=2)
                    self.selected_targets = {lbl}
                    self.sync_stream_ui_from_state(lbl)
                return

            if cb1_x1 <= event.x <= cb1_x2 and cb1_y1 <= event.y <= cb1_y2:
                if self.is_controller_on_screen(lbl):
                    self.log(f"[WARNING] Cannot launch on '{lbl}'.")
                else:
                    self.toggle_viewer_checkbox(lbl, layout=1)
                    self.selected_targets = {lbl}
                    self.sync_stream_ui_from_state(lbl)
                return
                
        self.toggle_selection(lbl)

    def draw_graph(self):
        self.canvas.delete("all")
        for r in range(self.rows):
            for c in range(self.cols):
                self.canvas.create_rectangle(c*CELL_W, r*CELL_H, (c+1)*CELL_W, (r+1)*CELL_H, outline=COLORS["GRID_LINE"], dash=(2,2))
        
        node_coords = {}
        for lbl, n in self.nodes.items():
            r, c, s = n["r"], n["c"], n["s"]
            x1, y1 = c * CELL_W + 10, r * CELL_H + 10
            x2, y2 = c * CELL_W + (CELL_W * s) - 10, r * CELL_H + CELL_H - 10
            node_coords[lbl] = {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'cx': (x1 + x2) / 2, 'cy': (y1 + y2) / 2, 'n': n}

        gpu_ports_used = {}

        for n1, n2 in self.connections:
            if n1 in node_coords and n2 in node_coords:
                d1, d2 = node_coords[n1], node_coords[n2]
                is_d1_gpu = d1['n']['type'] == "GPU"
                is_d2_gpu = d2['n']['type'] == "GPU"
                
                if is_d1_gpu and not is_d2_gpu: src, tgt = d1, d2
                elif is_d2_gpu and not is_d1_gpu: src, tgt = d2, d1
                else: src, tgt = (d1, d2) if d1['cx'] <= d2['cx'] else (d2, d1)

                sx_l = src['x2']
                ex_l = tgt['x1']
                ey_l = tgt['cy']

                if src['n']['type'] == "GPU":
                    src_lbl = src['n']['label']
                    if src_lbl not in gpu_ports_used:
                        total_conns = sum(1 for c1, c2 in self.connections if c1 == src_lbl or c2 == src_lbl)
                        gpu_ports_used[src_lbl] = {'total': max(1, total_conns), 'drawn': 0}
                    
                    drawn = gpu_ports_used[src_lbl]['drawn']
                    total = gpu_ports_used[src_lbl]['total']
                    
                    spacing = (src['y2'] - src['y1']) / (total + 1)
                    sy_l = src['y1'] + spacing * (drawn + 1)
                    gpu_ports_used[src_lbl]['drawn'] += 1
                else:
                    sy_l = src['cy']

                offset = max(abs(ex_l - sx_l) * 0.6, 60)
                self.canvas.create_line(sx_l, sy_l, sx_l + offset, sy_l, ex_l - offset, ey_l, ex_l, ey_l, fill="#3498db", width=2, smooth=True, splinesteps=36, arrow=tk.LAST, arrowshape=(10, 12, 4))
        
        for lbl, d in node_coords.items():
            safe_tag = f"btn_{lbl.replace(' ', '_').replace('-', '_')}"
            n = d['n']
            dev_type = n['type']
            is_selected = lbl in self.selected_targets
            outline_color = COLORS["ACCENT"] if is_selected else "#444444"
            outline_width = 3 if is_selected else 1
            
            if dev_type == "GPU":
                bg_color = COLORS["GPU_BG"]
                display_text = f"[GPU]\n{n['label']}\n{n['info1']}"
            else:
                bg_color = COLORS["SELECTED"] if is_selected else COLORS["CELL_BG"]
                display_text = f"{n['label']}\n[{n['info1']}]\n{n['res']}"

            self.canvas.create_rectangle(d['x1'], d['y1'], d['x2'], d['y2'], fill=bg_color, outline=outline_color, width=outline_width, tags=safe_tag)
            port_r = 3
            
            if dev_type == "GPU": 
                total_conns = sum(1 for c1, c2 in self.connections if c1 == lbl or c2 == lbl)
                total_ports = max(1, total_conns)
                spacing = (d['y2'] - d['y1']) / (total_ports + 1)
                for p in range(1, total_ports + 1):
                    py = d['y1'] + spacing * p
                    self.canvas.create_oval(d['x2']-port_r, py-port_r, d['x2']+port_r, py+port_r, fill=COLORS["ACCENT"], outline="", tags=safe_tag)
            else: 
                self.canvas.create_oval(d['x1']-port_r, d['cy']-port_r, d['x1']+port_r, d['cy']+port_r, fill=COLORS["ACCENT"], outline="", tags=safe_tag)
                
                is_host = self.is_controller_on_screen(lbl)
                is_running = lbl in self.active_viewers
                current_layout = self.screen_layouts.get(lbl, 4)
                
                cb_size = 13
                cb4_x1, cb4_y1 = d['x2'] - 18, d['y1'] + 5
                cb4_x2, cb4_y2 = cb4_x1 + cb_size, cb4_y1 + cb_size
                
                cb2_x1, cb2_y1 = d['x2'] - 18, d['y1'] + 20
                cb2_x2, cb2_y2 = cb2_x1 + cb_size, cb2_y1 + cb_size

                cb1_x1, cb1_y1 = d['x2'] - 18, d['y1'] + 35
                cb1_x2, cb1_y2 = cb1_x1 + cb_size, cb1_y1 + cb_size

                if is_host:
                    self.canvas.create_rectangle(cb4_x1, cb4_y1, cb1_x2, cb1_y2, fill="#333333", outline="#666666", tags=safe_tag)
                    self.canvas.create_line(cb4_x1+2, cb4_y1+2, cb1_x2-2, cb1_y2-2, fill="#888888", width=2, tags=safe_tag)
                    self.canvas.create_line(cb1_x2-2, cb4_y1+2, cb4_x1+2, cb1_y2-2, fill="#888888", width=2, tags=safe_tag)
                else:
                    fill4 = COLORS["ACCENT"] if (is_running and current_layout == 4) else COLORS["BG_MAIN"]
                    text4 = "black" if (is_running and current_layout == 4) else "white"
                    self.canvas.create_rectangle(cb4_x1, cb4_y1, cb4_x2, cb4_y2, fill=fill4, outline="white", tags=safe_tag)
                    self.canvas.create_text(cb4_x1 + cb_size/2, cb4_y1 + cb_size/2, text="4", fill=text4, font=("Consolas", 8, "bold"), tags=safe_tag)

                    fill2 = COLORS["ACCENT"] if (is_running and current_layout == 2) else COLORS["BG_MAIN"]
                    text2 = "black" if (is_running and current_layout == 2) else "white"
                    self.canvas.create_rectangle(cb2_x1, cb2_y1, cb2_x2, cb2_y2, fill=fill2, outline="white", tags=safe_tag)
                    self.canvas.create_text(cb2_x1 + cb_size/2, cb2_y1 + cb_size/2, text="2", fill=text2, font=("Consolas", 8, "bold"), tags=safe_tag)

                    fill1 = COLORS["ACCENT"] if (is_running and current_layout == 1) else COLORS["BG_MAIN"]
                    text1 = "black" if (is_running and current_layout == 1) else "white"
                    self.canvas.create_rectangle(cb1_x1, cb1_y1, cb1_x2, cb1_y2, fill=fill1, outline="white", tags=safe_tag)
                    self.canvas.create_text(cb1_x1 + cb_size/2, cb1_y1 + cb_size/2, text="1", fill=text1, font=("Consolas", 8, "bold"), tags=safe_tag)

            self.canvas.create_text(d['cx'], d['cy'], text=display_text, fill="white", font=("Consolas", 10, "bold"), justify="center", tags=safe_tag)
            self.canvas.tag_bind(safe_tag, "<Button-1>", lambda e, l=lbl, c=d: self.on_node_click(e, l, c))

    def toggle_selection(self, lbl):
        if self.nodes[lbl]["type"] == "GPU": return
        
        if lbl in self.selected_targets:
            self.selected_targets.clear()
            self._disable_stream_controls()
        else:
            self.selected_targets = {lbl}
            self.sync_stream_ui_from_state(lbl)
            
        self.draw_graph()

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")

    def show_help(self):
        pop = ctk.CTkToplevel(self)
        pop.geometry("480x250")
        pop.configure(fg_color=COLORS["BG_MAIN"], highlightbackground=COLORS["ACCENT"], highlightthickness=1)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        x = self.winfo_x() + (self.winfo_width() // 2) - 240
        y = self.winfo_y() + (self.winfo_height() // 2) - 125
        pop.geometry(f"+{x}+{y}")
        hdr = ctk.CTkFrame(pop, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="HELP - CONTROLLER", font=("Consolas", 12, "bold"), text_color=COLORS["ACCENT"]).pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="Quit", width=40, height=30, command=pop.destroy, fg_color=COLORS["QUIT_BTN"], corner_radius=0).pack(side="right")
        help_text = "- Select a screen to edit its streams.\n- Click Checkbox to Launch/Kill viewers locally.\n- Networked state syncs instantly across devices."
        ctk.CTkLabel(pop, text=help_text, font=("Consolas", 11), text_color="white", justify="left").pack(pady=20, padx=20, anchor="w")

    def toggle_visibility(self, event=None):
        if self.winfo_viewable(): self.withdraw()
        else: self.deiconify(); self.attributes("-topmost", True); self.after(100, lambda: self.attributes("-topmost", False))

    def on_close(self):
        self.destroy()
        os._exit(0)

if __name__ == "__main__":
    app = DisplayController()
    app.mainloop()