# v3 - all dialogs themed, remote shutdown, setup wizard

Saved: 2026-09-15

Builds on v2. Confirmed working by Oded (QA passed, one finding fixed).

## Files
- clint.py   headless client
- server.py  GUI + integrated signalling server
- setup.py   headset auto-detection wizard (creates settings.txt)

## What v3 adds over v2
- Help dialog and the START INTERCOM validation errors now use the themed
  dark dialog instead of the native Windows messagebox. No native dialog is
  left anywhere in the app.
- Help text ends with an oT copyright line.
- QA fix: the connect beep no longer starts while a call is running - both
  start_ring call sites (hp report and registration) are guarded by
  is_running, so a beep can never bleed into live call audio.
- setup.py: themed tkinter wizard. Snapshots audio devices, asks the user to
  unplug the headset, snapshots again, and identifies the headset from what
  disappeared. Writes hp_device into settings.txt, preserving ip/port/my_id.
  Keyword = longest contiguous word run shared by the removed devices and
  absent from the survivors, so it works when the name is inside parentheses
  (OCULUSVAD Wave) and tells two similar USB dongles apart.

## Required setting
settings.txt must name the headset (setup.py fills it in), or the client
falls back to the system default device and cannot see an unplug:

    {
        "ip": "192.168.1.12",
        "port": "9999",
        "my_id": "node_B",
        "hp_device": "OCULUSVAD Wave"
    }

## Layout note
Anything added to the server window must be packed BEFORE _create_dashboard().
The dashboard takes expand=True, so a widget packed after it gets no space and
is silently not drawn.

## Known limitation
setup.py cannot auto-detect headphones plugged into a Realtek jack - Windows
keeps that endpoint alive, so nothing disappears. A PnP/PowerShell fallback is
the planned next step.
