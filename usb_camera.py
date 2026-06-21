#!/usr/bin/env python3
"""Display the image from a USB-connected camera.

Single self-contained file: command-line viewer, 360 inner-sphere viewer,
and an optional Tkinter GUI.

Requirements:
    pip install opencv-python numpy            # CLI + sphere viewer
    pip install pillow                         # also needed for the GUI

Usage:
    python usb_camera.py                        # graphical interface (default)
    python usb_camera.py --live                 # plain command-line live view
    python usb_camera.py --camera 1             # select another camera
    python usb_camera.py --snapshot photo.jpg   # save a single image to a file
    python usb_camera.py --sphere               # 360 view projected on an inner sphere

Keys while running (live feed):
    q / Esc  - quit
    s        - save a screenshot to a file

Keys/mouse in --sphere mode:
    drag mouse  - look around (rotate the view inside the sphere)
    wheel / +/- - zoom in / out
    arrow keys  - look around with the keyboard
    s           - save a screenshot
    q / Esc     - quit
"""

import argparse
import sys
from datetime import datetime

try:
    import cv2
except ImportError:
    sys.exit(
        "OpenCV is not installed.\n"
        "Install it with: pip install opencv-python"
    )


def open_camera(index: int) -> "cv2.VideoCapture":
    """Open the camera by index and return a VideoCapture object."""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        sys.exit(
            f"Could not open camera at index {index}.\n"
            "Make sure the USB camera is connected and not used by another app."
        )
    return cap


def take_snapshot(cap: "cv2.VideoCapture", path: str) -> None:
    """Capture a single frame and save it to a file."""
    ok, frame = cap.read()
    if not ok:
        sys.exit("Failed to read a frame from the camera.")
    cv2.imwrite(path, frame)
    print(f"Image saved: {path}")


def live_view(cap: "cv2.VideoCapture") -> None:
    """Show a live feed from the camera in a window."""
    window = "USB Camera - press q to quit, s to save"
    print("Showing live feed. Press 'q' or Esc to quit, 's' to save an image.")
    while True:
        ok, frame = cap.read()
        if not ok:
            print("Failed to read a frame from the camera.")
            break

        cv2.imshow(window, frame)
        key = cv2.waitKey(1) & 0xFF

        if key in (ord("q"), 27):  # q or Esc
            break
        if key == ord("s"):
            name = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S.jpg")
            cv2.imwrite(name, frame)
            print(f"Image saved: {name}")


