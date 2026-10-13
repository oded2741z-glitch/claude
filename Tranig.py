import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import os
import sys
import threading
import random
import shutil
import webbrowser

try:
    from PIL import Image, ImageTk, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import cv2
except ImportError:
    cv2 = None

MASK_COLORS = [
    (224, 122, 95), (129, 178, 154), (242, 204, 143), (61, 133, 198), (224, 74, 63),
    (56, 147, 121), (171, 120, 214), (255, 107, 0), (100, 200, 230), (230, 230, 90),
]
MASK_PALETTE = [0, 0, 0] + [c for i in range(255) for c in MASK_COLORS[i % len(MASK_COLORS)]]
MASK_ALPHA_LUT = [0] + [110] * 255
IMG_EXTS = (".png", ".jpg", ".jpeg")
TOOLS = ["Brush", "Eraser", "Polygon"]
ARCHS = ["DeepLabV3 ResNet-50", "DeepLabV3 MobileNetV3"]


class MaskEditor:
    def __init__(self, app, tag_var, tool_var, brush_var, default_tags):
        self.app = app
        self.tag_var = tag_var
        self.tool_var = tool_var
        self.brush_var = brush_var
        self.default_tags = list(default_tags)
        self.tags = list(default_tags)
        self.folder = None
        self.image_paths = []
        self.index = -1
        self.base = None
        self.mask = None
        self.views = []
        self.tag_combos = []
        self.stroke_view = None
        self.stroke_value = 0
        self.last_pt = None
        self.poly_view = None
        self.poly_pts = []
        self.render_job = None

    def add_view(self, canvas, counter_lbl, default_size):
        view = {"canvas": canvas, "counter": counter_lbl, "default": default_size,
                "scale": (1.0, 1.0), "cache": None, "photo": None, "cfg_job": None}
        self.views.append(view)
        canvas.bind("<ButtonPress-1>", lambda e: self.on_press(view, e))
        canvas.bind("<B1-Motion>", lambda e: self.on_drag(view, e))
        canvas.bind("<ButtonRelease-1>", lambda e: self.on_release(view, e))
        canvas.bind("<Double-Button-1>", lambda e: self.on_double(view, e))
        canvas.bind("<ButtonPress-3>", lambda e: self.cancel_polygon())
        canvas.bind("<Configure>", lambda e: self.on_configure(view))

    def add_tag_combo(self, combo):
        self.tag_combos.append(combo)
        combo['values'] = self.tags

    def refresh_tag_combos(self):
        for combo in self.tag_combos:
            combo['values'] = self.tags

    def _read_tags(self):
        path = os.path.join(self.folder, "classes.txt") if self.folder else None
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    return [line.strip() for line in f if line.strip()]
            except (OSError, UnicodeError):
                pass
        return []

    def load_tags(self):
        loaded = self._read_tags()
        self.tags = loaded or list(self.default_tags)
        self.refresh_tag_combos()
        if self.tag_var.get().strip() not in self.tags:
            self.tag_var.set(self.tags[0])
        return bool(loaded)

    def save_tags(self):
        if not self.folder:
            return
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(os.path.join(self.folder, "classes.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(self.tags) + "\n")
        except OSError as e:
            self.app.update_status(f"Status: Error saving classes.txt - {e}")

    def current_value(self):
        tag = self.tag_var.get().strip()
        if not tag:
            tag = "object"
            self.tag_var.set(tag)
        disk = self._read_tags()
        if disk and disk != self.tags:
            self.tags = disk
            self.refresh_tag_combos()
        if tag not in self.tags:
            self.tags.append(tag)
            self.refresh_tag_combos()
            self.save_tags()
        return self.tags.index(tag) + 1

    def load_folder(self, folder):
        if not HAS_PIL:
            messagebox.showerror("Dependency Missing", "Pillow is required for mask annotation.\nInstall it with: pip install pillow")
            return False
        img_dir = os.path.join(folder, "images")
        if not os.path.isdir(img_dir):
            self.image_paths = []
            self.index = -1
            self.show()
            self.app.update_status(f"Status: Error - No 'images' folder found in {folder}")
            return False
        self.folder = folder
        if not self.load_tags():
            self.save_tags()
        self.image_paths = [
            os.path.join(img_dir, f) for f in sorted(os.listdir(img_dir))
            if f.lower().endswith(IMG_EXTS)
        ]
        self.index = 0 if self.image_paths else -1
        self.show()
        if self.image_paths:
            self.app.update_status(f"Status: Loaded {len(self.image_paths)} images")
        else:
            self.app.update_status("Status: No images found")
        return True

    def mask_path(self, img_path):
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        return os.path.join(os.path.dirname(os.path.dirname(img_path)), "masks", base_name + ".png")

    def _load_mask(self, img_path):
        path = self.mask_path(img_path)
        if not os.path.exists(path):
            return Image.new("L", self.base.size, 0)
        with Image.open(path) as m:
            if m.mode in ("L", "P"):
                mask = Image.frombytes("L", m.size, m.tobytes())
            else:
                mask = m.convert("L")
        if mask.size != self.base.size:
            mask = mask.resize(self.base.size, Image.NEAREST)
        return mask

    def show(self):
        self.cancel_polygon()
        self._cancel_render()
        self.stroke_view = None
        self.last_pt = None
        self.base = None
        self.mask = None
        if not (0 <= self.index < len(self.image_paths)):
            for view in self.views:
                view["canvas"].delete("all")
                view["counter"].config(text="0 / 0")
            return
        counter = f"{self.index + 1} / {len(self.image_paths)}"
        for view in self.views:
            view["cache"] = None
            view["counter"].config(text=counter)
        img_path = self.image_paths[self.index]
        try:
            with Image.open(img_path) as im:
                self.base = im.convert("RGB")
            self.mask = self._load_mask(img_path)
        except Exception as e:
            self.base = None
            self.mask = None
            for view in self.views:
                view["canvas"].delete("all")
            self.app.update_status(f"Status: Error loading image {e}")
            return
        self.render_all()

    def prev_image(self):
        if self.image_paths and self.index > 0:
            self.index -= 1
            self.show()

    def next_image(self):
        if self.image_paths and self.index < len(self.image_paths) - 1:
            self.index += 1
            self.show()

    def render(self, view):
        canvas = view["canvas"]
        canvas.delete("all")
        if self.base is None:
            return
        w, h = self.base.size
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if cw < 50 or ch < 50:
            cw, ch = view["default"]
        scale = min(cw / w, ch / h, 1.0)
        dw, dh = max(1, int(w * scale)), max(1, int(h * scale))
        if view["cache"] is None or view["cache"].size != (dw, dh):
            view["cache"] = self.base.resize((dw, dh), Image.BILINEAR)
        view["scale"] = (dw / w, dh / h)
        disp = view["cache"].copy()
        small = self.mask.resize((dw, dh), Image.NEAREST)
        if small.getbbox():
            color = Image.frombytes("P", small.size, small.tobytes())
            color.putpalette(MASK_PALETTE)
            disp.paste(color.convert("RGB"), (0, 0), small.point(MASK_ALPHA_LUT))
        view["photo"] = ImageTk.PhotoImage(disp)
        canvas.create_image(0, 0, anchor=tk.NW, image=view["photo"])
        if view is self.poly_view:
            self._draw_poly_preview()

    def render_all(self):
        for view in self.views:
            self.render(view)

    def _schedule_render(self, view):
        if self.render_job is None:
            canvas = view["canvas"]
            self.render_job = (canvas, canvas.after(40, lambda: self._run_render(view)))

    def _run_render(self, view):
        self.render_job = None
        self.render(view)

    def _cancel_render(self):
        if self.render_job is not None:
            canvas, job = self.render_job
            canvas.after_cancel(job)
            self.render_job = None

    def on_configure(self, view):
        if view["cfg_job"] is not None:
            view["canvas"].after_cancel(view["cfg_job"])
        view["cfg_job"] = view["canvas"].after(100, lambda: self._resized(view))

    def _resized(self, view):
        view["cfg_job"] = None
        if self.base is not None and self.stroke_view is None:
            self.render(view)

    def _to_image(self, view, x, y, clamp=False):
        sx, sy = view["scale"]
        ix, iy = x / sx, y / sy
        if clamp:
            w, h = self.base.size
            ix = max(0, min(ix, w - 1))
            iy = max(0, min(iy, h - 1))
        return (ix, iy)

    def _brush_radius(self, view):
        try:
            size = int(float(self.brush_var.get()))
        except ValueError:
            size = 15
        size = max(1, min(size, 500))
        return max(0.5, size / 2.0 / view["scale"][0])

    def _paint(self, view, p0, p1):
        r = self._brush_radius(view)
        draw = ImageDraw.Draw(self.mask)
        if p0 != p1:
            draw.line([p0, p1], fill=self.stroke_value, width=max(1, int(round(r * 2))))
        draw.ellipse([p1[0] - r, p1[1] - r, p1[0] + r, p1[1] + r], fill=self.stroke_value)

    def on_press(self, view, event):
        if self.base is None:
            return
        if self.tool_var.get() == "Polygon":
            self._polygon_click(view, event)
            return
        self.stroke_view = view
        self.stroke_value = 0 if self.tool_var.get() == "Eraser" else self.current_value()
        self.last_pt = self._to_image(view, event.x, event.y)
        self._paint(view, self.last_pt, self.last_pt)
        self._schedule_render(view)

    def on_drag(self, view, event):
        if self.last_pt is None or view is not self.stroke_view:
            return
        pt = self._to_image(view, event.x, event.y)
        self._paint(view, self.last_pt, pt)
        self.last_pt = pt
        self._schedule_render(view)

    def on_release(self, view, event):
        if self.last_pt is None or view is not self.stroke_view:
            return
        self.last_pt = None
        self.stroke_view = None
        self._cancel_render()
        self.save_mask()
        self.render_all()

    def on_double(self, view, event):
        if self.base is None:
            return
        if self.tool_var.get() == "Polygon":
            if view is self.poly_view and len(self.poly_pts) >= 3:
                self.close_polygon()
            return
        self.on_press(view, event)

    def on_tool_change(self, event=None):
        self.cancel_polygon()
        if self.tool_var.get() == "Polygon":
            self.app.update_status("Status: Polygon - click points, click the first point or double-click to close, right-click to cancel")
        else:
            self.app.update_status(f"Status: Tool set to {self.tool_var.get()}")

    def _polygon_click(self, view, event):
        if self.poly_view is not view:
            self.cancel_polygon()
            self.poly_view = view
        sx, sy = view["scale"]
        if len(self.poly_pts) >= 3:
            fx, fy = self.poly_pts[0]
            if abs(event.x - fx * sx) <= 8 and abs(event.y - fy * sy) <= 8:
                self.close_polygon()
                return
        self.poly_pts.append(self._to_image(view, event.x, event.y, clamp=True))
        self._draw_poly_preview()

    def _draw_poly_preview(self):
        canvas = self.poly_view["canvas"]
        canvas.delete("poly")
        sx, sy = self.poly_view["scale"]
        pts = [(x * sx, y * sy) for x, y in self.poly_pts]
        if len(pts) >= 2:
            canvas.create_line(*[c for p in pts for c in p], fill="#FFFFFF", width=2, tags="poly")
        for x, y in pts:
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, outline="#FFFFFF", fill="#E07A5F", tags="poly")

    def close_polygon(self):
        view = self.poly_view
        if view is None or len(self.poly_pts) < 3 or self.mask is None:
            self.cancel_polygon()
            return
        pts = list(self.poly_pts)
        value = self.current_value()
        self.cancel_polygon()
        ImageDraw.Draw(self.mask).polygon(pts, fill=value)
        self.save_mask()
        self.render_all()

    def cancel_polygon(self):
        if self.poly_view is not None:
            self.poly_view["canvas"].delete("poly")
        self.poly_view = None
        self.poly_pts = []

    def save_mask(self):
        if self.mask is None or not (0 <= self.index < len(self.image_paths)):
            return
        path = self.mask_path(self.image_paths[self.index])
        name = os.path.basename(path)
        try:
            if self.mask.getbbox() is None:
                if os.path.exists(path):
                    os.remove(path)
                    self.app.update_status(f"Status: Mask is empty - removed {name}")
                else:
                    self.app.update_status("Status: Mask is empty")
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self.mask.save(path)
                self.app.update_status(f"Status: Saved mask {name}")
        except OSError as e:
            self.app.update_status(f"Status: Error saving mask - {e}")

    def clear_mask(self):
        if self.base is None or not (0 <= self.index < len(self.image_paths)):
            return
        self.cancel_polygon()
        path = self.mask_path(self.image_paths[self.index])
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as e:
            self.app.update_status(f"Status: Error clearing mask - {e}")
            return
        self.mask = Image.new("L", self.base.size, 0)
        self.render_all()
        self.app.update_status(f"Status: Cleared mask for {os.path.splitext(os.path.basename(path))[0]}")

    def delete_image(self):
        if not self.image_paths or self.index < 0:
            return
        img_path = self.image_paths[self.index]
        if not messagebox.askyesno("Delete Image", f"Delete {os.path.basename(img_path)} and its mask file?"):
            return
        mask_path = self.mask_path(img_path)
        try:
            os.remove(img_path)
            if os.path.exists(mask_path):
                os.remove(mask_path)
        except OSError as e:
            self.app.update_status(f"Status: Error deleting file(s) - {e}")
            return
        del self.image_paths[self.index]
        self.index = min(self.index, len(self.image_paths) - 1)
        self.show()
        if self.image_paths:
            self.app.update_status("Status: Image and mask deleted successfully.")
        else:
            self.app.update_status("Status: Image deleted. No more images left.")


class TranigStudioApp:
    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.geometry("1200x850")
        self.root.configure(bg="#121212", highlightbackground="#E07A5F", highlightcolor="#E07A5F", highlightthickness=1)

        self.venv_python = None
        self.current_file = None

        self.tag_var = tk.StringVar()
        self.env_path = "venv"
        self.is_hidden = False

        self.auto_model_path = None

        self.active_process = None
        self.is_running = False
        self.restore_win = None

        self.ds_editor = MaskEditor(self, self.tag_var, tk.StringVar(value="Brush"), tk.StringVar(value="15"), ["drone"])
        self.ds_editor.folder = "my_dataset"

        # Video Studio state
        self.video_source_path = tk.StringVar()
        self.video_folder_name = tk.StringVar(value="video_dataset")
        self.video_interval_sec = tk.StringVar(value="1.0")
        self.video_active_tag = tk.StringVar(value="object")
        self.video_editor = MaskEditor(self, self.video_active_tag, tk.StringVar(value="Brush"), tk.StringVar(value="15"), ["object"])

        # Training Studio state
        self.train_dataset_dir = None
        self.train_classes = []
        self.train_arch_var = tk.StringVar(value=ARCHS[0])
        self.train_epochs_var = tk.StringVar(value="10")
        self.train_batch_var = tk.StringVar(value="2")
        self.train_lr_var = tk.StringVar(value="0.005")

        self.root.bind("<F4>", self.toggle_visibility)

        self.load_settings()
        self.setup_ui()
        self.ds_editor.load_tags()

    def toggle_visibility(self, event=None):
        if self.is_hidden:
            self.show_main_window()
        else:
            self.root.withdraw()
            self.is_hidden = True
            self.show_restore_window()

    def show_main_window(self, event=None):
        if self.restore_win is not None:
            self.restore_win.destroy()
            self.restore_win = None
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.attributes("-topmost", False)
        self.root.focus_force()
        self.is_hidden = False

    def show_restore_window(self):
        # A withdrawn overrideredirect window cannot receive key events,
        # so leave a tiny topmost handle on screen to bring the app back.
        self.restore_win = tk.Toplevel(self.root)
        self.restore_win.overrideredirect(True)
        self.restore_win.attributes("-topmost", True)
        sw = self.restore_win.winfo_screenwidth()
        sh = self.restore_win.winfo_screenheight()
        self.restore_win.geometry(f"30x20+{sw - 40}+{sh - 50}")
        lbl = tk.Label(self.restore_win, text="oT", bg="#121212", fg="#555555", font=("Arial", 9, "bold"))
        lbl.pack(fill=tk.BOTH, expand=True)
        lbl.bind("<Button-1>", self.show_main_window)
        self.restore_win.bind("<F4>", self.show_main_window)
        self.restore_win.focus_force()

    def load_settings(self):
        if os.path.exists("settings.txt"):
            try:
                with open("settings.txt", "r", encoding="utf-8") as f:
                    saved_path = f.read().strip()
                    if saved_path:
                        self.env_path = saved_path
            except Exception:
                pass

    def save_settings(self):
        try:
            with open("settings.txt", "w", encoding="utf-8") as f:
                f.write(self.env_path)
        except Exception:
            pass

    # --- Background process helpers ---
    def start_task(self, target):
        if self.is_running:
            self.update_status("Status: Error - Another process is already running")
            return False
        self.is_running = True

        def wrapper():
            try:
                target()
            finally:
                self.is_running = False

        threading.Thread(target=wrapper, daemon=True).start()
        return True

    def stream_subprocess(self, cmd):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=env)
        self.active_process = process
        try:
            for line in iter(process.stdout.readline, ''):
                if line:
                    try:
                        self.root.after(0, self.append_console, line)
                    except tk.TclError:
                        break
            process.stdout.close()
            process.wait()
        finally:
            self.active_process = None
        return process.returncode

    def quit_app(self):
        if self.active_process is not None and self.active_process.poll() is None:
            try:
                self.active_process.terminate()
            except Exception:
                pass
        self.root.destroy()

    def _add_tool_controls(self, parent, editor):
        tk.Label(parent, text="Tool:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)
        tool_cb = ttk.Combobox(parent, textvariable=editor.tool_var, font=("Arial", 10), width=9, state="readonly")
        tool_cb['values'] = TOOLS
        tool_cb.pack(side=tk.LEFT, padx=5)
        tool_cb.bind("<<ComboboxSelected>>", editor.on_tool_change)
        tk.Label(parent, text="Size:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)
        size = editor.brush_var.get()
        tk.Spinbox(parent, from_=1, to=200, textvariable=editor.brush_var, width=4, bg="#333333", fg="#FFFFFF", buttonbackground="#333333", relief="flat", insertbackground="#FFFFFF", font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
        editor.brush_var.set(size)

    def setup_ui(self):
        style = ttk.Style()
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TCombobox', fieldbackground='#333333', background='#333333', foreground='#FFFFFF', bordercolor='#E07A5F', arrowcolor='#FFFFFF')
        style.map('TCombobox', fieldbackground=[('readonly', '#333333')], selectbackground=[('readonly', '#E07A5F')], selectforeground=[('readonly', '#FFFFFF')])

        style.configure('TLabelframe', background='#121212', bordercolor='#555555')
        style.configure('TLabelframe.Label', background='#121212', foreground='#FFFFFF', font=('Arial', 9))

        # Notebook (Tabs) Styling
        style.configure('TNotebook', background='#121212', borderwidth=0)
        style.configure('TNotebook.Tab', background='#333333', foreground='#FFFFFF', padding=[15, 5], font=('Arial', 10, 'bold'), borderwidth=0)
        style.map('TNotebook.Tab', background=[('selected', '#E07A5F')], foreground=[('selected', '#121212')])

        # --- Top Bar ---
        top_bar = tk.Frame(self.root, bg="#121212")
        top_bar.pack(fill=tk.X)
        top_bar.bind("<ButtonPress-1>", self.start_move)
        top_bar.bind("<B1-Motion>", self.do_move)

        title = tk.Label(top_bar, text="Tranig Studio", bg="#121212", fg="#E07A5F", font=("Arial", 12, "bold"))
        title.pack(side=tk.LEFT, padx=10, pady=5)

        quit_btn = tk.Button(top_bar, text="Quit", bg="#FF6B00", fg="#FFFFFF", relief="flat", borderwidth=0, padx=15, pady=5, command=self.quit_app)
        quit_btn.pack(side=tk.RIGHT, padx=5, pady=5)

        help_btn = tk.Button(top_bar, text="Help", bg="#333333", fg="#FFFFFF", relief="flat", borderwidth=0, padx=15, pady=5, command=self.show_help)
        help_btn.pack(side=tk.RIGHT, padx=5, pady=5)

        # --- Tabs Container ---
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 20))

        # TAB 1: Vision Studio
        self.tab_main = tk.Frame(self.notebook, bg="#121212")
        self.notebook.add(self.tab_main, text=" Vision Studio ")

        # TAB 2: Auto-Labeling Studio
        self.tab_auto = tk.Frame(self.notebook, bg="#121212")
        self.notebook.add(self.tab_auto, text=" Auto-Labeling Studio ")
        self.setup_auto_tab()

        # TAB 3: Video Studio
        self.tab_video = tk.Frame(self.notebook, bg="#121212")
        self.notebook.add(self.tab_video, text=" Video Studio ")
        self.setup_video_tab()

        # TAB 4: Training Studio
        self.tab_train = tk.Frame(self.notebook, bg="#121212")
        self.notebook.add(self.tab_train, text=" Training Studio ")
        self.setup_training_tab()

        btn_args = {"bg": "#333333", "fg": "#FFFFFF", "relief": "flat", "borderwidth": 0, "padx": 10, "pady": 5}

        # --- MAIN TAB CONTENT ---
        controls_container = tk.Frame(self.tab_main, bg="#121212")
        controls_container.pack(fill=tk.X, padx=0, pady=5)

        files_lf = ttk.LabelFrame(controls_container, text=" File Execution ")
        files_lf.pack(side=tk.LEFT, padx=(0, 5), pady=5, fill=tk.Y)

        files_top = tk.Frame(files_lf, bg="#121212")
        files_top.pack(fill=tk.X, pady=2, padx=5)
        tk.Button(files_top, text="Load train_deeplab.py", command=lambda: self.load_file("train_deeplab.py"), **btn_args).pack(side=tk.LEFT, padx=2)
        tk.Button(files_top, text="Load run_deeplab.py", command=lambda: self.load_file("run_deeplab.py"), **btn_args).pack(side=tk.LEFT, padx=2)

        files_bot = tk.Frame(files_lf, bg="#121212")
        files_bot.pack(fill=tk.X, pady=2, padx=5)
        tk.Button(files_bot, text="Run train_deeplab.py", command=lambda: self.run_file("train_deeplab.py"), **btn_args).pack(side=tk.LEFT, padx=2)
        tk.Button(files_bot, text="Run run_deeplab.py", command=lambda: self.run_file("run_deeplab.py"), **btn_args).pack(side=tk.LEFT, padx=2)

        env_lf = ttk.LabelFrame(controls_container, text=" Environment Setup ")
        env_lf.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.Y)

        env_top = tk.Frame(env_lf, bg="#121212")
        env_top.pack(fill=tk.X, pady=2, padx=5)
        tk.Button(env_top, text="Create Environment & Folders", command=self.create_env, **btn_args).pack(side=tk.LEFT, padx=2)
        tk.Button(env_top, text="Set Env Path", command=self.set_env_path, **btn_args).pack(side=tk.LEFT, padx=2)
        tk.Button(env_top, text="Open Env Folder", command=self.open_venv_folder, **btn_args).pack(side=tk.LEFT, padx=2)

        env_mid = tk.Frame(env_lf, bg="#121212")
        env_mid.pack(fill=tk.X, pady=2, padx=5)
        tk.Button(env_mid, text="Install Requirements", command=self.install_requirements, **btn_args).pack(side=tk.LEFT, padx=2)
        tk.Button(env_mid, text="Check GPU / CUDA", command=self.check_gpu, **btn_args).pack(side=tk.LEFT, padx=2)

        env_bot = tk.Frame(env_lf, bg="#121212")
        env_bot.pack(fill=tk.X, pady=2, padx=5)

        self.activate_btn = tk.Button(env_bot, text="Activate Environment", command=self.activate_env, **btn_args)
        self.activate_btn.pack(side=tk.LEFT, padx=2)

        self.status_lbl = tk.Label(env_bot, text="Status: Inactive", bg="#121212", fg="#FFFFFF", font=("Arial", 10))
        self.status_lbl.pack(side=tk.LEFT, padx=10)

        main_paned = tk.PanedWindow(self.tab_main, orient=tk.HORIZONTAL, bg="#E07A5F", sashwidth=2, borderwidth=0)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=0, pady=5)

        left_paned = tk.PanedWindow(main_paned, orient=tk.VERTICAL, bg="#E07A5F", sashwidth=2, borderwidth=0)
        main_paned.add(left_paned, minsize=400)

        editor_frame = tk.Frame(left_paned, bg="#121212")

        editor_top_bar = tk.Frame(editor_frame, bg="#121212")
        editor_top_bar.pack(fill=tk.X, pady=2)
        tk.Button(editor_top_bar, text="Save Code", command=self.save_file, **btn_args).pack(side=tk.RIGHT, padx=2)

        self.editor = tk.Text(editor_frame, bg="#000000", fg="#FFFFFF", insertbackground="#FFFFFF", relief="flat", borderwidth=0, font=("Consolas", 11))
        self.editor.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        left_paned.add(editor_frame, minsize=200)

        console_frame = tk.Frame(left_paned, bg="#121212")
        self.console = tk.Text(console_frame, bg="#000000", fg="#E07A5F", insertbackground="#FFFFFF", relief="flat", borderwidth=0, font=("Consolas", 10))
        self.console.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        left_paned.add(console_frame, minsize=100)

        right_frame = tk.Frame(main_paned, bg="#121212")
        main_paned.add(right_frame, minsize=300)

        dataset_lf = ttk.LabelFrame(right_frame, text=" Dataset Images ")
        dataset_lf.pack(fill=tk.X, pady=5)

        viewer_controls_top = tk.Frame(dataset_lf, bg="#121212")
        viewer_controls_top.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(viewer_controls_top, text="Load Dataset Images", command=self.load_dataset_images, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(viewer_controls_top, text="Open Images Folder", command=self.open_images_folder, **btn_args).pack(side=tk.LEFT, padx=5)

        viewer_controls_split = tk.Frame(dataset_lf, bg="#121212")
        viewer_controls_split.pack(fill=tk.X, pady=5, padx=5)
        tk.Label(viewer_controls_split, text="Train %:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)
        self.train_pct_var = tk.StringVar(value="80")
        tk.Entry(viewer_controls_split, textvariable=self.train_pct_var, width=5, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF", font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
        tk.Button(viewer_controls_split, text="Split Dataset", command=self.split_dataset, **btn_args).pack(side=tk.LEFT, padx=5)

        viewer_controls_mid = tk.Frame(dataset_lf, bg="#121212")
        viewer_controls_mid.pack(fill=tk.X, pady=5, padx=5)
        tk.Label(viewer_controls_mid, text="Tag:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)

        self.tag_cb = ttk.Combobox(viewer_controls_mid, textvariable=self.tag_var, font=("Arial", 10))
        self.ds_editor.add_tag_combo(self.tag_cb)
        self.tag_cb.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        viewer_controls_tool = tk.Frame(dataset_lf, bg="#121212")
        viewer_controls_tool.pack(fill=tk.X, pady=5, padx=5)
        self._add_tool_controls(viewer_controls_tool, self.ds_editor)

        viewer_controls_bot = tk.Frame(dataset_lf, bg="#121212")
        viewer_controls_bot.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(viewer_controls_bot, text="Prev", command=self.ds_editor.prev_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(viewer_controls_bot, text="Next", command=self.ds_editor.next_image, **btn_args).pack(side=tk.LEFT, padx=5)

        tk.Button(viewer_controls_bot, text="Delete Image", command=self.ds_editor.delete_image, bg="#E04A3F", fg="#FFFFFF", relief="flat", borderwidth=0, padx=10, pady=5, font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=5)
        tk.Button(viewer_controls_bot, text="Clear Mask", command=self.ds_editor.clear_mask, **btn_args).pack(side=tk.RIGHT, padx=5)

        self.img_counter_lbl = tk.Label(viewer_controls_bot, text="0 / 0", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold"))
        self.img_counter_lbl.pack(expand=True)

        self.img_canvas = tk.Canvas(right_frame, bg="#000000", highlightthickness=1, highlightbackground="#333333")
        self.img_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.ds_editor.add_view(self.img_canvas, self.img_counter_lbl, (400, 400))

        watermark = tk.Label(self.root, text="oT", bg="#121212", fg="#555555", font=("Arial", 9, "bold"))
        watermark.place(relx=1.0, rely=1.0, anchor=tk.SE, x=-6, y=-2)

    def setup_auto_tab(self):
        lbl = tk.Label(self.tab_auto, text="Auto-Labeling Studio", bg="#121212", fg="#E07A5F", font=("Arial", 16, "bold"))
        lbl.pack(pady=(15, 5))

        controls_frame = tk.Frame(self.tab_auto, bg="#121212")
        controls_frame.pack(fill=tk.X, padx=20, pady=5)

        auto_settings_lf = ttk.LabelFrame(controls_frame, text=" 1. Run Auto-Labeling ")
        auto_settings_lf.pack(side=tk.TOP, fill=tk.X, pady=5)

        btn_args = {"bg": "#333333", "fg": "#FFFFFF", "relief": "flat", "borderwidth": 0, "padx": 10, "pady": 5}

        self.auto_model_btn = tk.Button(auto_settings_lf, text="Select Trained Model", command=self.select_auto_model, **btn_args)
        self.auto_model_btn.pack(side=tk.LEFT, padx=10, pady=10)

        tk.Label(auto_settings_lf, text="Min Conf %:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)
        self.conf_pct_var = tk.StringVar(value="50")
        tk.Entry(auto_settings_lf, textvariable=self.conf_pct_var, width=5, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF", font=("Arial", 10)).pack(side=tk.LEFT, padx=5)

        self.auto_label_btn = tk.Button(auto_settings_lf, text="Start Auto-Labeling", command=self.auto_label_images, bg="#81B29A", fg="#121212", font=("Arial", 10, "bold"), relief="flat", borderwidth=0, padx=15, pady=5)
        self.auto_label_btn.pack(side=tk.LEFT, padx=20, pady=10)

        review_lf = ttk.LabelFrame(controls_frame, text=" 2. Review & Edit Masks ")
        review_lf.pack(side=tk.TOP, fill=tk.X, pady=5)

        review_top = tk.Frame(review_lf, bg="#121212")
        review_top.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(review_top, text="Load Dataset Images", command=self.load_dataset_images, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(review_top, text="Open Images Folder", command=self.open_images_folder, **btn_args).pack(side=tk.LEFT, padx=5)

        tk.Label(review_top, text="Tag:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=15)
        self.auto_tag_cb = ttk.Combobox(review_top, textvariable=self.tag_var, font=("Arial", 10))
        self.ds_editor.add_tag_combo(self.auto_tag_cb)
        self.auto_tag_cb.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        review_tool = tk.Frame(review_lf, bg="#121212")
        review_tool.pack(fill=tk.X, pady=5, padx=5)
        self._add_tool_controls(review_tool, self.ds_editor)

        review_bot = tk.Frame(review_lf, bg="#121212")
        review_bot.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(review_bot, text="Prev", command=self.ds_editor.prev_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(review_bot, text="Next", command=self.ds_editor.next_image, **btn_args).pack(side=tk.LEFT, padx=5)

        tk.Button(review_bot, text="Delete Image", command=self.ds_editor.delete_image, bg="#E04A3F", fg="#FFFFFF", relief="flat", borderwidth=0, padx=10, pady=5, font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=5)
        tk.Button(review_bot, text="Clear Current Image Mask", command=self.ds_editor.clear_mask, **btn_args).pack(side=tk.RIGHT, padx=5)

        self.auto_img_counter_lbl = tk.Label(review_bot, text="0 / 0", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold"))
        self.auto_img_counter_lbl.pack(expand=True)

        self.auto_img_canvas = tk.Canvas(self.tab_auto, bg="#000000", highlightthickness=1, highlightbackground="#333333")
        self.auto_img_canvas.pack(fill=tk.BOTH, expand=True, padx=20, pady=(5, 20))
        self.ds_editor.add_view(self.auto_img_canvas, self.auto_img_counter_lbl, (800, 500))

    def setup_video_tab(self):
        btn_args = {"bg": "#333333", "fg": "#FFFFFF", "relief": "flat", "borderwidth": 0, "padx": 10, "pady": 5}
        tk.Label(self.tab_video, text="Video Extraction & Annotation Studio", bg="#121212", fg="#E07A5F", font=("Arial", 16, "bold")).pack(pady=(15, 5))

        top_f = tk.Frame(self.tab_video, bg="#121212")
        top_f.pack(fill=tk.X, padx=20, pady=5)

        ext_lf = ttk.LabelFrame(top_f, text=" 1. Extract Frames from Video ")
        ext_lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        row1 = tk.Frame(ext_lf, bg="#121212")
        row1.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(row1, text="Select Video", command=self.video_browse, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Entry(row1, textvariable=self.video_source_path, width=40, bg="#333333", fg="#FFFFFF", relief="flat", state="readonly").pack(side=tk.LEFT, padx=5)

        row2 = tk.Frame(ext_lf, bg="#121212")
        row2.pack(fill=tk.X, pady=5, padx=5)
        tk.Label(row2, text="New Folder:", bg="#121212", fg="#FFFFFF", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        tk.Entry(row2, textvariable=self.video_folder_name, width=15, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF").pack(side=tk.LEFT, padx=5)
        tk.Label(row2, text="Interval (sec):", bg="#121212", fg="#FFFFFF", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        tk.Entry(row2, textvariable=self.video_interval_sec, width=5, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF").pack(side=tk.LEFT, padx=5)
        tk.Button(row2, text="Extract Frames", command=self.video_extract, bg="#81B29A", fg="#121212", font=("Arial", 9, "bold"), relief="flat", padx=10, pady=5).pack(side=tk.LEFT, padx=15)

        edit_lf = ttk.LabelFrame(top_f, text=" 2. Workspace & Annotation ")
        edit_lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))

        erow1 = tk.Frame(edit_lf, bg="#121212")
        erow1.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(erow1, text="Load Dataset Folder", command=self.video_load_folder, **btn_args).pack(side=tk.LEFT, padx=5)
        self._add_tool_controls(erow1, self.video_editor)

        erow2 = tk.Frame(edit_lf, bg="#121212")
        erow2.pack(fill=tk.X, pady=5, padx=5)
        tk.Label(erow2, text="Active Tag:", bg="#121212", fg="#FFFFFF", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        self.video_tag_cb = ttk.Combobox(erow2, textvariable=self.video_active_tag, font=("Arial", 10), width=15)
        self.video_editor.add_tag_combo(self.video_tag_cb)
        self.video_tag_cb.pack(side=tk.LEFT, padx=5)
        tk.Button(erow2, text="Clear Mask", command=self.video_editor.clear_mask, **btn_args).pack(side=tk.RIGHT, padx=5)

        bot_f = tk.Frame(self.tab_video, bg="#121212")
        bot_f.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

        self.video_canvas = tk.Canvas(bot_f, bg="#000000", highlightthickness=1, highlightbackground="#333333", cursor="crosshair")
        self.video_canvas.pack(fill=tk.BOTH, expand=True, pady=5)

        c_bot = tk.Frame(bot_f, bg="#121212")
        c_bot.pack(fill=tk.X, pady=5)
        tk.Button(c_bot, text="Prev", command=self.video_editor.prev_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(c_bot, text="Next", command=self.video_editor.next_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(c_bot, text="Delete Image", command=self.video_editor.delete_image, bg="#E04A3F", fg="#FFFFFF", relief="flat", padx=10, pady=5).pack(side=tk.RIGHT, padx=5)
        self.video_counter_lbl = tk.Label(c_bot, text="0 / 0", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold"))
        self.video_counter_lbl.pack(expand=True)
        self.video_editor.add_view(self.video_canvas, self.video_counter_lbl, (800, 500))

    # --- Video Studio Logic ---
    def video_browse(self):
        filepath = filedialog.askopenfilename(title="Select Video", filetypes=[("Video Files", "*.mp4 *.avi *.mov *.mkv")])
        if filepath:
            self.video_source_path.set(filepath)

    def video_extract(self):
        if cv2 is None:
            self.update_status("Status: Error - OpenCV not found. Click 'Install Requirements' first.")
            messagebox.showerror("Dependency Missing", "OpenCV is required for video extraction.\nGo to Vision Studio and click 'Install Requirements'.")
            return
        if self.is_running:
            self.update_status("Status: Error - Another process is already running")
            return

        vid_path = self.video_source_path.get()
        folder = self.video_folder_name.get().strip()
        try:
            interval = float(self.video_interval_sec.get())
            if interval <= 0:
                raise ValueError
        except ValueError:
            self.update_status("Status: Error - Invalid interval")
            return
        if not vid_path or not os.path.exists(vid_path):
            self.update_status("Status: Error - Video file not found")
            return
        if not folder:
            self.update_status("Status: Error - Invalid folder name")
            return

        self.update_status("Status: Extracting frames...")
        self.console.delete("1.0", tk.END)
        self.append_console(f"--- Starting Video Extraction ---\nTarget Folder: {folder}\nInterval: {interval} seconds\n")
        self.start_task(lambda: self._video_extract_thread(vid_path, folder, interval))

    def _video_extract_thread(self, vid_path, folder, interval):
        import tempfile
        target_dir = os.path.join(os.getcwd(), folder)
        img_dir = os.path.join(target_dir, "images")
        mask_dir = os.path.join(target_dir, "masks")
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(mask_dir, exist_ok=True)

        # Copy to a temp ASCII path so OpenCV can open videos with non-ASCII paths.
        temp_vid_path = os.path.join(tempfile.gettempdir(), "tranig_temp_video.mp4")
        try:
            shutil.copy2(vid_path, temp_vid_path)
            safe_vid_path = temp_vid_path
        except Exception:
            safe_vid_path = vid_path
            temp_vid_path = None

        cap = cv2.VideoCapture(safe_vid_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            cap.release()
            if temp_vid_path and os.path.exists(temp_vid_path):
                try:
                    os.remove(temp_vid_path)
                except OSError:
                    pass
            self.root.after(0, lambda: self.update_status("Status: Error - Failed to read video FPS"))
            self.root.after(0, self.append_console, "Error: Unsupported format or corrupted file.\n")
            return

        frame_skip = max(1, int(fps * interval))
        count, saved = 0, 0
        while cap.isOpened():
            ret = cap.grab()
            if not ret:
                break
            if count % frame_skip == 0:
                ret, frame = cap.retrieve()
                if ret:
                    out_path = os.path.join(img_dir, f"frame_{saved:05d}.jpg")
                    is_success, buf = cv2.imencode(".jpg", frame)
                    if is_success:
                        buf.tofile(out_path)
                    saved += 1
                    if saved % 10 == 0:
                        self.root.after(0, self.append_console, f"Extracted {saved} frames so far...\n")
            count += 1

        cap.release()
        if temp_vid_path and os.path.exists(temp_vid_path):
            try:
                os.remove(temp_vid_path)
            except OSError:
                pass

        self.root.after(0, self.append_console, f"--- Video Extraction Complete! Total frames: {saved} ---\n")
        self.root.after(0, lambda: self.video_finish_extract(target_dir, saved))

    def video_finish_extract(self, target_dir, saved_count):
        self.video_editor.load_folder(target_dir)
        self.update_status(f"Status: Extracted {saved_count} frames to {target_dir}")

    def video_load_folder(self):
        folder = filedialog.askdirectory(title="Select Dataset Folder (must contain 'images')")
        if folder:
            self.video_editor.load_folder(folder)

    # --- Training Studio (DeepLabV3 semantic segmentation) ---
    def setup_training_tab(self):
        btn_args = {"bg": "#333333", "fg": "#FFFFFF", "relief": "flat", "borderwidth": 0, "padx": 10, "pady": 5}
        tk.Label(self.tab_train, text="Training Studio (PyTorch / torchvision - DeepLabV3)", bg="#121212", fg="#E07A5F", font=("Arial", 16, "bold")).pack(pady=(15, 5))
        tk.Label(self.tab_train, text="Semantic segmentation. Reads pixel masks (images/ + masks/ + classes.txt) and trains a standalone DeepLabV3 model.", bg="#121212", fg="#AAAAAA", font=("Arial", 10)).pack(pady=(0, 10))

        data_lf = ttk.LabelFrame(self.tab_train, text=" 1. Dataset ")
        data_lf.pack(fill=tk.X, padx=20, pady=5)
        d_row = tk.Frame(data_lf, bg="#121212")
        d_row.pack(fill=tk.X, pady=8, padx=5)
        tk.Button(d_row, text="Select Dataset Folder", command=self.train_browse_dataset, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(d_row, text="Use my_dataset", command=lambda: self.train_set_dataset(os.path.abspath("my_dataset")), **btn_args).pack(side=tk.LEFT, padx=5)
        self.train_dataset_lbl = tk.Label(d_row, text="No dataset selected", bg="#121212", fg="#FFFFFF", font=("Arial", 10))
        self.train_dataset_lbl.pack(side=tk.LEFT, padx=10)

        cfg_lf = ttk.LabelFrame(self.tab_train, text=" 2. Training Configuration ")
        cfg_lf.pack(fill=tk.X, padx=20, pady=5)
        c_row = tk.Frame(cfg_lf, bg="#121212")
        c_row.pack(fill=tk.X, pady=8, padx=5)

        tk.Label(c_row, text="Architecture:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)
        arch_cb = ttk.Combobox(c_row, textvariable=self.train_arch_var, font=("Arial", 10), width=22, state="readonly")
        arch_cb['values'] = ARCHS
        arch_cb.pack(side=tk.LEFT, padx=5)

        tk.Label(c_row, text="Epochs:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(15, 2))
        tk.Entry(c_row, textvariable=self.train_epochs_var, width=5, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF").pack(side=tk.LEFT, padx=2)
        tk.Label(c_row, text="Batch:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(15, 2))
        tk.Entry(c_row, textvariable=self.train_batch_var, width=5, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF").pack(side=tk.LEFT, padx=2)
        tk.Label(c_row, text="LR:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(15, 2))
        tk.Entry(c_row, textvariable=self.train_lr_var, width=7, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF").pack(side=tk.LEFT, padx=2)

        act_lf = ttk.LabelFrame(self.tab_train, text=" 3. Train & Test ")
        act_lf.pack(fill=tk.X, padx=20, pady=5)
        a_row = tk.Frame(act_lf, bg="#121212")
        a_row.pack(fill=tk.X, pady=8, padx=5)
        tk.Button(a_row, text="Start Training", command=self.train_model, bg="#81B29A", fg="#121212", font=("Arial", 10, "bold"), relief="flat", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(a_row, text="Test Model on Image", command=self.train_test_model, **btn_args).pack(side=tk.LEFT, padx=5)
        self.train_status_lbl = tk.Label(a_row, text="Output model: model.pth", bg="#121212", fg="#AAAAAA", font=("Arial", 10))
        self.train_status_lbl.pack(side=tk.LEFT, padx=10)

        tk.Label(self.tab_train, text="Training progress is streamed to the Vision Studio console.", bg="#121212", fg="#555555", font=("Arial", 9)).pack(pady=10)

    def train_browse_dataset(self):
        folder = filedialog.askdirectory(title="Select Dataset Folder (must contain 'images' and 'masks')")
        if folder:
            self.train_set_dataset(folder)

    def train_set_dataset(self, folder):
        if not os.path.exists(os.path.join(folder, "images")):
            self.update_status("Status: Error - Folder has no 'images' subfolder")
            self.train_dataset_lbl.config(text="Invalid folder (no images/)")
            return
        self.train_dataset_dir = folder
        self.train_classes = self._read_dataset_classes(folder)
        name = os.path.basename(folder.rstrip(os.sep)) or folder
        if self.train_classes:
            self.train_dataset_lbl.config(text=f"{name}  |  {len(self.train_classes)} classes: {', '.join(self.train_classes)}")
        else:
            self.train_dataset_lbl.config(text=f"{name}  |  no classes/masks found")
        self.update_status(f"Status: Dataset set to {name}")

    def _read_dataset_classes(self, folder):
        classes_path = os.path.join(folder, "classes.txt")
        if os.path.exists(classes_path):
            try:
                with open(classes_path, "r", encoding="utf-8-sig") as f:
                    names = [line.strip() for line in f if line.strip()]
            except (OSError, UnicodeError):
                names = []
            if names:
                return names
        # Fall back to inferring class count from the mask files themselves.
        mask_dir = os.path.join(folder, "masks")
        max_id = 0
        if HAS_PIL and os.path.exists(mask_dir):
            for fn in os.listdir(mask_dir):
                if fn.lower().endswith(".png"):
                    try:
                        with Image.open(os.path.join(mask_dir, fn)) as m:
                            if m.mode in ("L", "P"):
                                max_id = max(max_id, Image.frombytes("L", m.size, m.tobytes()).getextrema()[1])
                    except Exception:
                        pass
        if max_id > 0:
            return [f"class_{i}" for i in range(1, max_id + 1)]
        return []

    def train_model(self):
        if not self.venv_python:
            self.update_status("Status: Error - Please activate environment first")
            return
        if not self.train_dataset_dir:
            self.update_status("Status: Error - Please select a dataset folder first")
            return
        self.train_set_dataset(self.train_dataset_dir)
        if not self.train_classes:
            self.update_status("Status: Error - No classes found in dataset")
            return
        try:
            epochs = int(self.train_epochs_var.get())
            batch = int(self.train_batch_var.get())
            lr = float(self.train_lr_var.get())
            if epochs <= 0 or batch <= 0 or lr <= 0:
                raise ValueError
        except ValueError:
            self.update_status("Status: Error - Invalid epochs / batch / lr")
            return
        if self.is_running:
            self.update_status("Status: Error - Another process is already running")
            return

        out_path = os.path.abspath("model.pth")
        script = self._build_train_script(self.train_dataset_dir, self.train_arch_var.get(), epochs, batch, lr, out_path, self.train_classes)
        with open("train_torch_script.py", "w", encoding="utf-8") as f:
            f.write(script)

        self.notebook.select(self.tab_main)
        self.update_status("Status: Training started...")
        self.console.delete("1.0", tk.END)
        self.append_console(f"--- Training {self.train_arch_var.get()} on {len(self.train_classes)} classes ---\n")
        self.start_task(self._train_thread)

    def _train_thread(self):
        self.stream_subprocess([self.venv_python, "train_torch_script.py"])
        if os.path.exists("train_torch_script.py"):
            try:
                os.remove("train_torch_script.py")
            except OSError:
                pass
        self.root.after(0, lambda: self.update_status("Status: Training finished (see console)"))

    def train_test_model(self):
        if not self.venv_python:
            self.update_status("Status: Error - Please activate environment first")
            return
        model_path = filedialog.askopenfilename(title="Select Trained Model (.pth)", filetypes=[("PyTorch Model", "*.pth")])
        if not model_path:
            return
        image_path = filedialog.askopenfilename(title="Select Image to Test", filetypes=[("Images", "*.png *.jpg *.jpeg")])
        if not image_path:
            return
        if self.is_running:
            self.update_status("Status: Error - Another process is already running")
            return

        out_path = os.path.splitext(image_path)[0] + "_pred.jpg"
        script = self._build_infer_script(model_path, image_path, out_path, 0.5)
        with open("infer_torch_script.py", "w", encoding="utf-8") as f:
            f.write(script)

        self.notebook.select(self.tab_main)
        self.update_status("Status: Running inference...")
        self.console.delete("1.0", tk.END)
        self.append_console("--- Running inference ---\n")
        self.start_task(lambda: self._infer_thread(out_path))

    def _infer_thread(self, out_path):
        self.stream_subprocess([self.venv_python, "infer_torch_script.py"])
        if os.path.exists("infer_torch_script.py"):
            try:
                os.remove("infer_torch_script.py")
            except OSError:
                pass
        if os.path.exists(out_path):
            self.root.after(0, lambda: self._open_file(out_path))
            self.root.after(0, lambda: self.update_status(f"Status: Saved prediction to {os.path.basename(out_path)}"))
        else:
            self.root.after(0, lambda: self.update_status("Status: Inference finished (see console)"))

    def _open_file(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    _TORCH_MODEL_BODY = '''
import os, sys
try:
    import torch
    import torch.nn.functional as F
    import torchvision
    import torchvision.transforms.functional as TF
    import numpy as np
    from PIL import Image
except ImportError:
    print("Error: torch/torchvision not found. Click 'Install Requirements' first.")
    sys.exit(1)

MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


def build_model(arch, num_classes, pretrained):
    from torchvision.models import segmentation
    if arch == "DeepLabV3 MobileNetV3":
        builder = segmentation.deeplabv3_mobilenet_v3_large
    else:
        builder = segmentation.deeplabv3_resnet50
    if pretrained:
        model = builder(weights="DEFAULT")
    else:
        model = builder(weights=None, weights_backbone=None)
    model.aux_classifier = None
    model.classifier[4] = torch.nn.Conv2d(model.classifier[4].in_channels, num_classes, kernel_size=1)
    return model


def to_input(img, size):
    x = TF.to_tensor(img.resize((size, size), Image.BILINEAR))
    return TF.normalize(x, MEAN, STD)


def load_trained(path, device):
    ckpt = torch.load(path, map_location="cpu")
    classes = list(ckpt.get("classes", []))
    model = build_model(ckpt.get("arch", "DeepLabV3 ResNet-50"), len(classes) + 1, False)
    model.load_state_dict(ckpt["model_state"])
    model.to(device).eval()
    return model, classes, int(ckpt.get("img_size", 512))


def predict(model, img, size, device, conf):
    w, h = img.size
    with torch.no_grad():
        logits = model(to_input(img, size).unsqueeze(0).to(device))["out"]
        logits = F.interpolate(logits, size=(h, w), mode="bilinear", align_corners=False)
        score, pred = torch.softmax(logits, dim=1)[0].max(dim=0)
        pred[score < conf] = 0
    return pred.cpu().numpy().astype(np.uint8)
'''

    def _build_train_script(self, dataset_dir, arch, epochs, batch, lr, out_path, classes):
        header = (
            "DATASET_DIR = %r\n"
            "ARCH = %r\n"
            "EPOCHS = %d\n"
            "BATCH = %d\n"
            "LR = %r\n"
            "OUT_PATH = %r\n"
            "CLASSES = %r\n"
            "IMG_SIZE = %d\n"
        ) % (dataset_dir, arch, epochs, batch, lr, out_path, list(classes), 512)
        body = '''
from torch.utils.data import Dataset, DataLoader

IMG_DIR = os.path.join(DATASET_DIR, "images")
MASK_DIR = os.path.join(DATASET_DIR, "masks")
IMG_EXTS = (".png", ".jpg", ".jpeg")


def load_classes():
    if CLASSES:
        return list(CLASSES)
    path = os.path.join(DATASET_DIR, "classes.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            return [line.strip() for line in f if line.strip()]
    return []


def list_samples():
    samples = []
    if not os.path.isdir(IMG_DIR):
        return samples
    for name in sorted(os.listdir(IMG_DIR)):
        if not name.lower().endswith(IMG_EXTS):
            continue
        mask = os.path.join(MASK_DIR, os.path.splitext(name)[0] + ".png")
        if os.path.exists(mask):
            samples.append((os.path.join(IMG_DIR, name), mask))
    return samples


class SegmentationDataset(Dataset):
    def __init__(self, samples, num_classes):
        self.samples = samples
        self.num_classes = num_classes

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, mask_path = self.samples[idx]
        with Image.open(img_path) as im:
            img = im.convert("RGB")
        with Image.open(mask_path) as m:
            m = m if m.mode in ("L", "P") else m.convert("L")
            mask = np.array(m.resize((IMG_SIZE, IMG_SIZE), Image.NEAREST), dtype=np.int64)
        mask[mask >= self.num_classes] = 255
        return to_input(img, IMG_SIZE), torch.from_numpy(mask)


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("Device:", device, flush=True)
    classes = load_classes()
    if not classes:
        print("Error: no classes found (classes.txt) in", DATASET_DIR)
        sys.exit(1)
    samples = list_samples()
    print("Found", len(samples), "images with masks", flush=True)
    if len(samples) < 2:
        print("Error: need at least 2 images with masks in", MASK_DIR)
        sys.exit(1)

    num_classes = len(classes) + 1
    print("Classes:", ["background"] + classes, flush=True)

    batch = max(2, min(BATCH, len(samples)))
    if batch != BATCH:
        print("Batch size set to", batch, "(DeepLabV3 needs at least 2 images per batch)", flush=True)

    ds = SegmentationDataset(samples, num_classes)
    loader = DataLoader(ds, batch_size=batch, shuffle=True, drop_last=True)

    model = build_model(ARCH, num_classes, True).to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.SGD(params, lr=LR, momentum=0.9, weight_decay=0.0001)
    criterion = torch.nn.CrossEntropyLoss(ignore_index=255)

    model.train()
    for epoch in range(EPOCHS):
        running = 0.0
        for images, masks in loader:
            images = images.to(device)
            masks = masks.to(device)
            loss = criterion(model(images)["out"], masks)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            running += float(loss.item())
        print("Epoch %d/%d - loss: %.4f" % (epoch + 1, EPOCHS, running / max(1, len(loader))), flush=True)

    torch.save({"model_state": model.state_dict(), "classes": classes, "arch": ARCH, "img_size": IMG_SIZE}, OUT_PATH)
    print("Saved model to", OUT_PATH, flush=True)


if __name__ == "__main__":
    main()
'''
        return header + self._TORCH_MODEL_BODY + body

    def _build_infer_script(self, model_path, image_path, out_path, conf):
        header = (
            "MODEL_PATH = %r\n"
            "IMAGE_PATH = %r\n"
            "OUT_PATH = %r\n"
            "CONF = %r\n"
            "COLORS = %r\n"
        ) % (model_path, image_path, out_path, conf, MASK_COLORS)
        body = '''

def overlay(img, pred):
    palette = np.zeros((256, 3), dtype=np.float32)
    for i in range(1, 256):
        palette[i] = COLORS[(i - 1) % len(COLORS)]
    out = np.array(img, dtype=np.float32)
    sel = pred > 0
    out[sel] = out[sel] * 0.5 + palette[pred[sel]] * 0.5
    return Image.fromarray(out.astype(np.uint8))


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model, classes, size = load_trained(MODEL_PATH, device)
with Image.open(IMAGE_PATH) as im:
    img = im.convert("RGB")
pred = predict(model, img, size, device, CONF)

found = 0
for i, name in enumerate(classes, start=1):
    count = int((pred == i).sum())
    if count:
        found += 1
        r, g, b = COLORS[(i - 1) % len(COLORS)]
        print("%s (#%02X%02X%02X): %.2f%% of pixels" % (name, r, g, b, 100.0 * count / pred.size), flush=True)
if not found:
    print("No objects found above confidence %.2f" % CONF, flush=True)

overlay(img, pred).save(OUT_PATH)
print("Saved:", OUT_PATH, flush=True)
'''
        return header + self._TORCH_MODEL_BODY + body

    def _build_autolabel_script(self, model_path, dataset_dir, conf):
        header = (
            "MODEL_PATH = %r\n"
            "DATASET_DIR = %r\n"
            "CONF = %r\n"
        ) % (model_path, dataset_dir, conf)
        body = '''
IMG_DIR = os.path.join(DATASET_DIR, "images")
MASK_DIR = os.path.join(DATASET_DIR, "masks")
CLASSES_PATH = os.path.join(DATASET_DIR, "classes.txt")

try:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, model_classes, size = load_trained(MODEL_PATH, device)

    tags = []
    if os.path.exists(CLASSES_PATH):
        with open(CLASSES_PATH, "r", encoding="utf-8-sig") as f:
            tags = [line.strip() for line in f if line.strip()]
    lut = np.zeros(256, dtype=np.uint8)
    for i, name in enumerate(model_classes, start=1):
        if name not in tags:
            tags.append(name)
        lut[i] = tags.index(name) + 1
    with open(CLASSES_PATH, "w", encoding="utf-8") as f:
        f.write("\\n".join(tags) + "\\n")

    os.makedirs(MASK_DIR, exist_ok=True)
    labeled_count = 0
    skipped_count = 0

    for img_name in sorted(os.listdir(IMG_DIR)):
        if not img_name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        mask_path = os.path.join(MASK_DIR, os.path.splitext(img_name)[0] + ".png")
        if os.path.exists(mask_path):
            skipped_count += 1
            continue
        with Image.open(os.path.join(IMG_DIR, img_name)) as im:
            img = im.convert("RGB")
        mask = lut[predict(model, img, size, device, CONF)]
        if mask.any():
            Image.fromarray(mask).save(mask_path)
            labeled_count += 1
            print("Auto-labeled:", img_name, flush=True)

    print("\\n--- Auto-Labeling Complete ---")
    print("Added %d new masks." % labeled_count)
    print("Skipped %d existing masks." % skipped_count)
except Exception as e:
    print("Error during auto-labeling:", e)
'''
        return header + self._TORCH_MODEL_BODY + body

    # --- File Execution Methods ---
    def load_file(self, filename):
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()
            self.editor.delete("1.0", tk.END)
            self.editor.insert(tk.END, content)
            self.current_file = filename
            self.update_status(f"Status: Loaded {filename}")
        else:
            self.update_status(f"Status: {filename} not found")

    def save_file(self):
        if self.current_file:
            content = self.editor.get("1.0", "end-1c")
            with open(self.current_file, "w", encoding="utf-8") as f:
                f.write(content)
            self.update_status(f"Status: Saved {self.current_file}")
        else:
            self.update_status("Status: No file loaded to save")

    def run_file(self, filename):
        if not self.venv_python:
            self.update_status("Status: Error - Please activate environment first")
            return
        if os.path.exists(filename):
            if self.is_running:
                self.update_status("Status: Error - Another process is already running")
                return
            self.update_status(f"Status: Running {filename}...")
            self.console.delete("1.0", tk.END)
            self.append_console(f"--- Starting {filename} ---\n")
            self.start_task(lambda: self._run_file_thread(filename))
        else:
            self.update_status(f"Status: {filename} not found")

    def _run_file_thread(self, filename):
        returncode = self.stream_subprocess([self.venv_python, filename])
        self.root.after(0, self.append_console, f"\n--- Process finished with code {returncode} ---\n")

    # --- Dataset and Splitting Methods ---
    def split_dataset(self):
        try:
            train_pct = float(self.train_pct_var.get()) / 100.0
            if train_pct <= 0 or train_pct >= 1:
                raise ValueError
        except ValueError:
            self.update_status("Status: Error - Invalid split percentage")
            return

        img_dir = os.path.join("my_dataset", "images")
        mask_dir = os.path.join("my_dataset", "masks")

        if not os.path.exists(img_dir):
            self.update_status("Status: Error - No images found to split")
            return

        images = [f for f in os.listdir(img_dir) if f.lower().endswith(IMG_EXTS)]
        if not images:
            self.update_status("Status: Error - No images found to split")
            return

        random.shuffle(images)
        train_count = int(len(images) * train_pct)
        train_imgs = images[:train_count]
        val_imgs = images[train_count:]

        split_base = os.path.join("my_dataset", "split")
        if os.path.exists(split_base):
            shutil.rmtree(split_base)

        for part, part_imgs in (("train", train_imgs), ("val", val_imgs)):
            part_img_dir = os.path.join(split_base, part, "images")
            part_mask_dir = os.path.join(split_base, part, "masks")
            os.makedirs(part_img_dir, exist_ok=True)
            os.makedirs(part_mask_dir, exist_ok=True)
            for img in part_imgs:
                shutil.copy(os.path.join(img_dir, img), os.path.join(part_img_dir, img))
                mask = os.path.splitext(img)[0] + ".png"
                mask_path = os.path.join(mask_dir, mask)
                if os.path.exists(mask_path):
                    shutil.copy(mask_path, os.path.join(part_mask_dir, mask))
            classes_src = os.path.join("my_dataset", "classes.txt")
            if os.path.exists(classes_src):
                shutil.copy(classes_src, os.path.join(split_base, part, "classes.txt"))
            else:
                with open(os.path.join(split_base, part, "classes.txt"), "w", encoding="utf-8") as f:
                    f.write("\n".join(self.ds_editor.tags) + "\n")

        self.update_status(f"Status: Split {len(train_imgs)} Train / {len(val_imgs)} Val into my_dataset/split")

    # --- Auto-Labeling Methods ---
    def select_auto_model(self):
        filepath = filedialog.askopenfilename(title="Select Trained Model (.pth)", filetypes=[("PyTorch Model", "*.pth")])
        if filepath:
            self.auto_model_path = filepath
            model_name = os.path.basename(filepath)
            self.update_status(f"Status: Selected auto-label model: {model_name}")
            short_name = model_name if len(model_name) <= 12 else model_name[:10] + "..."
            self.auto_model_btn.config(text=f"Model: {short_name}", bg="#E07A5F", fg="#121212", font=("Arial", 9, "bold"))

    def auto_label_images(self):
        if not self.venv_python:
            self.update_status("Status: Error - Activate environment first")
            return
        if not self.auto_model_path or not os.path.exists(self.auto_model_path):
            self.update_status("Status: Error - Please select a trained model (.pth) first")
            return

        try:
            float(self.conf_pct_var.get())
        except ValueError:
            self.update_status("Status: Error - Invalid confidence percentage")
            return

        if self.is_running:
            self.update_status("Status: Error - Another process is already running")
            return

        self.notebook.select(self.tab_main)
        self.update_status("Status: Starting Auto-Labeling...")
        self.console.delete("1.0", tk.END)
        self.append_console("--- Starting Auto-Labeling on Remaining Images ---\n")
        self.start_task(self._auto_label_thread)

    def _auto_label_thread(self):
        script = self._build_autolabel_script(self.auto_model_path, "my_dataset", float(self.conf_pct_var.get()) / 100.0)
        with open("auto_label_script.py", "w", encoding="utf-8") as f:
            f.write(script)

        self.stream_subprocess([self.venv_python, "auto_label_script.py"])

        if os.path.exists("auto_label_script.py"):
            os.remove("auto_label_script.py")

        self.root.after(0, self.load_dataset_images)
        self.root.after(0, lambda: self.update_status("Status: Auto-Labeling Complete!"))

    # --- General Utilities ---
    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        x = self.root.winfo_pointerx() - self.x
        y = self.root.winfo_pointery() - self.y
        self.root.geometry(f"+{x}+{y}")

    def show_help(self):
        if os.path.exists("guide.html"):
            webbrowser.open("file://" + os.path.abspath("guide.html"))
            self.update_status("Status: Opened guide.html in browser")
        else:
            self.update_status("Status: Error - guide.html not found in folder")

    def update_status(self, text):
        self.status_lbl.config(text=text)

    def append_console(self, text):
        self.console.insert(tk.END, text)
        self.console.see(tk.END)

    # --- Environment setup ---
    def set_env_path(self):
        selected_path = filedialog.askdirectory(title="Select Environment Folder")
        if selected_path:
            self.env_path = selected_path
            self.save_settings()
            env_name = os.path.basename(self.env_path)
            self.update_status(f"Status: Env path set to {env_name}")
            self.activate_btn.config(bg="#333333", fg="#FFFFFF")
            self.venv_python = None

    def create_env(self):
        self.update_status("Status: Creating Environment & Dataset Folders...")
        self.start_task(self._create_env_thread)

    def _create_env_thread(self):
        subprocess.run([sys.executable, "-m", "venv", self.env_path], check=False)
        os.makedirs(os.path.join("my_dataset", "images"), exist_ok=True)
        os.makedirs(os.path.join("my_dataset", "masks"), exist_ok=True)

        classes_path = os.path.join("my_dataset", "classes.txt")
        if not os.path.exists(classes_path):
            with open(classes_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.ds_editor.tags) + "\n")

        if not os.path.exists("train_deeplab.py"):
            with open("train_deeplab.py", "w", encoding="utf-8") as f:
                f.write(self._build_train_script("my_dataset", ARCHS[0], 50, 2, 0.005, "model.pth", []))

        if not os.path.exists("run_deeplab.py"):
            with open("run_deeplab.py", "w", encoding="utf-8") as f:
                f.write(self._build_infer_script("model.pth", "test.jpg", "test_pred.jpg", 0.5))

        self.root.after(0, lambda: self.update_status("Status: Environment and Folders Created"))

    def activate_env(self):
        if os.name == "nt":
            python_path = os.path.join(self.env_path, "Scripts", "python.exe")
        else:
            python_path = os.path.join(self.env_path, "bin", "python")
        if os.path.exists(python_path):
            self.venv_python = python_path
            env_name = os.path.basename(os.path.abspath(self.env_path))
            self.update_status(f"Status: Activated ({env_name})")
            self.activate_btn.config(bg="#81B29A", fg="#FFFFFF")
        else:
            self.update_status("Status: Error - Environment not found")
            self.activate_btn.config(bg="#333333", fg="#FFFFFF")

    def install_requirements(self):
        if not self.venv_python:
            self.update_status("Status: Error - Please activate environment first")
            return
        self.update_status("Status: Installing Requirements...")
        self.console.delete("1.0", tk.END)
        self.append_console("--- Starting Installation ---\n")
        self.start_task(self._install_requirements_thread)

    def _install_requirements_thread(self):
        self.stream_subprocess([self.venv_python, "-m", "pip", "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu118"])
        self.stream_subprocess([self.venv_python, "-m", "pip", "install", "numpy", "pillow", "opencv-python"])

        self.root.after(0, self.append_console, "\n--- Installation Finished ---\n")
        self.root.after(0, lambda: self.update_status("Status: Requirements Installed"))

    def check_gpu(self):
        if not self.venv_python:
            self.update_status("Status: Error - Please activate environment first")
            return
        self.update_status("Status: Checking GPU...")
        self.console.delete("1.0", tk.END)
        self.append_console("--- Checking GPU / CUDA Status ---\n")
        self.start_task(self._check_gpu_thread)

    def _check_gpu_thread(self):
        code = "import torch\nprint(f'CUDA Available: {torch.cuda.is_available()}')\nif torch.cuda.is_available():\n    print(f'GPU Name: {torch.cuda.get_device_name(0)}')\n"
        self.stream_subprocess([self.venv_python, "-c", code])
        self.root.after(0, self.append_console, "\n--- Check Finished ---\n")
        self.root.after(0, lambda: self.update_status("Status: GPU Check Complete"))

    def open_venv_folder(self):
        venv_path = os.path.abspath(self.env_path)
        if os.path.exists(venv_path):
            if os.name == 'nt':
                os.startfile(venv_path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', venv_path])
            else:
                subprocess.Popen(['xdg-open', venv_path])
            self.update_status("Status: Opened env folder")
        else:
            self.update_status("Status: Error - env folder does not exist yet")

    def open_images_folder(self):
        img_path = os.path.abspath(os.path.join("my_dataset", "images"))
        if os.path.exists(img_path):
            if os.name == 'nt':
                os.startfile(img_path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', img_path])
            else:
                subprocess.Popen(['xdg-open', img_path])
            self.update_status("Status: Opened images folder")
        else:
            self.update_status("Status: Error - images folder does not exist yet")

    def load_dataset_images(self):
        self.ds_editor.load_folder("my_dataset")

if __name__ == "__main__":
    root = tk.Tk()
    app = TranigStudioApp(root)
    root.mainloop()
