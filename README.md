# CPU Load Generator · מחולל עומס CPU

A Python desktop app (tkinter GUI, English UI) that stresses the CPU at a chosen intensity for a chosen duration, showing live usage and temperature.

תוכנת Python עם GUI שולחני שמעמיסה על המעבד בעוצמה ולמשך זמן שקובעים, ומציגה בזמן אמת את העומס ואת הטמפרטורה.

![screenshot](docs/screenshot.png)

## Quick start · הפעלה

```bash
python3 cpu_load_gui.py
```

No dependencies — Python standard library only.

- **Linux (Debian/Ubuntu):** if tkinter is missing, `sudo apt install python3-tk`.
- **Windows:** tkinter is included with the standard Python installer. Install `psutil` (`pip install psutil`) to enable CPU-usage readings.

## Features

* **Intensity slider (0–100%)** — each worker runs a 100 ms duty cycle (busy for the target percentage, sleeping for the rest); adjustable live while running
* **Run duration** — minutes : seconds with auto-stop and a live countdown; 0:00 = unlimited
* **Live usage display** — overall percentage, a 60-second history chart, and a bar per core
* **CPU temperature** — color-coded (amber from 70°C, red from 85°C); shows "—" when no sensor is readable
* **Worker-count control** — choose how many cores to load (up to 2× core count)

## Temperature readings · קריאת טמפרטורה

| Platform | Source |
|---|---|
| Linux | `/sys/class/thermal` and hwmon sensors (or `psutil` if installed) |
| Windows | WMI via PowerShell: [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) / OpenHardwareMonitor sensors if the app is running, otherwise the ACPI thermal zone |
| WSL | Windows WMI through `powershell.exe` |

**Windows note:** many PCs don't expose the ACPI thermal zone to regular users — if the temperature shows "—", either run the app **as Administrator**, or (better) install and run **LibreHardwareMonitor**; the app picks up its CPU sensor automatically within a few seconds.

**הערה ל-Windows:** אם הטמפרטורה מציגה "—", הריצו את התוכנה כמנהל (Run as Administrator), או עדיף — התקינו והריצו את LibreHardwareMonitor (חינמי); התוכנה תזהה את החיישן שלו אוטומטית.

> ⚠️ This tool is for stress-testing your own machine (cooling, throttling, behavior under load). A sustained 100% load heats the CPU — use common sense.
