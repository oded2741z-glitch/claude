"""אימון מודל Instance Segmentation עם Ultralytics YOLO.

דוגמת הרצה מהירה (על דאטהסט מובנה זעיר, יורד אוטומטית):
    python train.py

אימון על הדאטה שלך:
    python train.py --data path/to/data.yaml --model yolo11n-seg.pt --epochs 100

המודל שמתקבל יישמר תחת runs/segment/<name>/weights/best.pt
"""

import argparse

from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(description="Train a YOLO instance-segmentation model")
    parser.add_argument(
        "--model",
        default="yolo11n-seg.pt",
        help="מודל בסיס מאומן מראש. למשל: yolo11n-seg.pt / yolo11s-seg.pt / yolov8n-seg.pt",
    )
    parser.add_argument(
        "--data",
        default="coco8-seg.yaml",
        help="קובץ הגדרת דאטהסט (YAML). ברירת מחדל: coco8-seg.yaml (דאטהסט הדגמה זעיר)",
    )
    parser.add_argument("--epochs", type=int, default=50, help="מספר אפוקים")
    parser.add_argument("--imgsz", type=int, default=640, help="גודל תמונה לאימון")
    parser.add_argument("--batch", type=int, default=16, help="גודל באטץ' (-1 = אוטומטי)")
    parser.add_argument(
        "--device",
        default=None,
        help="מכשיר: 0 ל-GPU בודד, '0,1' לכמה, 'cpu' למעבד. ברירת מחדל: אוטומטי",
    )
    parser.add_argument("--name", default="seg_train", help="שם תיקיית הריצה")
    return parser.parse_args()


def main():
    args = parse_args()

    model = YOLO(args.model)

    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        name=args.name,
    )

    print("\nהאימון הסתיים.")
    print(f"המשקלים הטובים ביותר: {model.trainer.best}")
    return results


if __name__ == "__main__":
    main()
