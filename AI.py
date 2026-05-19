import sys
from html import escape
from pathlib import Path

from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QLineEdit, QFrame, QListWidget)
from PyQt5.QtCore import Qt, QUrl, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWebEngineWidgets import QWebEngineView

# --- הגדרות עיצוב גלובליות ---
BG_COLOR = "#121212"
BG2_COLOR = "#1E1E1E"
ACCENT_COLOR = "#389379"
BTN_COLOR = "#333333"
TEXT_COLOR = "#FFFFFF"

HISTORY_FILE = Path("receiver_history.txt")
MAX_HISTORY = 10
DEFAULT_URL = "http://127.0.0.1:9006"

YOLO_MODEL_PATH = "yolov8n.pt"
PERSON_CLASS_ID = 0
DETECTION_CONF = 0.4

STYLESHEET = f"""
QWidget {{
    background-color: {BG_COLOR};
    color: {TEXT_COLOR};
    font-family: Consolas;
    font-size: 10pt;
}}
QPushButton {{
    background-color: {BTN_COLOR};
    color: {TEXT_COLOR};
    border: none;
    border-radius: 0px;
    padding: 6px 12px;
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {ACCENT_COLOR};
    color: {BG_COLOR};
}}
QPushButton#QuitButton {{
    background-color: {BTN_COLOR};
}}
QPushButton#QuitButton:hover {{
    background-color: #ff0000;
    color: {TEXT_COLOR};
}}
QLineEdit {{
    background-color: {BG2_COLOR};
    border: 1px solid {BTN_COLOR};
    padding: 5px;
    color: {TEXT_COLOR};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT_COLOR};
}}
QListWidget {{
    background-color: {BG2_COLOR};
    border: 1px solid {BTN_COLOR};
    color: {TEXT_COLOR};
    outline: none;
}}
QListWidget::item {{
    padding: 8px;
}}
QListWidget::item:selected {{
    background-color: {ACCENT_COLOR};
    color: {BG_COLOR};
}}
QListWidget::item:hover {{
    background-color: {BTN_COLOR};
}}
"""


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        return "http://" + raw
    return raw


