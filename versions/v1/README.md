# v1 - headphone and server-link state reporting

Saved: 2026-09-06

Working state confirmed by Oded: headphone connect/disconnect is detected on
the client, printed to CMD, and reflected on the server dashboard.

## Files
- clint.py   headless client
- server.py  GUI + integrated signalling server

## Required setting
settings.txt must name the headset, otherwise the client falls back to the
system default device and cannot see an unplug:

    {
        "ip": "192.168.1.12",
        "port": "9999",
        "my_id": "node_B",
        "hp_device": "2- USB PnP"
    }
