"""Tkinter user interface."""

from __future__ import annotations

import math
import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from . import audio as audiolib
from . import net
from .config import APP_NAME, SIGNALING_PORT, SUPPORTED_RATES, Settings
from .i18n import Strings
from .phone import CALLING, IDLE, IN_CALL, RINGING, Phone

VERSION = "1.0"
TICK_MS = 100
MAX_LOG_LINES = 400


class PhoneApp:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.S = Strings(self.settings.language, self.settings.rtl_fix)
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self.phone = Phone(self.settings, self._emit)
        self._log_lines: list[str] = []
        self._peers: list[dict[str, Any]] = []
        self._input_devices: list[audiolib.DeviceInfo] = []
        self._output_devices: list[audiolib.DeviceInfo] = []
        self._saved: list[dict[str, Any]] = []
        self._in_peak = 0.0
        self._out_peak = 0.0
        self._topmost_until = 0.0
        self._alive = True

        self.root = tk.Tk()
        self.root.title(self._window_title())
        self.root.minsize(760, 560)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._init_fonts()
        self.container = ttk.Frame(self.root)
        self.container.pack(fill="both", expand=True)
        self._build_ui()
        self.root.after(TICK_MS, self._tick)
        self.root.after(200, self._start_phone)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _init_fonts(self) -> None:
        family = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"
        try:
            import tkinter.font as tkfont

            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                tkfont.nametofont(name).configure(family=family, size=10)
        except Exception:  # noqa: BLE001 - fonts are cosmetic
            pass

    def _window_title(self) -> str:
        translated = self.S.raw("app_title")
        return translated if translated == APP_NAME else f"{translated} - {APP_NAME}"

    @property
    def _anchor(self) -> str:
        return "e" if self.S.is_rtl else "w"

    @property
    def _justify(self) -> str:
        return "right" if self.S.is_rtl else "left"

    @property
    def _columns(self) -> tuple[int, int]:
        """(label column, field column) - mirrored for right-to-left layout."""
        return (1, 0) if self.S.is_rtl else (0, 1)

    def _build_ui(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()
        self._build_menu()

        pad = {"padx": 8, "pady": 4}
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(2, weight=1)

        # -- header --------------------------------------------------
        header = ttk.Frame(self.container)
        header.grid(row=0, column=0, sticky="ew", **pad)
        header.columnconfigure(1, weight=1)
        name_col, ip_col = (2, 0) if self.S.is_rtl else (0, 2)

        ttk.Label(header, text=self.S("my_name")).grid(row=0, column=name_col, sticky=self._anchor)
        self.name_var = tk.StringVar(value=self.settings.display_name)
        name_entry = ttk.Entry(header, textvariable=self.name_var, width=24)
        name_entry.grid(row=0, column=1, sticky=self._anchor, padx=6)
        name_entry.bind("<FocusOut>", self._on_name_changed)
        name_entry.bind("<Return>", self._on_name_changed)

        self.ip_label = ttk.Label(header, text=f"{self.S('my_ip')} ...")
        self.ip_label.grid(row=0, column=ip_col, sticky="w" if self.S.is_rtl else "e", padx=6)

        self.status_label = ttk.Label(
            header, text=f"{self.S('status')} {self.S('state_idle')}", font=("", 11, "bold")
        )
        self.status_label.grid(row=1, column=0, columnspan=3, sticky=self._anchor, pady=(6, 0))

        # -- middle: peers + audio -----------------------------------
        middle = ttk.Frame(self.container)
        middle.grid(row=1, column=0, sticky="ew", **pad)
        middle.columnconfigure(0, weight=1, uniform="cols")
        middle.columnconfigure(1, weight=1, uniform="cols")
        left_col, right_col = (1, 0) if self.S.is_rtl else (0, 1)

        peers_box = ttk.LabelFrame(middle, text=self.S("peers_group"))
        peers_box.grid(row=0, column=left_col, sticky="nsew", padx=4)
        peers_box.columnconfigure(0, weight=1)

        list_row = ttk.Frame(peers_box)
        list_row.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        list_row.columnconfigure(0, weight=1)
        self.peer_list = tk.Listbox(list_row, height=5, exportselection=False, activestyle="none")
        self.peer_list.grid(row=0, column=0, sticky="ew")
        self.peer_list.bind("<Double-Button-1>", lambda _e: self._on_call())
        self.peer_list.bind("<<ListboxSelect>>", self._on_peer_selected)
        scroll = ttk.Scrollbar(list_row, orient="vertical", command=self.peer_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.peer_list.configure(yscrollcommand=scroll.set)

        label_col, field_col = self._columns
        ip_row = ttk.Frame(peers_box)
        ip_row.grid(row=1, column=0, sticky="ew", padx=6, pady=4)
        # label | address box | forget, mirrored for right-to-left
        addr_cols = (2, 1, 0) if self.S.is_rtl else (0, 1, 2)
        ip_row.columnconfigure(addr_cols[1], weight=1)
        ttk.Label(ip_row, text=self.S("address")).grid(row=0, column=addr_cols[0], sticky=self._anchor)
        self.ip_var = tk.StringVar(value=self.settings.last_peer_ip)
        # Editable on purpose: type a new address, or pick a saved one.
        self.ip_combo = ttk.Combobox(ip_row, textvariable=self.ip_var, width=18)
        self.ip_combo.grid(row=0, column=addr_cols[1], sticky="ew", padx=6)
        self.ip_combo.bind("<Return>", lambda _e: self._on_call())
        self.ip_combo.bind("<<ComboboxSelected>>", self._on_saved_selected)
        self.forget_btn = ttk.Button(
            ip_row, text=self.S("forget"), width=8, command=self._on_forget
        )
        self.forget_btn.grid(row=0, column=addr_cols[2], sticky="e")

        buttons = ttk.Frame(peers_box)
        buttons.grid(row=2, column=0, sticky="ew", padx=6, pady=(4, 8))
        for col in range(4):
            buttons.columnconfigure(col, weight=1)
        self.call_btn = ttk.Button(buttons, text=self.S("call"), command=self._on_call)
        self.hangup_btn = ttk.Button(buttons, text=self.S("hangup"), command=self._on_hangup)
        self.answer_btn = ttk.Button(buttons, text=self.S("answer"), command=self._on_answer)
        self.reject_btn = ttk.Button(buttons, text=self.S("reject"), command=self._on_reject)
        order = [self.call_btn, self.hangup_btn, self.answer_btn, self.reject_btn]
        if self.S.is_rtl:
            order.reverse()
        for col, btn in enumerate(order):
            btn.grid(row=0, column=col, sticky="ew", padx=2)

        audio_box = ttk.LabelFrame(middle, text=self.S("audio_group"))
        audio_box.grid(row=0, column=right_col, sticky="nsew", padx=4)
        audio_box.columnconfigure(field_col, weight=1)

        ttk.Label(audio_box, text=self.S("mic")).grid(
            row=0, column=label_col, sticky=self._anchor, padx=6, pady=3
        )
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(audio_box, textvariable=self.mic_var, state="readonly", width=30)
        self.mic_combo.grid(row=0, column=field_col, sticky="ew", padx=6, pady=3)
        self.mic_combo.bind("<<ComboboxSelected>>", self._on_mic_selected)

        ttk.Label(audio_box, text=self.S("speaker")).grid(
            row=1, column=label_col, sticky=self._anchor, padx=6, pady=3
        )
        self.out_var = tk.StringVar()
        self.out_combo = ttk.Combobox(audio_box, textvariable=self.out_var, state="readonly", width=30)
        self.out_combo.grid(row=1, column=field_col, sticky="ew", padx=6, pady=3)
        self.out_combo.bind("<<ComboboxSelected>>", self._on_out_selected)

        ttk.Button(audio_box, text=self.S("refresh_devices"), command=self._on_refresh).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2)
        )

        self.auto_pick_var = tk.BooleanVar(value=self.settings.auto_pick_new_device)
        ttk.Checkbutton(
            audio_box,
            text=self.S("auto_pick_new"),
            variable=self.auto_pick_var,
            command=self._on_auto_pick,
        ).grid(row=3, column=0, columnspan=2, sticky=self._anchor, padx=6)

        self.auto_answer_var = tk.BooleanVar(value=self.settings.auto_answer)
        ttk.Checkbutton(
            audio_box,
            text=self.S("auto_answer"),
            variable=self.auto_answer_var,
            command=self._on_auto_answer,
        ).grid(row=4, column=0, columnspan=2, sticky=self._anchor, padx=6)

        ttk.Label(audio_box, text=self.S("mic_level")).grid(
            row=5, column=label_col, sticky=self._anchor, padx=6, pady=3
        )
        self.mic_meter = ttk.Progressbar(audio_box, maximum=100)
        self.mic_meter.grid(row=5, column=field_col, sticky="ew", padx=6, pady=3)

        ttk.Label(audio_box, text=self.S("volume")).grid(
            row=6, column=label_col, sticky=self._anchor, padx=6, pady=3
        )
        self.volume_var = tk.DoubleVar(value=self.settings.volume)
        ttk.Scale(
            audio_box, from_=0.0, to=2.0, variable=self.volume_var, command=self._on_volume
        ).grid(row=6, column=field_col, sticky="ew", padx=6, pady=3)

        ttk.Label(audio_box, text=self.S("mic_gain")).grid(
            row=7, column=label_col, sticky=self._anchor, padx=6, pady=3
        )
        self.gain_var = tk.DoubleVar(value=self.settings.mic_gain)
        ttk.Scale(
            audio_box, from_=0.0, to=4.0, variable=self.gain_var, command=self._on_gain
        ).grid(row=7, column=field_col, sticky="ew", padx=6, pady=3)

        toggles = ttk.Frame(audio_box)
        toggles.grid(row=8, column=0, columnspan=2, sticky="ew", padx=6, pady=(4, 8))
        self.mute_var = tk.BooleanVar(value=False)
        self.monitor_var = tk.BooleanVar(value=False)
        mute_check = ttk.Checkbutton(
            toggles, text=self.S("mute"), variable=self.mute_var, command=self._on_mute
        )
        monitor_check = ttk.Checkbutton(
            toggles, text=self.S("monitor"), variable=self.monitor_var, command=self._on_monitor
        )
        mute_col, monitor_col = (1, 0) if self.S.is_rtl else (0, 1)
        toggles.columnconfigure(mute_col, weight=0)
        toggles.columnconfigure(monitor_col, weight=1)
        mute_check.grid(row=0, column=mute_col, sticky=self._anchor)
        monitor_check.grid(row=0, column=monitor_col, sticky=self._anchor, padx=12)

        # -- log -----------------------------------------------------
        log_box = ttk.LabelFrame(self.container, text=self.S("log_group"))
        log_box.grid(row=2, column=0, sticky="nsew", **pad)
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_box, height=10, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.tag_configure("line", justify=self._justify)

        self.stats_label = ttk.Label(self.container, text="", anchor=self._anchor)
        self.stats_label.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))

        self._render_log()
        self._render_devices()
        self._render_peers()
        self._render_saved()
        self._update_state(self.phone.state)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label=self.S("settings"), command=self._open_settings)
        lang_menu = tk.Menu(settings_menu, tearoff=0)
        lang_menu.add_command(label=self.S.visual("עברית"), command=lambda: self._set_language("he"))
        lang_menu.add_command(label="English", command=lambda: self._set_language("en"))
        settings_menu.add_cascade(label=self.S("language"), menu=lang_menu)
        menubar.add_cascade(label=self.S("settings"), menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=self.S("about"), command=self._show_about)
        menubar.add_cascade(label=self.S("help"), menu=help_menu)
        self.root.configure(menu=menubar)

    # ------------------------------------------------------------------
    # phone events
    # ------------------------------------------------------------------
    def _emit(self, kind: str, **data: Any) -> None:
        """Called from worker and audio threads - only queues."""
        self.events.put((kind, data))

    def _start_phone(self) -> None:
        try:
            self.phone.start()
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            self.root.destroy()
            return
        self.ip_label.configure(text=f"{self.S('my_ip')} {self.phone.local_ip}")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "log":
                self._append_log(self.S(data.pop("key", ""), **data))
            elif kind == "state":
                self._update_state(data.get("state", IDLE))
            elif kind == "peers":
                self._peers = data.get("peers") or []
                self._render_peers()
            elif kind == "devices":
                self._render_devices()
            elif kind == "saved_peers":
                self.settings.save()
                self._render_saved()
            elif kind == "incoming":
                self._alert_incoming()

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log_lines.append(f"{stamp}  {text}")
        del self._log_lines[:-MAX_LOG_LINES]
        self.log_text.configure(state="normal")
        self.log_text.insert("end", self._log_lines[-1] + "\n", "line")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _render_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for line in self._log_lines:
            self.log_text.insert("end", line + "\n", "line")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _render_peers(self) -> None:
        selected = self._selected_peer_key()
        # A disabled Listbox ignores delete/insert, so re-enable it first.
        self.peer_list.configure(state="normal")
        self.peer_list.delete(0, "end")
        if not self._peers:
            self.peer_list.insert("end", self.S("no_peers"))
            self.peer_list.configure(state="disabled")
            return
        for index, peer in enumerate(self._peers):
            label = f"{peer['name']}  ({peer['ip']})"
            self.peer_list.insert("end", self.S.visual(label))
            if peer["id"] == selected:
                self.peer_list.selection_set(index)

    def _selected_peer_key(self) -> str | None:
        try:
            picks = self.peer_list.curselection()
        except tk.TclError:
            return None
        if not picks or not self._peers:
            return None
        index = int(picks[0])
        if index >= len(self._peers):
            return None
        return self._peers[index]["id"]

    def _render_saved(self) -> None:
        """Fill the address dropdown from the saved list."""
        self._saved = list(self.settings.saved_peers)
        labels = []
        for peer in self._saved:
            name = peer.get("name")
            labels.append(self.S.visual(f"{peer['ip']}  ({name})") if name else peer["ip"])
        self.ip_combo.configure(values=labels)
        self.forget_btn.configure(state="normal" if self._saved else "disabled")

    def _render_devices(self) -> None:
        self._input_devices = list(self.phone.inputs)
        self._output_devices = list(self.phone.outputs)
        self.mic_combo.configure(values=[self.S.visual(d.label) for d in self._input_devices])
        self.out_combo.configure(values=[self.S.visual(d.label) for d in self._output_devices])
        if self.phone.selected_input in self._input_devices:
            self.mic_var.set(self.S.visual(self.phone.selected_input.label))
        else:
            self.mic_var.set("")
        if self.phone.selected_output in self._output_devices:
            self.out_var.set(self.S.visual(self.phone.selected_output.label))
        else:
            self.out_var.set("")

    def _update_state(self, state: str) -> None:
        labels = {
            IDLE: "state_idle",
            CALLING: "state_calling",
            RINGING: "state_ringing",
            IN_CALL: "state_in_call",
        }
        text = self.S(labels.get(state, "state_idle"))
        peer = self.phone.peer_name or self.phone.peer_ip
        if state != IDLE and peer:
            text = self.S.visual(f"{self.S.raw(labels.get(state, 'state_idle'))} - {peer}")
        self.status_label.configure(text=f"{self.S('status')} {text}")

        def enable(widget: ttk.Button, on: bool) -> None:
            widget.configure(state="normal" if on else "disabled")

        enable(self.call_btn, state == IDLE)
        enable(self.hangup_btn, state in (CALLING, IN_CALL))
        enable(self.answer_btn, state == RINGING)
        enable(self.reject_btn, state == RINGING)

    def _alert_incoming(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self._topmost_until = time.monotonic() + 2.0
            self.root.bell()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # periodic tick
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if not self._alive:
            return
        self._drain_events()
        stats = self.phone.stats()
        self._in_peak = max(stats["in_level"], self._in_peak * 0.82)
        self._out_peak = max(stats["out_level"], self._out_peak * 0.82)
        self.mic_meter.configure(value=_level_percent(self._in_peak))
        rtt = self.S.raw("rtt_unknown") if stats["rtt"] is None else f"{stats['rtt']:.0f} ms"
        self.stats_label.configure(
            text=self.S(
                "stats",
                sent=stats["sent"],
                recv=stats["recv"],
                lost=stats["lost"],
                depth=stats["depth"],
                rtt=rtt,
            )
        )
        if self._topmost_until and time.monotonic() > self._topmost_until:
            self._topmost_until = 0.0
            try:
                self.root.attributes("-topmost", False)
            except tk.TclError:
                pass
        self.root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------------
    # widget callbacks
    # ------------------------------------------------------------------
    def _on_name_changed(self, _event: Any = None) -> None:
        name = self.name_var.get().strip()
        if name and name != self.settings.display_name:
            self.settings.display_name = name
            self.settings.save()
            if self.phone.discovery is not None:
                self.phone.discovery.announce_now()

    def _on_peer_selected(self, _event: Any = None) -> None:
        key = self._selected_peer_key()
        for peer in self._peers:
            if peer["id"] == key:
                self.ip_var.set(peer["ip"])
                return

    def _on_saved_selected(self, _event: Any = None) -> None:
        """Selecting from the dropdown puts the bare address in the box."""
        index = self.ip_combo.current()
        if 0 <= index < len(self._saved):
            self.ip_var.set(self._saved[index]["ip"])

    def _on_forget(self) -> None:
        address = self.ip_var.get().strip()
        if not self.phone.forget_peer(address) and self._saved:
            # Nothing typed that matches: drop the most recent entry instead.
            self.phone.forget_peer(self._saved[0]["ip"])

    def _call_target(self) -> tuple[str, int] | None:
        key = self._selected_peer_key()
        typed = self.ip_var.get().strip()
        for peer in self._peers:
            if peer["id"] == key and (not typed or typed == peer["ip"]):
                return peer["ip"], peer["sig_port"]
        if not typed:
            return None
        for peer in self._peers:
            if peer["ip"] == typed:
                return peer["ip"], peer["sig_port"]
        # Not on the network right now: use the port saved with the address.
        return typed, self.settings.saved_port(typed, SIGNALING_PORT)

    def _on_call(self) -> None:
        if self.phone.state != IDLE:
            return
        target = self._call_target()
        if target is None:
            messagebox.showinfo(APP_NAME, self.S.raw("err_no_target"))
            return
        host, port = target
        if not net.is_valid_host(host):
            messagebox.showerror(APP_NAME, self.S.raw("err_bad_ip", ip=host))
            return
        self.settings.save()
        self.phone.place_call(host, port)

    def _on_hangup(self) -> None:
        self.phone.hangup()

    def _on_answer(self) -> None:
        self.phone.answer()

    def _on_reject(self) -> None:
        self.phone.reject()

    def _on_refresh(self) -> None:
        self.phone.refresh_devices()

    def _on_mic_selected(self, _event: Any = None) -> None:
        index = self.mic_combo.current()
        if 0 <= index < len(self._input_devices):
            self.phone.select_input(self._input_devices[index])
            self.settings.save()

    def _on_out_selected(self, _event: Any = None) -> None:
        index = self.out_combo.current()
        if 0 <= index < len(self._output_devices):
            self.phone.select_output(self._output_devices[index])
            self.settings.save()

    def _on_auto_pick(self) -> None:
        self.settings.auto_pick_new_device = bool(self.auto_pick_var.get())
        self.settings.save()

    def _on_auto_answer(self) -> None:
        self.settings.auto_answer = bool(self.auto_answer_var.get())
        self.settings.save()

    def _on_mute(self) -> None:
        self.phone.set_mute(bool(self.mute_var.get()))

    def _on_monitor(self) -> None:
        self.phone.set_monitor(bool(self.monitor_var.get()))

    def _on_volume(self, _value: Any = None) -> None:
        self.phone.set_volume(self.volume_var.get())

    def _on_gain(self, _value: Any = None) -> None:
        self.phone.set_mic_gain(self.gain_var.get())

    def _set_language(self, language: str) -> None:
        if language == self.settings.language:
            return
        self.settings.language = language
        self.settings.save()
        self.S = Strings(language, self.settings.rtl_fix)
        self.root.title(self._window_title())
        self._log_lines.clear()
        self._build_ui()
        self.ip_label.configure(text=f"{self.S('my_ip')} {self.phone.local_ip}")

    def _show_about(self) -> None:
        messagebox.showinfo(APP_NAME, self.S.raw("about_text", version=VERSION))

    # ------------------------------------------------------------------
    # settings dialog
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title(self.S.raw("settings"))
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        label_col, field_col = self._columns
        frame.columnconfigure(field_col, weight=1)

        rate_var = tk.StringVar(value=str(self.settings.wire_rate))
        frame_var = tk.StringVar(value=str(self.settings.frame_ms))
        jitter_var = tk.StringVar(value=str(self.settings.jitter_ms))
        rtl_var = tk.BooleanVar(value=self.settings.rtl_fix)

        ttk.Label(frame, text=self.S("wire_rate")).grid(
            row=0, column=label_col, sticky=self._anchor, pady=4
        )
        ttk.Combobox(
            frame,
            textvariable=rate_var,
            values=[str(rate) for rate in SUPPORTED_RATES],
            state="readonly",
            width=10,
        ).grid(row=0, column=field_col, sticky="ew", padx=8)

        ttk.Label(frame, text=self.S("frame_ms")).grid(
            row=1, column=label_col, sticky=self._anchor, pady=4
        )
        ttk.Spinbox(frame, from_=10, to=40, increment=10, textvariable=frame_var, width=8).grid(
            row=1, column=field_col, sticky="ew", padx=8
        )

        ttk.Label(frame, text=self.S("jitter_ms")).grid(
            row=2, column=label_col, sticky=self._anchor, pady=4
        )
        ttk.Spinbox(frame, from_=20, to=400, increment=20, textvariable=jitter_var, width=8).grid(
            row=2, column=field_col, sticky="ew", padx=8
        )

        ttk.Checkbutton(frame, text=self.S("rtl_fix"), variable=rtl_var).grid(
            row=3, column=0, columnspan=2, sticky=self._anchor, pady=6
        )

        def save() -> None:
            try:
                self.settings.wire_rate = int(rate_var.get())
                self.settings.frame_ms = int(frame_var.get())
                self.settings.jitter_ms = int(jitter_var.get())
            except ValueError:
                pass
            rtl_changed = bool(rtl_var.get()) != self.settings.rtl_fix
            self.settings.rtl_fix = bool(rtl_var.get())
            self.settings.__post_init__()
            self.settings.save()
            self.phone.engine.configure_wire(
                self.settings.wire_rate, self.settings.frame_ms, self.settings.jitter_ms
            )
            win.destroy()
            if rtl_changed:
                self.S = Strings(self.settings.language, self.settings.rtl_fix)
                self._build_ui()
                self.ip_label.configure(text=f"{self.S('my_ip')} {self.phone.local_ip}")
            self._append_log(self.S("log_settings_saved"))

        row = ttk.Frame(frame)
        row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(row, text=self.S("save"), command=save).pack(
            side="right" if self.S.is_rtl else "left"
        )
        ttk.Button(row, text=self.S("cancel"), command=win.destroy).pack(
            side="right" if self.S.is_rtl else "left", padx=8
        )

    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        self._alive = False
        try:
            self.root.withdraw()  # shutting the sockets down takes a moment
        except tk.TclError:
            pass
        try:
            self.settings.save()
            self.phone.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _level_percent(rms: float) -> float:
    """Map an RMS level to a 0..100 meter on a decibel-ish scale."""
    if rms <= 1e-5:
        return 0.0
    db = 20.0 * math.log10(rms)
    return max(0.0, min(100.0, (db + 55.0) / 55.0 * 100.0))


def main() -> int:
    app = PhoneApp()
    app.run()
    return 0
