"""End-to-end checks for AI_Companion_App, driven headlessly under Xvfb."""

import json
import os
import types

from _harness import check, install_stubs, isolate_home, report, tts_calls

install_stubs()
home = isolate_home()

import tkinter as tk

import core
import AI_Companion_App as app_mod

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
reply, mood, desire, delta = app_mod.parse_model_reply(
    '{"reply": "I missed you.", "mood": "soft", "desire": "to hear more", "closeness_delta": 3}')
check("plain JSON parsed", (reply, mood, desire, delta) == ("I missed you.", "soft", "to hear more", 3))

reply, mood, _, _ = app_mod.parse_model_reply(
    '```json\n{"reply": "Hi.", "mood": "wary", "desire": "space", "closeness_delta": -2}\n```')
check("fenced JSON parsed", (reply, mood) == ("Hi.", "wary"))

reply, mood, _, delta = app_mod.parse_model_reply(
    'Sure thing!\n{"reply": "Hello.", "mood": "calm", "desire": "quiet", "closeness_delta": 99}')
check("JSON inside prose parsed", reply == "Hello." and mood == "calm")
check("closeness delta clamped", delta == 5, delta)

reply, mood, desire, delta = app_mod.parse_model_reply("Just *talking* normally, no JSON here.")
check("non-JSON falls back to speech", reply == "Just talking normally, no JSON here.")
check("fallback leaves mood and desire alone", mood == "" and desire == "")
check("fallback nudges closeness", delta == 1)

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

app.call_model(app.api_key, *(app_mod.persona_instruction(app.data["character"], app.data["state"], app.user_name),
                              app.recent_transcript()))
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
app.read_aloud()
check("Listen speaks the character's last line", tts_calls[-1] == "en", tts_calls)
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
app.call_model("key", "instruction", app.recent_transcript() + f"\n\n[{app_mod.IDLE_NUDGE}]")
root.update()
check("the nudge reaches the model", app_mod.IDLE_NUDGE in seen["prompt"])
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

report()
