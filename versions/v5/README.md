# v5 - audio quality controls + GUI client

Saved: 2026-10-21

Builds on v4. All four files are exact copies of the working tree at save time.

## Files
- server.py      GUI + integrated signalling server (server.exe)
- clint.py       headless client with headphone detection (client.exe)
- setup.py       headset detection + audio settings wizard (setup.exe)
- client_gui.py  NEW GUI client with CALL / HANG UP button

## What v5 adds over v4
- Sample rate selection 16k / 24k / 48k (default 24k) on server and clients;
  the server sends its rate with the peer match and clients warn on mismatch.
- Microphone gain and speaker level (0-200%) for the client, set live from
  setup.py and applied by clint.py every 2 seconds.
- setup.py and server: dark title bar from the first frame, no Tk feather icon.
- client_gui.py: CALL rings the server (beep + green row) and registers for a
  match; the call runs until HANG UP or until the other side stops (3s with no
  audio). Settings in settings_gui.txt. The SETTINGS section (server IP, port,
  ID, devices, quality) is collapsible and collapsed at startup; gain/level
  sliders stay visible.

## Notes
- Server and client quality must match.
- My ID on each client must differ from the server's ID.
