import sys
import os
import time
import json
import threading
import socketio
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QStackedWidget,
                             QSplitter, QFrame)
from PyQt5.QtCore import Qt, QUrl, QObject, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView

import shared

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--enable-gpu-rasterization "
    "--ignore-gpu-blocklist "
    "--disable-background-timer-throttling "
    "--disable-backgrounding-occluded-windows "
    "--disable-features=CalculateNativeWinOcclusion "
    "--autoplay-policy=no-user-gesture-required"
)

config_data = shared.load_config()
animation_name = shared.load_animation_name(config_data)

ANIM_SHELL = """<!DOCTYPE html><html><head><meta charset='utf-8'><style>
html,body{height:100%}
body{background:#121212;margin:0;display:flex;flex-direction:column;
 justify-content:center;align-items:center;overflow:hidden}
h2{color:#555555;font-family:Consolas,sans-serif;letter-spacing:3px;font-size:14px;margin:26px 0 0}
__CSS__
</style></head><body>
__SVG__
<h2>WAITING FOR SIGNAL...</h2>
</body></html>"""

CSS_ELTA = """
svg{width:62%;max-width:420px;min-width:170px}
.sq,.rot,.elta{animation-duration:2.333s;animation-iteration-count:infinite;
 animation-timing-function:ease-in-out}
.sq-a{transform-origin:-24px 68px;animation-name:flyLeftEarly}
.sq-b{transform-origin:2px 68px;animation-name:flyLeftLate}
.sq-c{transform-origin:118px 68px;animation-name:flyRightLate}
.sq-d{transform-origin:144px 68px;animation-name:flyRightEarly}
.rot-a{transform-origin:-24px 60px;animation-name:spinLeftEarly,tintEarly}
.rot-b{transform-origin:2px 60px;animation-name:spinLeftLate}
.rot-c{transform-origin:118px 60px;animation-name:spinRightLate}
.rot-d{transform-origin:144px 60px;animation-name:spinRightEarly,tintEarly}
.rot-b,.rot-c{fill:#2766BE}
.rot-a,.rot-d{fill:#246CD0}
.elta{transform-origin:60px 70px;animation-name:eltaBounce}
.elta text{fill:#3787F6;font-family:'Segoe UI',Arial,Helvetica,sans-serif;font-weight:700;
 font-size:26px;letter-spacing:2px;text-anchor:middle}
@keyframes flyLeftEarly{
 0%{transform:translate(0px,0px) scale(1,1)}
 5.4%{transform:translate(-13px,-21px) scale(1,1)}
 10.7%{transform:translate(-26px,0px) scale(1,1)}
 16.4%{transform:translate(-26px,0px) scale(1.2,0.8)}
 21.4%,72.1%{transform:translate(-26px,0px) scale(1,1)}
 77.5%{transform:translate(-13px,-21px) scale(1,1)}
 82.9%{transform:translate(0px,0px) scale(1,1)}
 88.6%{transform:translate(0px,0px) scale(1.2,0.8)}
 93.6%,100%{transform:translate(0px,0px) scale(1,1)}}
@keyframes flyLeftLate{
 0%,7.1%{transform:translate(0px,0px) scale(1,1)}
 12.5%{transform:translate(-13px,-21px) scale(1,1)}
 17.9%{transform:translate(-26px,0px) scale(1,1)}
 23.6%{transform:translate(-26px,0px) scale(1.2,0.8)}
 28.6%,65%{transform:translate(-26px,0px) scale(1,1)}
 70.4%{transform:translate(-13px,-21px) scale(1,1)}
 75.7%{transform:translate(0px,0px) scale(1,1)}
 81.4%{transform:translate(0px,0px) scale(1.2,0.8)}
 86.4%,100%{transform:translate(0px,0px) scale(1,1)}}
@keyframes flyRightEarly{
 0%{transform:translate(0px,0px) scale(1,1)}
 5.4%{transform:translate(13px,-21px) scale(1,1)}
 10.7%{transform:translate(26px,0px) scale(1,1)}
 16.4%{transform:translate(26px,0px) scale(1.2,0.8)}
 21.4%,72.1%{transform:translate(26px,0px) scale(1,1)}
 77.5%{transform:translate(13px,-21px) scale(1,1)}
 82.9%{transform:translate(0px,0px) scale(1,1)}
 88.6%{transform:translate(0px,0px) scale(1.2,0.8)}
 93.6%,100%{transform:translate(0px,0px) scale(1,1)}}
@keyframes flyRightLate{
 0%,7.1%{transform:translate(0px,0px) scale(1,1)}
 12.5%{transform:translate(13px,-21px) scale(1,1)}
 17.9%{transform:translate(26px,0px) scale(1,1)}
 23.6%{transform:translate(26px,0px) scale(1.2,0.8)}
 28.6%,65%{transform:translate(26px,0px) scale(1,1)}
 70.4%{transform:translate(13px,-21px) scale(1,1)}
 75.7%{transform:translate(0px,0px) scale(1,1)}
 81.4%{transform:translate(0px,0px) scale(1.2,0.8)}
 86.4%,100%{transform:translate(0px,0px) scale(1,1)}}
@keyframes spinLeftEarly{
 0%{transform:rotate(0deg)} 10.7%,72.1%{transform:rotate(-180deg)}
 82.9%,100%{transform:rotate(0deg)}}
@keyframes spinLeftLate{
 0%,7.1%{transform:rotate(0deg)} 17.9%,65%{transform:rotate(-180deg)}
 75.7%,100%{transform:rotate(0deg)}}
@keyframes spinRightEarly{
 0%{transform:rotate(0deg)} 10.7%,72.1%{transform:rotate(180deg)}
 82.9%,100%{transform:rotate(0deg)}}
@keyframes spinRightLate{
 0%,7.1%{transform:rotate(0deg)} 17.9%,65%{transform:rotate(180deg)}
 75.7%,100%{transform:rotate(0deg)}}
@keyframes tintEarly{
 0%{fill:#246CD0} 10.7%,72.1%{fill:#3787F6} 82.9%,100%{fill:#246CD0}}
@keyframes eltaBounce{
 0%,14.3%{transform:translate(0px,0px) scale(1,1)}
 20%{transform:translate(0px,-21px) scale(1,1)}
 25.7%{transform:translate(0px,0px) scale(1,1)}
 30.7%{transform:translate(0px,0px) scale(1.2,0.8)}
 35.7%,57.1%{transform:translate(0px,0px) scale(1,1)}
 62.9%{transform:translate(0px,-21px) scale(1,1)}
 68.6%{transform:translate(0px,0px) scale(1,1)}
 73.6%{transform:translate(0px,0px) scale(1.2,0.8)}
 78.6%,100%{transform:translate(0px,0px) scale(1,1)}}
"""

