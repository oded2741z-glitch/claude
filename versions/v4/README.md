# v4 - setup wizard hardened for real Windows device lists

Saved: 2026-09-17

Builds on v3. clint.py and server.py are UNCHANGED from v3; only setup.py grew.

## Files
- clint.py   headless client (same as v3)
- server.py  GUI + integrated signalling server (same as v3)
- setup.py   headset auto-detection wizard

## What v4 adds over v3 (setup.py only)
- PnP fallback: when PortAudio sees no change on unplug (the Realtek-jack case),
  the wizard runs a Windows Get-PnpDevice diff and maps the removed endpoint to
  a keyword that also matches the sounddevice list.
- Manual pick: if PnP finds nothing too, the wizard lists the devices and lets
  the user choose the headset. The PnP query runs only as a fallback.
- MME 31-char truncation handling: Windows truncates device names to 31 chars,
  so the closing paren is often missing. _identity now takes everything after
  the first '(' regardless, and the manual pick derives the keyword with
  _pair_keyword - the longest leading run of whole words shared by an input and
  an output device, which survives the mic/speaker names being cut at different
  points.
- Noise filter: Sound Mapper, Primary Sound, Stereo Mix and the NVIDIA HDMI
  monitor are hidden from the manual list. Keywords are short so truncation does
  not slip one past the filter.

## Required setting
settings.txt must name the headset (setup.py fills it in):

    {
        "ip": "192.168.1.12",
        "port": "9999",
        "my_id": "node_B",
        "hp_device": "3- USB PnP Audio"
    }

## Known limitation
A headset on a Realtek analog jack still cannot be detected as unplugged at
runtime - Windows/PortAudio keeps the endpoint alive. setup.py can still name
the right device (PnP or manual pick) so the intercom works; only live
unplug-detection is unavailable for that device type.
