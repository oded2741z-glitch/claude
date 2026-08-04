"""User interface strings.

One language, English: Tk has no bidirectional text support, so a Hebrew
interface needed every label reordered by hand and every panel mirrored, and
that whole layer is gone.
"""

from __future__ import annotations

EN = {
    "app_title": "LAN Phone",
    "my_name": "My name:",
    "my_ip": "My address:",
    "status": "Status:",
    "state_idle": "Idle",
    "state_calling": "Calling...",
    "state_ringing": "Incoming call",
    "state_in_call": "In call",
    "peers_group": "Computers on the network",
    "no_peers": "(searching...)",
    "address": "Address:",
    "forget": "Forget",
    "call": "Call",
    "hangup": "Hang up",
    "answer": "Answer",
    "reject": "Reject",
    "audio_group": "Audio devices",
    "mic": "Microphone:",
    "speaker": "Headset / speaker:",
    "refresh_devices": "Refresh devices",
    "auto_pick_new": "Auto-select newly connected device (USB headset)",
    "auto_answer": "Auto-answer incoming calls",
    "mic_level": "Mic level:",
    "volume": "Volume:",
    "mic_gain": "Mic gain:",
    "mute": "Mute microphone",
    "monitor": "Self test (hear yourself)",
    "log_group": "Log",
    "settings": "Settings",
    "help": "Help",
    "quit": "Quit",
    "about": "About",
    "close": "Close",
    "save": "Save",
    "cancel": "Cancel",
    "wire_rate": "Audio quality (samples/second):",
    "frame_ms": "Frame length (ms):",
    "jitter_ms": "Jitter buffer (ms):",
    "stats": "sent {sent} · recv {recv} · lost {lost} · buffer {depth} · rtt {rtt}",
    "rtt_unknown": "?",
    "log_started": "Ready. Local address {ip}, call port {port}.",
    "log_audio_ready": "Audio running: mic \"{mic}\", output \"{out}\".",
    "log_audio_error": "Audio error: {err}",
    "log_no_input": "No microphone found. Connect a headset and press \"Refresh devices\".",
    "log_no_output": "No audio output found. Connect a headset and press \"Refresh devices\".",
    "log_devices_refreshed": "Device list updated ({count} devices).",
    "log_new_device": "New device detected: {name}",
    "log_device_gone": "Audio device disconnected: {name}",
    "log_discovery_error": "Automatic discovery is off ({err}). Use a manual address.",
    "log_peer_found": "Found on the network: {name} ({ip})",
    "log_peer_left": "Left the network: {name}",
    "log_calling": "Calling {ip}...",
    "log_call_failed": "Could not reach {ip}: {err}",
    "log_why_other_subnet": (
        "This computer is on network {local} and {ip} is on a different one - "
        "both computers must be connected to the same router."
    ),
    "log_why_calling_self": "{ip} is this computer's own address.",
    "log_why_no_network": "This computer has no active network connection.",
    "log_why_no_dhcp": "The address {local} means the router never assigned one - check the network connection.",
    "log_ringing_out": "Ringing {name}...",
    "log_incoming": "Incoming call from {name} ({ip})",
    "log_call_started": "Call started with {name}.",
    "log_call_ended": "Call ended.",
    "log_rejected": "Call rejected.",
    "log_busy": "The other side is busy.",
    "log_peer_hangup": "The other side hung up.",
    "log_link_lost": "Connection lost.",
    "log_missed": "Missed call from {name}.",
    "log_monitor_on": "Self test on - you hear your own microphone.",
    "log_monitor_off": "Self test off.",
    "log_settings_saved": "Settings saved.",
    "log_peer_forgotten": "Removed {ip} from the saved addresses.",
    "log_rate_from_peer": "Caller selected {rate} Hz.",
    "err_no_target": "Pick a computer from the list or type an IP address.",
    "err_bad_ip": "Invalid address: {ip}",
    "about_text": (
        "LAN Phone - version {version}\n\n"
        "Both computers must be on the same network (same router).\n"
        "If the other computer is not listed, type its IP address manually.\n"
        "If you plugged in a USB headset after starting the app, press \"Refresh devices\"."
    ),
}

# Log lines are coloured by what they say: trouble in amber, call progress in
# the accent colour, everything else plain.
ALERT_KEYS = frozenset(
    {
        "log_audio_error",
        "log_no_input",
        "log_no_output",
        "log_call_failed",
        "log_link_lost",
        "log_device_gone",
        "log_discovery_error",
        "log_busy",
        "log_missed",
        "log_why_other_subnet",
        "log_why_calling_self",
        "log_why_no_network",
        "log_why_no_dhcp",
    }
)
EVENT_KEYS = frozenset(
    {"log_incoming", "log_call_started", "log_ringing_out", "log_new_device", "log_audio_ready"}
)


def severity(key: str) -> str:
    """Text tag for a log key: 'alert', 'event' or '' for plain."""
    if key in ALERT_KEYS:
        return "alert"
    if key in EVENT_KEYS:
        return "event"
    return ""


class Strings:
    """Look up an interface string, formatted."""

    def __call__(self, key: str, **kwargs: object) -> str:
        text = EN.get(key, key)
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError):
                pass
        return text
