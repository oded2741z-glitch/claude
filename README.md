# מחולל עומס CPU · CPU Load Generator

תוכנת Python עם GUI שולחני (tkinter) שמעמיסה על המעבד בעוצמה ולמשך זמן שקובעים, ומציגה בזמן אמת את העומס ואת הטמפרטורה.

A Python desktop app (tkinter GUI) that stresses the CPU at a chosen intensity for a chosen duration, showing live usage and temperature.

![screenshot](docs/screenshot.png)

## הפעלה · Quick start

```bash
python3 cpu_load_gui.py
```

ללא תלויות — ספריית התקן של Python בלבד. בלינוקס (דביאן/אובונטו), אם tkinter חסר:

```bash
sudo apt install python3-tk
```

No dependencies — Python standard library only. On Debian/Ubuntu install `python3-tk` if tkinter is missing (Windows/macOS Python installers include it).

## יכולות · Features

- **עוצמת עומס 0–100%** — סליידר שקובע כמה מכל ליבה להעסיק. כל תהליך עובד במחזוריות של 100ms (עסוק לפי היעד, ישן בשאר), ואפשר לשנות את העוצמה תוך כדי ריצה.
- **משך ריצה** — קובעים דקות ושניות; העומס נעצר אוטומטית בתום הזמן, עם ספירה לאחור חיה. ‎0:00 = ללא הגבלה.
- **תצוגת עומס חיה** — אחוז שימוש כולל, גרף של 60 השניות האחרונות, ופס נפרד לכל ליבה.
- **טמפרטורת מעבד** — נקראת מ-`/sys/class/thermal` ומחיישני hwmon בלינוקס (או `psutil` אם מותקן); התצוגה מצהיבה מ-70°C ומאדימה מ-85°C, וכשאין חיישן מוצג "—".
- **מספר תהליכי עומס** — בוחרים על כמה ליבות לעבוד (עד פי 2 ממספר הליבות).

* Intensity slider (0–100% duty cycle per worker), adjustable live
* Duration in minutes:seconds with auto-stop and live countdown (0:00 = unlimited)
* Live overall usage, 60-second history chart, and per-core bars
* CPU temperature via `/sys/class/thermal` / hwmon (or `psutil`), color-coded at 70°C/85°C
* Worker-count control (up to 2× core count)

> ⚠️ הכלי נועד לבדיקות עומס על המחשב שלכם (קירור, throttling, התנהגות תחת עומס). עומס ממושך של 100% מחמם את המעבד — השתמשו בהיגיון.
