# Headphone Detector 🎧

תוכנה שמזהה אם אוזניות (חוטיות, USB או Bluetooth) מחוברות למחשב — עם ממשק גרפי באנגלית.
עובדת על **Windows, Linux ו-macOS**, ללא צורך בהתקנת ספריות חיצוניות — רק Python 3.

A cross-platform tool that detects whether headphones (wired, USB, or
Bluetooth) are connected, with an English GUI. Works on Windows, Linux,
and macOS using only the Python standard library.

**הכל בקובץ אחד** — `headphone_detector.py`. מורידים קובץ אחד ומריצים.

## מה התוכנה עושה / Features

- 🎧 מציגה בזמן אמת אם אוזניות מחוברות (מתעדכן כל 2 שניות)
- 📝 **שומרת כל חיבור וניתוק לקובץ `headphone_log.txt`** עם תאריך ושעה
- 🚀 **פותחת תוכנה לבחירתך כשהאוזניות מתחברות** (למשל נגן מוזיקה)
- ❌ **סוגרת את התוכנה כשהאוזניות מתנתקות**
- Every connect/disconnect event is saved to `headphone_log.txt`
- Optionally opens a program of your choice on connect and closes it on disconnect

## ממשק גרפי / GUI

לחיצה כפולה על הקובץ, או:

```bash
python3 headphone_detector.py
```

**ב-Windows התוכנה נפתחת ישר למגש המערכת (System Tray) — ליד השעון:**
- לחיצה על האייקון פותחת את החלון
- סגירת החלון (X) מחזירה למגש — הניטור ממשיך ברקע
- קליק ימני על האייקון → **Open** / **Exit**
- ריחוף מעל האייקון מציג את המצב (connected / not connected)

בחלון:
1. **Connected devices** — רשימת האוזניות המחוברות כרגע
2. **Program to open/close** — בוחרים תוכנה עם Browse ומסמנים:
   - "Open the program when headphones connect" — תיפתח בחיבור
   - "Close the program when headphones disconnect" — תיסגר בניתוק
3. **Open log file** — פותח את קובץ ה-TXT (האירועים נשמרים אליו אוטומטית ברקע)

ההגדרות נשמרות בקובץ `headphone_settings.json` וייטענו שוב בהפעלה הבאה.

> בלינוקס ייתכן שצריך להתקין את tkinter פעם אחת: `sudo apt install python3-tk`
> (ב-Windows וב-macOS הוא כבר מגיע עם Python).

## שורת פקודה / Command line

```bash
# בדיקה חד-פעמית / one-shot check
python3 headphone_detector.py --cli

# מעקב רציף — מדווח בכל חיבור/ניתוק וכותב ל-TXT / continuous monitoring
python3 headphone_detector.py --watch

# פלט JSON לשימוש בסקריפטים / machine-readable output
python3 headphone_detector.py --json
```

### קוד יציאה / Exit code

- `0` — אוזניות מחוברות / headphones connected
- `1` — לא מחוברות / not connected

## קובץ הלוג / Log file

`headphone_log.txt` נוצר ליד התוכנה, ונראה כך:

```
2026-07-16 18:03:12 - Started, initial state: DISCONNECTED
2026-07-16 18:05:47 - CONNECTED - Logitech USB Headset (USB)
2026-07-16 18:05:47 - Opened program: spotify.exe
2026-07-16 19:12:03 - DISCONNECTED
2026-07-16 19:12:03 - Closed program.
```

## איך זה עובד / How it works

| מערכת הפעלה | שיטת הזיהוי |
|---|---|
| Linux | אוזניות USB דרך `/proc/asound/cards` (כרטיסי USB-Audio עם ערוץ השמעה), שקע אוזניות ו-Bluetooth דרך `pactl` (PulseAudio/PipeWire), עם fallback ל-`amixer` (ALSA) ו-`bluetoothctl` |
| Windows | PowerShell — `Get-PnpDevice -Class AudioEndpoint` לפי שם ההתקן, ו-`Get-PnpDevice -Class MEDIA` להתקני שמע בחיבור USB |
| macOS | `system_profiler SPAudioDataType` — התקני פלט מסוג USB, אוזניות או Bluetooth |
