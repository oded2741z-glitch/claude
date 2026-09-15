import json
import os
import time
import ctypes
import tkinter as tk
from typing import Dict, List, Optional, Set, Tuple

import sounddevice as sd

SAMPLE_RATE: int = 16000
CHANNELS: int = 1
DTYPE: str = 'int16'
SETTINGS_FILE: str = "settings.txt"
REFRESH_GAP: float = 0.3


class Theme:
    BG: str = "#121212"
    FG: str = "#FFFFFF"
    BTN_BG: str = "#333333"
    ACCENT: str = "#ff6600"
    QUIT: str = "#ff0000"
    MUTED: str = "#888888"
    PANEL: str = "#1e1e1e"
    DIVIDER: str = "#121212"

    FONT_TITLE: Tuple[str, int, str] = ("Consolas", 13, "bold")
    FONT_STEP: Tuple[str, int, str] = ("Arial", 11, "bold")
    FONT_TEXT: Tuple[str, int] = ("Arial", 10)
    FONT_SMALL: Tuple[str, int] = ("Consolas", 9)
    FONT_BTN: Tuple[str, int, str] = ("Arial", 10, "bold")


class SetupWizard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Intercom Setup")
        self.root.geometry("520x420")
        self.root.configure(bg=Theme.BG)
        self.root.resizable(False, False)

        self.before: Set[str] = set()
        self.hp_device: str = ""

        self._build()
        self._force_dark_titlebar()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def _force_dark_titlebar(self) -> None:
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            for attr in (20, 19):
                val = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    def _build(self) -> None:
        tk.Label(self.root, text="Intercom Setup", font=Theme.FONT_TITLE,
                 bg=Theme.BG, fg=Theme.ACCENT).pack(anchor="w", padx=20, pady=(18, 2))
        tk.Label(self.root, text="Detect your headset and create settings.txt automatically.",
                 font=Theme.FONT_TEXT, bg=Theme.BG, fg=Theme.MUTED).pack(anchor="w", padx=20)

        tk.Frame(self.root, bg=Theme.BTN_BG, height=1).pack(fill="x", padx=20, pady=14)

        self.step_lbl = tk.Label(self.root, text="", font=Theme.FONT_STEP,
                                 bg=Theme.BG, fg=Theme.FG, justify="left", anchor="w",
                                 wraplength=470)
        self.step_lbl.pack(fill="x", padx=20)

        self.info_lbl = tk.Label(self.root, text="", font=Theme.FONT_TEXT,
                                 bg=Theme.BG, fg=Theme.MUTED, justify="left", anchor="w",
                                 wraplength=470)
        self.info_lbl.pack(fill="x", padx=20, pady=(8, 0))

        self.result_lbl = tk.Label(self.root, text="", font=Theme.FONT_SMALL,
                                   bg=Theme.PANEL, fg=Theme.ACCENT, justify="left", anchor="w",
                                   wraplength=460)
        self.result_lbl.pack(fill="x", padx=20, pady=14, ipadx=8, ipady=8)
        self.result_lbl.pack_forget()

        self.action_btn = tk.Button(self.root, text="", font=Theme.FONT_BTN,
                                    bg=Theme.ACCENT, fg="#000000", relief="flat",
                                    width=30, pady=6, activebackground="#e65c00",
                                    activeforeground="#000000", command=self._on_action)
        self.action_btn.pack(pady=10)

        tk.Label(self.root, text="oT", font=("Arial", 7), bg=Theme.BG,
                 fg=Theme.BTN_BG).pack(side="bottom", anchor="e", padx=10, pady=6)

        self._show_intro()

    # ------------------------------------------------------------------
    def _devices(self) -> List[Dict]:
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        try:
            return [dict(d) for d in sd.query_devices()]
        except Exception:
            return []

    @staticmethod
    def _key(d: Dict) -> str:
        return f"{d.get('name','')}|in{d.get('max_input_channels',0)}|out{d.get('max_output_channels',0)}"

    def _snapshot(self) -> Set[str]:
        return {self._key(d) for d in self._devices()}

    @staticmethod
    def _words(name: str) -> List[str]:
        for ch in "()[]":
            name = name.replace(ch, " ")
        return name.split()

    # ------------------------------------------------------------------
    def _show_intro(self) -> None:
        self.state = "intro"
        self.step_lbl.config(text="Step 1 of 2")
        self.info_lbl.config(
            text="Make sure your headset is CONNECTED now.\n\n"
                 "When ready, click Start. You will then be asked to unplug it so the "
                 "tool can identify which device it is.")
        self.result_lbl.pack_forget()
        self.action_btn.config(text="Start", state="normal", bg=Theme.ACCENT, fg="#000000")

    def _show_unplug(self) -> None:
        self.state = "unplug"
        self.before = self._snapshot()
        self.step_lbl.config(text="Step 2 of 2")
        self.info_lbl.config(
            text="Now UNPLUG the headset (or turn it off).\n\n"
                 "Wait two seconds, then click Detect.")
        self.action_btn.config(text="Detect")

    def _detect(self) -> None:
        after = self._snapshot()
        removed = self.before - after
        if not removed:
            self.info_lbl.config(
                text="No change detected. The headset endpoint did not disappear.\n\n"
                     "Make sure you actually unplugged it, then click Detect again. "
                     "Some built-in jacks never disappear - such a device cannot be "
                     "tracked this way.")
            return

        names = [entry.split("|")[0] for entry in removed]
        survivors = [entry.split("|")[0] for entry in after]
        has_in = any("|in" in e and not e.split("|in")[1].startswith("0") for e in removed)
        has_out = any("out0" not in e for e in removed)

        keyword = self._pick_keyword(names, survivors)
        self.hp_device = keyword

        detail = "\n".join(f"  - {n}" for n in sorted(set(names)))
        note = ""
        if not (has_in and has_out):
            note = ("\n\nNote: only one direction disappeared, but the matching keyword "
                    "should still cover the mic and speaker pair.")
        self.result_lbl.config(
            text=f'Detected headset -> hp_device = "{keyword}"\n\nDevices that disappeared:\n{detail}{note}')
        self.result_lbl.pack(fill="x", padx=20, pady=14, ipadx=8, ipady=8)
        self.info_lbl.config(text="Plug the headset back in, then click Save.")
        self.step_lbl.config(text="Done")
        self.state = "save"
        self.action_btn.config(text="Save settings.txt")

    def _pick_keyword(self, names: List[str], survivors: List[str]) -> str:
        """Longest contiguous word run shared by all removed devices and, if possible,
        absent from every surviving device - so the keyword tracks the headset only."""
        names = [n for n in names if n.strip()]
        if not names:
            return ""
        # כל רצפי המילים הרציפים של ההתקן הראשון, מהארוך לקצר
        w = self._words(names[0])
        cands: List[str] = []
        for length in range(len(w), 0, -1):
            for start in range(0, len(w) - length + 1):
                cands.append(" ".join(w[start:start + length]))

        lower_names = [n.lower() for n in names]
        lower_surv = [s.lower() for s in survivors]

        shared = [c for c in cands if all(c.lower() in n for n in lower_names)]
        # מעדיפים מילה שלא מופיעה באף התקן ששרד, אחרת נתפוס גם אותם
        distinct = [c for c in shared if not any(c.lower() in s for s in lower_surv)]
        if distinct:
            return distinct[0]
        if shared:
            return shared[0]
        return names[0].split("(")[0].strip() or names[0].strip()

    def _save(self) -> None:
        data: Dict[str, str] = {"ip": "192.168.1.11", "port": "9999",
                                "my_id": "node_B", "hp_device": self.hp_device}
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                for k in ("ip", "port", "my_id"):
                    if k in existing:
                        data[k] = str(existing[k])
            except Exception:
                pass
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            self.info_lbl.config(text=f"Could not write {SETTINGS_FILE}: {e}", fg=Theme.QUIT)
            return

        path = os.path.abspath(SETTINGS_FILE)
        self.result_lbl.config(
            text=f'Saved:\n{path}\n\nip={data["ip"]}  port={data["port"]}\n'
                 f'my_id={data["my_id"]}\nhp_device="{data["hp_device"]}"')
        self.info_lbl.config(text="Setup complete. You can close this window and start the client.")
        self.step_lbl.config(text="Saved")
        self.state = "close"
        self.action_btn.config(text="Close", bg=Theme.BTN_BG, fg=Theme.FG)

    def _on_action(self) -> None:
        if self.state == "intro":
            self._show_unplug()
        elif self.state == "unplug":
            self._detect()
        elif self.state == "save":
            self._save()
        elif self.state == "close":
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    SetupWizard(root)
    root.mainloop()
