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
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Person Detector (YOLO)")
        self.root.geometry("960x720")

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
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Source:").pack(side=tk.LEFT)
        self.source_var = tk.StringVar(value="0")
        ttk.Entry(top, textvariable=self.source_var, width=40).pack(side=tk.LEFT, padx=4)

        ttk.Button(top, text="Browse Video...", command=self.browse_video).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top, text="Webcam", command=lambda: self.source_var.set("0")).pack(
            side=tk.LEFT, padx=2
        )

        ttk.Label(top, text="Conf:").pack(side=tk.LEFT, padx=(12, 2))
        self.conf_var = tk.DoubleVar(value=0.4)
        ttk.Spinbox(
            top, from_=0.05, to=0.95, increment=0.05,
            textvariable=self.conf_var, width=5,
        ).pack(side=tk.LEFT)

        self.save_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Save output", variable=self.save_var).pack(
            side=tk.LEFT, padx=(12, 2)
        )

        ttk.Label(top, text="Interval (s):").pack(side=tk.LEFT, padx=(12, 2))
        self.interval_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            top, from_=0.0, to=10.0, increment=0.5,
            textvariable=self.interval_var, width=5,
        ).pack(side=tk.LEFT)

        self.start_btn = ttk.Button(top, text="Start", command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=(12, 2))
        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        self.video_label = ttk.Label(self.root, background="black")
        self.video_label.pack(expand=True, fill=tk.BOTH, padx=8, pady=8)

        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Idle. Load a model and start.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)

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
