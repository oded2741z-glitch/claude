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
show_anim = (config_data.get("show_animation", "True") == "True")

HTML_ANIMATED_FALLBACK = """
<body style='background-color: #121212; margin: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh;'>
    <div style='border: 4px solid rgba(255,255,255,0.05); border-left-color: #389379; border-radius: 50%; width: 60px; height: 60px; animation: spin 1s linear infinite;'></div>
    <h2 style='color: #555555; font-family: Consolas, sans-serif; letter-spacing: 3px; margin-top: 25px; font-size: 14px;'>WAITING FOR SIGNAL...</h2>
    <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
</body>
"""

LOTTIE_FILE = "loading.json"
HTML_ANIMATED = HTML_ANIMATED_FALLBACK

if os.path.exists(LOTTIE_FILE):
    try:
        with open(LOTTIE_FILE, "r", encoding="utf-8") as f:
            lottie_data = f.read()

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

HTML_STATIC = "<body style='background-color: #121212; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh;'><h2 style='color: #555555; font-family: Consolas, sans-serif; letter-spacing: 3px; font-size: 14px;'>WAITING FOR SIGNAL...</h2></body>"
HTML_BLACK = "<body style='background-color: #000000; margin: 0;'></body>"

PLACEHOLDER_HTML = HTML_ANIMATED if show_anim else HTML_STATIC
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
