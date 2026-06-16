# Instance Segmentation עם YOLO

צינור אימון והרצה ל-**instance segmentation** (סגמנטציה ברמת המופע) מבוסס
[Ultralytics YOLO](https://docs.ultralytics.com/). עובד עם `yolo11*-seg`
וגם עם `yolov8*-seg` — בדיוק אותו API.

## התקנה

```bash
pip install -r requirements.txt
```

## אימון מהיר (לבדיקה)

מאמן על דאטהסט הדגמה זעיר (`coco8-seg`) שיורד אוטומטית — נועד לוודא שהכול עובד:

```bash
python train.py
```

## אימון על הדאטה שלך

1. ארגן את הדאטה במבנה הבא:

   ```
   dataset/
     images/train/  *.jpg
     images/val/    *.jpg
     labels/train/  *.txt   # פורמט YOLO-seg (פוליגונים מנורמלים)
     labels/val/    *.txt
   ```

2. ערוך את `data_custom.yaml` עם הנתיבים ושמות המחלקות שלך.

3. הרץ:

   ```bash
   python train.py --data data_custom.yaml --model yolo11n-seg.pt --epochs 100 --imgsz 640
   ```

   גדלי מודל זמינים (קטן→גדול): `n`, `s`, `m`, `l`, `x`
   (למשל `yolo11s-seg.pt`). גדול יותר = מדויק יותר אך איטי יותר.

המודל הטוב ביותר יישמר ב-`runs/segment/<name>/weights/best.pt`.

## הרצת חיזוי (inference)

```bash
python predict.py --model runs/segment/seg_train/weights/best.pt --source image.jpg
```

המקור יכול להיות תמונה, תיקייה, וידאו או URL. התוצאות עם המסכות
נשמרות תחת `runs/segment/predict/`.

## קבצים

| קובץ | תיאור |
|------|-------|
| `train.py` | אימון מודל segmentation |
| `predict.py` | הרצת חיזוי על תמונות/וידאו |
| `data_custom.yaml` | תבנית הגדרת דאטהסט לאימון משלך |
| `requirements.txt` | תלויות |
