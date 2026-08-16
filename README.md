# P2P Intercom (headless)

Two-machine voice intercom over UDP with NAT hole punching. Console only — no
GUI anywhere. Each node is driven by a plain TXT file that any other program
can rewrite while the node is running.

## Install

```bash
pip install sounddevice numpy
# Linux also needs PortAudio:  sudo apt install libportaudio2
```

## Run

**Computer A** — hosts the signalling server and takes part in the call
(headphones):

```bash
python node.py --role a
```

**Computer B** — client only (speakers + microphone):

```bash
python node.py --role b --server-ip <computer A's address>
```

Each node writes a `control_<ROLE>.txt` template on first start and a
`status_<ROLE>.txt` next to it. Ids must differ between the two machines.

## Controlling a node from another program

Write to `control_A.txt` (changes apply within half a second, no restart):

```ini
intercom   = on          # on | off   - open or close the call
signalling = on          # role A only: host the signalling server
server_ip  = 10.20.1.5   # where the signalling server lives
port       = 9999
my_id      = node_A
ext_ip     =             # external IP handed to peers (needs a port forward)
local_mode = on          # on = ignore ext_ip (single LAN)
command    = quit        # shut the node down
```

Read `status_A.txt` for the live state:

```ini
updated      = 2026-08-16 09:41:02
state        = live          # idle | waiting | punching | live | no-audio
peer_id      = node_B
peer_addr    = 10.20.1.9:51314
call_seconds = 137
rx_age       = 0.0           # seconds since the last audio packet arrived
signalling   = up            # up | off | error
clients      = node_B:connected
remote_ready = yes           # the far side is registered and ready
```

JSON is accepted in the control file too, so the `settings.txt` /
`settings_A.txt` files written by the older GUI version keep working.

## Self test

No sound card required — it swaps in fake audio devices and drives two real
nodes over real UDP:

```bash
python tests/selftest.py
```

## Layout

| File | Role |
|---|---|
| `node.py` | the only entry point: role A (server + peer) or role B (peer) |
| `intercom_core.py` | wire protocol, PortAudio guards, the peer/call loop |
| `signalling.py` | UDP rendezvous server — matches two peers, never carries audio |
| `txt_bridge.py` | control/status TXT files |
| `legacy_gui/` | the original Tkinter version, kept for reference only |
