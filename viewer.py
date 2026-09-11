import sys
import os
import time
import json
import keyboard
import threading
import socketio
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QStackedWidget,
                             QSplitter, QFrame, QDesktopWidget)
from PyQt5.QtCore import Qt, QTimer, QUrl, QObject, pyqtSignal
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
show_anim = (config_data.get("show_animation", "True") == "True")

# --- אנימציית ברירת מחדל (עיגול ירוק) למקרה שהקובץ חסר ---
HTML_ANIMATED_FALLBACK = """
<body style='background-color: #121212; margin: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh;'>
    <div style='border: 4px solid rgba(255,255,255,0.05); border-left-color: #389379; border-radius: 50%; width: 60px; height: 60px; animation: spin 1s linear infinite;'></div>
    <h2 style='color: #555555; font-family: Consolas, sans-serif; letter-spacing: 3px; margin-top: 25px; font-size: 14px;'>WAITING FOR SIGNAL...</h2>
    <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
</body>
"""

# --- ניסיון לטעון את אנימציית ה-JSON ---
LOTTIE_FILE = "loading.json"
HTML_ANIMATED = HTML_ANIMATED_FALLBACK

if os.path.exists(LOTTIE_FILE):
    try:
        with open(LOTTIE_FILE, "r", encoding="utf-8") as f:
            lottie_data = f.read()
        
        # יצירת HTML ששותל את ה-JSON פנימה ומנגן אותו
        HTML_ANIMATED = f"""
        <body style='background-color: #121212; margin: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh;'>
            <div id='lottie-container' style='width: 150px; height: 150px;'></div>
            <h2 style='color: #555555; font-family: Consolas, sans-serif; letter-spacing: 3px; margin-top: 15px; font-size: 14px;'>WAITING FOR SIGNAL...</h2>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/bodymovin/5.12.2/lottie.min.js"></script>
            <script>
                var animData = {lottie_data};
                lottie.loadAnimation({{
                  container: document.getElementById('lottie-container'),
                  renderer: 'svg',
                  loop: true,
                  autoplay: true,
                  animationData: animData
                }});
            </script>
        </body>
        """
    except Exception as e:
        print(f"Failed to load Lottie JSON: {e}")

# מסך סטטי למקרה שהמשתמש כיבה אנימציות בהגדרות
HTML_STATIC = "<body style='background-color: #121212; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh;'><h2 style='color: #555555; font-family: Consolas, sans-serif; letter-spacing: 3px; font-size: 14px;'>WAITING FOR SIGNAL...</h2></body>"

PLACEHOLDER_HTML = HTML_ANIMATED if show_anim else HTML_STATIC

sio = socketio.Client()

class SocketSignals(QObject):
    state_received = pyqtSignal(dict)
    snapshot_requested = pyqtSignal(int)
    kill_requested = pyqtSignal()

signals = SocketSignals()

def get_layout_mode():
    if len(sys.argv) > 2:
        try: return int(sys.argv[2])
        except: return 4
    return 4

LAYOUT_FILE = "viewer_layouts.json"

