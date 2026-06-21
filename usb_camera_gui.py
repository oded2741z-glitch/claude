#!/usr/bin/env python3
"""Graphical interface for the USB camera viewer.

A Tkinter GUI to view a USB camera, switch between a normal feed and a
360 inner-sphere view, change the camera index, zoom and save snapshots.

Requirements:
    pip install opencv-python numpy pillow

Run:
    python usb_camera_gui.py
"""

import sys
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:
    sys.exit("Tkinter is not available in this Python installation.")

try:
    import cv2
except ImportError:
    sys.exit("OpenCV is not installed. Install it with: pip install opencv-python")

try:
    from PIL import Image, ImageTk
except ImportError:
    sys.exit("Pillow is not installed. Install it with: pip install pillow")

from usb_camera import SphereViewer


class CameraGUI:
    """Tkinter application that shows a USB camera feed with optional 360 view."""

    REFRESH_MS = 15  # ~66 fps cap for the update loop

    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.root.title("USB Camera Viewer")
        self.cap = None
        self.sphere = None  # SphereViewer instance when sphere mode is active

        self._build_controls()
        self._build_view()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----- UI construction ------------------------------------------------
    def _build_controls(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="Camera:").pack(side=tk.LEFT)
        self.camera_index = tk.IntVar(value=0)
        ttk.Spinbox(
            bar, from_=0, to=10, width=4, textvariable=self.camera_index
        ).pack(side=tk.LEFT, padx=(2, 12))

        self.mode = tk.StringVar(value="normal")
        ttk.Radiobutton(
            bar, text="Normal", value="normal", variable=self.mode,
            command=self._on_mode_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            bar, text="360 Sphere", value="sphere", variable=self.mode,
            command=self._on_mode_change,
        ).pack(side=tk.LEFT, padx=(0, 12))

        self.start_btn = ttk.Button(bar, text="Start", command=self.start)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(
            bar, text="Stop", command=self.stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Button(bar, text="Snapshot", command=self.snapshot).pack(side=tk.LEFT)

        self.zoom_in_btn = ttk.Button(
            bar, text="Zoom +", command=lambda: self._zoom(-1), state=tk.DISABLED
        )
        self.zoom_in_btn.pack(side=tk.LEFT, padx=(12, 2))
        self.zoom_out_btn = ttk.Button(
            bar, text="Zoom -", command=lambda: self._zoom(1), state=tk.DISABLED
        )
        self.zoom_out_btn.pack(side=tk.LEFT)

    def _build_view(self) -> None:
        self.canvas = tk.Label(self.root, background="black")
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)

        self.status = ttk.Label(self.root, text="Stopped", anchor=tk.W, padding=4)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    # ----- camera control -------------------------------------------------
    def start(self) -> None:
        if self.cap is not None:
            return
        index = self.camera_index.get()
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            messagebox.showerror(
                "Camera error",
                f"Could not open camera at index {index}.\n"
                "Make sure it is connected and not used by another app.",
            )
            return
        self.cap = cap
        self.sphere = SphereViewer() if self.mode.get() == "sphere" else None
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status.configure(text=f"Running (camera {index})")
        self._update_frame()

    def stop(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.status.configure(text="Stopped")

    def _update_frame(self) -> None:
        if self.cap is None:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.status.configure(text="Failed to read from camera")
            self.stop()
            return

        display = self.sphere.render(frame) if self.sphere is not None else frame
        self._last_raw = frame
        self._last_display = display

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(image)  # keep a reference
        self.canvas.configure(image=self._photo)

        self.root.after(self.REFRESH_MS, self._update_frame)

    # ----- mode / zoom / interaction -------------------------------------
    def _on_mode_change(self) -> None:
        sphere_on = self.mode.get() == "sphere"
        state = tk.NORMAL if sphere_on else tk.DISABLED
        self.zoom_in_btn.configure(state=state)
        self.zoom_out_btn.configure(state=state)
        if self.cap is not None:
            self.sphere = SphereViewer() if sphere_on else None

    def _zoom(self, direction: int) -> None:
        if self.sphere is not None:
            self.sphere.zoom(direction)

    def _on_drag_start(self, event) -> None:
        if self.sphere is not None:
            self.sphere.dragging = True
            self.sphere.last_xy = (event.x, event.y)

    def _on_drag_move(self, event) -> None:
        if self.sphere is None:
            return
        dx = event.x - self.sphere.last_xy[0]
        dy = event.y - self.sphere.last_xy[1]
        self.sphere.last_xy = (event.x, event.y)
        self.sphere.yaw += dx * 0.005
        self.sphere.pitch = self.sphere._clamp_pitch(self.sphere.pitch + dy * 0.005)

    def snapshot(self) -> None:
        display = getattr(self, "_last_display", None)
        if display is None:
            messagebox.showinfo("Snapshot", "Start the camera first.")
            return
        name = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S.jpg")
        cv2.imwrite(name, display)
        self.status.configure(text=f"Saved {name}")

    def on_close(self) -> None:
        self.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.geometry("960x640")
    CameraGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
