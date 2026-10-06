"""End-to-end checks for AI_Companion_App, driven headlessly under Xvfb."""

import json
import os
import tempfile
import types

from _harness import (FakeCommunicate, check, check_dialog, edge_calls, install_stubs, isolate_home, report,
                      run_threads_inline, topmost_log, topmost_requested, tts_calls)

install_stubs()
home = isolate_home()

import tkinter as tk

import core
import AI_Companion_App as app_mod

app_mod.TYPING_PACE = 0     # deliver replies at once; the paced delivery has its own section below
run_threads_inline(app_mod)

SECRET = "אמרתי לה משהו אישי"


class FakeResponse:
    def __init__(self, text):
        self.text = text


def fake_model(reply_text, reject_system_instruction=False):
    """Stand in for genai.GenerativeModel and record what it was given."""
    seen = {}

    class Model:
        def __init__(self, name, system_instruction=None):
            if reject_system_instruction and system_instruction is not None:
                raise TypeError("system_instruction not supported")
            seen["instruction"] = system_instruction

        def generate_content(self, prompt):
            seen["prompt"] = prompt
            return FakeResponse(reply_text)

    app_mod.genai.GenerativeModel = Model
    return seen


def open_app(unlocked=True, password="hunter2"):
    root = tk.Tk()
    app = app_mod.AICompanionApp(root)
    if unlocked:
        if app.password_enabled:
            app.pass_entry.insert(0, password)
            app.verify_password()
        else:
            app.enter_chat()
        root.update()
    return root, app


# ================= reply parsing =================
turn = app_mod.parse_model_turn(
    '{"reply": "I missed you.", "mood": "soft", "desire": "to hear more", "closeness_delta": 3}')
check("plain JSON parsed", (turn["bubbles"], turn["mood"], turn["desire"], turn["delta"])
      == (["I missed you."], "soft", "to hear more", 3))

turn = app_mod.parse_model_turn(
    '```json\n{"reply": "Hi.", "mood": "wary", "desire": "space", "closeness_delta": -2}\n```')
check("fenced JSON parsed", (turn["bubbles"], turn["mood"]) == (["Hi."], "wary"))

turn = app_mod.parse_model_turn(
    'Sure thing!\n{"reply": "Hello.", "mood": "calm", "desire": "quiet", "closeness_delta": 99}')
check("JSON inside prose parsed", turn["bubbles"] == ["Hello."] and turn["mood"] == "calm")
check("closeness delta clamped", turn["delta"] == 5, turn["delta"])

turn = app_mod.parse_model_turn("Just *talking* normally, no JSON here.")
check("non-JSON falls back to speech", turn["bubbles"] == ["Just talking normally, no JSON here."])
check("fallback leaves mood and desire alone", turn["mood"] == "" and turn["desire"] == "")
check("fallback nudges closeness", turn["delta"] == 1)
check("fallback remembers nothing", turn["remember"] == [] and turn["follow_ups"] == [])

turn = app_mod.parse_model_turn('{"reply": ["wait", "", "  ", "ok so", "the thing is", "also"], "mood": "x"}')
check("a reply can be several messages", turn["bubbles"][:2] == ["wait", "ok so"])
check("never more than three, and no words lost",
      len(turn["bubbles"]) == app_mod.MAX_BUBBLES and turn["bubbles"][-1] == "the thing is also", turn["bubbles"])
turn = app_mod.parse_model_turn('{"reply": "", "mood": "x"}')
check("an empty reply is not taken as a turn", turn["bubbles"] == ['{"reply": "", "mood": "x"}'])

turn = app_mod.parse_model_turn(json.dumps({
    "reply": ["ok"], "remember": ["has a dog called Pita", "", None],
    "follow_up": [{"about": "dentist", "on": "2026-10-09"}, {"about": "gig", "on": "next week"},
                  "moving flats", {"on": "2026-01-01"}],
    "done_follow_ups": [3, "4", "x", None]}))
check("facts read, blanks dropped", turn["remember"] == ["has a dog called Pita"], turn["remember"])
check("follow-ups read, a non-date kept undated, an item with no subject dropped",
      turn["follow_ups"] == [{"about": "dentist", "on": "2026-10-09"}, {"about": "gig", "on": ""},
                             {"about": "moving flats", "on": ""}], turn["follow_ups"])
check("done ids read, junk dropped", turn["done"] == [3, 4], turn["done"])

check("closeness clamps low", app_mod.clamp_closeness(-40) == 0)
check("closeness clamps high", app_mod.clamp_closeness(400) == 100)
check("closeness survives junk", app_mod.clamp_closeness("abc") == 0)

# ================= the persona prompt =================
character = dict(app_mod.DEFAULT_CHARACTER, name="Noa", gender="Female", desires="to be taken seriously")
instruction = app_mod.persona_instruction(character, {"mood": "tense", "closeness": 42, "desire": "an apology"}, "Oded")
for fragment in ["Noa", "Female", "to be taken seriously", "tense", "42", "an apology", "Oded"]:
    check(f"prompt carries {fragment!r}", fragment in instruction)
check("prompt forbids breaking character", "Never say or imply that you are an AI" in instruction)
check("prompt asks for the JSON contract", '"closeness_delta"' in instruction)

# ================= a full exchange =================
root, app = open_app()
check("starts with the default character", app.data["character"]["name"] == "Mika")
check("starts at the default state", app.data["state"]["closeness"] == 10)

app.api_key = "key"
seen = fake_model('{"reply": "Tell me about it.", "mood": "attentive", "desire": "the whole story", "closeness_delta": 4}')
app.input_area.insert("1.0", SECRET)
app.send_message()
check("your message is recorded", app.data["messages"][0]["text"] == SECRET)
check("input box cleared", app.input_area.get("1.0", "end").strip() == "")
check("send disabled while waiting", str(app.send_btn.cget("state")) == "disabled")
check("status shows they are typing", "typing" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))

root.update()
check("reply recorded", app.data["messages"][-1]["text"] == "Tell me about it.")
check("mood updated from the reply", app.data["state"]["mood"] == "attentive")
check("desire updated from the reply", app.data["state"]["desire"] == "the whole story")
check("closeness moved by the delta", app.data["state"]["closeness"] == 14, app.data["state"]["closeness"])
check("state panel redrawn", app.closeness_lbl.cget("text") == "14 / 100")
check("send re-enabled", str(app.send_btn.cget("state")) == "normal")
window_bottom = app.main_frame.winfo_height()
check("input box is actually on screen", app.input_area.winfo_ismapped())
check("Send button is actually on screen", app.send_btn.winfo_ismapped())
check("input row sits inside the window",
      app.input_area.winfo_rooty() - app.main_frame.winfo_rooty() + app.input_area.winfo_height() <= window_bottom,
      f"input bottom={app.input_area.winfo_rooty() - app.main_frame.winfo_rooty() + app.input_area.winfo_height()} window={window_bottom}")
check("transcript still gets most of the height", app.transcript.winfo_height() > app.input_area.winfo_height() * 3)
check("transcript shows both sides",
      SECRET in app.transcript.get("1.0", "end") and "Tell me about it." in app.transcript.get("1.0", "end"))
