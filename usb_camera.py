#!/usr/bin/env python3
"""תוכנה להצגת תמונה ממצלמת USB.

דורש את הספרייה OpenCV:
    pip install opencv-python

הפעלה:
    python usb_camera.py            # מצלמה ברירת מחדל (אינדקס 0)
    python usb_camera.py --camera 1 # בחירת מצלמה אחרת
    python usb_camera.py --snapshot photo.jpg  # שמירת תמונה בודדת לקובץ

מקשים בזמן ריצה:
    q / Esc  - יציאה
    s        - שמירת צילום מסך לקובץ
"""

import argparse
import sys
from datetime import datetime

try:
    import cv2
except ImportError:
    sys.exit(
        "הספרייה OpenCV אינה מותקנת.\n"
        "התקן אותה באמצעות: pip install opencv-python"
    )


def open_camera(index: int) -> "cv2.VideoCapture":
    """פותח את המצלמה לפי האינדקס ומחזיר אובייקט VideoCapture."""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        sys.exit(
            f"לא ניתן לפתוח את המצלמה באינדקס {index}.\n"
            "ודא שהמצלמה מחוברת ב-USB ושאף תוכנה אחרת אינה משתמשת בה."
        )
    return cap


def take_snapshot(cap: "cv2.VideoCapture", path: str) -> None:
    """מצלם תמונה בודדת ושומר אותה לקובץ."""
    ok, frame = cap.read()
    if not ok:
        sys.exit("שגיאה בקריאת תמונה מהמצלמה.")
    cv2.imwrite(path, frame)
    print(f"התמונה נשמרה: {path}")


def live_view(cap: "cv2.VideoCapture") -> None:
    """מציג שידור חי מהמצלמה בחלון."""
    window = "מצלמת USB - לחץ q ליציאה, s לשמירה"
    print("מציג שידור חי. לחץ 'q' או Esc ליציאה, 's' לשמירת תמונה.")
    while True:
        ok, frame = cap.read()
        if not ok:
            print("שגיאה בקריאת תמונה מהמצלמה.")
            break

        cv2.imshow(window, frame)
        key = cv2.waitKey(1) & 0xFF

        if key in (ord("q"), 27):  # q או Esc
            break
        if key == ord("s"):
            name = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S.jpg")
            cv2.imwrite(name, frame)
            print(f"התמונה נשמרה: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="הצגת תמונה ממצלמת USB")
    parser.add_argument(
        "--camera", type=int, default=0, help="אינדקס המצלמה (ברירת מחדל: 0)"
    )
    parser.add_argument(
        "--snapshot",
        metavar="PATH",
        help="צילום תמונה בודדת ושמירתה לקובץ במקום שידור חי",
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
