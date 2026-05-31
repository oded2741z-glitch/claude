"""
Person Detector with GUI using Claude API (Vision + Structured Outputs).

Uses claude-opus-4-8 to detect people and return bounding boxes, then draws
rectangles on the image. Supports image files or single-frame capture from
video files / webcam (LLM inference is too slow for real-time video).

Run:
    export ANTHROPIC_API_KEY=...
    python person_detector_llm.py
"""

import base64
import io
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List

import anthropic
import cv2
from PIL import Image, ImageTk
from pydantic import BaseModel, Field

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are a computer vision assistant that detects people in images.

For each person clearly visible in the image, return a bounding box in normalized
coordinates (0.0 to 1.0) where (0,0) is the top-left of the image and (1,1) is
the bottom-right.

Rules:
- Only include humans (not statues, posters, reflections, or drawings).
- A box must tightly enclose the visible body parts of one person.
- If two people overlap heavily, return separate boxes for each.
- Confidence is your subjective certainty that this is a person (0.0 to 1.0).
- If no people are visible, return an empty list."""


class PersonBox(BaseModel):
    x1: float = Field(description="Left edge, normalized 0..1")
    y1: float = Field(description="Top edge, normalized 0..1")
    x2: float = Field(description="Right edge, normalized 0..1")
    y2: float = Field(description="Bottom edge, normalized 0..1")
    confidence: float = Field(description="Detection confidence, 0..1")


class DetectionResult(BaseModel):
    people: List[PersonBox]


class PersonDetectorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Person Detector (Claude API)")
        self.root.geometry("1000x780")

        self.client = anthropic.Anthropic()
        self.current_frame_bgr = None
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
            value=f"Ready. Model: {MODEL}. Load an image or capture a frame to begin."
        )
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)

    def open_image(self):
        path = filedialog.askopenfilename(
            title="Select image",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        img = cv2.imread(path)
        if img is None:
            messagebox.showerror("Error", f"Cannot read image: {path}")
            return
        self._set_frame(img)
        self.status_var.set(f"Loaded image: {path}")

    def open_video(self):
        path = filedialog.askopenfilename(
            title="Select video (a frame will be captured)",
            filetypes=[
                ("Video", "*.mp4 *.avi *.mov *.mkv *.webm"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            messagebox.showerror("Error", f"Cannot open video: {path}")
            return
        ok, frame = cap.read()
        cap.release()
        if not ok:
            messagebox.showerror("Error", "Cannot read frame from video.")
            return
        self._set_frame(frame)
        self.status_var.set(f"Captured first frame from: {path}")

    def webcam_snapshot(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            messagebox.showerror("Error", "Cannot open webcam.")
            return
        for _ in range(5):
            cap.read()
        ok, frame = cap.read()
        cap.release()
        if not ok:
            messagebox.showerror("Error", "Cannot capture frame from webcam.")
            return
        self._set_frame(frame)
        self.status_var.set("Captured webcam snapshot.")

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
        self.status_var.set("Sending image to Claude... (this can take 10-30s)")
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self):
        try:
            rgb = cv2.cvtColor(self.current_frame_bgr, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=90)
            b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

            response = self.client.messages.parse(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": "Detect all people in this image and return their bounding boxes.",
                            },
                        ],
                    }
                ],
                output_format=DetectionResult,
            )

            result: DetectionResult = response.parsed_output
            usage = response.usage
            self.root.after(0, self._draw_results, result, usage)
        except anthropic.APIError as e:
            self.root.after(0, self._on_error, f"API error: {e}")
        except Exception as e:
            self.root.after(0, self._on_error, f"Error: {e}")

    def _draw_results(self, result: DetectionResult, usage):
        frame = self.current_frame_bgr.copy()
        h, w = frame.shape[:2]
        for box in result.people:
            x1 = int(box.x1 * w)
            y1 = int(box.y1 * h)
            x2 = int(box.x2 * w)
            y2 = int(box.y2 * h)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"person {box.confidence:.2f}"
            cv2.putText(
                frame,
                label,
                (x1, max(y1 - 8, 15)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
        cv2.putText(
            frame,
            f"People: {len(result.people)}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
        )
        self.annotated_frame = frame
        self._show(frame)
        self.status_var.set(
            f"Detected {len(result.people)} people. "
            f"Tokens: in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_read={usage.cache_read_input_tokens}"
        )
        self.detecting = False
        self.detect_btn.config(state=tk.NORMAL)

    def _on_error(self, msg):
        messagebox.showerror("Detection failed", msg)
        self.status_var.set(msg)
        self.detecting = False
        self.detect_btn.config(state=tk.NORMAL)

    def save_output(self):
        if not hasattr(self, "annotated_frame"):
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
