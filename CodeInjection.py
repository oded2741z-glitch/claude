import tkinter as tk
from tkinterdnd2 import DND_FILES, TkinterDnD
import os
import shutil
import subprocess
import keyboard
from datetime import datetime
import time
import webbrowser
import ctypes

COLOR_BG = "#121212"
COLOR_ACCENT = "#FF6600"
COLOR_BTN = "#333333"
COLOR_QUIT = "#FF0000"
COLOR_TEXT = "#FFFFFF"

class CodeInjectionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Code Injection")
        self.root.geometry("440x205")
        self.root.configure(bg=COLOR_BG)

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.target_file = None
        self.armed_until = 0.0
        self.arm_seq = 0
        self.shift_was_down = False
        self.is_visible = True

        self.init_ui()
        self.setup_hotkeys()
        self.position_window()
        self.monitor_clipboard()
        self.require_consent()

    def consent_file(self):
        return os.path.join(os.path.expanduser("~"), ".code_injection_accepted")

    def require_consent(self):
        try:
            if os.path.exists(self.consent_file()):
                return
        except Exception:
            return
        self.root.attributes("-alpha", 0.0)
        self.show_consent()

    def accept_consent(self, win, agreed):
        if not agreed.get():
            return
        try:
            with open(self.consent_file(), "w", encoding="utf-8") as f:
                f.write("accepted")
        except Exception:
            pass
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()
        self.root.attributes("-alpha", 1.0)
        self.root.attributes("-topmost", True)

    def decline_consent(self):
        self.root.destroy()

    def show_consent(self):
        win = tk.Toplevel(self.root)
        win.title("Terms of Use")
        win.configure(bg=COLOR_BG)
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        width, height = 500, 560
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = int((sw - width) / 2)
        y = int((sh - height) / 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

        outer = tk.Frame(win, bg=COLOR_ACCENT)
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        container = tk.Frame(outer, bg=COLOR_BG)
        container.pack(fill="both", expand=True)

        header = tk.Frame(container, bg=COLOR_BG)
        header.pack(fill="x", padx=16, pady=12)
        tk.Label(header, text="CODE INJECTION", fg=COLOR_ACCENT, bg=COLOR_BG,
                 font=("Consolas", 13, "bold")).pack(side="left")
        tk.Label(header, text="TERMS OF USE", fg="#888", bg=COLOR_BG,
                 font=("Consolas", 9)).pack(side="left", padx=6, pady=4)

        tk.Frame(container, bg=COLOR_ACCENT, height=2).pack(fill="x", padx=16)

        agreed = tk.BooleanVar(master=win, value=False)

        footer = tk.Frame(container, bg=COLOR_BG)
        footer.pack(side="bottom", fill="x", padx=16, pady=12)
        tk.Frame(container, bg=COLOR_ACCENT, height=2).pack(side="bottom", fill="x", padx=16)

        continue_btn = tk.Button(footer, text="Continue", state="disabled",
                                 bg=COLOR_BTN, fg=COLOR_TEXT, relief="flat",
                                 font=("Consolas", 9, "bold"), width=14, cursor="hand2",
                                 command=lambda: self.accept_consent(win, agreed))
        continue_btn.pack(side="right")
        tk.Button(footer, text="Decline & Exit", command=self.decline_consent, bg=COLOR_BTN,
                  fg=COLOR_TEXT, relief="flat", font=("Consolas", 9, "bold"),
                  width=14, cursor="hand2").pack(side="left")

        def toggle_continue(*args):
            if agreed.get():
                continue_btn.config(state="normal", bg=COLOR_ACCENT, fg="black")
            else:
                continue_btn.config(state="disabled", bg=COLOR_BTN, fg=COLOR_TEXT)
        agreed.trace_add("write", toggle_continue)

        body_wrap = tk.Frame(container, bg=COLOR_BG)
        body_wrap.pack(fill="both", expand=True, padx=16, pady=10)

        canvas = tk.Canvas(body_wrap, bg=COLOR_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(body_wrap, orient="vertical", command=canvas.yview,
                                 bg=COLOR_BTN, troughcolor="#1a1a1a", activebackground=COLOR_ACCENT,
                                 width=10, bd=0, relief="flat", highlightthickness=0)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg=COLOR_BG)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(body_id, width=e.width))
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        terms = [
            "Please read these terms before using Code Injection.",
            "",
            "Code Injection writes the clipboard into your target file",
            "and can overwrite its entire contents. A .txt backup is",
            "saved before each injection, but you remain responsible",
            "for your own files.",
            "",
            "(c) 2026 ToClipsKit",
            "Free to use for any purpose, personal or commercial.",
            "",
            "This software is provided \"as is\", without warranty of",
            "any kind. You use it at your own risk. The author is not",
            "liable for any damage, data loss, or issues arising from",
            "its use.",
            "",
            "Always keep backups of important files before injecting.",
            "",
            "By continuing you confirm that you have read and accept",
            "these terms. If you do not agree, choose Decline & Exit",
            "and the app will close.",
        ]
        for line in terms:
            tk.Label(body, text=line, fg=COLOR_TEXT, bg=COLOR_BG,
                     font=("Consolas", 9), anchor="w", justify="left").pack(fill="x")

        tk.Frame(body, bg="#333333", height=1).pack(fill="x", pady=10)
        tk.Checkbutton(body, text="I have read and agree to the terms",
                       variable=agreed, selectcolor="#444444",
                       fg=COLOR_ACCENT, bg=COLOR_BG,
                       activeforeground=COLOR_ACCENT, activebackground=COLOR_BG,
                       font=("Consolas", 9, "bold"), anchor="w").pack(fill="x", pady=(0, 2))
        tk.Label(body, text="Then press Continue below.", fg="#888", bg=COLOR_BG,
                 font=("Consolas", 8), anchor="w").pack(fill="x", pady=(0, 6))

        def lock_focus():
            try:
                win.focus_force()
                win.grab_set()
            except Exception:
                pass
        win.after(250, lock_focus)


    def init_ui(self):
        top_bar = tk.Frame(self.root, bg=COLOR_BG)
        top_bar.pack(fill="x", padx=10, pady=5)

        self.create_btn(top_bar, "Run", self.run_file).pack(side="left", padx=2)
        self.create_btn(top_bar, "CMD", self.run_cmd).pack(side="left", padx=2)
        self.create_btn(top_bar, "Build", self.build_target).pack(side="left", padx=2)

        tk.Label(top_bar, text="Code Injection", fg=COLOR_ACCENT, bg=COLOR_BG,
                 font=("Consolas", 11, "bold")).pack(side="left", expand=True)

        self.create_btn(top_bar, "Help", self.show_help).pack(side="left", padx=2)
        self.create_btn(top_bar, "Quit", self.root.quit, is_quit=True).pack(side="left", padx=2)

        info_frame = tk.Frame(self.root, bg=COLOR_BG)
        info_frame.pack(fill="x", padx=12)

        self.lbl_target = tk.Label(info_frame, text="Target: None", fg=COLOR_TEXT,
                                   bg=COLOR_BG, font=("Consolas", 8, "bold"))
        self.lbl_target.pack(side="left")

        self.lbl_mod = tk.Label(info_frame, text="Mod: --", fg="#888",
                                bg=COLOR_BG, font=("Consolas", 8))
        self.lbl_mod.pack(side="right")

        self.progress_canvas = tk.Canvas(self.root, height=3, bg="#1a1a1a", highlightthickness=0)
        self.progress_canvas.pack(fill="x", padx=10, pady=2)
        self.progress_rect = self.progress_canvas.create_rectangle(0, 0, 0, 3, fill=COLOR_ACCENT, width=0)

        self.drop_group = tk.LabelFrame(self.root, text="Target File", fg=COLOR_TEXT,
                                       bg=COLOR_BG, font=("Consolas", 8), bd=1, relief="solid")
        self.drop_group.pack(fill="both", expand=True, padx=10, pady=5)

        self.drop_zone = tk.Label(self.drop_group, text="DROP TARGET HERE",
                                  fg=COLOR_ACCENT, bg="#1E1E1E",
                                  font=("Consolas", 10, "bold"),
                                  cursor="hand2", pady=10)
        self.drop_zone.pack(fill="both", expand=True, padx=5, pady=5)

        self.drop_zone.drop_target_register(DND_FILES)
        self.drop_zone.dnd_bind('<<Drop>>', self.handle_drop)

        bottom_bar = tk.Frame(self.root, bg=COLOR_BG)
        bottom_bar.pack(fill="x", padx=10, pady=2)

        self.chk_inject = tk.BooleanVar(value=True)
        tk.Checkbutton(bottom_bar, text="Shift+Copy Inject", variable=self.chk_inject,
                       fg=COLOR_TEXT, bg=COLOR_BG, selectcolor=COLOR_BG,
                       activeforeground=COLOR_ACCENT, font=("Consolas", 8)).pack(side="left")

        self.chk_ontop = tk.BooleanVar(value=True)
        tk.Checkbutton(bottom_bar, text="Stay On Top", variable=self.chk_ontop,
                       fg=COLOR_TEXT, bg=COLOR_BG, selectcolor=COLOR_BG,
                       command=self.update_ontop, font=("Consolas", 8)).pack(side="left", padx=5)

        self.chk_ghost = tk.BooleanVar(value=False)
        tk.Checkbutton(bottom_bar, text="Ghost", variable=self.chk_ghost,
                       fg=COLOR_TEXT, bg=COLOR_BG, selectcolor=COLOR_BG,
                       font=("Consolas", 8)).pack(side="left")

        tk.Button(bottom_bar, text="...", fg="#888", bg=COLOR_BG, relief="flat",
                  font=("Consolas", 8, "bold"), command=self.restore_and_fix).pack(side="right")

        self.sig = tk.Label(self.root, text="oT", fg="#222", bg=COLOR_BG, font=("Arial", 7))
        self.sig.place(relx=1.0, rely=1.0, anchor="se")

        top_bar.bind("<Button-1>", self.start_move)
        top_bar.bind("<B1-Motion>", self.do_move)
        self.root.bind("<Enter>", self.handle_enter)
        self.root.bind("<Leave>", self.handle_leave)

    def unique_path(self, base, ext):
        candidate = base + ext
        if not os.path.exists(candidate):
            return candidate
        index = 1
        while True:
            candidate = f"{base}_{index}{ext}"
            if not os.path.exists(candidate):
                return candidate
            index += 1

    def handle_drop(self, event):
        path = event.data.strip('{}')
        if path:
            if path.lower().endswith('.txt'):
                base = path.rsplit('.', 1)[0]
                new_path = self.unique_path(base, ".py")
                try:
                    os.rename(path, new_path)
                    path = new_path
                except Exception:
                    pass

            self.target_file = path
            name = os.path.basename(path)
            self.lbl_target.config(text=f"Target: {name}")
            self.lbl_mod.config(text=f"Mod: {datetime.now().strftime('%H:%M:%S')}")
            self.drop_zone.config(bg=COLOR_ACCENT, fg="black", text=name)

    def animate_progress(self):
        def fill(w):
            if w <= 440:
                self.progress_canvas.coords(self.progress_rect, 0, 0, w, 3)
                self.root.after(5, lambda: fill(w + 40))
            else:
                self.root.after(300, lambda: self.progress_canvas.coords(self.progress_rect, 0, 0, 0, 3))
        fill(0)

    def create_btn(self, parent, text, cmd, is_quit=False):
        color = COLOR_QUIT if is_quit else COLOR_BTN
        return tk.Button(parent, text=text, command=cmd, bg=color, fg=COLOR_TEXT,
                         relief="flat", font=("Consolas", 8, "bold"), width=6)

    def handle_enter(self, event):
        self.root.attributes("-alpha", 1.0)

    def handle_leave(self, event):
        x, y = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(x, y)
        if widget is None:
            if self.chk_ghost.get():
                self.root.attributes("-alpha", 0.5)
        else:
            self.root.attributes("-alpha", 1.0)

    def update_ontop(self):
        self.root.attributes("-topmost", self.chk_ontop.get())

    def position_window(self):
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(f"+10+{screen_h - 250}")

    def start_move(self, event):
        self.x, self.y = event.x, event.y

    def do_move(self, event):
        deltax, deltay = event.x - self.x, event.y - self.y
        self.root.geometry(f"+{self.root.winfo_x() + deltax}+{self.root.winfo_y() + deltay}")

    def setup_hotkeys(self):
        try:
            keyboard.unhook_all()
            keyboard.add_hotkey('f4', self.toggle_visibility)
        except Exception:
            pass

    def toggle_visibility(self):
        if self.is_visible:
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 1.0)
        self.is_visible = not self.is_visible

    def shift_pressed(self):
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
        except Exception:
            return False

    def clipboard_seq(self):
        try:
            return ctypes.windll.user32.GetClipboardSequenceNumber()
        except Exception:
            return 0

    def monitor_clipboard(self):
        if self.chk_inject.get():
            shift_down = self.shift_pressed()

            if shift_down and not self.shift_was_down:
                self.arm_seq = self.clipboard_seq()
            if shift_down:
                self.armed_until = time.time() + 0.6
            self.shift_was_down = shift_down

            if time.time() <= self.armed_until:
                seq = self.clipboard_seq()
                if seq != self.arm_seq:
                    try:
                        clip = self.root.clipboard_get()
                        if clip:
                            self.inject_code(clip)
                            self.arm_seq = seq
                            self.armed_until = 0.0
                    except Exception:
                        pass
        self.root.after(100, self.monitor_clipboard)

    def inject_code(self, text):
        if not self.target_file:
            return
        try:
            shutil.copy2(self.target_file, self.target_file + ".txt")
            with open(self.target_file, 'w', encoding='utf-8') as f:
                f.write(text)
            self.animate_progress()
            self.lbl_mod.config(text=f"Mod: {datetime.now().strftime('%H:%M:%S')}")
        except Exception:
            pass

    def build_target(self):
        if not self.target_file:
            return
        folder = os.path.normpath(os.path.dirname(self.target_file))
        icon_arg = '--icon="icon.ico"' if os.path.exists(os.path.join(folder, "icon.ico")) else ""
        cmd = f'python -m PyInstaller --noconsole --onefile {icon_arg} "{os.path.basename(self.target_file)}"'
        subprocess.Popen(f'start cmd /k "cd /d "{folder}" && {cmd}"', shell=True)

    def run_file(self):
        if self.target_file:
            os.startfile(self.target_file)

    def run_cmd(self):
        if self.target_file:
            folder = os.path.dirname(self.target_file)
            subprocess.Popen(f'start cmd /k "cd /d "{folder}" && python \"{os.path.basename(self.target_file)}\""', shell=True)

    def restore_and_fix(self):
        if self.target_file:
            bak = self.target_file + ".txt"
            if os.path.exists(bak):
                shutil.copy2(bak, self.target_file)
                self.lbl_mod.config(text=f"Mod: {datetime.now().strftime('%H:%M:%S')}")
        self.armed_until = 0.0
        self.arm_seq = 0
        self.shift_was_down = False
        self.setup_hotkeys()

    def update_pip(self):
        subprocess.Popen('start cmd /k "python -m pip install --upgrade pip"', shell=True)

    def show_help(self):
        win = tk.Toplevel(self.root)
        win.title("Help")
        win.configure(bg=COLOR_BG)
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        width, height = 470, 560
        x = self.root.winfo_x() + 20
        y = self.root.winfo_y() - height - 10
        if y < 10:
            y = 10
        win.geometry(f"{width}x{height}+{x}+{y}")

        outer = tk.Frame(win, bg=COLOR_ACCENT)
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        container = tk.Frame(outer, bg=COLOR_BG)
        container.pack(fill="both", expand=True)

        header = tk.Frame(container, bg=COLOR_BG)
        header.pack(fill="x", padx=14, pady=10)

        tk.Label(header, text="CODE INJECTION", fg=COLOR_ACCENT, bg=COLOR_BG,
                 font=("Consolas", 13, "bold")).pack(side="left")
        tk.Label(header, text="MANUAL", fg="#888", bg=COLOR_BG,
                 font=("Consolas", 9)).pack(side="left", padx=6, pady=4)

        def drag_start(event):
            win._dx, win._dy = event.x, event.y

        def drag_move(event):
            win.geometry(f"+{win.winfo_x() + event.x - win._dx}+{win.winfo_y() + event.y - win._dy}")

        header.bind("<Button-1>", drag_start)
        header.bind("<B1-Motion>", drag_move)

        sep = tk.Frame(container, bg=COLOR_ACCENT, height=2)
        sep.pack(fill="x", padx=14)

        footer = tk.Frame(container, bg=COLOR_BG)
        footer.pack(side="bottom", fill="x", padx=14, pady=12)

        foot_sep = tk.Frame(container, bg=COLOR_ACCENT, height=2)
        foot_sep.pack(side="bottom", fill="x", padx=14)

        tk.Button(footer, text="Close", command=win.destroy, bg=COLOR_ACCENT, fg="black",
                  relief="flat", font=("Consolas", 9, "bold"), width=12,
                  cursor="hand2").pack(side="right")

        tk.Label(footer, text="oT", fg="#222", bg=COLOR_BG,
                 font=("Arial", 8)).pack(side="left")

        body_wrap = tk.Frame(container, bg=COLOR_BG)
        body_wrap.pack(fill="both", expand=True, padx=14, pady=10)

        canvas = tk.Canvas(body_wrap, bg=COLOR_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(body_wrap, orient="vertical", command=canvas.yview,
                                 bg=COLOR_BTN, troughcolor="#1a1a1a", activebackground=COLOR_ACCENT,
                                 width=10, bd=0, relief="flat", highlightthickness=0)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg=COLOR_BG)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def on_body_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_configure(event):
            canvas.itemconfig(body_id, width=event.width)

        body.bind("<Configure>", on_body_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        win.bind("<MouseWheel>", on_wheel)

        sections = [
            ("Target File", [
                "Drag a .txt or .py file onto the drop zone to set it as Target.",
                ".txt files are auto-renamed to .py (a free name is",
                "chosen automatically if the .py name is already taken).",
                "The drop zone turns orange and shows the active target.",
            ]),
            ("Shift+Copy Inject", [
                "When enabled, hold Shift while copying to write the",
                "clipboard content into the Target, replacing its full body.",
                "A .txt backup of the previous content is saved first.",
            ]),
            ("Run / CMD / Build", [
                "Run   - opens the Target with its default program.",
                "CMD   - runs the Target in a console (python <file>).",
                "Build - compiles the Target to a single .exe via",
                "        PyInstaller (uses icon.ico if found in the folder).",
            ]),
            ("Window Controls", [
                "F4          - toggle window visibility (show / hide).",
                "Stay On Top - keep the window above all others.",
                "Ghost       - fade to 50% when the mouse leaves.",
                "Drag the top bar to move the main window.",
            ]),
            ("Restore ( ... )", [
                "Restores the Target from its .txt backup and",
                "refreshes the keyboard hotkeys.",
            ]),
            ("Build & Icon", [
                "Place icon.ico next to the .py file to set the EXE icon.",
                "Keep the project in an English-only path. Non-English",
                "or special characters in the folder path can break the build.",
            ]),
            ("License & Terms", [
                "(c) 2026 ToClipsKit",
                "Free to use for any purpose, personal or commercial.",
                "This software is provided \"as is\", without warranty of",
                "any kind. You use it at your own risk. The author is not",
                "liable for any damage, data loss, or issues from its use.",
                "If you do not agree to these terms, delete the software.",
                "Always keep backups of important files before injecting.",
            ]),
        ]

        tk.Label(body, text="Requirements", fg=COLOR_ACCENT, bg=COLOR_BG,
                 font=("Consolas", 10, "bold"), anchor="w").pack(fill="x", pady=(6, 2))

        req_lines = [
            "Python 3 is required for the Build button (Tkinter is included).",
            "PyInstaller compiles the Target into a single .exe.",
            "If you only run the prebuilt app, Python is not required.",
        ]
        for line in req_lines:
            tk.Label(body, text=line, fg=COLOR_TEXT, bg=COLOR_BG,
                     font=("Consolas", 8), anchor="w", justify="left").pack(fill="x")

        link = tk.Label(body, text="Download Python 3  >", fg=COLOR_ACCENT, bg=COLOR_BG,
                        font=("Consolas", 8, "underline"), anchor="w", cursor="hand2")
        link.pack(fill="x", pady=(4, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open("https://www.python.org/downloads/"))

        tk.Label(body, text="Install PyInstaller in CMD:", fg=COLOR_TEXT, bg=COLOR_BG,
                 font=("Consolas", 8), anchor="w").pack(fill="x", pady=(8, 2))

        cmd_box = tk.Frame(body, bg="#1a1a1a")
        cmd_box.pack(fill="x")
        tk.Label(cmd_box, text="pip install pyinstaller", fg="#e2e8f0", bg="#1a1a1a",
                 font=("Consolas", 9), anchor="w").pack(side="left", padx=8, pady=5)

        tk.Button(body, text="Update pip", command=self.update_pip, bg=COLOR_ACCENT, fg="black",
                  relief="flat", font=("Consolas", 8, "bold"), cursor="hand2",
                  width=12).pack(anchor="w", pady=(8, 4))

        for title, lines in sections:
            tk.Label(body, text=title, fg=COLOR_ACCENT, bg=COLOR_BG,
                     font=("Consolas", 10, "bold"), anchor="w").pack(fill="x", pady=(6, 2))
            for line in lines:
                tk.Label(body, text=line, fg=COLOR_TEXT, bg=COLOR_BG,
                         font=("Consolas", 8), anchor="w", justify="left").pack(fill="x")

if __name__ == "__main__":
    root = TkinterDnD.Tk()
    app = CodeInjectionApp(root)
    root.mainloop()