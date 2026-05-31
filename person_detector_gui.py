"""
Person Detector with GUI (Tkinter) using YOLO (ultralytics).

Run:
    python person_detector_gui.py
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk
from ultralytics import YOLO

PERSON_CLASS_ID = 0  # 'person' in COCO


class PersonDetectorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Person Detector (YOLO)")
        self.root.geometry("960x720")

        self.model = None
        self.cap = None
        self.running = False
        self.worker = None
        self.writer = None
        self.save_path = None

        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Source:").pack(side=tk.LEFT)
        self.source_var = tk.StringVar(value="0")
        self.source_entry = ttk.Entry(top, textvariable=self.source_var, width=40)
        self.source_entry.pack(side=tk.LEFT, padx=4)

        ttk.Button(top, text="Browse Video...", command=self.browse_video).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top, text="Webcam", command=lambda: self.source_var.set("0")).pack(
            side=tk.LEFT, padx=2
        )

        ttk.Label(top, text="Conf:").pack(side=tk.LEFT, padx=(12, 2))
        self.conf_var = tk.DoubleVar(value=0.4)
        ttk.Spinbox(
            top,
            from_=0.05,
            to=0.95,
            increment=0.05,
            textvariable=self.conf_var,
            width=5,
        ).pack(side=tk.LEFT)

        self.save_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="Save output", variable=self.save_var
        ).pack(side=tk.LEFT, padx=(12, 2))

        ttk.Label(top, text="Interval (s):").pack(side=tk.LEFT, padx=(12, 2))
        self.interval_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(
            top,
            from_=0.0,
            to=10.0,
            increment=0.5,
            textvariable=self.interval_var,
            width=5,
        ).pack(side=tk.LEFT)

        self.start_btn = ttk.Button(top, text="Start", command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=(12, 2))
        self.stop_btn = ttk.Button(
            top, text="Stop", command=self.stop, state=tk.DISABLED
        )
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
            filetypes=[
                ("Video files", "*.mp4 *.avi *.mov *.mkv *.webm"),
                ("All files", "*.*"),
            ],
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

        cap = cv2.VideoCapture(int(source)) if source.isdigit() else cv2.VideoCapture(source)
        if not cap.isOpened():
            messagebox.showerror("Error", f"Cannot open source: {source}")
            return
        self.cap = cap

        if self.save_var.get():
            path = filedialog.asksaveasfilename(
                title="Save output as",
                defaultextension=".mp4",
                filetypes=[("MP4", "*.mp4")],
            )
            if path:
                fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self.writer = cv2.VideoWriter(path, fourcc, fps, (w, h))
                self.save_path = path

        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("Running...")
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    def _loop(self):
        import time
        conf = float(self.conf_var.get())
        last_detect_time = 0.0
        last_boxes = []
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                break

            interval = float(self.interval_var.get())
            now = time.monotonic()
            if interval <= 0 or (now - last_detect_time) >= interval:
                results = self.model.predict(
                    frame, classes=[PERSON_CLASS_ID], conf=conf, verbose=False
                )
                last_boxes = [
                    (list(map(int, b.xyxy[0].tolist())), float(b.conf[0]))
                    for b in results[0].boxes
                ]
                last_detect_time = now

            count = 0
            for (x1, y1, x2, y2), c in last_boxes:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(
                    frame,
                    f"person {c:.2f}",
                    (x1, max(y1 - 8, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )
                count += 1
            cv2.putText(
                frame,
                f"People: {count}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
            )
            if self.writer is not None:
                self.writer.write(frame)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self.root.after(0, self._update_frame, rgb, count)
        self.root.after(0, self._on_stream_end)

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
