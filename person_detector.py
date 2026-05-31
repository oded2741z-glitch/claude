"""
זיהוי אנשים וציור ריבוע מסביבם באמצעות YOLO (ultralytics).
תומך במקור וידאו (קובץ) או מצלמת רשת.

שימוש:
    python person_detector.py --source 0                # מצלמת רשת
    python person_detector.py --source video.mp4        # קובץ וידאו
    python person_detector.py --source video.mp4 --save out.mp4
"""

import argparse
import sys

import cv2
from ultralytics import YOLO

PERSON_CLASS_ID = 0  # class 'person' ב-COCO


def parse_args():
    parser = argparse.ArgumentParser(description="זיהוי אנשים בווידאו/מצלמה")
    parser.add_argument(
        "--source",
        default="0",
        help="מקור: מספר התקן מצלמה (למשל 0) או נתיב לקובץ וידאו",
    )
    parser.add_argument(
        "--model",
        default="yolov8n.pt",
        help="קובץ משקלות YOLO (יורד אוטומטית בפעם הראשונה)",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.4,
        help="סף ביטחון מינימלי לזיהוי",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="נתיב אופציונלי לשמירת הפלט כווידאו",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="לא להציג חלון תצוגה (שימושי בשרתים ללא GUI)",
    )
    return parser.parse_args()


def open_source(source: str) -> cv2.VideoCapture:
    if source.isdigit():
        cap = cv2.VideoCapture(int(source))
    else:
        cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        sys.exit(f"שגיאה: לא ניתן לפתוח את המקור '{source}'")
    return cap


def main():
    args = parse_args()
    model = YOLO(args.model)
    cap = open_source(args.source)

    writer = None
    if args.save:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(args.save, fourcc, fps, (w, h))

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            results = model.predict(
                frame,
                classes=[PERSON_CLASS_ID],
                conf=args.conf,
                verbose=False,
            )

            count = 0
            for box in results[0].boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                label = f"person {conf:.2f}"
                cv2.putText(
                    frame,
                    label,
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

            if writer is not None:
                writer.write(frame)

            if not args.no_show:
                cv2.imshow("Person Detector", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
