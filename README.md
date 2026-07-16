# Headphone Detector | גלאי אוזניות 🎧

תוכנה שמזהה אם אוזניות (חוטיות או Bluetooth) מחוברות למחשב.
עובדת על **Windows, Linux ו-macOS**, ללא צורך בהתקנת ספריות חיצוניות — רק Python 3.

A cross-platform tool that detects whether headphones (wired or Bluetooth)
are currently connected. Works on Windows, Linux, and macOS using only the
Python standard library.

## שימוש / Usage

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
| Linux | `pactl` (PulseAudio/PipeWire) לזיהוי שקע אוזניות ו-sinks של Bluetooth, עם fallback ל-`amixer` (ALSA) ו-`bluetoothctl` |
| Windows | PowerShell — `Get-PnpDevice -Class AudioEndpoint` וסינון לפי שם ההתקן |
| macOS | `system_profiler SPAudioDataType` — התקני פלט מסוג אוזניות או Bluetooth |
