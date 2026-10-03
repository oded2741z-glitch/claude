import tkinter as tk
import tkinter.font as tkfont
import threading
import json
import random
import time
import os
import sys
import io
import re
import google.generativeai as genai
from gtts import gTTS
import pygame
from datetime import datetime
from PIL import Image, ImageTk
import speech_recognition as sr

import core

APP_DIR_NAME = "AI_Companion"
DATA_DIR = core.user_data_dir(APP_DIR_NAME)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
COMPANION_FILE = os.path.join(DATA_DIR, "companion.json")

# --- GLOBAL FONT SETTING ---
MAIN_FONT = "Georgia"
FONT_STYLE = "italic"

GEMINI_MODEL = "models/gemini-3.6-flash"
HISTORY_TURNS = 24      # how much of the conversation is sent back to the model
MAX_MESSAGES = 500      # how much is kept on disk

DEFAULT_IDLE_MINUTES = 3.0   # quiet time before the character speaks unprompted
MIN_IDLE_MINUTES = 0.25      # low enough to actually try the feature out
MAX_IDLE_MINUTES = 120.0
IDLE_JITTER_SHARE = 0.3      # varied, so it never feels like a metronome
VOICE_RETRY_MS = 1500   # pause before listening again after silence or a misheard phrase
MAX_VOICE_FAILURES = 3  # consecutive microphone errors before voice mode switches itself off

OPENING_NUDGE = (
    "The conversation has not started yet. Open it yourself: say the first thing, "
    "in character, as if you had been waiting for them."
)
IDLE_NUDGE = (
    "They have gone quiet for a while and have not written anything new. "
    "Say something unprompted — pick it up yourself, from your own mood and what you want. "
    "Do not pretend they just spoke."
)

GENDERS = ["Female", "Male", "Non-binary", "Unspecified"]

CHARACTER_FIELDS = [
    ("personality", "Personality"),
    ("desires", "What they want"),
    ("behaviour", "How they behave"),
    ("backstory", "Background"),
]

DEFAULT_CHARACTER = {
    "name": "Mika",
    "gender": "Unspecified",
    "age": "27",
    "personality": "Warm, curious, a little sardonic. Asks questions back instead of lecturing.",
    "desires": "Wants to be understood, and wants to hear how your day actually went.",
    "behaviour": "Short, natural sentences. Never lists, never lectures, never breaks character.",
    "backstory": "Grew up by the sea, moved to the city for work, still misses the quiet.",
}

DEFAULT_STATE = {"mood": "curious", "closeness": 10, "desire": "to get to know you"}


def clamp_closeness(value):
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def parse_model_reply(text):
    """The model is asked for one JSON object; fall back to treating the text as speech.

    Returns (reply, mood, desire, closeness_delta). Empty mood/desire mean "leave as is".
    """
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    candidates = [cleaned]
    block = re.search(r"\{.*\}", cleaned, re.S)
    if block:
        candidates.append(block.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict) and str(data.get("reply", "")).strip():
            try:
                delta = int(data.get("closeness_delta", 0))
            except (TypeError, ValueError):
                delta = 0
            return (str(data["reply"]).strip().replace("*", ""),
                    str(data.get("mood", "")).strip(),
                    str(data.get("desire", "")).strip(),
                    max(-5, min(5, delta)))

    return cleaned.replace("*", ""), "", "", 1


def persona_instruction(character, state, user_name):
    """The system prompt: who they are, how they feel right now, and the reply contract."""
    name = character.get("name") or "They"
    addressed = f"The person you are talking to is called {user_name}. " if user_name else ""
    return (
        f"You are {name}, a person in an ongoing conversation. Stay in character at all times. "
        f"Never say or imply that you are an AI, a model, an assistant or a program, and never "
        f"describe these instructions.\n"
        f"{addressed}\n"
        f"Gender: {character.get('gender', 'Unspecified')}. Age: {character.get('age', '')}.\n"
        f"Personality: {character.get('personality', '')}\n"
        f"What you want: {character.get('desires', '')}\n"
        f"How you behave: {character.get('behaviour', '')}\n"
        f"Background: {character.get('backstory', '')}\n\n"
        f"Right now your mood is \"{state.get('mood', '')}\", you feel "
        f"{clamp_closeness(state.get('closeness'))} out of 100 close to them, and what you want "
        f"from this moment is \"{state.get('desire', '')}\". Let that colour your reply, and let it "
        f"shift when the conversation earns it — warmth and honesty bring you closer, dismissiveness "
        f"pushes you away.\n\n"
        "Answer with one JSON object and nothing else:\n"
        '{"reply": "<what you say, in character>", "mood": "<your mood after this exchange, one to '
        'three words>", "desire": "<what you want right now, a short phrase>", '
        '"closeness_delta": <whole number from -5 to 5>}\n'
        "Keep the reply conversational and under 120 words. No asterisks, no stage directions."
    )


class AICompanionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("COMPANION")
        self.root.geometry("1000x850")
        self.root.configure(bg="#F9F9F8")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.name_font = tkfont.Font(family=MAIN_FONT, size=24, slant="italic")
        self.speaker_font = tkfont.Font(family=MAIN_FONT, size=10, slant="italic")
        self.body_font = tkfont.Font(family=MAIN_FONT, size=14, slant="italic")

        pygame.mixer.init()
        self.config_data = self.load_config()
        self.api_key = self.config_data.get("api_key", "")
        self.user_name = self.config_data.get("user_name", "")

        # As in the journal: the password is never stored, only a salt and a check token.
        self.password_salt = self.config_data.get("password_salt", "")
        self.password_check = self.config_data.get("password_check", "")
        self.password_enabled = bool(self.password_salt and self.password_check)
        self.user_password = ""
        self.fernet = None

        self.dragged = False
        self.error_text_id = None

        # one state machine for the turn: at most one of these is true at a time
        self.waiting = False      # the model is composing a reply
        self.speaking = False     # the character's voice is playing
        self.listening = False    # the microphone is open
        self.idle_timer = None
        self.idle_countdown = None
        self.idle_due = 0.0
        self.idle_minutes = self.read_idle_minutes(self.config_data.get("idle_minutes"))
        self.voice_failures = 0

        self.data = self.load_data()

        self.main_frame = tk.Frame(self.root, bg="#F9F9F8", highlightbackground="#E0E0E0", highlightthickness=1)
        self.build_main_app()
        self.build_cover_screen()
        self.refresh_all()

        if self.load_failed:
            self.set_status("⚠ Saved conversation unreadable — saving is paused to protect it")

    def resource_path(self, relative_path):
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    # ==========================================
    # COVER / LOCK SCREEN
    # ==========================================
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

        title = self.data["character"].get("name") or "Companion"
        self.canvas.create_text(500, 120, text=title, font=(MAIN_FONT, 56, "bold italic"), fill="#C5A059")

        if self.password_enabled:
            self.pass_input_frame = tk.Frame(self.cover_frame, bg="#FFFFFF", highlightbackground="#C5A059", highlightthickness=1)
            self.pass_entry = tk.Entry(self.pass_input_frame, bg="#FFFFFF", fg="#333333", relief="flat", font=(MAIN_FONT, 12), show="*", width=18)
            self.pass_entry.pack(side="left", padx=5, pady=5)
            self.pass_entry.bind("<Return>", lambda event: self.verify_password())

            tk.Button(self.pass_input_frame, text="Unlock", bg="#C5A059", fg="#FFFFFF", relief="flat",
                      font=(MAIN_FONT, 10, "bold"), command=self.verify_password).pack(side="right", padx=5, pady=5)

            self.canvas.create_window(500, 200, window=self.pass_input_frame)
            self.error_text_id = self.canvas.create_text(500, 245, text="", font=(MAIN_FONT, 11, FONT_STYLE), fill="#cc0000")
            self.pass_entry.focus_set()
        else:
            self.canvas.create_text(500, 780, text="Click anywhere to open", font=(MAIN_FONT, 16, FONT_STYLE), fill="#FFFFFF")
            self.canvas.bind("<ButtonRelease-1>", self.open_chat)

        self.canvas.bind("<Button-1>", self.get_pos)
        self.canvas.bind("<B1-Motion>", self.move_window)

        tk.Button(self.cover_frame, text="✕", bg="#2C2C2C", fg="#FFFFFF", relief="flat", borderwidth=0,
                  font=(MAIN_FONT, 14, "bold"), command=self.root.quit).place(x=960, y=10)

    def verify_password(self):
        fernet = core.unlock(self.pass_entry.get(), self.password_salt, self.password_check)
        if fernet is None:
            if self.error_text_id:
                self.canvas.itemconfig(self.error_text_id, text="Incorrect password, try again.")
            return

        self.user_password = self.pass_entry.get()
        self.fernet = fernet
        self.data = self.load_data()
        self.enter_chat()
        if self.load_failed:
            self.set_status("⚠ Saved conversation unreadable — saving is paused to protect it")

    def open_chat(self, event):
        if not self.dragged and not self.password_enabled:
            self.enter_chat()
        self.dragged = False

    def enter_chat(self):
        self.cover_frame.destroy()
        self.main_frame.pack(fill="both", expand=True)
        self.refresh_all()
        self.schedule_idle_turn()
        self.start_listening()

    def return_to_cover(self):
        self.cancel_idle_timer()
        self.voice_var.set(False)       # stop listening before the cover goes back up
        self.save_data()
        if self.password_enabled:
            self.fernet = None
            self.user_password = ""
            self.data = self.load_data()

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
    # UI BUILDERS
    # ==========================================
    def build_main_app(self):
        self.top_bar = tk.Frame(self.main_frame, bg="#F9F9F8")
        self.top_bar.pack(fill="x", pady=5)
        self.top_bar.bind("<B1-Motion>", self.move_window)
        self.top_bar.bind("<Button-1>", self.get_pos)

        tk.Button(self.top_bar, text="✕", bg="#F9F9F8", fg="#A0A0A0", relief="flat", borderwidth=0,
                  font=(MAIN_FONT, 12, FONT_STYLE), command=self.return_to_cover).pack(side="right", padx=10)
        tk.Button(self.top_bar, text="⚙ Settings", bg="#F9F9F8", fg="#A0A0A0", relief="flat", borderwidth=0,
                  font=(MAIN_FONT, 11, FONT_STYLE), command=self.show_settings).pack(side="right", padx=5)

        self.status_lbl = tk.Label(self.top_bar, text="", bg="#F9F9F8", fg="#A0A0A0", font=(MAIN_FONT, 10, FONT_STYLE))
        self.status_lbl.pack(side="right", padx=10)

        self.title_lbl = tk.Label(self.top_bar, text="", bg="#F9F9F8", fg="#505050", font=(MAIN_FONT, 18, FONT_STYLE))
        self.title_lbl.pack(side="left", expand=True)

        self.left_panel = tk.Frame(self.main_frame, bg="#F9F9F8", width=280)
        self.left_panel.pack(side="left", fill="y", padx=20, pady=10)
        self.left_panel.pack_propagate(False)

        self.center_panel = tk.Frame(self.main_frame, bg="#F9F9F8")
        self.center_panel.pack(side="left", fill="both", expand=True, padx=(0, 20), pady=10)

        self.build_left_panel()
        self.build_chat_panel()

    def build_left_panel(self):
        self.name_lbl = tk.Label(self.left_panel, text="", bg="#F9F9F8", fg="#333333",
                                 font=self.name_font, anchor="w", justify="left", wraplength=260)
        self.name_lbl.pack(anchor="w")
        self.subtitle_lbl = tk.Label(self.left_panel, text="", bg="#F9F9F8", fg="#A0A0A0",
                                     font=(MAIN_FONT, 11, FONT_STYLE), anchor="w")
        self.subtitle_lbl.pack(anchor="w", pady=(0, 15))

        tk.Frame(self.left_panel, bg="#E0E0E0", height=1).pack(fill="x", pady=5)

        tk.Label(self.left_panel, text="How they feel", bg="#F9F9F8", fg="#888888",
                 font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w", pady=(10, 5))

        self.mood_lbl = tk.Label(self.left_panel, text="", bg="#F9F9F8", fg="#505050",
                                 font=(MAIN_FONT, 13, FONT_STYLE), anchor="w", wraplength=260, justify="left")
        self.mood_lbl.pack(anchor="w")

        tk.Label(self.left_panel, text="Closeness", bg="#F9F9F8", fg="#888888",
                 font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w", pady=(15, 2))

        bar_outer = tk.Frame(self.left_panel, bg="#E8E8E8", height=8)
        bar_outer.pack(fill="x")
        bar_outer.pack_propagate(False)
        self.closeness_bar = tk.Frame(bar_outer, bg="#C5705D")
        self.closeness_bar.place(x=0, y=0, relheight=1, relwidth=0)
        self.closeness_lbl = tk.Label(self.left_panel, text="", bg="#F9F9F8", fg="#A0A0A0",
                                      font=(MAIN_FONT, 10, FONT_STYLE), anchor="w")
        self.closeness_lbl.pack(anchor="w")

        tk.Label(self.left_panel, text="What they want right now", bg="#F9F9F8", fg="#888888",
                 font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w", pady=(15, 2))
        self.desire_lbl = tk.Label(self.left_panel, text="", bg="#F9F9F8", fg="#505050",
                                   font=(MAIN_FONT, 12, FONT_STYLE), anchor="w", wraplength=260, justify="left")
        self.desire_lbl.pack(anchor="w")

        tk.Frame(self.left_panel, bg="#E0E0E0", height=1).pack(fill="x", pady=20)

        tk.Button(self.left_panel, text="Edit Character", bg="#323232", fg="#FFFFFF", relief="flat",
                  font=(MAIN_FONT, 11, FONT_STYLE), command=self.show_character_editor).pack(fill="x", pady=3)

        audio_frame = tk.Frame(self.left_panel, bg="#F9F9F8")
        audio_frame.pack(fill="x", pady=2)
        self.read_btn = tk.Button(audio_frame, text="Listen 🔊", bg="#E0E0E0", fg="#333333", relief="flat",
                                  font=(MAIN_FONT, 11, FONT_STYLE), command=self.read_aloud)
        self.read_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.dictate_btn = tk.Button(audio_frame, text="Dictate 🎤", bg="#E0E0E0", fg="#333333", relief="flat",
                                     font=(MAIN_FONT, 11, FONT_STYLE), command=self.start_dictation)
        self.dictate_btn.pack(side="left", fill="x", expand=True, padx=(2, 0))

        self.initiative_var = tk.BooleanVar(value=bool(self.config_data.get("speak_first", False)))
        self.voice_var = tk.BooleanVar(value=bool(self.config_data.get("voice_mode", False)))

        tk.Checkbutton(self.left_panel, text="Let them speak first", variable=self.initiative_var,
                       bg="#F9F9F8", activebackground="#F9F9F8", selectcolor="#FFFFFF", fg="#505050",
                       font=(MAIN_FONT, 10, FONT_STYLE), anchor="w",
                       command=self.toggle_initiative).pack(fill="x", pady=(10, 0))
        tk.Checkbutton(self.left_panel, text="Voice mode (always listening)", variable=self.voice_var,
                       bg="#F9F9F8", activebackground="#F9F9F8", selectcolor="#FFFFFF", fg="#505050",
                       font=(MAIN_FONT, 10, FONT_STYLE), anchor="w",
                       command=self.toggle_voice_mode).pack(fill="x")

        self.idle_lbl = tk.Label(self.left_panel, text="", bg="#F9F9F8", fg="#A0A0A0",
                                 font=(MAIN_FONT, 10, FONT_STYLE), anchor="w")
        self.idle_lbl.pack(fill="x", pady=(4, 0))

    def build_chat_panel(self):
        self.transcript = tk.Text(self.center_panel, bg="#FFFFFF", fg="#333333", relief="flat",
                                  highlightbackground="#E0E0E0", highlightthickness=1, wrap="word",
                                  font=self.body_font, spacing1=4, spacing3=10, padx=14, pady=12)
        self.transcript.tag_config("them_name", foreground="#C5705D", font=self.speaker_font, spacing1=12)
        self.transcript.tag_config("you_name", foreground="#A0A0A0", font=self.speaker_font, spacing1=12)
        self.transcript.config(state="disabled")

        # the input row must claim its space before the transcript expands into the rest,
        # otherwise the expanding widget takes the whole panel and pushes it off-screen
        input_frame = tk.Frame(self.center_panel, bg="#F9F9F8")
        input_frame.pack(side="bottom", fill="x", pady=(10, 0))
        self.transcript.pack(side="top", fill="both", expand=True)

        self.input_area = tk.Text(input_frame, bg="#FFFFFF", fg="#333333", relief="flat",
                                  highlightbackground="#E0E0E0", highlightthickness=1,
                                  insertbackground="#333333", font=(MAIN_FONT, 13, FONT_STYLE),
                                  height=3, wrap="word", padx=8, pady=6)
        self.input_area.bind("<Return>", self.on_return)
        self.input_area.bind("<Shift-Return>", lambda e: None)

        # again: the fixed-width button is packed before the box that expands into the rest
        self.send_btn = tk.Button(input_frame, text="Send", bg="#323232", fg="#FFFFFF", relief="flat",
                                  font=(MAIN_FONT, 11, FONT_STYLE), width=10, command=self.send_message)
        self.send_btn.pack(side="right", fill="y", padx=(8, 0))
        self.input_area.pack(side="left", fill="both", expand=True)

    # ==========================================
    # RENDERING
    # ==========================================
    def refresh_all(self):
        self.refresh_character()
        self.refresh_state()
        self.refresh_transcript()

    def refresh_character(self):
        character = self.data["character"]
        name = character.get("name") or "Companion"
        self.title_lbl.config(text=name)
        self.name_lbl.config(text=name)
        details = [character.get("gender", ""), str(character.get("age", ""))]
        self.subtitle_lbl.config(text=" · ".join([d for d in details if d]))

    def refresh_state(self):
        state = self.data["state"]
        closeness = clamp_closeness(state.get("closeness"))
        self.mood_lbl.config(text=state.get("mood") or "—")
        self.desire_lbl.config(text=state.get("desire") or "—")
        self.closeness_bar.place_configure(relwidth=closeness / 100)
        self.closeness_lbl.config(text=f"{closeness} / 100")

    def refresh_transcript(self):
        self.transcript.config(state="normal")
        self.transcript.delete("1.0", tk.END)
        name = self.data["character"].get("name") or "Companion"
        you = self.user_name or "You"

        if not self.data["messages"]:
            self.transcript.insert(tk.END, f"Say something to {name}.\n", "you_name")
        for message in self.data["messages"]:
            speaker = you if message.get("role") == "you" else name
            tag = "you_name" if message.get("role") == "you" else "them_name"
            self.transcript.insert(tk.END, speaker + "\n", tag)
            self.transcript.insert(tk.END, message.get("text", "") + "\n")

        self.transcript.config(state="disabled")
        self.transcript.see(tk.END)

    def set_status(self, message):
        if hasattr(self, "status_lbl") and self.status_lbl.winfo_exists():
            self.status_lbl.config(text=message)

    # ==========================================
    # CONVERSATION
    # ==========================================
    def on_return(self, event):
        self.send_message()
        return "break"      # keep the newline out of the input box

    def send_message(self):
        if self.waiting:
            return
        text = self.input_area.get("1.0", tk.END).strip()
        if not text:
            return
        if not self.api_key:
            self.set_status("⚠ API key missing — open Settings")
            return

        self.input_area.delete("1.0", tk.END)
        self.append_message("you", text)
        self.dispatch()

    def dispatch(self, nudge=""):
        """Ask the model for the next line, whether or not the user just said something."""
        character = self.data["character"]
        instruction = persona_instruction(character, self.data["state"], self.user_name)
        transcript = self.recent_transcript()
        if nudge:
            transcript = (transcript + "\n\n" if transcript else "") + f"[{nudge}]"

        self.waiting = True
        self.cancel_idle_timer()
        self.send_btn.config(state="disabled")
        self.set_status(f"{character.get('name') or 'They'} is typing…")
        threading.Thread(target=self.call_model, args=(self.api_key, instruction, transcript), daemon=True).start()

    # ---------- speaking unprompted ----------
    def toggle_initiative(self):
        self.persist_config()
        self.schedule_idle_turn()

    def cancel_idle_timer(self):
        if self.idle_timer is not None:
            self.root.after_cancel(self.idle_timer)
            self.idle_timer = None
        if self.idle_countdown is not None:
            self.root.after_cancel(self.idle_countdown)
            self.idle_countdown = None
        if hasattr(self, "idle_lbl") and self.idle_lbl.winfo_exists():
            self.idle_lbl.config(text="")

    @staticmethod
    def read_idle_minutes(value):
        try:
            return max(MIN_IDLE_MINUTES, min(MAX_IDLE_MINUTES, float(value)))
        except (TypeError, ValueError):
            return DEFAULT_IDLE_MINUTES

    def schedule_idle_turn(self):
        """Re-arm the quiet-time timer. Called after every turn and whenever the toggle moves."""
        self.cancel_idle_timer()
        if not self.initiative_var.get():
            return
        base = self.idle_minutes * 60
        seconds = base + random.uniform(0, base * IDLE_JITTER_SHARE)
        self.idle_due = time.monotonic() + seconds
        self.idle_timer = self.root.after(int(seconds * 1000), self.idle_turn)
        self.tick_idle_countdown()

    def tick_idle_countdown(self):
        """Show how long the quiet has to run, so an armed timer is visible rather than hoped for."""
        if self.idle_countdown is not None:
            self.root.after_cancel(self.idle_countdown)
            self.idle_countdown = None
        if not hasattr(self, "idle_lbl") or not self.idle_lbl.winfo_exists():
            return
        if self.idle_timer is None or not self.initiative_var.get():
            self.idle_lbl.config(text="")
            return

        remaining = max(0, int(round(self.idle_due - time.monotonic())))
        self.idle_lbl.config(text=f"speaks up in {remaining // 60}:{remaining % 60:02d}")
        self.idle_countdown = self.root.after(1000, self.tick_idle_countdown)

    def idle_turn(self):
        self.idle_timer = None
        if not self.initiative_var.get() or self.waiting or self.speaking:
            self.schedule_idle_turn()
            return
        if not self.api_key:
            self.set_status("⚠ API key missing — open Settings")
            return
        if self.load_failed:
            return
        if self.password_enabled and self.fernet is None:
            return      # locked: nothing should be happening behind the cover screen
        if self.input_area.get("1.0", tk.END).strip():
            self.schedule_idle_turn()       # they are mid-sentence; do not talk over them
            return

        self.dispatch(OPENING_NUDGE if not self.data["messages"] else IDLE_NUDGE)

    def recent_transcript(self):
        """The tail of the conversation, labelled so the model can follow who said what."""
        name = self.data["character"].get("name") or "They"
        you = self.user_name or "User"
        lines = []
        for message in self.data["messages"][-HISTORY_TURNS:]:
            speaker = you if message.get("role") == "you" else name
            lines.append(f"{speaker}: {message.get('text', '')}")
        return "\n".join(lines)

    def call_model(self, api_key, instruction, transcript):
        try:
            genai.configure(api_key=api_key)
            try:
                model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=instruction)
                prompt = transcript
            except TypeError:
                # older SDKs have no system_instruction; fold it into the prompt instead
                model = genai.GenerativeModel(GEMINI_MODEL)
                prompt = instruction + "\n\n" + transcript
            response = model.generate_content(prompt)
            self.root.after(0, self.receive_reply, response.text)
        except Exception as e:
            self.root.after(0, self.receive_error, str(e))

    def receive_reply(self, raw_text):
        reply, mood, desire, delta = parse_model_reply(raw_text)
        state = self.data["state"]
        if mood:
            state["mood"] = mood
        if desire:
            state["desire"] = desire
        state["closeness"] = clamp_closeness(clamp_closeness(state.get("closeness")) + delta)

        self.append_message("them", reply)
        self.refresh_state()
        self.finish_turn("")
        if self.voice_var.get():
            self.read_aloud()       # its "finished speaking" hand-off reopens the microphone
        else:
            self.start_listening()

    def receive_error(self, message):
        self.finish_turn(f"⚠ {message[:50]}")
        self.start_listening()

    def finish_turn(self, status):
        self.waiting = False
        self.send_btn.config(state="normal")
        self.set_status(status)
        self.schedule_idle_turn()

    def append_message(self, role, text):
        self.data["messages"].append({"role": role, "text": text,
                                      "time": datetime.now().isoformat(timespec="seconds")})
        del self.data["messages"][:-MAX_MESSAGES]
        self.refresh_transcript()
        self.save_data()

    def last_reply(self):
        for message in reversed(self.data["messages"]):
            if message.get("role") == "them":
                return message.get("text", "")
        return ""

    # ==========================================
    # DATA
    # ==========================================
    def load_config(self):
        return core.read_json(CONFIG_FILE)

    def load_data(self):
        stored, status = core.read_store(COMPANION_FILE, self.fernet)
        self.load_failed = (status == "failed")

        character = dict(DEFAULT_CHARACTER)
        if isinstance(stored.get("character"), dict):
            character.update(stored["character"])
        state = dict(DEFAULT_STATE)
        if isinstance(stored.get("state"), dict):
            state.update(stored["state"])
        messages = stored.get("messages")
        return {"character": character, "state": state,
                "messages": messages if isinstance(messages, list) else []}

    def save_data(self):
        if self.password_enabled and self.fernet is None:
            return      # locked: writing now would replace the ciphertext with plaintext
        if self.load_failed:
            self.set_status("⚠ Saved conversation unreadable — saving is paused to protect it")
            return
        try:
            core.write_store(COMPANION_FILE, self.data, self.fernet)
        except Exception as e:
            self.set_status(f"⚠ Save failed: {str(e)[:45]}")

    def persist_config(self):
        try:
            core.write_json_atomic(CONFIG_FILE, {"api_key": self.api_key, "user_name": self.user_name,
                                                 "password_salt": self.password_salt,
                                                 "password_check": self.password_check,
                                                 "speak_first": bool(self.initiative_var.get()),
                                                 "voice_mode": bool(self.voice_var.get()),
                                                 "idle_minutes": self.idle_minutes})
        except Exception as e:
            self.set_status(f"⚠ Settings not saved: {str(e)[:45]}")

    def apply_password_change(self, new_password):
        """Re-key the conversation. Returns False (and says why) if it could not be done safely."""
        if self.load_failed:
            self.set_status("⚠ Conversation unreadable — password unchanged")
            return False

        previous = (self.fernet, self.password_salt, self.password_check, self.password_enabled)
        if new_password:
            self.password_salt, self.password_check, self.fernet = core.new_password_credentials(new_password)
            self.password_enabled = True
        else:
            self.password_salt = ""
            self.password_check = ""
            self.fernet = None
            self.password_enabled = False

        try:
            core.write_store(COMPANION_FILE, self.data, self.fernet)
        except Exception as e:
            self.fernet, self.password_salt, self.password_check, self.password_enabled = previous
            self.set_status(f"⚠ Password unchanged: {str(e)[:40]}")
            return False

        self.user_password = new_password
        return True

    # ==========================================
    # DIALOGS
    # ==========================================
    def show_settings(self):
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.geometry("400x370")
        window.configure(bg="#FFFFFF")

        frame = tk.Frame(window, bg="#FFFFFF", highlightbackground="#C8C8C8", highlightthickness=1)
        frame.pack(fill="both", expand=True)

        tk.Button(frame, text="X", bg="#cc0000", fg="#FFFFFF", relief="flat", borderwidth=0,
                  font=(MAIN_FONT, 10, FONT_STYLE), command=window.destroy).place(x=370, y=5, width=25, height=25)
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

        tk.Label(frame, text="Speaks first after (minutes of quiet):", bg="#FFFFFF", fg="#505050",
                 font=(MAIN_FONT, 11, FONT_STYLE)).place(x=20, y=224)
        idle_entry = tk.Entry(frame, bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1,
                              font=(MAIN_FONT, 11, FONT_STYLE))
        idle_entry.place(x=20, y=247, width=360, height=28)
        idle_entry.insert(0, str(self.idle_minutes))

        def save_and_close():
            self.save_settings(api_entry.get().strip(), name_entry.get().strip(),
                               pass_entry.get().strip(), idle_entry.get().strip())
            window.destroy()

        tk.Button(frame, text="Save Settings", bg="#323232", fg="#FFFFFF", relief="flat",
                  font=(MAIN_FONT, 11, FONT_STYLE), command=save_and_close).place(x=20, y=300, width=150, height=35)

    def save_settings(self, api_key, user_name, user_password, idle_minutes=None):
        self.api_key = api_key
        self.user_name = user_name
        if idle_minutes is not None:
            self.idle_minutes = self.read_idle_minutes(idle_minutes)

        if user_password != self.user_password and not self.apply_password_change(user_password):
            return

        self.persist_config()
        self.refresh_transcript()
        self.set_status("")
        self.schedule_idle_turn()

    def show_character_editor(self):
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.geometry("440x640")
        window.configure(bg="#FFFFFF")

        frame = tk.Frame(window, bg="#FFFFFF", highlightbackground="#C8C8C8", highlightthickness=1)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg="#FFFFFF")
        header.pack(fill="x", padx=20, pady=(18, 10))
        tk.Label(header, text="THE CHARACTER", bg="#FFFFFF", fg="#333333", font=(MAIN_FONT, 12, FONT_STYLE)).pack(side="left")
        tk.Button(header, text="X", bg="#cc0000", fg="#FFFFFF", relief="flat", borderwidth=0,
                  font=(MAIN_FONT, 10, FONT_STYLE), width=2, command=window.destroy).pack(side="right")

        character = self.data["character"]
        body = tk.Frame(frame, bg="#FFFFFF")
        body.pack(fill="both", expand=True, padx=20)

        tk.Label(body, text="Name", bg="#FFFFFF", fg="#505050", font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w")
        name_entry = tk.Entry(body, bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1, font=(MAIN_FONT, 11, FONT_STYLE))
        name_entry.pack(fill="x", ipady=3)
        name_entry.insert(0, character.get("name", ""))

        row = tk.Frame(body, bg="#FFFFFF")
        row.pack(fill="x", pady=(8, 0))
        gender_col = tk.Frame(row, bg="#FFFFFF")
        gender_col.pack(side="left", fill="x", expand=True)
        tk.Label(gender_col, text="Gender", bg="#FFFFFF", fg="#505050", font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w")
        gender_var = tk.StringVar(value=character.get("gender", GENDERS[-1]))
        gender_menu = tk.OptionMenu(gender_col, gender_var, *GENDERS)
        gender_menu.config(bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1, highlightthickness=0,
                           font=(MAIN_FONT, 10, FONT_STYLE))
        gender_menu.pack(fill="x")

        age_col = tk.Frame(row, bg="#FFFFFF")
        age_col.pack(side="left", padx=(10, 0))
        tk.Label(age_col, text="Age", bg="#FFFFFF", fg="#505050", font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w")
        age_entry = tk.Entry(age_col, bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1,
                             font=(MAIN_FONT, 11, FONT_STYLE), width=6)
        age_entry.pack(ipady=3)
        age_entry.insert(0, str(character.get("age", "")))

        boxes = {}
        for key, label in CHARACTER_FIELDS:
            tk.Label(body, text=label, bg="#FFFFFF", fg="#505050",
                     font=(MAIN_FONT, 10, FONT_STYLE)).pack(anchor="w", pady=(10, 0))
            box = tk.Text(body, bg="#F9F9F9", fg="#333333", relief="solid", borderwidth=1,
                          font=(MAIN_FONT, 10, FONT_STYLE), height=3, wrap="word",
                          insertbackground="#333333", padx=4, pady=3)
            box.pack(fill="x")
            box.insert("1.0", character.get(key, ""))
            boxes[key] = box

        footer = tk.Frame(frame, bg="#FFFFFF")
        footer.pack(fill="x", padx=20, pady=14)

        def save_character():
            character["name"] = name_entry.get().strip() or DEFAULT_CHARACTER["name"]
            character["gender"] = gender_var.get()
            character["age"] = age_entry.get().strip()
            for key, _ in CHARACTER_FIELDS:
                character[key] = boxes[key].get("1.0", tk.END).strip()
            self.save_data()
            self.refresh_all()
            window.destroy()

        def clear_conversation():
            self.data["messages"] = []
            self.data["state"] = dict(DEFAULT_STATE)
            self.save_data()
            self.refresh_all()
            window.destroy()

        tk.Button(footer, text="Save Character", bg="#323232", fg="#FFFFFF", relief="flat",
                  font=(MAIN_FONT, 11, FONT_STYLE), command=save_character).pack(side="left", ipadx=14, ipady=4)
        tk.Button(footer, text="Start over", bg="#FFFFFF", fg="#cc0000", relief="flat",
                  font=(MAIN_FONT, 10, FONT_STYLE), command=clear_conversation).pack(side="right")

    # ==========================================
    # AUDIO
    # ==========================================
    def read_aloud(self):
        text = self.last_reply()
        if not text or self.speaking:
            return
        self.speaking = True
        self.set_status("")
        self.read_btn.config(state="disabled", text="Speaking...")
        threading.Thread(target=self.speak_text, args=(text,), daemon=True).start()

    def speak_text(self, text):
        try:
            tts = gTTS(text=text, lang="en", slow=False)
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            pygame.mixer.music.load(fp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        except Exception as e:
            self.root.after(0, self.set_status, f"⚠ Playback failed: {str(e)[:45]}")
        finally:
            self.root.after(0, self.finish_speaking)

    def finish_speaking(self):
        self.speaking = False
        self.read_btn.config(state="normal", text="Listen 🔊")
        self.start_listening()      # only reopens the microphone when voice mode is on

    # ---------- voice mode: a microphone that stays open between turns ----------
    def toggle_voice_mode(self):
        self.persist_config()
        if self.voice_var.get():
            self.voice_failures = 0
            self.start_listening()
        else:
            self.set_status("")

    def start_listening(self):
        """Open the microphone, unless something else in the turn is already using it."""
        if not self.voice_var.get() or self.listening or self.waiting or self.speaking:
            return
        if self.password_enabled and self.fernet is None:
            return
        self.listening = True
        self.set_status("listening…")
        threading.Thread(target=self.voice_worker, daemon=True).start()

    def voice_worker(self):
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=8, phrase_time_limit=20)
            text = recognizer.recognize_google(audio, language="en-US")
            self.root.after(0, self.voice_heard, text)
        except (sr.WaitTimeoutError, sr.UnknownValueError):
            self.root.after(0, self.voice_quiet)        # nobody spoke; just listen again
        except Exception as e:
            self.root.after(0, self.voice_failed, str(e))

    def voice_heard(self, text):
        self.listening = False
        self.voice_failures = 0
        if not text.strip():
            self.voice_quiet()
            return
        self.input_area.delete("1.0", tk.END)
        self.input_area.insert("1.0", text)
        self.send_message()

    def voice_quiet(self):
        self.listening = False
        self.voice_failures = 0
        if self.voice_var.get():
            self.root.after(VOICE_RETRY_MS, self.start_listening)

    def voice_failed(self, message):
        """A real microphone problem, not silence — back off, and give up after a few."""
        self.listening = False
        self.voice_failures += 1
        if self.voice_failures >= MAX_VOICE_FAILURES:
            self.voice_var.set(False)
            self.persist_config()
            self.set_status(f"⚠ Voice mode off: {message[:40]}")
            return
        self.set_status(f"⚠ Microphone: {message[:40]}")
        self.root.after(VOICE_RETRY_MS, self.start_listening)

    def start_dictation(self):
        self.dictate_btn.config(state="disabled", text="Listening... 🎙️")
        threading.Thread(target=self.dictation_worker, daemon=True).start()

    def dictation_worker(self):
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=15)
            text = recognizer.recognize_google(audio, language="en-US")
            self.root.after(0, self.insert_dictation, text)
        except sr.WaitTimeoutError:
            self.root.after(0, self.reset_dictate_btn)
        except sr.UnknownValueError:
            self.root.after(0, self.reset_dictate_btn)
        except Exception as e:
            print("Dictation error:", e)
            self.root.after(0, self.reset_dictate_btn)

    def insert_dictation(self, text):
        existing = self.input_area.get("1.0", tk.END).strip()
        self.input_area.insert(tk.END, (" " + text) if existing else text)
        self.reset_dictate_btn()

    def reset_dictate_btn(self):
        self.dictate_btn.config(state="normal", text="Dictate 🎤")


if __name__ == "__main__":
    root = tk.Tk()
    app = AICompanionApp(root)
    root.mainloop()
