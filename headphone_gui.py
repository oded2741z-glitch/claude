#!/usr/bin/env python3
"""Graphical interface for the headphone connection detector.

Shows live connection status (wired / USB / Bluetooth headphones) and a log
of connect/disconnect events. Built with tkinter — no external packages.

Usage:
    python headphone_gui.py
"""

import time
import tkinter as tk
from tkinter import ttk

from headphone_detector import detect

POLL_INTERVAL_MS = 2000

CONNECTED_COLOR = "#1a7f37"
DISCONNECTED_COLOR = "#c62828"


class HeadphoneApp:
    def __init__(self, root):
        self.root = root
        root.title("גלאי אוזניות | Headphone Detector")
        root.geometry("460x420")
        root.minsize(380, 340)

        self.previous_connected = None
        self.after_id = None

        # Big status area.
        self.icon_label = tk.Label(root, text="…", font=("Segoe UI Emoji", 48))
        self.icon_label.pack(pady=(20, 0))

        self.status_label = tk.Label(root, text="בודק…", font=("Arial", 18, "bold"))
        self.status_label.pack(pady=(5, 15))

        # Connected devices list.
        devices_frame = ttk.LabelFrame(root, text="התקנים מחוברים / Connected devices")
        devices_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        self.devices_list = tk.Listbox(devices_frame, font=("Arial", 11), height=5)
        self.devices_list.pack(fill="both", expand=True, padx=8, pady=8)

        # Event log.
        log_frame = ttk.LabelFrame(root, text="יומן אירועים / Event log")
        log_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        self.log_text = tk.Text(
            log_frame, font=("Arial", 10), height=5, state="disabled"
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # Manual refresh button.
        refresh_button = ttk.Button(root, text="רענן עכשיו / Refresh", command=self.refresh)
        refresh_button.pack(pady=(0, 12))

        self.refresh()

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def refresh(self):
        # Cancel any pending poll so the manual refresh button can't stack
        # multiple polling loops.
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        try:
            devices = detect()
        except RuntimeError as error:
            self.icon_label.configure(text="⚠️")
            self.status_label.configure(text=str(error), fg="black")
            return
        connected = bool(devices)

        if connected:
            self.icon_label.configure(text="🎧")
            self.status_label.configure(
                text="אוזניות מחוברות / Connected", fg=CONNECTED_COLOR
            )
        else:
            self.icon_label.configure(text="🔇")
            self.status_label.configure(
                text="אוזניות לא מחוברות / Not connected", fg=DISCONNECTED_COLOR
            )

        self.devices_list.delete(0, "end")
        for name in devices:
            self.devices_list.insert("end", f"  {name}")

        if connected != self.previous_connected:
            if self.previous_connected is None:
                self.log("מצב התחלתי: " + ("מחוברות 🎧" if connected else "לא מחוברות 🔇"))
            elif connected:
                self.log("אוזניות חוברו 🎧 " + ", ".join(devices))
            else:
                self.log("אוזניות נותקו 🔇")
            self.previous_connected = connected

        self.after_id = self.root.after(POLL_INTERVAL_MS, self.refresh)


def main():
    root = tk.Tk()
    HeadphoneApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
