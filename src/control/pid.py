"""בקר PID פשוט עם הגבלת פלט ומניעת windup."""

import time


class PID:
    def __init__(self, kp, ki, kd, output_limit=None, integral_limit=None):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_limit = output_limit        # (min, max) או None
        self.integral_limit = integral_limit    # מגביל את מצבר האינטגרל
        self.reset()

    def reset(self):
        self._integral = 0.0
        self._prev_error = None
        self._prev_time = None

    def update(self, error, now=None):
        """מקבל שגיאה, מחזיר פקודת בקרה. dt מחושב אוטומטית לפי הזמן."""
        now = time.monotonic() if now is None else now

        if self._prev_time is None:
            dt = 0.0
        else:
            dt = now - self._prev_time
        self._prev_time = now

        # רכיב פרופורציונלי
        p = self.kp * error

        # רכיב אינטגרלי (רק אם עבר זמן)
        if dt > 0:
            self._integral += error * dt
            if self.integral_limit is not None:
                lo, hi = self.integral_limit
                self._integral = max(lo, min(hi, self._integral))
        i = self.ki * self._integral

        # רכיב נגזרתי
        d = 0.0
        if dt > 0 and self._prev_error is not None:
            d = self.kd * (error - self._prev_error) / dt
        self._prev_error = error

        output = p + i + d
        if self.output_limit is not None:
            lo, hi = self.output_limit
            output = max(lo, min(hi, output))
        return output