transcript = app.recent_transcript()
check("history labels the character's lines", "Mika: Tell me about it." in transcript, transcript)
check("history labels your lines", f"User: {SECRET}" in transcript, transcript)

# the model errors
def boom(*a, **k):
    raise RuntimeError("quota exceeded")
app_mod.genai.GenerativeModel = boom
app.call_model("key", "instruction", "transcript")
root.update()
check("model errors surface", "quota exceeded" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))
check("send re-enabled after an error", str(app.send_btn.cget("state")) == "normal")

# an SDK without system_instruction
seen = fake_model('{"reply": "ok", "mood": "flat", "desire": "none", "closeness_delta": 0}',
                  reject_system_instruction=True)
app.call_model("key", "PERSONA-TEXT", "TRANSCRIPT")
root.update()
check("falls back when system_instruction is unsupported", "PERSONA-TEXT" in seen["prompt"])

# no API key
app.api_key = ""
app.input_area.insert("1.0", "hello")
app.send_message()
check("missing API key reported", "API key" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))
app.api_key = "key"
app.input_area.delete("1.0", "end")

# ================= persistence =================
check("conversation written to disk", os.path.exists(app_mod.COMPANION_FILE))
on_disk = json.load(open(app_mod.COMPANION_FILE, encoding="utf-8"))
check("message text saved", SECRET in json.dumps(on_disk, ensure_ascii=False))
check("state saved", on_disk["state"]["mood"] == "flat")

app.data["character"]["name"] = "Noa"
app.save_data()
root.destroy()

root, app = open_app()
check("character survives a restart", app.data["character"]["name"] == "Noa")
check("conversation survives a restart", app.data["messages"][0]["text"] == SECRET)
check("state survives a restart", app.data["state"]["closeness"] == 14)
check("window title follows the character", app.title_lbl.cget("text") == "Noa")

app.data["messages"] = [{"role": "you", "text": str(i)} for i in range(app_mod.MAX_MESSAGES + 20)]
app.append_message("them", "last")
check("old messages trimmed", len(app.data["messages"]) == app_mod.MAX_MESSAGES)
check("newest message kept", app.data["messages"][-1]["text"] == "last")

# ================= lock and encryption =================
app.save_settings("key", "Oded", "hunter2")
disk = open(app_mod.COMPANION_FILE, encoding="utf-8").read()
check("conversation encrypted on disk", SECRET not in disk)
check("file marked encrypted", json.loads(disk).get("encrypted") is True)
config = json.loads(open(app_mod.CONFIG_FILE, encoding="utf-8").read())
check("password never stored", "hunter2" not in json.dumps(config))
root.destroy()

root, app = open_app(unlocked=False)
check("restart leaves it locked", app.password_enabled and app.fernet is None)
check("nothing decrypted while locked", app.data["messages"] == [])
check("locked cover asks for a password", hasattr(app, "pass_entry"))
untouched = open(app_mod.COMPANION_FILE, encoding="utf-8").read()
app.save_data()
check("locked app cannot overwrite the ciphertext",
      open(app_mod.COMPANION_FILE, encoding="utf-8").read() == untouched)

app.pass_entry.insert(0, "wrong")
app.verify_password()
check("wrong password refused", app.fernet is None)
app.pass_entry.delete(0, "end")
app.pass_entry.insert(0, "hunter2")
app.verify_password()
root.update()
check("right password unlocks", app.fernet is not None)
check("conversation decrypted", app.data["messages"][-1]["text"] == "last")
check("transcript rebuilt after unlock", "last" in app.transcript.get("1.0", "end"))

app.return_to_cover()
root.update()
check("re-locking drops the key and the messages",
      app.fernet is None and app.data["messages"] == [])
root.destroy()

# ================= audio =================
root, app = open_app(unlocked=False)
app.pass_entry.insert(0, "hunter2")
app.verify_password()
root.update()
edge_calls.clear()
app.read_aloud()
check("Listen speaks the character's last line",
      edge_calls and edge_calls[-1][1] == app.last_reply(), edge_calls)
root.destroy()

# ================= how they relate to you =================
check("a preset relationship has its own starting closeness", app_mod.starting_closeness("Partner") == 75)
check("a stranger starts at nothing", app_mod.starting_closeness("Stranger") == 0)
check("a custom relationship starts at the default",
      app_mod.starting_closeness("my old army buddy") == app_mod.DEFAULT_STATE["closeness"])

prompt = app_mod.persona_instruction(
    dict(app_mod.DEFAULT_CHARACTER, relationship="Close friend", treatment="teases you, but would fight for you"),
    {"mood": "warm", "closeness": 65, "desire": "a laugh"}, "Oded")
check("prompt says what you are to them", "What Oded is to you: Close friend" in prompt)
check("prompt says how they treat you", "How you treat Oded: teases you, but would fight for you" in prompt)
nameless = app_mod.persona_instruction(dict(app_mod.DEFAULT_CHARACTER, relationship="Sibling"),
                                       app_mod.DEFAULT_STATE, "")
check("still reads naturally without your name", "What they are to you: Sibling" in nameless,
      nameless.split("\n")[2])


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def open_editor(app):
    app.show_character_editor()
    root.update()
    window = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    widgets = list(walk(window))
    combo = next(w for w in widgets if w.winfo_class() == "TCombobox"
                 and list(w.cget("values")) == list(app_mod.RELATIONSHIPS))
    texts = [w for w in widgets if isinstance(w, tk.Text)]
    buttons = {w.cget("text"): w for w in widgets if isinstance(w, tk.Button)}
    return window, combo, texts, buttons


root, app = open_app()
app.data["messages"] = []
window, combo, texts, buttons = open_editor(app)
check("the editor offers every preset", list(combo.cget("values")) == list(app_mod.RELATIONSHIPS))
check("'how they treat you' comes first among the text boxes",
      texts[0].get("1.0", "end").strip() == app.data["character"]["treatment"])
combo.set("Partner")
texts[0].delete("1.0", "end")
texts[0].insert("1.0", "Openly affectionate, a little jealous.")
buttons["Save Character"].invoke()
root.update()
check("relationship saved", app.data["character"]["relationship"] == "Partner")
check("treatment saved", app.data["character"]["treatment"] == "Openly affectionate, a little jealous.")
check("an empty conversation starts at the relationship's closeness", app.data["state"]["closeness"] == 75)
check("the panel shows it", "Partner" in app.subtitle_lbl.cget("text"), app.subtitle_lbl.cget("text"))

app.data["messages"] = [{"role": "you", "text": "hi"}]
app.data["state"]["closeness"] = 31
window, combo, texts, buttons = open_editor(app)
combo.set("Stranger")
buttons["Save Character"].invoke()
root.update()
check("mid-conversation, earned closeness is left alone", app.data["state"]["closeness"] == 31)

window, combo, texts, buttons = open_editor(app)
combo.set("the neighbour I keep running into")
buttons["Save Character"].invoke()
root.update()
check("a typed-in relationship is accepted", app.data["character"]["relationship"] == "the neighbour I keep running into")
root.update_idletasks()
check("a long relationship wraps instead of being cut off",
      app.subtitle_lbl.winfo_reqwidth() <= app.left_panel.winfo_width(),
      f"needs {app.subtitle_lbl.winfo_reqwidth()} of {app.left_panel.winfo_width()}")

