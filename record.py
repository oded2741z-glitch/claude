"""
הקלטת מסך של סרטון YouTube.

הסקריפט פותח את הסרטון בדפדפן אוטומטי (Playwright), מנגן אותו במסך מלא,
ובמקביל מקליט את המסך + הקול בעזרת ffmpeg ושומר ל-MP4.

זו חלופה לשיטת yt-dlp (app.py). היא איטית יותר (הקלטה בזמן אמת)
ואיכות הפלט תלויה ברזולוציית התצוגה, אבל היא "מצלמת" את מה שמתנגן בפועל.

--- דרישות (Linux) ---
    sudo apt install ffmpeg xvfb pulseaudio
    pip install playwright
    playwright install chromium

--- שימוש ---
    # מצב headless עם תצוגה וירטואלית (שרת ללא מסך):
    python record.py "https://www.youtube.com/watch?v=..." --out video.mp4

    # אם כבר יש לך מסך אמיתי (DISPLAY מוגדר), אפשר:
    python record.py "<url>" --out video.mp4 --no-xvfb
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

WIDTH, HEIGHT = 1280, 720
DISPLAY_NUM = 99  # מספר התצוגה הווירטואלית עבור Xvfb


def check_deps(use_xvfb: bool):
    """ודא שכל הכלים החיצוניים קיימים לפני שמתחילים."""
    required = ["ffmpeg"]
    if use_xvfb:
        required += ["Xvfb"]
    missing = [t for t in required if shutil.which(t) is None]
    if missing:
        sys.exit(f"חסרים כלים: {', '.join(missing)}. ראה הוראות התקנה בראש הקובץ.")
    try:
        import playwright  # noqa: F401
    except ImportError:
        sys.exit("חסרה החבילה playwright. הרץ: pip install playwright && playwright install chromium")


def get_duration(url: str) -> float:
    """משך הסרטון בשניות — דרך yt-dlp אם זמין, אחרת ברירת מחדל."""
    try:
        from yt_dlp import YoutubeDL
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return float(info.get("duration") or 0)
    except Exception:
        return 0.0


def start_xvfb() -> subprocess.Popen:
    """מפעיל תצוגה וירטואלית כדי שאפשר יהיה להקליט ללא מסך פיזי."""
    proc = subprocess.Popen(
        ["Xvfb", f":{DISPLAY_NUM}", "-screen", "0", f"{WIDTH}x{HEIGHT}x24", "-ac"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)  # זמן לתצוגה לעלות
    return proc


def start_ffmpeg(display: str, out_path: str) -> subprocess.Popen:
    """מקליט את המסך (x11grab) + קול (PulseAudio) ל-MP4."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "x11grab", "-video_size", f"{WIDTH}x{HEIGHT}",
        "-framerate", "30", "-i", display,
        # קול: ברירת המחדל של PulseAudio (monitor של הפלט)
        "-f", "pulse", "-i", "default",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        out_path,
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play_video(url: str, display: str, duration: float):
    """פותח את הסרטון, מדלג על מודעות אם אפשר, ומנגן עד הסוף."""
    from playwright.sync_api import sync_playwright

    os.environ["DISPLAY"] = display
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # חייב חלון אמיתי כדי לצלם את המסך
            args=[
                f"--window-size={WIDTH},{HEIGHT}",
                "--autoplay-policy=no-user-gesture-required",
                "--start-fullscreen",
                "--no-sandbox",
            ],
        )
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.goto(url, wait_until="domcontentloaded")

        # ניסיון לאשר cookies אם מופיע
        for label in ("Accept all", "אשר הכול", "I agree"):
            try:
                page.get_by_role("button", name=label).click(timeout=1500)
                break
            except Exception:
                pass

        # נגן ועבור למסך מלא
        try:
            page.wait_for_selector("video", timeout=15000)
            page.evaluate("() => { const v=document.querySelector('video'); if(v){v.muted=false; v.play();} }")
            page.keyboard.press("f")  # מסך מלא ב-YouTube
        except Exception as exc:
            print(f"אזהרה: לא הצלחתי להפעיל את הנגן אוטומטית ({exc})")

        # המתן למשך הסרטון (עם מרווח קטן), או 60 שניות אם לא ידוע
        wait_for = duration + 5 if duration else 60
        print(f"מקליט כ-{int(wait_for)} שניות...")
        time.sleep(wait_for)
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="הקלטת מסך של סרטון YouTube")
    parser.add_argument("url", help="קישור הסרטון")
    parser.add_argument("--out", default="recording.mp4", help="קובץ הפלט")
    parser.add_argument("--no-xvfb", action="store_true",
                        help="השתמש ב-DISPLAY הקיים במקום תצוגה וירטואלית")
    args = parser.parse_args()

    use_xvfb = not args.no_xvfb
    check_deps(use_xvfb)

    display = f":{DISPLAY_NUM}" if use_xvfb else os.environ.get("DISPLAY", ":0")
    duration = get_duration(args.url)

    xvfb_proc = start_xvfb() if use_xvfb else None
    ffmpeg_proc = None
    try:
        ffmpeg_proc = start_ffmpeg(display, args.out)
        play_video(args.url, display, duration)
    finally:
        # סגירה מסודרת של ffmpeg כדי שהקובץ ייחתם כראוי
        if ffmpeg_proc:
            ffmpeg_proc.send_signal(signal.SIGINT)
            try:
                ffmpeg_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
        if xvfb_proc:
            xvfb_proc.terminate()

    if os.path.exists(args.out):
        print(f"✓ נשמר: {args.out}")
    else:
        sys.exit("ההקלטה נכשלה — הקובץ לא נוצר.")


if __name__ == "__main__":
    main()