class StreamPlayer(QWebEngineView):
    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setContextMenuPolicy(Qt.NoContextMenu)

        safe_url = escape(url, quote=True)
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <style>
            html, body {{
                width: 100%; height: 100%; margin: 0; padding: 0;
                overflow: hidden; background-color: {BG_COLOR};
                display: flex; justify-content: center; align-items: center;
            }}
            iframe {{
                border: none; width: 100%; height: 100%;
                background-color: {BG_COLOR};
            }}
        </style>
        </head>
        <body>
            <iframe src="{safe_url}" scrolling="no"></iframe>
        </body>
        </html>
        """
        self.setHtml(html, QUrl(url))


class DetectionWorker(QThread):
    """קורא פריימים מה-stream ומריץ זיהוי אנשים עם YOLOv8n."""

    frame_ready = pyqtSignal(QImage)
    error = pyqtSignal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        try:
            self._run_inner()
        except BaseException as e:
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            self.error.emit(f"Detection thread crashed:\n{type(e).__name__}: {e}")

    def _run_inner(self):
        try:
            import numpy as np
            import cv2
        except ImportError as e:
            self.error.emit(
                f"Missing package: {getattr(e, 'name', e)}\n"
                "Install: pip install opencv-python numpy"
            )
            return

        try:
            from ultralytics import YOLO
        except ImportError as e:
            self.error.emit(
                f"Missing package: {getattr(e, 'name', e)}\n"
                "Install: pip install ultralytics"
            )
            return

        try:
            model = YOLO(YOLO_MODEL_PATH)
        except Exception as e:
            self.error.emit(f"Model load failed:\n{type(e).__name__}: {e}")
            return

        cap = cv2.VideoCapture(self.url)
        if not cap.isOpened():
            cap.release()
            self.error.emit(f"Cannot open stream:\n{self.url}")
            return

        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        try:
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.msleep(50)
                    continue

                try:
                    results = model.predict(
                        frame,
                        classes=[PERSON_CLASS_ID],
                        conf=DETECTION_CONF,
                        verbose=False,
                        device="cpu",
                    )
                    annotated = results[0].plot()
                except Exception as e:
                    sys.stderr.write(f"inference error: {e}\n")
                    annotated = frame

                rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
                rgb = np.ascontiguousarray(rgb)
                h, w, ch = rgb.shape
                qimg = QImage(
                    rgb.tobytes(), w, h, ch * w, QImage.Format_RGB888
                )
                self.frame_ready.emit(qimg)
        finally:
            cap.release()


class DetectionPlayer(QLabel):
    """תצוגת stream עם זיהוי אנשים מצויר על הפריים."""

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background-color: {BG_COLOR}; color: {ACCENT_COLOR};"
            " font-size: 12pt; border: none;"
        )
        self.setText("LOADING YOLOv8n MODEL...")
        self._last_image = None

        self.worker = DetectionWorker(url, parent=self)
        self.worker.frame_ready.connect(self._update_frame)
        self.worker.error.connect(self._show_error)
        self.worker.start()

    def _update_frame(self, qimg):
        self._last_image = qimg
        self._render()

    def _render(self):
        if self._last_image is None:
            return
        pix = QPixmap.fromImage(self._last_image).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(pix)

    def _show_error(self, msg):
        self.setText(f"DETECTION ERROR\n\n{msg}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    def stop(self):
        if self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)


class FramelessDialog(QWidget):
    """חלון מודאלי משותף עם כותרת וכפתור סגירה."""

    def __init__(self, title, size, parent_window):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(STYLESHEET)
        self.resize(*size)

        x = parent_window.x() + (parent_window.width() - self.width()) // 2
        y = parent_window.y() + (parent_window.height() - self.height()) // 2
        self.move(x, y)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)

        self.container = QFrame()
        self.container.setStyleSheet(
            f"border: 1px solid {ACCENT_COLOR}; background-color: {BG_COLOR};"
        )
        self.body_layout = QVBoxLayout(self.container)
        outer.addWidget(self.container)

        title_bar = QFrame()
        title_bar.setStyleSheet("border: none;")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(10, 5, 5, 5)

        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {ACCENT_COLOR}; font-weight: bold;")
        close_btn = QPushButton("Close")
        close_btn.setObjectName("QuitButton")
        close_btn.clicked.connect(self.close)

        tb_layout.addWidget(lbl)
        tb_layout.addStretch()
        tb_layout.addWidget(close_btn)
        self.body_layout.addWidget(title_bar)


class IsolatedStreamReceiver(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(STYLESHEET)
        self.resize(750, 500)
        self.is_hidden = False
        self.drag_origin = None
        self.current_player = None
        self.placeholder = None
        self.active_dialog = None
        self.detection_enabled = False

        self.history = self._load_history()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, 1)

        self.container = QFrame(self)
        self.container.setStyleSheet(
            f"border: 1px solid {ACCENT_COLOR}; background-color: {BG_COLOR};"
        )
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        self._build_title_bar()
        self._build_controls()
        self._build_video_area()
        self._build_watermark()
        self._register_hotkey()

    def _load_history(self):
        if not HISTORY_FILE.exists():
            return [DEFAULT_URL]
        try:
            lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            return [DEFAULT_URL]
        items = [line.strip() for line in lines if line.strip()]
        return items or [DEFAULT_URL]

    def _save_history(self):
        try:
            HISTORY_FILE.write_text(
                "\n".join(self.history) + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    def _build_title_bar(self):
        title_bar = QFrame()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet("border: none;")
        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(15, 0, 5, 0)

        lbl = QLabel("STREAM RECEIVER [ISOLATED]")
        lbl.setStyleSheet(
            f"color: {ACCENT_COLOR}; font-weight: bold; font-size: 11pt;"
        )
        layout.addWidget(lbl)
        layout.addStretch()

        help_btn = QPushButton("Help")
        help_btn.clicked.connect(self.show_help)
        layout.addWidget(help_btn)

        quit_btn = QPushButton("Quit")
        quit_btn.setObjectName("QuitButton")
        quit_btn.clicked.connect(self.close)
        layout.addWidget(quit_btn)

        title_bar.mousePressEvent = self._title_press
        title_bar.mouseMoveEvent = self._title_move
        title_bar.mouseReleaseEvent = self._title_release

        self.container_layout.addWidget(title_bar)

    def _build_controls(self):
        frame = QFrame()
        frame.setStyleSheet("border: none;")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 10, 20, 5)

        layout.addWidget(QLabel("STREAM URL:"))

        self.url_input = QLineEdit()
        self.url_input.setText(self.history[0])
        self.url_input.returnPressed.connect(self.connect_stream)
        layout.addWidget(self.url_input, stretch=1)

        saved_btn = QPushButton("SAVED")
        saved_btn.clicked.connect(self.open_saved_list)
        layout.addWidget(saved_btn)

        self.detect_btn = QPushButton("DETECT: OFF")
        self.detect_btn.setCheckable(True)
        self.detect_btn.clicked.connect(self._toggle_detection)
        layout.addWidget(self.detect_btn)

        connect_btn = QPushButton("CONNECT")
        connect_btn.setStyleSheet(
            f"background-color: {BTN_COLOR}; color: {ACCENT_COLOR};"
        )
        connect_btn.clicked.connect(self.connect_stream)
        layout.addWidget(connect_btn)

        self.container_layout.addWidget(frame)

    def _build_video_area(self):
        self.video_container = QFrame()
        self.video_container.setStyleSheet("border: none;")
        self.video_layout = QVBoxLayout(self.video_container)
        self.video_layout.setContentsMargins(20, 10, 20, 20)
        self.container_layout.addWidget(self.video_container, stretch=1)

        self.placeholder = QLabel("WAITING FOR STREAM CONNECTION...")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet(
            f"font-size: 14pt; color: {BTN_COLOR};"
        )
        self.video_layout.addWidget(self.placeholder)

    def _build_watermark(self):
        watermark = QLabel("oT", self.container)
        watermark.setStyleSheet(
            f"color: {BTN_COLOR}; border: none; font-weight: bold; font-size: 9pt;"
        )
        watermark.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.container_layout.addWidget(watermark, alignment=Qt.AlignRight)

    def _register_hotkey(self):
        try:
            import keyboard
            keyboard.add_hotkey("f4", self.toggle_visibility)
        except (ImportError, OSError):
            pass

    def _title_press(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_origin = event.globalPos() - self.frameGeometry().topLeft()

    def _title_move(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self.drag_origin)

    def _title_release(self, _event):
        self.drag_origin = None

    def open_saved_list(self):
        dialog = FramelessDialog("SAVED ADDRESSES", (400, 250), self)

        listbox = QListWidget()
        listbox.addItems(self.history)

        def on_select(item):
            self.url_input.setText(item.text())
            dialog.close()

        listbox.itemClicked.connect(on_select)
        dialog.body_layout.addWidget(listbox)

        self.active_dialog = dialog
        dialog.show()

    def _toggle_detection(self, checked):
        if checked and not self._check_detection_deps():
            self.detect_btn.setChecked(False)
            return
        self.detection_enabled = checked
        self.detect_btn.setText(f"DETECT: {'ON' if checked else 'OFF'}")
        if self.current_player is not None:
            self.connect_stream()

    def _check_detection_deps(self):
        missing = []
        for pkg, install_name in (
            ("cv2", "opencv-python"),
            ("numpy", "numpy"),
            ("ultralytics", "ultralytics"),
        ):
            try:
                __import__(pkg)
            except ImportError:
                missing.append(install_name)
        if missing:
            self._show_missing_deps(missing)
            return False
        return True

    def _show_missing_deps(self, missing):
        dialog = FramelessDialog("MISSING DEPENDENCIES", (420, 180), self)
        text = QLabel(
            "Detection requires:\n  " + "\n  ".join(missing) +
            "\n\nInstall:\n  pip install " + " ".join(missing)
        )
        text.setStyleSheet("border: none;")
        text.setMargin(15)
        dialog.body_layout.addWidget(text)
        dialog.body_layout.addStretch()
        self.active_dialog = dialog
        dialog.show()

    def _clear_player(self):
        if self.placeholder is not None:
            self.placeholder.deleteLater()
            self.placeholder = None
        if self.current_player is not None:
            if isinstance(self.current_player, DetectionPlayer):
                self.current_player.stop()
            self.current_player.deleteLater()
            self.current_player = None

    def connect_stream(self):
        raw_url = self.url_input.text().strip()
        if not raw_url:
            return

        full_url = normalize_url(raw_url)

        self._clear_player()

        if raw_url in self.history:
            self.history.remove(raw_url)
        self.history.insert(0, raw_url)
        del self.history[MAX_HISTORY:]
        self._save_history()

        if self.detection_enabled:
            self.current_player = DetectionPlayer(full_url)
        else:
            self.current_player = StreamPlayer(full_url)
        self.video_layout.addWidget(self.current_player)

    def show_help(self):
        dialog = FramelessDialog("SYSTEM MANUAL", (380, 220), self)

        text = QLabel(
            "1. Enter the exact Stream URL.\n"
            "2. Addresses are saved automatically.\n"
            "3. Click 'SAVED' to pick from history.\n"
            "4. Click 'DETECT' to toggle person detection (YOLOv8n).\n"
            "5. Click 'CONNECT' to load stream.\n"
            "6. Press 'F4' to instantly hide/show."
        )
        text.setStyleSheet("border: none;")
        text.setMargin(15)
        dialog.body_layout.addWidget(text)
        dialog.body_layout.addStretch()

        self.active_dialog = dialog
        dialog.show()

    def toggle_visibility(self):
        if self.is_hidden:
            self.show()
            self.raise_()
            self.activateWindow()
            self.is_hidden = False
        else:
            self.hide()
            self.is_hidden = True

    def closeEvent(self, event):
        self._clear_player()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = IsolatedStreamReceiver()
    window.show()
    sys.exit(app.exec_())