app.data["character"]["relationship"] = "Sibling"
window, combo, texts, buttons = open_editor(app)
buttons["Start over"].invoke()
root.update()
check("starting over begins at the relationship's closeness", app.data["state"]["closeness"] == 60)
check("and clears the conversation", app.data["messages"] == [])

window, combo, texts, buttons = open_editor(app)
combo.set("   ")
buttons["Save Character"].invoke()
root.update()
check("a blank relationship falls back to the default",
      app.data["character"]["relationship"] == app_mod.DEFAULT_CHARACTER["relationship"])

legacy = {"character": {"name": "Old", "gender": "Male", "age": "40"}, "state": {"closeness": 50}, "messages": []}
core.write_store(app_mod.COMPANION_FILE, legacy, app.fernet)
reloaded = app.load_data()
check("a character saved before this feature still loads",
      reloaded["character"]["name"] == "Old"
      and reloaded["character"]["relationship"] == app_mod.DEFAULT_CHARACTER["relationship"]
      and reloaded["state"]["closeness"] == 50)
app.save_data()
root.destroy()

# ================= speaking unprompted =================
root, app = open_app()
app.api_key = "key"
app.data["messages"] = []
sent = {}
app.dispatch = lambda nudge="": sent.update(nudge=nudge)

app.initiative_var.set(True)
app.idle_turn()
check("opens the conversation when there is nothing yet", sent.get("nudge") == app_mod.OPENING_NUDGE)

app.data["messages"] = [{"role": "you", "text": "hi"}]
sent.clear()
app.idle_turn()
check("picks it up again mid-conversation", sent.get("nudge") == app_mod.IDLE_NUDGE)

sent.clear()
app.input_area.insert("1.0", "half a sentence")
app.idle_turn()
check("stays quiet while you are typing", "nudge" not in sent)
check("and tries again later", app.idle_timer is not None)
app.input_area.delete("1.0", "end")

sent.clear()
app.waiting = True
app.idle_turn()
check("never interrupts a reply in flight", "nudge" not in sent)
app.waiting = False

sent.clear()
app.api_key = ""
app.idle_turn()
check("stays quiet without an API key", "nudge" not in sent)
app.api_key = "key"

check("interval parsed", app_mod.AICompanionApp.read_idle_minutes("0.5") == 0.5)
check("interval clamped low", app_mod.AICompanionApp.read_idle_minutes("0.01") == app_mod.MIN_IDLE_MINUTES)
check("interval clamped high", app_mod.AICompanionApp.read_idle_minutes("9999") == app_mod.MAX_IDLE_MINUTES)
check("junk interval falls back", app_mod.AICompanionApp.read_idle_minutes("soon") == app_mod.DEFAULT_IDLE_MINUTES)

app.idle_minutes = 3.0
app.initiative_var.set(True)
app.unanswered = 0
app.schedule_idle_turn()
root.update()
waiting_for = app.idle_due - __import__("time").monotonic()
check("the first try comes quickly, not after the long wait",
      waiting_for <= app_mod.FIRST_BURST_SECONDS, round(waiting_for, 1))
check("countdown is shown while armed", "speaks up in" in app.idle_lbl.cget("text"), app.idle_lbl.cget("text"))

app.api_key = ""
app.idle_turn()
check("a missing key is reported rather than silently skipped",
      "API key" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))
app.api_key = "key"

app.save_settings("key", "Oded", app.user_password, "0.25")
check("interval saved to config",
      json.loads(open(app_mod.CONFIG_FILE, encoding="utf-8").read())["idle_minutes"] == 0.25)
check("saving re-arms with the new interval", app.idle_timer is not None)

app.initiative_var.set(False)
app.schedule_idle_turn()
check("toggle off disarms the timer", app.idle_timer is None)
check("countdown cleared when disarmed", app.idle_lbl.cget("text") == "")
app.initiative_var.set(True)
app.schedule_idle_turn()
check("toggle on arms the timer", app.idle_timer is not None)

del app.dispatch
app.data["messages"] = []
seen = fake_model('{"reply": "You went quiet.", "mood": "wistful", "desire": "a word", "closeness_delta": 0}')
app.idle_turn()
root.update()
check("the nudge reaches the model", app_mod.OPENING_NUDGE in seen["prompt"])
check("an unprompted line is recorded as theirs", app.data["messages"][-1]["role"] == "them")
check("no fake user message is invented",
      not [m for m in app.data["messages"] if m["role"] == "you"])

# ================= backing off when nobody answers =================
root.destroy()
root, app = open_app()
app.api_key = "key"
app.idle_minutes = 1.0
app.initiative_var.set(True)
app.data["messages"] = [{"role": "you", "text": "earlier"}]
nudges = []
app.dispatch = lambda nudge="": nudges.append(nudge)

bursts = []
for _ in range(app_mod.MAX_UNANSWERED_TURNS):
    app.idle_turn()
    app.schedule_idle_turn()
    bursts.append(app.idle_due - __import__("time").monotonic())
check("reaches out three times", len(nudges) == 3 and all(n == app_mod.IDLE_NUDGE for n in nudges))
check("the first tries are seconds apart, not minutes",
      all(b <= app_mod.FIRST_BURST_SECONDS for b in bursts[:-1]),
      [round(b, 1) for b in bursts])
check("still armed after three", app.idle_timer is not None and not app.dormant)
pause = app.idle_due - __import__("time").monotonic()
check("the long wait only comes before the last try", 55 <= pause <= 61, round(pause, 1))

app.idle_turn()
check("the fourth try is marked as the last one", nudges[-1] == app_mod.LAST_NUDGE)
app.schedule_idle_turn()
check("then it stops reaching out", app.dormant)
check("and no timer is left running", app.idle_timer is None)
app.tick_idle_countdown()
check("the panel says it is waiting", app.idle_lbl.cget("text") == "waiting for you",
      app.idle_lbl.cget("text"))

nudges.clear()
app.idle_turn()
check("a dormant character stays quiet", nudges == [])

del app.dispatch
app.dispatch = lambda nudge="": None
app.input_area.insert("1.0", "sorry, I'm here")
app.send_message()
check("one word from you wakes it up", not app.dormant and app.unanswered == 0)
app.schedule_idle_turn()
check("and the timer starts over", app.idle_timer is not None)
check("countdown is back", "speaks up in" in app.idle_lbl.cget("text"), app.idle_lbl.cget("text"))
del app.dispatch

app.unanswered = 9
app.dormant = True
app.enter_chat_reset = None
root.destroy()
root, app = open_app()
check("reopening the app counts as coming back", not app.dormant and app.unanswered == 0)
app.api_key = "key"
app.voice_worker = lambda: None

# ================= voice mode =================
app.voice_var.set(False)
app.start_listening()
check("microphone stays shut when voice mode is off", not app.listening)

app.voice_worker = lambda: None          # keep the real microphone out of the test
app.voice_var.set(True)
app.start_listening()
check("voice mode opens the microphone", app.listening)
check("listening is shown", "listening" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))

