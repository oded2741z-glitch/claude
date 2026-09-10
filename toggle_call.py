#!/usr/bin/env python3
"""Flip the intercom call on or off from another program or a shortcut.

    python toggle_call.py            on <-> off, whichever it is now
    python toggle_call.py off        force it off
    python toggle_call.py on         force it on
    python toggle_call.py quit       shut the node down
    python toggle_call.py --role B   act on switch_B.txt / status_B.txt

Deliberately standalone - it imports nothing from this project, because the
machine that runs the intercom holds a single file (intercom_A.py or the
built .exe) and nothing else.

After writing, it waits for the node to pick the change up and reports the
state it settled on, so a script can tell success from "nobody is listening".
Exit code 0 means the node confirmed; 1 means it never did.
"""

import argparse
import os
import sys
import tempfile
import time

ON, OFF, QUIT = "on", "off", "quit"
TRUE_WORDS = {"1", "on", "true", "yes", "y", "start", "up", "enable", "enabled"}
FALSE_WORDS = {"0", "off", "false", "no", "n", "stop", "down", "disable", "disabled"}
QUIT_WORDS = {"quit", "exit", "shutdown"}

CONFIRM_TIMEOUT = 5.0     # הנוד קורא כל חצי שנייה; זה מרווח נדיב
CONFIRM_POLL = 0.2


def read_word(path: str) -> str:
    """The switch file's current word, or '' when it is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            return line
    return ""


def write_word(path: str, word: str) -> None:
    """Atomic write, so the node never reads a half-written file."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".switch_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(word + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_status(path: str) -> dict:
    fields = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key.strip()] = value.strip()
    except OSError:
        pass
    return fields


def wait_for(status_path: str, expected: str, timeout: float) -> dict:
    """Waits until the node's status file agrees with what we asked for."""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = read_status(status_path)
        if last.get("intercom") == expected:
            return last
        time.sleep(CONFIRM_POLL)
    return last


def resolve(requested: str, current: str) -> str:
    if requested:
        word = requested.strip().lower()
        if word in TRUE_WORDS:
            return ON
        if word in FALSE_WORDS:
            return OFF
        if word in QUIT_WORDS:
            return QUIT
        raise SystemExit(f"Unknown command {requested!r}. Use on, off or quit.")
    # בלי ארגומנט זה מתג: כל מה שאינו 'on' נחשב כבוי, כולל קובץ חסר
    return OFF if current in TRUE_WORDS else ON


def main() -> int:
    p = argparse.ArgumentParser(description="Turn the intercom call on or off.")
    p.add_argument("command", nargs="?", default="",
                   help="on, off or quit. Omit to toggle whatever it is now.")
    p.add_argument("--role", default="A", help="A or B (default A)")
    p.add_argument("--file", help="switch file path (default switch_<ROLE>.txt)")
    p.add_argument("--status", help="status file path (default status_<ROLE>.txt)")
    p.add_argument("--no-wait", action="store_true",
                   help="write and exit without waiting for the node to confirm")
    args = p.parse_args()

    role = args.role.strip().upper()
    switch_path = args.file or f"switch_{role}.txt"
    status_path = args.status or f"status_{role}.txt"

    current = read_word(switch_path)
    target = resolve(args.command, current)

    write_word(switch_path, target)
    print(f"{switch_path}: {current or '(empty)'} -> {target}")

    if args.no_wait or target == QUIT:
        return 0

    status = wait_for(status_path, target, CONFIRM_TIMEOUT)
    if status.get("intercom") != target:
        print(f"  the node did not confirm within {CONFIRM_TIMEOUT:.0f}s - "
              f"is it running, and is {status_path} in this folder?", file=sys.stderr)
        return 1

    state = status.get("state", "?")
    peer = status.get("peer_id") or status.get("peer_addr") or ""
    print(f"  node confirmed: state = {state}" + (f"  ({peer})" if peer else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
