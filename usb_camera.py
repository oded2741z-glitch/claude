#!/usr/bin/env python3
"""Display the image from a USB-connected camera.

Requires OpenCV (and NumPy for the sphere viewer):
    pip install opencv-python numpy

Usage:
    python usb_camera.py                        # default camera (index 0)
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
    args = parser.parse_args()

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
