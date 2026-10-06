"""Test harness: stubs the third-party deps so the GUI can run headless.

Run a test with:  xvfb-run -a python3 tests/test_journal.py
"""

import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

tts_calls = []
edge_calls = []
_failures = []


class FakeCommunicate:
    """Stands in for edge_tts.Communicate. Set FakeCommunicate.fail to simulate the service being down."""
    fail = False

    def __init__(self, text, voice="en-US-EmmaMultilingualNeural"):
        self.text, self.voice = text, voice

    async def stream(self):
        if FakeCommunicate.fail:
            raise RuntimeError("service unavailable")
        edge_calls.append((self.voice, self.text))
        yield {"type": "audio", "data": b"ID3-fake-mp3"}
        yield {"type": "WordBoundary", "offset": 0, "duration": 1, "text": "x"}


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def install_stubs():
    """Replace the network/audio/image deps with doubles, keeping gTTS's real validation."""
    from gtts import gTTS as RealGTTS

    class FakeGTTS:
        def __init__(self, text="", lang="en", slow=False):
            RealGTTS(text=text, lang=lang)      # still rejects a language code gTTS does not know
            tts_calls.append(lang)

        def write_to_fp(self, fp):
            fp.write(b"")

    genai = _stub("google.generativeai", configure=lambda **k: None,
                  GenerativeModel=lambda *a, **k: None)
    google = _stub("google")
    google.generativeai = genai
    _stub("gtts", gTTS=FakeGTTS)
    _stub("edge_tts", Communicate=FakeCommunicate)
    _stub("pygame", mixer=types.SimpleNamespace(
        init=lambda: None,
        music=types.SimpleNamespace(load=lambda f: None, play=lambda: None, get_busy=lambda: False)),
        time=types.SimpleNamespace(Clock=lambda: types.SimpleNamespace(tick=lambda n: None)))
    _stub("PIL", __version__="10.0.0", Image=types.SimpleNamespace(open=lambda p: None,
                                             Resampling=types.SimpleNamespace(LANCZOS=1)),
          ImageTk=types.SimpleNamespace(PhotoImage=lambda i: None))
    _stub("speech_recognition", Recognizer=object, Microphone=object,
          WaitTimeoutError=type("W", (Exception,), {}), UnknownValueError=type("U", (Exception,), {}))


class InlineThread:
    """Runs its target the moment it is started, on the calling thread.

    Real background threads outlive the test that started them and then call back into a Tk
    loop that has stopped. Running them inline keeps every test deterministic, and the code
    path is the same: the app still hands results back through root.after.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def run_threads_inline(module):
    module.threading = types.SimpleNamespace(Thread=InlineThread)


def drag(widget, dx, dy):
    """Press on a widget and move the mouse by (dx, dy), the way a real drag arrives."""
    x, y = widget.winfo_rootx() + 5, widget.winfo_rooty() + 5
    widget.event_generate("<Button-1>", x=5, y=5, rootx=x, rooty=y)
    widget.event_generate("<B1-Motion>", x=5 + dx, y=5 + dy, rootx=x + dx, rooty=y + dy)
    widget.update()


def check_dialog(name, root, window):
    """A borderless dialog opens over the app, moves when its title is dragged, and not when a field is."""
    for _ in range(3):
        root.update()
        root.update_idletasks()
    centre = lambda w: (w.winfo_x() + w.winfo_width() // 2, w.winfo_y() + w.winfo_height() // 2)
    (wx, wy), (rx, ry) = centre(window), centre(root)
    check(f"{name}: opens over the app, not in the corner", abs(wx - rx) <= 2 and abs(wy - ry) <= 2,
          f"dialog centre {wx},{wy} vs app centre {rx},{ry}")
    check(f"{name}: opens fully on screen", window.winfo_x() >= 0 and window.winfo_y() >= 0
          and window.winfo_x() + window.winfo_width() <= window.winfo_screenwidth()
          and window.winfo_y() + window.winfo_height() <= window.winfo_screenheight())

    def everything(widget):
        for child in widget.winfo_children():
            yield child
            yield from everything(child)

    title = next(w for w in everything(window) if w.winfo_class() == "Label" and str(w.cget("cursor")) == "fleur")
    before = (window.winfo_x(), window.winfo_y())
    drag(title, 60, 40)
    after = (window.winfo_x(), window.winfo_y())
    check(f"{name}: dragging the title moves it", after == (before[0] + 60, before[1] + 40), f"{before} -> {after}")

    # a text field where there is one, otherwise a checkbox or button: anything you interact with
    controls = [w for w in everything(window) if w.winfo_class() in ("Entry", "Text")] or \
               [w for w in everything(window) if w.winfo_class() in ("Checkbutton", "Button")]
    drag(controls[0], 80, 0)
    check(f"{name}: dragging on a {controls[0].winfo_class().lower()} inside it does not",
          (window.winfo_x(), window.winfo_y()) == after)
    window.destroy()


def isolate_home():
    """Point the per-user data directory at a throwaway location."""
    home = tempfile.mkdtemp()
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    os.chdir(tempfile.mkdtemp())
    return home


def check(name, condition, extra=""):
    print(("PASS  " if condition else "FAIL  ") + name + (("  -> " + str(extra)) if extra else ""))
    if not condition:
        _failures.append(name)


def report():
    print("\n" + ("ALL PASSED" if not _failures else "FAILURES: " + ", ".join(_failures)))
    sys.exit(1 if _failures else 0)
