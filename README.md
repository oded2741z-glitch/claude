# P2P Intercom (headless)

Two-machine voice intercom over UDP with NAT hole punching. Console only — no
GUI anywhere. Each node is driven by a plain TXT file that any other program
can rewrite while the node is running.

## Install

Scripted, one command per machine:

```bash
deploy\windows\install.bat A                  # Windows, computer A
deploy\windows\install.bat B 192.168.1.10     # Windows, computer B
./deploy/linux/install.sh a                    # Linux, computer A
./deploy/linux/install.sh b 192.168.1.10       # Linux, computer B
```

They install the dependencies, copy the single file for that role, create
on/off shortcuts, and register it to start at logon (inside the user session,
never as a Session 0 service — audio would be silent there).

To skip Python on the target machines entirely, build standalone executables
once on any Windows box that has Python — `deploy\windows\build_exe.bat`
produces `dist\intercom_A.exe` and `dist\intercom_B.exe`, and `install.bat`
uses them automatically when it finds them.

For the operator, not the developer: **[docs/USER_GUIDE.he.md](docs/USER_GUIDE.he.md)**
(Hebrew, also as [PDF](docs/USER_GUIDE.he.pdf)) covers install, daily use, the
status fields and troubleshooting.

## Install by hand

```bash
pip install sounddevice numpy
# Linux also needs PortAudio:  sudo apt install libportaudio2
```

## Run

Copy **one file** to each machine — that is all each side needs:

| Machine | File | Command |
|---|---|---|
| A — signalling server + peer (headphones) | `intercom_A.py` | `python intercom_A.py` |
| B — peer only (speakers + microphone) | `intercom_B.py` | `python intercom_B.py --server-ip <A>` |

Each node writes a `control_<ROLE>.txt` template on first start and a
`status_<ROLE>.txt` next to it, in the directory it is run from. Ids must
differ between the two machines.

`intercom_A.py` and `intercom_B.py` are built from the modules in this repo by
`python build_single_file.py`. To work on the code, run the modules directly
instead — same program, same flags:

```bash
python node.py --role a
python node.py --role b --server-ip <computer A's address>
```

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

### Toggling the call with one word

Rewriting the whole control file just to flip ON/OFF is awkward, and a program
that rewrites it can lose `server_ip` or `port` by accident. So a node also
reads a **switch file** whose entire content is a single word:

```bash
echo off  > switch_A.txt      # closes the call
echo on   > switch_A.txt      # opens it
echo quit > switch_A.txt      # shuts the node down
```

`toggle_call.py` does this for you, and waits for the node to confirm:

```bash
python toggle_call.py          # on <-> off
python toggle_call.py off      # force
python toggle_call.py --role B # act on switch_B.txt
```

It exits 0 once the node's status file agrees, 1 if nothing picked the change
up — so a script can tell "switched" from "nobody is listening". It is
standalone: copy it next to the node and it works.

The node creates this file on first start, next to the control file, holding
whatever state it is already in. `on`/`off`/`start`/`stop`/`1`/`0`/`true`/`false`
all work. The other settings are never touched, and whichever of the two files
changed last is the one that takes effect. Use `--switch <path>` to put it
elsewhere, `--no-switch` to ignore it.

### Reading the state

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
| `intercom_A.py`, `intercom_B.py` | **generated** standalone builds — what you deploy |
| `build_single_file.py` | rebuilds those two from the modules below |
| `node.py` | the only entry point: role A (server + peer) or role B (peer) |
| `intercom_core.py` | wire protocol, PortAudio guards, the peer/call loop |
| `signalling.py` | UDP rendezvous server — matches two peers, never carries audio |
| `txt_bridge.py` | control/status TXT files |
| `toggle_call.py` | standalone on/off utility for the deployed machine |
| `deploy/` | installers for Windows and Linux |
| `docs/USER_GUIDE.he.md` | operator guide, in Hebrew |
| `legacy_gui/` | the original Tkinter version, kept for reference only |