SVG_ELTA = """
<svg viewBox='-62 24 244 52' xmlns='http://www.w3.org/2000/svg'>
 <g class='sq sq-a'><rect class='rot rot-a' x='-32' y='52' width='16' height='16' rx='2'/></g>
 <g class='sq sq-b'><rect class='rot rot-b' x='-6' y='52' width='16' height='16' rx='2'/></g>
 <g class='sq sq-c'><rect class='rot rot-c' x='110' y='52' width='16' height='16' rx='2'/></g>
 <g class='sq sq-d'><rect class='rot rot-d' x='136' y='52' width='16' height='16' rx='2'/></g>
 <g class='elta'><text x='62' y='70'>ELTA</text></g>
</svg>
"""

CSS_ELTA_WAVE = """
svg{width:58%;max-width:380px;min-width:160px}
.w{animation:hop 2s ease-in-out infinite}
.w1{transform-origin:144px 68px;animation-delay:0s}
.w2{transform-origin:118px 68px;animation-delay:.09s}
.w3{transform-origin:87px 70px;animation-delay:.18s}
.w4{transform-origin:68px 70px;animation-delay:.27s}
.w5{transform-origin:50px 70px;animation-delay:.36s}
.w6{transform-origin:33px 70px;animation-delay:.45s}
.w7{transform-origin:2px 68px;animation-delay:.54s}
.w8{transform-origin:-24px 68px;animation-delay:.63s}
.sqf{fill:#246CD0}
.ltr{fill:#3787F6;font-family:'Segoe UI',Arial,Helvetica,sans-serif;font-weight:700;
 font-size:26px;text-anchor:middle}
@keyframes hop{
 0%,45%,100%{transform:translate(0px,0px) scale(1,1)}
 15%{transform:translate(0px,-16px) scale(1,1)}
 30%{transform:translate(0px,0px) scale(1,1)}
 37%{transform:translate(0px,0px) scale(1.2,0.8)}}
"""

