"""רחפן מדומה — לא שולח כלום, רק מדפיס. לפיתוח ובדיקות בטוחות."""

from .base import DroneInterface


class SimDrone(DroneInterface):
    def __init__(self, verbose=True):
        self.verbose = verbose
        self.armed = False

    def connect(self):
        print("[SIM] מחובר (מדומה)")

    def arm_and_takeoff(self, altitude_m):
        self.armed = True
        print(f"[SIM] arm + המראה לגובה {altitude_m} מ' (מדומה)")

    def send_velocity(self, command):
        if self.verbose:
            print(f"[SIM] vx={command.vx:+.2f} vz={command.vz:+.2f} "
                  f"yaw_rate={command.yaw_rate:+.2f}")

    def land(self):
        self.armed = False
        print("[SIM] נחיתה (מדומה)")

    def close(self):
        print("[SIM] נסגר")
