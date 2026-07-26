"""ממשק אחיד לכל סוגי הרחפנים. main.py עובד מול הממשק הזה בלבד."""

from abc import ABC, abstractmethod


class DroneInterface(ABC):
    @abstractmethod
    def connect(self):
        ...

    @abstractmethod
    def arm_and_takeoff(self, altitude_m):
        ...

    @abstractmethod
    def send_velocity(self, command):
        """שולח פקודת מהירות (Command עם vx, vy, vz, yaw_rate)."""
        ...

    @abstractmethod
    def land(self):
        ...

    def close(self):
        pass
