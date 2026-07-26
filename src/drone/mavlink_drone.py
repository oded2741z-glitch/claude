"""ממשק MAVLink לבקר טיסה מסוג ArduPilot/PX4 (מסלול ב').

שולח פקודות מהירות ב-body frame במצב GUIDED. דורש pymavlink.

    pip install pymavlink

חיבור לדוגמה:
    /dev/serial0 בקצב 921600 (UART של ה-Pi), או
    udp:127.0.0.1:14550 מול סימולטור SITL.

⚠️ בדקו קודם בלי פרופלורים. ודאו שיש kill switch בשלט שגובר על ה-Pi.
"""

import time

from .base import DroneInterface

try:
    from pymavlink import mavutil
except ImportError:  # מאפשר לייבא את הקובץ גם בלי החבילה מותקנת
    mavutil = None


class MavlinkDrone(DroneInterface):
    def __init__(self, connection_string, baud=921600):
        if mavutil is None:
            raise ImportError("pymavlink לא מותקן. הריצו: pip install pymavlink")
        self.connection_string = connection_string
        self.baud = baud
        self.master = None

    def connect(self):
        print(f"[MAV] מתחבר אל {self.connection_string} ...")
        self.master = mavutil.mavlink_connection(
            self.connection_string, baud=self.baud)
        self.master.wait_heartbeat()
        print(f"[MAV] heartbeat התקבל (system {self.master.target_system})")

    def _set_mode(self, mode_name):
        mode_id = self.master.mode_mapping().get(mode_name)
        if mode_id is None:
            raise ValueError(f"מצב לא נתמך: {mode_name}")
        self.master.mav.set_mode_send(
            self.master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id)

    def arm_and_takeoff(self, altitude_m):
        self._set_mode("GUIDED")
        time.sleep(1)
        self.master.arducopter_arm()
        self.master.motors_armed_wait()
        print("[MAV] armed")

        self.master.mav.command_long_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0,
            0, 0, 0, 0, 0, 0, altitude_m)
        print(f"[MAV] המראה לגובה {altitude_m} מ'")
        time.sleep(max(3, int(altitude_m * 2)))  # המתנה גסה לייצוב

    def send_velocity(self, command):
        """SET_POSITION_TARGET_LOCAL_NED ב-body frame, מהירויות בלבד + yaw rate."""
        # type_mask: להתעלם ממיקום ותאוצה, להפעיל מהירויות + yaw_rate
        type_mask = 0b0000_0111_1100_0111
        self.master.mav.set_position_target_local_ned_send(
            0,  # time_boot_ms
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_FRAME_BODY_OFFSET_NED,
            type_mask,
            0, 0, 0,                                  # x, y, z (מתעלמים)
            command.vx, command.vy, command.vz,       # מהירויות [m/s]
            0, 0, 0,                                   # תאוצות (מתעלמים)
            0, command.yaw_rate)                       # yaw, yaw_rate [rad/s]

    def land(self):
        print("[MAV] נחיתה")
        self._set_mode("LAND")

    def close(self):
        if self.master is not None:
            self.master.close()