CSS_ELTA_WAVE_LTR = """
svg{width:58%;max-width:380px;min-width:160px}
.w{animation:hop 2s ease-in-out infinite}
.w1{transform-origin:144px 68px;animation-delay:.63s}
.w2{transform-origin:118px 68px;animation-delay:.54s}
.w3{transform-origin:87px 70px;animation-delay:.45s}
.w4{transform-origin:68px 70px;animation-delay:.36s}
.w5{transform-origin:50px 70px;animation-delay:.27s}
.w6{transform-origin:33px 70px;animation-delay:.18s}
.w7{transform-origin:2px 68px;animation-delay:.09s}
.w8{transform-origin:-24px 68px;animation-delay:0s}
.sqf{fill:#246CD0}
.ltr{fill:#3787F6;font-family:'Segoe UI',Arial,Helvetica,sans-serif;font-weight:700;
 font-size:26px;text-anchor:middle}
@keyframes hop{
 0%,45%,100%{transform:translate(0px,0px) scale(1,1)}
 15%{transform:translate(0px,-16px) scale(1,1)}
 30%{transform:translate(0px,0px) scale(1,1)}
 37%{transform:translate(0px,0px) scale(1.2,0.8)}}
"""

SVG_ELTA_WAVE = """
<svg viewBox='-38 32 194 44' xmlns='http://www.w3.org/2000/svg'>
 <g class='w w8'><rect class='sqf' x='-32' y='52' width='16' height='16' rx='2'/></g>
 <g class='w w7'><rect class='sqf' x='-6' y='52' width='16' height='16' rx='2'/></g>
 <g class='w w6'><text class='ltr' x='33' y='70'>E</text></g>
 <g class='w w5'><text class='ltr' x='50' y='70'>L</text></g>
 <g class='w w4'><text class='ltr' x='68' y='70'>T</text></g>
 <g class='w w3'><text class='ltr' x='87' y='70'>A</text></g>
 <g class='w w2'><rect class='sqf' x='110' y='52' width='16' height='16' rx='2'/></g>
 <g class='w w1'><rect class='sqf' x='136' y='52' width='16' height='16' rx='2'/></g>
</svg>
"""

CSS_DOTS = """
svg{width:26%;max-width:150px;min-width:90px}
.dot{fill:#3787F6;transform-origin:center bottom;animation:dotHop 1.2s ease-in-out infinite}
.d2{animation-delay:.16s;fill:#2E7BE4}
.d3{animation-delay:.32s;fill:#246CD0}
@keyframes dotHop{
 0%,55%,100%{transform:translate(0px,0px) scale(1,1)}
 22%{transform:translate(0px,-16px) scale(1,1)}
 42%{transform:translate(0px,0px) scale(1,1)}
 48%{transform:translate(0px,0px) scale(1.25,0.75)}}
"""

SVG_DOTS = """
<svg viewBox='-32 -26 64 32' xmlns='http://www.w3.org/2000/svg'>
 <circle class='dot d1' cx='-20' cy='0' r='7'/>
 <circle class='dot d2' cx='0' cy='0' r='7'/>
 <circle class='dot d3' cx='20' cy='0' r='7'/>
</svg>
"""

