# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`AI_Journal_App.py` is the whole application: a single-file Tkinter desktop journal with a Gemini-powered "AI reflection", text-to-speech playback (gTTS + pygame) and microphone dictation (SpeechRecognition). There is no package, no build system, and no test suite — the file is run directly.

## Commands

```bash
# Run
python AI_Journal_App.py

# Dependencies (no requirements.txt exists yet)
pip install google-generativeai gTTS pygame pillow SpeechRecognition
# Dictation also needs a PortAudio binding, e.g. pip install pyaudio
```

`resource_path()` reads `sys._MEIPASS`, so the app is meant to be frozen with PyInstaller and `cover.png` bundled as data (`--add-data`). The image is optional at runtime — the cover screen falls back to a plain dark canvas when it is missing.

## Architecture

**One class, two screens, one window.** `AIJournalHardcoded` builds both `self.main_frame` (the journal) and `self.cover_frame` (the lock/cover screen) against the same `root`. Only the cover is packed at startup; `open_journal`/`verify_password` destroy the cover and pack the main frame, `return_to_cover` does the reverse and *rebuilds* the cover from scratch. Anything added to the cover must therefore be re-created in `build_cover_screen()`, not stashed on the instance.

The window is borderless (`overrideredirect(True)`, `-topmost`), so there is no OS title bar: dragging is implemented manually with `get_pos`/`move_window` bound on the canvas and the top bar, and the `dragged` flag exists so that a drag-release does not count as the "click anywhere to open" gesture.

**Widgets are the model.** There is no in-memory state object for a day. `save_current_day_data()` reconstructs the day by reading widget properties — water intake is counted by comparing each label's text to `"●"`, mood is the emoji string in `self.current_mood`, to-dos come from the `Entry`/`BooleanVar` pairs in `self.todo_items`. Changing a glyph or a widget type means changing the persistence code with it.

**Persistence.** Two JSON files next to the working directory:
- `config.json` — `api_key`, `user_name`, `password` (stored in plaintext; the password only gates the cover screen, nothing is encrypted).
- `journal_history.json` — one entry per day, keyed by `"{year}-{MON}-{day}"` with a short uppercase month and an unpadded day (e.g. `2026-AUG-25`). Keys sort lexically, not chronologically; `analyze_journal` takes "the last 3 entries" as `list(keys)[-3:]`, i.e. insertion order, not date order.

Saves are debounced: every edit calls `schedule_auto_save`, which cancels the pending `root.after` and re-arms it 2s out. `save_current_day_data` returns early for a completely empty day (so blank days are never written) and blanks the reflection field when it still holds the placeholder or a `[SYSTEM]` message, so those strings never reach disk.

Day/month selection is a save-then-load cycle: `select_day`/`select_month` flush the current day before mutating `current_day`/`current_month`, then call `load_day_data()`, which tears down and rebuilds the to-do rows. The `auto_update=False` flag on `select_month` exists for the constructor, where the widgets are built but no day has been loaded yet.

**Threading.** Gemini calls (`call_gemini`), TTS (`speak_text`) and dictation (`dictation_worker`) each run on a daemon thread; every touch of a widget from those threads is marshalled back through `self.root.after(0, ...)`. Keep that discipline — Tkinter is not thread-safe. Each also disables its button on start and re-enables it in the `after` callback / `finally`.

**Prompting.** `analyze_journal` assembles the prompt (recent-entry context, date, mood, to-dos, persona instruction from the `persona_instructions` dict) and hands it to the thread; `call_gemini` strips `*` from the response because the prompt asks for no asterisk formatting. The model id is hardcoded in `call_gemini`.
