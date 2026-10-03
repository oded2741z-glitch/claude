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
_failures = []


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
    _stub("pygame", mixer=types.SimpleNamespace(
        init=lambda: None,
        music=types.SimpleNamespace(load=lambda f: None, play=lambda: None, get_busy=lambda: False)),
        time=types.SimpleNamespace(Clock=lambda: types.SimpleNamespace(tick=lambda n: None)))
    _stub("PIL", Image=types.SimpleNamespace(open=lambda p: None,
                                             Resampling=types.SimpleNamespace(LANCZOS=1)),
          ImageTk=types.SimpleNamespace(PhotoImage=lambda i: None))
    _stub("speech_recognition", Recognizer=object, Microphone=object,
          WaitTimeoutError=type("W", (Exception,), {}), UnknownValueError=type("U", (Exception,), {}))


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