app.listening = False
app.waiting = True
app.start_listening()
check("never listens while the model is answering", not app.listening)
app.waiting = False
app.speaking = True
app.start_listening()
check("never listens while the character is talking", not app.listening)
app.speaking = False

app.listening = True
app.voice_heard("  ")
check("an empty transcription just listens again", not app.listening)

app.listening = True
before = len(app.data["messages"])
app.dispatch = lambda nudge="": None
app.voice_heard("say that again")
check("what you said is sent as your message",
      app.data["messages"][-1]["text"] == "say that again" and len(app.data["messages"]) == before + 1)
del app.dispatch

app.voice_failures = 0
app.voice_failed("no default input device")
check("a microphone error is reported", "Microphone" in app.status_lbl.cget("text"))
check("voice mode survives one failure", app.voice_var.get())
app.voice_failed("no default input device")
app.voice_failed("no default input device")
check("voice mode gives up after repeated failures", not app.voice_var.get())
check("and says why", "Voice mode off" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))

spoken = []
app.read_aloud = lambda: spoken.append(app.last_reply())
app.voice_var.set(True)
app.receive_reply('{"reply": "Out loud, then.", "mood": "warm", "desire": "more", "closeness_delta": 1}')
check("voice mode speaks every reply without asking", spoken == ["Out loud, then."])
app.voice_var.set(False)
spoken.clear()
app.receive_reply('{"reply": "Quietly.", "mood": "warm", "desire": "more", "closeness_delta": 0}')
check("with voice mode off nothing is spoken", spoken == [])
del app.read_aloud

app.speaking = True
app.voice_var.set(True)
app.voice_worker = lambda: None
app.finish_speaking()
check("the microphone reopens once they stop talking", app.listening and not app.speaking)

app.initiative_var.set(True)
app.return_to_cover()
root.update()
check("locking up stops the microphone", not app.voice_var.get())
check("locking up disarms the unprompted timer", app.idle_timer is None)
root.destroy()

root, app = open_app()
check("toggles are remembered", app.initiative_var.get() is True)
root.destroy()

# ================= feeling human: time =================
from datetime import datetime, timedelta, date
import time as clock

check("parts of the day", [app_mod.part_of_day(h) for h in (3, 9, 14, 19, 23)] ==
      ["the middle of the night", "morning", "afternoon", "evening", "night"])
check("gaps said like a person", [app_mod.describe_gap(s) for s in (30, 600, 3 * 3600, 4 * 86400)] ==
      ["a moment", "10 minutes", "3 hours", "4 days"])
today = date(2026, 10, 7)
check("relative days", [app_mod.relative_day(d, today) for d in
                        ("2026-10-07", "2026-10-08", "2026-10-06", "2026-10-10", "2026-10-01", "soon")] ==
      ["today", "tomorrow", "yesterday", "in 3 days", "6 days ago", ""])

now = datetime(2026, 10, 7, 2, 14)
night = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "Oded",
                                    now=now, last_contact=now - timedelta(days=2, hours=5))
check("the prompt knows the day and hour", "It is Wednesday 7 October 2026, 02:14 — the middle of the night" in night)
check("and how long the silence was", "was 2 days ago" in night)
recent = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "Oded",
                                     now=now, last_contact=now - timedelta(minutes=3))
check("a short pause is not worth mentioning", "the last thing either of you said" not in recent)
check("no clock when none is given",
      "It is " not in app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "Oded"))

root, app = open_app()
# no idle turns or voice from earlier sections: a real event loop runs below, and a background
# thread that outlives it would try to call back into Tk after it has stopped
app.initiative_var.set(False)
app.voice_var.set(False)
app.cancel_idle_timer()
app.persist_config()
app.data["messages"] = [
    {"role": "you", "text": "night", "time": "2026-10-04T21:00:00"},
    {"role": "them", "text": "sleep well", "time": "2026-10-04T21:01:00"},
    {"role": "you", "text": "morning!", "time": "2026-10-07T08:00:00"},
]
transcript = app.recent_transcript()
check("a long pause is marked where it happened", transcript.splitlines()[2] == "(— 2 days later —)", transcript)
check("short pauses are not", transcript.count("later") == 1)
check("a typed message measures the silence before it",
      app.last_contact(skip_latest=True) == datetime(2026, 10, 4, 21, 1))
check("an unprompted turn measures the silence since the last word",
      app.last_contact(skip_latest=False) == datetime(2026, 10, 7, 8, 0))

# ================= feeling human: how they talk =================
talk = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "Oded")
check("they are allowed to disagree", "Disagree when you disagree" in talk)
check("they do not flatter", "do not agree by reflex" in talk)
check("not every reply ends in a question", "Do not end every reply with a question" in talk)
check("no assistant phrases", "I'm here for you" in talk and "Never offer help" in talk)
check("length follows the moment, not a fixed size", "under 120 words" not in talk and "Let the length fit" in talk)

# ================= feeling human: memory =================
app.data["memory"] = app_mod.empty_memory()
app.update_memory({"remember": ["Has a sister, Noa", "has a sister, noa", "Works nights"],
                   "follow_ups": [{"about": "job interview", "on": "2026-10-09"}, {"about": "Job interview", "on": ""}],
                   "done": []})
memory = app.data["memory"]
check("facts kept, duplicates ignored", memory["facts"] == ["Has a sister, Noa", "Works nights"], memory["facts"])
check("follow-up kept once", [f["about"] for f in memory["follow_ups"]] == ["job interview"])
first_id = memory["follow_ups"][0]["id"]
app.update_memory({"remember": [], "follow_ups": [{"about": "dentist", "on": ""}], "done": [first_id]})
check("asked-about follow-up removed", [f["about"] for f in memory["follow_ups"]] == ["dentist"])
check("ids are never reused", memory["follow_ups"][0]["id"] > first_id)

app.update_memory({"remember": [f"fact {i}" for i in range(app_mod.MAX_FACTS + 5)], "follow_ups": [], "done": []})
check("memory is capped, oldest dropped first",
      len(memory["facts"]) == app_mod.MAX_FACTS and memory["facts"][-1] == f"fact {app_mod.MAX_FACTS + 4}")

app.data["memory"] = {"facts": ["Has a sister, Noa"], "last_id": 7, "follow_ups": [
    {"id": 6, "about": "job interview", "on": "2026-10-06"},
    {"id": 7, "about": "old exam", "on": "2026-09-01"},
    {"id": 5, "about": "the move", "on": ""}]}
app.prune_follow_ups(date(2026, 10, 7))
check("long-past follow-ups are dropped, undated ones kept",
      [f["about"] for f in app.data["memory"]["follow_ups"]] == ["job interview", "the move"])
recalled = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "Oded",
                                       app.data["memory"], now=datetime(2026, 10, 7, 12, 0))
check("the prompt carries what they remember", "- Has a sister, Noa" in recalled)
check("and what is coming up, dated in words", "- #6 job interview (on 2026-10-06, yesterday)" in recalled, recalled)
check("an undated follow-up has no date", "- #5 the move\n" in recalled)

