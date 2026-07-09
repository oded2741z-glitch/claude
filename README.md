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
- **משך ריצה** — בוחרים כמה זמן העומס ירוץ (30 שניות עד שעה, או ללא הגבלה); העומס נעצר אוטומטית בסוף, עם ספירה לאחור חיה. אפשר להאריך/לקצר תוך כדי ריצה.
- **טמפרטורת מעבד** — נקראת מ-`/sys/class/thermal` ומ-hwmon בלינוקס (או `psutil` אם מותקן); התצוגה מצהיבה מ-70°C ומאדימה מ-85°C.
- **שליטה במספר הליבות** — בוחרים כמה תהליכי עומס להריץ (עד פי 2 ממספר הליבות).
- **גרף חי** — שימוש ה-CPU ב-60 השניות האחרונות, עם סמן ו-tooltip במעבר עכבר.
- **פסי עומס לכל ליבה** בנפרד, מתעדכנים כל חצי שנייה.
- **ללא התקנות** — ספריית התקן של Python בלבד; מדידת CPU דרך `/proc/stat` בלינוקס (או `psutil` אם מותקן, ב-Windows/macOS).

* Precise duty-cycle load (0–100%), adjustable live while running
* Run-duration presets (30s–1h or unlimited) with auto-stop and a live countdown
* CPU temperature readout via `/sys/class/thermal` / hwmon (or `psutil`), color-coded from 70°C/85°C
* Per-core worker control, live 60s usage chart with hover tooltip, per-core bars
* No dependencies; CPU sampling via `/proc/stat` on Linux (falls back to `psutil` if installed on Windows/macOS)

## API

הממשק הגרפי משתמש ב-API פשוט שאפשר גם לתסרט:

```bash
curl -X POST localhost:8321/api/control -d '{"action":"start","workers":4,"target":70,"duration":300}'
curl -X POST localhost:8321/api/control -d '{"target":30}'      # change intensity live
curl -X POST localhost:8321/api/control -d '{"duration":900}'   # extend/shorten the timer live
curl -X POST localhost:8321/api/control -d '{"action":"stop"}'
curl localhost:8321/api/stats   # cpu, per-core, temp (°C), remaining seconds
```

`duration` בשניות; `0` או השמטה = ללא הגבלה. `duration` is in seconds; `0` or omitted = unlimited.

> ⚠️ הכלי נועד לבדיקות עומס על המחשב שלכם (בדיקת קירור, throttling, התנהגות תחת עומס). עומס ממושך של 100% מחמם את המעבד — השתמשו בהיגיון.
