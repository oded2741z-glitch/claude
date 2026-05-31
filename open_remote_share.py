#!/usr/bin/env python3
"""
פותח תיקייה ב-SMB share מרוחק בתוך סייר הקבצים המקומי.

שימוש:
    python open_remote_share.py //server/share/path
    python open_remote_share.py //server/share/path -u DOMAIN\\user -p secret
    python open_remote_share.py \\\\server\\share\\path

תומך ב-Windows (Explorer), macOS (Finder) ו-Linux (gio mount + מנהל קבצים ברירת מחדל).
"""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import shutil
import subprocess
import sys
from urllib.parse import quote


def normalize_unc(path: str) -> tuple[str, str, str]:
    """מחזיר (unc_windows, server, share_path) מתוך קלט בפורמט UNC או POSIX."""
    p = path.replace("\\", "/").strip()
    while p.startswith("//"):
        p = p[1:]
    p = p.lstrip("/")
    if not p:
        raise ValueError("נתיב ריק")
    parts = p.split("/", 2)
    if len(parts) < 2:
        raise ValueError("נדרש לפחות //server/share")
    server = parts[0]
    share = parts[1]
    sub = parts[2] if len(parts) > 2 else ""
    unc = "\\\\" + server + "\\" + share + (("\\" + sub.replace("/", "\\")) if sub else "")
    posix = "/" + server + "/" + share + (("/" + sub) if sub else "")
    return unc, server, posix


def open_windows(unc: str, server: str, user: str | None, password: str | None) -> int:
    if user:
        cmd = ["net", "use", "\\\\" + server, "/user:" + user]
        if password:
            cmd.append(password)
        rc = subprocess.call(cmd)
        if rc != 0:
            print("net use נכשל", file=sys.stderr)
            return rc
    return subprocess.call(["explorer", unc])


def open_macos(server: str, posix: str, user: str | None, password: str | None) -> int:
    auth = ""
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    url = f"smb://{auth}{server}{posix[len(server) + 1:]}"
    return subprocess.call(["open", url])


def open_linux(server: str, posix: str, user: str | None, password: str | None) -> int:
    if not shutil.which("gio"):
        print("נדרש gio (GVFS). התקן: sudo apt install gvfs-bin", file=sys.stderr)
        return 1
    auth = ""
    if user:
        auth = quote(user, safe="") + "@"
    url = f"smb://{auth}{server}{posix[len(server) + 1:]}"

    mount_input = None
    if user and password:
        mount_input = f"{user}\n\n{password}\n".encode()
    rc = subprocess.run(
        ["gio", "mount", url],
        input=mount_input,
        capture_output=True,
    ).returncode
    if rc != 0:
        print(f"gio mount החזיר {rc} - ייתכן שהשיתוף כבר מחובר", file=sys.stderr)

    return subprocess.call(["xdg-open", url])


def main() -> int:
    parser = argparse.ArgumentParser(description="פתח תיקיית SMB מרוחקת בסייר הקבצים")
    parser.add_argument("path", help="נתיב UNC, למשל //server/share/folder")
    parser.add_argument("-u", "--user", help="שם משתמש (אופציונלי)")
    parser.add_argument("-p", "--password", help="סיסמה (אם חסר - יבקש)")
    parser.add_argument("--ask-pass", action="store_true", help="בקש סיסמה גם אם לא נתת -u")
    args = parser.parse_args()

    try:
        unc, server, posix = normalize_unc(args.path)
    except ValueError as exc:
        print(f"שגיאה: {exc}", file=sys.stderr)
        return 2

    password = args.password
    if args.user and password is None:
        password = getpass.getpass(f"סיסמה ל-{args.user}@{server}: ")
    elif args.ask_pass:
        password = getpass.getpass(f"סיסמה ל-{server}: ")

    system = platform.system()
    if system == "Windows":
        return open_windows(unc, server, args.user, password)
    if system == "Darwin":
        return open_macos(server, posix, args.user, password)
    if system == "Linux":
        return open_linux(server, posix, args.user, password)
    print(f"מערכת לא נתמכת: {system}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
