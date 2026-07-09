# מחולל עומס CPU · CPU Load Generator

כלי ליצירת עומס מבוקר על המעבד, עם ממשק גרפי מודרני בדפדפן — ללא תלויות, Python בלבד.

A controllable CPU stress tool with a modern web GUI — zero dependencies, pure Python standard library.

![screenshot](docs/screenshot.png)

## הפעלה · Quick start

```bash
python3 cpu_load.py
```

הדפדפן ייפתח אוטומטית בכתובת http://127.0.0.1:8321 (או הוסיפו `--no-browser` ופתחו ידנית).

The browser opens automatically at http://127.0.0.1:8321 (or pass `--no-browser` and open it yourself).

```bash
python3 cpu_load.py --port 9000 --no-browser
```

## יכולות · Features

- **עוצמת עומס מדויקת (0–100%)** — כל תהליך עובד במחזוריות של 100ms: עסוק לפי אחוז היעד וישן בשאר הזמן. אפשר לשנות את העוצמה בזמן אמת, בלי לעצור.
- **שליטה במספר הליבות** — בוחרים כמה תהליכי עומס להריץ (עד פי 2 ממספר הליבות).
- **גרף חי** — שימוש ה-CPU ב-60 השניות האחרונות, עם סמן ו-tooltip במעבר עכבר.
- **פסי עומס לכל ליבה** בנפרד, מתעדכנים כל חצי שנייה.
- **ללא התקנות** — ספריית התקן של Python בלבד; מדידת CPU דרך `/proc/stat` בלינוקס (או `psutil` אם מותקן, ב-Windows/macOS).

* Precise duty-cycle load (0–100%), adjustable live while running
* Per-core worker control, live 60s usage chart with hover tooltip, per-core bars
* No dependencies; CPU sampling via `/proc/stat` on Linux (falls back to `psutil` if installed on Windows/macOS)

## API

הממשק הגרפי משתמש ב-API פשוט שאפשר גם לתסרט:

```bash
curl -X POST localhost:8321/api/control -d '{"action":"start","workers":4,"target":70}'
curl -X POST localhost:8321/api/control -d '{"target":30}'      # change intensity live
curl -X POST localhost:8321/api/control -d '{"action":"stop"}'
curl localhost:8321/api/stats
```

> ⚠️ הכלי נועד לבדיקות עומס על המחשב שלכם (בדיקת קירור, throttling, התנהגות תחת עומס). עומס ממושך של 100% מחמם את המעבד — השתמשו בהיגיון.