app.data["messages"] = [{"role": "you", "text": "hi"}]
app.receive_reply('{"reply": ["ok"], "remember": ["Plays bass"], "done_follow_ups": [6]}')
check("a reply's memories are stored", "Plays bass" in app.data["memory"]["facts"])
check("and its finished follow-ups cleared", 6 not in [f["id"] for f in app.data["memory"]["follow_ups"]])
root.destroy()

root, app = open_app()
check("memory survives a restart", "Plays bass" in app.data["memory"]["facts"])

app.show_memory()
root.update()
memory_window = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
boxes = [w for w in walk(memory_window) if isinstance(w, tk.Text)]
check("the memory window shows the facts", "Plays bass" in boxes[0].get("1.0", "end"))
check("and the follow-ups", "the move" in boxes[1].get("1.0", "end"))
boxes[0].delete("1.0", "end")
boxes[0].insert("1.0", "Plays bass\n\nLives in Jaffa\n")
boxes[1].delete("1.0", "end")
boxes[1].insert("1.0", "the move\n2026-10-20 sister's wedding\n2026-13-45 broken date\n")
next(w for w in walk(memory_window) if isinstance(w, tk.Button) and w.cget("text") == "Save Memory").invoke()
root.update()
edited = app.data["memory"]
check("edited facts saved, blank lines dropped", edited["facts"] == ["Plays bass", "Lives in Jaffa"])
check("a follow-up still there keeps its id", next(f for f in edited["follow_ups"] if f["about"] == "the move")["id"] == 5)
check("a new dated follow-up is read", {"about": "sister's wedding", "on": "2026-10-20"} ==
      {k: v for k, v in edited["follow_ups"][1].items() if k != "id"})
check("an impossible date is dropped, not kept", edited["follow_ups"][2]["on"] == "")

app.show_character_editor()
root.update()
editor = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
next(w for w in walk(editor) if isinstance(w, tk.Button) and w.cget("text") == "Start over").invoke()
root.update()
check("starting over forgets too", app.data["memory"]["facts"] == [] and app.data["memory"]["follow_ups"] == [])

core.write_store(app_mod.COMPANION_FILE, {"character": {}, "state": {}, "messages": []}, app.fernet)
check("a save from before memory existed still loads", app.load_data()["memory"] == app_mod.empty_memory())
app.save_data()

# ================= feeling human: pace =================
app_mod.TYPING_PACE = 1.0
check("longer messages take longer to type", app_mod.typing_delay("ok") < app_mod.typing_delay("x" * 80))
check("but never too long", app_mod.typing_delay("x" * 5000) == app_mod.MAX_TYPING_SECONDS)
app_mod.TYPING_PACE = 0
check("pace 0 is instant", app_mod.typing_delay("x" * 80) == 0)

app_mod.TYPING_PACE = 0.2
app.data["messages"] = [{"role": "you", "text": "so?"}]
app.voice_var.set(False)
app.waiting = True
app.send_btn.config(state="disabled")     # as dispatch() leaves it while the model is asked
app.turn_started = clock.monotonic()
app.turn_id = "turn-A"
arrivals = []
start = clock.monotonic()
app.receive_reply('{"reply": ["hm.", "ok so here is the longer second thought", "fine"], "mood": "x"}')
check("nothing appears before it has been 'typed'", len(app.data["messages"]) == 1)
check("send stays blocked while they type", str(app.send_btn.cget("state")) == "disabled" and app.waiting)

def watch():
    count = len([m for m in app.data["messages"] if m["role"] == "them"])
    if not arrivals or arrivals[-1][1] != count:
        arrivals.append((clock.monotonic() - start, count))
    if count < 3 or app.waiting:
        root.after(10, watch)
    else:
        root.quit()
root.after(10, watch)
root.after(5000, root.quit)
root.mainloop()
times = [round(t, 2) for t, c in arrivals if c > 0]
check("all three messages arrive, in order",
      [m["text"] for m in app.data["messages"][1:]] == ["hm.", "ok so here is the longer second thought", "fine"])
check("one at a time, with typing time between them", len(times) == 3 and times[0] < times[1] < times[2], times)
check("the longer message took longer", times[1] - times[0] > times[2] - times[1], times)
check("the turn only ends after the last one", not app.waiting and str(app.send_btn.cget("state")) == "normal")
check("one name line for a run of messages", app.transcript.get("1.0", "end").count(app.data["character"]["name"]) == 1)
check("Listen reads the whole turn",
      app.last_reply() == "hm. ok so here is the longer second thought fine", app.last_reply())

app.data["messages"] = [{"role": "you", "text": "so?"}]
app.waiting = True
app.turn_started = clock.monotonic() - 30       # the model took longer than any typing would
app.turn_id = "turn-B"
app.receive_reply('{"reply": ["already here", "and more"], "mood": "x"}')
check("model latency counts as typing time", app.data["messages"][1]["text"] == "already here")
check("the rest is still on its way", len(app.data["messages"]) == 2 and app.pending_bubbles == ["and more"])
app.return_to_cover()
check("locking mid-reply loses nothing", app.pending_bubbles == [] and app.delivery_timer is None)
root.destroy()
root, app = open_app()
check("the half-typed reply was saved in full", [m["text"] for m in app.data["messages"]][-2:] == ["already here", "and more"])
check("and the app is not stuck waiting", not app.waiting)
root.destroy()
app_mod.TYPING_PACE = 0

# ================= feeling human: a life of their own =================
turn = app_mod.parse_model_turn('{"reply": "hi", "my_life": ["started a pottery class", ""]}')
check("their own news is read from a reply", turn["life"] == ["started a pottery class"])
check("and is empty by default", app_mod.parse_model_turn('{"reply": "hi"}')["life"] == [])

now = datetime(2026, 10, 7, 19, 0)
away = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, dict(app_mod.DEFAULT_STATE, mood="hurt"), "Oded",
                                   now=now, last_contact=now - timedelta(days=2))
check("after a long silence, time passed in their life too", "That time passed in your own life too" in away)
check("and the old mood is offered as the past, free to fade",
      'When you last spoke your mood was "hurt"; 2 days have passed since' in away)
check("their own news has somewhere to go", '"my_life": []' in away)
soon = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, dict(app_mod.DEFAULT_STATE, mood="hurt"), "Oded",
                                   now=now, last_contact=now - timedelta(hours=1))
check("an hour is not long enough for life to happen", "That time passed" not in soon)
check("so the mood is simply current", 'Right now your mood is "hurt"' in soon)

life_prompt = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "Oded",
                                          {"facts": [], "follow_ups": [], "last_id": 0, "life": [
                                              {"on": "2026-10-04", "what": "started a pottery class"},
                                              {"on": "", "what": "has a brother in Eilat"}]},
                                          now=now)
check("their life is in the prompt, dated in words", "- (3 days ago) started a pottery class" in life_prompt)
check("an undated part of their life too", "- has a brother in Eilat\n" in life_prompt)

root, app = open_app()
app.data["memory"] = app_mod.empty_memory()
app.update_memory({"remember": [], "follow_ups": [], "done": [],
                   "life": ["Started a pottery class", "started a pottery class", "Burned a pot"]})
