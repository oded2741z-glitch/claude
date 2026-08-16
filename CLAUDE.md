# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A two-node P2P voice intercom over UDP. Two Python files, no package, no build step:

- `server.py` — Tkinter app that bundles **two independent roles**: the UDP *signalling server* (`_run_internal_server`) and a full *intercom peer* (`_connection_flow` + audio threads). Either can run without the other.
- `clint.py` — headless intercom peer only (`IntercomCLI`). Same wire protocol, no signalling server, no GUI.

The signalling server is a rendezvous point only. **Audio never passes through it** — it hands each side the other's `(ip, port)` and drops out of the path.

## Commands

```bash
pip install sounddevice numpy          # PortAudio must be present on the OS (apt install libportaudio2)
python server.py                       # signalling server + GUI peer (needs tkinter + a display)
python clint.py                        # headless peer; reconnect loop, Ctrl+C to exit
```

There are no tests, linters, or build tooling in this repo. Verify changes by running the two processes against each other (server on one machine, `clint.py` on both, or `127.0.0.1` on a single machine with two distinct `my_id` values).

## Deployment topology this code targets

- **Computer A** — runs the signalling server *and* a peer; headphones (mic + speaker in one device).
- **Computer B** — peer only (`clint.py`); separate speakers + microphone.
- Both machines are meant to run **headless**. `clint.py` already is. `server.py` is not: it hard-requires `tkinter` and a display, and every action is behind a button. Work toward headless control of computer A goes through the settings file described below, which an external program rewrites — that TXT file is the control bridge, not the GUI.
- Settings are re-read by `clint.py` at the top of every reconnect iteration (`start()` → `load_settings()`), so an external writer changes behaviour without a restart. `server.py` reads its file only at startup.

## Wire protocol (single socket, three multiplexed message kinds)

Each peer opens **one** UDP socket and uses it for both signalling and audio. This is deliberate: the NAT mapping created when registering with the server is the same mapping the peer punches into. Never split these onto two sockets.

Dispatch is by first byte:

| Kind | Shape | Notes |
|---|---|---|
| Signalling | JSON object, starts with `{` | `{"id": "<id>"}` = register/keepalive; `{"id": "<id>", "status": "disconnected"}` = release slot |
| Server reply | `{"peer_ip", "peer_port", "peer_id"}` | sent `MATCH_RETRIES` times — UDP is lossy and there is no ack |
| Audio | `\x01` + raw int16 PCM | `AUDIO_TAG` |
| Punch | `\x02PUNCH` | `PUNCH_TAG` |

Untagged/unknown packets are dropped, never written to the speaker. Keep it that way — that tag byte is the only thing preventing a JSON control message from being played as a burst of noise.

`SAMPLE_RATE`, `CHANNELS`, `CHUNK_SIZE`, `DTYPE`, and the two tag bytes are duplicated at the top of both files and **must be edited in lockstep**; a mismatch produces garbled audio rather than an error.

## Matching and peer locking

Server side (`_handle_signalling`): registrations accumulate in a dict keyed by client id, pruned at `CLIENT_TTL` (30 s). The moment the dict holds **exactly two** entries it pairs them, blasts each the other's address, and clears the dict. Consequences worth knowing: ids must differ, the server pairs whichever two clients it sees first (no id-based routing), and after a pairing the registry is empty — which is why a `status: disconnected` message is honoured even for an id not currently registered.

Peer side (`_peer_audio`, both files): three separate addresses are tracked.

- `assigned_peer` — what the server said.
- `locked_peer` — the source of the *first tagged packet actually received*; the socket locks onto it, and every later packet from a different address is discarded (blocks audio injection from anything else on the network).
- `tx_peer` — send target, retargeted to `locked_peer` on lock, because NAT often allocates a port different from the one the server observed.

## PortAudio safety invariants

`sd._terminate()` / `sd._initialize()` while a stream is open kills the process at the C level with no traceback. The guards exist for that, not for tidiness:

- `_open_streams` counter, guarded by `audio_lock`, incremented for the **entire lifetime including close** (`_counted_stream`) — closing a stream on a just-unplugged device can block for seconds.
- `_safe_refresh_hardware()` refuses to run while the counter is non-zero and throttles itself to `HW_REFRESH_GAP` (5 s).
- Every device re-init must go through `_safe_refresh_hardware()`. Never call `sd._terminate()` directly.

**Shutdown order is load-bearing:** `stop()` / `stop_connection()` sends the disconnect notice, then joins the audio threads (`STREAM_CLOSE_WAIT`), and only then closes the socket. Closing the socket first leaves the sound card held and produces misleading hardware exceptions. Both methods are re-entrant-safe from an audio thread (they skip joining `current_thread()`).

Failure handling differs between the two files: `server.py`'s audio threads retry internally on `PortAudioError` and keep the call alive; `clint.py`'s threads exit, `_connect_and_stream` notices a dead thread, and the outer `start()` loop reconnects from scratch.

## Settings files

Both are **JSON despite the `.txt` extension**, and the two files use different names and different key sets:

- `server.py` → `settings_A.txt`: `ext_ip`, `local_mode`, `ip`, `port`, `my_id`
- `clint.py` → `settings.txt`: `ip`, `port`, `my_id` (written with defaults if missing)

`ext_ip` / `local_mode` drive `_visible_ip`: when local mode is off and an external IP is set, private or loopback source addresses are rewritten to that external IP before being handed to the other peer. The **port is never translated**, so this path only works behind a static port forward. On a single LAN, local mode must be on.

## Language conventions

Inline comments are in Hebrew and explain *why* a guard exists (crash modes, NAT quirks, race conditions); docstrings and log output are in English. Match that split when editing.
