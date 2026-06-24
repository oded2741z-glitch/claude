#!/usr/bin/env python3
"""
Simple offline version backups for ollama_chat.py -- no git required.

Backups are plain timestamped copies kept in the "backups" folder, so going
back is just copying a file. Works fully offline.

Usage:
    python backup.py save  [note]       Save a timestamped backup (optional note)
    python backup.py list               List all backups, newest first
    python backup.py restore <name|#>   Restore a backup by filename or list number
                                        (the current file is saved first, just in case)
"""

import datetime
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "ollama_chat.py")
BACKUP_DIR = os.path.join(HERE, "backups")


def _backups():
    """Return backup filenames (newest first)."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    files = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".py")]
    files.sort(reverse=True)
    return files


def save(note=""):
    if not os.path.isfile(TARGET):
        print(f"Nothing to back up - not found: {TARGET}")
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    note = re.sub(r"[^A-Za-z0-9_-]+", "-", note).strip("-")
    name = f"ollama_chat_{stamp}{('_' + note) if note else ''}.py"
    dest = os.path.join(BACKUP_DIR, name)
    shutil.copy2(TARGET, dest)
    print(f"Saved backup: backups/{name}")
    return dest


def list_backups():
    files = _backups()
    if not files:
        print("No backups yet. Create one with:  python backup.py save")
        return
    print(f"{len(files)} backup(s) in backups/  (newest first):\n")
    for i, f in enumerate(files, 1):
        size = os.path.getsize(os.path.join(BACKUP_DIR, f))
        print(f"  [{i}]  {f}   ({size:,} bytes)")


def restore(ref):
    files = _backups()
    if not files:
        print("No backups to restore from.")
        return
    name = None
    if ref.isdigit():
        idx = int(ref) - 1
        if 0 <= idx < len(files):
            name = files[idx]
    elif ref in files:
        name = ref
    if name is None:
        print(f"Backup not found: {ref}")
        print("Run 'python backup.py list' to see available backups.")
        return
    # safety: snapshot the current file before overwriting it
    if os.path.isfile(TARGET):
        save("before-restore")
    shutil.copy2(os.path.join(BACKUP_DIR, name), TARGET)
    print(f"Restored ollama_chat.py from backups/{name}")


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "save":
        save(argv[1] if len(argv) > 1 else "")
    elif cmd == "list":
        list_backups()
    elif cmd == "restore" and len(argv) > 1:
        restore(argv[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
