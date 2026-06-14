"""
מוריד סרטוני YouTube - שרת Flask מינימלי.

מריץ את yt-dlp ברקע ומגיש את ממשק ה-HTML.
שימוש: pip install -r requirements.txt && python app.py
פתח בדפדפן: http://localhost:5000
"""

import os
import re
import tempfile
import threading

from flask import Flask, request, jsonify, send_file, render_template

try:
    from yt_dlp import YoutubeDL
except ImportError:  # הודעה ברורה אם החבילה חסרה
    raise SystemExit("חסרה החבילה yt-dlp. הרץ: pip install -r requirements.txt")

app = Flask(__name__)

# תיקיית הורדות זמנית
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "yt_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# מנעול כדי למנוע ריצות מקבילות שיכבידו על המכונה
_download_lock = threading.Lock()

# קישור YouTube תקין בלבד
YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)[\w\-]+",
    re.IGNORECASE,
)


def _safe_filename(name: str) -> str:
    """מנקה תווים בעייתיים משם הקובץ."""
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "video"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def info():
    """מחזיר כותרת, תמונה ממוזערת ומשך לתצוגה מקדימה."""
    url = (request.json or {}).get("url", "").strip()
    if not YOUTUBE_RE.match(url):
        return jsonify({"error": "קישור YouTube לא תקין"}), 400

    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"לא ניתן לקרוא את הסרטון: {exc}"}), 502

    return jsonify(
        {
            "title": data.get("title"),
            "thumbnail": data.get("thumbnail"),
            "duration": data.get("duration"),
            "uploader": data.get("uploader"),
        }
    )


@app.route("/api/download", methods=["POST"])
def download():
    """מוריד את הסרטון (mp4) או את האודיו (mp3) ומחזיר את הקובץ."""
    payload = request.json or {}
    url = payload.get("url", "").strip()
    fmt = payload.get("format", "video")  # "video" או "audio"

    if not YOUTUBE_RE.match(url):
        return jsonify({"error": "קישור YouTube לא תקין"}), 400

    if fmt == "audio":
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
            "quiet": True,
            "no_warnings": True,
        }
    else:
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
        }

    with _download_lock:
        try:
            with YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(url, download=True)
                filepath = ydl.prepare_filename(data)
                if fmt == "audio":
                    filepath = os.path.splitext(filepath)[0] + ".mp3"
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"ההורדה נכשלה: {exc}"}), 502

    if not os.path.exists(filepath):
        return jsonify({"error": "הקובץ לא נוצר"}), 500

    ext = ".mp3" if fmt == "audio" else ".mp4"
    download_name = _safe_filename(data.get("title", "video")) + ext
    return send_file(filepath, as_attachment=True, download_name=download_name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
