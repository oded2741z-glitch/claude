import asyncio
import json
import threading
import time

import cv2
import numpy as np
import mss
import websockets
from ultralytics import YOLO

# Config
SOURCE_INDEX = 0           # The index of the screen in Commander (0 for Screen 1, etc.)
COOLDOWN_TIME = 5          # Seconds to wait before sending another alert
CONFIDENCE_THRESHOLD = 0.3 # Lowered to 30% for higher sensitivity
MONITOR_INDEX = 1          # mss monitor index (1 = primary monitor)
SHOW_DEBUG_WINDOW = True   # Set False to run headless (no cv2 window)
HOST = "localhost"
PORT = 8766

# Target COCO Classes: 0: person, 2: car, 3: motorcycle, 5: bus, 7: truck
TARGET_CLASSES = {0, 2, 3, 5, 7}

clients = set()
stop_event = threading.Event()


async def handler(websocket):
    """Register a client and keep the connection open. Detection runs elsewhere."""
    clients.add(websocket)
    print(f"Client connected ({len(clients)} total)")
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)
        print(f"Client disconnected ({len(clients)} total)")


async def broadcast(payload):
    message = json.dumps(payload)
    for ws in list(clients):
        try:
            await ws.send(message)
        except websockets.ConnectionClosed:
            clients.discard(ws)


def detection_loop(loop, on_exit):
    """Single capture+inference loop, shared by all clients. Runs in its own
    thread so the asyncio event loop (websocket ping/pong) is never blocked."""
    try:
        print("Loading AI Model...")
        model = YOLO("yolov8n.pt")
        print("AI Vision Ready. Capturing Screen...")
        if SHOW_DEBUG_WINDOW:
            print("Press 'q' in the video window to stop.")

        last_alert = 0.0
        with mss.mss() as sct:
            monitor = sct.monitors[MONITOR_INDEX]

            while not stop_event.is_set():
                frame = np.array(sct.grab(monitor))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                detected_objects = []
                for r in model(frame, stream=True, verbose=False):
                    for box in r.boxes:
                        cls_id = int(box.cls[0])
                        conf = float(box.conf[0])
                        if cls_id in TARGET_CLASSES and conf > CONFIDENCE_THRESHOLD:
                            detected_objects.append(model.names[cls_id])
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            label = f"{model.names[cls_id]} {conf:.2f}"
                            cv2.putText(frame, label, (x1, y1 - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

                if SHOW_DEBUG_WINDOW:
                    debug_frame = cv2.resize(frame, (960, 540))
                    cv2.imshow("AI Screen Vision Debug", debug_frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

                now = time.monotonic()
                if detected_objects and now - last_alert >= COOLDOWN_TIME:
                    unique_objects = sorted(set(detected_objects))
                    print(f"Target Detected on Screen! Objects: {unique_objects}. Sending alert...")
                    payload = {
                        "action": "motion",
                        "source_index": SOURCE_INDEX,
                        "details": unique_objects,
                    }
                    asyncio.run_coroutine_threadsafe(broadcast(payload), loop)
                    last_alert = now

                time.sleep(0.01)  # Small delay to prevent CPU overload
    finally:
        cv2.destroyAllWindows()
        loop.call_soon_threadsafe(on_exit)


async def main():
    loop = asyncio.get_running_loop()
    stopped = loop.create_future()

    def on_detection_exit():
        if not stopped.done():
            stopped.set_result(None)

    detector = threading.Thread(target=detection_loop, args=(loop, on_detection_exit), daemon=True)
    detector.start()

    async with websockets.serve(handler, HOST, PORT):
        print(f"WebSocket Server running on ws://{HOST}:{PORT}")
        await stopped  # exits when the detection loop stops (e.g. 'q' pressed)

    stop_event.set()
    detector.join(timeout=5)
    print("Server stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        stop_event.set()
        print("Interrupted. Shutting down.")