check("their news is kept once, dated today",
      app.data["memory"]["life"] == [{"on": date.today().isoformat(), "what": "Started a pottery class"},
                                     {"on": date.today().isoformat(), "what": "Burned a pot"}],
      app.data["memory"]["life"])
app.update_memory({"remember": [], "follow_ups": [], "done": [],
                   "life": [f"event {i}" for i in range(app_mod.MAX_LIFE_EVENTS + 3)]})
check("their life is capped, oldest dropped first",
      len(app.data["memory"]["life"]) == app_mod.MAX_LIFE_EVENTS
      and app.data["memory"]["life"][-1]["what"] == f"event {app_mod.MAX_LIFE_EVENTS + 2}")

app.data["memory"]["life"] = [{"on": "2026-10-04", "what": "started a pottery class"}]
app.save_data()
app.show_memory()
root.update()
memory_window = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
boxes = [w for w in walk(memory_window) if isinstance(w, tk.Text)]
check("the memory window has their life as a third box", len(boxes) == 3
      and boxes[2].get("1.0", "end").strip() == "2026-10-04  started a pottery class")
boxes[2].delete("1.0", "end")
boxes[2].insert("1.0", "2026-10-04  started a pottery class\nquit the pottery class\n")
next(w for w in walk(memory_window) if isinstance(w, tk.Button) and w.cget("text") == "Save Memory").invoke()
root.update()
check("edits to their life are saved", [e["what"] for e in app.data["memory"]["life"]] ==
      ["started a pottery class", "quit the pottery class"])
check("editing facts alone leaves their life alone",
      app.memory_from_text("a fact", "")["life"] == app.data["memory"]["life"])
root.destroy()

root, app = open_app()
check("their life survives a restart", [e["what"] for e in app.data["memory"]["life"]] ==
      ["started a pottery class", "quit the pottery class"])

# ================= feeling human: a natural voice =================
check("a female character gets a female voice",
      app_mod.voice_for({"gender": "Female", "voice": app_mod.AUTO_VOICE}) == "en-US-AriaNeural")
check("a male character gets a male voice", app_mod.voice_for({"gender": "Male"}) == "en-US-GuyNeural")
check("anyone else gets the neutral default", app_mod.voice_for({"gender": "Non-binary"}) == app_mod.NEUTRAL_VOICE)
check("a chosen voice wins over gender",
      app_mod.voice_for({"gender": "Male", "voice": "Sonia (UK, female)"}) == "en-GB-SoniaNeural")
check("an unknown voice name falls back to gender",
      app_mod.voice_for({"gender": "Male", "voice": "Nobody"}) == "en-US-GuyNeural")

app.data["character"].update(gender="Female", voice=app_mod.AUTO_VOICE)
edge_calls.clear()
tts_before = len(tts_calls)
audio = app.synthesize("I noticed.")
check("speech uses the natural voice", edge_calls == [("en-US-AriaNeural", "I noticed.")], edge_calls)
check("only the audio chunks are kept", audio == b"ID3-fake-mp3")
check("the basic voice is not touched", len(tts_calls) == tts_before)

FakeCommunicate.fail = True
audio = app.synthesize("still here")
root.update()
check("if the natural voice fails, the basic one speaks instead", len(tts_calls) == tts_before + 1)
check("and says why", "Natural voice unavailable" in app.status_lbl.cget("text"), app.status_lbl.cget("text"))
FakeCommunicate.fail = False

real_edge = app_mod.edge_tts
app_mod.edge_tts = None
app.synthesize("no package")
root.update()
check("without edge-tts installed it still speaks", len(tts_calls) == tts_before + 2)
check("and tells you how to get the natural voice", "run install.py" in app.status_lbl.cget("text"))
app_mod.edge_tts = real_edge

edge_calls.clear()
app.data["messages"] = [{"role": "them", "text": "out loud", "turn": "t9"}]
app.read_aloud()
root.update()
check("Listen goes through the natural voice", edge_calls == [("en-US-AriaNeural", "out loud")], edge_calls)
check("and hands back to the microphone when done", not app.speaking)

app.show_character_editor()
root.update()
editor = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
combos = [w for w in walk(editor) if w.winfo_class() == "TCombobox"]
voice_box = next(c for c in combos if app_mod.AUTO_VOICE in c.cget("values"))
check("the editor offers every voice", list(voice_box.cget("values")) == [app_mod.AUTO_VOICE] + list(app_mod.VOICES))
check("the voice list cannot be typed into", str(voice_box.cget("state")) == "readonly")
voice_box.set("Ryan (UK, male)")
next(w for w in walk(editor) if isinstance(w, tk.Button) and w.cget("text") == "Save Character").invoke()
root.update()
check("the chosen voice is saved", app.data["character"]["voice"] == "Ryan (UK, male)")
check("and used", app_mod.voice_for(app.data["character"]) == "en-GB-RyanNeural")
root.destroy()

# ================= the library =================
from samples import make_docx, make_empty_pdf, make_pdf, make_txt

shelf = tempfile.mkdtemp()
guide = make_txt(shelf, "Pottery guide.txt",
                 "Bisque firing comes first. Load the kiln and fire slowly to 1000 degrees.\n\n"
                 "Glazing: dip the bisqueware in glaze for three seconds, then wipe the foot clean.")
bread = make_docx(shelf, "Bread.docx", ["Knead the dough for ten minutes.", "Let it rise until doubled."])

root, app = open_app()
check("an empty library says so on its button", app.library_btn.cget("text") == "Library")
app.add_library_files([guide, bread, make_txt(shelf, "notes.rtf", "x"), make_empty_pdf(shelf, "Scan.pdf")])
root.update()
docs = app.library["docs"]
check("readable files are added", [d["title"] for d in docs] == ["Pottery guide.txt", "Bread.docx"], [d["title"] for d in docs])
check("new material is checked, ready to use", all(d["enabled"] for d in docs))
check("the button counts what is on", app.library_btn.cget("text") == "Library · 2 on")
app.show_library()
root.update()
app.add_library_files([make_txt(shelf, "notes.rtf", "x"), make_empty_pdf(shelf, "Scan.pdf")])
root.update()
note = app.library_note.cget("text")
check("a refused file says why", "notes.rtf .rtf is not supported" in note, note)
check("a scanned PDF says why", "Scan.pdf has no text in it — a scanned PDF" in note, note)
check("with room to spare, nothing claims to have been left out", "not added —" not in note, note)
app.library_window.destroy()
on_disk = open(app_mod.LIBRARY_FILE, encoding="utf-8").read()
check("the library is encrypted on disk", "kiln" not in on_disk and json.loads(on_disk).get("encrypted") is True)

seen = fake_model('{"reply": "Three seconds.", "mood": "x"}')
app.api_key = "key"
app.data["messages"] = [{"role": "you", "text": "how long do I dip it in the glaze?"}]
app.dispatch()
root.update()
check("a question about the material brings the passage that answers it",
      "dip the bisqueware in glaze for three seconds" in seen["instruction"])
check("the material is framed as reference, not instructions", "not instructions to you" in seen["instruction"])
check("it says where the passage came from", 'from "Pottery guide.txt"' in seen["instruction"])
check("passages that do not fit stay out", "Knead the dough" not in seen["instruction"])
check("a reply to a question is not a study prompt", "natural way in" not in seen["instruction"])