CSS_RINGS = """
svg{width:20%;max-width:120px;min-width:80px}
.track{fill:none;stroke:rgba(55,135,246,0.14)}
.r1,.r2{fill:none;stroke-linecap:round;transform-origin:0px 0px}
.r1{stroke:#3787F6;animation:spinCw 1.4s linear infinite}
.r2{stroke:#246CD0;animation:spinCcw 1s linear infinite}
@keyframes spinCw{to{transform:rotate(360deg)}}
@keyframes spinCcw{to{transform:rotate(-360deg)}}
"""

SVG_RINGS = """
<svg viewBox='-34 -34 68 68' xmlns='http://www.w3.org/2000/svg'>
 <circle class='track' cx='0' cy='0' r='26' stroke-width='5'/>
 <circle class='track' cx='0' cy='0' r='16' stroke-width='5'/>
 <circle class='r1' cx='0' cy='0' r='26' stroke-width='5' stroke-dasharray='58 106'/>
 <circle class='r2' cx='0' cy='0' r='16' stroke-width='5' stroke-dasharray='34 67'/>
</svg>
"""

CSS_RADAR = """
svg{width:22%;max-width:130px;min-width:85px}
.grid{fill:none;stroke:rgba(55,135,246,0.16);stroke-width:1.5}
.sweep{transform-origin:0px 0px;animation:spinCw 2s linear infinite}
.beam{fill:url(#beamGrad)}
.edge{stroke:#3787F6;stroke-width:2;stroke-linecap:round}
.pip{fill:#3787F6}
@keyframes spinCw{to{transform:rotate(360deg)}}
"""

SVG_RADAR = """
<svg viewBox='-36 -36 72 72' xmlns='http://www.w3.org/2000/svg'>
 <defs><linearGradient id='beamGrad' x1='0' y1='0' x2='1' y2='0'>
  <stop offset='0%' stop-color='#3787F6' stop-opacity='0'/>
  <stop offset='100%' stop-color='#3787F6' stop-opacity='0.45'/>
 </linearGradient></defs>
 <circle class='grid' cx='0' cy='0' r='30'/>
 <circle class='grid' cx='0' cy='0' r='20'/>
 <circle class='grid' cx='0' cy='0' r='10'/>
 <g class='sweep'>
  <path class='beam' d='M0 0 L30 0 A30 30 0 0 0 21.21 -21.21 Z'/>
  <line class='edge' x1='0' y1='0' x2='30' y2='0'/>
 </g>
 <circle class='pip' cx='0' cy='0' r='3'/>
</svg>
"""

CSS_WAVES = """
svg{width:22%;max-width:130px;min-width:85px}
.ring{fill:none;stroke:#3787F6;stroke-width:3;transform-origin:0px 0px;
 animation:ripple 1.8s ease-out infinite}
.p2{animation-delay:.6s}
.p3{animation-delay:1.2s}
.core{fill:#3787F6}
@keyframes ripple{
 0%{transform:scale(0.18);opacity:0.9}
 100%{transform:scale(1);opacity:0}}
"""

SVG_WAVES = """
<svg viewBox='-34 -34 68 68' xmlns='http://www.w3.org/2000/svg'>
 <circle class='ring p1' cx='0' cy='0' r='30'/>
 <circle class='ring p2' cx='0' cy='0' r='30'/>
 <circle class='ring p3' cx='0' cy='0' r='30'/>
 <circle class='core' cx='0' cy='0' r='5'/>
</svg>
"""

CSS_SCAN = """
svg{width:36%;max-width:230px;min-width:130px}
.rail{fill:rgba(55,135,246,0.14)}
.knob{fill:#3787F6;animation:sweepX 1.1s ease-in-out infinite alternate}
.glow{fill:rgba(55,135,246,0.25);animation:sweepX 1.1s ease-in-out infinite alternate}
@keyframes sweepX{
 0%{transform:translate(-30px,0px)}
 100%{transform:translate(30px,0px)}}
"""

