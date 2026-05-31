"""
Person Detector with GUI (Tkinter) using YOLO (ultralytics).

CPU optimizations:
  1. Inference frame downscaled before YOLO.
  2. Display FPS capped (~15) + video files paced to native FPS.
  3. yolov8n.pt (smallest YOLO).
  4. GUI updates skipped when nothing changed.
  5. Pacing for video file sources.
  6. YOLO imgsz=320 (vs default 640) -> ~4x less compute.

Run:
    python person_detector_gui.py
"""

import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk
from ultralytics import YOLO

PERSON_CLASS_ID = 0  # 'person' in COCO
INFER_IMGSZ = 320     # YOLO inference size; lower = faster
INFER_MAX_DIM = 640   # downscale frame before sending to YOLO if larger
DISPLAY_FPS = 15      # cap UI refresh rate


class PersonDetectorApp:
    BG = "#1e1e2e"
    PANEL = "#282838"
    FG = "#cdd6f4"
    MUTED = "#9399b2"
    ACCENT = "#89b4fa"
    SUCCESS = "#a6e3a1"
    DANGER = "#f38ba8"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Person Detector")
        self.root.geometry("1100x780")
        self.root.configure(bg=self.BG)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=self.BG, foreground=self.FG, font=("Segoe UI", 10))
        style.configure("TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BG, foreground=self.FG)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=self.BG, foreground=self.ACCENT, font=("Segoe UI Semibold", 14))
        style.configure("TButton",
            background=self.PANEL, foreground=self.FG,
            borderwidth=0, focusthickness=0, padding=(12, 6), font=("Segoe UI", 10))
        style.map("TButton",
            background=[("active", self.ACCENT), ("disabled", "#3a3a4a")],
            foreground=[("active", self.BG), ("disabled", self.MUTED)])
        style.configure("Accent.TButton",
            background=self.ACCENT, foreground=self.BG,
            borderwidth=0, focusthickness=0, padding=(14, 6),
            font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton",
            background=[("active", "#74a8f0"), ("disabled", "#3a3a4a")],
            foreground=[("disabled", self.MUTED)])
        style.configure("Danger.TButton",
            background=self.DANGER, foreground=self.BG,
            borderwidth=0, focusthickness=0, padding=(14, 6),
            font=("Segoe UI Semibold", 10))
        style.map("Danger.TButton",
            background=[("active", "#e07896"), ("disabled", "#3a3a4a")])
        style.configure("TEntry",
            fieldbackground=self.PANEL, foreground=self.FG,
            insertcolor=self.FG, borderwidth=0, padding=6)
        style.configure("TSpinbox",
            fieldbackground=self.PANEL, foreground=self.FG,
            arrowcolor=self.ACCENT, borderwidth=0, padding=4)
        style.configure("TCheckbutton", background=self.BG, foreground=self.FG)
        style.map("TCheckbutton", background=[("active", self.BG)])

        self.model = None
        self.cap = None
        self.running = False
        self.worker = None
        self.detector = None
        self.writer = None
        self.save_path = None

        self.source_fps = 0.0
        self.is_file_source = False

        self._latest_frame = None
        self._latest_lock = threading.Lock()
        self._last_boxes = []
        self._boxes_lock = threading.Lock()

        self._last_display_time = 0.0
        self._last_box_signature = None

        self._build_ui()

    def _build_ui(self):
        header = ttk.Frame(self.root, padding=(20, 16, 20, 8))
        header.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(header, text="Person Detector", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="YOLOv8n  ·  local  ·  real-time",
                  style="Muted.TLabel").pack(side=tk.LEFT, padx=12)

        controls = ttk.Frame(self.root, padding=(20, 8, 20, 8))
        controls.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(controls, text="Source").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.source_var = tk.StringVar(value="0")
        ttk.Entry(controls, textvariable=self.source_var, width=42).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(controls, text="Browse", command=self.browse_video).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="Webcam", command=lambda: self.source_var.set("0")).grid(row=0, column=3, padx=2)

        ttk.Label(controls, text="Confidence").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(10, 0))
        self.conf_var = tk.DoubleVar(value=0.4)
        ttk.Spinbox(controls, from_=0.05, to=0.95, increment=0.05,
                    textvariable=self.conf_var, width=6).grid(row=1, column=1, sticky="w", pady=(10, 0))

        ttk.Label(controls, text="Interval (s)").grid(row=1, column=2, sticky="e", padx=(20, 6), pady=(10, 0))
        self.interval_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(controls, from_=0.0, to=10.0, increment=0.5,
                    textvariable=self.interval_var, width=6).grid(row=1, column=3, sticky="w", pady=(10, 0))

        self.save_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Save output", variable=self.save_var).grid(
            row=1, column=4, padx=(20, 0), pady=(10, 0), sticky="w"
        )

        actions = ttk.Frame(self.root, padding=(20, 4, 20, 12))
        actions.pack(side=tk.TOP, fill=tk.X)
        self.start_btn = ttk.Button(actions, text="▶  Start", command=self.start, style="Accent.TButton")
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(actions, text="■  Stop", command=self.stop,
                                   style="Danger.TButton", state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=8)

        video_wrap = tk.Frame(self.root, bg=self.PANEL, highlightthickness=0)
        video_wrap.pack(expand=True, fill=tk.BOTH, padx=20, pady=8)
        self.video_label = tk.Label(video_wrap, bg=self.PANEL)
        self.video_label.pack(expand=True, fill=tk.BOTH, padx=2, pady=2)

        bottom = ttk.Frame(self.root, padding=(20, 8, 20, 12))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Idle.  Pick a source and press Start.")
        ttk.Label(bottom, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.LEFT)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def browse_video(self):
        path = filedialog.askopenfilename(
            title="Select video file",
            filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.webm"), ("All", "*.*")],
        )
        if path:
            self.source_var.set(path)

    def _ensure_model(self):
        if self.model is None:
            self.status_var.set("Loading YOLO model...")
            self.root.update_idletasks()
            self.model = YOLO("yolov8n.pt")

    def start(self):
        if self.running:
            return
        source = self.source_var.get().strip()
        if not source:
            messagebox.showerror("Error", "Please provide a source.")
            return
        try:
            self._ensure_model()
        except Exception as e:
            messagebox.showerror("Model error", str(e))
            return

        self.is_file_source = not source.isdigit()
        cap = cv2.VideoCapture(int(source)) if not self.is_file_source else cv2.VideoCapture(source)
        if not cap.isOpened():
            messagebox.showerror("Error", f"Cannot open source: {source}")
            return
        self.cap = cap
        self.source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

        if self.save_var.get():
            path = filedialog.asksaveasfilename(
                title="Save output as",
                defaultextension=".mp4",
                filetypes=[("MP4", "*.mp4")],
            )
            if path:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self.writer = cv2.VideoWriter(path, fourcc, self.source_fps, (w, h))
                self.save_path = path

        self.running = True
        self._last_boxes = []
        self._latest_frame = None
        self._last_box_signature = None
        self._last_display_time = 0.0

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("Running...")
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()
        self.detector = threading.Thread(target=self._detector_thread, daemon=True)
        self.detector.start()

    def _detector_thread(self):
        conf = float(self.conf_var.get())
        last_detect_time = 0.0
        while self.running:
            interval = float(self.interval_var.get())
            now = time.monotonic()
            if (now - last_detect_time) < max(interval, 0.0):
                time.sleep(0.02)
                continue
            with self._latest_lock:
                frame = None if self._latest_frame is None else self._latest_frame.copy()
            if frame is None:
                time.sleep(0.02)
                continue

            # (1) Downscale before YOLO to reduce compute.
            h, w = frame.shape[:2]
            scale = min(1.0, INFER_MAX_DIM / max(h, w))
            if scale < 1.0:
                small = cv2.resize(frame, (int(w * scale), int(h * scale)))
            else:
                small = frame

            # (6) imgsz=320 cuts compute ~4x vs default 640.
            results = self.model.predict(
                small,
                classes=[PERSON_CLASS_ID],
                conf=conf,
                imgsz=INFER_IMGSZ,
                verbose=False,
            )
            inv = 1.0 / scale if scale < 1.0 else 1.0
            boxes = []
            for b in results[0].boxes:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                boxes.append((
                    [int(x1 * inv), int(y1 * inv), int(x2 * inv), int(y2 * inv)],
                    float(b.conf[0]),
                ))
            with self._boxes_lock:
                self._last_boxes = boxes
            last_detect_time = now

    def _loop(self):
        display_period = 1.0 / DISPLAY_FPS
        frame_period = 1.0 / self.source_fps if self.is_file_source else 0.0
        next_frame_time = time.monotonic()

        while self.running:
            # (5) Pace video file reads to native FPS so playback isn't fast-forwarded.
            if frame_period > 0:
                sleep_for = next_frame_time - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                next_frame_time += frame_period

            ok, frame = self.cap.read()
            if not ok:
                break

            with self._latest_lock:
                self._latest_frame = frame
            with self._boxes_lock:
                boxes = list(self._last_boxes)

            # Always write the source-FPS frame to disk if saving.
            if self.writer is not None:
                annotated = frame.copy()
                self._draw(annotated, boxes)
                self.writer.write(annotated)

            # (2)+(4) Throttle GUI updates and skip if nothing changed.
            now = time.monotonic()
            box_sig = tuple((tuple(b[0]) for b in boxes))
            if (now - self._last_display_time) >= display_period or box_sig != self._last_box_signature:
                disp = frame.copy() if self.writer is None else annotated
                if self.writer is None:
                    self._draw(disp, boxes)
                rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
                self.root.after(0, self._update_frame, rgb, len(boxes))
                self._last_display_time = now
                self._last_box_signature = box_sig

        self.root.after(0, self._on_stream_end)

    @staticmethod
    def _draw(frame, boxes):
        for (x1, y1, x2, y2), c in boxes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                frame, f"person {c:.2f}", (x1, max(y1 - 8, 15)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
            )
        cv2.putText(
            frame, f"People: {len(boxes)}", (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2,
        )

    def _update_frame(self, rgb, count):
        lw = max(self.video_label.winfo_width(), 1)
        lh = max(self.video_label.winfo_height(), 1)
        img = Image.fromarray(rgb)
        img.thumbnail((lw, lh))
        photo = ImageTk.PhotoImage(img)
        self.video_label.configure(image=photo)
        self.video_label.image = photo
        self.status_var.set(f"Running - people detected: {count}")

    def _on_stream_end(self):
        if self.running:
            self.stop()

    def stop(self):
        self.running = False
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.writer is not None:
            self.writer.release()
            msg = f"Saved to {self.save_path}" if self.save_path else "Stopped."
            self.writer = None
            self.save_path = None
            self.status_var.set(msg)
        else:
            self.status_var.set("Stopped.")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

    def on_close(self):
        self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    PersonDetectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
