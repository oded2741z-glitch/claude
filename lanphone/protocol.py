"""Wire formats: JSON control messages and binary audio packets."""

from __future__ import annotations

import json
import struct
from typing import Any

import numpy as np

MAGIC = b"LPh1"

# magic | call_id | seq | flags | codec | samples
AUDIO_HEADER = struct.Struct("!4sIIBBH")
AUDIO_HEADER_SIZE = AUDIO_HEADER.size

CODEC_PCM16 = 0

FLAG_SILENCE = 1 << 0


class ProtocolError(ValueError):
    pass


# --------------------------------------------------------------------------
# audio packets
# --------------------------------------------------------------------------
def encode_audio(call_id: int, seq: int, samples: np.ndarray, silence: bool = False) -> bytes:
    """Pack one frame of mono float32 audio as 16 bit PCM."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    data = (pcm * 32767.0).astype("<i2").tobytes()
    flags = FLAG_SILENCE if silence else 0
    header = AUDIO_HEADER.pack(
        MAGIC, call_id & 0xFFFFFFFF, seq & 0xFFFFFFFF, flags, CODEC_PCM16, len(pcm) & 0xFFFF
    )
    return header + data


def decode_audio(packet: bytes) -> tuple[int, int, int, np.ndarray]:
    """Return (call_id, seq, flags, samples).  Raises ProtocolError on junk."""
    if len(packet) < AUDIO_HEADER_SIZE:
        raise ProtocolError("packet too short")
    magic, call_id, seq, flags, codec, count = AUDIO_HEADER.unpack_from(packet)
    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if codec != CODEC_PCM16:
        raise ProtocolError(f"unsupported codec {codec}")
    body = packet[AUDIO_HEADER_SIZE:]
    if len(body) != count * 2:
        raise ProtocolError("payload length mismatch")
    pcm = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
    return call_id, seq, flags, pcm


# --------------------------------------------------------------------------
# control messages (newline delimited JSON over TCP)
# --------------------------------------------------------------------------
INVITE = "invite"
RINGING = "ringing"
ACCEPT = "accept"
REJECT = "reject"
BUSY = "busy"
BYE = "bye"
PING = "ping"
PONG = "pong"

MAX_MESSAGE_SIZE = 64 * 1024


def encode_message(msg: dict[str, Any]) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode_message(line: bytes) -> dict[str, Any]:
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"bad control message: {exc}") from exc
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        raise ProtocolError("control message must be an object with a 't' field")
    return msg