SVG_SCAN = """
<svg viewBox='-46 -12 92 24' xmlns='http://www.w3.org/2000/svg'>
 <rect class='rail' x='-40' y='-2' width='80' height='4' rx='2'/>
 <rect class='glow' x='-14' y='-4' width='28' height='8' rx='4'/>
 <rect class='knob' x='-7' y='-3' width='14' height='6' rx='3'/>
</svg>
"""

CSS_SPINNER = """
svg{width:18%;max-width:100px;min-width:70px}
.track{fill:none;stroke:rgba(55,135,246,0.15);stroke-width:5}
.arc{fill:none;stroke:#3787F6;stroke-width:5;stroke-linecap:round;
 transform-origin:0px 0px;animation:spinCw 1s linear infinite}
@keyframes spinCw{to{transform:rotate(360deg)}}
"""

SVG_SPINNER = """
<svg viewBox='-30 -30 60 60' xmlns='http://www.w3.org/2000/svg'>
 <circle class='track' cx='0' cy='0' r='22'/>
 <circle class='arc' cx='0' cy='0' r='22' stroke-dasharray='42 96'/>
</svg>
"""

ANIMATIONS = {
    "elta": (CSS_ELTA, SVG_ELTA),
    "elta_wave": (CSS_ELTA_WAVE, SVG_ELTA_WAVE),
    "elta_wave_ltr": (CSS_ELTA_WAVE_LTR, SVG_ELTA_WAVE),
    "dots": (CSS_DOTS, SVG_DOTS),
    "rings": (CSS_RINGS, SVG_RINGS),
    "radar": (CSS_RADAR, SVG_RADAR),
    "waves": (CSS_WAVES, SVG_WAVES),
    "scan": (CSS_SCAN, SVG_SCAN),
    "spinner": (CSS_SPINNER, SVG_SPINNER),
    "none": ("", ""),
}

def build_animation_page(name):
    css, svg = ANIMATIONS.get(name, ANIMATIONS[shared.DEFAULT_ANIMATION])
    return ANIM_SHELL.replace("__CSS__", css).replace("__SVG__", svg)

PLACEHOLDER_HTML = build_animation_page(animation_name)
HTML_BLACK = "<body style='background-color: #000000; margin: 0;'></body>"
BLACKOUT_KEY = "__blackout__"

sio = socketio.Client()

class SocketSignals(QObject):
    state_received = pyqtSignal(dict)
    snapshot_requested = pyqtSignal(int)
    kill_requested = pyqtSignal()

signals = SocketSignals()

def get_screen_id():
    return sys.argv[1] if len(sys.argv) > 1 else "Screen 1"

def get_layout_mode():
    if len(sys.argv) > 2:
        try:
            return int(sys.argv[2])
        except ValueError:
            return 4
    return 4

LAYOUT_FILE = "viewer_layouts.json"
DEFAULT_DIM = {"w": 1200, "h": 800}

def load_layout_config():
    if os.path.exists(LAYOUT_FILE):
        try:
            with open(LAYOUT_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"1": dict(DEFAULT_DIM), "2": dict(DEFAULT_DIM), "4": dict(DEFAULT_DIM)}

def save_layout_config(layout, w, h):
    conf = load_layout_config()
    conf[str(layout)] = {"w": w, "h": h}
    try:
        with open(LAYOUT_FILE, "w") as f:
            json.dump(conf, f)
    except Exception:
        pass

@sio.event
def connect():
    sio.emit("announce_viewer", {"target": get_screen_id(), "layout": get_layout_mode()})

@sio.event
def init_sync(data):
    state_data = data.get("state", {})
    my_id = get_screen_id()
    if my_id in state_data:
        signals.state_received.emit(state_data[my_id])

@sio.event
def screen_update(data):
    if data.get("target") == get_screen_id():
        payload = data.get("payload")
        if isinstance(payload, dict):
            signals.state_received.emit(payload)

@sio.event
def execute_snapshot(data):
    if data.get("target") == get_screen_id():
        try:
            signals.snapshot_requested.emit(int(data.get("quad", -1)))
        except (TypeError, ValueError):
            pass