class SphereViewer:
    """Project an equirectangular 360 frame onto the inside of a sphere.

    For each output pixel a viewing ray is cast from the centre of the sphere,
    rotated by the current yaw/pitch, converted to a longitude/latitude and
    sampled from the equirectangular frame. This is equivalent to texturing the
    frame onto the inner surface of a sphere and looking out from its centre.
    """

    def __init__(self, out_w: int = 960, out_h: int = 540):
        import numpy as np  # imported lazily so plain viewing needs no NumPy

        self.np = np
        self.out_w = out_w
        self.out_h = out_h
        self.yaw = 0.0          # radians, left/right
        self.pitch = 0.0        # radians, up/down
        self.fov = np.radians(90.0)
        self.src_size = None    # (w, h) of the source frame the rays were built for
        self.rays = None        # cached camera-space ray directions
        self.dragging = False
        self.last_xy = (0, 0)

    def _build_rays(self) -> None:
        np = self.np
        f = 0.5 * self.out_w / np.tan(0.5 * self.fov)
        xs = np.arange(self.out_w, dtype=np.float32) - 0.5 * self.out_w
        ys = np.arange(self.out_h, dtype=np.float32) - 0.5 * self.out_h
        xv, yv = np.meshgrid(xs, ys)
        x = xv
        y = -yv                      # image y grows downward, world y grows up
        z = np.full_like(xv, f)
        norm = np.sqrt(x * x + y * y + z * z)
        self.rays = np.stack([x / norm, y / norm, z / norm], axis=-1)

    def _maps_for(self, src_w: int, src_h: int):
        np = self.np
        rays = self.rays  # (H, W, 3)

        cp, sp = np.cos(self.pitch), np.sin(self.pitch)
        cy, sy = np.cos(self.yaw), np.sin(self.yaw)
        # Rotate around X (pitch) then around Y (yaw).
        rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], dtype=np.float32)
        ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
        rot = ry @ rx
        world = rays @ rot.T  # (H, W, 3)

        X, Y, Z = world[..., 0], world[..., 1], world[..., 2]
        lon = np.arctan2(X, Z)                       # -pi..pi, 0 = forward
        lat = np.arcsin(np.clip(Y, -1.0, 1.0))       # -pi/2..pi/2, + = up

        map_x = (lon / (2 * np.pi) + 0.5) * src_w
        map_y = (0.5 - lat / np.pi) * src_h
        return map_x.astype(np.float32), map_y.astype(np.float32)

    def render(self, frame):
        h, w = frame.shape[:2]
        if self.rays is None:
            self._build_rays()
        map_x, map_y = self._maps_for(w, h)
        return cv2.remap(
            frame, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_WRAP,
        )

    def on_mouse(self, event, x, y, flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.dragging = True
            self.last_xy = (x, y)
        elif event == cv2.EVENT_LBUTTONUP:
            self.dragging = False
        elif event == cv2.EVENT_MOUSEMOVE and self.dragging:
            dx, dy = x - self.last_xy[0], y - self.last_xy[1]
            self.last_xy = (x, y)
            self.yaw += dx * 0.005
            self.pitch = self._clamp_pitch(self.pitch + dy * 0.005)
        elif event == cv2.EVENT_MOUSEWHEEL:
            self.zoom(-1 if flags > 0 else 1)

    def _clamp_pitch(self, value: float) -> float:
        limit = self.np.radians(89.0)
        return max(-limit, min(limit, value))

    def zoom(self, direction: int) -> None:
        np = self.np
        self.fov = float(np.clip(self.fov + direction * np.radians(5.0),
                                 np.radians(30.0), np.radians(120.0)))
        self.rays = None  # fov changed -> ray grid must be rebuilt


def sphere_view(cap: "cv2.VideoCapture") -> None:
    """Show the camera feed mapped onto the inside of a sphere (360 viewer)."""
    try:
        import numpy  # noqa: F401  (used inside SphereViewer)
    except ImportError:
        sys.exit(
            "The sphere viewer needs NumPy.\n"
            "Install it with: pip install numpy"
        )

    window = "360 Sphere - drag to look, wheel/+/- to zoom, q to quit"
    viewer = SphereViewer()
    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, viewer.on_mouse)
    print(
        "360 sphere view. Drag the mouse to look around, wheel or +/- to zoom, "
        "arrow keys to pan, 's' to save, 'q'/Esc to quit."
    )

    step = 0.05  # radians per arrow-key press
    while True:
        ok, frame = cap.read()
        if not ok:
            print("Failed to read a frame from the camera.")
            break

        view = viewer.render(frame)
        cv2.imshow(window, view)
        key = cv2.waitKey(1) & 0xFF

        if key in (ord("q"), 27):
            break
        elif key in (ord("+"), ord("=")):
            viewer.zoom(-1)
        elif key in (ord("-"), ord("_")):
            viewer.zoom(1)
        elif key == ord("s"):
            name = datetime.now().strftime("sphere_%Y%m%d_%H%M%S.jpg")
            cv2.imwrite(name, view)
            print(f"Image saved: {name}")
        elif key == 81:   # left arrow
            viewer.yaw -= step
        elif key == 83:   # right arrow
            viewer.yaw += step
        elif key == 82:   # up arrow
            viewer.pitch = viewer._clamp_pitch(viewer.pitch + step)
        elif key == 84:   # down arrow
            viewer.pitch = viewer._clamp_pitch(viewer.pitch - step)


