# 🎬 מוריד סרטוני YouTube

אתר פשוט להורדת סרטוני YouTube כווידאו (MP4) או אודיו (MP3).
ממשק HTML נקי + שרת Flask קטן שמריץ [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) ברקע.

## למה צריך שרת?
ב-HTML בלבד אי אפשר להוריד סרטוני YouTube — הדפדפן חוסם זאת (CORS)
ו-YouTube מצפין את זרמי הווידאו. לכן יש שרת קטן שמבצע את ההורדה בפועל.

## התקנה

```bash
pip install -r requirements.txt
```

> להורדת **אודיו (MP3)** צריך גם את [`ffmpeg`](https://ffmpeg.org/) מותקן במערכת.
> - macOS: `brew install ffmpeg`
> - Ubuntu/Debian: `sudo apt install ffmpeg`
> - Windows: הורד מ-ffmpeg.org והוסף ל-PATH

## הרצה

```bash
python app.py
```

פתח בדפדפן: <http://localhost:5000>

הדבק קישור YouTube, בחר וידאו או אודיו, ולחץ "הורד".

## מבנה הפרויקט

```
app.py                 # שרת Flask + לוגיקת ההורדה (yt-dlp)
templates/index.html   # ממשק המשתמש (HTML/CSS/JS)
record.py              # חלופה: הקלטת מסך של הסרטון המתנגן
requirements.txt       # תלויות Python
```

## חלופה: הקלטת מסך (`record.py`)

במקום להוריד את הזרם, אפשר **לפתוח את הסרטון בדפדפן אוטומטי, לנגן אותו,
ולהקליט את המסך + הקול** ל-MP4. איטי יותר (הקלטה בזמן אמת) ובאיכות נמוכה
יותר, אבל "מצלם" את מה שמתנגן בפועל.

דרישות נוספות (Linux):
```bash
sudo apt install ffmpeg xvfb pulseaudio
pip install playwright && playwright install chromium
```

הרצה:
```bash
python record.py "https://www.youtube.com/watch?v=..." --out video.mp4
```

## ⚠️ הבהרה משפטית
הורדת תוכן מוגן בזכויות יוצרים עלולה להפר את תנאי השימוש של YouTube.
השתמש בכלי זה רק עבור:
- תוכן שאתה הבעלים שלו
- תוכן ברישיון חופשי (למשל Creative Commons)
- תוכן שקיבלת עליו אישור מבעל הזכויות

האחריות על השימוש היא עליך בלבד.
