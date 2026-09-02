# claude
## P2P Intercom

`server.py` (GUI + signalling server) and `clint.py` (headless client) form a
UDP hole-punching intercom.

### Signalling messages (client -> server)

| message | meaning |
| --- | --- |
| `{"id": X}` | registration: headphones present, looking for a peer |
| `{"id": X, "status": "alive"}` | heartbeat during a call - headphones still present |
| `{"id": X, "status": "left"}` | left the call (timeout / manual stop); headphone state unchanged |
| `{"id": X, "status": "disconnected"}` | the audio device is really gone |

Only registration enters the matching pool. Every client keeps reporting its
state (1-2 s), each state report is sent several times, and the server marks a
client that has been silent for `HP_TIMEOUT` (8 s) as disconnected - so a lost
UDP packet can never leave the CLIENT HEADPHONES row showing a stale state.

### Echo

Echo is produced by the side using **loudspeakers**: its microphone picks up
the other party's voice and sends it back, so the *other* side hears itself.
Both programs therefore run a simple voice switch - the microphone is
attenuated (`DUCK_GAIN`) while the far end is talking, with a `DUCK_HANGOVER`
release and a `BREAK_IN_RATIO` so loud speech can still interrupt. Attenuated
blocks are still transmitted, so the peer's 3 s silence timeout never fires.

Tuning constants at the top of both files: `ECHO_SUPPRESSION`,
`FAR_END_ACTIVE_RMS`, `DUCK_HANGOVER`, `DUCK_GAIN`, `BREAK_IN_RATIO`.
Real headsets on both ends remain the best fix; this is the fallback.

### Remote client control

The GUI has a **STOP / START REMOTE CLIENT** button. It does not kill the
client process - a killed process could never receive the command to come
back. It puts the client into **standby**: the call ends, the sound card is
released, the client stays out of the matching pool, and it keeps reporting
its state and listening for commands every 2 s.

Commands (`{"cmd": "standby"}` / `{"cmd": "active"}`) are sent by the
**internal** signalling server only, and always back to the address the
client's last report came from - that is the only hole its NAT keeps open.
The button is therefore disabled while the internal server is not running.
If a command is lost, the server re-sends it on every client report whose
`mode` does not match, so the button and the client cannot drift apart.
