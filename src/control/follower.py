"""ממיר זיהוי אדם לפקודת תנועה לרחפן, דרך שלושה בקרי PID.

מוסכמות הצירים (body frame, כמו ArduPilot):
  vx  – מהירות קדימה(+)/אחורה(-)   [m/s]
  vz  – מהירות מטה(+)/מעלה(-)       [m/s]  (NED: Z חיובי כלפי מטה)
  yaw_rate – סיבוב ימינה(+)/שמאלה(-) [rad/s]
"""

from dataclasses import dataclass

from .pid import PID


@dataclass
class Command:
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    yaw_rate: float = 0.0

    @classmethod
    def hover(cls):
        return cls()


class Follower:
    def __init__(self, cfg):
        c = cfg["control"]
        self.target_height = c["target_bbox_height"]   # גובה יעד של האדם בפריים
        self.target_cy = c["target_cy"]                # מיקום אנכי יעד
        self.deadzone = c["deadzone"]                  # אזור מת סביב האפס

        self.yaw_pid = PID(**c["yaw_pid"])
        self.forward_pid = PID(**c["forward_pid"])
        self.vertical_pid = PID(**c["vertical_pid"])

    def reset(self):
        self.yaw_pid.reset()
        self.forward_pid.reset()
        self.vertical_pid.reset()

    def compute(self, detection):
        """detection=None → hover. אחרת מחשב פקודת מהירות."""
        if detection is None:
            self.reset()
            return Command.hover()

        # שגיאה אופקית → yaw. חיובי = אדם מימין → סובב ימינה.
        yaw_err = detection.cx - 0.5
        yaw_err = self._apply_deadzone(yaw_err)
        yaw_rate = self.yaw_pid.update(yaw_err)

        # שגיאת מרחק: תיבה קטנה מהיעד = אדם רחוק → טוס קדימה (vx חיובי).
        dist_err = self.target_height - detection.h
        dist_err = self._apply_deadzone(dist_err)
        vx = self.forward_pid.update(dist_err)

        # שגיאה אנכית: לשמור את האדם בגובה היעד בפריים.
        # אם האדם נמוך בפריים (cy גדול) → צריך לרדת → vz חיובי (מטה).
        vert_err = detection.cy - self.target_cy
        vert_err = self._apply_deadzone(vert_err)
        vz = self.vertical_pid.update(vert_err)

        return Command(vx=vx, vy=0.0, vz=vz, yaw_rate=yaw_rate)

    def _apply_deadzone(self, err):
        return 0.0 if abs(err) < self.deadzone else err
