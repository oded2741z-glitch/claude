import sys
import os
import json
import re
import time
import queue

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
except ImportError:
    GPU_AVAILABLE = False

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QPushButton, QLabel,
                             QLineEdit, QComboBox, QFrame, QFileDialog, QSizePolicy, QScrollArea,
                             QStackedLayout, QCheckBox)
from PyQt5.QtCore import Qt, QUrl, QTimer, QPoint, pyqtSignal, QThread
from PyQt5.QtGui import QKeySequence, QFont, QColor, QPainter, QPen, QImage
from PyQt5.QtWebEngineWidgets import QWebEngineView

# --- PATHS & CONFIG ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DC_DATA_FILE = os.path.join(BASE_DIR, "display_commander_db.txt")
AUTO_IMPORT_FILE = os.path.join(BASE_DIR, "Auto-Import.txt")

# --- STYLE CONSTANTS ---
C_BG = "#121212"
C_BG2 = "#1E1E1E"
C_ACCENT = "#389379"
C_BORDER = "#333333"
C_QUIT = "#aa2222"
C_QUIT_HOVER = "#ff0000"

DC_LAYOUTS = ["1x1", "2x1", "2x2", "3x1"]
DC_LAYOUT_COUNTS = {"1x1": 1, "2x1": 2, "2x2": 4, "3x1": 3}
DC_RATIOS = ["Free", "16:9", "4:3"]

DRONE_MODEL_PATH = os.path.join(BASE_DIR, "drone.pt")

# Max dimension (px) of the frame fed to YOLO; larger frames are downscaled
DETECT_MAX_DIM = 640

# --- MUZZLE FLASH DETECTION ---
FLASH_PERSIST_SECS  = 30    
FLASH_DIFF_THRESH   = 75    
FLASH_BRIGHT_THRESH = 200   
FLASH_MIN_AREA      = 80    
FLASH_MAX_AREA      = 40000 

def detect_muzzle_flash(prev_gray, curr_gray):
    diff = cv2.absdiff(curr_gray, prev_gray)
    _, diff_mask   = cv2.threshold(diff,      FLASH_DIFF_THRESH,   255, cv2.THRESH_BINARY)
    _, bright_mask = cv2.threshold(curr_gray, FLASH_BRIGHT_THRESH, 255, cv2.THRESH_BINARY)
    combined = cv2.bitwise_and(diff_mask, bright_mask)
    kernel   = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    combined = cv2.dilate(combined, kernel, iterations=2)
    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if FLASH_MIN_AREA <= area <= FLASH_MAX_AREA:
            x, y, w, h = cv2.boundingRect(cnt)
            rects.append((x, y, w, h))
    return rects

# --- GLOBAL STYLESHEET ---
STYLESHEET = f"""
QWidget {{
    background-color: {C_BG};
    color: #FFFFFF;
    font-family: Consolas;
    font-size: 10pt;
}}
QPushButton {{
    background-color: {C_BORDER};
    border: none;
    border-radius: 0px;
    padding: 5px 10px;
    color: white;
    font-weight: bold;
}}
QPushButton:hover {{ background-color: {C_ACCENT}; color: {C_BG}; }}
QPushButton#AccentButton {{ background-color: {C_ACCENT}; color: {C_BG}; }}
QPushButton#AccentButton:hover {{ background-color: #2b7560; }}
QPushButton#QuitButton {{ background-color: {C_QUIT}; }}
QPushButton#QuitButton:hover {{ background-color: {C_QUIT_HOVER}; }}
QPushButton#RatioBtn {{ background-color: {C_BG}; border: 1px solid {C_BORDER}; padding: 1px 5px; font-size: 8pt; }}
QPushButton#RatioBtnActive {{ background-color: {C_BG}; border: 1px solid {C_ACCENT}; color: {C_ACCENT}; padding: 1px 5px; font-size: 8pt; }}
QLineEdit {{
    background-color: {C_BG2};
    border: 1px solid {C_BORDER};
    padding: 2px 4px;
    color: #FFFFFF;
}}
QLineEdit:focus {{ border: 1px solid {C_ACCENT}; }}
QComboBox {{
    background-color: {C_BG2};
    border: 1px solid {C_BORDER};
    padding: 2px 4px;
    color: #FFFFFF;
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{ background-color: {C_BG2}; selection-background-color: {C_ACCENT}; selection-color: {C_BG}; }}
QFrame#Container {{ border: 1px solid {C_ACCENT}; }}
QFrame#Separator {{ background-color: {C_ACCENT}; max-width: 1px; min-width: 1px; }}
QFrame#Card {{ background-color: {C_BG2}; border: 1px solid {C_BORDER}; }}
QFrame#StreamContainer {{ background-color: {C_BORDER}; border: none; padding: 1px; }}
QScrollArea {{ border: none; background-color: transparent; }}
QScrollBar:vertical {{ background: {C_BG}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {C_BORDER}; }}
QScrollBar::handle:vertical:hover {{ background: {C_ACCENT}; }}
QCheckBox {{ color: #FFFFFF; spacing: 4px; font-size: 9pt; }}
QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {C_BORDER}; background: {C_BG2}; }}
QCheckBox::indicator:checked {{ background: {C_ACCENT}; border: 1px solid {C_ACCENT}; }}
"""

