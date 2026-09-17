import json
import os
import platform
import subprocess
import time
import ctypes
import tkinter as tk
from typing import Any, Dict, List, Optional, Set, Tuple

import sounddevice as sd

SAMPLE_RATE: int = 16000
CHANNELS: int = 1
DTYPE: str = 'int16'
SETTINGS_FILE: str = "settings.txt"

# Virtual / mapper / non-headset endpoints that are never a real headset.
NOISE_KEYWORDS: Tuple[str, ...] = (
    "sound mapper", "primary sound", "stereo mix", "wave out mix",
    "what u hear", "microsoft sound", "nvidia",
)
REFRESH_GAP: float = 0.3

DEFAULT_SAMPLE_RATE: int = 24000
ALLOWED_RATES: Tuple[int, ...] = (16000, 24000, 48000)
RATE_LABELS: Dict[int, str] = {
    16000: "16k  Narrow (slow internet)",
    24000: "24k  Balanced (recommended)",
    48000: "48k  Wide (fast network)",
}

DEFAULT_GAIN: float = 1.0    # mic
DEFAULT_LEVEL: float = 1.0   # speaker


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
        self.root.geometry("520x560")
        self.root.configure(bg=Theme.BG)
        self.root.resizable(False, False)

        self.before: Set[str] = set()
        self.pnp_before: Set[str] = set()
        self.pick_names: List[str] = []
        self.pick_devices: List[Dict] = []
        self.hp_device: str = ""
        self.sample_rate: int = self._load_saved_rate()
        self.gain: float = self._load_saved_float("gain", DEFAULT_GAIN)
        self.level: float = self._load_saved_float("level", DEFAULT_LEVEL)

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

        self.picker = tk.Listbox(self.root, bg=Theme.PANEL, fg=Theme.FG, font=Theme.FONT_SMALL,
                                 selectbackground=Theme.ACCENT, selectforeground="#000000",
                                 relief="flat", highlightthickness=0, height=8, activestyle="none")
        self.picker.pack(fill="x", padx=20, pady=(0, 6))
        self.picker.pack_forget()

        self.action_btn = tk.Button(self.root, text="", font=Theme.FONT_BTN,
                                    bg=Theme.ACCENT, fg="#000000", relief="flat",
                                    width=30, pady=6, activebackground="#e65c00",
                                    activeforeground="#000000", command=self._on_action)
        self.action_btn.pack(pady=10)

        # --- Audio quality ---
        self.rate_frame = tk.Frame(self.root, bg=Theme.BG)
        self.rate_frame.pack(fill="x", padx=20, pady=(4, 0))
        tk.Label(self.rate_frame, text="Audio quality (must match the other side):",
                 font=Theme.FONT_TEXT, bg=Theme.BG, fg=Theme.MUTED).pack(anchor="w")
        self.rate_var = tk.IntVar(value=self.sample_rate)
        for rate in ALLOWED_RATES:
            tk.Radiobutton(self.rate_frame, text=RATE_LABELS[rate], value=rate,
                           variable=self.rate_var, command=self._on_rate_change,
                           bg=Theme.BG, fg=Theme.FG, selectcolor=Theme.BTN_BG,
                           activebackground=Theme.BG, activeforeground=Theme.FG,
                           font=Theme.FONT_TEXT).pack(anchor="w")

        # --- Live gain / level (0-200%, 100% = unchanged) ---
        vol_frame = tk.Frame(self.root, bg=Theme.BG)
        vol_frame.pack(fill="x", padx=20, pady=(8, 0))
        self.gain_lbl = tk.Label(vol_frame, bg=Theme.BG, fg=Theme.FG, font=Theme.FONT_TEXT, anchor="w")
        self.gain_lbl.pack(fill="x")
        self.gain_scale = tk.Scale(vol_frame, from_=0, to=200, orient="horizontal",
                                   showvalue=False, command=self._on_gain,
                                   bg=Theme.BG, fg=Theme.FG, troughcolor=Theme.BTN_BG,
                                   highlightthickness=0, sliderrelief="flat",
                                   activebackground=Theme.ACCENT)
        self.gain_scale.set(int(self.gain * 100))
        self.gain_scale.pack(fill="x")

        self.level_lbl = tk.Label(vol_frame, bg=Theme.BG, fg=Theme.FG, font=Theme.FONT_TEXT, anchor="w")
        self.level_lbl.pack(fill="x", pady=(6, 0))
        self.level_scale = tk.Scale(vol_frame, from_=0, to=200, orient="horizontal",
                                    showvalue=False, command=self._on_level,
                                    bg=Theme.BG, fg=Theme.FG, troughcolor=Theme.BTN_BG,
                                    highlightthickness=0, sliderrelief="flat",
                                    activebackground=Theme.ACCENT)
        self.level_scale.set(int(self.level * 100))
        self.level_scale.pack(fill="x")
        self._update_vol_labels()

        tk.Label(self.root, text="oT", font=("Arial", 7), bg=Theme.BG,
                 fg=Theme.BTN_BG).pack(side="bottom", anchor="e", padx=10, pady=6)

        self._show_intro()

    def _load_saved_rate(self) -> int:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                rate = int(json.load(f).get("sample_rate", DEFAULT_SAMPLE_RATE))
            return rate if rate in ALLOWED_RATES else DEFAULT_SAMPLE_RATE
        except Exception:
            return DEFAULT_SAMPLE_RATE

    def _load_saved_float(self, key: str, default: float) -> float:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                v = float(json.load(f).get(key, default))
            return max(0.0, min(2.0, v))
        except Exception:
            return default

    def _update_vol_labels(self) -> None:
        self.gain_lbl.config(text=f"Microphone gain: {int(self.gain * 100)}%")
        self.level_lbl.config(text=f"Speaker level: {int(self.level * 100)}%")

    def _on_rate_change(self) -> None:
        rate = self.rate_var.get()
        self.sample_rate = rate if rate in ALLOWED_RATES else DEFAULT_SAMPLE_RATE
        # שמירה מיידית ל-settings.txt הקיים, משמרת את שאר המפתחות
        if os.path.exists(SETTINGS_FILE):
            self._write_settings()

    def _on_gain(self, value: str) -> None:
        self.gain = max(0.0, min(2.0, int(value) / 100.0))
        self._update_vol_labels()
        if os.path.exists(SETTINGS_FILE):
            self._write_settings()

    def _on_level(self, value: str) -> None:
        self.level = max(0.0, min(2.0, int(value) / 100.0))
        self._update_vol_labels()
        if os.path.exists(SETTINGS_FILE):
            self._write_settings()

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

    @staticmethod
    def _identity(name: str) -> str:
        """The device's own name: the text after the first '(', without the closing ')'.
        Windows/MME truncates names to 31 chars, so the ')' is often missing -
        'Speakers (3- USB PnP Audio Devi' -> '3- USB PnP Audio Devi'."""
        if "(" in name:
            inside = name[name.index("(") + 1:]
            if inside.endswith(")"):
                inside = inside[:-1]
            inside = inside.strip()
            if inside:
                return inside
        return name.strip()

    def _pair_keyword(self, name: str, devices: List[Dict]) -> str:
        """A keyword for the chosen device that also matches its mic/speaker twin.
        Uses the longest leading run of whole words from the device identity that
        appears in both an input-capable and an output-capable device - so it
        survives MME's 31-char truncation, which cuts the two names differently."""
        ident_words = self._words(self._identity(name))
        ins = [str(d.get("name", "")).lower() for d in devices
               if d.get("max_input_channels", 0) > 0]
        outs = [str(d.get("name", "")).lower() for d in devices
                if d.get("max_output_channels", 0) > 0]
        for take in range(len(ident_words), 0, -1):
            cand = " ".join(ident_words[:take])
            low = cand.lower()
            if any(low in n for n in ins) and any(low in n for n in outs):
                return cand
        return self._identity(name)

    def _pnp_endpoints(self) -> Set[str]:
        """Windows audio-endpoint names via PnP. Empty set on other platforms or on failure.
        This is the fallback path: it sees jack-sensed devices that PortAudio never drops."""
        if platform.system() != "Windows":
            return set()
        script = ("Get-PnpDevice -Class AudioEndpoint -Status OK | "
                  "ForEach-Object { $_.FriendlyName }")
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return set()
        if result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _map_pnp_keyword(self, pnp_name: str) -> str:
        """Turn a PnP endpoint name into a keyword that also matches a sounddevice name,
        so hp_device works with the client. Falls back to the PnP identity itself."""
        sd_names = [str(d.get("name", "")).lower() for d in self._devices()]
        w = self._words(pnp_name)
        for length in range(len(w), 0, -1):
            for start in range(0, len(w) - length + 1):
                cand = " ".join(w[start:start + length])
                if any(cand.lower() in n for n in sd_names):
                    return cand
        return self._identity(pnp_name)

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
        self.pnp_before = self._pnp_endpoints()
        self.step_lbl.config(text="Step 2 of 2")
        self.info_lbl.config(
            text="Now UNPLUG the headset (or turn it off).\n\n"
                 "Wait two seconds, then click Detect.")
        self.action_btn.config(text="Detect")

    def _detect(self) -> None:
        after = self._snapshot()
        removed = self.before - after
        if not removed:
            self._detect_fallback()
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

    def _detect_fallback(self) -> None:
        """PortAudio saw no change. Try the Windows PnP diff, then a manual pick."""
        pnp_removed = self._pnp_endpoints_removed()
        if pnp_removed:
            keyword = ""
            for name in pnp_removed:
                keyword = self._map_pnp_keyword(name)
                if keyword:
                    break
            if keyword:
                self.hp_device = keyword
                detail = "\n".join(f"  - {n}" for n in sorted(pnp_removed))
                self.result_lbl.config(
                    text=f'Detected via Windows (PnP) -> hp_device = "{keyword}"\n\n'
                         f'Endpoints that disappeared:\n{detail}\n\n'
                         f'Note: a jack device may still not report unplug at runtime, '
                         f'but the client will use the right device.')
                self.result_lbl.pack(fill="x", padx=20, pady=14, ipadx=8, ipady=8)
                self.info_lbl.config(text="Plug the headset back in, then click Save.")
                self.step_lbl.config(text="Done")
                self.state = "save"
                self.action_btn.config(text="Save settings.txt")
                return
        self._show_pick()

    def _pnp_endpoints_removed(self) -> Set[str]:
        if not self.pnp_before:
            return set()
        return self.pnp_before - self._pnp_endpoints()

    def _show_pick(self) -> None:
        """Last resort: list current devices and let the user choose the headset."""
        self.state = "pick"
        self.result_lbl.pack_forget()
        self.pick_devices = self._devices()
        self.pick_names = []
        self.picker.delete(0, "end")
        seen: Set[str] = set()
        for d in self.pick_devices:
            name = str(d.get("name", ""))
            if any(k in name.lower() for k in NOISE_KEYWORDS):
                continue  # מיפוי/מיקסר/מסך - לעולם לא אוזניות
            ident = self._identity(name)
            if not ident or ident in seen:
                continue
            seen.add(ident)
            io = f"in{d.get('max_input_channels',0)}/out{d.get('max_output_channels',0)}"
            self.pick_names.append(name)
            self.picker.insert("end", f"  {ident}    [{io}]")
        self.step_lbl.config(text="Manual selection")
        self.info_lbl.config(
            text="Automatic detection did not find the headset. Plug it back in, "
                 "then pick it from the list below and click Use selected.")
        self.picker.pack(fill="x", padx=20, pady=(0, 6))
        self.action_btn.config(text="Use selected")

    def _use_selected(self) -> None:
        sel = self.picker.curselection()
        if not sel:
            self.info_lbl.config(text="Select a device from the list first.")
            return
        self.hp_device = self._pair_keyword(self.pick_names[sel[0]], self.pick_devices)
        self.picker.pack_forget()
        self.result_lbl.config(text=f'Selected -> hp_device = "{self.hp_device}"')
        self.result_lbl.pack(fill="x", padx=20, pady=14, ipadx=8, ipady=8)
        self.info_lbl.config(text="Click Save to write settings.txt.")
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

    def _write_settings(self) -> Optional[Dict[str, Any]]:
        """Writes hp_device + sample_rate, preserving ip/port/my_id when present."""
        data: Dict[str, Any] = {"ip": "192.168.1.11", "port": "9999",
                                "my_id": "node_B", "hp_device": self.hp_device,
                                "sample_rate": self.sample_rate,
                                "gain": self.gain, "level": self.level}
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                for k in ("ip", "port", "my_id"):
                    if k in existing:
                        data[k] = str(existing[k])
                if not self.hp_device and "hp_device" in existing:
                    data["hp_device"] = str(existing["hp_device"])
            except Exception:
                pass
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError:
            return None
        return data

    def _save(self) -> None:
        data = self._write_settings()
        if data is None:
            self.info_lbl.config(text=f"Could not write {SETTINGS_FILE}", fg=Theme.QUIT)
            return

        path = os.path.abspath(SETTINGS_FILE)
        self.result_lbl.config(
            text=f'Saved:\n{path}\n\nip={data["ip"]}  port={data["port"]}\n'
                 f'my_id={data["my_id"]}\nhp_device="{data["hp_device"]}"\n'
                 f'sample_rate={data["sample_rate"]}  '
                 f'gain={int(data["gain"]*100)}%  level={int(data["level"]*100)}%')
        self.info_lbl.config(text="Setup complete. You can close this window and start the client.")
        self.step_lbl.config(text="Saved")
        self.state = "close"
        self.action_btn.config(text="Close", bg=Theme.BTN_BG, fg=Theme.FG)

    def _on_action(self) -> None:
        if self.state == "intro":
            self._show_unplug()
        elif self.state == "unplug":
            self._detect()
        elif self.state == "pick":
            self._use_selected()
        elif self.state == "save":
            self._save()
        elif self.state == "close":
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    SetupWizard(root)
    root.mainloop()
