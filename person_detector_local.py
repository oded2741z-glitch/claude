"""
Person Detector with GUI using a LOCAL VLM via Ollama.

Setup (one-time):
    1. Install Ollama: https://ollama.com/download
    2. Pull a vision model:
         ollama pull qwen2.5vl:7b      # ~6GB, best at bounding boxes
         # or smaller: ollama pull llava:7b
    3. Ollama must be running (it auto-starts on Windows after install).

Run:
    pip install ollama opencv-python Pillow pydantic
    python person_detector_local.py
"""

import base64
import io
import json
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import ollama
from PIL import Image, ImageTk

MODEL = "qwen2.5vl:7b"

PROMPT = """Detect every person in this image.

Return ONLY a JSON array (no prose, no markdown) of bounding boxes in this exact format:
[{"x1": 0.0, "y1": 0.0, "x2": 0.0, "y2": 0.0, "confidence": 0.0}, ...]

Coordinates are normalized: 0.0 = top/left, 1.0 = bottom/right of the image.
Each box must tightly enclose ONE person. Only real humans (not statues/posters/drawings).
If no people are visible, return [].
"""


class PersonDetectorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"Person Detector (Local VLM: {MODEL})")
        self.root.geometry("1000x780")
        self.current_frame_bgr = None
        self.annotated_frame = None
        self.detecting = False
        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="Open Image...", command=self.open_image).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top, text="Open Video / Frame...", command=self.open_video).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top, text="Webcam Snapshot", command=self.webcam_snapshot).pack(
            side=tk.LEFT, padx=2
        )

        self.detect_btn = ttk.Button(
            top, text="Detect People", command=self.detect, state=tk.DISABLED
        )
        self.detect_btn.pack(side=tk.LEFT, padx=(16, 2))
        ttk.Button(top, text="Save Output...", command=self.save_output).pack(
            side=tk.LEFT, padx=2
        )

        self.image_label = ttk.Label(self.root, background="black")
        self.image_label.pack(expand=True, fill=tk.BOTH, padx=8, pady=8)

        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(
            value=f"Ready. Local model: {MODEL}. Make sure Ollama is running."
        )
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)

    def open_image(self):
        path = filedialog.askopenfilename(
            title="Select image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All", "*.*")],
        )
        if not path:
            return
        img = cv2.imread(path)
        if img is None:
            messagebox.showerror("Error", f"Cannot read image: {path}")
            return
        self._set_frame(img)
        self.status_var.set(f"Loaded: {path}")

    def open_video(self):
        path = filedialog.askopenfilename(
            title="Select video (first frame will be captured)",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv *.webm"), ("All", "*.*")],
        )
        if not path:
            return
        cap = cv2.VideoCapture(path)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            messagebox.showerror("Error", "Cannot read frame.")
            return
        self._set_frame(frame)
        self.status_var.set(f"Captured frame from: {path}")

    def webcam_snapshot(self):
        cap = cv2.VideoCapture(0)
        for _ in range(5):
            cap.read()
        ok, frame = cap.read()
        cap.release()
        if not ok:
            messagebox.showerror("Error", "Cannot capture webcam.")
            return
        self._set_frame(frame)
        self.status_var.set("Webcam snapshot captured.")

    def _set_frame(self, frame_bgr):
        self.current_frame_bgr = frame_bgr
        self._show(frame_bgr)
        self.detect_btn.config(state=tk.NORMAL)

    def _show(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        lw = max(self.image_label.winfo_width(), 800)
        lh = max(self.image_label.winfo_height(), 600)
        img.thumbnail((lw, lh))
        photo = ImageTk.PhotoImage(img)
        self.image_label.configure(image=photo)
        self.image_label.image = photo

    def detect(self):
        if self.current_frame_bgr is None or self.detecting:
            return
        self.detecting = True
        self.detect_btn.config(state=tk.DISABLED)
        self.status_var.set("Running local VLM... (can take 30-120s depending on hardware)")
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self):
        try:
            rgb = cv2.cvtColor(self.current_frame_bgr, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=90)
            img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=MODEL,
                messages=[
                    {"role": "user", "content": PROMPT, "images": [img_b64]}
                ],
                options={"temperature": 0.0},
            )
            text = response["message"]["content"]
            boxes = self._parse_boxes(text)
            self.root.after(0, self._draw_results, boxes, text)
        except Exception as e:
            self.root.after(0, self._on_error, f"Error: {e}")

    @staticmethod
    def _parse_boxes(text):
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        boxes = []
        for d in data:
            try:
                boxes.append(
                    {
                        "x1": float(d["x1"]),
                        "y1": float(d["y1"]),
                        "x2": float(d["x2"]),
                        "y2": float(d["y2"]),
                        "confidence": float(d.get("confidence", 1.0)),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return boxes

    def _draw_results(self, boxes, raw_text):
        frame = self.current_frame_bgr.copy()
        h, w = frame.shape[:2]
        for b in boxes:
            scale = 1.0 if max(b["x2"], b["y2"]) <= 1.5 else 1 / 1000.0
            x1 = int(b["x1"] * w * scale)
            y1 = int(b["y1"] * h * scale)
            x2 = int(b["x2"] * w * scale)
            y2 = int(b["y2"] * h * scale)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                frame,
                f"person {b['confidence']:.2f}",
                (x1, max(y1 - 8, 15)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
        cv2.putText(
            frame,
            f"People: {len(boxes)}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
        )
        self.annotated_frame = frame
        self._show(frame)
        if not boxes:
            self.status_var.set(
                f"No people detected. Raw model output: {raw_text[:120]}"
            )
        else:
            self.status_var.set(f"Detected {len(boxes)} people.")
        self.detecting = False
        self.detect_btn.config(state=tk.NORMAL)

    def _on_error(self, msg):
        messagebox.showerror("Detection failed", msg)
        self.status_var.set(msg)
        self.detecting = False
        self.detect_btn.config(state=tk.NORMAL)

    def save_output(self):
        if self.annotated_frame is None:
            messagebox.showinfo("Nothing to save", "Run detection first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")],
        )
        if path:
            cv2.imwrite(path, self.annotated_frame)
            self.status_var.set(f"Saved: {path}")


def main():
    root = tk.Tk()
    PersonDetectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
