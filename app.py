import os
import re
import threading
import uuid

import cv2
from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_from_directory,
)
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
ALLOWED_VIDEO_EXT = {".mp4", ".avi", ".mkv", ".mov"}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB

# In-memory tracking of background extraction jobs.
jobs = {}
jobs_lock = threading.Lock()


def safe_folder(name):
    """Sanitize a folder name so it can never escape DATA_DIR."""
    name = (name or "").strip()
    name = re.sub(r"[^\w\-. ]", "", name).strip()
    name = name.replace(" ", "_")
    if not name or name in (".", "..") or name == "labels":
        return None
    return name


def folder_path(folder):
    folder = safe_folder(folder)
    if not folder:
        return None
    return os.path.join(DATA_DIR, folder)


def labels_path(folder):
    fp = folder_path(folder)
    return os.path.join(fp, "labels") if fp else None


def txt_for_image(folder, filename):
    base = os.path.splitext(secure_filename(filename))[0]
    return os.path.join(labels_path(folder), f"{base}.txt")


def list_images(folder):
    fp = folder_path(folder)
    if not fp or not os.path.isdir(fp):
        return []
    files = [
        f
        for f in os.listdir(fp)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    return sorted(files)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/folders")
def api_folders():
    folders = []
    for f in os.listdir(DATA_DIR):
        if os.path.isdir(os.path.join(DATA_DIR, f)) and f != "labels":
            folders.append(f)
    return jsonify(sorted(folders))


@app.route("/api/folder/<folder>")
def api_folder(folder):
    if not folder_path(folder):
        abort(400)
    return jsonify({"folder": folder, "images": list_images(folder)})


@app.route("/api/image/<folder>/<path:filename>")
def api_image(folder, filename):
    fp = folder_path(folder)
    if not fp:
        abort(400)
    return send_from_directory(fp, secure_filename(filename))


@app.route("/api/annotations/<folder>/<path:filename>", methods=["GET"])
def get_annotations(folder, filename):
    if not folder_path(folder):
        abort(400)
    txt = txt_for_image(folder, filename)
    boxes = []
    if txt and os.path.exists(txt):
        with open(txt, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 5:
                    try:
                        x_c, y_c, w, h = map(float, parts[1:5])
                    except ValueError:
                        continue
                    boxes.append(
                        {
                            "tag": parts[0],
                            "x_center": x_c,
                            "y_center": y_c,
                            "width": w,
                            "height": h,
                        }
                    )
    return jsonify({"boxes": boxes})


@app.route("/api/annotations/<folder>/<path:filename>", methods=["POST"])
def save_annotations(folder, filename):
    if not folder_path(folder):
        abort(400)
    data = request.get_json(silent=True) or {}
    boxes = data.get("boxes", [])
    lbl_dir = labels_path(folder)
    os.makedirs(lbl_dir, exist_ok=True)
    txt = txt_for_image(folder, filename)
    with open(txt, "w") as f:
        for b in boxes:
            try:
                tag = str(b["tag"]).strip().replace(" ", "_")
                line = "{} {:.6f} {:.6f} {:.6f} {:.6f}\n".format(
                    tag,
                    float(b["x_center"]),
                    float(b["y_center"]),
                    float(b["width"]),
                    float(b["height"]),
                )
            except (KeyError, ValueError, TypeError):
                continue
            f.write(line)
    return jsonify({"status": "ok", "count": len(boxes)})


@app.route("/api/image/<folder>/<path:filename>", methods=["DELETE"])
def delete_image(folder, filename):
    fp = folder_path(folder)
    if not fp:
        abort(400)
    safe_name = secure_filename(filename)
    img = os.path.join(fp, safe_name)
    txt = txt_for_image(folder, filename)
    if os.path.exists(img):
        os.remove(img)
    if txt and os.path.exists(txt):
        os.remove(txt)
    return jsonify({"status": "ok", "images": list_images(folder)})


def run_extraction(job_id, video_path, folder, interval_sec):
    dst = folder_path(folder)
    lbl = labels_path(folder)
    try:
        os.makedirs(dst, exist_ok=True)
        os.makedirs(lbl, exist_ok=True)

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            _set_job(job_id, status="error", message="Could not open video file.")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            cap.release()
            _set_job(job_id, status="error", message="Could not determine video FPS.")
            return

        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        frame_interval = max(1, int(fps * interval_sec))

        count = 0
        saved = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if count % frame_interval == 0:
                fname = os.path.join(dst, f"frame_{saved:04d}.jpg")
                cv2.imwrite(fname, frame)
                saved += 1
            count += 1
            if total_frames:
                _set_job(
                    job_id,
                    status="running",
                    progress=min(100, int(count / total_frames * 100)),
                    saved=saved,
                )
        cap.release()
        _set_job(
            job_id,
            status="done",
            progress=100,
            saved=saved,
            message=f"Done! {saved} images ready.",
        )
    except Exception as exc:  # noqa: BLE001
        _set_job(job_id, status="error", message=f"Error: {exc}")
    finally:
        try:
            os.remove(video_path)
        except OSError:
            pass


def _set_job(job_id, **kwargs):
    with jobs_lock:
        job = jobs.setdefault(job_id, {})
        job.update(kwargs)


@app.route("/api/extract", methods=["POST"])
def api_extract():
    if "video" not in request.files:
        return jsonify({"error": "No video uploaded."}), 400
    video = request.files["video"]
    folder = safe_folder(request.form.get("folder", ""))
    if not folder:
        return jsonify({"error": "Invalid folder name."}), 400

    try:
        interval = float(request.form.get("interval", "1"))
        if interval <= 0:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Interval must be a positive number."}), 400

    ext = os.path.splitext(video.filename or "")[1].lower()
    if ext not in ALLOWED_VIDEO_EXT:
        return jsonify({"error": "Unsupported video format."}), 400

    job_id = uuid.uuid4().hex
    tmp_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
    video.save(tmp_path)

    _set_job(job_id, status="running", progress=0, saved=0, folder=folder)
    thread = threading.Thread(
        target=run_extraction,
        args=(job_id, tmp_path, folder, interval),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id, "folder": folder})


@app.route("/api/job/<job_id>")
def api_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        abort(404)
    return jsonify(job)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
