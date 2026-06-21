#!/usr/bin/env python3
"""Display the image from a USB-connected camera.

Requires OpenCV:
    pip install opencv-python

Usage:
    python usb_camera.py                        # default camera (index 0)
    python usb_camera.py --camera 1             # select another camera
    python usb_camera.py --snapshot photo.jpg   # save a single image to a file

Keys while running:
    q / Esc  - quit
    s        - save a screenshot to a file
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
    args = parser.parse_args()

    cap = open_camera(args.camera)
    try:
        if args.snapshot:
            take_snapshot(cap, args.snapshot)
        else:
            live_view(cap)
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