def load_layout_config():
    if os.path.exists(LAYOUT_FILE):
        try:
            with open(LAYOUT_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {"1": {"w": 1200, "h": 800}, "2": {"w": 1200, "h": 800}, "4": {"w": 1200, "h": 800}}

def save_layout_config(layout, w, h):
    conf = load_layout_config()
    conf[str(layout)] = {"w": w, "h": h}
    try:
        with open(LAYOUT_FILE, "w") as f:
            json.dump(conf, f)
    except: pass

@sio.event
def connect():
    my_id = sys.argv[1] if len(sys.argv) > 1 else "Screen 1"
    my_layout = get_layout_mode()
    sio.emit("announce_viewer", {"target": my_id, "layout": my_layout})

@sio.event
def init_sync(data):
    state_data = data.get("state", {})
    my_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if my_id in state_data:
        signals.state_received.emit(state_data[my_id])

@sio.event
def screen_update(data):
    my_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if data.get("target") == my_id:
        signals.state_received.emit(data.get("payload"))

@sio.event
def execute_snapshot(data):
    my_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if data.get("target") == my_id:
        signals.snapshot_requested.emit(data.get("quad"))

@sio.event
def kill_command(data):
    my_id = sys.argv[1] if len(sys.argv) > 1 else "Screen 1"
    if data.get("target") == my_id:
        signals.kill_requested.emit()

def connect_socket():
    while True:
        try:
            if not sio.connected:
                sio.connect(shared.SERVER_URL, transports=['websocket'])
            sio.wait()
        except Exception as e:
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
# SCREEN WIDGET
# ==========================================
class ScreenWidget(QWidget):
    def __init__(self, main_window, index):
        super().__init__()
        self.main_window = main_window
        self.screen_index = index
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        
        self.title_label = QLabel("")
        self.title_label.setStyleSheet("background-color: #1A1A1A; color: #389379; padding: 4px 10px; font-weight: bold; font-family: 'Consolas'; font-size: 12px; border-bottom: 1px solid #333333;")
        self.title_label.setFixedHeight(28)
        self.title_label.hide()

        self.video_frame = QWebEngineView()
        self.video_frame.setHtml(PLACEHOLDER_HTML)
        
        self.layout.addWidget(self.title_label)
        self.layout.addWidget(self.video_frame)
        self.raw_url = ""
        self.current_url = ""

    def load_url(self, raw_url, name=""):
        self.raw_url = raw_url
        url = raw_url if "://" in raw_url or not raw_url else "http://" + raw_url
        if url != self.current_url:
            self.current_url = url
            if url: 
                self.video_frame.load(QUrl(url))
            else: 
                self.video_frame.setHtml(PLACEHOLDER_HTML)

        if name and name != "None" and url:
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
        my_dim = self.layout_config.get(str(self.layout_mode), {"w": 1200, "h": 800})
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
        
        keyboard.on_press_key("F4", lambda _: QTimer.singleShot(0, self.toggle_visibility))
        
        self.apply_stylesheet()
        
        signals.state_received.connect(self.on_state_sync_received)
        signals.snapshot_requested.connect(self.take_quad_snapshot)
        signals.kill_requested.connect(self.close)

    def take_quad_snapshot(self, quad_idx):
        if quad_idx >= len(self.screens): return
        try:
            folder = os.path.abspath(shared.SNAPSHOTS_DIR)
            if not os.path.exists(folder):
                os.makedirs(folder)
                
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_screen_id = self.screen_id.replace(" ", "")
            filename = f"Snap_{safe_screen_id}_Quad{quad_idx+1}_{timestamp}.png"
            filepath = os.path.join(folder, filename)
            
            pixmap = self.screens[quad_idx].video_frame.grab()
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
            s = ScreenWidget(self, 0)
            self.screens.append(s)
            grid_v_layout.addWidget(s)
        elif self.layout_mode == 2:
            self.main_splitter = QSplitter(Qt.Horizontal)
            for i in range(2):
                s = ScreenWidget(self, i)
                self.screens.append(s)
                self.main_splitter.addWidget(s)
            grid_v_layout.addWidget(self.main_splitter)
        else:
            self.main_splitter = QSplitter(Qt.Vertical)
            self.top_splitter = QSplitter(Qt.Horizontal)
            self.bottom_splitter = QSplitter(Qt.Horizontal)
            
            for i in range(2):
                s = ScreenWidget(self, i)
                self.screens.append(s)
                self.top_splitter.addWidget(s)
                
            for i in range(2, 4):
                s = ScreenWidget(self, i)
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
        
        self.fs_title_label = QLabel("")
        self.fs_title_label.setStyleSheet("background-color: #1A1A1A; color: #389379; padding: 4px 10px; font-weight: bold; font-family: 'Consolas'; font-size: 12px; border-bottom: 1px solid #333333;")
        self.fs_title_label.setFixedHeight(28)
        self.fs_title_label.hide()

        self.fs_video_frame = QWebEngineView()
        self.fs_video_frame.setHtml(PLACEHOLDER_HTML)
        
        fs_v_layout.addWidget(self.fs_title_label)
        fs_v_layout.addWidget(self.fs_video_frame)
        
        self.fullscreen_page_layout.addWidget(self.fs_container)
        self.stacked_widget.addWidget(self.fullscreen_page)

    def on_state_sync_received(self, state):
        self.is_blackout = (state.get('blackout', 'False') == 'True')
        
        for i in range(self.layout_mode):
            url = "" if self.is_blackout else state.get(str(i), "")
            name = "" if self.is_blackout else state.get(f"{i}_name", "")
            self.screens[i].load_url(url, name)
                
        new_fs = int(state.get('fullscreen', '-1'))
        if new_fs != self.current_fs_index and new_fs < self.layout_mode:
            self.current_fs_index = new_fs
            if self.current_fs_index >= 0:
                fs_url = "" if self.is_blackout else state.get(str(self.current_fs_index), "")
                fs_name = "" if self.is_blackout else state.get(f"{self.current_fs_index}_name", "")
                self.apply_fullscreen_internal(self.current_fs_index, fs_url, fs_name)
            else:
                self.apply_fullscreen_internal(-1, "", "")

    def apply_fullscreen_internal(self, index, url, name=""):
        conf = load_layout_config()
        
        if index == -1: 
            self.stacked_widget.setCurrentIndex(0)
            dim = conf.get(str(self.layout_mode), {"w": 1200, "h": 800})
            self.grid_container.setFixedSize(dim["w"], dim["h"])
            self.fs_container.setFixedSize(dim["w"], dim["h"])
        else:
            if url:
                self.fs_video_frame.load(QUrl(url if "://" in url else "http://" + url))
            else:
                self.fs_video_frame.setHtml(PLACEHOLDER_HTML)
                
            if name and name != "None" and url:
                self.fs_title_label.setText(name)
                self.fs_title_label.show()
            else:
                self.fs_title_label.hide()

            self.stacked_widget.setCurrentIndex(1)
            
            dim1 = conf.get("1", {"w": 1200, "h": 800})
            self.grid_container.setFixedSize(dim1["w"], dim1["h"])
            self.fs_container.setFixedSize(dim1["w"], dim1["h"])

    def toggle_visibility(self):
        if self.isHidden(): self.showFullScreen()
        else: self.hide()

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

if __name__ == '__main__':
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    
    app = QApplication(sys.argv)
    target_screen_name = sys.argv[1] if len(sys.argv) > 1 else "Screen 1"
    
    window = MainWindow(target_screen_name)
    
    desktop = QDesktopWidget()
    try:
        screen_index = int(''.join(filter(str.isdigit, target_screen_name))) - 1
        if screen_index < desktop.screenCount():
            rect = desktop.screenGeometry(screen_index)
            window.move(rect.left(), rect.top())
    except: pass

    window.showFullScreen()
    sys.exit(app.exec_())