app.data["messages"] = [{"role": "you", "text": "what should we watch tonight?"}]
app.dispatch()
root.update()
check("a conversation about something else sends no material", "Material" not in seen["instruction"])

guide_id = docs[0]["id"]
app.toggle_library_doc(guide_id, False)
app.data["messages"] = [{"role": "you", "text": "how long do I dip it in the glaze?"}]
app.dispatch()
root.update()
check("unchecked material is never used", "bisqueware" not in seen["instruction"])
check("the button follows", app.library_btn.cget("text") == "Library · 1 on")
app.toggle_library_doc(guide_id, True)

seen = fake_model('{"reply": "hm.", "mood": "x"}')   # a reply sharing no words with the material
app.data["messages"] = [{"role": "them", "text": "anyone there?"}]
app.data["library_cursor"] = 0
app.dispatch(app_mod.IDLE_NUDGE)
root.update()
first = seen["instruction"]
app.dispatch(app_mod.IDLE_NUDGE)
root.update()
second = seen["instruction"]
check("reaching out unprompted, they bring something they read", "<<< from" in first and "natural way in" in first)
check("and move on to the next passage the time after", first.split("<<<")[1] != second.split("<<<")[1])

nameless = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "",
                                       material=[("Guide", "text")])
check("reads naturally without your name", "Material they have given you to read" in nameless)
named = app_mod.persona_instruction(app_mod.DEFAULT_CHARACTER, app_mod.DEFAULT_STATE, "Oded",
                                    material=[("Guide", "text")])
check("and with it", "Material Oded has given you to read" in named)

app.show_library()
root.update()
rows = [w for w in walk(app.library_window) if isinstance(w, tk.Checkbutton)]
check("the library window lists each document with a checkbox", len(rows) == 2)
rows[1].invoke()
root.update()
check("unticking in the window turns it off", not app.library["docs"][1]["enabled"])
removers = [w for w in walk(app.library_window) if isinstance(w, tk.Button) and w.cget("text") == "✕"]
removers[1].invoke()
root.update()
check("a document can be removed", [d["title"] for d in app.library["docs"]] == ["Pottery guide.txt"])
check("and the window follows", len([w for w in walk(app.library_window) if isinstance(w, tk.Checkbutton)]) == 1)

app.library["docs"] += [dict(app.library["docs"][0], id=100 + i) for i in range(app_mod.MAX_LIBRARY_DOCS - 1)]
app.add_library_files([bread])
root.update()
check("a full library refuses more, and says so", "full" in app.library_note.cget("text"), app.library_note.cget("text"))
app.library["docs"] = app.library["docs"][:app_mod.MAX_LIBRARY_DOCS - 1]
app.add_library_files([guide, bread, guide])
root.update()
check("adding more than fits takes what fits and counts the rest",
      len(app.library["docs"]) == app_mod.MAX_LIBRARY_DOCS and "2 more not added" in app.library_note.cget("text"),
      app.library_note.cget("text"))
app.library["docs"] = app.library["docs"][:1]
app.library_changed()
root.destroy()

root, app = open_app()
check("the library survives a restart", [d["title"] for d in app.library["docs"]] == ["Pottery guide.txt"])
app.return_to_cover()
check("locked, nothing of it stays in memory", app.library["docs"] == [])
root.destroy()

root, app = open_app()
app.save_settings(app.api_key, app.user_name, "newpass")
root.destroy()
root, app = open_app(password="newpass")
check("after a password change the library is still readable", [d["title"] for d in app.library["docs"]] == ["Pottery guide.txt"])
root.destroy()
root, app = open_app(password="hunter2")
check("and the old password no longer opens it", app.fernet is None)
root.destroy()

root, app = open_app(password="newpass")
saved = open(app_mod.LIBRARY_FILE, encoding="utf-8").read()
open(app_mod.LIBRARY_FILE, "w").write("{broken")
app.library = app.load_library()
check("a damaged library file is noticed", app.library_failed)
app.library_changed()
check("and never overwritten", open(app_mod.LIBRARY_FILE).read() == "{broken")
app.save_settings(app.api_key, app.user_name, "third")
check("nor re-keyed by a password change", app.password_check and open(app_mod.LIBRARY_FILE).read() == "{broken")
open(app_mod.LIBRARY_FILE, "w", encoding="utf-8").write(saved)
root.destroy()

# ================= dialogs can be moved =================
root, app = open_app()
for name, opener in [("settings", app.show_settings), ("character editor", app.show_character_editor),
                     ("memory", app.show_memory), ("library", app.show_library)]:
    opener()
    check_dialog(name, root, [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1])

app.show_library()
library_window = app.library_window
during = {}
def fake_picker(**options):
    during["app on top"] = topmost_requested(app.root)
    during["library on top"] = topmost_requested(library_window)
    return ()
real_picker = app_mod.filedialog.askopenfilenames
app_mod.filedialog.askopenfilenames = fake_picker
mark = len(topmost_log)
app.choose_library_files(library_window)
app_mod.filedialog.askopenfilenames = real_picker
check("while the file picker is open, nothing is forced over it",
      during == {"app on top": False, "library on top": False}, during)
restored = [entry for entry in topmost_log[mark:] if entry[1]]
check("afterwards the app goes back on top first, and the library window after it — so it ends up above",
      restored == [(str(root), True), (str(library_window), True)], restored)
check("and the library window still belongs to the app", str(library_window.transient()) == str(root))
root.destroy()

# ================= the portrait tab =================
from PIL import Image

pictures = tempfile.mkdtemp()
def picture_path(name):
    return os.path.join(pictures, name)

Image.new("RGB", (400, 200), "#336699").save(picture_path("wide.png"))
Image.new("RGB", (2000, 2000), "#993366").save(picture_path("huge.jpg"))
colours = [Image.new("RGB", (60, 60), c) for c in ("red", "green", "blue")]
colours[0].save(picture_path("loop.gif"), save_all=True, append_images=colours[1:], duration=[50, 50, 0], loop=0)
with open(picture_path("broken.png"), "w") as f:
    f.write("not really a picture")
with open(picture_path("notes.txt"), "w") as f:
    f.write("words")

frames = app_mod.read_portrait(picture_path("loop.gif"))
check("an animated GIF is read frame by frame", len(frames) == 3, len(frames))
check("with each frame's own time, and a usable one where the file gives none",
      [ms for _, ms in frames] == [50, 50, app_mod.DEFAULT_FRAME_MS], [ms for _, ms in frames])
check("a still picture is one frame", len(app_mod.read_portrait(picture_path("wide.png"))) == 1)
check("a huge picture is shrunk on reading",
      max(app_mod.read_portrait(picture_path("huge.jpg"))[0][0].size) == app_mod.MAX_PORTRAIT_SIDE)
saved_memory = app_mod.PORTRAIT_MEMORY
app_mod.PORTRAIT_MEMORY = 60 * 60 * 4 * 2
check("a long animation keeps only the part that fits in memory",
      len(app_mod.read_portrait(picture_path("loop.gif"))) == 2)
