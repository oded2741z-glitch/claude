import tkinter as tk
from tkinter import ttk, filedialog
import subprocess
import os
import sys
import threading
import random
import shutil
import re
import webbrowser

try:
    from PIL import Image, ImageTk
except ImportError:
    pass

class TranigStudioApp:
    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.geometry("1200x850")
        self.root.configure(bg="#121212", highlightbackground="#E07A5F", highlightcolor="#E07A5F", highlightthickness=1)

        self.venv_python = None
        self.current_file = None
        self.image_files = []
        self.current_img_index = -1
        self.current_photo = None
        self.rect_id = None
        self.start_x = 0
        self.start_y = 0
        self.img_width = 0
        self.img_height = 0
        
        self.tags = ["drone"]
        self.tag_var = tk.StringVar()
        self.chunk_sec_var = tk.StringVar(value="3.0")
        self.env_path = "venv"
        self.is_hidden = False
        
        self.auto_model_path = None
        self.selected_audio_files = []

        self.root.bind("<F4>", self.toggle_visibility)

        self.load_settings()
        self.setup_ui()
        self.load_tags_from_yaml()

    def toggle_visibility(self, event=None):
        if self.is_hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.attributes("-topmost", False)
            self.root.focus_force()
            self.is_hidden = False
        else:
            self.root.withdraw()
            self.is_hidden = True

    def load_settings(self):
        if os.path.exists("settings.txt"):
            try:
                with open("settings.txt", "r", encoding="utf-8") as f:
                    saved_path = f.read().strip()
                    if saved_path:
                        self.env_path = saved_path
            except:
                pass

    def save_settings(self):
        try:
            with open("settings.txt", "w", encoding="utf-8") as f:
                f.write(self.env_path)
        except:
            pass

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

        quit_btn = tk.Button(top_bar, text="Quit", bg="#FF6B00", fg="#FFFFFF", relief="flat", borderwidth=0, padx=15, pady=5, command=self.root.destroy)
        quit_btn.pack(side=tk.RIGHT, padx=5, pady=5)

        help_btn = tk.Button(top_bar, text="Help", bg="#333333", fg="#FFFFFF", relief="flat", borderwidth=0, padx=15, pady=5, command=self.show_help)
        help_btn.pack(side=tk.RIGHT, padx=5, pady=5)

        # --- Tabs Container ---
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # TAB 1: Vision Studio
        self.tab_main = tk.Frame(self.notebook, bg="#121212")
        self.notebook.add(self.tab_main, text=" Vision Studio ")

        # TAB 2: Auto-Labeling Studio
        self.tab_auto = tk.Frame(self.notebook, bg="#121212")
        self.notebook.add(self.tab_auto, text=" Auto-Labeling Studio ")
        self.setup_auto_tab()

        # TAB 3: Audio-to-Image
        self.tab_audio = tk.Frame(self.notebook, bg="#121212")
        self.notebook.add(self.tab_audio, text=" Audio-to-Image ")
        self.setup_audio_tab()

        btn_args = {"bg": "#333333", "fg": "#FFFFFF", "relief": "flat", "borderwidth": 0, "padx": 10, "pady": 5}

        # --- MAIN TAB CONTENT ---
        controls_container = tk.Frame(self.tab_main, bg="#121212")
        controls_container.pack(fill=tk.X, padx=0, pady=5)

        files_lf = ttk.LabelFrame(controls_container, text=" File Execution ")
        files_lf.pack(side=tk.LEFT, padx=(0, 5), pady=5, fill=tk.Y)
        
        files_top = tk.Frame(files_lf, bg="#121212")
        files_top.pack(fill=tk.X, pady=2, padx=5)
        tk.Button(files_top, text="Load train_yolo.py", command=lambda: self.load_file("train_yolo.py"), **btn_args).pack(side=tk.LEFT, padx=2)
        tk.Button(files_top, text="Load run_yolo.py", command=lambda: self.load_file("run_yolo.py"), **btn_args).pack(side=tk.LEFT, padx=2)
        
        self.yaml_var = tk.StringVar(value="data.yaml")
        self.yaml_cb = ttk.Combobox(files_top, textvariable=self.yaml_var, font=("Arial", 10), width=15)
        self.yaml_cb['values'] = ["data.yaml", "data_split.yaml"]
        self.yaml_cb.pack(side=tk.LEFT, padx=5)
        
        self.yaml_cb.bind("<<ComboboxSelected>>", self.update_yaml_in_script)
        self.yaml_cb.bind("<Return>", self.update_yaml_in_script)
        self.yaml_cb.bind("<FocusOut>", self.update_yaml_in_script)
        
        tk.Button(files_top, text="Load Selected YAML", command=self.load_selected_yaml, **btn_args).pack(side=tk.LEFT, padx=2)
        
        files_bot = tk.Frame(files_lf, bg="#121212")
        files_bot.pack(fill=tk.X, pady=2, padx=5)
        tk.Button(files_bot, text="Run train_yolo.py", command=lambda: self.run_file("train_yolo.py"), **btn_args).pack(side=tk.LEFT, padx=2)
        
        tk.Label(files_bot, text="Model:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(10, 2))
        self.model_var = tk.StringVar(value="yolov8n.pt")
        self.model_cb = ttk.Combobox(files_bot, textvariable=self.model_var, font=("Arial", 10), width=12)
        self.model_cb['values'] = ["yolov8n.pt", "yolov8s.pt", "yolov8m.pt"]
        self.model_cb.pack(side=tk.LEFT, padx=2)
        self.model_cb.bind("<<ComboboxSelected>>", self.update_model_in_editor)
        self.model_cb.bind("<Return>", self.update_model_in_editor)
        self.model_cb.bind("<FocusOut>", self.update_model_in_editor)

        tk.Button(files_bot, text="Run run_yolo.py", command=lambda: self.run_file("run_yolo.py"), **btn_args).pack(side=tk.LEFT, padx=(10, 2))

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

        dataset_lf = ttk.LabelFrame(right_frame, text=" Dataset Images & Audio ")
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
        self.tag_cb['values'] = self.tags
        if self.tags:
            self.tag_cb.current(0)
        self.tag_cb.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        viewer_controls_bot = tk.Frame(dataset_lf, bg="#121212")
        viewer_controls_bot.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(viewer_controls_bot, text="Prev", command=self.prev_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(viewer_controls_bot, text="Next", command=self.next_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(viewer_controls_bot, text="Play Audio", command=self.play_audio, **btn_args).pack(side=tk.LEFT, padx=5)
        
        tk.Button(viewer_controls_bot, text="Delete Image", command=self.delete_current_image, bg="#E04A3F", fg="#FFFFFF", relief="flat", borderwidth=0, padx=10, pady=5, font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=5)
        tk.Button(viewer_controls_bot, text="Clear Labels", command=self.clear_labels, **btn_args).pack(side=tk.RIGHT, padx=5)
        
        self.img_counter_lbl = tk.Label(viewer_controls_bot, text="0 / 0", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold"))
        self.img_counter_lbl.pack(expand=True)

        self.img_canvas = tk.Canvas(right_frame, bg="#000000", highlightthickness=1, highlightbackground="#333333")
        self.img_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.img_canvas.bind("<ButtonPress-1>", self.on_draw_start)
        self.img_canvas.bind("<B1-Motion>", self.on_draw_motion)
        self.img_canvas.bind("<ButtonRelease-1>", self.on_draw_release)

        watermark = tk.Label(self.root, text="oT", bg="#121212", fg="#555555", font=("Arial", 9, "bold"))
        watermark.pack(side=tk.BOTTOM, anchor=tk.SE, padx=5, pady=2)

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

        review_lf = ttk.LabelFrame(controls_frame, text=" 2. Review & Edit Labels ")
        review_lf.pack(side=tk.TOP, fill=tk.X, pady=5)

        review_top = tk.Frame(review_lf, bg="#121212")
        review_top.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(review_top, text="Load Dataset Images", command=self.load_dataset_images, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(review_top, text="Open Images Folder", command=self.open_images_folder, **btn_args).pack(side=tk.LEFT, padx=5)

        tk.Label(review_top, text="Tag:", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=15)
        self.auto_tag_cb = ttk.Combobox(review_top, textvariable=self.tag_var, font=("Arial", 10))
        self.auto_tag_cb['values'] = self.tags
        self.auto_tag_cb.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        review_bot = tk.Frame(review_lf, bg="#121212")
        review_bot.pack(fill=tk.X, pady=5, padx=5)
        tk.Button(review_bot, text="Prev", command=self.prev_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(review_bot, text="Next", command=self.next_image, **btn_args).pack(side=tk.LEFT, padx=5)
        tk.Button(review_bot, text="Play Audio", command=self.play_audio, **btn_args).pack(side=tk.LEFT, padx=5)
        
        tk.Button(review_bot, text="Delete Image", command=self.delete_current_image, bg="#E04A3F", fg="#FFFFFF", relief="flat", borderwidth=0, padx=10, pady=5, font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=5)
        tk.Button(review_bot, text="Clear Current Image Labels", command=self.clear_labels, **btn_args).pack(side=tk.RIGHT, padx=5)

        self.auto_img_counter_lbl = tk.Label(review_bot, text="0 / 0", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold"))
        self.auto_img_counter_lbl.pack(expand=True)

        self.auto_img_canvas = tk.Canvas(self.tab_auto, bg="#000000", highlightthickness=1, highlightbackground="#333333")
        self.auto_img_canvas.pack(fill=tk.BOTH, expand=True, padx=20, pady=(5, 20))
        self.auto_img_canvas.bind("<ButtonPress-1>", self.on_draw_start)
        self.auto_img_canvas.bind("<B1-Motion>", self.on_draw_motion)
        self.auto_img_canvas.bind("<ButtonRelease-1>", self.on_draw_release)

    def setup_audio_tab(self):
        lbl = tk.Label(self.tab_audio, text="Audio-to-Image (Mel-Spectrograms & Chunking)", bg="#121212", fg="#E07A5F", font=("Arial", 16, "bold"))
        lbl.pack(pady=(30, 5))

        desc = tk.Label(self.tab_audio, text="Select audio files. They will be split into chunks, converted to visual features, and saved with matching .wav files.", bg="#121212", fg="#AAAAAA", font=("Arial", 11))
        desc.pack(pady=5)

        btn_frame = tk.Frame(self.tab_audio, bg="#121212")
        btn_frame.pack(pady=15)

        btn_args = {"bg": "#333333", "fg": "#FFFFFF", "relief": "flat", "borderwidth": 0, "padx": 20, "pady": 8, "font": ("Arial", 10, "bold")}

        tk.Button(btn_frame, text="Browse Audio Files", command=self.browse_audio, **btn_args).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Clear List", command=self.clear_audio, **btn_args).pack(side=tk.LEFT, padx=10)
        
        tk.Label(btn_frame, text="Chunk Length (sec):", bg="#121212", fg="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)
        tk.Entry(btn_frame, textvariable=self.chunk_sec_var, width=5, bg="#333333", fg="#FFFFFF", relief="flat", insertbackground="#FFFFFF", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)

        tk.Button(btn_frame, text="Convert & Chunk", command=self.convert_audio, bg="#81B29A", fg="#121212", font=("Arial", 10, "bold"), relief="flat", borderwidth=0, padx=20, pady=8).pack(side=tk.LEFT, padx=10)

        list_frame = tk.Frame(self.tab_audio, bg="#121212")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=50, pady=(10, 40))

        self.audio_listbox = tk.Listbox(list_frame, bg="#000000", fg="#FFFFFF", selectbackground="#E07A5F", relief="flat", borderwidth=1, highlightthickness=1, highlightbackground="#555555", font=("Consolas", 11))
        self.audio_listbox.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.audio_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.audio_listbox.config(yscrollcommand=scrollbar.set)

    # --- File Execution Methods ---
    def load_selected_yaml(self):
        filename = self.yaml_var.get()
        self.load_file(filename)

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
            self.update_status(f"Status: Running {filename}...")
            self.console.delete("1.0", tk.END)
            self.append_console(f"--- Starting {filename} ---\n")
            threading.Thread(target=self._run_file_thread, args=(filename,)).start()
        else:
            self.update_status(f"Status: {filename} not found")

    def _run_file_thread(self, filename):
        command = [self.venv_python, filename]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in iter(process.stdout.readline, ''):
            if line:
                self.root.after(0, self.append_console, line)
        process.stdout.close()
        process.wait()
        self.root.after(0, self.append_console, f"\n--- Process finished with code {process.returncode} ---\n")

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
        lbl_dir = os.path.join("my_dataset", "labels")
        
        if not os.path.exists(img_dir):
            self.update_status("Status: Error - No images found to split")
            return
            
        images = [f for f in os.listdir(img_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
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
            
        train_img_dir = os.path.join(split_base, "train", "images")
        train_lbl_dir = os.path.join(split_base, "train", "labels")
        val_img_dir = os.path.join(split_base, "val", "images")
        val_lbl_dir = os.path.join(split_base, "val", "labels")
        
        for d in [train_img_dir, train_lbl_dir, val_img_dir, val_lbl_dir]:
            os.makedirs(d, exist_ok=True)
            
        for img in train_imgs:
            shutil.copy(os.path.join(img_dir, img), os.path.join(train_img_dir, img))
            lbl = os.path.splitext(img)[0] + ".txt"
            lbl_path = os.path.join(lbl_dir, lbl)
            if os.path.exists(lbl_path):
                shutil.copy(lbl_path, os.path.join(train_lbl_dir, lbl))
                
        for img in val_imgs:
            shutil.copy(os.path.join(img_dir, img), os.path.join(val_img_dir, img))
            lbl = os.path.splitext(img)[0] + ".txt"
            lbl_path = os.path.join(lbl_dir, lbl)
            if os.path.exists(lbl_path):
                shutil.copy(lbl_path, os.path.join(val_lbl_dir, lbl))
                
        yaml_path = "data_split.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write("path: my_dataset/split\n")
            f.write("train: train/images\n")
            f.write("val: val/images\n\n")
            f.write("names:\n")
            for i, t in enumerate(self.tags):
                f.write(f"  {i}: {t}\n")
                
        if yaml_path not in self.yaml_cb['values']:
            vals = list(self.yaml_cb['values'])
            vals.append(yaml_path)
            self.yaml_cb['values'] = vals
            
        self.yaml_cb.set(yaml_path)
        self.update_status(f"Status: Split {len(train_imgs)} Train / {len(val_imgs)} Val")

    # --- Audio Processing Methods (CHUNK & CONVERT) ---
    def browse_audio(self):
        files = filedialog.askopenfilenames(title="Select Audio Files", filetypes=[("Audio Files", "*.wav *.mp3 *.ogg *.flac")])
        if files:
            for f in files:
                if f not in self.selected_audio_files:
                    self.selected_audio_files.append(f)
                    self.audio_listbox.insert(tk.END, os.path.basename(f))
            self.update_status(f"Status: Loaded {len(self.selected_audio_files)} audio files")

    def clear_audio(self):
        self.selected_audio_files = []
        self.audio_listbox.delete(0, tk.END)
        self.update_status("Status: Audio list cleared")

    def convert_audio(self):
        if not self.venv_python:
            self.update_status("Status: Error - Activate environment first")
            return
        if not self.selected_audio_files:
            self.update_status("Status: Error - No audio files selected")
            return
        try:
            float(self.chunk_sec_var.get())
        except ValueError:
            self.update_status("Status: Error - Invalid Chunk Length")
            return

        self.notebook.select(self.tab_main) 
        self.update_status("Status: Cutting and Converting Audio...")
        self.console.delete("1.0", tk.END)
        self.append_console("--- Starting Audio-to-Image Conversion & Chunking ---\n")
        self.append_console("Note: Ensure you clicked 'Install Requirements' first.\n\n")

        threading.Thread(target=self._convert_audio_thread).start()

    def _convert_audio_thread(self):
        script = f"""
import os
import sys
import math

try:
    import librosa
    import librosa.display
    import matplotlib.pyplot as plt
    import numpy as np
    import soundfile as sf
except ImportError:
    print("Error: Required libraries (librosa, matplotlib, soundfile) not found.")
    print("Please click 'Install Requirements' in the UI to install them.")
    sys.exit(1)

audio_files = {self.selected_audio_files}
chunk_length_sec = {float(self.chunk_sec_var.get())}
out_dir = os.path.join('my_dataset', 'images')
os.makedirs(out_dir, exist_ok=True)

success_count = 0
for audio_path in audio_files:
    try:
        print(f"Processing: {{os.path.basename(audio_path)}}")
        y, sr = librosa.load(audio_path, sr=None)
        
        total_duration = librosa.get_duration(y=y, sr=sr)
        num_chunks = math.ceil(total_duration / chunk_length_sec)
        
        for i in range(num_chunks):
            start_sample = int(i * chunk_length_sec * sr)
            end_sample = int((i + 1) * chunk_length_sec * sr)
            y_chunk = y[start_sample:end_sample]
            
            # Skip chunks that are way too short (less than 0.1 seconds)
            if len(y_chunk) < sr * 0.1:
                continue
                
            base_name = os.path.splitext(os.path.basename(audio_path))[0]
            chunk_name = f"{{base_name}}_chunk_{{i:03d}}"
            
            # Save corresponding .wav chunk
            wav_out = os.path.join(out_dir, f"{{chunk_name}}.wav")
            sf.write(wav_out, y_chunk, sr)
            
            # Create Mel-spectrogram
            S = librosa.feature.melspectrogram(y=y_chunk, sr=sr, n_mels=128, fmax=8000)
            S_dB = librosa.power_to_db(S, ref=np.max)

            plt.figure(figsize=(5, 5))
            librosa.display.specshow(S_dB, sr=sr, fmax=8000)
            plt.axis('off')
            plt.tight_layout(pad=0)

            png_out = os.path.join(out_dir, f"{{chunk_name}}.png")
            plt.savefig(png_out, bbox_inches='tight', pad_inches=0)
            plt.close()
            
            print(f"  -> Saved: {{chunk_name}}.png / .wav")
            success_count += 1
            
    except Exception as e:
        print(f"Error with {{audio_path}}: {{e}}")

print(f"\\n--- Conversion Complete! {{success_count}} chunks generated. ---")
"""
        script_path = "convert_audio_script.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        cmd = [self.venv_python, script_path]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in iter(process.stdout.readline, ''):
            if line:
                self.root.after(0, self.append_console, line)
        process.wait()

        if os.path.exists(script_path):
            os.remove(script_path)

        self.root.after(0, self.load_dataset_images)
        self.root.after(0, lambda: self.update_status("Status: Audio Chunking & Conversion Finished!"))

    # --- Script Modification Methods ---
    def update_yaml_in_script(self, event=None):
        new_yaml = self.yaml_var.get().strip()
        if not new_yaml:
            return

        if self.current_file == "train_yolo.py":
            content = self.editor.get("1.0", "end-1c")
            updated_content = re.sub(r"data=['\"].*?['\"]", f"data='{new_yaml}'", content)
            if updated_content != content:
                self.editor.delete("1.0", tk.END)
                self.editor.insert(tk.END, updated_content)
                self.update_status(f"Status: YAML updated to {new_yaml} in editor")
        
        elif os.path.exists("train_yolo.py"):
            with open("train_yolo.py", "r", encoding="utf-8") as f:
                content = f.read()
            updated_content = re.sub(r"data=['\"].*?['\"]", f"data='{new_yaml}'", content)
            if updated_content != content:
                with open("train_yolo.py", "w", encoding="utf-8") as f:
                    f.write(updated_content)
                self.update_status(f"Status: YAML updated to {new_yaml} in train_yolo.py (background)")

    def update_model_in_editor(self, event=None):
        new_model = self.model_var.get().strip()
        if not new_model:
            return
            
        if self.current_file in ["train_yolo.py", "run_yolo.py"]:
            content = self.editor.get("1.0", "end-1c")
            updated_content = re.sub(r"YOLO\(['\"].*?['\"]\)", f"YOLO('{new_model}')", content)
            if updated_content != content:
                self.editor.delete("1.0", tk.END)
                self.editor.insert(tk.END, updated_content)
                self.update_status(f"Status: Model updated to {new_model} in editor")
                
        for filename in ["train_yolo.py", "run_yolo.py"]:
            if self.current_file != filename and os.path.exists(filename):
                with open(filename, "r", encoding="utf-8") as f:
                    content = f.read()
                updated_content = re.sub(r"YOLO\(['\"].*?['\"]\)", f"YOLO('{new_model}')", content)
                if updated_content != content:
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(updated_content)

    # --- Auto-Labeling Methods ---
    def select_auto_model(self):
        filepath = filedialog.askopenfilename(title="Select Trained Model (.pt)", filetypes=[("PyTorch Model", "*.pt")])
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
            self.update_status("Status: Error - Please select a trained model (.pt) first")
            return
            
        try:
            float(self.conf_pct_var.get())
        except ValueError:
            self.update_status("Status: Error - Invalid confidence percentage")
            return
            
        self.notebook.select(self.tab_main) 
        self.update_status("Status: Starting Auto-Labeling...")
        self.console.delete("1.0", tk.END)
        self.append_console("--- Starting Auto-Labeling on Remaining Images ---\n")
        threading.Thread(target=self._auto_label_thread).start()

    def _auto_label_thread(self):
        script = f"""
from ultralytics import YOLO
import os

try:
    model = YOLO(r'{self.auto_model_path}')
    img_dir = os.path.join('my_dataset', 'images')
    lbl_dir = os.path.join('my_dataset', 'labels')
    conf_thresh = {float(self.conf_pct_var.get()) / 100.0}
    
    if not os.path.exists(lbl_dir):
        os.makedirs(lbl_dir)
        
    labeled_count = 0
    skipped_count = 0
    
    for img_name in os.listdir(img_dir):
        if not img_name.lower().endswith(('.png', '.jpg', '.jpeg')):
            continue
            
        base_name = os.path.splitext(img_name)[0]
        lbl_path = os.path.join(lbl_dir, base_name + '.txt')
        
        if os.path.exists(lbl_path):
            skipped_count += 1
            continue
            
        img_path = os.path.join(img_dir, img_name)
        results = model(img_path, conf=conf_thresh, verbose=False)
        
        for r in results:
            boxes = r.boxes
            if len(boxes) > 0:
                with open(lbl_path, 'w', encoding='utf-8') as f:
                    for box in boxes:
                        cls_id = int(box.cls[0].item())
                        cx, cy, w, h = box.xywhn[0].tolist()
                        f.write(f"{{cls_id}} {{cx:.6f}} {{cy:.6f}} {{w:.6f}} {{h:.6f}}\\n")
                labeled_count += 1
                print(f"Auto-labeled: {{img_name}}")
                
    print(f"\\n--- Auto-Labeling Complete ---")
    print(f"Added {{labeled_count}} new labels.")
    print(f"Skipped {{skipped_count}} existing labels.")
except Exception as e:
    print(f"Error during auto-labeling: {{e}}")
"""
        with open("auto_label_script.py", "w", encoding="utf-8") as f:
            f.write(script)
            
        cmd = [self.venv_python, "auto_label_script.py"]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in iter(process.stdout.readline, ''):
            if line:
                self.root.after(0, self.append_console, line)
        process.wait()
        
        if os.path.exists("auto_label_script.py"):
            os.remove("auto_label_script.py")
            
        self.root.after(0, self.load_dataset_images) 
        self.root.after(0, lambda: self.update_status("Status: Auto-Labeling Complete!"))

    # --- Tags and YAML Configuration ---
    def load_tags_from_yaml(self):
        if os.path.exists("data.yaml"):
            try:
                with open("data.yaml", "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    in_names = False
                    loaded_tags = []
                    for line in lines:
                        if line.strip().startswith("names:"):
                            in_names = True
                            continue
                        if in_names:
                            parts = line.strip().split(":")
                            if len(parts) == 2:
                                tag_val = parts[1].strip().strip("'").strip('"')
                                loaded_tags.append(tag_val)
                    if loaded_tags:
                        self.tags = loaded_tags
                        self.tag_cb['values'] = self.tags
                        if hasattr(self, 'auto_tag_cb'):
                            self.auto_tag_cb['values'] = self.tags
                        self.tag_var.set(self.tags[0])
            except:
                pass

    def update_data_yaml(self):
        if os.path.exists("data.yaml"):
            with open("data.yaml", "w", encoding="utf-8") as f:
                f.write("path: my_dataset\n")
                f.write("train: images\n")
                f.write("val: images\n\n")
                f.write("names:\n")
                for i, t in enumerate(self.tags):
                    f.write(f"  {i}: {t}\n")

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
            webbrowser.open("guide.html")
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
        threading.Thread(target=self._create_env_thread).start()

    def _create_env_thread(self):
        subprocess.run([sys.executable, "-m", "venv", self.env_path])
        os.makedirs(os.path.join("my_dataset", "images"), exist_ok=True)
        os.makedirs(os.path.join("my_dataset", "labels"), exist_ok=True)
        
        if not os.path.exists("data.yaml"):
            with open("data.yaml", "w", encoding="utf-8") as f:
                f.write("path: my_dataset\n")
                f.write("train: images\n")
                f.write("val: images\n\n")
                f.write("names:\n")
                for i, t in enumerate(self.tags):
                    f.write(f"  {i}: {t}\n")
                    
        if not os.path.exists("train_yolo.py"):
            with open("train_yolo.py", "w", encoding="utf-8") as f:
                f.write("from ultralytics import YOLO\n\n")
                f.write("# Start from the base model\n")
                f.write("model = YOLO('yolov8n.pt')\n\n")
                f.write("# Train the model on your custom dataset\n")
                f.write("results = model.train(data='data.yaml', epochs=50, imgsz=640, device=0, workers=0)\n")
                
        if not os.path.exists("run_yolo.py"):
            with open("run_yolo.py", "w", encoding="utf-8") as f:
                f.write("from ultralytics import YOLO\n\n")
                f.write("# Load your custom trained model\n")
                f.write("model = YOLO('My_model.pt')\n\n")
                f.write("# Run inference on a test image\n")
                f.write("results = model('test.jpg', save=True, device=0)\n")
                
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
        threading.Thread(target=self._install_requirements_thread).start()

    def _install_requirements_thread(self):
        cmd1 = [self.venv_python, "-m", "pip", "install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu118"]
        process1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in iter(process1.stdout.readline, ''):
            if line:
                self.root.after(0, self.append_console, line)
        process1.wait()

        # Added 'soundfile' for saving wav chunks
        cmd2 = [self.venv_python, "-m", "pip", "install", "ultralytics", "librosa", "matplotlib", "soundfile"]
        process2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in iter(process2.stdout.readline, ''):
            if line:
                self.root.after(0, self.append_console, line)
        process2.wait()

        self.root.after(0, self.append_console, "\n--- Installation Finished ---\n")
        self.root.after(0, lambda: self.update_status("Status: Requirements Installed"))

    def check_gpu(self):
        if not self.venv_python:
            self.update_status("Status: Error - Please activate environment first")
            return
        self.update_status("Status: Checking GPU...")
        self.console.delete("1.0", tk.END)
        self.append_console("--- Checking GPU / CUDA Status ---\n")
        threading.Thread(target=self._check_gpu_thread).start()

    def _check_gpu_thread(self):
        code = "import torch\nprint(f'CUDA Available: {torch.cuda.is_available()}')\nif torch.cuda.is_available():\n    print(f'GPU Name: {torch.cuda.get_device_name(0)}')\n"
        cmd = [self.venv_python, "-c", code]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in iter(process.stdout.readline, ''):
            if line:
                self.root.after(0, self.append_console, line)
        process.wait()
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

    # --- Canvas, Image Display, Play, and Delete ---
    def load_dataset_images(self):
        img_dir = os.path.join("my_dataset", "images")
        self.image_files = []
        if os.path.exists(img_dir):
            for f in os.listdir(img_dir):
                if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                    self.image_files.append(os.path.join(img_dir, f))
        if self.image_files:
            self.img_canvas.configure(bg="#121212")
            if hasattr(self, 'auto_img_canvas'):
                self.auto_img_canvas.configure(bg="#121212")
            self.current_img_index = 0
            self.show_image()
            self.update_status(f"Status: Loaded {len(self.image_files)} images")
        else:
            self.img_canvas.delete("all")
            self.img_canvas.configure(bg="#000000")
            self.img_counter_lbl.config(text="0 / 0")
            if hasattr(self, 'auto_img_canvas'):
                self.auto_img_canvas.delete("all")
                self.auto_img_canvas.configure(bg="#000000")
                self.auto_img_counter_lbl.config(text="0 / 0")
            self.update_status("Status: No images found")

    def show_image(self):
        if 0 <= self.current_img_index < len(self.image_files):
            img_path = self.image_files[self.current_img_index]
            self.img_canvas.delete("all")
            if hasattr(self, 'auto_img_canvas'):
                self.auto_img_canvas.delete("all")
            try:
                if 'PIL.Image' in sys.modules:
                    img = Image.open(img_path)
                    img.thumbnail((400, 400))
                    self.img_width = img.width
                    self.img_height = img.height
                    self.current_photo = ImageTk.PhotoImage(img)
                else:
                    self.current_photo = tk.PhotoImage(file=img_path)
                    self.img_width = self.current_photo.width()
                    self.img_height = self.current_photo.height()
                
                self.img_canvas.create_image(0, 0, anchor=tk.NW, image=self.current_photo)
                if hasattr(self, 'auto_img_canvas'):
                    self.auto_img_canvas.create_image(0, 0, anchor=tk.NW, image=self.current_photo)
                
                counter_text = f"{self.current_img_index + 1} / {len(self.image_files)}"
                self.img_counter_lbl.config(text=counter_text)
                if hasattr(self, 'auto_img_counter_lbl'):
                    self.auto_img_counter_lbl.config(text=counter_text)
                    
                self.draw_existing_labels(img_path)
            except Exception as e:
                self.update_status(f"Status: Error loading image {e}")

    def draw_existing_labels(self, img_path):
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        label_path = os.path.join("my_dataset", "labels", f"{base_name}.txt")
        if os.path.exists(label_path):
            with open(label_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) == 5:
                        try:
                            cls_id_float, cx, cy, w, h = map(float, parts)
                            cls_id = int(cls_id_float)
                            tag_name = self.tags[cls_id] if cls_id < len(self.tags) else str(cls_id)
                            x1 = (cx - w/2) * self.img_width
                            y1 = (cy - h/2) * self.img_height
                            x2 = (cx + w/2) * self.img_width
                            y2 = (cy + h/2) * self.img_height
                            
                            self.img_canvas.create_rectangle(x1, y1, x2, y2, outline="#E07A5F", width=2)
                            self.img_canvas.create_text(x1, max(0, y1-10), text=tag_name, fill="#E07A5F", anchor=tk.W, font=("Arial", 10, "bold"))
                            
                            if hasattr(self, 'auto_img_canvas'):
                                self.auto_img_canvas.create_rectangle(x1, y1, x2, y2, outline="#E07A5F", width=2)
                                self.auto_img_canvas.create_text(x1, max(0, y1-10), text=tag_name, fill="#E07A5F", anchor=tk.W, font=("Arial", 10, "bold"))
                                
                        except ValueError:
                            pass

    def prev_image(self):
        if self.image_files and self.current_img_index > 0:
            self.current_img_index -= 1
            self.show_image()

    def next_image(self):
        if self.image_files and self.current_img_index < len(self.image_files) - 1:
            self.current_img_index += 1
            self.show_image()

    def clear_labels(self):
        if not self.image_files or self.current_img_index < 0:
            return
        img_path = self.image_files[self.current_img_index]
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        label_path = os.path.join("my_dataset", "labels", f"{base_name}.txt")
        if os.path.exists(label_path):
            os.remove(label_path)
        self.show_image()
        self.update_status(f"Status: Cleared labels for {base_name}")

    def play_audio(self):
        if not self.image_files or self.current_img_index < 0:
            return
        img_path = self.image_files[self.current_img_index]
        base_name = os.path.splitext(img_path)[0]
        wav_path = base_name + ".wav"
        
        if os.path.exists(wav_path):
            self.update_status(f"Status: Playing {os.path.basename(wav_path)}...")
            if os.name == 'nt':
                import winsound
                winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                import subprocess
                if sys.platform == 'darwin':
                    subprocess.Popen(['afplay', wav_path])
                else:
                    subprocess.Popen(['aplay', wav_path])
        else:
            self.update_status("Status: Error - No associated .wav file found for this image.")

    def delete_current_image(self):
        if not self.image_files or self.current_img_index < 0:
            return
            
        img_path = self.image_files[self.current_img_index]
        base_name = os.path.splitext(img_path)[0]
        wav_path = base_name + ".wav"
        lbl_path = os.path.join("my_dataset", "labels", os.path.basename(base_name) + ".txt")

        try:
            os.remove(img_path)
            if os.path.exists(wav_path):
                os.remove(wav_path)
            if os.path.exists(lbl_path):
                os.remove(lbl_path)
        except Exception as e:
            self.update_status(f"Status: Error deleting file(s) - {e}")
            return

        del self.image_files[self.current_img_index]

        if len(self.image_files) == 0:
            self.current_img_index = -1
            self.img_canvas.delete("all")
            if hasattr(self, 'auto_img_canvas'):
                self.auto_img_canvas.delete("all")
            self.img_counter_lbl.config(text="0 / 0")
            if hasattr(self, 'auto_img_counter_lbl'):
                self.auto_img_counter_lbl.config(text="0 / 0")
            self.update_status("Status: Image deleted. No more images left.")
        else:
            if self.current_img_index >= len(self.image_files):
                self.current_img_index = len(self.image_files) - 1
            self.show_image()
            self.update_status("Status: Image and related data deleted successfully.")

    def on_draw_start(self, event):
        if not self.image_files or self.current_img_index < 0:
            return
        self.start_x = event.x
        self.start_y = event.y
        self.rect_id = event.widget.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline="#E07A5F", width=2)

    def on_draw_motion(self, event):
        if self.rect_id:
            event.widget.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_draw_release(self, event):
        if not self.rect_id:
            return
        x1, y1, x2, y2 = event.widget.coords(self.rect_id)
        
        x1 = max(0, min(x1, self.img_width))
        x2 = max(0, min(x2, self.img_width))
        y1 = max(0, min(y1, self.img_height))
        y2 = max(0, min(y2, self.img_height))
        
        bw = abs(x2 - x1)
        bh = abs(y2 - y1)
        
        if bw < 5 or bh < 5:
            event.widget.delete(self.rect_id)
            self.rect_id = None
            return
            
        cx = min(x1, x2) + bw / 2
        cy = min(y1, y2) + bh / 2
        
        current_tag = self.tag_var.get().strip()
        if not current_tag:
            current_tag = "object"
            self.tag_var.set(current_tag)
            
        if current_tag not in self.tags:
            self.tags.append(current_tag)
            self.tag_cb['values'] = self.tags
            if hasattr(self, 'auto_tag_cb'):
                self.auto_tag_cb['values'] = self.tags
            self.update_data_yaml()
            
        class_id = self.tags.index(current_tag)
        
        if self.img_width > 0 and self.img_height > 0:
            yx = cx / self.img_width
            yy = cy / self.img_height
            yw = bw / self.img_width
            yh = bh / self.img_height
            
            img_path = self.image_files[self.current_img_index]
            base_name = os.path.splitext(os.path.basename(img_path))[0]
            label_path = os.path.join("my_dataset", "labels", f"{base_name}.txt")
            
            with open(label_path, "a", encoding="utf-8") as f:
                f.write(f"{class_id} {yx:.6f} {yy:.6f} {yw:.6f} {yh:.6f}\n")
                
            self.update_status(f"Status: Saved box '{current_tag}' to {base_name}.txt")
        
        self.rect_id = None
        self.show_image()

if __name__ == "__main__":
    root = tk.Tk()
    app = TranigStudioApp(root)
    root.mainloop()