# v2 - beep on connect, remote shutdown, themed dialog

Saved: 2026-09-06

Builds on v1. Confirmed working by Oded.

## Files
- clint.py   headless client
- server.py  GUI + integrated signalling server

## What v2 adds over v1
- Beep on the server when a client's headphones connect (1000 Hz, 200 ms,
  every 1.5 s). Fires only on a real state change. Silenced by clicking the
  CLIENT HEADPHONES card, and stops on unplug, on START INTERCOM, on stopping
  the internal server, and on close.
- SHUTDOWN CLIENT button. Sends {"cmd":"shutdown"} from the internal server
  socket; the client exits its process. The client obeys the command only when
  it arrives from the signalling server address it registered with.
- Confirmation and info dialogs drawn in the app theme instead of the native
  Windows messagebox. Draggable, modal, Escape cancels and Return confirms.
- oT watermark on the dialog; window grown to 480x700 so the watermark and all
  three status cards fit.

## Required setting
settings.txt must name the headset, or the client falls back to the system
default device and cannot see an unplug:

    {
        "ip": "192.168.1.12",
        "port": "9999",
        "my_id": "node_B",
        "hp_device": "2- USB PnP"
    }

## Layout note
Anything added to the server window must be packed BEFORE _create_dashboard().
The dashboard takes expand=True, so a widget packed after it gets no space and
is silently not drawn.

## Still native Windows dialogs
Help, and the START INTERCOM validation errors.
