#!/usr/bin/env python3
"""A one-button window for opening and closing the call.

    python toggle_gui.py             acts on switch_A.txt / status_A.txt
    python toggle_gui.py --role B    acts on the B files

This is an OPERATOR tool that runs on someone's desktop. The intercom node
itself stays headless - it never imports this, and nothing here runs on the
machine unless a person starts it.

Ships next to toggle_call.py and reuses it, so the switch file is written by
exactly one implementation. Copy both files together.
"""

import argparse
import os
import sys
import tkinter as tk

try:
    import toggle_call
except ImportError:
    raise SystemExit(
        "toggle_call.py must sit in the same folder as toggle_gui.py.\n"
        "Copy both files together.")

POLL_MS = 500          # אותו קצב שבו הנוד קורא את הקבצים

BG = "#1b1f23"
FG = "#e8edf2"
MUTED = "#8b98a5"
CARD = "#262b31"
GREEN = "#2ea043"
GREEN_HI = "#3fb950"
RED = "#cf3b3b"
RED_HI = "#e5484d"
GREY = "#4a525a"


def short_path(path: str, keep: int = 34) -> str:
    """Tail end of the path - the folder matters (files are per working dir),
    but a full absolute path wraps into an unreadable block."""
    full = os.path.abspath(path)
    return full if len(full) <= keep else "..." + full[-keep:]


class ToggleWindow:
    def __init__(self, root: tk.Tk, switch_path: str, status_path: str, role: str) -> None:
        self.root = root
        self.switch_path = switch_path
        self.status_path = status_path
        self.busy = False

        root.title(f"Intercom {role}")
        root.configure(bg=BG)
        root.minsize(320, 260)

        tk.Label(root, text=f"INTERCOM {role}", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10, "bold")).pack(pady=(16, 2))
        self.file_label = tk.Label(root, text=short_path(switch_path), bg=BG, fg=MUTED,
                                   font=("Consolas", 8))
        self.file_label.pack(pady=(0, 12))

        self.button = tk.Button(root, text="...", font=("Segoe UI", 22, "bold"),
                                bg=GREY, fg=FG, activeforeground=FG, relief="flat",
                                width=10, height=2, cursor="hand2", command=self.on_click)
        self.button.pack(padx=20)

        card = tk.Frame(root, bg=CARD)
        card.pack(fill="x", padx=20, pady=16, ipady=8)
        self.state_label = tk.Label(card, text="-", bg=CARD, fg=FG,
                                    font=("Segoe UI", 11, "bold"))
        self.state_label.pack()
        self.detail_label = tk.Label(card, text="", bg=CARD, fg=MUTED,
                                     font=("Consolas", 8))
        self.detail_label.pack()

        self.tick()

    # ------------------------------------------------------------------
    def current_word(self) -> str:
        return toggle_call.read_word(self.switch_path)

    def on_click(self) -> None:
        if self.busy:
            return
        target = toggle_call.OFF if self.current_word() in toggle_call.TRUE_WORDS else toggle_call.ON
        self.busy = True
        self.button.config(text="...", bg=GREY, state="disabled")
        try:
            toggle_call.write_word(self.switch_path, target)
        except OSError as e:
            self.detail_label.config(text=f"cannot write: {e}")
        # הכפתור נשאר מושבת עד שהסטטוס מאשר, כדי שלא ילחצו פעמיים באמצע
        self.root.after(POLL_MS, self.release, target)

    def release(self, target: str) -> None:
        status = toggle_call.read_status(self.status_path)
        if status.get("intercom") == target or not status:
            self.busy = False
            self.button.config(state="normal")
        else:
            self.root.after(POLL_MS, self.release, target)

    # ------------------------------------------------------------------
    def tick(self) -> None:
        status = toggle_call.read_status(self.status_path)
        wanted_on = self.current_word() in toggle_call.TRUE_WORDS

        if not self.busy:
            if wanted_on:
                self.button.config(text="ON", bg=GREEN, activebackground=GREEN_HI)
            else:
                self.button.config(text="OFF", bg=RED, activebackground=RED_HI)

        if not status:
            # אין קובץ סטטוס, או שהוא של נוד שכבר לא רץ
            self.state_label.config(text="node not running", fg=MUTED)
            self.detail_label.config(text=f"no {os.path.basename(self.status_path)}")
        else:
            state = status.get("state", "?")
            colour = GREEN_HI if state == "live" else (
                MUTED if state == "idle" else "#d29922")
            self.state_label.config(text=state, fg=colour)
            peer = status.get("peer_id") or status.get("peer_addr") or ""
            extra = []
            if peer:
                extra.append(peer)
            if status.get("remote_ready"):
                extra.append(f"remote_ready={status['remote_ready']}")
            if state == "live" and status.get("call_seconds"):
                seconds = int(status["call_seconds"] or 0)
                extra.append(f"{seconds // 60:02d}:{seconds % 60:02d}")
            self.detail_label.config(text="   ".join(extra) or status.get("updated", ""))

        self.root.after(POLL_MS, self.tick)


def main() -> int:
    p = argparse.ArgumentParser(description="One-button intercom control.")
    p.add_argument("--role", default="A", help="A or B (default A)")
    p.add_argument("--file", help="switch file path (default switch_<ROLE>.txt)")
    p.add_argument("--status", help="status file path (default status_<ROLE>.txt)")
    args = p.parse_args()

    role = args.role.strip().upper()
    root = tk.Tk()
    ToggleWindow(root,
                 args.file or f"switch_{role}.txt",
                 args.status or f"status_{role}.txt",
                 role)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