# ----------------------------------------------------------------------------
# Graphical interface (Tkinter)
# ----------------------------------------------------------------------------
class CameraGUI:
    """Tkinter application that shows a USB camera feed with optional 360 view."""

    REFRESH_MS = 15  # ~66 fps cap for the update loop

    def __init__(self, root, tk, ttk, messagebox, Image, ImageTk):
        self.root = root
        self.tk = tk
        self.messagebox = messagebox
        self.Image = Image
        self.ImageTk = ImageTk

        self.root.title("USB Camera Viewer")
        self.cap = None
        self.sphere = None  # SphereViewer instance when sphere mode is active

        # --- controls ---
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

        # --- view ---
        self.canvas = tk.Label(self.root, background="black")
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)

        self.status = ttk.Label(self.root, text="Stopped", anchor=tk.W, padding=4)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def start(self) -> None:
        if self.cap is not None:
            return
        index = self.camera_index.get()
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            self.messagebox.showerror(
                "Camera error",
                f"Could not open camera at index {index}.\n"
                "Make sure it is connected and not used by another app.",
            )
            return
        self.cap = cap
        self.sphere = SphereViewer() if self.mode.get() == "sphere" else None
        self.start_btn.configure(state=self.tk.DISABLED)
        self.stop_btn.configure(state=self.tk.NORMAL)
        self.status.configure(text=f"Running (camera {index})")
        self._update_frame()

    def stop(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.start_btn.configure(state=self.tk.NORMAL)
        self.stop_btn.configure(state=self.tk.DISABLED)
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
        self._last_display = display

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        image = self.Image.fromarray(rgb)
        self._photo = self.ImageTk.PhotoImage(image)  # keep a reference
        self.canvas.configure(image=self._photo)

        self.root.after(self.REFRESH_MS, self._update_frame)

    def _on_mode_change(self) -> None:
        sphere_on = self.mode.get() == "sphere"
        state = self.tk.NORMAL if sphere_on else self.tk.DISABLED
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
            self.messagebox.showinfo("Snapshot", "Start the camera first.")
            return
        name = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S.jpg")
        cv2.imwrite(name, display)
        self.status.configure(text=f"Saved {name}")

    def on_close(self) -> None:
        self.stop()
        self.root.destroy()


def run_gui() -> None:
    """Launch the Tkinter GUI."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError:
        sys.exit(
            "Tkinter is not available in this Python installation.\n"
            "On Windows reinstall Python with the 'tcl/tk' option enabled; "
            "on Linux run: sudo apt install python3-tk"
        )

    # Pillow is missing? Tkinter works, so show the error in a popup window
    # (a double-clicked script has no console to read a printed message from).
    try:
        from PIL import Image, ImageTk
    except ImportError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing dependency",
            "Pillow is not installed.\n\nInstall it with:\n    pip install pillow",
        )
        root.destroy()
        return

    root = tk.Tk()
    root.geometry("960x640")
    CameraGUI(root, tk, ttk, messagebox, Image, ImageTk)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Display the image from a USB camera")
    parser.add_argument(
        "--camera", type=int, default=0, help="camera index (default: 0)"
    )
    parser.add_argument(
        "--snapshot",
        metavar="PATH",
        help="capture a single image and save it to a file instead of live feed",
    )
    parser.add_argument(
        "--sphere",
        action="store_true",
        help="project a 360 (equirectangular) feed onto an inner sphere viewer",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="launch the graphical interface (Tkinter) - this is the default",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="plain command-line live view instead of the GUI",
    )
    args = parser.parse_args()

    # The GUI is the default. The command-line modes need an explicit flag so
    # that simply running the file (or double-clicking it) opens the GUI.
    if not (args.snapshot or args.sphere or args.live):
        run_gui()
        return

    cap = open_camera(args.camera)
    try:
        if args.snapshot:
            take_snapshot(cap, args.snapshot)
        elif args.sphere:
            sphere_view(cap)
        else:
            live_view(cap)
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
