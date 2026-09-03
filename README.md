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

### The client does not notice the headphones

Two things make a plugged-in headset invisible to the client:

1. **PortAudio caches its device list at startup.** A device plugged in later
   does not exist for it until the list is refreshed (`_terminate()` +
   `_initialize()`), which the client does every few seconds - but never while
   a stream is still open. An audio thread stuck on a removed device therefore
   blocks detection permanently; the client now says so in its log.
2. **The check looks at the *default* device.** On a machine with built-in
   audio the default may stay on the internal speakers/mic, so the check
   passes whether or not the headset is plugged in - the state never changes
   and no notification is ever produced.

There is a third, and on a machine with more than one sound card the most
common: **unplugging moves the system default somewhere else.** Pull the USB
adapter and Windows falls back to the built-in Realtek card, so a check on the
*default* device still succeeds - the state never changes and nothing is ever
reported, in either direction. The client now compares the *names* of the
devices it is using between checks and reports a switch as a device change
(`DETECT_DEVICE_SWITCH`), but the precise fix is to name the adapter.

To name the headset explicitly in `settings.txt`:

```json
{"ip": "192.168.1.11", "port": "9999", "my_id": "node_B",
 "mic": "USB Audio", "speaker": "USB Audio"}
```

Empty means "system default"; a number is a device index; anything else is
matched against the device name. `python audio_check.py` prints the device
list (and follows plug/unplug live) so the right name can be copied from it.

The name is resolved to a single device by the client itself, not by
sounddevice: the same hardware appears once per host API (MME, DirectSound,
WASAPI), and sounddevice raises `ValueError: Multiple devices found` on such a
name instead of picking one. The client keeps the match that sits on the same
host API as the system default.

#### The wedged sound card

The concrete failure behind "re-plugging is never noticed": leaving a stream
through a `with` block calls `stop()`, which **waits for the output buffers to
drain**. On a USB device that was just yanked that wait never returns, so the
audio thread stays alive holding the sound card, `_open_streams` never drops
to zero, the device list is never refreshed, and the adapter being plugged
back in cannot be seen - until the process is restarted.

Both programs therefore `abort()` the stream (which discards buffers instead
of draining them) before closing it. As a last resort the client restarts
itself (`RESTART_IF_AUDIO_WEDGED`) if a stream still holds the device for
`WEDGED_RESTART_AFTER` seconds, since nothing can free it from inside.
