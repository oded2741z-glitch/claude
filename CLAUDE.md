# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A two-machine P2P voice intercom over UDP with NAT hole punching. **Console
only — nothing in the active code imports tkinter**, and no code path may
require a display or a keypress. Both target machines run headless; every
action the old GUI had behind a button is now a line in a TXT file.

| File | Role |
|---|---|
| `node.py` | the only entry point. `--role a` = signalling server + peer, `--role b` = peer only |
| `intercom_core.py` | wire protocol, `AudioGuard`, `IntercomPeer` (one call, start to finish) |
| `signalling.py` | `SignallingServer`: UDP rendezvous, matches two peers, never carries audio |
| `txt_bridge.py` | `ControlFile` / `StatusFile` — the TXT bridge |
| `legacy_gui/` | the original Tkinter `server.py` + `clint.py`. **Reference only, not deployed** — do not fix bugs there, and do not import from it |

The signalling server is a rendezvous point only. **Audio never passes through
it** — it hands each side the other's `(ip, port)` and drops out of the path.

## Commands

```bash
pip install sounddevice numpy      # plus PortAudio on the OS: apt install libportaudio2
python node.py --role a            # computer A: signalling server + peer
python node.py --role b --server-ip <A>   # computer B: peer only
python tests/selftest.py           # end-to-end, no sound card needed; exit 0 = pass
```

`tests/selftest.py` is the whole test suite — there is no pytest, linter or
build step. It installs `tests/fake_sounddevice.py` in place of the real
module (which is why `fake_sounddevice.install()` must run *before*
`intercom_core` is imported — that module binds `sd` at import time), then
runs two real nodes over real UDP sockets and checks matching, live audio,
`intercom = off/on`, device unplug/replug recovery, and `command = quit`.
Run it after any change to the protocol, the call loop or the bridge.

## Deployment topology

- **Computer A** — signalling server *and* a peer; headphones (mic + speaker in one device). Driven by `control_A.txt`, which another program on that machine rewrites.
- **Computer B** — peer only; separate speakers + microphone.
- Ids must differ; the server pairs whichever two clients it sees first.

**The loopback trap:** if node A registers via `127.0.0.1`, the source address
the server records is loopback, and that is the useless address computer B
gets handed. Role A therefore defaults `server_ip` to the auto-detected LAN
address (`detect_local_ip()`), and `_warn_loopback()` logs a warning if a
control file puts it back to `127.0.0.1` without external-IP rewriting.

## Wire protocol (single socket, three multiplexed message kinds)

Each peer opens **one** UDP socket and uses it for both signalling and audio.
This is deliberate: the NAT mapping created when registering with the server is
the same mapping the peer punches into. Never split these onto two sockets.

Dispatch is by first byte:

| Kind | Shape | Notes |
|---|---|---|
| Signalling | JSON object, starts with `{` | `{"id": "<id>"}` = register/keepalive; `{"id": ..., "status": "disconnected"}` = release slot |
| Server reply | `{"peer_ip", "peer_port", "peer_id"}` | sent `MATCH_RETRIES` times — UDP is lossy and there is no ack |
| Audio | `\x01` + raw int16 PCM | `AUDIO_TAG` |
| Punch | `\x02PUNCH` | `PUNCH_TAG` |

Untagged/unknown packets are dropped, never written to the speaker. Keep it
that way — that tag byte is the only thing preventing a JSON control message
from being played as a burst of noise. Audio format constants live once, in
`intercom_core.py`; a mismatch between the two machines produces garbled audio
rather than an error, so never re-declare them elsewhere.

## Matching and peer locking

Server side (`_handle_signalling`): registrations accumulate in a dict keyed by
client id, pruned at `CLIENT_TTL` (30 s). The moment the dict holds **exactly
two** entries it pairs them, blasts each the other's address, and clears the
dict. Consequences worth knowing: after a pairing the registry is empty — which
is why a `status: disconnected` message is honoured even for an id that is not
currently registered — and each side must re-register after any drop.

Peer side (`IntercomPeer._peer_audio`): three separate addresses are tracked.

- `assigned_peer` — what the server said.
- `locked_peer` — the source of the *first tagged packet actually received*; the socket locks onto it, and every later packet from a different address is discarded (blocks audio injection from anything else on the network).
- `tx_peer` — send target, retargeted to `locked_peer` on lock, because NAT often allocates a port different from the one the server observed.

## PortAudio safety invariants

`sd._terminate()` / `sd._initialize()` while a stream is open kills the process
at the C level with no traceback. `AudioGuard` (the module-level `AUDIO`
singleton — PortAudio is global state, so there is exactly one) exists for that:

- `_open_streams` counter incremented for the **entire lifetime including close** (`counted_stream`) — closing a stream on a just-unplugged device can block for seconds.
- `refresh()` refuses to run while the counter is non-zero and throttles itself to `HW_REFRESH_GAP` (5 s). Never call `sd._terminate()` directly.
- `devices_ready()` *queries* the devices instead of opening and closing real streams; open/close cycles on a device that is being unplugged are themselves a crash source.

**Shutdown order is load-bearing:** `IntercomPeer.stop()` sends the disconnect
notice, joins the audio threads (`STREAM_CLOSE_WAIT`), and only then closes the
socket. Closing the socket first leaves the sound card held and produces
misleading hardware exceptions. It is idempotent and safe to call from an audio
thread (it skips joining `current_thread()`), which matters because both the
node supervisor and `run()`'s own `finally` call it.

## The TXT bridge

`node.py` polls the control file every `POLL_INTERVAL` (0.5 s) and applies only
what changed: `ext_ip`/`local_mode` are pushed onto the live server object, a
port change restarts the server, an id/target change reopens the call.

Rules that must survive any edit here:

- **The main loop never blocks.** Device availability is checked with the non-blocking `AUDIO.devices_ready()`, not `wait_for_devices()`, so the control file stays responsive on a machine with no headphones plugged in. The call itself runs on its own thread with a per-call stop event.
- **A control file that fails to parse is ignored, and its (mtime, size) signature is not cached** — the other program may be halfway through writing, so the file is simply re-read next tick and the last good config stays in force.
- **Status writes are atomic** (temp file + `os.replace`) and skipped when nothing changed, and the key set is fixed so a reader never sees a key disappear.
- `parse_config_text` accepts `key = value`, `key: value` and JSON, and maps aliases (`ip` → `server_ip`), which is what keeps the old GUI's `settings_A.txt` / `settings.txt` working as control files.

## Language conventions

Inline comments are in Hebrew and explain *why* a guard exists (crash modes,
NAT quirks, race conditions); docstrings, log output and file contents are in
English. Match that split when editing.
