"""The TXT bridge: how an external program drives a node and reads its state.

Two plain text files, no GUI and no sockets involved:

  control.txt  - written by the other program, polled by the node (commands in)
  status.txt   - written by the node, read by the other program (state out)

Both use `key = value` lines, and the control file also accepts the JSON
objects the old GUI wrote (`settings_A.txt` / `settings.txt`), so existing
files keep working unchanged.

Partial reads are expected: the other program may be halfway through writing
when we poll. A file that fails to parse is ignored and re-read on the next
tick, and the last good configuration stays in force. Our own writes are
atomic (temp file + os.replace) so the other program never sees a half file.
"""

import json
import os
import tempfile
import time
from typing import Dict, Optional, Tuple

from intercom_core import log

TRUE_WORDS = {"1", "on", "true", "yes", "y", "start", "up", "enable", "enabled"}
FALSE_WORDS = {"0", "off", "false", "no", "n", "stop", "down", "disable", "disabled"}

# מילים נרדפות: הקבצים הישנים של ה-GUI השתמשו ב-"ip" ל-כתובת השרת
ALIASES: Dict[str, str] = {
    "ip": "server_ip",
    "host": "server_ip",
    "server": "server_ip",
    "id": "my_id",
    "name": "my_id",
    "external_ip": "ext_ip",
    "local": "local_mode",
    "call": "intercom",
}


def as_bool(value: str, default: bool = False) -> bool:
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return default


def as_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


QUIT_WORDS = {"quit", "exit", "shutdown"}


def parse_switch_word(text: str) -> Optional[Dict[str, str]]:
    """A whole file that is just `on`, `off` or `quit`.

    זה מה שמאפשר לתוכנה החיצונית לכתוב מילה אחת במקום לשכתב את כל
    קובץ ההגדרות - שכתוב מלא עלול לאבד server_ip או port בטעות.
    """
    lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) != 1:
        return None
    word = lines[0].lower()
    if word in TRUE_WORDS:
        return {"intercom": "on"}
    if word in FALSE_WORDS:
        return {"intercom": "off"}
    if word in QUIT_WORDS:
        return {"command": word}
    return None


def parse_config_text(text: str) -> Optional[Dict[str, str]]:
    """Parses JSON, `key = value` text, or a bare on/off/quit switch word.

    None means 'unusable, try again later'.
    """
    text = text.strip()
    if not text:
        return None

    if "=" not in text and ":" not in text and not text.startswith("{"):
        return parse_switch_word(text)

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None  # כתיבה חלקית של הקובץ
        if not isinstance(data, dict):
            return None
        raw = {str(k): str(v) for k, v in data.items()}
    else:
        raw = {}
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            for sep in ("=", ":"):
                if sep in line:
                    key, value = line.split(sep, 1)
                    raw[key.strip()] = value.strip()
                    break

    if not raw:
        return None
    return {ALIASES.get(k.strip().lower(), k.strip().lower()): v for k, v in raw.items()}


class ControlFile:
    """Polls a control file and reports the configuration when it changes.

    `optional=True` is for the one-word switch file, which is only read when
    the other program chose to create it - its absence is not worth a log line.
    """

    def __init__(self, path: str, optional: bool = False) -> None:
        self.path: str = path
        self.optional: bool = optional
        self._signature: Optional[Tuple[float, int]] = None
        self._config: Dict[str, str] = {}
        self._missing_logged: bool = False

    @property
    def config(self) -> Dict[str, str]:
        return dict(self._config)

    def ensure_template(self, defaults: Dict[str, str]) -> None:
        """Writes a commented template so the other program sees the format."""
        if os.path.exists(self.path):
            return
        lines = [
            "# Intercom control file - edit from any program, changes apply live.",
            "# intercom = on | off      open or close the call",
            "# command  = quit          shut the node down",
            "#",
            "# To toggle the call without rewriting this file, put a single word",
            "# (on / off / quit) in the switch file next to it - see --switch.",
            "",
        ]
        lines += [f"{k} = {v}" for k, v in defaults.items()]
        try:
            write_text_atomic(self.path, "\n".join(lines) + "\n")
            log(f"Created control file: {self.path}")
        except OSError as e:
            log(f"Could not create {self.path}: {e}")

    def poll(self) -> Optional[Dict[str, str]]:
        """Returns the new configuration if it changed, otherwise None."""
        try:
            stat = os.stat(self.path)
        except OSError:
            if not self._missing_logged and not self.optional:
                self._missing_logged = True
                log(f"Control file {self.path} is missing; keeping current settings.")
            self._signature = None   # קובץ שנמחק ונוצר מחדש ייקרא שוב
            return None
        self._missing_logged = False

        signature = (stat.st_mtime, stat.st_size)
        if signature == self._signature:
            return None

        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return None  # ננסה שוב בסבב הבא

        config = parse_config_text(text)
        if config is None:
            # לא שומרים חתימה: קריאה חלקית תיקרא שוב בסבב הבא
            return None

        self._signature = signature
        if config == self._config:
            return None
        self._config = config
        return dict(config)


def write_text_atomic(path: str, text: str) -> None:
    """Writes via a temp file in the same directory, then replaces in one step."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with_suppressed_unlink(tmp)
        raise


def with_suppressed_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class StatusFile:
    """Writes the node state out, skipping writes that would change nothing."""

    def __init__(self, path: str) -> None:
        self.path: str = path
        self._last: str = ""
        self._error_logged: bool = False

    def write(self, fields: Dict[str, str]) -> None:
        body = "\n".join(f"{k} = {v}" for k, v in fields.items())
        if body == self._last:
            return  # לא שוחקים את הדיסק כשאין שינוי
        stamped = f"updated = {time.strftime('%Y-%m-%d %H:%M:%S')}\n{body}\n"
        try:
            write_text_atomic(self.path, stamped)
            self._last = body
            self._error_logged = False
        except OSError as e:
            if not self._error_logged:
                self._error_logged = True
                log(f"Could not write {self.path}: {e}")
