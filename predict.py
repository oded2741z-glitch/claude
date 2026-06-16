"""הרצת inference (חיזוי) עם מודל Instance Segmentation מאומן.

דוגמה:
    python predict.py --model runs/segment/seg_train/weights/best.pt --source image.jpg

אפשר לתת כמקור: תמונה בודדת, תיקיית תמונות, וידאו, או כתובת URL.
התוצאות נשמרות תחת runs/segment/predict/ עם המסכות מצוירות.
"""

import argparse

from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(description="Run YOLO instance-segmentation inference")
    parser.add_argument(
        "--model",
        default="yolo11n-seg.pt",
        help="נתיב למודל מאומן (.pt). ברירת מחדל: yolo11n-seg.pt המאומן על COCO",
    )
    parser.add_argument(
        "--source",
        default="https://ultralytics.com/images/bus.jpg",
        help="תמונה / תיקייה / וידאו / URL להרצה",
    )
    parser.add_argument("--conf", type=float, default=0.25, help="סף ביטחון מינימלי")
    parser.add_argument("--imgsz", type=int, default=640, help="גודל תמונה ל-inference")
    parser.add_argument("--device", default=None, help="0 ל-GPU, 'cpu' למעבד")
    parser.add_argument("--save", action="store_true", default=True, help="לשמור תמונות עם המסכות")
    return parser.parse_args()


def main():
    args = parse_args()

    model = YOLO(args.model)

    results = model.predict(
        source=args.source,
        conf=args.conf,
        imgsz=args.imgsz,
        device=args.device,
        save=args.save,
    )

    for r in results:
        n = 0 if r.masks is None else len(r.masks)
        print(f"{r.path}: זוהו {n} מופעים")

    print("\nהתוצאות נשמרו תחת runs/segment/predict/")
    return results


if __name__ == "__main__":
    main()
