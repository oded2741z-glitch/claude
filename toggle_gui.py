#!/usr/bin/env python3
"""One button that flips switch_A.txt between on and off.

Put this file in the intercom_A folder (next to switch_A.txt) and run it:

    python toggle_gui.py

The file is found next to this script, not in the current directory, so a
desktop shortcut works from anywhere. To point somewhere else, edit
SWITCH_FILE below or pass a path:  python toggle_gui.py C:\\intercom_A\\switch_A.txt
"""

import os
import sys
import tkinter as tk

SWITCH_FILE = "switch_A.txt"      # relative = next to this script; a full path works too
REFRESH_MS = 1000                 # pick up changes made by someone else


def resolve_path() -> str:
    target = sys.argv[1] if len(sys.argv) > 1 else SWITCH_FILE
    if os.path.isabs(target):
        return target
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), target)


def read_state(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "on" if f.read().strip().lower() == "on" else "off"
    except OSError:
        return "off"                  # קובץ חסר נחשב כבוי; הלחיצה תיצור אותו


def write_state(path: str, state: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(state + "\n")
    os.replace(tmp, path)             # החלפה אטומית - הנוד לא יקרא חצי קובץ


def main() -> None:
    path = resolve_path()

    root = tk.Tk()
    root.title("switch_A.txt")
    root.configure(bg="#1b1f23")
    root.resizable(False, False)

    button = tk.Button(root, font=("Segoe UI", 28, "bold"), fg="white",
                       relief="flat", width=8, height=2, cursor="hand2")
    button.pack(padx=24, pady=(24, 8))
    shown = path if len(path) <= 44 else "..." + path[-44:]   # לא למתוח את החלון
    tk.Label(root, text=shown, bg="#1b1f23", fg="#8b98a5",
             font=("Consolas", 8)).pack(padx=16, pady=(0, 16))

    def show(state: str) -> None:
        if state == "on":
            button.config(text="ON", bg="#2ea043", activebackground="#3fb950")
        else:
            button.config(text="OFF", bg="#cf3b3b", activebackground="#e5484d")

    def toggle() -> None:
        new_state = "off" if read_state(path) == "on" else "on"
        try:
            write_state(path, new_state)
        except OSError as e:
            button.config(text="ERR", bg="#4a525a")
            root.title(f"cannot write: {e}")
            return
        show(new_state)

    def refresh() -> None:
        show(read_state(path))
        root.after(REFRESH_MS, refresh)

    button.config(command=toggle)
    refresh()
    root.mainloop()


if __name__ == "__main__":
    main()