@sio.event
def kill_command(data):
    if data.get("target") == get_screen_id():
        signals.kill_requested.emit()

def connect_socket():
    while True:
        try:
            if not sio.connected:
                sio.connect(shared.SERVER_URL, transports=['websocket'])
            sio.wait()
        except Exception:
            pass
        time.sleep(3)

threading.Thread(target=connect_socket, daemon=True).start()

class TopRightResizeGrip(QWidget):
    def __init__(self, main_window, target_widget):
        super().__init__()
        self.main_window = main_window
        self.target_widget = target_widget
        self.setFixedSize(26, 26)
        self.setCursor(Qt.SizeBDiagCursor)
        self.setStyleSheet("background-color: #389379; border: none;")
        self._resizing = False
        self.center_pos = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("⤢")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #121212; font-size: 16px; font-weight: bold; background: transparent; border: none;")
        layout.addWidget(lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._resizing = True
            self.center_pos = self.target_widget.mapToGlobal(self.target_widget.rect().center())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._resizing:
            mouse_x = event.globalPos().x()
            mouse_y = event.globalPos().y()
            new_w = max(400, int(abs(mouse_x - self.center_pos.x()) * 2))
            new_h = max(300, int(abs(mouse_y - self.center_pos.y()) * 2))
            self.target_widget.setFixedSize(new_w, new_h)
            self.main_window.fs_container.setFixedSize(new_w, new_h)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._resizing = False
        new_w = self.target_widget.width()
        new_h = self.target_widget.height()
        save_layout_config(self.main_window.layout_mode, new_w, new_h)
        event.accept()

# ==========================================
# CONTENT FRAME (one stream + title bar)
# ==========================================
class ContentFrame(QWidget):
    TITLE_STYLE = "background-color: #1A1A1A; color: #389379; padding: 4px 10px; font-weight: bold; font-family: 'Consolas'; font-size: 12px; border-bottom: 1px solid #333333;"

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_label = QLabel("")
        self.title_label.setStyleSheet(self.TITLE_STYLE)
        self.title_label.setFixedHeight(28)
        self.title_label.hide()

        self.video_frame = QWebEngineView()
        self.video_frame.setHtml(PLACEHOLDER_HTML)

        layout.addWidget(self.title_label)
        layout.addWidget(self.video_frame)
        self.current_key = ""

    def show_content(self, raw_url, name="", blackout=False):
        raw_url = raw_url or ""
        if blackout:
            key = BLACKOUT_KEY
        elif raw_url and "://" not in raw_url:
            key = "http://" + raw_url
        else:
            key = raw_url

        if key != self.current_key:
            self.current_key = key
            if key == BLACKOUT_KEY:
                self.video_frame.setHtml(HTML_BLACK)
            elif key:
                self.video_frame.load(QUrl(key))
            else:
                self.video_frame.setHtml(PLACEHOLDER_HTML)

        if name and name != "None" and key and key != BLACKOUT_KEY:
            self.title_label.setText(name)
            self.title_label.show()
        else:
            self.title_label.hide()

# ==========================================
# MAIN WINDOW
# ==========================================
class MainWindow(QWidget):
    def __init__(self, screen_id):
        super().__init__()
        self.screen_id = screen_id
        self.layout_mode = get_layout_mode()
        self.setObjectName("MainWindow")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        self.current_config = shared.load_config()
        self.show_header = (self.current_config.get("show_header", "True") == "True")

        self.layout_config = load_layout_config()
        my_dim = self.layout_config.get(str(self.layout_mode), DEFAULT_DIM)
        self.start_w = my_dim["w"]
        self.start_h = my_dim["h"]

        self.screens = []
        self.current_fs_index = -1
        self.is_blackout = False

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()
        self.main_layout.addWidget(self.stacked_widget)

        self.setup_grid_page()
        self.setup_fullscreen_page()

        self.watermark = QPushButton("oT", self)
        self.watermark.setObjectName("Watermark")
        self.watermark.setCursor(Qt.PointingHandCursor)
        self.watermark.clicked.connect(self.close)

        self.apply_stylesheet()

        signals.state_received.connect(self.on_state_sync_received)
        signals.snapshot_requested.connect(self.take_quad_snapshot)
        signals.kill_requested.connect(self.close)

    def take_quad_snapshot(self, quad_idx):
        if quad_idx < 0 or quad_idx >= len(self.screens):
            return
        try:
            folder = os.path.abspath(shared.SNAPSHOTS_DIR)
            if not os.path.exists(folder):
                os.makedirs(folder)

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_screen_id = self.screen_id.replace(" ", "")
            filename = f"Snap_{safe_screen_id}_Quad{quad_idx+1}_{timestamp}.png"
            filepath = os.path.join(folder, filename)

            if self.current_fs_index == quad_idx:
                widget = self.fs_frame.video_frame
            else:
                widget = self.screens[quad_idx].video_frame

            pixmap = None
            handle = self.windowHandle()
            screen = handle.screen() if handle else QApplication.primaryScreen()
            if screen and self.isVisible():
                top_left = widget.mapToGlobal(widget.rect().topLeft())
                geo = screen.geometry()
                pixmap = screen.grabWindow(0, top_left.x() - geo.x(), top_left.y() - geo.y(), widget.width(), widget.height())
            if pixmap is None or pixmap.isNull():
                pixmap = widget.grab()

            pixmap.save(filepath, "PNG")
            print(f"Snapshot saved: {filepath}")
        except Exception as e:
            print(f"Snapshot error: {e}")

    def setup_grid_page(self):
        self.grid_page = QWidget()
        self.grid_page_layout = QVBoxLayout(self.grid_page)
        self.grid_page_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_page_layout.setAlignment(Qt.AlignCenter)

        self.grid_container = QFrame()
        self.grid_container.setObjectName("GridContainer")
        self.grid_container.setFixedSize(self.start_w, self.start_h)

        grid_v_layout = QVBoxLayout(self.grid_container)
        grid_v_layout.setContentsMargins(2, 2, 2, 2)
        grid_v_layout.setSpacing(0)

        if self.show_header:
            header = QHBoxLayout()
            header.addStretch()
            header.addWidget(TopRightResizeGrip(self, self.grid_container))
            grid_v_layout.addLayout(header)

        if self.layout_mode == 1:
            s = ContentFrame()
            self.screens.append(s)
            grid_v_layout.addWidget(s)
        elif self.layout_mode == 2:
            self.main_splitter = QSplitter(Qt.Horizontal)
            for i in range(2):
                s = ContentFrame()
                self.screens.append(s)
                self.main_splitter.addWidget(s)
            grid_v_layout.addWidget(self.main_splitter)
        else:
            self.main_splitter = QSplitter(Qt.Vertical)
            self.top_splitter = QSplitter(Qt.Horizontal)
            self.bottom_splitter = QSplitter(Qt.Horizontal)

            for i in range(2):
                s = ContentFrame()
                self.screens.append(s)
                self.top_splitter.addWidget(s)

            for i in range(2, 4):
                s = ContentFrame()
                self.screens.append(s)
                self.bottom_splitter.addWidget(s)

            self.main_splitter.addWidget(self.top_splitter)
            self.main_splitter.addWidget(self.bottom_splitter)
            grid_v_layout.addWidget(self.main_splitter)

        self.grid_page_layout.addWidget(self.grid_container)
        self.stacked_widget.addWidget(self.grid_page)

    def setup_fullscreen_page(self):
        self.fullscreen_page = QWidget()
        self.fullscreen_page_layout = QVBoxLayout(self.fullscreen_page)
        self.fullscreen_page_layout.setContentsMargins(0, 0, 0, 0)
        self.fullscreen_page_layout.setAlignment(Qt.AlignCenter)

        self.fs_container = QFrame()
        self.fs_container.setObjectName("GridContainer")
        self.fs_container.setFixedSize(self.start_w, self.start_h)

        fs_v_layout = QVBoxLayout(self.fs_container)
        fs_v_layout.setContentsMargins(0, 0, 0, 0)
        fs_v_layout.setSpacing(0)

        self.fs_frame = ContentFrame()
        fs_v_layout.addWidget(self.fs_frame)

        self.fullscreen_page_layout.addWidget(self.fs_container)
        self.stacked_widget.addWidget(self.fullscreen_page)

    def on_state_sync_received(self, state):
        self.is_blackout = (state.get('blackout', 'False') == 'True')

        for i in range(self.layout_mode):
            self.screens[i].show_content(state.get(str(i), ""), state.get(f"{i}_name", ""), self.is_blackout)

        try:
            new_fs = int(state.get('fullscreen', '-1'))
        except (TypeError, ValueError):
            new_fs = -1
        if new_fs >= self.layout_mode:
            new_fs = -1

        page_changed = (new_fs != self.current_fs_index)
        self.current_fs_index = new_fs

        if new_fs >= 0:
            self.fs_frame.show_content(state.get(str(new_fs), ""), state.get(f"{new_fs}_name", ""), self.is_blackout)
            if page_changed:
                self.show_fullscreen_page()
        else:
            if page_changed:
                self.fs_frame.show_content("", "", False)
                self.show_grid_page()

    def show_grid_page(self):
        conf = load_layout_config()
        dim = conf.get(str(self.layout_mode), DEFAULT_DIM)
        self.stacked_widget.setCurrentIndex(0)
        self.grid_container.setFixedSize(dim["w"], dim["h"])
        self.fs_container.setFixedSize(dim["w"], dim["h"])

    def show_fullscreen_page(self):
        conf = load_layout_config()
        dim1 = conf.get("1", DEFAULT_DIM)
        self.stacked_widget.setCurrentIndex(1)
        self.grid_container.setFixedSize(dim1["w"], dim1["h"])
        self.fs_container.setFixedSize(dim1["w"], dim1["h"])

    def resizeEvent(self, event):
        self.watermark.adjustSize()
        self.watermark.move(self.width() - self.watermark.width() - 20, self.height() - self.watermark.height() - 20)

    def apply_stylesheet(self):
        self.setStyleSheet("""
            QWidget { background-color: #000; color: #fff; font-family: 'Consolas'; }
            QFrame#GridContainer { border: 2px solid #000000; background-color: #121212; }
            QPushButton { background-color: #333; color: #fff; border: none; font-weight: bold; }
            QPushButton#Watermark { background: transparent; color: rgba(255,255,255,0.2); }
        """)

def resolve_target_screen(app, screen_label):
    screens = app.screens()
    if not screens:
        return None, "no screens"

    node = shared.load_display_nodes().get(screen_label)

    if node:
        device_name = node.get("info2", "").strip().lower()
        if device_name:
            for scr in screens:
                if scr.name().strip().lower() == device_name:
                    return scr, f"device name '{scr.name()}'"

        offset = shared.parse_offset(node.get("offset", ""))
        if offset:
            for scr in screens:
                if scr.geometry().contains(offset[0], offset[1]):
                    return scr, f"offset X:{offset[0]} Y:{offset[1]}"

    digits = ''.join(filter(str.isdigit, screen_label))
    if digits:
        index = int(digits) - 1
        if 0 <= index < len(screens):
            return screens[index], f"label digit {index + 1}"

    return app.primaryScreen(), "fallback to primary"

if __name__ == '__main__':
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    target_screen_name = get_screen_id()

    window = MainWindow(target_screen_name)

    screen, reason = resolve_target_screen(app, target_screen_name)
    if screen is not None:
        rect = screen.geometry()
        window.move(rect.left(), rect.top())
        handle = window.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        print(f"Viewer '{target_screen_name}' -> {screen.name()} at {rect.x()},{rect.y()} ({reason})")

    window.showFullScreen()
    sys.exit(app.exec_())