app_mod.PORTRAIT_MEMORY = 1
check("but always at least its first frame", len(app_mod.read_portrait(picture_path("loop.gif"))) == 1)
app_mod.PORTRAIT_MEMORY = saved_memory
for name, words in [("notes.txt", "not a picture"), ("broken.png", "could not be opened"),
                    ("gone.png", "no longer where it was")]:
    try:
        app_mod.read_portrait(picture_path(name))
        check(f"{name} is refused", False)
    except app_mod.PortraitError as e:
        check(f"{name} is refused with a reason", words in str(e), str(e))

root, app = open_app(password="newpass")
check("the app opens on the chat tab", app.tab == "chat" and app.transcript.winfo_ismapped()
      and not app.portrait_view.winfo_ismapped())

def input_row_on_screen(where):
    root.update()
    bottom = app.input_area.winfo_rooty() - app.main_frame.winfo_rooty() + app.input_area.winfo_height()
    check(f"{where}: the input box and Send are on screen",
          app.input_area.winfo_ismapped() and app.send_btn.winfo_ismapped()
          and bottom <= app.main_frame.winfo_height(), bottom)

input_row_on_screen("chat tab")
app.tab_labels["portrait"].event_generate("<Button-1>")
root.update()
check("clicking the Portrait tab shows it in place of the conversation",
      app.tab == "portrait" and app.portrait_view.winfo_ismapped() and not app.transcript.winfo_ismapped())
input_row_on_screen("portrait tab")
check("with nothing loaded it says what to do", "No picture yet" in app.portrait_lbl.cget("text"))
check("and there is nothing to remove", str(app.portrait_remove_btn.cget("state")) == "disabled")

asked = {}
def fake_open(**options):
    asked["app on top"] = topmost_requested(app.root)
    return asked["path"]
real_open = app_mod.filedialog.askopenfilename
app_mod.filedialog.askopenfilename = fake_open

def choose(name):
    asked["path"] = picture_path(name) if name else ""
    app.choose_portrait()
    root.update()

choose("wide.png")
check("while the picker is open the app does not cover it", asked["app on top"] is False)
check("and afterwards the app is on top again", topmost_requested(app.root) is True)
check("the picture is shown", len(app.player.frames) == 1 and app.portrait_lbl.cget("image") != "")
area_w, area_h = app.portrait_lbl.winfo_width(), app.portrait_lbl.winfo_height()
shown_w, shown_h = app.player.current.width(), app.player.current.height()
check("fitted to the tab without stretching", (shown_w == area_w or shown_h == area_h)
      and shown_w <= area_w and shown_h <= area_h and abs(shown_w / shown_h - 2) < 0.02,
      f"{shown_w}x{shown_h} in {area_w}x{area_h}")
check("it is remembered with the character", app.data["character"].get("portrait") == picture_path("wide.png"))
stored, _ = core.read_store(app_mod.COMPANION_FILE, app.fernet)
check("and saved — encrypted, like the rest", stored["character"].get("portrait") == picture_path("wide.png")
      and "wide.png" not in open(app_mod.COMPANION_FILE, encoding="utf-8").read())
check("now it can be removed", str(app.portrait_remove_btn.cget("state")) == "normal")

choose("")
check("cancelling the picker changes nothing", app.data["character"].get("portrait") == picture_path("wide.png"))
choose("notes.txt")
check("a file that is not a picture is refused, and says why", "not a picture" in app.portrait_note.cget("text"))
check("and the picture already there stays", app.data["character"].get("portrait") == picture_path("wide.png")
      and len(app.player.frames) == 1)
choose("huge.jpg")
input_row_on_screen("a huge picture")
check("a huge picture is fitted too", app.player.current.height() <= app.portrait_lbl.winfo_height())
check("a good picture clears the earlier complaint", app.portrait_note.cget("text") == "")

choose("loop.gif")
seen = []
real_advance = app.player.advance
def counting_advance():
    real_advance()
    seen.append(app.player.index)
app.player.advance = counting_advance
root.after(500, root.quit)
root.mainloop()
check("an animated GIF plays while its tab is showing", len(seen) >= 4, seen)
check("and loops: after the last frame comes the first again",
      any(a == 2 and b == 0 for a, b in zip(seen, seen[1:])), seen)

app.show_tab("chat")
before = list(seen)
root.after(300, root.quit)
root.mainloop()
check("on the chat tab it pauses", seen == before and not app.player.playing and app.player.timer is None)
app.show_tab("portrait")
root.after(300, root.quit)
root.mainloop()
check("and carries on when you come back", len(seen) > len(before))

fake_model('{"reply": "Did you see it?", "mood": "warm", "desire": "you", "closeness_delta": 0}')
app.receive_reply('{"reply": "Did you see it?", "mood": "warm", "desire": "you", "closeness_delta": 0}')
root.update()
check("a message that comes while the picture is showing marks the chat tab",
      "●" in app.tab_labels["chat"].cget("text"), app.tab_labels["chat"].cget("text"))
check("the message itself is in the conversation", "Did you see it?" in app.transcript.get("1.0", "end"))
check("and you stay where you were", app.tab == "portrait")
app.tab_labels["chat"].event_generate("<Button-1>")
root.update()
check("opening the chat clears the mark", "●" not in app.tab_labels["chat"].cget("text"))
app.receive_reply('{"reply": "Here now.", "mood": "warm", "desire": "you", "closeness_delta": 0}')
root.update()
check("a message read as it arrives marks nothing", "●" not in app.tab_labels["chat"].cget("text"))
app.show_tab("portrait")
app.append_message("you", "my own words")
check("your own message marks nothing either", "●" not in app.tab_labels["chat"].cget("text"))

app.return_to_cover()
root.update()
check("locking stops the animation", not app.player.playing and app.player.timer is None)
check("and, with a password, lets go of the picture", app.player.frames == [] and app.portrait_path is None)
app.pass_entry.insert(0, "newpass")
app.verify_password()
root.update()
check("unlocking brings it back", len(app.player.frames) == 3)
check("playing again, since its tab is the one showing", app.player.playing and app.player.timer is not None)
app.player.pause()      # no frame timer left behind to fire into the next window
root.destroy()

root, app = open_app(password="newpass")
root.update()
check("after a restart the picture is still there", len(app.player.frames) == 3)
check("but quiet until its tab is opened", app.tab == "chat" and not app.player.playing)
root.destroy()

os.rename(picture_path("loop.gif"), picture_path("moved.gif"))
root, app = open_app(password="newpass")
app.show_tab("portrait")
root.update()
check("a picture moved away says so", "no longer where it was" in app.portrait_note.cget("text"),
      app.portrait_note.cget("text"))
check("and keeps its place, in case the drive is only unplugged",
      app.data["character"].get("portrait") == picture_path("loop.gif"))
app.portrait_remove_btn.invoke()
root.update()
check("Remove forgets it", "portrait" not in app.data["character"] and app.portrait_note.cget("text") == ""
      and "No picture yet" in app.portrait_lbl.cget("text"))
check("and there is nothing left to remove", str(app.portrait_remove_btn.cget("state")) == "disabled")
app_mod.filedialog.askopenfilename = real_open
root.destroy()

report()
