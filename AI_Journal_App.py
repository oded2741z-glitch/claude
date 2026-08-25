import tkinter as tk
import threading
import json
import os
import sys
import io
import google.generativeai as genai
from gtts import gTTS
import pygame
from datetime import datetime
from PIL import Image, ImageTk
import speech_recognition as sr

CONFIG_FILE = "config.json"
HISTORY_FILE = "journal_history.json"

# --- GLOBAL FONT SETTING ---
MAIN_FONT = "Georgia"
FONT_STYLE = "italic"

class AIJournalHardcoded:
    def __init__(self, root):
        self.root = root
        self.root.title("DAILY JOURNAL")
        self.root.geometry("1000x850")
        self.root.configure(bg="#F9F9F8")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        pygame.mixer.init()
        self.config_data = self.load_config()
        self.api_key = self.config_data.get("api_key", "")
        self.user_name = self.config_data.get("user_name", "My")
        self.user_password = self.config_data.get("password", "")

        self.current_mood = ""
        now = datetime.now()
        self.current_year = now.year
        
        self.month_names_short = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        self.current_month = self.month_names_short[now.month - 1]
        self.current_day = str(now.day)
        self.current_weekday = "" 
        
        self.journal_data = self.load_journal_data()
        self.auto_save_timer = None
        self.dragged = False
        
        self.error_text_id = None 

        # --- MAIN FRAME (Built but NOT packed initially) ---
        self.main_frame = tk.Frame(self.root, bg="#F9F9F8", highlightbackground="#E0E0E0", highlightthickness=1)

        self.build_main_app()
        self.build_cover_screen()
        
        month_idx = self.month_names_short.index(self.current_month)
        day_idx = int(self.current_day) - 1
        
        self.select_month(self.current_month, self.month_labels[month_idx], auto_update=False)
        self.select_day(self.current_day, self.day_labels[day_idx])

    def resource_path(self, relative_path):
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    # --- COVER SCREEN ---
    def build_cover_screen(self):
        self.cover_frame = tk.Frame(self.root, bg="#2C2C2C", highlightbackground="#1A1A1A", highlightthickness=2)
        self.cover_frame.pack(fill="both", expand=True)
        
        self.canvas = tk.Canvas(self.cover_frame, width=1000, height=850, highlightthickness=0, bg="#2C2C2C")
        self.canvas.pack(fill="both", expand=True)
        
        cover_path = self.resource_path("cover.png") 
        if os.path.exists(cover_path):
            try:
                img = Image.open(cover_path)
                img = img.resize((1000, 850), Image.Resampling.LANCZOS)
                self.cover_photo = ImageTk.PhotoImage(img)
                self.canvas.create_image(0, 0, image=self.cover_photo, anchor="nw")
            except Exception as e:
                print(f"Error loading cover: {e}")

        cover_text = f"{self.user_name}'s AI Journal"
        font_setting = (MAIN_FONT, 56, "bold italic")
        
        self.canvas.create_text(500, 120, text=cover_text, font=font_setting, fill="#C5A059")
        
        if self.user_password:
            self.pass_input_frame = tk.Frame(self.cover_frame, bg="#FFFFFF", highlightbackground="#C5A059", highlightthickness=1)
            self.pass_entry = tk.Entry(self.pass_input_frame, bg="#FFFFFF", fg="#333333", relief="flat", font=(MAIN_FONT, 12), show="*", width=18)
            self.pass_entry.pack(side="left", padx=5, pady=5)
            self.pass_entry.bind("<Return>", lambda event: self.verify_password())
            
            unlock_btn = tk.Button(self.pass_input_frame, text="Unlock", bg="#C5A059", fg="#FFFFFF", relief="flat", font=(MAIN_FONT, 10, "bold"), command=self.verify_password)
            unlock_btn.pack(side="right", padx=5, pady=5)
            
            self.canvas.create_window(500, 200, window=self.pass_input_frame)
            
            self.error_text_id = self.canvas.create_text(500, 245, text="", font=(MAIN_FONT, 11, FONT_STYLE), fill="#cc0000")
            self.pass_entry.focus_set()
        else:
            self.canvas.create_text(500, 780, text="Click anywhere to open", font=(MAIN_FONT, 16, FONT_STYLE), fill="#FFFFFF", justify="center")
            self.canvas.bind("<ButtonRelease-1>", self.open_journal)

        self.canvas.bind("<Button-1>", self.get_pos)
        self.canvas.bind("<B1-Motion>", self.move_window)
        
        quit_btn = tk.Button(self.cover_frame, text="✕", bg="#2C2C2C", fg="#FFFFFF", relief="flat", borderwidth=0, font=(MAIN_FONT, 14, "bold"), command=self.root.quit)
        quit_btn.place(x=960, y=10)

    def verify_password(self):
        entered_pass = self.pass_entry.get()
        if entered_pass == self.user_password:
            self.cover_frame.destroy()
            self.main_frame.pack(fill="both", expand=True)
            self.update_weekday_highlight()
            self.load_day_data()
        else:
            if self.error_text_id:
                self.canvas.itemconfig(self.error_text_id, text="Incorrect password, try again.")

    def open_journal(self, event):
        if not self.dragged and not self.user_password:
            self.cover_frame.destroy()
            self.main_frame.pack(fill="both", expand=True)
            self.update_weekday_highlight()
            self.load_day_data()
        self.dragged = False
        
    def return_to_cover(self):
        self.save_current_day_data()
        if self.auto_save_timer is not None:
            self.root.after_cancel(self.auto_save_timer)
            self.auto_save_timer = None
        
        self.main_frame.pack_forget()
        self.build_cover_screen()

    def get_pos(self, event):
        self.xwin = event.x
        self.ywin = event.y
        self.dragged = False

    def move_window(self, event):
        self.root.geometry(f'+{event.x_root - self.xwin}+{event.y_root - self.ywin}')
        self.dragged = True

    # ==========================================
    # UI BUILDERS (Main App)
    # ==========================================
    def build_main_app(self):
        self.top_bar = tk.Frame(self.main_frame, bg="#F9F9F8")
        self.top_bar.pack(fill="x", pady=5)
        self.top_bar.bind("<B1-Motion>", self.move_window)
        self.top_bar.bind("<Button-1>", self.get_pos)

        tk.Button(self.top_bar, text="✕", bg="#F9F9F8", fg="#A0A0A0", relief="flat", borderwidth=0, font=(MAIN_FONT, 12, FONT_STYLE), command=self.return_to_cover).pack(side="right", padx=10)
        tk.Button(self.top_bar, text="⚙ Settings", bg="#F9F9F8", fg="#A0A0A0", relief="flat", borderwidth=0, font=(MAIN_FONT, 11, FONT_STYLE), command=self.show_help_settings).pack(side="right", padx=5)

        self.main_title_lbl = tk.Label(self.top_bar, text=f"{self.user_name}'s {self.current_year} Journal", bg="#F9F9F8", fg="#505050", font=(MAIN_FONT, 18, FONT_STYLE))
        self.main_title_lbl.pack(side="left", padx=300, expand=True)

        self.left_panel = tk.Frame(self.main_frame, bg="#F9F9F8", width=300)
        self.left_panel.pack(side="left", fill="y", padx=20, pady=10)
        
        self.right_tabs = tk.Frame(self.main_frame, bg="#EFEFEF", width=40)
        self.right_tabs.pack(side="right", fill="y")

        self.center_panel = tk.Frame(self.main_frame, bg="#F9F9F8")
        self.center_panel.pack(side="left", fill="both", expand=True, pady=10)

        self.build_left_panel()
        self.build_center_panel()
        self.build_right_tabs()
        
        self.thoughts_area.bind("<KeyRelease>", self.schedule_auto_save)

    def create_section_title(self, parent, text, pady=(15, 2)):
        lbl = tk.Label(parent, text=text, bg="#F9F9F8", fg="#888888", font=(MAIN_FONT, 10, FONT_STYLE))
        lbl.pack(anchor="w", pady=pady)
        return lbl

    def build_left_panel(self):
        header_frame = tk.Frame(self.left_panel, bg="#F9F9F8")
        header_frame.pack(fill="x", pady=(0, 10))
        self.day_display_lbl = tk.Label(header_frame, text="4", bg="#F9F9F8", fg="#333333", font=(MAIN_FONT, 32, FONT_STYLE))
        self.day_display_lbl.pack(side="left")
        self.month_display_lbl = tk.Label(header_frame, text="January", bg="#F9F9F8", fg="#333333", font=(MAIN_FONT, 20, FONT_STYLE)) 
        self.month_display_lbl.pack(side="left", padx=5, anchor="s")

        days_frame = tk.Frame(self.left_panel, bg="#F9F9F8", highlightbackground="#E0E0E0", highlightthickness=1)
        days_frame.pack(fill="x", pady=(0, 20))
        self.weekday_labels = []
        weekdays = ["S", "M", "T", "W", "T", "F", "S"]
        for wd in weekdays:
            lbl = tk.Label(days_frame, text=wd, bg="#F9F9F8", fg="#505050", font=(MAIN_FONT, 11, FONT_STYLE), width=3)
            lbl.pack(side="left", expand=True, pady=2)
            self.weekday_labels.append(lbl)

        todo_header = tk.Frame(self.left_panel, bg="#F9F9F8")
        todo_header.pack(fill="x", pady=(5, 5))
        
        tk.Label(todo_header, text="To-Do List", bg="#F9F9F8", fg="#888888", font=(MAIN_FONT, 10, FONT_STYLE)).pack(side="left")
        
        tk.Button(todo_header, text="+", bg="#F9F9F8", fg="#888888", relief="flat", font=("Arial", 14, "bold"), cursor="hand2", command=self.add_todo_item).pack(side="left", padx=(10, 2))
        tk.Button(todo_header, text="−", bg="#F9F9F8", fg="#888888", relief="flat", font=("Arial", 14, "bold"), cursor="hand2", command=self.remove_todo_item).pack(side="left")
        
        self.todo_box = tk.Frame(self.left_panel, bg="#FFFFFF", highlightbackground="#E0E0E0", highlightthickness=1)
        self.todo_box.pack(fill="x", pady=2)
        
        self.todo_items = []

        self.create_section_title(self.left_panel, "Overall Well Being", pady=(15, 5))
        wb_frame = tk.Frame(self.left_panel, bg="#F9F9F8")
        wb_frame.pack(fill="x")
        
        tk.Label(wb_frame, text="Mood", bg="#F9F9F8", fg="#505050", font=(MAIN_FONT, 11, FONT_STYLE)).grid(row=0, column=0, sticky="w", pady=5, padx=(0, 10))
        self.mood_frame = tk.Frame(wb_frame, bg="#F9F9F8")
        self.mood_frame.grid(row=0, column=1, sticky="w")
        
        self.mood_labels = []
        emojis = ["😄", "🙂", "😐", "🙁", "😢"]
        for emj in emojis:
            lbl = tk.Label(self.mood_frame, text=emj, bg="#F9F9F8", fg="#888888", font=(MAIN_FONT, 12), cursor="hand2")
            lbl.pack(side="left", padx=2)
            lbl.bind("<Button-1>", lambda e, m=emj, l=lbl: self.select_mood(m, l))
            self.mood_labels.append(lbl)
        
        tk.Label(wb_frame, text="Water", bg="#F9F9F8", fg="#505050", font=(MAIN_FONT, 11, FONT_STYLE)).grid(row=1, column=0, sticky="w", pady=5, padx=(0, 10))
        self.water_frame = tk.Frame(wb_frame, bg="#F9F9F8")
        self.water_frame.grid(row=1, column=1, sticky="w")
        
        self.water_cups = []
        for i in range(8):
            lbl = tk.Label(self.water_frame, text="○", bg="#F9F9F8", fg="#888888", font=(MAIN_FONT, 14), cursor="hand2")
            lbl.pack(side="left", padx=1)
            lbl.bind("<Button-1>", lambda e, idx=i: self.toggle_water(idx))
            self.water_cups.append(lbl)

        tk.Frame(self.left_panel, bg="#E0E0E0", height=1).pack(fill="x", pady=20)
        
        tk.Label(self.left_panel, text="AI Advisor", bg="#F9F9F8", fg="#888888", font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w")
        self.persona_var = tk.StringVar(value="Wellness Coach")
        personas = ["Wellness Coach", "Psychologist", "Spiritual Guide", "Tough Love Mentor"]
        menu = tk.OptionMenu(self.left_panel, self.persona_var, *personas)
        menu.config(bg="#FFFFFF", fg="#333333", relief="flat", highlightbackground="#E0E0E0", highlightthickness=1, font=(MAIN_FONT, 11, FONT_STYLE))
        menu.pack(fill="x", pady=5)

        self.analyze_btn = tk.Button(self.left_panel, text="Get AI Reflection", bg="#323232", fg="#FFFFFF", relief="flat", font=(MAIN_FONT, 11, FONT_STYLE), command=self.analyze_journal)
        self.analyze_btn.pack(fill="x", pady=5)
        
        # Audio Control Buttons (Listen & Dictate side by side)
        audio_btn_frame = tk.Frame(self.left_panel, bg="#F9F9F8")
        audio_btn_frame.pack(fill="x", pady=2)
        
        self.read_btn = tk.Button(audio_btn_frame, text="Listen 🔊", bg="#E0E0E0", fg="#333333", relief="flat", font=(MAIN_FONT, 11, FONT_STYLE), command=self.read_aloud)
        self.read_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
        
        self.dictate_btn = tk.Button(audio_btn_frame, text="Dictate 🎤", bg="#E0E0E0", fg="#333333", relief="flat", font=(MAIN_FONT, 11, FONT_STYLE), command=self.start_dictation)
        self.dictate_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))

    def build_center_panel(self):
        self.days_frame = tk.Frame(self.center_panel, bg="#F9F9F8")
        self.days_frame.pack(side="top", fill="x", pady=(0, 20))
        self.day_labels = []
        for d in range(1, 32):
            lbl = tk.Label(self.days_frame, text=str(d), bg="#F9F9F8", fg="#A0A0A0", font=(MAIN_FONT, 11, FONT_STYLE), cursor="hand2")
            lbl.pack(side="left", padx=3)
            lbl.bind("<Button-1>", lambda e, day=str(d), l=lbl: self.select_day(day, l))
            self.day_labels.append(lbl)

        self.bottom_frame = tk.Frame(self.center_panel, bg="#F9F9F8")
        self.bottom_frame.pack(side="bottom", fill="x")

        tk.Frame(self.bottom_frame, bg="#E0E0E0", height=1).pack(fill="x", pady=15)

        tk.Label(self.bottom_frame, text="AI Insight", bg="#F9F9F8", fg="#888888", font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w", padx=10)
        self.reflection_area = tk.Text(self.bottom_frame, bg="#FFFFFF", fg="#505050", relief="flat", highlightbackground="#E0E0E0", highlightthickness=1, insertbackground="#333333", font=(MAIN_FONT, 14, FONT_STYLE), height=8, wrap="word")
        self.reflection_area.pack(fill="x", padx=10, pady=(5, 20))
        self.reflection_area.insert("1.0", "Your personalized reflection will appear here...")
        self.reflection_area.config(state="disabled")

        self.thoughts_area = tk.Text(self.center_panel, bg="#F9F9F8", fg="#333333", relief="flat", highlightthickness=0, insertbackground="#333333", font=(MAIN_FONT, 16, FONT_STYLE), spacing1=10, spacing3=10, wrap="word")
        self.thoughts_area.pack(side="top", fill="both", expand=True, padx=10)

    def build_right_tabs(self):
        self.month_labels = []
        for month in self.month_names_short:
            tab = tk.Label(self.right_tabs, text=month, bg="#EFEFEF", fg="#888888", font=(MAIN_FONT, 10, FONT_STYLE), width=4, height=3, cursor="hand2")
            tab.pack(pady=1)
            tab.bind("<Button-1>", lambda e, m=month, t=tab: self.select_month(m, t))
            self.month_labels.append(tab)

    # ==========================================
    # TO-DO LIST LOGIC
    # ==========================================
    def add_todo_item(self, task_text="", is_done=False, trigger_save=True):
        if len(self.todo_items) >= 10:  
            return
            
        row_frame = tk.Frame(self.todo_box, bg="#FFFFFF")
        row_frame.pack(fill="x", padx=5, pady=2)
        
        var = tk.BooleanVar(value=is_done)
        cb = tk.Checkbutton(row_frame, variable=var, bg="#FFFFFF", activebackground="#FFFFFF", 
                            selectcolor="#FFFFFF", command=self.schedule_auto_save)
        cb.pack(side="left")
        
        entry = tk.Entry(row_frame, bg="#FFFFFF", fg="#333333", relief="flat", font=(MAIN_FONT, 11, FONT_STYLE), insertbackground="#333333")
        entry.pack(side="left", fill="x", expand=True, padx=2)
        if task_text:
            entry.insert(0, task_text)
        entry.bind("<KeyRelease>", self.schedule_auto_save)
        
        sep = tk.Frame(self.todo_box, bg="#F0F0F0", height=1)
        sep.pack(fill="x", padx=5)
        
        self.todo_items.append({"var": var, "entry": entry, "frame": row_frame, "sep": sep})
        
        if trigger_save:
            self.schedule_auto_save()

    def remove_todo_item(self):
        if self.todo_items:
            item = self.todo_items.pop()
            item["frame"].destroy()
            item["sep"].destroy()
            self.schedule_auto_save()

    # ==========================================
    # DICTATION LOGIC (Speech to Text)
    # ==========================================
    def start_dictation(self):
        self.dictate_btn.config(state="disabled", text="Listening... 🎙️")
        threading.Thread(target=self.dictation_worker, daemon=True).start()

    def dictation_worker(self):
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                # Adjust for ambient noise for half a second before listening
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                # Listen to the user (timeout if nobody speaks for 5s, max phrase length 15s)
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=15)
            
            # Use Google's free Web Speech API (Default language is English)
            text = recognizer.recognize_google(audio)
            self.root.after(0, self.insert_dictation, text)
            
        except sr.WaitTimeoutError:
            self.root.after(0, self.reset_dictate_btn)
        except sr.UnknownValueError:
            self.root.after(0, self.reset_dictate_btn)
        except Exception as e:
            print("Dictation error:", e)
            self.root.after(0, self.reset_dictate_btn)

    def insert_dictation(self, text):
        current_text = self.thoughts_area.get("1.0", tk.END).strip()
        if current_text:
            self.thoughts_area.insert(tk.END, " " + text)
        else:
            self.thoughts_area.insert(tk.END, text)
            
        self.reset_dictate_btn()
        self.schedule_auto_save()

    def reset_dictate_btn(self):
        self.dictate_btn.config(state="normal", text="Dictate 🎤")

    # ==========================================
    # DATA SAVING & LOADING LOGIC
    # ==========================================
    def load_journal_data(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
                    else:
                        return {}
            except Exception:
                pass
        return {}

    def get_current_date_key(self):
        return f"{self.current_year}-{self.current_month}-{self.current_day}"

    def schedule_auto_save(self, event=None):
        if self.auto_save_timer is not None:
            self.root.after_cancel(self.auto_save_timer)
        self.auto_save_timer = self.root.after(2000, self.save_current_day_data)

    def save_current_day_data(self):
        date_key = self.get_current_date_key()
        
        water_count = sum(1 for lbl in self.water_cups if lbl.cget("text") == "●")
        
        todos = []
        for item in self.todo_items:
            task_text = item["entry"].get().strip()
            is_done = item["var"].get()
            todos.append({"task": task_text, "done": is_done})
        
        data = {
            "thoughts": self.thoughts_area.get("1.0", tk.END).strip(),
            "todos": todos,
            "mood": self.current_mood,
            "water": water_count,
            "ai_reflection": self.reflection_area.get("1.0", tk.END).strip()
        }
        
        has_tasks = any(t["task"] != "" for t in todos)
        if not data["thoughts"] and not has_tasks and not data["mood"] and data["water"] == 0:
             return
             
        if data["ai_reflection"] == "Your personalized reflection will appear here..." or "[SYSTEM]" in data["ai_reflection"]:
            data["ai_reflection"] = ""

        self.journal_data[date_key] = data
        
        try:
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.journal_data, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def load_day_data(self):
        date_key = self.get_current_date_key()
        data = self.journal_data.get(date_key, {})

        self.thoughts_area.delete("1.0", tk.END)
        
        for item in self.todo_items:
            item["frame"].destroy()
            item["sep"].destroy()
        self.todo_items.clear()
        
        for lbl in self.mood_labels:
            lbl.config(bg="#F9F9F8")
        self.current_mood = ""
            
        for lbl in self.water_cups:
            lbl.config(text="○", fg="#888888")
            
        self.reflection_area.config(state="normal")
        self.reflection_area.delete("1.0", tk.END)

        if data:
            self.thoughts_area.insert("1.0", data.get("thoughts", ""))
            
            saved_todos = data.get("todos", [])
            if saved_todos:
                for t in saved_todos:
                    self.add_todo_item(t.get("task", ""), t.get("done", False), trigger_save=False)
            else:
                for _ in range(5): 
                    self.add_todo_item(trigger_save=False)
            
            saved_mood = data.get("mood", "")
            if saved_mood:
                for lbl in self.mood_labels:
                    if lbl.cget("text") == saved_mood:
                        self.select_mood(saved_mood, lbl)
                        break
                        
            saved_water = data.get("water", 0)
            for i in range(min(saved_water, 8)):
                self.toggle_water(i)
                
            ai_text = data.get("ai_reflection", "")
            if ai_text:
                self.reflection_area.insert("1.0", ai_text)
            else:
                self.reflection_area.insert("1.0", "Your personalized reflection will appear here...")
        else:
             for _ in range(5):
                 self.add_todo_item(trigger_save=False)
                 
             self.reflection_area.insert("1.0", "Your personalized reflection will appear here...")
             
        self.reflection_area.config(state="disabled")

    # ==========================================
    # INTERACTIVITY LOGIC
    # ==========================================
    def update_weekday_highlight(self):
        try:
            month_num = self.month_names_short.index(self.current_month) + 1
            day_num = int(self.current_day)
            dt = datetime(self.current_year, month_num, day_num)
            
            isoweekday = dt.isoweekday()
            target_idx = 0 if isoweekday == 7 else isoweekday
            
            for lbl in self.weekday_labels:
                lbl.config(bg="#F9F9F8", fg="#505050")
            
            self.weekday_labels[target_idx].config(bg="#FFA0A0", fg="#FFFFFF")
            self.current_weekday = self.weekday_labels[target_idx].cget("text")
            
        except ValueError:
            for lbl in self.weekday_labels:
                lbl.config(bg="#F9F9F8", fg="#505050")
            self.current_weekday = ""

    def toggle_water(self, index):
        lbl = self.water_cups[index]
        if lbl.cget("text") == "○":
            lbl.config(text="●", fg="#4A90E2")
        else:
            lbl.config(text="○", fg="#888888")
        self.schedule_auto_save()

    def select_mood(self, mood, label):
        self.current_mood = mood
        for lbl in self.mood_labels:
            lbl.config(bg="#F9F9F8")
        label.config(bg="#FFA0A0")
        self.schedule_auto_save()

    def select_month(self, month, label, auto_update=True):
        if auto_update:
            self.save_current_day_data()
            
        self.current_month = month
        for lbl in self.month_labels:
            lbl.config(bg="#EFEFEF", fg="#888888")
        label.config(bg="#FFA0A0", fg="#FFFFFF")
        
        full_names = {"JAN": "January", "FEB": "February", "MAR": "March", "APR": "April", "MAY": "May", "JUN": "June", "JUL": "July", "AUG": "August", "SEP": "September", "OCT": "October", "NOV": "November", "DEC": "December"}
        if hasattr(self, 'month_display_lbl'):
            self.month_display_lbl.config(text=full_names[month])
            
        if auto_update:
            self.update_weekday_highlight()
            self.load_day_data()

    def select_day(self, day, label):
        self.save_current_day_data()
        
        self.current_day = day
        for lbl in self.day_labels:
            lbl.config(bg="#F9F9F8", fg="#A0A0A0")
        label.config(bg="#FFA0A0", fg="#FFFFFF")
        
        if hasattr(self, 'day_display_lbl'):
            self.day_display_lbl.config(text=day)
            
        self.update_weekday_highlight()
        self.load_day_data()

    # ==========================================
    # LOGIC (API, TTS, CONFIG)
    # ==========================================
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_config(self, api_key, user_name, user_password):
        self.api_key = api_key
        self.user_name = user_name
        self.user_password = user_password
        self.main_title_lbl.config(text=f"{self.user_name}'s {self.current_year} Journal")
        
        if hasattr(self, 'cover_frame') and self.cover_frame.winfo_exists():
            self.cover_frame.destroy()
            self.build_cover_screen()
            
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump({"api_key": api_key, "user_name": user_name, "password": user_password}, f)
        except Exception:
            pass

    def show_help_settings(self):
        settings_win = tk.Toplevel(self.root)
        settings_win.overrideredirect(True)
        settings_win.geometry("400x310")
        settings_win.configure(bg="#FFFFFF")
        
        frame = tk.Frame(settings_win, bg="#FFFFFF", highlightbackground="#C8C8C8", highlightthickness=1)
        frame.pack(fill="both", expand=True)
        
        tk.Button(frame, text="X", bg="#cc0000", fg="#FFFFFF", relief="flat", borderwidth=0, font=(MAIN_FONT, 10, FONT_STYLE), command=settings_win.destroy).place(x=370, y=5, width=25, height=25)
        
        tk.Label(frame, text="⚙ SETTINGS", bg="#FFFFFF", fg="#333333", font=(MAIN_FONT, 12, FONT_STYLE)).place(x=20, y=20)
        
        tk.Label(frame, text="Your Name:", bg="#FFFFFF", fg="#505050", font=(MAIN_FONT, 11, FONT_STYLE)).place(x=20, y=55)
        name_entry = tk.Entry(frame, bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1, font=(MAIN_FONT, 11, FONT_STYLE))
        name_entry.place(x=20, y=78, width=360, height=28)
        name_entry.insert(0, self.user_name)

        tk.Label(frame, text="Password (optional):", bg="#FFFFFF", fg="#505050", font=(MAIN_FONT, 11, FONT_STYLE)).place(x=20, y=112)
        pass_entry = tk.Entry(frame, bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1, font=(MAIN_FONT, 11, FONT_STYLE), show="*")
        pass_entry.place(x=20, y=135, width=360, height=28)
        pass_entry.insert(0, self.user_password)

        tk.Label(frame, text="Gemini API Key:", bg="#FFFFFF", fg="#505050", font=(MAIN_FONT, 11, FONT_STYLE)).place(x=20, y=168)
        api_entry = tk.Entry(frame, bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1, font=(MAIN_FONT, 11, FONT_STYLE))
        api_entry.place(x=20, y=191, width=360, height=28)
        api_entry.insert(0, self.api_key)
        
        def save_and_close():
            self.save_config(api_entry.get().strip(), name_entry.get().strip(), pass_entry.get().strip())
            settings_win.destroy()

        tk.Button(frame, text="Save Settings", bg="#323232", fg="#FFFFFF", relief="flat", font=(MAIN_FONT, 11, FONT_STYLE), command=save_and_close).place(x=20, y=245, width=150, height=35)

    def read_aloud(self):
        text = self.reflection_area.get("1.0", tk.END).strip()
        if not text or "[SYSTEM]" in text or "personalized reflection" in text:
            return
        self.read_btn.config(state="disabled", text="Speaking...")
        threading.Thread(target=self.speak_text, args=(text,), daemon=True).start()

    def speak_text(self, text):
        try:
            tts = gTTS(text=text, lang='en', slow=False)
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            pygame.mixer.music.load(fp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        except Exception:
            pass
        finally:
            self.root.after(0, lambda: self.read_btn.config(state="normal", text="Listen 🔊"))

    def analyze_journal(self):
        self.reflection_area.config(state="normal")
        self.reflection_area.delete("1.0", tk.END)
        
        if not self.api_key:
            self.reflection_area.insert(tk.END, "[SYSTEM] API Key is missing. Click Settings to enter your key.")
            self.reflection_area.config(state="disabled")
            return

        user_text = self.thoughts_area.get("1.0", tk.END).strip()
        selected_persona = self.persona_var.get()
        
        has_tasks = any(item["entry"].get().strip() != "" for item in self.todo_items)
        
        if not user_text and not has_tasks:
            self.reflection_area.insert(tk.END, "[SYSTEM] Please write your thoughts or tasks first.")
            self.reflection_area.config(state="disabled")
            return

        self.reflection_area.insert(tk.END, "[SYSTEM] Analyzing thoughts...")
        self.analyze_btn.config(state="disabled")
        
        todo_text = ""
        for item in self.todo_items:
            task = item["entry"].get().strip()
            is_done = item["var"].get()
            if task:
                status = "Done" if is_done else "Pending"
                todo_text += f"- {task} [{status}]\n"

        history_context = ""
        keys = list(self.journal_data.keys())
        if len(keys) > 0:
            recent_keys = keys[-3:]
            history_context = "\nFor context, here are the user's past 3 journal entries:\n"
            for k in recent_keys:
                history_context += f"Entry from {k}: Text: '{self.journal_data[k].get('thoughts', '')}'\n"

        persona_instructions = {
            "Wellness Coach": "Act as an empathetic and insightful wellness coach. Provide a brief, supportive reflection.",
            "Psychologist": "Act as an analytical psychologist. Provide one deep cognitive follow-up question.",
            "Spiritual Guide": "Act as a wise spiritual guide. Provide a grounding reflection.",
            "Tough Love Mentor": "Act as a direct, no-nonsense mentor. Provide actionable advice."
        }

        persona_prompt = persona_instructions.get(selected_persona, persona_instructions["Wellness Coach"])
        
        mood_context = f"The user noted their mood as {self.current_mood}. " if self.current_mood else ""
        mood_instruction = f"IMPORTANT: You MUST begin your response by explicitly addressing and acknowledging the user's current mood ({self.current_mood}). " if self.current_mood else ""
        name_instruction = f"IMPORTANT: Address the user by their name ({self.user_name}) naturally in your response. " if self.user_name and self.user_name != "My" else ""
        date_context = f"Date: {self.current_month} {self.current_day}. "
        
        tasks_context = f"\nUser's To-Do List today:\n{todo_text}" if todo_text else ""
        
        prompt = (
            f"{history_context}\n"
            f"{date_context}{mood_context}{tasks_context}\n"
            f"The user just wrote this in their private journal: '{user_text}'.\n\n"
            f"{persona_prompt} {mood_instruction}{name_instruction}"
            f"Take into account their past entries, tasks, and current mood if relevant. "
            f"Do not use asterisks for formatting. Keep it concise."
        )

        threading.Thread(target=self.call_gemini, args=(self.api_key, prompt), daemon=True).start()

    def call_gemini(self, api_key, prompt):
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('models/gemini-3.6-flash')
            response = model.generate_content(prompt)
            clean_text = response.text.replace("*", "")
            self.root.after(0, self.update_reflection, clean_text)
        except Exception as e:
            self.root.after(0, self.update_reflection, f"[ERROR] Failed to connect: {str(e)}")
        finally:
            self.root.after(0, lambda: self.analyze_btn.config(state="normal"))

    def update_reflection(self, text):
        self.reflection_area.delete("1.0", tk.END)
        self.reflection_area.insert(tk.END, text)
        self.reflection_area.config(state="disabled")
        self.save_current_day_data()

if __name__ == "__main__":
    root = tk.Tk()
    app = AIJournalHardcoded(root)
    root.after(100, app.load_day_data)
    root.mainloop()