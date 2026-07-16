# Headphone Detector | גלאי אוזניות 🎧

תוכנה שמזהה אם אוזניות (חוטיות, USB או Bluetooth) מחוברות למחשב — עם ממשק גרפי.
עובדת על **Windows, Linux ו-macOS**, ללא צורך בהתקנת ספריות חיצוניות — רק Python 3.

A cross-platform tool that detects whether headphones (wired, USB, or
Bluetooth) are currently connected, with a GUI. Works on Windows, Linux,
and macOS using only the Python standard library.

## ממשק גרפי / GUI

```bash
python3 headphone_gui.py
```

החלון מציג בזמן אמת:
- 🎧 / 🔇 — מצב החיבור הנוכחי (מתעדכן כל 2 שניות)
- רשימת ההתקנים המחוברים (כולל אוזניות USB ו-Bluetooth)
- יומן אירועים — כל חיבור וניתוק עם שעה
- כפתור רענון ידני

> בלינוקס ייתכן שצריך להתקין את tkinter פעם אחת: `sudo apt install python3-tk`
> (ב-Windows וב-macOS הוא כבר מגיע עם Python).

## שורת פקודה / Command line

```bash
# בדיקה חד-פעמית / one-shot check
python3 headphone_detector.py

# מעקב רציף — מדווח בכל חיבור/ניתוק / continuous monitoring
python3 headphone_detector.py --watch

# פלט JSON לשימוש בסקריפטים / machine-readable output
python3 headphone_detector.py --json
```

### דוגמת פלט / Example output

```
🎧 Headphones connected:
  - Logitech USB Headset (USB)
  - AirPods Pro (Bluetooth)
```

```
🔇 No headphones connected.
```

### קוד יציאה / Exit code

- `0` — אוזניות מחוברות / headphones connected
- `1` — לא מחוברות / not connected

כך אפשר להשתמש בתוכנה ישירות בתוך סקריפטים:

```bash
if python3 headphone_detector.py --json > /dev/null; then
    echo "connected"
fi
```

## איך זה עובד / How it works

| מערכת הפעלה | שיטת הזיהוי |
|---|---|
| Linux | אוזניות USB דרך `/proc/asound/cards` (כרטיסי USB-Audio עם ערוץ השמעה), שקע אוזניות ו-Bluetooth דרך `pactl` (PulseAudio/PipeWire), עם fallback ל-`amixer` (ALSA) ו-`bluetoothctl` |
| Windows | PowerShell — `Get-PnpDevice -Class AudioEndpoint` לפי שם ההתקן, ו-`Get-PnpDevice -Class MEDIA` להתקני שמע בחיבור USB |
| macOS | `system_profiler SPAudioDataType` — התקני פלט מסוג USB, אוזניות או Bluetooth |
