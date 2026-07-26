"""לולאה ראשית של PiFollow: מצלמה → זיהוי → בקרה → רחפן.

דוגמאות הרצה:
    # סימולציה עם מצלמת המחשב (בטוח, לא שולח לרחפן):
    python -m src.main --drone sim --source 0

    # Tello אמיתי:
    python -m src.main --drone tello --source tello

    # רחפן MAVLink (רק אחרי בדיקות! בלי פרופלורים בהתחלה):
    python -m src.main --drone mavlink --source 0 --takeoff
"""

import argparse
import time

import cv2
import yaml

from .vision.detector import PersonDetector
from .control.follower import Follower


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_drone(kind, cfg):
    if kind == "sim":
        from .drone.sim_drone import SimDrone
        return SimDrone()
    if kind == "tello":
        from .drone.tello_drone import TelloDrone
        d = cfg["drone"]
        return TelloDrone(speed_scale=d["tello_speed_scale"],
                          yaw_scale=d["tello_yaw_scale"],
                          vertical_scale=d["tello_vertical_scale"])
    if kind == "mavlink":
        from .drone.mavlink_drone import MavlinkDrone
        d = cfg["drone"]
        return MavlinkDrone(d["connection_string"], baud=d["baud"])
    raise ValueError(f"סוג רחפן לא מוכר: {kind}")


def open_capture(source, cfg):
    """source: מספר/נתיב למצלמה/קובץ, או 'tello' לזרם הווידאו של ה-Tello."""
    if source == "tello":
        return None  # זרם ה-Tello מטופל בנפרד (לא ממומש כאן לקיצור)
    try:
        source = int(source)
    except (ValueError, TypeError):
        pass
    cap = cv2.VideoCapture(source)
    width = cfg["vision"]["frame_width"]
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    return cap


def draw_overlay(frame, detection, command):
    h, w = frame.shape[:2]
    if detection is not None:
        x = int((detection.cx - detection.w / 2) * w)
        y = int((detection.cy - detection.h / 2) * h)
        bw = int(detection.w * w)
        bh = int(detection.h * h)
        cv2.rectangle(frame, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
    cv2.putText(frame, f"vx={command.vx:+.2f} vz={command.vz:+.2f} "
                f"yaw={command.yaw_rate:+.2f}", (10, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    return frame


def main():
    ap = argparse.ArgumentParser(description="PiFollow – רחפן שעוקב אחרי אדם")
    ap.add_argument("--drone", default="sim",
                    choices=["sim", "tello", "mavlink"])
    ap.add_argument("--source", default="0", help="מצלמה/קובץ, או 'tello'")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--takeoff", action="store_true",
                    help="בצע arm+takeoff (רק לרחפן אמיתי!)")
    ap.add_argument("--show", action="store_true", help="הצג חלון תצוגה")
    args = ap.parse_args()

    cfg = load_config(args.config)
    v = cfg["vision"]
    s = cfg["safety"]

    detector = PersonDetector(engine=v["engine"], model_dir=v.get("model_dir"),
                              conf_threshold=v["conf_threshold"],
                              redetect_interval=v["redetect_interval"])
    follower = Follower(cfg)
    drone = make_drone(args.drone, cfg)

    cap = open_capture(args.source, cfg)
    if cap is None:
        raise SystemExit("זרם Tello לא ממומש בדוגמה זו — השתמש ב--source 0.")

    drone.connect()
    if args.takeoff:
        drone.arm_and_takeoff(s["takeoff_altitude_m"])

    last_seen = time.monotonic()
    start = time.monotonic()
    print("PiFollow רץ. Ctrl+C לעצירה.")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("אין פריים מהמצלמה — עוצר.")
                break

            detection = detector.process(frame)
            now = time.monotonic()

            # לוגיקת איבוד מטרה: אם לא ראינו אדם מספיק זמן → hover
            if detection is not None:
                last_seen = now
            elif now - last_seen > s["lost_timeout_s"]:
                detection = None  # יגרום ל-hover + reset בתוך ה-follower

            command = follower.compute(detection)
            drone.send_velocity(command)

            if args.show:
                cv2.imshow("PiFollow", draw_overlay(frame, detection, command))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            if s["max_runtime_s"] and now - start > s["max_runtime_s"]:
                print("הגיע זמן ריצה מקסימלי — עוצר.")
                break

    except KeyboardInterrupt:
        print("\nעצירה ידנית.")
    finally:
        # תמיד לעצור, לנחות ולסגור בבטחה
        from .control.follower import Command
        drone.send_velocity(Command.hover())
        if args.takeoff:
            drone.land()
        drone.close()
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
