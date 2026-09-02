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
