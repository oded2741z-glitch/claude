"""ממשק ל-DJI Tello (מסלול א' — הכי פשוט ובטוח).

דורש djitellopy:
    pip install djitellopy

ה-Tello מקבל פקודות rc_control בטווח [-100, 100]. אנחנו ממירים את פקודות המהירות
(m/s ו-rad/s) לטווח הזה דרך פקטורי סקייל בקונפיג.
"""

from .base import DroneInterface

try:
    from djitellopy import Tello
except ImportError:
    Tello = None


def _clamp(v, lo=-100, hi=100):
    return int(max(lo, min(hi, v)))


class TelloDrone(DroneInterface):
    def __init__(self, speed_scale=60.0, yaw_scale=60.0, vertical_scale=60.0):
        if Tello is None:
            raise ImportError("djitellopy לא מותקן. הריצו: pip install djitellopy")
        self.tello = Tello()
        self.speed_scale = speed_scale
        self.yaw_scale = yaw_scale
        self.vertical_scale = vertical_scale

    def connect(self):
        self.tello.connect()
        print(f"[TELLO] מחובר. סוללה: {self.tello.get_battery()}%")

    def arm_and_takeoff(self, altitude_m=None):
        self.tello.takeoff()
        print("[TELLO] המראה")

    def send_velocity(self, command):
        # ל-Tello: (left/right, forward/back, up/down, yaw)
        # אצלנו vx=קדימה, vz=מטה(NED חיובי מטה), yaw_rate=ימינה חיובי
        lr = 0
        fb = _clamp(command.vx * self.speed_scale)
        ud = _clamp(-command.vz * self.vertical_scale)   # הופכים כי vz חיובי=מטה
        yaw = _clamp(command.yaw_rate * self.yaw_scale)
        self.tello.send_rc_control(lr, fb, ud, yaw)

    def land(self):
        self.tello.send_rc_control(0, 0, 0, 0)
        self.tello.land()
        print("[TELLO] נחיתה")

    def close(self):
        try:
            self.tello.end()
        except Exception:
            pass
