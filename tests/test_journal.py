"""End-to-end checks for AI_Journal_App, driven headlessly under Xvfb."""

import json
import os

from _harness import check, check_dialog, install_stubs, isolate_home, report, tts_calls

install_stubs()
home = isolate_home()

# files an older version left in the working directory, to prove they are migrated
json.dump({"user_name": "עודד", "api_key": "k", "password": ""},
          open("config.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"2026-AUG-01": {"thoughts": "רשומה ישנה", "todos": [], "mood": "", "water": 0, "ai_reflection": ""}},
          open("journal_history.json", "w", encoding="utf-8"), ensure_ascii=False)

import tkinter as tk

import core
import AI_Journal_App as app_mod

SECRET = "היום פגשתי את הרופא"


def open_app(unlocked=True):
    root = tk.Tk()
    app = app_mod.AIJournalHardcoded(root)
    if unlocked:
        app.cover_frame.destroy()
        app.main_frame.pack(fill="both", expand=True)
        app.load_day_data()
        root.update()
    return root, app


def unlock(app, password):
    app.pass_entry.delete(0, "end")
    app.pass_entry.insert(0, password)
    app.verify_password()


# ---------- storage location and migration ----------
check("data dir is under HOME, not cwd", app_mod.DATA_DIR.startswith(home), app_mod.DATA_DIR)
check("legacy config migrated", os.path.exists(app_mod.CONFIG_FILE))
check("legacy history migrated", os.path.exists(app_mod.HISTORY_FILE))

root, app = open_app()

# ---------- config encoding ----------
check("Hebrew name read from config", app.user_name == "עודד", app.user_name)
app.save_config("key2", "יוסי", "")
check("Hebrew name written as utf-8",
      json.loads(open(app_mod.CONFIG_FILE, encoding="utf-8").read())["user_name"] == "יוסי")
check("no plaintext password key", "password" not in json.loads(open(app_mod.CONFIG_FILE, encoding="utf-8").read()))

# ---------- month lengths and the day strip ----------
app.select_day("31", app.day_labels[30])
root.update()
check("AUG shows 31 days", len(app.days_frame.grid_slaves()) == 31)
check("all 31 fit inside the panel",
      max(l.winfo_x() + l.winfo_width() for l in app.day_labels if l.winfo_ismapped())
      <= app.days_frame.winfo_width())
check("no day number is clipped",
      not [l for l in app.day_labels if l.winfo_ismapped() and l.winfo_width() < l.winfo_reqwidth()])
app.select_month("FEB", app.month_labels[1])
root.update()
check("FEB 2026 shows 28 days", len(app.days_frame.grid_slaves()) == 28)
check("day 31 clamped into February", app.current_day == "28", app.current_day)
app.current_year = 2028
app.select_month("FEB", app.month_labels[1])
root.update()
check("FEB 2028 (leap) shows 29 days", len(app.days_frame.grid_slaves()) == 29)
app.current_year = 2026

# ---------- saving ----------
app.select_month("AUG", app.month_labels[7])
app.select_day("25", app.day_labels[24])
root.update()
app.thoughts_area.insert("1.0", SECRET)
app.save_current_day_data()
saved = json.load(open(app_mod.HISTORY_FILE, encoding="utf-8"))
check("day saved with Hebrew intact", saved["2026-AUG-25"]["thoughts"] == SECRET)
check("older entry preserved", "2026-AUG-01" in saved)
check("no temp files left behind", not [f for f in os.listdir(app_mod.DATA_DIR) if f.startswith(".tmp_")])

original_write = core.write_store
core.write_store = lambda p, d, f: (_ for _ in ()).throw(OSError("No space left on device"))
app.thoughts_area.insert("1.0", "x")
app.save_current_day_data()
check("save failure shown to user", "Save failed" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))
core.write_store = original_write

before = open(app_mod.HISTORY_FILE, encoding="utf-8").read()
open(app_mod.HISTORY_FILE, "w").write("{not json")
app.journal_data = app.load_journal_data()
check("corrupt history flagged", app.history_load_failed)
app.save_current_day_data()
check("corrupt history never overwritten", open(app_mod.HISTORY_FILE).read() == "{not json")
open(app_mod.HISTORY_FILE, "w", encoding="utf-8").write(before)
app.journal_data = app.load_journal_data()

# ---------- audio stays English ----------
app.speak_text("hello")
check("TTS uses en", tts_calls[-1] == "en", tts_calls)
app.speak_text("שלום")
check("TTS stays en for any text", tts_calls[-1] == "en")

# ---------- to-do list ----------
for item in list(app.todo_items):
    app.remove_todo_item(item)
app.add_todo_item("Buy milk", False)
app.add_todo_item("Call mum", True)
root.update()
check("unticked task uses the plain font", app.todo_items[0]["entry"].cget("font") == str(app.todo_font))
check("ticked task is struck through", app.todo_items[1]["entry"].cget("font") == str(app.todo_font_done))
check("strikethrough font really has overstrike", app.todo_font_done.actual("overstrike") == 1)
first = app.todo_items[0]
app.remove_todo_item(first)
check("any row can be deleted", len(app.todo_items) == 1 and app.todo_items[0]["entry"].get() == "Call mum")
check("deleted row leaves the screen", not first["frame"].winfo_exists())
while len(app.todo_items) < app_mod.MAX_TODO_ITEMS:
    app.add_todo_item("x")
app.add_todo_item("one too many")
check("hitting the cap is reported", "full" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))
check("cap still enforced", len(app.todo_items) == app_mod.MAX_TODO_ITEMS)

# ---------- password encrypts the journal ----------
app.save_config("key2", "יוסי", "hunter2")
disk = open(app_mod.HISTORY_FILE, encoding="utf-8").read()
check("journal text no longer in the clear", SECRET not in disk)
check("file marked encrypted", json.loads(disk).get("encrypted") is True)
config = json.loads(open(app_mod.CONFIG_FILE, encoding="utf-8").read())
check("password itself never stored", "hunter2" not in json.dumps(config))
check("salt and check token stored", bool(config["password_salt"]) and bool(config["password_check"]))
root.destroy()

root, app = open_app(unlocked=False)
check("restart leaves the journal locked", app.password_enabled and app.fernet is None)
check("nothing decrypted while locked", app.journal_data == {})
unlock(app, "wrong")
check("wrong password refused", app.fernet is None)
check("wrong password shows an error", "Incorrect" in app.canvas.itemcget(app.error_text_id, "text"))
untouched = open(app_mod.HISTORY_FILE, encoding="utf-8").read()
app.save_current_day_data()
check("locked app cannot overwrite the ciphertext",
      open(app_mod.HISTORY_FILE, encoding="utf-8").read() == untouched)
unlock(app, "hunter2")
root.update()
check("right password unlocks", app.fernet is not None)
check("entry decrypted after unlock", app.journal_data.get("2026-AUG-25", {}).get("thoughts") == SECRET)
app.return_to_cover()
root.update()
check("re-locking drops the key", app.fernet is None and app.journal_data == {})
root.destroy()

root, app = open_app(unlocked=False)
unlock(app, "hunter2")
root.update()
app.save_config("key2", "יוסי", "")
check("clearing the password decrypts the file",
      SECRET in open(app_mod.HISTORY_FILE, encoding="utf-8").read())
root.destroy()

# ---------- the settings dialog can be moved ----------
root, app = open_app()      # the password was cleared by the section above
app.show_help_settings()
check_dialog("journal settings", root, [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1])
root.destroy()

report()