# ==========================================
# COMMANDER STATE MANAGEMENT
# ==========================================
def dc_default_state():
    return {
        "urls": [], "screens": ["", "", "", ""], "ratios": ["Free", "Free", "Free", "Free"],
        "layout": "2x2", "window_w": 1280, "window_h": 800
    }

def dc_load_state():
    state = dc_default_state()
    if not os.path.exists(DC_DATA_FILE): return state
    try:
        with open(DC_DATA_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.startswith("{"): return json.loads(content)
            state["urls"] = []
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("LAYOUT:"): state["layout"] = line.split(":", 1)[1].strip()
                elif line.startswith("WINDOW_SIZE:"):
                    parts = line.split(":", 1)[1].split("|")
                    if len(parts) == 2:
                        state["window_w"], state["window_h"] = int(parts[0].strip()), int(parts[1].strip())
                elif line.startswith("SCREEN_"):
                    parts = line.split(":", 1)
                    idx, val = int(parts[0].split("_")[1]), parts[1].strip()
                    if "|" in val:
                        url, ratio = val.split("|", 1)
                        while len(state["screens"]) <= idx: state["screens"].append("")
                        while len(state["ratios"]) <= idx: state["ratios"].append("Free")
                        state["screens"][idx], state["ratios"][idx] = url.strip(), ratio.strip()
                elif line.startswith("URL:"):
                    val = line.split(":", 1)[1].strip()
                    if "|" in val:
                        lbl, url = val.split("|", 1)
                        state["urls"].append({"label": lbl.strip(), "url": url.strip()})
    except Exception: pass
    return state

def dc_save_state(state):
    try:
        with open(DC_DATA_FILE, "w", encoding="utf-8") as f:
            f.write(f"LAYOUT: {state.get('layout', '2x2')}\n")
            f.write(f"WINDOW_SIZE: {state.get('window_w', 1280)} | {state.get('window_h', 800)}\n")
            for i in range(len(state.get('screens', []))):
                url = state['screens'][i]
                ratio = state['ratios'][i] if i < len(state.get('ratios', [])) else "Free"
                f.write(f"SCREEN_{i}: {url} | {ratio}\n")
            for u in state.get("urls", []): f.write(f"URL: {u['label']} | {u['url']}\n")
    except Exception: pass

def dc_normalize_url(raw):
    raw = raw.strip()
    if not raw: return ""
    if not re.match(r"^https?://", raw, re.I): return "http://" + raw
    return raw

def clear_layout(layout):
    if layout is not None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None: widget.deleteLater()
            else: clear_layout(item.layout())


# ==========================================
# WORKER THREAD FOR DETECTION
# ==========================================
import threading

class DetectionWorker(QThread):
    results_ready = pyqtSignal(int, list)

    def __init__(self):
        super().__init__()
        self.running = True
        # Only keep the latest frame per stream so detection never lags
        # behind the live video when YOLO is slower than the capture tick.
        self._latest_frames = {}
        self._frames_lock = threading.Lock()
        self._frames_event = threading.Event()
        self.person_model = None
        self.drone_model = None
        self._prev_frames = {}
        self._flash_persist = {}
        self.enable_person = True
        self.enable_drone = True
        self.enable_flash = True
        self.device = "cpu"

    def submit_frame(self, idx, frame_bgr, gray, drawn_zones=None):
        with self._frames_lock:
            self._latest_frames[idx] = (frame_bgr, gray, drawn_zones or [])
        self._frames_event.set()

    @staticmethod
    def _in_drawn_zone(x, y, w, h, zones):
        for (zx, zy, zw, zh) in zones:
            if x < zx + zw and x + w > zx and y < zy + zh and y + h > zy:
                return True
        return False

    def run(self):
        if YOLO_AVAILABLE:
            self.person_model = YOLO("yolov8n.pt")
            if os.path.exists(DRONE_MODEL_PATH):
                self.drone_model = YOLO(DRONE_MODEL_PATH)

        while self.running:
            if not self._frames_event.wait(timeout=0.1):
                continue
            with self._frames_lock:
                pending = self._latest_frames
                self._latest_frames = {}
                self._frames_event.clear()

            for idx, (frame_bgr, gray, drawn_zones) in pending.items():
                if not self.running:
                    break
                rects = []
                now = time.time()

                # Run YOLO on a downscaled copy (max 640px) and scale the
                # boxes back up — much faster on CPU, same overlay coords.
                h0, w0 = frame_bgr.shape[:2]
                scale = min(1.0, DETECT_MAX_DIM / max(h0, w0))
                if scale < 1.0:
                    small = cv2.resize(frame_bgr, (max(1, int(w0 * scale)), max(1, int(h0 * scale))))
                else:
                    small = frame_bgr
                inv = 1.0 / scale

                if self.person_model and self.enable_person:
                    results = self.person_model(small, classes=[0], verbose=False, device=self.device)
                    for box in results[0].boxes:
                        conf = float(box.conf[0])
                        if conf >= 0.4:
                            x1, y1, x2, y2 = box.xyxy[0].tolist()
                            x1, y1, x2, y2 = int(x1 * inv), int(y1 * inv), int(x2 * inv), int(y2 * inv)
                            rects.append((x1, y1, x2 - x1, y2 - y1, conf, "person"))

                if self.drone_model and self.enable_drone:
                    results = self.drone_model(small, verbose=False, device=self.device)
                    for box in results[0].boxes:
                        conf = float(box.conf[0])
                        if conf >= 0.4:
                            x1, y1, x2, y2 = box.xyxy[0].tolist()
                            x1, y1, x2, y2 = int(x1 * inv), int(y1 * inv), int(x2 * inv), int(y2 * inv)
                            rects.append((x1, y1, x2 - x1, y2 - y1, conf, "drone"))

                if self.enable_flash:
                    prev_gray = self._prev_frames.get(idx)
                    if prev_gray is not None and prev_gray.shape == gray.shape:
                        for (x, y, fw, fh) in detect_muzzle_flash(prev_gray, gray):
                            # The screen grab includes our own overlay, so a
                            # box/label appearing or moving looks like a
                            # bright change — ignore candidates that overlap
                            # zones we painted ourselves.
                            if self._in_drawn_zone(x, y, fw, fh, drawn_zones):
                                continue
                            self._flash_persist.setdefault(idx, []).append((x, y, fw, fh, now))
                    self._prev_frames[idx] = gray

                    active = [(x, y, fw, fh, t) for (x, y, fw, fh, t)
                              in self._flash_persist.get(idx, [])
                              if now - t <= FLASH_PERSIST_SECS]
                    self._flash_persist[idx] = active

                    for (x, y, fw, fh, t) in active:
                        remaining = max(1, FLASH_PERSIST_SECS - int(now - t))
                        rects.append((x, y, fw, fh, 1.0, "flash", remaining))
                else:
                    self._prev_frames[idx] = gray

                self.results_ready.emit(idx, rects)

    def stop(self):
        self.running = False
        self._frames_event.set()
        self.wait()


# ==========================================
# STREAM PLAYER 
# ==========================================
class StreamPlayer(QWebEngineView):
    def __init__(self, url, ratio="Free", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        valid_url = url if url.startswith("http") else "http://" + url
        
        ratio_css = "width: 100%; height: 100%;"
        if ratio == "16:9": ratio_css = "width: 100vw; height: 56.25vw; max-height: 100vh; max-width: 177.77vh;"
        elif ratio == "4:3": ratio_css = "width: 100vw; height: 75vw; max-height: 100vh; max-width: 133.33vh;"
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <style>
            html, body {{ width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; background-color: #000000; pointer-events: none; user-select: none; display: flex; justify-content: center; align-items: center; }}
            iframe {{ border: none; background-color: #000000; pointer-events: none; {ratio_css} }}
        </style>
        </head>
        <body><iframe src="{valid_url}" scrolling="no" allowfullscreen></iframe></body>
        </html>
        """
        self.setHtml(html, QUrl(valid_url))

# ==========================================
# DETECTION OVERLAY
# ==========================================
class DetectionOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Top-level transparent window: QWebEngineView uses a native GPU
        # surface that draws above sibling Qt widgets, so an in-layout
        # overlay stays hidden. A frameless top-level window with
        # transparent input sits above the web view reliably.
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")
        # Animated display state: boxes ease toward the latest detection
        # results instead of jumping, so they follow moving targets smoothly.
        self._display = []
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._animate)

    def set_rects(self, rects):
        targets = []
        for item in rects:
            targets.append({
                "x": float(item[0]), "y": float(item[1]),
                "w": float(item[2]), "h": float(item[3]),
                "conf": item[4] if len(item) > 4 else None,
                "kind": item[5] if len(item) > 5 else "person",
                "remaining": item[6] if len(item) > 6 else None,
            })
        # Match each new detection to the nearest currently displayed box of
        # the same kind so it animates there instead of being replaced.
        used = set()
        new_display = []
        for t in targets:
            tcx, tcy = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
            best, best_dist = None, None
            for i, d in enumerate(self._display):
                if i in used or d["kind"] != t["kind"]:
                    continue
                dcx, dcy = d["x"] + d["w"] / 2, d["y"] + d["h"] / 2
                dist = abs(dcx - tcx) + abs(dcy - tcy)
                if dist <= max(t["w"], t["h"], 80) and (best_dist is None or dist < best_dist):
                    best, best_dist = i, dist
            if best is not None:
                used.add(best)
                d = dict(self._display[best])
                d["conf"], d["remaining"] = t["conf"], t["remaining"]
            else:
                d = dict(t)
            d["tx"], d["ty"], d["tw"], d["th"] = t["x"], t["y"], t["w"], t["h"]
            new_display.append(d)
        self._display = new_display
        if self._display and not self._anim.isActive():
            self._anim.start()
        self.update()

    def _animate(self):
        moving = False
        for d in self._display:
            for cur, tgt in (("x", "tx"), ("y", "ty"), ("w", "tw"), ("h", "th")):
                delta = d[tgt] - d[cur]
                if abs(delta) > 0.5:
                    d[cur] += delta * 0.35
                    moving = True
                else:
                    d[cur] = d[tgt]
        if not moving:
            self._anim.stop()
        self.update()

    def drawn_zones(self):
        # Areas (box border + label) currently painted on screen, in widget
        # coordinates. The capture grabs the screen *including* this overlay,
        # so the flash detector must ignore changes inside these zones.
        zones = []
        for d in self._display:
            x, y, w, h = int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"])
            zones.append((x - 4, y - 20, w + 120, h + 26))
        return zones

    def paintEvent(self, event):
        if not self._display:
            return
        p = QPainter(self)
        f = p.font()
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        for d in self._display:
            x, y, w, h = int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"])
            conf      = d["conf"]
            kind      = d["kind"]
            remaining = d["remaining"]
            if kind == "drone":
                color, bg_color = QColor(255, 60, 60), QColor(200, 40, 40, 200)
            elif kind == "flash":
                color, bg_color = QColor(255, 220, 0), QColor(180, 150, 0, 200)
            else:
                color, bg_color = QColor(0, 255, 0), QColor(0, 180, 0, 200)
            
            if kind == "flash" and remaining is not None:
                label = f"Flash {remaining}s"
            elif conf is not None:
                label = f"{kind.capitalize()} {int(conf * 100)}%"
            else:
                label = kind.capitalize()
                
            label_w = len(label) * 7
            p.setPen(QPen(color, 2))
            p.drawRect(x, y, w, h)
            p.fillRect(x, y - 15, label_w, 15, bg_color)
            p.setPen(QColor(255, 255, 255))
            p.drawText(x + 2, y - 2, label)
        p.end()

# ==========================================
# CONFIGURATION POPUP WINDOW
# ==========================================
class ConfigWindow(QWidget):
    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setStyleSheet(STYLESHEET)
        self.resize(350, 180)
        
        if self.parent_app:
            x = self.parent_app.x() + (self.parent_app.width() // 2) - (self.width() // 2)
            y = self.parent_app.y() + (self.parent_app.height() // 2) - (self.height() // 2)
            self.move(x, y)
        
        self.container = QFrame(self)
        self.container.setObjectName("Container")
        
        main_vbox = QVBoxLayout(self)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.addWidget(self.container)
        
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(1, 1, 1, 1)
        
        title_bar = QFrame()
        title_bar.setStyleSheet(f"background-color: {C_BG};")
        t_layout = QHBoxLayout(title_bar)
        t_layout.setContentsMargins(10, 5, 5, 5)
        
        lbl = QLabel("CONFIGURATION")
        lbl.setStyleSheet(f"color: {C_ACCENT}; font-weight: bold; font-size: 11pt;")
        t_layout.addWidget(lbl)
        t_layout.addStretch()
        
        q_btn = QPushButton("Quit")
        q_btn.setObjectName("QuitButton")
        q_btn.clicked.connect(self.close)
        t_layout.addWidget(q_btn)
        layout.addWidget(title_bar)
        
        self.old_pos = None
        title_bar.mousePressEvent = self.title_press
        title_bar.mouseMoveEvent = self.title_move
        
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(15, 10, 15, 15)
        content_layout.setSpacing(10)
        
        add_layout = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Tag")
        self.name_input.setFixedWidth(60)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("IP / URL")
        
        add_btn = QPushButton("+")
        add_btn.setObjectName("AccentButton")
        add_btn.setFixedWidth(40) 
        add_btn.clicked.connect(self.parent_app.add_url)
        add_btn.clicked.connect(self.clear_inputs) 
        
        add_layout.addWidget(self.name_input)
        add_layout.addWidget(self.url_input)
        add_layout.addWidget(add_btn)
        content_layout.addLayout(add_layout)
        
        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("W:"))
        self.w_input = QLineEdit(str(self.parent_app.state_data.get("window_w", 1280)))
        self.w_input.setFixedWidth(50)
        size_layout.addWidget(self.w_input)
        
        size_layout.addWidget(QLabel("H:"))
        self.h_input = QLineEdit(str(self.parent_app.state_data.get("window_h", 800)))
        self.h_input.setFixedWidth(50)
        size_layout.addWidget(self.h_input)
        
        apply_btn = QPushButton("APPLY SIZE")
        apply_btn.setObjectName("AccentButton")
        apply_btn.clicked.connect(self.apply_size_to_parent)
        size_layout.addWidget(apply_btn)
        size_layout.addStretch()
        content_layout.addLayout(size_layout)
        
        import_btn = QPushButton("IMPORT DATABASE FILE")
        import_btn.setObjectName("AccentButton")
        import_btn.clicked.connect(self.parent_app.import_db)
        content_layout.addWidget(import_btn)
        
        layout.addLayout(content_layout)
        layout.addStretch()

    def title_press(self, event):
        if event.button() == Qt.LeftButton: self.old_pos = event.globalPos()
        
    def title_move(self, event):
        if self.old_pos:
            delta = QPoint(event.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPos()

    def clear_inputs(self):
        self.name_input.clear()
        self.url_input.clear()

    def apply_size_to_parent(self):
        try:
            w = int(self.w_input.text())
            h = int(self.h_input.text())
            self.parent_app.resize(w, h)
            self.parent_app.state_data["window_w"] = w
            self.parent_app.state_data["window_h"] = h
            dc_save_state(self.parent_app.state_data)
        except ValueError: pass


# ==========================================
# MAIN APPLICATION
# ==========================================
class CombinedSystemApp(QMainWindow):
    system_status_signal = pyqtSignal(str, str) 

    def __init__(self):
        super().__init__()
        qt_plugin_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
        if os.path.exists(qt_plugin_path): os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = qt_plugin_path
            
        self.state_data = dc_load_state()
        self.is_hidden = False
        self.previous_state = None
        self.focused_screen_idx = None
        self.config_window = None
        self.detection_enabled = False
        self.detect_person = True
        self.detect_drone = True
        self.detect_flash = True
        self.use_gpu = GPU_AVAILABLE
        self._stream_pairs = []
        self._detect_btn = None

        # Worker setup
        self.detection_worker = DetectionWorker()
        self.detection_worker.device = "cuda:0" if self.use_gpu else "cpu"
        self.detection_worker.results_ready.connect(self._on_detection_results)
        self.detection_worker.start()

        # Timer runs faster now (100ms) to feed the worker without blocking UI
        self._detect_timer = QTimer()
        self._detect_timer.timeout.connect(self._capture_frames_for_worker)
        self._detect_timer.start(100)

        self._auto_import()
        
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setStyleSheet(STYLESHEET)
        self.resize(self.state_data.get("window_w", 1280), self.state_data.get("window_h", 800))
        self.center_window()

        self._build_ui()

        try:
            import keyboard
            keyboard.add_hotkey("f4", self.toggle_visibility)
        except ImportError: pass

    # ================= UI BUILDING =================
    def _build_ui(self):
        self.container = QFrame()
        self.container.setObjectName("Container")
        self.setCentralWidget(self.container)
        
        self.main_layout = QVBoxLayout(self.container)
        self.main_layout.setContentsMargins(1, 1, 1, 1)
        self.main_layout.setSpacing(0)
        
        self.build_title_bar()
        
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        self.commander_panel = QFrame()
        com_layout = QVBoxLayout(self.commander_panel)
        com_layout.setContentsMargins(0, 0, 0, 0)
        com_layout.setSpacing(0)
        
        self.displays_frame = QFrame()
        self.displays_layout = QGridLayout(self.displays_frame)
        self.displays_layout.setContentsMargins(0, 0, 0, 0)
        self.displays_layout.setSpacing(0)
        com_layout.addWidget(self.displays_frame, stretch=1)
        
        h_sep = QFrame(); h_sep.setObjectName("Separator"); h_sep.setFixedHeight(1)
        com_layout.addWidget(h_sep)
        
        self.dashboard_frame = QFrame()
        self.dashboard_frame.setMaximumHeight(110) 
        self.dashboard_layout = QHBoxLayout(self.dashboard_frame)
        self.dashboard_layout.setContentsMargins(10, 5, 10, 5) 
        com_layout.addWidget(self.dashboard_frame, stretch=0)
        
        content_layout.addWidget(self.commander_panel, stretch=1)
        self.main_layout.addLayout(content_layout)
        
        sig = QLabel("oT", self.container)
        sig.setStyleSheet(f"color: {C_ACCENT}; font-size: 8pt;")
        sig.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.main_layout.addWidget(sig, alignment=Qt.AlignRight)

        self.build_dashboard()
        self.refresh_screens()
        self.refresh_routing()

    def build_title_bar(self):
        self.title_bar = QFrame()
        self.title_bar.setFixedHeight(35) 
        self.title_bar.setStyleSheet(f"background-color: {C_BG};")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(15, 0, 5, 0)
        
        lbl = QLabel("STREAM COMMANDER")
        lbl.setStyleSheet(f"color: {C_ACCENT}; font-weight: bold; font-size: 11pt;")
        title_layout.addWidget(lbl)
        title_layout.addStretch()
        
        help_btn = QPushButton("Help")
        help_btn.clicked.connect(self.show_help)
        title_layout.addWidget(help_btn)
        
        quit_btn = QPushButton("Quit")
        quit_btn.setObjectName("QuitButton")
        quit_btn.clicked.connect(self.on_close)
        title_layout.addWidget(quit_btn)
        
        self.main_layout.addWidget(self.title_bar)
        self.old_pos = None
        self.title_bar.mousePressEvent = self.title_press
        self.title_bar.mouseMoveEvent = self.title_move

    def title_press(self, event):
        if event.button() == Qt.LeftButton: self.old_pos = event.globalPos()
    def title_move(self, event):
        if self.old_pos:
            delta = QPoint(event.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPos()
            
    def center_window(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.geometry()
        self.move((screen.width() - size.width()) // 2, (screen.height() - size.height()) // 2)

    # ================= COMMANDER LOGIC =================
    def build_dashboard(self):
        layout_widget = QWidget()
        layout_widget.setFixedWidth(240) 
        layout_vbox = QVBoxLayout(layout_widget)
        layout_vbox.setContentsMargins(0, 0, 0, 0)
        
        l_btn_frame = QFrame()
        l_btn_layout = QHBoxLayout(l_btn_frame)
        l_btn_layout.setContentsMargins(0, 0, 0, 0)
        self.layout_btns = {}
        for lay in DC_LAYOUTS:
            btn = QPushButton(lay)
            btn.setObjectName("AccentButton" if lay == self.state_data["layout"] else "")
            btn.clicked.connect(lambda checked, l=lay: self.set_layout_user(l))
            l_btn_layout.addWidget(btn)
            self.layout_btns[lay] = btn
        l_btn_layout.addStretch()
        layout_vbox.addWidget(l_btn_frame)
        
        config_btn = QPushButton("[ CONFIGURATION ]")
        config_btn.setObjectName("AccentButton")
        config_btn.clicked.connect(self.show_config_popup)
        layout_vbox.addWidget(config_btn)

        detect_status = "ON" if self.detection_enabled else "OFF"
        self._detect_btn = QPushButton(f"DETECTION: {detect_status}")
        self._detect_btn.setObjectName("AccentButton" if self.detection_enabled else "")
        self._detect_btn.clicked.connect(self.toggle_detection)
        layout_vbox.addWidget(self._detect_btn)

        models_frame = QFrame()
        models_layout = QHBoxLayout(models_frame)
        models_layout.setContentsMargins(0, 0, 0, 0)
        models_layout.setSpacing(8)
        self.cb_person = QCheckBox("Person")
        self.cb_drone = QCheckBox("Drone")
        self.cb_flash = QCheckBox("Flash")
        self.cb_person.setChecked(self.detect_person)
        self.cb_drone.setChecked(self.detect_drone)
        self.cb_flash.setChecked(self.detect_flash)
        self.cb_person.toggled.connect(lambda v: self._set_model_enabled("person", v))
        self.cb_drone.toggled.connect(lambda v: self._set_model_enabled("drone", v))
        self.cb_flash.toggled.connect(lambda v: self._set_model_enabled("flash", v))
        models_layout.addWidget(self.cb_person)
        models_layout.addWidget(self.cb_drone)
        models_layout.addWidget(self.cb_flash)
        models_layout.addStretch()
        layout_vbox.addWidget(models_frame)

        device_frame = QFrame()
        device_layout = QHBoxLayout(device_frame)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(8)
        self.cb_gpu = QCheckBox("GPU" if GPU_AVAILABLE else "GPU (unavailable)")
        self.cb_gpu.setChecked(self.use_gpu)
        self.cb_gpu.setEnabled(GPU_AVAILABLE)
        self.cb_gpu.toggled.connect(self._set_device)
        device_layout.addWidget(self.cb_gpu)
        device_layout.addStretch()
        layout_vbox.addWidget(device_frame)

        layout_vbox.addStretch()
        self.dashboard_layout.addWidget(layout_widget)
        
        routing_widget = QWidget()
        self.routing_main_layout = QVBoxLayout(routing_widget)
        self.routing_main_layout.setContentsMargins(15, 0, 0, 0)
        self.routing_grid = QGridLayout()
        self.routing_grid.setSpacing(4) 
        self.routing_main_layout.addLayout(self.routing_grid)
        self.routing_main_layout.addStretch()
        self.dashboard_layout.addWidget(routing_widget, stretch=1)

    def show_config_popup(self):
        if self.config_window is None or not self.config_window.isVisible():
            self.config_window = ConfigWindow(self)
            self.config_window.show()
        else:
            self.config_window.raise_()
            self.config_window.activateWindow()

    def refresh_screens(self):
        for overlay, _ in self._stream_pairs:
            if overlay:
                overlay.close()
                overlay.deleteLater()
        clear_layout(self.displays_layout)
        self._stream_pairs = []
        with self.detection_worker._frames_lock:
            self.detection_worker._latest_frames.clear()
        
        s = self.state_data
        layout_type = s["layout"]
        count = DC_LAYOUT_COUNTS.get(layout_type, 4)
        
        rows, cols = (2, 2) if layout_type == "3x1" else {"1x1": (1, 1), "2x1": (1, 2), "2x2": (2, 2)}[layout_type]
        
        for i in range(count):
            container = QFrame()
            container.setObjectName("StreamContainer")
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            
            inner_layout = QGridLayout(container)
            inner_layout.setContentsMargins(1, 1, 1, 1)
            
            url = s["screens"][i] if i < len(s["screens"]) else ""
            current_ratio = s["ratios"][i] if i < len(s["ratios"]) else "Free"
            
            if url:
                player = StreamPlayer(url, ratio=current_ratio)
                inner_layout.addWidget(player, 0, 0)
                overlay = DetectionOverlay()
                if self.detection_enabled:
                    overlay.show()
                self._stream_pairs.append((overlay, player))
            else:
                lbl = QLabel(f"SCREEN {i+1}\n\n(No Stream Selected)")
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet("background-color: #000000; color: #333333; font-size: 12pt;")
                inner_layout.addWidget(lbl, 0, 0)
                self._stream_pairs.append((None, None))
                
            if layout_type == "3x1":
                if i == 0: self.displays_layout.addWidget(container, 0, 0)
                elif i == 1: self.displays_layout.addWidget(container, 0, 1)
                elif i == 2: self.displays_layout.addWidget(container, 1, 0, 1, 2)
            else:
                r, c = divmod(i, cols)
                self.displays_layout.addWidget(container, r, c)

    def refresh_routing(self):
        clear_layout(self.routing_grid)
        s = self.state_data
        count = DC_LAYOUT_COUNTS.get(s["layout"], 4)
        display_opts = ["[ OFF ]"] + [u["label"] for u in s["urls"]]
        label_to_url = {"[ OFF ]": ""}
        for u in s["urls"]: label_to_url[u["label"]] = u["url"]
        url_to_label = {v: k for k, v in label_to_url.items()}
        
        actual_count = 1 if self.previous_state is not None else count

        for i in range(actual_count):
            display_idx = 0 if self.previous_state is not None else i
            ui_label_idx = self.focused_screen_idx if self.previous_state is not None else i
            
            card = QFrame(); card.setObjectName("Card")
            card_layout = QVBoxLayout(card); card_layout.setContentsMargins(4, 2, 4, 2) 
            
            top_row = QHBoxLayout(); top_row.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"SCR {ui_label_idx + 1}"); lbl.setStyleSheet(f"color: {C_ACCENT}; font-size: 8pt; font-weight: bold;"); lbl.setFixedWidth(40)
            top_row.addWidget(lbl)
            
            combo = QComboBox(); combo.addItems(display_opts)
            current_url = s["screens"][display_idx] if display_idx < len(s["screens"]) else ""
            combo.setCurrentText(url_to_label.get(current_url, "[ OFF ]"))
            combo.currentIndexChanged.connect(lambda idx, screen_i=display_idx, cb=combo, map_dict=label_to_url: self.on_routing_change(screen_i, cb.currentText(), map_dict))
            top_row.addWidget(combo); card_layout.addLayout(top_row)
            
            ratio_frame = QFrame(); ratio_layout = QHBoxLayout(ratio_frame); ratio_layout.setContentsMargins(0, 2, 0, 0)
            current_ratio = s["ratios"][display_idx] if display_idx < len(s["ratios"]) else "Free"
            for r in DC_RATIOS:
                btn = QPushButton(r); btn.setObjectName("RatioBtnActive" if r == current_ratio else "RatioBtn")
                btn.clicked.connect(lambda checked, rv=r, screen_i=display_idx: self.set_ratio(screen_i, rv))
                ratio_layout.addWidget(btn)
            ratio_layout.addStretch()
            
            if self.previous_state is None:
                focus_btn = QPushButton("1x1"); focus_btn.setObjectName("RatioBtn"); focus_btn.setStyleSheet(f"color: {C_ACCENT}; border-color: {C_ACCENT};")
                focus_btn.clicked.connect(lambda checked, screen_i=i: self.focus_screen(screen_i))
            else:
                focus_btn = QPushButton("BACK"); focus_btn.setObjectName("RatioBtnActive")
                focus_btn.clicked.connect(self.unfocus_screen)
                
            ratio_layout.addWidget(focus_btn); card_layout.addWidget(ratio_frame)
            self.routing_grid.addWidget(card, i // 2, i % 2)

    def focus_screen(self, idx):
        self.previous_state = {"layout": self.state_data["layout"], "screens": list(self.state_data["screens"]), "ratios": list(self.state_data["ratios"])}
        self.focused_screen_idx = idx
        if idx != 0: self.state_data["screens"][0] = self.state_data["screens"][idx]; self.state_data["ratios"][0] = self.state_data["ratios"][idx]
        self.set_layout("1x1")

    def unfocus_screen(self):
        if not self.previous_state: return
        self.state_data["layout"] = self.previous_state["layout"]; self.state_data["screens"] = list(self.previous_state["screens"]); self.state_data["ratios"] = list(self.previous_state["ratios"])
        self.previous_state = None; self.focused_screen_idx = None
        dc_save_state(self.state_data); self.set_layout(self.state_data["layout"])

    def set_layout_user(self, layout):
        if self.previous_state is not None: self.previous_state = None; self.focused_screen_idx = None
        self.set_layout(layout)

    def on_routing_change(self, screen_i, label, map_dict):
        self.state_data["screens"][screen_i] = map_dict.get(label, "")
        dc_save_state(self.state_data); self.refresh_screens()

    def set_layout(self, layout):
        self.state_data["layout"] = layout
        dc_save_state(self.state_data)
        for lay, btn in self.layout_btns.items():
            btn.setObjectName("AccentButton" if lay == layout else "")
            btn.setStyleSheet("") 
        self.refresh_screens(); self.refresh_routing()

    def set_ratio(self, idx, ratio):
        while len(self.state_data["ratios"]) <= idx: self.state_data["ratios"].append("Free")
        self.state_data["ratios"][idx] = ratio
        dc_save_state(self.state_data); self.refresh_screens(); self.refresh_routing()

    def add_url(self):
        if not self.config_window: return
        label = self.config_window.name_input.text().strip()
        url_raw = self.config_window.url_input.text().strip()
        if not url_raw: return
        url = dc_normalize_url(url_raw)
        if any(u["url"] == url for u in self.state_data["urls"]): return
        self.state_data["urls"].append({"label": label or f"Source {len(self.state_data['urls']) + 1}", "url": url})
        dc_save_state(self.state_data)
        self.refresh_routing()

    def import_db(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Database", "", "Text files (*.txt);;All files (*.*)")
        if path and self._process_import_file(path):
            dc_save_state(self.state_data)
            self.refresh_routing()

    def _process_import_file(self, path):
        if not os.path.exists(path): return False
        added = False
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("LAYOUT:") or line.startswith("SCREEN_"): continue
                label, raw_url = "", line
                sep = re.search(r"\s*[|,]\s*", line)
                if sep:
                    parts = re.split(r"\s*[|,]\s*", line, maxsplit=1)
                    label, raw_url = parts[0].strip(), parts[1].strip()
                    if line.startswith("URL:"): label = label.replace("URL:", "").strip()
                url = dc_normalize_url(raw_url)
                if not url or any(u["url"] == url for u in self.state_data["urls"]): continue
                self.state_data["urls"].append({"label": label or f"Source {len(self.state_data['urls'])+1}", "url": url})
                added = True
        return added

    def _set_device(self, use_gpu):
        self.use_gpu = use_gpu and GPU_AVAILABLE
        self.detection_worker.device = "cuda:0" if self.use_gpu else "cpu"

    def _set_model_enabled(self, kind, value):
        if kind == "person":
            self.detect_person = value
            self.detection_worker.enable_person = value
        elif kind == "drone":
            self.detect_drone = value
            self.detection_worker.enable_drone = value
        elif kind == "flash":
            self.detect_flash = value
            self.detection_worker.enable_flash = value
        if not value:
            for overlay, _ in self._stream_pairs:
                if overlay:
                    overlay.set_rects([])

    def toggle_detection(self):
        if not YOLO_AVAILABLE or not CV2_AVAILABLE:
            if self._detect_btn:
                self._detect_btn.setText("ultralytics / opencv not installed")
            return
        self.detection_enabled = not self.detection_enabled
        if self._detect_btn:
            self._detect_btn.setText(f"DETECTION: {'ON' if self.detection_enabled else 'OFF'}")
            self._detect_btn.setObjectName("AccentButton" if self.detection_enabled else "")
            self._detect_btn.setStyleSheet("")
        if not self.detection_enabled:
            for overlay, _ in self._stream_pairs:
                if overlay:
                    overlay.set_rects([])
                    overlay.hide()
        else:
            self._sync_overlay_geometry()

    def _sync_overlay_geometry(self):
        for overlay, player in self._stream_pairs:
            if not overlay or not player:
                continue
            if not player.isVisible() or not self.detection_enabled or self.is_hidden:
                if overlay.isVisible():
                    overlay.hide()
                continue
            top_left = player.mapToGlobal(QPoint(0, 0))
            overlay.setGeometry(top_left.x(), top_left.y(),
                                player.width(), player.height())
            if not overlay.isVisible():
                overlay.show()
            overlay.raise_()

    def _capture_frames_for_worker(self):
        self._sync_overlay_geometry()

        if not self.detection_enabled or not YOLO_AVAILABLE or not CV2_AVAILABLE:
            return

        screen = QApplication.primaryScreen()
        if screen is None:
            return

        for idx, (overlay, player) in enumerate(self._stream_pairs):
            if not overlay or not player:
                continue

            if not player.isVisible() or player.width() <= 0 or player.height() <= 0:
                continue

            try:
                # QWebEngineView renders web content in a separate GPU process,
                # so player.grab() returns a blank image. Grab the widget's
                # region from the screen instead so YOLO sees the real pixels.
                top_left = player.mapToGlobal(QPoint(0, 0))
                pixmap = screen.grabWindow(
                    0, top_left.x(), top_left.y(),
                    player.width(), player.height()
                )
                if pixmap.isNull():
                    continue

                qimg = pixmap.toImage().convertToFormat(QImage.Format_RGB888)
                # On HiDPI displays the grabbed image is larger than the
                # widget's logical size; rescale so detection rects align
                # with the overlay's coordinate system.
                if qimg.width() != player.width() or qimg.height() != player.height():
                    qimg = qimg.scaled(player.width(), player.height())
                w, h = qimg.width(), qimg.height()
                if w == 0 or h == 0:
                    continue

                ptr = qimg.bits()
                ptr.setsize(qimg.byteCount())
                bpl = qimg.bytesPerLine()
                arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, bpl)
                frame = arr[:, : w * 3].reshape(h, w, 3).copy()
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

                self.detection_worker.submit_frame(idx, frame_bgr, gray, overlay.drawn_zones())

            except Exception:
                pass

    def _on_detection_results(self, idx, rects):
        if idx < len(self._stream_pairs):
            overlay, _ = self._stream_pairs[idx]
            if overlay:
                overlay.set_rects(rects)

    def _auto_import(self):
        if self._process_import_file(AUTO_IMPORT_FILE):
            dc_save_state(self.state_data)

    # ================= SYSTEM =================
    def toggle_visibility(self):
        if self.is_hidden:
            self.show()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.show() 
            QTimer.singleShot(100, lambda: self.setWindowFlag(Qt.WindowStaysOnTopHint, False) or self.show())
            self.is_hidden = False
        else:
            self.hide()
            self.is_hidden = True

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F4: self.toggle_visibility()

    def show_help(self):
        self.help_win = QWidget()
        self.help_win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.help_win.setStyleSheet(STYLESHEET); self.help_win.resize(400, 200)
        self.help_win.move(self.x() + (self.width() // 2) - 200, self.y() + (self.height() // 2) - 100)
        
        layout = QVBoxLayout(self.help_win); layout.setContentsMargins(1, 1, 1, 1)
        tb = QFrame(); tb.setStyleSheet(f"background-color: {C_BG};")
        t_layout = QHBoxLayout(tb); t_layout.setContentsMargins(10, 5, 5, 5)
        lbl = QLabel("HELP"); lbl.setStyleSheet(f"color: {C_ACCENT}; font-weight: bold;")
        q_btn = QPushButton("Quit"); q_btn.setObjectName("QuitButton"); q_btn.clicked.connect(self.help_win.close)
        t_layout.addWidget(lbl); t_layout.addStretch(); t_layout.addWidget(q_btn); layout.addWidget(tb)
        
        txt = QLabel("STREAM COMMANDER HELP:\n\n- Press F4 to hide/show window.\n- Use the dashboard below to control video routing & layout.\n- All settings are saved automatically.")
        txt.setMargin(20); layout.addWidget(txt); self.help_win.show()

    def on_close(self):
        self.detection_worker.stop()
        self.close()
        os._exit(0)

if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    window = CombinedSystemApp()
    window.show()
    sys.exit(app.exec_())