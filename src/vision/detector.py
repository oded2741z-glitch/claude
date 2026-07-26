"""זיהוי ומעקב אחרי אדם.

מחזיר תיבה מנורמלת (ערכים ב-[0,1]) כדי שהבקרה תהיה בלתי-תלויה ברזולוציה.

שני מנועי זיהוי:
  - "hog"      : person detector מובנה של OpenCV. לא צריך מודל חיצוני, טוב לבדיקות.
  - "mobilenet": MobileNet-SSD דרך OpenCV DNN. מדויק/מהיר יותר, צריך קבצי מודל.

אחרי זיהוי מוצלח מופעל tracker (CSRT) שעוקב אחרי אותו אדם בין פריימים, וחוסך
הרצה מלאה של הגלאי בכל פריים.
"""

from dataclasses import dataclass

import cv2
import numpy as np

# מזהה ה-class של "person" ברשימת COCO/VOC של MobileNet-SSD
_MOBILENET_PERSON_CLASS = 15


@dataclass
class Detection:
    cx: float      # מרכז X מנורמל [0,1]
    cy: float      # מרכז Y מנורמל [0,1]
    w: float       # רוחב מנורמל [0,1]
    h: float       # גובה מנורמל [0,1]
    confidence: float

    @property
    def area(self):
        return self.w * self.h


class PersonDetector:
    def __init__(self, engine="hog", model_dir=None, conf_threshold=0.5,
                 redetect_interval=15):
        self.engine = engine
        self.conf_threshold = conf_threshold
        self.redetect_interval = redetect_interval  # כל כמה פריימים לזהות מחדש
        self._tracker = None
        self._frames_since_detect = 0

        if engine == "hog":
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        elif engine == "mobilenet":
            if model_dir is None:
                raise ValueError("engine=mobilenet דורש model_dir עם קבצי הרשת")
            proto = f"{model_dir}/MobileNetSSD_deploy.prototxt"
            weights = f"{model_dir}/MobileNetSSD_deploy.caffemodel"
            self._net = cv2.dnn.readNetFromCaffe(proto, weights)
        else:
            raise ValueError(f"מנוע לא מוכר: {engine}")

    # ---- API ראשי ----
    def process(self, frame):
        """מקבל פריים (BGR), מחזיר Detection או None."""
        h, w = frame.shape[:2]

        # אם יש tracker פעיל ולא הגיע הזמן לזהות מחדש — עקוב
        if self._tracker is not None and \
                self._frames_since_detect < self.redetect_interval:
            ok, box = self._tracker.update(frame)
            self._frames_since_detect += 1
            if ok:
                return self._to_detection(box, w, h, confidence=0.99)
            self._tracker = None  # איבדנו — ננסה לזהות מחדש

        # זיהוי מלא
        box = self._detect(frame)
        if box is None:
            self._tracker = None
            return None

        # אתחל tracker על הזיהוי החדש
        self._tracker = self._make_tracker()
        self._tracker.init(frame, box)
        self._frames_since_detect = 0
        return self._to_detection(box, w, h, confidence=0.9)

    # ---- פנימי ----
    def _detect(self, frame):
        if self.engine == "hog":
            return self._detect_hog(frame)
        return self._detect_mobilenet(frame)

    def _detect_hog(self, frame):
        rects, weights = self._hog.detectMultiScale(
            frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
        if len(rects) == 0:
            return None
        # בחר את הזיהוי החזק ביותר
        best = int(np.argmax(weights)) if len(weights) else 0
        x, y, bw, bh = rects[best]
        return (int(x), int(y), int(bw), int(bh))

    def _detect_mobilenet(self, frame):
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        self._net.setInput(blob)
        detections = self._net.forward()

        best_box, best_conf = None, self.conf_threshold
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            cls = int(detections[0, 0, i, 1])
            if cls == _MOBILENET_PERSON_CLASS and conf > best_conf:
                best_conf = conf
                x1 = int(detections[0, 0, i, 3] * w)
                y1 = int(detections[0, 0, i, 4] * h)
                x2 = int(detections[0, 0, i, 5] * w)
                y2 = int(detections[0, 0, i, 6] * h)
                best_box = (x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        return best_box

    @staticmethod
    def _make_tracker():
        # CSRT מדויק; אם לא זמין בגרסת OpenCV, נופלים ל-KCF
        if hasattr(cv2, "TrackerCSRT_create"):
            return cv2.TrackerCSRT_create()
        if hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerCSRT_create"):
            return cv2.legacy.TrackerCSRT_create()
        return cv2.TrackerKCF_create()

    @staticmethod
    def _to_detection(box, frame_w, frame_h, confidence):
        x, y, bw, bh = box
        cx = (x + bw / 2.0) / frame_w
        cy = (y + bh / 2.0) / frame_h
        return Detection(cx=cx, cy=cy, w=bw / frame_w, h=bh / frame_h,
                         confidence=confidence)
