"""User interface strings in Hebrew and English."""

from __future__ import annotations

from .config import DEFAULT_LANGUAGE
from .rtl import to_visual

HE = {
    "app_title": "שיחה ברשת מקומית",
    "my_name": "השם שלי:",
    "my_ip": "הכתובת שלי:",
    "status": "מצב:",
    "state_idle": "פנוי",
    "state_calling": "מתקשר...",
    "state_ringing": "שיחה נכנסת",
    "state_in_call": "בשיחה",
    "peers_group": "מחשבים ברשת",
    "no_peers": "(מחפש מחשבים...)",
    "address": "כתובת:",
    "forget": "הסר",
    "call": "התקשר",
    "hangup": "נתק",
    "answer": "ענה",
    "reject": "דחה",
    "audio_group": "התקני שמע",
    "mic": "מיקרופון:",
    "speaker": "אוזניות / רמקול:",
    "refresh_devices": "רענן התקנים",
    "auto_pick_new": "בחר אוטומטית התקן חדש (אוזניות USB)",
    "auto_answer": "ענה אוטומטית לשיחות נכנסות",
    "mic_level": "עוצמת מיקרופון:",
    "volume": "עוצמת שמיעה:",
    "mic_gain": "הגבר מיקרופון:",
    "mute": "השתק מיקרופון",
    "monitor": "בדיקה עצמית (שמע את עצמך)",
    "log_group": "יומן",
    "settings": "הגדרות",
    "language": "שפה",
    "help": "עזרה",
    "quit": "יציאה",
    "about": "אודות",
    "close": "סגור",
    "save": "שמור",
    "cancel": "בטל",
    "wire_rate": "איכות שמע (דגימות בשנייה):",
    "frame_ms": "אורך מקטע (מ\"ש):",
    "jitter_ms": "חוצץ השהיה (מ\"ש):",
    "rtl_fix": "תיקון כתיבה מימין לשמאל",
    "stats": "נשלחו {sent} · התקבלו {recv} · אבדו {lost} · חוצץ {depth} · הלוך-חזור {rtt}",
    "rtt_unknown": "?",
    # log lines
    "log_started": "התוכנה מוכנה. כתובת מקומית {ip}, פורט שיחות {port}.",
    "log_audio_ready": "שמע פעיל: מיקרופון \"{mic}\", יציאה \"{out}\".",
    "log_audio_error": "שגיאת שמע: {err}",
    "log_no_input": "לא נמצא מיקרופון. חבר אוזניות ולחץ \"רענן התקנים\".",
    "log_no_output": "לא נמצאה יציאת שמע. חבר אוזניות ולחץ \"רענן התקנים\".",
    "log_devices_refreshed": "רשימת ההתקנים עודכנה ({count} התקנים).",
    "log_new_device": "זוהה התקן חדש: {name}",
    "log_device_gone": "התקן השמע נותק: {name}",
    "log_discovery_error": "חיפוש אוטומטי של מחשבים לא פעיל ({err}). השתמש בכתובת ידנית.",
    "log_peer_found": "נמצא ברשת: {name} ({ip})",
    "log_peer_left": "יצא מהרשת: {name}",
    "log_calling": "מתקשר אל {ip}...",
    "log_call_failed": "החיבור אל {ip} נכשל: {err}",
    "log_ringing_out": "מצלצל אצל {name}...",
    "log_incoming": "שיחה נכנסת מ-{name} ({ip})",
    "log_call_started": "השיחה החלה עם {name}.",
    "log_call_ended": "השיחה הסתיימה.",
    "log_rejected": "השיחה נדחתה.",
    "log_busy": "הצד השני תפוס.",
    "log_peer_hangup": "הצד השני ניתק.",
    "log_link_lost": "החיבור אבד.",
    "log_missed": "שיחה שלא נענתה מ-{name}.",
    "log_monitor_on": "בדיקה עצמית פעילה - אתה שומע את המיקרופון שלך.",
    "log_monitor_off": "בדיקה עצמית כבויה.",
    "log_settings_saved": "ההגדרות נשמרו.",
    "log_peer_forgotten": "הכתובת {ip} הוסרה מהרשימה השמורה.",
    "log_rate_from_peer": "הצד המתקשר קבע איכות של {rate} הרץ.",
    "err_no_target": "בחר מחשב מהרשימה או הקלד כתובת IP.",
    "err_bad_ip": "כתובת לא תקינה: {ip}",
    "about_text": (
        "שיחה ברשת מקומית - גרסה {version}\n\n"
        "שני המחשבים חייבים להיות באותה רשת (אותו ראוטר).\n"
        "אם המחשב השני לא מופיע ברשימה, הקלד את כתובת ה-IP שלו ידנית.\n"
        "אם חיברת אוזניות USB אחרי הפעלת התוכנה - לחץ \"רענן התקנים\"."
    ),
}

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
    "language": "Language",
    "help": "Help",
    "quit": "Quit",
    "about": "About",
    "close": "Close",
    "save": "Save",
    "cancel": "Cancel",
    "wire_rate": "Audio quality (samples/second):",
    "frame_ms": "Frame length (ms):",
    "jitter_ms": "Jitter buffer (ms):",
    "rtl_fix": "Right-to-left text fix",
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

TABLES = {"he": HE, "en": EN}

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
    """Look up interface strings, ready for display."""

    def __init__(self, language: str = DEFAULT_LANGUAGE, rtl_fix: bool = True) -> None:
        self.language = language if language in TABLES else DEFAULT_LANGUAGE
        self.rtl_fix = rtl_fix

    @property
    def is_rtl(self) -> bool:
        return self.language == "he"

    def raw(self, key: str, **kwargs: object) -> str:
        """The string in logical order (for stdout, files, tests)."""
        table = TABLES[self.language]
        text = table.get(key) or EN.get(key) or key
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError):
                pass
        return text

    def __call__(self, key: str, **kwargs: object) -> str:
        """The string in the order Tk needs to draw it."""
        return self.visual(self.raw(key, **kwargs))

    def visual(self, text: str) -> str:
        """Reorder text for display: labels, peer names, device names.

        Not gated on the interface language.  Tk's missing bidi support mangles
        Hebrew wherever it appears - a Hebrew computer name, or the "עברית"
        button - even when every label around it is English.  ``to_visual``
        leaves text without right-to-left characters alone.
        """
        return to_visual(text) if self.rtl_fix else text
