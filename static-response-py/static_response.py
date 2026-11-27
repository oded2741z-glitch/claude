import json
import math
import os
import random
import sys
import threading

import cv2
import numpy as np
import pygame

if getattr(sys, "frozen", False):
    HERE = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
DEMO = False
IMG_DIR = "img"
AUDIO_DIR = "audio"
FONT_DIR = "fonts"

VOID = (5, 6, 5)
GLASS = (11, 13, 11)
BEZEL = (27, 28, 25)
RING = (10, 10, 9)
PHOSPHOR = (217, 220, 207)
DIM = (125, 130, 118)
BLOOD = (200, 22, 29)
OKC = (143, 211, 168)

SR = 44100
MASTER = 0.85


def now_ms():
    return pygame.time.get_ticks()


def rand(a, b):
    return a + random.random() * (b - a)


def clamp(v, a, b):
    return max(a, min(b, v))


def mix(c, bg, k):
    return tuple(int(bg[i] + (c[i] - bg[i]) * k) for i in range(3))


def brighten(c, k=1.4):
    return tuple(min(255, int(v * k)) for v in c)


def ease_in_out(k):
    return k * k * (3 - 2 * k)


# ---------------------------------------------------------------- images

def read_rgb(name):
    bgr = cv2.imread(os.path.join(IMG_DIR, name), cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def read_optional(name):
    bgr = cv2.imread(os.path.join(IMG_DIR, name), cv2.IMREAD_COLOR)
    return None if bgr is None else cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def css_filter(arr, *ops):
    a = arr.astype(np.float32) / 255.0
    for op, v in ops:
        if op == "gray":
            g = a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722
            a = np.repeat(g[..., None], 3, axis=2)
        elif op == "contrast":
            a = (a - 0.5) * v + 0.5
        elif op == "bright":
            a = a * v
        a = np.clip(a, 0, 1)
    return (a * 255).astype(np.uint8)


def rgb_surface(arr):
    arr = np.ascontiguousarray(arr)
    return pygame.image.frombytes(arr.tobytes(), (arr.shape[1], arr.shape[0]), "RGB")


def rgba_surface(rgb, alpha):
    a = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    arr = np.ascontiguousarray(np.dstack([rgb, a]))
    return pygame.image.frombytes(arr.tobytes(), (arr.shape[1], arr.shape[0]), "RGBA")


_mask_cache = {}


def ellipse_mask(w, h, inner):
    key = (w, h, inner)
    m = _mask_cache.get(key)
    if m is None:
        y = (np.arange(h) + 0.5) / h * 2 - 1
        x = (np.arange(w) + 0.5) / w * 2 - 1
        r = np.sqrt(x[None, :] ** 2 + y[:, None] ** 2)
        m = np.clip((1 - r) / (1 - inner), 0, 1).astype(np.float32)
        if len(_mask_cache) > 64:
            _mask_cache.clear()
        _mask_cache[key] = m
    return m


_mult_cache = {}


def mult_mask_surface(w, h, inner, gain=1.0):
    key = (w, h, inner, gain)
    s = _mult_cache.get(key)
    if s is None:
        m = (ellipse_mask(w, h, inner) * gain * 255).astype(np.uint8)
        s = rgb_surface(np.repeat(m[..., None], 3, axis=2))
        if len(_mult_cache) > 32:
            _mult_cache.clear()
        _mult_cache[key] = s
    return s


def masked_image(rgb, w, h, inner, opacity=1.0):
    w, h = max(1, int(w)), max(1, int(h))
    scaled = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
    return rgba_surface(scaled, ellipse_mask(w, h, inner) * opacity)


def draw_cover(g, img, w, h, zoom, ox, oy, fit):
    iw, ih = img.get_size()
    sc = (min(w / iw, h / ih) if fit else max(w / iw, h / ih)) * zoom
    dw, dh = max(1, int(iw * sc)), max(1, int(ih * sc))
    x, y = (w - dw) / 2 + ox, (h - dh) / 2 + oy
    g.blit(pygame.transform.smoothscale(img, (dw, dh)), (int(x), int(y)))
    return x, y, dw, dh


# ---------------------------------------------------------------- video

class Clip:
    def __init__(self, name, contrast=1.0):
        self.path = os.path.join(IMG_DIR, name)
        self.contrast = contrast
        self.frames = []
        self.fps = 30.0
        self.size = (1, 1)
        self.duration = 0.0
        self.ready = False
        self._idx = -1
        self._surf = None

    def load(self):
        cap = cv2.VideoCapture(self.path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps and fps > 0:
            self.fps = fps
        frames = []
        size = (1, 1)
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if self.contrast != 1.0:
                f = cv2.addWeighted(f, self.contrast, f, 0, 127.5 * (1 - self.contrast))
            size = (f.shape[1], f.shape[0])
            ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                frames.append(buf)
        cap.release()
        self.frames = frames
        self.size = size
        self.duration = len(frames) / self.fps
        self.ready = bool(frames)

    def index(self, t):
        return clamp(int(t * self.fps), 0, len(self.frames) - 1)

    def rgb(self, t):
        bgr = cv2.imdecode(self.frames[self.index(t)], cv2.IMREAD_COLOR)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def frame(self, t):
        i = self.index(t)
        if i != self._idx:
            self._surf = rgb_surface(self.rgb(t))
            self._idx = i
        return self._surf


class StreamVideo:
    def __init__(self, name):
        self.cap = cv2.VideoCapture(os.path.join(IMG_DIR, name))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 24.0
        self.n = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.ok = self.cap.isOpened() and self.n > 0
        self.idx = -1
        self.surf = None

    def frame(self, t):
        if not self.ok:
            return None
        i = int(t * self.fps) % self.n
        if i != self.idx:
            if i < self.idx:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.idx = -1
            f = None
            while self.idx < i:
                ok, f = self.cap.read()
                if not ok:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self.idx = -1
                    f = None
                    break
                self.idx += 1
            if f is not None:
                self.surf = rgb_surface(np.ascontiguousarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)[:, ::-1]))
        return self.surf


class Player:
    def __init__(self, clip, loop=False):
        self.clip = clip
        self.loop = loop
        self.t = 0.0
        self.rate = 1.0
        self.playing = False
        self.ended = False
        PLAYERS.append(self)

    def play(self):
        self.playing = True
        self.ended = False

    def pause(self):
        self.playing = False

    def seek(self, t):
        self.t = t
        self.ended = False

    def update(self, dt):
        if not self.playing or not self.clip.ready:
            return
        self.t += dt * self.rate
        d = self.clip.duration
        if self.t >= d:
            if self.loop:
                self.t %= d
            else:
                self.t = d
                self.playing = False
                self.ended = True

    def frame(self):
        return self.clip.frame(self.t)


PLAYERS = []

_screen_cache = {}


def draw_screen_video(surf, player, rect, inner=None, opacity=1.0):
    if not player.clip.ready:
        return
    x, y, w, h = [int(v) for v in rect]
    if w < 2 or h < 2:
        return
    key = (id(player), player.clip.index(player.t), w, h, inner, opacity)
    img = _screen_cache.get(key)
    if img is None:
        img = pygame.transform.smoothscale(player.frame(), (w, h))
        if inner is not None or opacity != 1.0:
            img.blit(mult_mask_surface(w, h, inner if inner is not None else 2.0, opacity), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        if len(_screen_cache) > 24:
            _screen_cache.clear()
        _screen_cache[key] = img
    surf.blit(img, (x, y), special_flags=pygame.BLEND_RGB_ADD)


# ---------------------------------------------------------------- audio

def to_sound(stereo):
    arr = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    return pygame.sndarray.make_sound(np.ascontiguousarray(arr))


def mono_sound(mono):
    return to_sound(np.stack([mono, mono], axis=1))


def sound_array(snd):
    return pygame.sndarray.array(snd).astype(np.float32) / 32768.0


def load_sound(name, gain=1.0):
    path = os.path.join(AUDIO_DIR, name)
    try:
        snd = pygame.mixer.Sound(path)
    except (pygame.error, FileNotFoundError):
        return None
    if gain == 1.0:
        return snd
    return to_sound(np.tanh(sound_array(snd) * gain))


def resample(arr, rate):
    n = len(arr)
    m = max(1, int(n / rate))
    idx = np.arange(m) * rate
    src = np.arange(n)
    return np.stack([np.interp(idx, src, arr[:, c]) for c in range(arr.shape[1])], axis=1)


def fft_filter(x, kind, f0, q=0.707):
    n = len(x)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    r = np.maximum(f, 1e-6) / f0
    if kind == "lowpass":
        h = 1 / np.sqrt((1 - r ** 2) ** 2 + (r / q) ** 2)
    elif kind == "highpass":
        h = r ** 2 / np.sqrt((1 - r ** 2) ** 2 + (r / q) ** 2)
    else:
        h = (r / q) / np.sqrt((1 - r ** 2) ** 2 + (r / q) ** 2)
    return np.fft.irfft(spec * h, n)


def env_curve(n_total, t0, peak, a, hold, rel):
    t = np.arange(n_total) / SR - t0
    out = np.zeros(n_total, dtype=np.float64)
    m = (t >= 0) & (t < a)
    out[m] = 0.0001 + (peak - 0.0001) * t[m] / a
    m = (t >= a) & (t < a + hold)
    out[m] = peak
    m = t >= a + hold
    tr = t[m] - a - hold
    out[m] = peak * (0.0001 / peak) ** np.clip(tr / rel, 0, 1)
    out[t >= a + hold + rel] = 0
    return out


class Param:
    def __init__(self, v=0.0):
        self.v = v
        self.mode = None

    def value(self):
        if not self.mode:
            return self.v
        t = now_ms() / 1000
        if self.mode[0] == "lin":
            _, a, b, t0, secs = self.mode
            k = clamp((t - t0) / secs, 0, 1) if secs > 0 else 1
            return a + (b - a) * k
        _, target, tau, t0, v0 = self.mode
        return target + (v0 - target) * math.exp(-(t - t0) / tau)

    def set(self, v):
        self.v = v
        self.mode = None

    def ramp(self, target, secs):
        self.mode = ("lin", self.value(), target, now_ms() / 1000, secs)

    def toward(self, target, tau):
        self.mode = ("exp", target, tau, now_ms() / 1000, self.value())


class Voice:
    def __init__(self, sound, gain=1.0, loops=0):
        self.sound = sound
        self.gain = Param(gain)
        self.stop_at = None
        self.pan = None
        self.ch = sound.play(loops=loops) if sound else None
        self.apply()
        VOICES.append(self)

    def alive(self):
        return self.ch is not None and self.ch.get_sound() is self.sound

    def apply(self):
        if self.alive():
            v = clamp(self.gain.value() * MASTER, 0, 1)
            if self.pan:
                self.ch.set_volume(v * self.pan[0], v * self.pan[1])
            else:
                self.ch.set_volume(v)

    def fade_out(self, tau, stop_after):
        self.gain.toward(0, tau)
        self.stop_at = now_ms() + stop_after * 1000

    def stop(self):
        if self.alive():
            self.ch.stop()
        self.ch = None


VOICES = []


def update_voices():
    t = now_ms()
    for v in VOICES[:]:
        if v.stop_at is not None and t >= v.stop_at:
            v.stop()
        if not v.alive():
            VOICES.remove(v)
        else:
            v.apply()


def play(sound, gain=1.0):
    if sound is None:
        return None
    return Voice(sound, gain)


DRONE_LEVEL = 0.15


class Drone:
    def __init__(self):
        self.out = Param(0)
        self.hg = Param(0)
        self.stop_at = None
        self.synth = Voice(A.drone_synth, 0, loops=-1)
        self.out.ramp(0.025, 2)
        self.heart = Voice(A.heart, 0, loops=-1) if A.heart else None
        self.amb = Voice(A.ambient, 0, loops=-1) if A.heart and A.ambient else None
        if self.heart:
            self.hg.ramp(0.22, 1.5)

    def update(self):
        self.synth.gain.set(self.out.value() * A.drone_scale * DRONE_LEVEL)
        if self.heart:
            self.heart.gain.set(self.hg.value() * DRONE_LEVEL)
        if self.amb:
            self.amb.gain.set(self.hg.value() * 0.6 * DRONE_LEVEL)
        if self.stop_at is not None and now_ms() >= self.stop_at:
            for v in (self.synth, self.heart, self.amb):
                if v:
                    v.stop()
            return False
        return True

    def stop(self):
        self.out.toward(0, 0.3)
        self.hg.toward(0, 0.3)
        self.stop_at = now_ms() + 1500


class AudioState:
    def __init__(self):
        self.drone = None
        self.dying = []

    def start_drone(self):
        if self.drone:
            return
        self.drone = Drone()

    def stop_drone(self):
        if not self.drone:
            return
        self.drone.stop()
        self.dying.append(self.drone)
        self.drone = None

    def update(self):
        if self.drone:
            self.drone.update()
        self.dying = [d for d in self.dying if d.update()]
        update_voices()


def make_drone_synth():
    n = 5 * SR
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * 47 * t) + np.sin(2 * np.pi * 47.6 * t)
    noise = fft_filter(np.random.uniform(-1, 1, n), "lowpass", 300)
    data = s + 0.25 * noise
    peak = float(np.abs(data).max())
    return mono_sound(data / peak), peak


def play_narration(sound):
    if sound is None:
        return None
    v = play(sound)
    d = AU.drone
    if d:
        d.out.toward(0.01, 0.4)
        d.hg.toward(0.08, 0.4)
    return v


def stop_narration(v):
    if v:
        v.fade_out(0.1, 0.5)
    d = AU.drone
    if d:
        d.out.toward(0.025, 0.6)
        d.hg.toward(0.22, 0.6)


def play_scream():
    play(A.scream)


def tone_phase(freqs):
    return 2 * np.pi * np.cumsum(freqs) / SR


def exp_slide(n, f0, f1, secs, start=0.0):
    t = np.arange(n) / SR - start
    k = np.clip(t / secs, 0, 1)
    return f0 * (f1 / f0) ** k


def play_synth(mono, soft=False):
    if soft:
        mono = np.tanh(mono)
    return play(mono_sound(mono))


def cue_knock(v):
    n = int(0.75 * SR)
    out = np.zeros(n)
    for i in range(3):
        tt = i * 0.2
        f = exp_slide(n, 110, 40, 0.15, tt)
        out += np.sin(tone_phase(f)) * env_curve(n, tt, 0.9 * v, 0.004, 0.02, 0.18)
        noise = fft_filter(np.random.uniform(-1, 1, n), "lowpass", 900)
        out += noise * env_curve(n, tt, 0.4 * v, 0.002, 0.01, 0.06)
    play_synth(out, True)


def cue_whisper(v):
    pts = [(0.0, 0.0001)]
    tt = 0.0
    for _ in range(5):
        pts.append((tt + 0.06, rand(0.5, 0.9) * v))
        pts.append((tt + rand(0.14, 0.22), 0.05 * v))
        tt += rand(0.16, 0.26)
    pts.append((tt + 0.1, 0.0001))
    n = int((tt + 0.2) * SR)
    t = np.arange(n) / SR
    g = np.interp(t, [p[0] for p in pts], [p[1] for p in pts])
    noise = fft_filter(np.random.uniform(-1, 1, n), "bandpass", rand(2000, 3400), 1.4)
    play_synth(noise * g, True)


def camera_flash():
    n = int(0.35 * SR)
    hp = fft_filter(np.random.uniform(-1, 1, n), "highpass", 2500)
    out = hp * env_curve(n, 0, 0.7, 0.002, 0.03, 0.2)
    f = exp_slide(n, 180, 50, 0.12)
    out += np.sin(tone_phase(f)) * env_curve(n, 0, 0.6, 0.002, 0.02, 0.15)
    play_synth(out, True)


def tv_click():
    n = int(0.12 * SR)
    lp = fft_filter(np.random.uniform(-1, 1, n), "lowpass", 3000)
    play_synth(lp * env_curve(n, 0, 0.35, 0.001, 0.01, 0.08), True)


def make_music_box():
    notes = [76, 79, 84, 79, 81, 77, 74, 77, 76, 72, 74, 71, 72, 76, 79, 0]
    step = 0.38
    n = int(len(notes) * step * SR)
    out = np.zeros(n)
    t = np.arange(int(1.6 * SR)) / SR
    for i, m in enumerate(notes):
        if not m:
            continue
        f = 440 * 2 ** ((m - 69) / 12)
        tone = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 2.01 * t)) * np.exp(-t * 3.2)
        a = int(i * step * SR)
        seg = tone[:n - a]
        out[a:a + len(seg)] += seg
    out += np.roll(out, int(0.11 * SR)) * 0.25
    return mono_sound(out / np.abs(out).max() * 0.9)


def slam_hit():
    n = int(0.8 * SR)
    f = exp_slide(n, 95, 32, 0.45)
    out = np.sin(tone_phase(f)) * env_curve(n, 0, 1.6, 0.003, 0.05, 0.5)
    lp = fft_filter(np.random.uniform(-1, 1, n), "lowpass", 2200)
    out += lp * env_curve(n, 0, 1.2, 0.002, 0.03, 0.25)
    bp = fft_filter(np.random.uniform(-1, 1, n), "bandpass", 3200, 6)
    out += bp * env_curve(n, 0.02, 0.5, 0.005, 0.1, 0.45)
    play_synth(out, True)


# ---------------------------------------------------------------- assets

class Assets:
    progress = 0.0
    error = None

    def load(self):
        threading.Thread(target=self._load_all, daemon=True).start()

    def _load_all(self):
        try:
            self._load_base()
            self.progress = 0.15
            for i, c in enumerate(self.clips):
                c.load()
                self.progress = 0.15 + 0.85 * (i + 1) / len(self.clips)
            self.clips_done = True
        except Exception as e:
            self.error = e

    def load_bike(self):
        snd = load_sound("bike.wav")
        if snd is None:
            self.bike = None
            return
        a = sound_array(snd) * 2.0
        nx = int(0.3 * SR)
        if len(a) > 3 * nx:
            k = np.linspace(0, 1, nx)[:, None]
            head = a[:nx] * k + a[-nx:] * (1 - k)
            a = np.concatenate([head, a[nx:-nx]])
        self.bike = to_sound(np.tanh(a))

    def load_drone(self):
        self.drone_synth, self.drone_scale = make_drone_synth()
        self.heart = load_sound("heartbeat.wav")
        self.ambient = load_sound("ambient.wav")

    def _load_base(self):
        self.scream = load_sound("scream.mp3", 1.8)
        if DEMO:
            self._load_runsteps()
            self.faces = []
            self.intro = Clip("intro.mp4", 1.1)
            self.scare = Clip("scare.mp4")
            self.walker = Clip("walker.mp4", 1.15)
            self.turn = Clip("turn.mp4")
            self.clips = [self.intro, self.scare, self.walker, self.turn]
            return
        self.eye = css_filter(read_rgb("eye.jpg"), ("gray", 1), ("contrast", 1.2), ("bright", 0.9))
        self.flash = [css_filter(read_rgb(f"f{i}.jpg"), ("contrast", 1.2), ("bright", 0.95)) for i in (18, 19, 20)]
        self.xray = css_filter(read_rgb("xray.jpg"), ("contrast", 1.2))
        f19 = read_rgb("f19.jpg")
        fam = read_optional("family.jpg")
        flash_src = fam if fam is not None else f19
        self.peek = {"flash": (css_filter(flash_src, ("gray", 1), ("bright", 0.95), ("contrast", 1.4)), 0.9, 1.05),
                     "p3": (css_filter(f19, ("gray", 1), ("bright", 0.9), ("contrast", 1.3)), 1.0, 1.18)}
        self.narr = [load_sound("story1.wav"), load_sound("story2.wav")]
        self.music_box = make_music_box()
        self.room_empty = read_optional("room_empty.jpg")
        self.tv_set = read_optional("tv_set.jpg")
        self._load_runsteps()
        self.faces = []
        for i in (6, 7, 8, 9, 10):
            try:
                self.faces.append(rgb_surface(read_rgb(f"s{i}.jpg")))
            except cv2.error:
                pass
        self.intro = Clip("intro.mp4", 1.1)
        self.scare = Clip("scare.mp4")
        self.walker = Clip("walker.mp4", 1.15)
        self.turn = Clip("turn.mp4")
        self.tv_shows = sorted(os.path.splitext(f)[0] for f in os.listdir(IMG_DIR) if f.startswith("tv_show") and f.endswith(".mp4"))
        names = ["room_left", "room_right", "room_closet", "tv_wrong", "tv_empty"] + self.tv_shows
        self.glass = {k: Clip(f"{k}.mp4") for k in names}
        self.clips = [self.intro, self.scare, self.walker, self.turn] + list(self.glass.values())

    def _load_runsteps(self):
        rs = load_sound("runstep.mp3")
        self.runstep_walk = []
        self.runstep_clown = []
        if rs is not None:
            arr = sound_array(rs)
            self.runstep_walk = [to_sound(np.tanh(resample(arr, r) * 2.5)) for r in np.linspace(0.92, 1.08, 7)]
            self.runstep_clown = [to_sound(np.tanh(resample(arr, r) * 1.4)) for r in np.linspace(0.85, 0.95, 5)]

    clips_done = False

    def ready(self):
        return self.clips_done


A = Assets()
AU = AudioState()


# ---------------------------------------------------------------- fonts / text

_fonts = {}


def font(kind, px):
    px = max(6, int(round(px)))
    key = (kind, px)
    f = _fonts.get(key)
    if f is None:
        name = "VT323-Regular.ttf" if kind == "display" else "IBMPlexMono-Regular.ttf"
        try:
            f = pygame.font.Font(os.path.join(FONT_DIR, name), px)
        except (pygame.error, FileNotFoundError):
            f = pygame.font.SysFont("couriernew,courier,monospace", px)
        _fonts[key] = f
    return f


def text_width(kind, px, text, ls):
    return font(kind, px).size(text)[0] + ls * px * len(text)


def render_spaced(kind, px, text, color, ls):
    f = font(kind, px)
    if not ls:
        return f.render(text or " ", True, color)
    sp = ls * px
    widths = [f.size(c)[0] for c in text]
    total = max(1, int(math.ceil(sum(widths) + sp * len(text))))
    surf = pygame.Surface((total, f.get_height()), pygame.SRCALPHA)
    x = 0.0
    for c, w in zip(text, widths):
        if c != " ":
            surf.blit(f.render(c, True, color), (int(round(x)), 0))
        x += w + sp
    return surf


def blur_surface(s, r):
    w, h = s.get_size()
    k = max(1.0, r / 2.0)
    sw, sh = max(1, int(w / k)), max(1, int(h / k))
    return pygame.transform.smoothscale(pygame.transform.smoothscale(s, (sw, sh)), (w, h))


_text_cache = {}


def text_image(kind, px, text, color, ls=0.0, eff=None):
    px = int(round(px))
    key = (kind, px, text, color, ls, eff)
    hit = _text_cache.get(key)
    if hit:
        return hit
    base = render_spaced(kind, px, text, color, ls)
    if not eff:
        res = (base, 0)
    else:
        pad = 14
        w, h = base.get_size()
        out = pygame.Surface((w + 2 * pad, h + 2 * pad), pygame.SRCALPHA)
        if eff[0] == "glow":
            _, gc, a, r = eff
            layer = pygame.Surface(out.get_size(), pygame.SRCALPHA)
            layer.blit(render_spaced(kind, px, text, gc, ls), (pad, pad))
            layer = blur_surface(layer, r)
            layer.fill((255, 255, 255, int(a * 255)), special_flags=pygame.BLEND_RGBA_MULT)
            out.blit(layer, (0, 0))
        else:
            sil = pygame.Surface(out.get_size(), pygame.SRCALPHA)
            sil.blit(render_spaced(kind, px, text, (0, 0, 0), ls), (pad, pad))
            for r in eff[1]:
                out.blit(blur_surface(sil, r), (0, 0))
        out.blit(base, (pad, pad))
        res = (out, pad)
    if len(_text_cache) > 3000:
        _text_cache.clear()
    _text_cache[key] = res
    return res


def wrap(kind, px, text, ls, maxw):
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        t = w if not cur else cur + " " + w
        if not cur or text_width(kind, px, t, ls) <= maxw:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def balanced(kind, px, text, ls, maxw):
    lines = wrap(kind, px, text, ls, maxw)
    if len(lines) <= 1:
        return lines
    lo, hi = 1, int(maxw)
    while lo < hi:
        mid = (lo + hi) // 2
        if len(wrap(kind, px, text, ls, mid)) <= len(lines):
            hi = mid
        else:
            lo = mid + 1
    return wrap(kind, px, text, ls, lo)


SHADOW = ("shadow", (12, 6, 2))


def vmin():
    return min(R.win_w, R.win_h) / 100


def style(name):
    v = vmin()
    if name == "h1":
        return dict(kind="display", px=clamp(9 * v, 36, 96), ls=0.12, lh=1.0, maxch=None, color=PHOSPHOR, eff=("glow", PHOSPHOR, 0.35, 12), balance=True)
    if name == "h2":
        return dict(kind="display", px=clamp(6 * v, 26, 64), ls=0.08, lh=None, maxch=None, color=PHOSPHOR, eff=("glow", PHOSPHOR, 0.3, 10), balance=True)
    if name == "p":
        return dict(kind="body", px=clamp(1.9 * v, 11, 16), ls=0.0, lh=1.55, maxch=60, color=PHOSPHOR, eff=None, balance=False)
    if name == "warn":
        return dict(kind="display", px=clamp(2.6 * v, 14, 24), ls=0.08, lh=1.55, maxch=60, color=BLOOD, eff=None, balance=False)
    if name == "story":
        return dict(kind="display", px=clamp(3.1 * v, 15, 32), ls=0.05, lh=1.2, maxch=56, color=PHOSPHOR, eff=SHADOW, balance=False)
    if name == "label":
        return dict(kind="body", px=16, ls=0.0, lh=None, maxch=None, color=DIM, eff=None, balance=False)
    raise KeyError(name)


# ---------------------------------------------------------------- panel

class Block:
    def __init__(self, w, h, draw, glitch, gap=None):
        self.w = w
        self.h = h
        self.draw = draw
        self.glitch = glitch
        self.gap = gap


class Text:
    def __init__(self, text, kind="p", color=None, eff="default", glitch=True):
        self.text = text
        self.kind = kind
        self.color = color
        self.eff = eff
        self.glitchable = glitch
        self.gap = None

    def lines(self, maxw):
        s = style(self.kind)
        f = font(s["kind"], s["px"])
        if s["maxch"]:
            maxw = min(maxw, s["maxch"] * f.size("0")[0])
        fn = balanced if s["balance"] else wrap
        return s, f, fn(s["kind"], s["px"], self.text, s["ls"], maxw)

    def blocks(self, maxw):
        s, f, lines = self.lines(maxw)
        color = self.color or s["color"]
        eff = s["eff"] if self.eff == "default" else self.eff
        lh = int(s["lh"] * s["px"]) if s["lh"] else f.get_linesize()
        imgs = [text_image(s["kind"], s["px"], ln, color, s["ls"], eff) for ln in lines]
        widths = [text_width(s["kind"], s["px"], ln, s["ls"]) for ln in lines]
        bw = max(widths) if widths else 0

        def draw(surf, x, y, bright):
            for i, (img, pad) in enumerate(imgs):
                if bright:
                    img, pad = text_image(s["kind"], s["px"], lines[i], brighten(color), s["ls"], eff)
                lx = x + (bw - widths[i]) / 2
                ly = y + i * lh + (lh - f.get_height()) / 2
                surf.blit(img, (int(lx - pad), int(ly - pad)))
        return [Block(bw, lh * len(lines), draw, self.glitchable, self.gap)]


class OList:
    def __init__(self, items):
        self.items = items

    def blocks(self, maxw):
        s = style("p")
        px = s["px"]
        f = font("body", px)
        indent = 1.6 * px
        lh = int(1.6 * px)
        tw = maxw - indent
        wrapped = [wrap("body", px, it, 0, tw) for it in self.items]
        bw = indent + max(f.size(ln)[0] for ls in wrapped for ln in ls)
        out = []
        for n, lines in enumerate(wrapped, 1):
            def draw(surf, x, y, bright, lines=lines, n=n):
                c = brighten(PHOSPHOR) if bright else PHOSPHOR
                mk = f.render(f"{n}.", True, c)
                surf.blit(mk, (int(x + indent - mk.get_width() - 0.35 * px), int(y + (lh - f.get_height()) / 2)))
                for i, ln in enumerate(lines):
                    surf.blit(f.render(ln, True, c), (int(x + indent), int(y + i * lh + (lh - f.get_height()) / 2)))
            out.append(Block(bw, lh * len(lines), draw, True, 0 if n > 1 else None))
        return out


class Button:
    def __init__(self, label, cb=None, small=False):
        self.label = label
        self.cb = cb
        self.small = small
        self.disabled = False

    def metrics(self):
        v = vmin()
        if self.small:
            px = clamp(2.2 * v, 13, 20)
            padx, pady = 0.8 * px, 0.15 * px
        else:
            px = clamp(3.4 * v, 18, 32)
            padx, pady = 1.2 * px, 0.25 * px
        f = font("display", px)
        w = text_width("display", px, self.label, 0.12) + 2 * padx + 2
        h = f.get_height() + 2 * pady + 2
        return px, w, h, padx

    def draw(self, surf, x, y, bright):
        px, w, h, padx = self.metrics()
        rect = pygame.Rect(int(x), int(y), int(w), int(h))
        P.hits.append((rect, self))
        base = DIM if self.small else PHOSPHOR
        hover = not self.disabled and R.mouse_in_crt(rect)
        if self.disabled:
            base = mix(base, GLASS, 0.4)
        if bright:
            base = brighten(base)
        if hover:
            pygame.draw.rect(surf, PHOSPHOR, rect)
            tc = VOID
        else:
            pygame.draw.rect(surf, base, rect, 1)
            tc = base
        img, pad = text_image("display", px, self.label, tc, 0.12)
        surf.blit(img, (int(x + padx + 1), int(y + (h - img.get_height()) / 2)))


class Row:
    def __init__(self, children, glitch=True, hidden=False):
        self.children = children
        self.glitchable = glitch
        self.hidden = hidden

    def blocks(self, maxw):
        if self.hidden:
            return []
        gap = 16
        sizes = []
        for c in self.children:
            if isinstance(c, Button):
                _, w, h, _ = c.metrics()
            else:
                s = style(c.kind)
                w = text_width(s["kind"], s["px"], c.text, s["ls"])
                h = font(s["kind"], s["px"]).get_height()
            sizes.append((w, h))
        rows, cur, cw = [], [], 0
        for c, (w, h) in zip(self.children, sizes):
            need = w if not cur else cw + gap + w
            if cur and need > maxw:
                rows.append(cur)
                cur, cw = [], 0
                need = w
            cur.append((c, w, h))
            cw = need
        if cur:
            rows.append(cur)
        rws = [sum(w for _, w, _ in r) + gap * (len(r) - 1) for r in rows]
        rhs = [max(h for _, _, h in r) for r in rows]
        bw = max(rws)
        bh = sum(rhs) + gap * (len(rows) - 1)

        def draw(surf, x, y, bright):
            yy = y
            for r, rw, rh in zip(rows, rws, rhs):
                xx = x + (bw - rw) / 2
                for c, w, h in r:
                    cy = yy + (rh - h) / 2
                    if isinstance(c, Button):
                        c.draw(surf, xx, cy, bright)
                    else:
                        s = style(c.kind)
                        img, pad = text_image(s["kind"], s["px"], c.text, c.color or s["color"], s["ls"])
                        surf.blit(img, (int(xx), int(cy)))
                    xx += w + gap
                yy += rh + gap
        return [Block(bw, bh, draw, self.glitchable)]


DART = [(0.0, 0, 0), (0.18, 0, 0), (0.21, -0.025, 0.01), (0.40, -0.025, 0.01), (0.43, 0.02, -0.01), (0.62, 0.02, -0.01),
        (0.65, -0.01, -0.015), (0.82, -0.01, -0.015), (0.85, 0, 0), (1.0, 0, 0)]


def cubic_bezier(x1, y1, x2, y2, x):
    lo, hi = 0.0, 1.0
    for _ in range(30):
        t = (lo + hi) / 2
        bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3
        if bx < x:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3


def keyframes(frames, k):
    for i in range(len(frames) - 1):
        a, b = frames[i], frames[i + 1]
        if k <= b[0]:
            u = 0 if b[0] == a[0] else ease_in_out((k - a[0]) / (b[0] - a[0]))
            return tuple(a[j] + (b[j] - a[j]) * u for j in range(1, len(a)))
    return frames[-1][1:]


class Eye:
    def __init__(self):
        self.t0 = now_ms()
        self.gap = None

    def blocks(self, maxw):
        w = int(min(maxw * 0.5, 480))
        h = int(w * 9 / 16)

        def draw(surf, x, y, bright):
            t = (now_ms() - self.t0) / 1000
            fade = 1 - (1 - clamp(t / 1.2, 0, 1)) ** 2
            tx, ty = keyframes(DART, (t % 5) / 5)
            sy = 1.0
            if t > 1.4:
                sy = keyframes([(0, 1.0), (0.9, 1.0), (0.94, 0.03), (1.0, 1.0)], ((t - 1.4) % 4.6) / 4.6)[0]
            iw, ih = int(w * 1.06), int(h * 1.06)
            ox = (w - iw) / 2 + tx * w * 1.06
            oy = (h - ih) / 2 + ty * h * 1.06
            img = cv2.resize(A.eye, (iw, ih), interpolation=cv2.INTER_AREA)
            canvas = np.zeros((h, w, 3), np.uint8)
            x0, y0 = int(round(ox)), int(round(oy))
            sx0, sy0 = max(0, -x0), max(0, -y0)
            dx0, dy0 = max(0, x0), max(0, y0)
            cw = min(w - dx0, iw - sx0)
            ch = min(h - dy0, ih - sy0)
            canvas[dy0:dy0 + ch, dx0:dx0 + cw] = img[sy0:sy0 + ch, sx0:sx0 + cw]
            s = rgba_surface(canvas, ellipse_mask(w, h, 0.62) * fade)
            bh = max(1, int(h * sy))
            if bh != h:
                s = pygame.transform.smoothscale(s, (w, bh))
            surf.blit(s, (int(x), int(y + (h - bh) / 2)))
        return [Block(w, h + 18, draw, False, self.gap)]


class StoryBlock:
    def __init__(self, lines, last=None):
        self.lines = lines
        self.last = last

    def blocks(self, maxw):
        avail = P.avail_h
        base = style("story")["px"]
        px = base
        while True:
            out = self._blocks(maxw, px)
            total = sum(b.h for b in out) + sum(b.gap or 0 for b in out[1:])
            if total <= avail - 60 or px <= 11:
                return out
            px -= 1

    def _blocks(self, maxw, px):
        out = []
        f = font("display", px)
        maxw = min(maxw, 56 * f.size("0")[0])
        lh = int(1.2 * px)
        items = [(ln, PHOSPHOR, SHADOW, 8.8) for ln in self.lines]
        if self.last:
            items.append((self.last, BLOOD, ("glow", BLOOD, 0.6, 10), 8.8 + 0.4 * px))
        for i, (text, color, eff, gap) in enumerate(items):
            lines = wrap("display", px, text, 0.05, maxw)
            imgs = [text_image("display", px, ln, color, 0.05, eff) for ln in lines]
            widths = [text_width("display", px, ln, 0.05) for ln in lines]
            bw = max(widths)

            def draw(surf, x, y, bright, lines=lines, imgs=imgs, widths=widths, bw=bw, color=color, eff=eff):
                for j, (img, pad) in enumerate(imgs):
                    if bright:
                        img, pad = text_image("display", px, lines[j], brighten(color), 0.05, eff)
                    surf.blit(img, (int(x + (bw - widths[j]) / 2 - pad), int(y + j * lh + (lh - f.get_height()) / 2 - pad)))
            out.append(Block(bw, lh * len(lines), draw, True, None if i == 0 else gap))
        return out


class Wall:
    def draw_abs(self, surf):
        W, H = surf.get_size()
        px = clamp(3.4 * vmin(), 16, 34)
        img, pad = text_image("display", px, "YOU LET HIM IN", BLOOD, 0.06, ("glow", BLOOD, 0.6, 8))
        w = text_width("display", px, "YOU LET HIM IN", 0.06)
        lh = font("display", px).get_linesize()
        layer = pygame.Surface((W, H), pygame.SRCALPHA)
        padx = 0.03 * W
        x, y, n = padx, padx, 0
        while n < 160 and y < H:
            if x + w > W - padx and x > padx:
                x = padx
                y += lh + 0.2 * px
                continue
            layer.blit(img, (int(x - pad), int(y - pad)))
            x += w + 0.9 * px
            n += 1
        if (now_ms() // 60) % 2:
            layer.fill((255, 255, 255, 204), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(layer, (0, 0))


class Panel:
    avail_h = 600

    def __init__(self):
        self.elems = []
        self.visible = False
        self.side = False
        self.hits = []
        self.glitch = None
        self.next_glitch = 2500

    def show(self, elems):
        self.elems = elems
        self.visible = True

    def hide(self):
        self.visible = False
        self.elems = []
        self.hits = []

    def update(self):
        t = now_ms()
        if t >= self.next_glitch:
            if self.visible:
                self.glitch = (random.random(), random.randint(0, 2), round(20 + random.random() * 50), t + 120 + random.random() * 160)
            self.next_glitch = t + 2000 + random.random() * 3000
        if self.glitch and t >= self.glitch[3]:
            self.glitch = None

    def draw(self, surf):
        self.hits = []
        if not self.visible:
            return
        W, H = surf.get_size()
        for e in self.elems:
            if hasattr(e, "draw_abs"):
                e.draw_abs(surf)
        if self.side:
            pl, pr = 0.04 * W, 0.30 * W
        else:
            pl = pr = 0.08 * W
        pt = pb = 0.06 * W
        self.avail_h = H - pt - pb
        cw = W - pl - pr
        blocks = []
        for e in self.elems:
            if hasattr(e, "blocks"):
                blocks += e.blocks(cw)
        gap = clamp(2 * vmin(), 8, 18)
        total = 0
        for i, b in enumerate(blocks):
            total += b.h + (0 if i == 0 else (gap if b.gap is None else b.gap))
        y = pt + max(0, (H - pt - pb - total) / 2)
        cx = pl + cw / 2
        eligible = [b for b in blocks if b.glitch]
        chosen = set()
        if self.glitch and eligible:
            start = int(self.glitch[0] * len(eligible))
            chosen = {id(b) for b in eligible[start:start + 1 + self.glitch[1]]}
        for i, b in enumerate(blocks):
            if i:
                y += gap if b.gap is None else b.gap
            on = id(b) in chosen
            b.draw(surf, cx - b.w / 2 + (self.glitch[2] if on else 0), y, on)
            y += b.h

    def click(self, pos):
        for rect, btn in reversed(self.hits):
            if rect.collidepoint(pos):
                if not btn.disabled and btn.cb:
                    btn.cb()
                return True
        return False


P = Panel()


# ---------------------------------------------------------------- CRT renderer

class Renderer:
    def __init__(self):
        info = pygame.display.Info()
        sw, sh = info.current_w or 1366, info.current_h or 768
        self.screen = pygame.display.set_mode((sw, sh), pygame.NOFRAME)
        self.shake_dir = 1
        self.shake_boost = 1.0
        self.shake_x = 0.0
        self.next_shake = 0
        self.roll_idx = 0
        self.next_roll = 0
        self.roll_jit = 0
        self.hit_until = 0
        self.build()

    def build(self):
        self.win_w, self.win_h = self.screen.get_size()
        W = max(64, min(self.win_w - 32, int((self.win_h - 32) * 16 / 9)))
        H = max(36, int(W * 9 / 16))
        self.W, self.H = W, H
        self.cx = (self.win_w - W) // 2
        self.cy = (self.win_h - H) // 2
        self.crt = pygame.Surface((W, H)).convert()
        self.noise = self.make_noise(W, H)
        self.scare_noise = self.make_noise(self.win_w, self.win_h)
        self.scare_surf = pygame.Surface((self.win_w, self.win_h)).convert()
        self.scan = self.make_scan(W, H)
        self.vignette = self.make_vignette(W, H)
        self.rolls = self.make_roll(W, H)
        self.frames = (self.make_frame(W, H, BEZEL), self.make_frame(W, H, BLOOD))
        self.bg = self.make_bg()
        _screen_cache.clear()

    def make_noise(self, w, h):
        out = []
        for _ in range(6):
            arr = np.random.randint(0, 256, (124, 220), dtype=np.uint8)
            s = rgb_surface(np.repeat(arr[..., None], 3, axis=2))
            s = pygame.transform.scale(s, (w, h)).convert()
            out.append(s)
        return out

    def make_scan(self, W, H):
        a = np.zeros((H, W), np.float32)
        a[0::3, :] = 0.28
        return rgba_surface(np.zeros((H, W, 3), np.uint8), a).convert_alpha()

    def make_vignette(self, W, H):
        y = (np.arange(H) + 0.5) / H * 2 - 1
        x = (np.arange(W) + 0.5) / W * 2 - 1
        r = np.sqrt(x[None, :] ** 2 + y[:, None] ** 2) / math.sqrt(2)
        v = np.clip((r - 0.55) / 0.45, 0, 1) * 0.85
        xs = np.arange(W) + 0.5
        ys = np.arange(H) + 0.5
        d = np.minimum(np.minimum(xs[None, :], W - xs[None, :]), np.minimum(ys[:, None], H - ys[:, None]))
        ins = 0.9 * 0.5 * np.clip(1 - d / 80, 0, 1) ** 2
        a = 1 - (1 - v) * (1 - ins)
        return rgba_surface(np.zeros((H, W, 3), np.uint8), a).convert_alpha()

    def make_roll(self, W, H):
        bh = max(4, int(H * 0.22))
        k = (np.arange(bh) + 0.5) / bh

        def interp(stops):
            xs = [s[0] for s in stops]
            return np.interp(k, xs, [s[1] for s in stops])
        ga = interp([(0, 0), (0.55, 0.05), (0.92, 0.16), (0.97, 0.55), (1.0, 0.08)])
        gc = np.where(k[:, None] > 0.94, np.array([240, 242, 232]), np.array(PHOSPHOR))
        grad = gc * ga[:, None]
        nm = interp([(0, 0), (0.5, 0.35), (0.9, 1), (0.97, 1), (1.0, 0)])
        frames = []
        for _ in range(8):
            tile_v = np.random.uniform(160, 255, (70, 220))
            streak = np.where(np.random.random(70) < 0.12, 1.8, 1.0)
            tile_a = np.minimum(255, np.random.random((70, 220)) * 120 * streak[:, None]) / 255
            reps_y = bh // 70 + 1
            reps_x = W // 220 + 2
            nv = np.tile(tile_v, (reps_y, reps_x))[:bh, :W + 8]
            na = np.tile(tile_a, (reps_y, reps_x))[:bh, :W + 8] * nm[:, None]
            comp = nv[..., None] * na[..., None] + grad[:, None, :] * (1 - na[..., None])
            frames.append(rgb_surface(np.clip(comp, 0, 255).astype(np.uint8)).convert())
        return frames

    def make_frame(self, W, H, border):
        s = pygame.Surface((W + 20, H + 20), pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        pygame.draw.rect(s, RING, (10, 10, W, H))
        pygame.draw.rect(s, RING, (0, 0, W + 20, H + 20), border_radius=42)
        pygame.draw.rect(s, (0, 0, 0, 0), (10, 10, W, H), border_radius=32)
        pygame.draw.rect(s, border, (10, 10, W, H), 3, border_radius=32)
        return s.convert_alpha()

    def make_bg(self):
        bg = pygame.Surface((self.win_w, self.win_h)).convert()
        bg.fill(VOID)
        glow = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
        pygame.draw.rect(glow, PHOSPHOR + (16,), (self.cx - 10, self.cy - 10, self.W + 20, self.H + 20), border_radius=42)
        bg.blit(blur_surface(glow, 60), (0, 0))
        return bg

    def mouse_in_crt(self, rect):
        mx, my = pygame.mouse.get_pos()
        return rect.collidepoint(mx - self.cx, my - self.cy)

    def to_crt(self, pos):
        return pos[0] - self.cx, pos[1] - self.cy

    def update(self):
        t = now_ms()
        if S.shake_until and t >= S.shake_until:
            self.shake_boost = 1.0
            S.shake_until = 0
        if t >= self.next_shake:
            self.shake_dir = -self.shake_dir
            self.shake_x = self.shake_dir * (1 + random.random() * 1.2) * self.shake_boost
            self.next_shake = t + 28
        if t >= self.next_roll:
            self.roll_idx = (self.roll_idx + 1) % len(self.rolls)
            self.roll_jit = round(random.random() * 6 - 3)
            self.next_roll = t + 60

    def draw_noise(self, surf, frames, alpha):
        n = random.choice(frames)
        n.set_alpha(int(alpha * 255))
        surf.blit(n, (0, 0))

    def render(self):
        if S.scare:
            self.draw_scare()
            pygame.display.flip()
            return
        c = self.crt
        W, H = self.W, self.H
        c.fill(GLASS)
        self.draw_noise(c, self.noise, 0.07)
        if random.random() < 0.015:
            y, bh = rand(0, H), rand(2, 14)
            band = pygame.Surface((W, int(bh) + 1), pygame.SRCALPHA)
            band.fill(PHOSPHOR + (20,))
            c.blit(band, (0, int(y)))
        if S.blackout:
            c.fill((0, 0, 0))
        if now_ms() < S.flash_until and S.flash_img is not None:
            self.draw_flash(c, S.flash_img)
        self.draw_peek(c)
        self.draw_hand(c)
        if S.walker:
            wf = S.walker_from
            if wf:
                k = clamp((now_ms() - wf["t0"]) / wf["dur"], 0, 1)
                h = H * (wf["s0"] + (1 - wf["s0"]) * k)
                bottom = H * (wf["b0"] + (1 - wf["b0"]) * k)
                w = h * 460 / 720
                draw_screen_video(c, S.walker_player, ((W - w) / 2, bottom - h, w, h))
            else:
                h = H
                w = h * 460 / 720
                draw_screen_video(c, S.walker_player, ((W - w) / 2, 0, w, h))
        if S.rlgl:
            self.draw_rlgl(c)
        if S.glass:
            self.draw_glass(c)
        if S.intro_clown:
            ch = H * 0.82
            cw = ch * 460 / 720
            draw_screen_video(c, S.intro_player, (W - W * 0.03 - cw, H - H * 0.02 - ch, cw, ch), 0.75, 0.85)
        if S.loading:
            self.draw_loading(c)
        P.draw(c)
        c.blit(self.scan, (0, 0))
        c.blit(self.vignette, (0, 0))
        bh = self.rolls[0].get_height()
        k = (now_ms() % 6000) / 6000
        c.blit(self.rolls[self.roll_idx], (self.roll_jit - 4, int(bh * (-1 + 5.6 * k))), special_flags=pygame.BLEND_RGB_ADD)
        scr = self.screen
        scr.blit(self.bg, (0, 0))
        sx = int(round(self.shake_x))
        scr.blit(c, (self.cx + sx, self.cy))
        scr.blit(self.frames[1 if now_ms() < self.hit_until else 0], (self.cx + sx - 10, self.cy - 10))
        pygame.display.flip()

    def draw_flash(self, c, rgb):
        W, H = self.W, self.H
        key = (id(rgb), W, H)
        if self._flash_key != key:
            ih, iw = rgb.shape[:2]
            k = min(W / iw, H / ih) * 1.04
            self._flash_surf = rgb_surface(cv2.resize(rgb, (max(1, int(iw * k)), max(1, int(ih * k))), interpolation=cv2.INTER_AREA))
            self._flash_key = key
        img = self._flash_surf
        layer = pygame.Surface((W, H)).convert()
        layer.fill((0, 0, 0))
        dx = (-1 if random.random() < 0.5 else 1) * rand(2, 4)
        layer.blit(img, (int((W - img.get_width()) / 2 + dx), int((H - img.get_height()) / 2)))
        layer.set_alpha(int(rand(0.75, 1) * 255))
        c.blit(layer, (0, 0))
        self.draw_noise(c, self.noise, 0.22)

    _flash_key = None
    _flash_surf = None
    _hand_cache = {}

    def hand_surface(self, scale, opacity, bright):
        h = int(self.H * 0.96 * scale)
        w = int(h * 514 / 1000)
        key = (w, h, round(opacity, 2), round(bright, 2))
        s = self._hand_cache.get(key)
        if s is None:
            rgb = cv2.resize(A.xray, (max(1, w), max(1, h)), interpolation=cv2.INTER_AREA)
            if bright != 1:
                rgb = cv2.addWeighted(rgb, bright, rgb, 0, 0)
            s = rgba_surface(rgb, ellipse_mask(w, h, 0.65) * opacity).convert_alpha()
            if len(self._hand_cache) > 40:
                self._hand_cache.clear()
            self._hand_cache[key] = s
        return s

    _glass_cache = {}

    def glass_frame(self, clip, t, mode):
        W, H = self.W, self.H
        still = isinstance(clip, np.ndarray)
        key = (id(clip), 0 if still else clip.index(t), W, H, mode)
        surf = self._glass_cache.get(key)
        if surf is None:
            rgb = clip if still else clip.rgb(t)
            ih, iw = rgb.shape[:2]
            k = max(W / iw, H / ih)
            r = cv2.resize(rgb, (int(iw * k) + 1, int(ih * k) + 1), interpolation=cv2.INTER_AREA)
            y0, x0 = (r.shape[0] - H) // 2, (r.shape[1] - W) // 2
            r = r[y0:y0 + H, x0:x0 + W]
            if mode == "set":
                pass
            elif mode == "tv":
                r = cv2.addWeighted(r, 1.1, r, 0, 127.5 * (1 - 1.1))
            else:
                r = np.ascontiguousarray(r[:, ::-1])
                r = cv2.GaussianBlur(r, (0, 0), 1.2)
                gain = {"glass": 0.38, "lit": 0.85}[mode]
                r = cv2.addWeighted(r, gain, r, 0, 0)
            surf = rgb_surface(r).convert()
            if len(self._glass_cache) > 40:
                self._glass_cache.clear()
            self._glass_cache[key] = surf
        return surf

    def tv_screen(self, clip, t):
        W, H = self.W, self.H
        sw = int((TV_SCREEN[2] - TV_SCREEN[0]) * W)
        sh = int((TV_SCREEN[3] - TV_SCREEN[1]) * H)
        key = ("screen", id(clip), clip.index(t), sw, sh)
        surf = self._glass_cache.get(key)
        if surf is None:
            rgb = clip.rgb(t)
            ih, iw = rgb.shape[:2]
            k = max(sw / iw, sh / ih)
            r = cv2.resize(rgb, (int(iw * k) + 1, int(ih * k) + 1), interpolation=cv2.INTER_AREA)
            y0, x0 = (r.shape[0] - sh) // 2, (r.shape[1] - sw) // 2
            r = r[y0:y0 + sh, x0:x0 + sw]
            r = cv2.addWeighted(r, 1.1, r, 0, 127.5 * (1 - 1.1))
            mkey = ("screenmask", sw, sh)
            m = self._glass_cache.get(mkey)
            if m is None:
                yy = (np.arange(sh) + 0.5) / sh * 2 - 1
                xx = (np.arange(sw) + 0.5) / sw * 2 - 1
                d = (np.abs(xx[None, :]) ** 6 + np.abs(yy[:, None]) ** 6) ** (1 / 6)
                m = (np.clip((1.0 - d) / 0.08, 0, 1) * (1 - 0.45 * np.clip(d - 0.55, 0, 1) / 0.45))[..., None].astype(np.float32)
                self._glass_cache[mkey] = m
            surf = rgb_surface((r * m).astype(np.uint8)).convert()
            self._glass_cache[key] = surf
        return surf

    def glare(self):
        key = ("glare", self.W, self.H)
        g = self._glass_cache.get(key)
        if g is None:
            y, x = np.mgrid[0:self.H, 0:self.W]
            a = np.clip(1 - np.abs((x / self.W) + (y / self.H) * 0.6 - 0.55) / 0.12, 0, 1) * 18
            g = rgb_surface(np.repeat(a[..., None], 3, axis=2).astype(np.uint8)).convert()
            self._glass_cache[key] = g
        return g

    def draw_glass(self, c):
        gl = S.glass
        W, H = self.W, self.H
        t = now_ms()
        if gl["mode"] == "intro":
            return
        if gl["tv"]:
            pl = gl["tv_player"]
            if A.tv_set is not None:
                c.blit(self.glass_frame(A.tv_set, 0, "set"), (0, 0))
                c.blit(self.tv_screen(pl.clip, pl.t), (int(TV_SCREEN[0] * W), int(TV_SCREEN[1] * H)), special_flags=pygame.BLEND_RGB_ADD)
            else:
                c.blit(self.glass_frame(pl.clip, pl.t, "tv"), (0, 0))
        else:
            lit = t < gl["flash_until"]
            pl = gl["room_player"]
            src = A.room_empty if gl["clown"] == "away" and A.room_empty is not None else pl.clip
            c.blit(self.glass_frame(src, pl.t, "lit" if lit else "glass"), (0, 0))
            c.blit(self.glare(), (0, 0), special_flags=pygame.BLEND_RGB_ADD)
            if lit:
                k = (gl["flash_until"] - t) / 300
                fx, fy = gl["flash_pos"]
                rad = int(H * 0.55)
                key = ("flash", rad)
                spot = self._glass_cache.get(key)
                if spot is None:
                    m = ellipse_mask(2 * rad, 2 * rad, 0.0) ** 2 * 140
                    spot = rgb_surface(np.repeat(m[..., None], 3, axis=2).astype(np.uint8)).convert()
                    self._glass_cache[key] = spot
                spot.set_alpha(int(255 * clamp(k, 0, 1)))
                c.blit(spot, (int(fx - rad), int(fy - rad)), special_flags=pygame.BLEND_RGB_ADD)
        if t < gl["static_until"]:
            self.draw_noise(c, self.noise, 0.55)
        px = clamp(2.4 * vmin(), 14, 24)
        f = font("display", px)
        pt = clamp(0.03 * W, 10, 28)
        pl_ = clamp(0.04 * W, 14, 40)
        img, pad = text_image("display", px, f"NIGHT {gl['night']}", PHOSPHOR, 0.06, ("shadow", (6,)))
        c.blit(img, (int(pl_ - pad), int(pt - pad)))
        hour = int(gl["t"] / gl["hour_len"])
        img, pad = text_image("display", px, f"{12 if hour == 0 else hour}:00 AM", PHOSPHOR, 0.06, ("shadow", (6,)))
        c.blit(img, (int(W - pl_ - img.get_width() + pad), int(pt - pad)))
        if gl["tv"]:
            hint = "CHARGING THE FLASH    SPACE  TURN OFF THE TV" if gl["charge"] < 1 else "SPACE  TURN OFF THE TV"
        else:
            hint = "CLICK ON HIM TO FLASH    SPACE  TV ON" if gl["charge"] >= 1 else "THE FLASH CHARGES ONLY WHILE THE TV IS ON"
        img, pad = text_image("display", px, hint, DIM, 0.06, ("shadow", (6, 2)))
        fh = f.get_height()
        c.blit(img, (int((W - img.get_width()) / 2), int(H - pt - fh - pad)))
        bw = int(W * 0.24)
        bh = max(12, int(H * 0.028))
        bx, by = int(pl_), int(H - pt - fh - bh - 14)
        img, pad = text_image("display", px, "FLASH", BLOOD, 0.1, ("shadow", (6,)))
        c.blit(img, (bx - pad, int(by - img.get_height() + pad - 2)))
        pygame.draw.rect(c, BLOOD, (bx, by, bw, bh), 2)
        charge = gl["charge"]
        pygame.draw.rect(c, BLOOD, (bx + 4, by + 4, int((bw - 8) * charge), bh - 8))

    def draw_loading(self, c):
        W, H = self.W, self.H
        ld = S.loading
        t = now_ms()
        dt = min(0.05, (t - ld["last"]) / 1000)
        ld["last"] = t
        ld["x"] += (A.progress - ld["x"]) * min(1.0, dt * 2.5)
        img = ld["video"].frame((t - ld["t0"]) / 1000)
        if img is not None:
            h = int(H * 0.62)
            w = int(h * img.get_width() / img.get_height())
            cx = W * 0.05 + ld["x"] * W * 0.9
            c.blit(pygame.transform.smoothscale(img, (w, h)), (int(cx - w / 2), int(H * 0.08)), special_flags=pygame.BLEND_RGB_ADD)
        px = clamp(3 * vmin(), 16, 28)
        txt, pad = text_image("display", px, "LOADING", PHOSPHOR, 0.12, ("glow", PHOSPHOR, 0.3, 8))
        c.blit(txt, (int((W - txt.get_width()) / 2), int(H * 0.76 - pad)))
        bw, bx, by = int(W * 0.5), int(W * 0.25), int(H * 0.76 + px * 1.6)
        bar = pygame.Surface((bw, 4), pygame.SRCALPHA)
        bar.fill(PHOSPHOR + (38,))
        c.blit(bar, (bx, by))
        pygame.draw.rect(c, PHOSPHOR, (bx, by, int(bw * ld["x"]), 4))

    def draw_rlgl(self, c):
        W, H = self.W, self.H
        rg = S.rlgl
        sc = round(rg["scale"], 3)
        h0 = H * 0.96
        w0 = h0 * 540 / 960
        top0 = H * 0.02
        h, w = h0 * sc, w0 * sc
        top = top0 + 0.7 * h0 - 0.7 * h
        draw_screen_video(c, S.turn_player, ((W - w) / 2, top, w, h))
        bx, bw = int(0.06 * W), int(0.88 * W)
        by = int(H - 0.04 * H - 4)
        bar = pygame.Surface((bw, 4), pygame.SRCALPHA)
        bar.fill(PHOSPHOR + (38,))
        c.blit(bar, (bx, by))
        pygame.draw.rect(c, PHOSPHOR, (bx, by, int(bw * rg["progress"]), 4))
        if rg["info"]:
            px = clamp(3 * vmin(), 16, 28)
            img, pad = text_image("display", px, rg["info"], BLOOD if rg["stop"] else PHOSPHOR, 0.1, ("shadow", (6,)))
            fh = font("display", px).get_height()
            c.blit(img, (int((W - img.get_width()) / 2), int(H - 0.07 * H - fh - pad)))

    def draw_peek(self, c):
        if not S.peek:
            return
        rgb, op, scale = A.peek[S.peek]
        h = int(self.H * 0.92 * scale)
        w = int(h * rgb.shape[1] / rgb.shape[0])
        key = ("peek", S.peek, w, h)
        img = self._hand_cache.get(key)
        if img is None:
            img = masked_image(rgb, w, h, 0.70, op).convert_alpha()
            self._hand_cache[key] = img
        c.blit(img, (int((self.W - w) / 2), int((self.H - h) / 2)))

    def draw_hand(self, c):
        hd = S.hand
        if not hd:
            return
        t = (now_ms() - hd["t0"]) / 1000
        if hd["mode"] == "slam" and t < 0.22:
            k = t / 0.22
            if k < 0.5:
                op = cubic_bezier(0.2, 0.9, 0.3, 1.2, k / 0.5)
            else:
                op = 1 + (0.9 - 1) * cubic_bezier(0.2, 0.9, 0.3, 1.2, (k - 0.5) / 0.5)
            e = cubic_bezier(0.2, 0.9, 0.3, 1.2, k)
            scale = 1.45 + (1 - 1.45) * e
            bright = 2 + (1 - 2) * e
            img = self.hand_surface(round(scale, 2), clamp(op, 0, 1), round(bright, 1))
            dx = 0
        else:
            img = self.hand_surface(1, 0.9, 1)
            dx = 0
            if hd["mode"] == "sway":
                st = ((now_ms() - hd["sway0"]) / 1000 % 1.6) / 1.6
                dx = keyframes([(0, 0.0), (0.25, -0.35), (0.75, 0.35), (1.0, 0.0)], st)[0] * img.get_width()
        c.blit(img, (int((self.W - img.get_width()) / 2 + dx), int((self.H - img.get_height()) / 2)))

    def draw_scare(self):
        sc = S.scare
        g = self.scare_surf
        w, h = self.win_w, self.win_h
        p = clamp((now_ms() - sc["start"]) / sc["dur"], 0, 1)
        g.fill((0, 0, 0))
        zoom, ox, oy = 1.1 + p * 0.4, rand(-1, 1) * 30, rand(-1, 1) * 30
        if sc["player"]:
            rgb = A.scare.rgb(sc["player"].t)
            ih, iw = rgb.shape[:2]
            k = min(w / iw, h / ih) * zoom
            dw, dh = max(1, int(iw * k)), max(1, int(ih * k))
            rgb = cv2.resize(rgb, (dw, dh), interpolation=cv2.INTER_LINEAR)
            if random.random() < 0.25:
                rgb = cv2.addWeighted(rgb, 1.8 * 1.3, rgb, 0, 127.5 * (1 - 1.8) * 1.3)
            else:
                rgb = cv2.addWeighted(rgb, 1.25, rgb, 0, 127.5 * (1 - 1.25))
            x, y = (w - dw) / 2 + ox, (h - dh) / 2 + oy
            g.blit(rgb_surface(rgb), (int(x), int(y)))
            feather(g, x, y, dw, dh)
        elif sc["face"] is not None:
            x, y, dw, dh = draw_cover(g, sc["face"], w, h, zoom, ox, oy, True)
            feather(g, x, y, dw, dh)
        if random.random() < 0.3:
            g.fill((179, 6, 12), special_flags=pygame.BLEND_RGB_MULT)
        self.draw_noise(g, self.scare_noise, 0.16)
        if random.random() < 0.15:
            inv = pygame.Surface((w, h)).convert()
            inv.fill((255, 255, 255))
            inv.blit(g, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
            g.blit(inv, (0, 0))
        for _ in range(2):
            if random.random() < 0.45:
                y, bh = int(rand(0, h)), int(rand(10, 70))
                bh = max(1, min(bh, h - y))
                if bh > 0 and y < h:
                    strip = g.subsurface((0, y, w, bh)).copy()
                    g.blit(strip, (int(rand(-50, 50)), y))
        self.screen.blit(g, (0, 0))


_grad = {}


def gradient_strip(n, horizontal):
    key = (n, horizontal)
    s = _grad.get(key)
    if s is None:
        a = np.linspace(1, 0, 256, dtype=np.float32)
        if horizontal:
            alpha = np.tile(a[None, :], (2, 1))
        else:
            alpha = np.tile(a[:, None], (1, 2))
        s = rgba_surface(np.zeros(alpha.shape + (3,), np.uint8), alpha)
        _grad[key] = s
    return s


def feather(g, x, y, dw, dh):
    f = int(dw * 0.3)
    if f > 0:
        left = pygame.transform.smoothscale(gradient_strip(0, True), (f, int(dh)))
        g.blit(left, (int(x), int(y)))
        g.blit(pygame.transform.flip(left, True, False), (int(x + dw - f), int(y)))
    fb = int(dh * 0.22)
    if fb > 0:
        bottom = pygame.transform.smoothscale(gradient_strip(0, False), (int(dw) + 4, fb))
        g.blit(pygame.transform.flip(bottom, False, True), (int(x - 2), int(y + dh - fb)))


# ---------------------------------------------------------------- script runner

class Runner:
    def __init__(self):
        self.gen = None
        self.cond = None

    def start(self, gen, clear_keys=True):
        if clear_keys:
            KEYS.clear()
        self.gen = gen
        self.cond = None
        self.step(None)

    def step(self, val):
        try:
            y = self.gen.send(val)
        except StopIteration:
            self.gen = None
            self.cond = None
            return
        if isinstance(y, (int, float)):
            until = now_ms() + y
            self.cond = lambda: True if now_ms() >= until else None
        else:
            self.cond = y

    def update(self):
        while self.gen is not None and self.cond is not None:
            v = self.cond()
            if v is None:
                break
            self.step(v)


RUN = Runner()
KEYS = []


class State:
    gate = True
    intro_clown = False
    intro_player = None
    scare = None
    game = None
    blackout = False
    flash_img = None
    flash_until = 0
    hand = None
    shake_until = 0
    peek = None
    walker = False
    walker_player = None
    walker_from = None
    run_steps = None
    rlgl = None
    holding = False
    turn_player = None
    glass = None
    loading = None
    menu = False


S = State()


class Game:
    def __init__(self):
        self.fail_msg = None


# ---------------------------------------------------------------- shared sequences

def show_panel(elems):
    P.show(elems)


def hide_panel():
    P.hide()


def type_text(el, text):
    el.text = ""
    for ch in text:
        el.text += ch
        yield 30 if ch == " " else 55


def jumpscare():
    if A.scare.ready:
        pl = S.scare_player
        pl.seek(0)
        pl.play()
        S.scare = dict(start=now_ms(), dur=2600 if A.scream else 1500, player=pl, face=None)
    else:
        S.scare = dict(start=now_ms(), dur=2600 if A.scream else 1500, player=None, face=random.choice(A.faces) if A.faces else None)
    play_scream()
    yield S.scare["dur"]
    if S.scare["player"]:
        S.scare["player"].pause()
    S.scare = None


# ---------------------------------------------------------------- stage 1: questions

QUESTIONS = [
    "Are you alone right now?",
    "Are the lights off?",
    "Is there a door behind you?",
    "Did you lock the front door tonight?",
    "Have you ever felt someone watching you while you sleep?",
    "Do you hear anything right now?",
    "Would you open the door if someone knocked?",
    "Are you sure you are alone?",
]


def wait_answer(row):
    box = {"v": None}

    def done(v):
        if box["v"] is None:
            box["v"] = v
            for b in row.children:
                b.disabled = True

    row.children[0].cb = lambda: done(True)
    row.children[1].cb = lambda: done(False)

    def key(k):
        if k == pygame.K_y:
            done(True)
        elif k == pygame.K_n:
            done(False)

    KEYS.append(key)
    v = yield (lambda: box["v"])
    if key in KEYS:
        KEYS.remove(key)
    return v


def ask_question(text):
    q = Text("", "h2", eff=SHADOW)
    row = Row([Button("YES"), Button("NO")], hidden=True)
    show_panel([Eye(), q, row])
    yield from type_text(q, text)
    row.hidden = False
    return (yield from wait_answer(row))


def stage_questions():
    for q in QUESTIONS:
        yield from ask_question(q)
        hide_panel()
        yield 800
    show_panel([Text("Your answers have been recorded.", "h2")])
    yield 2800
    return True


ACTIONS = [
    dict(text="Raise your right hand.", hold="Keep it raised.", secs=5),
    dict(text="Close your eyes and count to five.", eyes=True),
    dict(text="Bring an object from another room.", done="Press DONE when you are back."),
    dict(text="Turn around and look behind you.", hold="Keep looking.", secs=4),
    dict(text="Put your hand on the screen.", hold="Do not move it.", secs=11, hand=True),
    dict(text="Stay completely still.", hold="Do not move.", secs=8),
]


def wait_done(row):
    row.hidden = False
    box = {"v": None}

    def done():
        if box["v"] is None:
            box["v"] = True
            row.children[0].disabled = True

    row.children[0].cb = done

    def key(k):
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_d):
            done()

    KEYS.append(key)
    yield (lambda: box["v"])
    if key in KEYS:
        KEYS.remove(key)


def hand_slam():
    S.hand = dict(mode="slam", t0=now_ms())
    slam_hit()
    R.shake_boost = 9
    S.shake_until = now_ms() + 350


def eyes_closed(sub):
    sub.text = "Keep them closed."
    yield 1500
    hide_panel()
    S.blackout = True
    yield 2500
    random.choice([cue_whisper, cue_knock])(0.7)
    yield 3000
    S.blackout = False
    S.flash_img = A.flash[0]
    S.flash_until = now_ms() + 320
    show_panel([Text("Open your eyes.", "h2")])
    yield 2000


def stage_actions():
    for a in ACTIONS:
        q = Text("", "h2", eff=SHADOW)
        sub = Text("", "p", eff=SHADOW)
        row = Row([Button("DONE")], hidden=True)
        show_panel([q, sub, row])
        yield from type_text(q, a["text"])
        yield 900
        if a.get("eyes"):
            yield from eyes_closed(sub)
        elif a.get("done"):
            sub.text = a["done"]
            yield from wait_done(row)
        else:
            for n in range(a["secs"], 0, -1):
                sub.text = f"{a['hold']} {n}"
                if a.get("hand") and n == a["secs"] - 2:
                    hand_slam()
                if a.get("hand") and n == a["secs"] - 7 and S.hand:
                    S.hand["mode"] = "sway"
                    S.hand["sway0"] = now_ms()
                yield 1000
            S.hand = None
        hide_panel()
        yield 900
    show_panel([Text("Thank you. You did everything we asked.", "h2")])
    yield 2600
    return True


STORY_SAD = [
    "When you were six, you ate lunch alone every day, facing the wall so no one would see you cry.",
    "The other children laughed when you walked by. Your name was the joke.",
    "For your birthday, your mother set the table with twelve paper cups.",
    "You waited by the window in your best shirt until it got dark.",
    "Nobody came.",
    "Then you heard bells. A clown was standing at the door, smiling just for you.",
    "He stayed until midnight. He was the first one who ever chose you.",
    "After that he was always there. He said he would never leave you like the others did.",
    "Your mother never saw him. She held you and said you made him up because you were so lonely.",
    "Maybe she was right.",
]
STORY_DARK = [
    "But the night you stopped talking to him, he whispered that he would wait until you were alone again.",
    "Tonight you answered our questions. You said you were alone.",
    "You put your hand on the screen, and something on the other side pressed back.",
    "Look at the dark glass. Just behind your shoulder.",
    'He always used to smile and say, "Now it\'s just you and me."',
    "Every night, after the clown left, Mom came into your room to kiss you goodnight.",
    "Her lips were always too red, and little bells were caught in her hair.",
    'She whispered, "You don\'t need friends. You have me."',
]
STORY_LAST = '"No one will ever love you like Mommy."'


def peek_flash():
    S.peek = "flash"
    yield 2000
    S.peek = None


def stage_story():
    S.peek = None
    row = Row([Button("CONTINUE")], hidden=True)
    show_panel([StoryBlock(STORY_SAD), row])
    n1 = play_narration(A.narr[0])
    yield A.narr[0].get_length() * 1000 if n1 else 5000
    yield from wait_done(row)
    stop_narration(n1)
    hide_panel()
    yield 1200
    show_panel([StoryBlock(STORY_DARK, STORY_LAST)])
    n2 = play_narration(A.narr[1])
    if n2:
        yield 19200
        yield from peek_flash()
        yield 8000
        yield from peek_flash()
        yield max(0, A.narr[1].get_length() * 1000 - 31200)
    else:
        yield 5000
        yield from peek_flash()
        yield 6000
        yield from peek_flash()
        yield 5000
    stop_narration(None)
    show_panel([Wall()])
    S.peek = "p3"
    cue_whisper(0.9)
    yield 4500
    hide_panel()
    S.peek = None
    yield 2600
    return True


def show_walker(rate, step_ms=340):
    S.walker = True
    pl = S.walker_player
    pl.rate = rate
    pl.seek(0)
    pl.play()
    if A.runstep_walk and not S.run_steps:
        S.run_steps = dict(interval=step_ms / rate, next=now_ms(), voices=[])


def stop_run_steps():
    rs = S.run_steps
    if not rs:
        return
    S.run_steps = None
    for v in rs["voices"]:
        v.fade_out(0.05, 0.3)


def update_run_steps():
    rs = S.run_steps
    if not rs:
        return
    t = now_ms()
    if t >= rs["next"]:
        v = play(random.choice(A.runstep_walk))
        if v:
            rs["voices"] = [x for x in rs["voices"] if x.alive()] + [v]
        rs["next"] += rs["interval"]


def hide_walker():
    S.walker = False
    S.walker_from = None
    S.walker_player.pause()
    stop_run_steps()


RLGL_WALK = 30.0
TURN = dict(backStart=0.1, backEnd=4.8, turnStart=4.95, front=5.6, frontStart=6.0, frontEnd=9.8, revStart=10.0, revEnd=11.3)


def next_frame():
    st = {"n": 0}

    def c():
        st["n"] += 1
        return True if st["n"] > 1 else None
    return c


def stage_clown_game():
    v = S.turn_player
    rg = dict(scale=0.42, progress=0.0, info="", stop=False)
    S.rlgl = rg
    S.holding = False
    phase = "back"
    progress = 0.0
    timer = rand(2.5, 4.5)
    watch_t = 0.0
    step_t = 0.0
    last = now_ms()

    def seek(t):
        v.seek(t)
        v.play()

    v.rate = 1.0
    seek(TURN["backStart"])
    while True:
        yield next_frame()
        t = now_ms()
        dt = min(0.05, (t - last) / 1000)
        last = t
        holding = S.holding
        caught = False
        if phase == "back":
            if v.t >= TURN["backEnd"]:
                seek(TURN["backStart"])
            if holding:
                progress = min(1, progress + dt / RLGL_WALK)
                step_t -= dt
                if step_t <= 0 and A.runstep_clown:
                    play(random.choice(A.runstep_clown))
                    step_t = 0.5
            timer -= dt
            if progress >= 1:
                S.rlgl = None
                show_panel([Text("You touched him.", "h2")])
                cue_whisper(1)
                yield 2600
                hide_panel()
                v.pause()
                return True
            if timer <= 0:
                phase = "turning"
                watch_t = 0
                v.rate = 2.6
                seek(TURN["turnStart"])
        elif phase == "turning":
            watch_t += dt
            if holding and v.t >= (TURN["turnStart"] + TURN["front"]) / 2:
                caught = True
            elif v.t >= TURN["front"] or watch_t > 0.6:
                phase = "watching"
                watch_t = 0
                v.rate = 1.0
                timer = rand(1.6, 3.2)
        elif phase == "watching":
            if v.t >= TURN["frontEnd"]:
                seek(TURN["frontStart"])
            watch_t += dt
            if holding:
                caught = True
            else:
                timer -= dt
                if timer <= 0:
                    phase = "turningBack"
                    watch_t = 0
                    seek(TURN["revStart"])
        elif phase == "turningBack":
            if holding:
                caught = True
            else:
                watch_t += dt
                if v.t >= TURN["revEnd"] or watch_t > 2:
                    phase = "back"
                    seek(TURN["backStart"])
                    timer = rand(2, 4.5) * (1 - 0.5 * progress)
        if caught:
            v.pause()
            S.rlgl = None
            sc = rg["scale"]
            S.walker_from = dict(s0=0.96 * sc, b0=0.02 + 0.96 * (0.7 + 0.3 * sc), t0=now_ms(), dur=2000)
            show_walker(0.8, 520)
            yield 2000
            hide_walker()
            return False
        rg["scale"] = 0.42 + 0.78 * progress
        rg["progress"] = progress
        rg["info"] = "HOLD SPACE OR PRESS THE SCREEN TO WALK" if phase == "back" else ("" if phase == "turningBack" else "DO NOT MOVE")
        rg["stop"] = phase != "back"


def stage_clown(intro=True):
    if intro:
        show_panel([Text("ONE, TWO, THREE, CLOWN", "h2"),
                    Text("He wants to play. Walk to him while his back is turned. When he turns around, do not move.", color=DIM)])
        yield 4500
        hide_panel()
    while True:
        ok = yield from stage_clown_game()
        S.rlgl = None
        S.holding = False
        if ok:
            return True


GLASS_SPOTS = {
    "left": dict(clip="room_left", rect=(0.04, 0.18, 0.22, 0.72), pan=(1.0, 0.25)),
    "right": dict(clip="room_right", rect=(0.74, 0.18, 0.98, 0.75), pan=(0.25, 1.0)),
    "closet": dict(clip="room_closet", rect=(0.38, 0.2, 0.62, 0.8), pan=(0.7, 0.7)),
}
GLASS_HOLD = 0.3
SIGHT_TIME = 2.0
TV_SCREEN = (0.4135, 0.284, 0.5835, 0.514)
NIGHTS = 5
NIGHT_LEN = 660.0
QUIET_START = 180.0
MAX_VISITS = 3


def night_spawn_times(night_len):
    lo, hi = QUIET_START, night_len - 30.0
    while True:
        ts = sorted(rand(lo, hi) for _ in range(MAX_VISITS))
        if all(b - a >= 45.0 for a, b in zip(ts, ts[1:])):
            return ts


def glass_spot_rect(spot):
    x0, y0, x1, y1 = GLASS_SPOTS[spot]["rect"]
    return (1 - x1) * R.W, y0 * R.H, (1 - x0) * R.W, y1 * R.H


def glass_toggle_tv():
    gl = S.glass
    if not gl or gl["mode"] != "night":
        return
    gl["tv"] = not gl["tv"]
    gl["static_until"] = now_ms() + 150
    tv_click()
    if gl["music"]:
        gl["music"].gain.toward(0.16 if gl["tv"] else 0.0, 0.15)


def glass_flash(pos):
    gl = S.glass
    if not gl or gl["mode"] != "night" or gl["tv"] or gl["charge"] < 1:
        return
    gl["charge"] = 0.0
    gl["flash_until"] = now_ms() + 300
    gl["flash_pos"] = pos
    camera_flash()
    if gl["clown"] == "peek":
        x0, y0, x1, y1 = glass_spot_rect(gl["spot"])
        mx, my = (x1 - x0) * 0.25, (y1 - y0) * 0.15
        if x0 - mx <= pos[0] <= x1 + mx and y0 - my <= pos[1] <= y1 + my:
            gl["clown"] = "retreat"
            gl["room_player"].rate = 3.0
            gl["room_player"].play()
            gl["hits"] += 1


def glass_set_tv_clip(gl, name):
    pl = gl["tv_player"]
    if name == "tv_show":
        cur = gl["tv_name"]
        if cur in A.tv_shows and not pl.ended:
            return
        name = random.choice([k for k in A.tv_shows if k != cur] or A.tv_shows)
        pl.loop = False
    else:
        if gl["tv_name"] == name:
            return
        pl.loop = True
    gl["tv_name"] = name
    pl.clip = A.glass[name]
    pl.seek(0)
    pl.play()
    if gl["tv"]:
        gl["static_until"] = now_ms() + 120


def glass_night(n):
    gl = S.glass
    night_len = NIGHT_LEN
    gl.update(night=n, t=0.0, hour_len=night_len / 6, tv=True, charge=1.0, flash_until=0, static_until=0,
              clown="away", spot=None, threat=0.0, seen=None, cooldown=0.0, fake=rand(8, 14), mode="night")
    gl["spawns"] = night_spawn_times(night_len)
    threat_time = 7.4 - 0.9 * n
    gl["tv_name"] = None
    glass_set_tv_clip(gl, "tv_show")
    rp = gl["room_player"]
    rp.clip = A.glass["room_left"]
    rp.seek(rp.clip.duration)
    rp.pause()
    gl["music"] = Voice(A.music_box, 0.0, loops=-1)
    gl["music"].gain.toward(0.16, 0.3)
    last = now_ms()
    while True:
        yield next_frame()
        now = now_ms()
        dt = min(0.05, (now - last) / 1000)
        last = now
        gl["t"] += dt
        if gl["tv"]:
            gl["charge"] = min(1.0, gl["charge"] + dt / 8.0)
        if gl["clown"] == "away":
            if gl["spawns"] and gl["t"] >= gl["spawns"][0]:
                gl["spawns"].pop(0)
                choices = [k for k in GLASS_SPOTS if k != gl["spot"]]
                gl["spot"] = random.choice(choices)
                sp = GLASS_SPOTS[gl["spot"]]
                rp.clip = A.glass[sp["clip"]]
                rp.rate = 1.0
                rp.seek(0)
                rp.play()
                gl["clown"] = "peek"
                gl["threat"] = 0.0
                gl["seen"] = None
                if A.runstep_walk:
                    v = play(random.choice(A.runstep_walk), 0.35)
                    if v:
                        v.pan = sp["pan"]
                        v.apply()
        elif gl["clown"] == "peek":
            if rp.t >= GLASS_HOLD * rp.clip.duration:
                rp.pause()
            if not gl["tv"] and gl["seen"] is None:
                gl["seen"] = gl["t"]
            if gl["seen"] is not None:
                if gl["t"] - gl["seen"] >= SIGHT_TIME:
                    break
            else:
                gl["threat"] += dt / threat_time * 0.35
                if gl["threat"] >= 1:
                    break
        elif gl["clown"] == "retreat":
            if rp.ended:
                gl["clown"] = "away"
        if n >= 3:
            gl["fake"] -= dt
            if gl["fake"] <= 0:
                gl["fake"] = rand(7, 13)
                if A.runstep_walk:
                    v = play(random.choice(A.runstep_walk), 0.3)
                    if v:
                        v.pan = random.choice([sp["pan"] for sp in GLASS_SPOTS.values()])
                        v.apply()
        if gl["clown"] == "peek" and gl["threat"] >= 0.8:
            glass_set_tv_clip(gl, "tv_empty")
        elif gl["clown"] == "peek" and gl["threat"] >= 0.5:
            glass_set_tv_clip(gl, "tv_wrong")
        else:
            glass_set_tv_clip(gl, "tv_show")
        if gl["t"] >= night_len:
            gl["music"].fade_out(0.2, 1.0)
            gl["mode"] = "intro"
            return True
    gl["music"].fade_out(0.05, 0.3)
    gl["mode"] = "dead"
    S.game.fail_msg = "He was standing right behind you."
    return False


def glass_setup():
    S.glass = dict(mode="intro", tv=True, room_player=Player(A.glass["room_left"]), tv_player=Player(A.glass["tv_show"], loop=True),
                   music=None, hits=0, night=1, t=0.0, hour_len=15.0,
                   charge=1.0, flash_until=0, static_until=0, flash_pos=(0, 0), tv_name=None)


def glass_teardown():
    gl = S.glass
    for p in (gl["room_player"], gl["tv_player"]):
        p.pause()
        if p in PLAYERS:
            PLAYERS.remove(p)
    S.glass = None


def play_night(n, show_help):
    glass_setup()
    if show_help:
        show_panel([Text("THE DARK GLASS", "h2"),
                    Text("He is somewhere behind you. Do not turn around. Turn the TV off and watch the room in the dark glass. "
                         "When you see him, click on him to use the flash. The flash charges only while the TV is on. Survive until 6 AM.", color=DIM)])
        yield 7000
        hide_panel()
    while True:
        S.glass["mode"] = "intro"
        show_panel([Text(f"NIGHT {n}", "h2"), Text("12:00 AM", color=DIM)])
        yield 2200
        hide_panel()
        ok = yield from glass_night(n)
        if ok:
            break
        AU.stop_drone()
        yield 250
        yield from jumpscare()
        AU.start_drone()
    show_panel([Text("6:00 AM", "h2"), Text(f"NIGHT {n} SURVIVED", color=DIM)])
    yield 2400
    hide_panel()
    glass_teardown()
    return True


SAVE_FILE = "save.json"


def has_save():
    try:
        with open(SAVE_FILE) as f:
            return json.load(f).get("checkpoint") == "nights"
    except (OSError, ValueError):
        return False


def write_save():
    try:
        with open(SAVE_FILE, "w") as f:
            json.dump({"checkpoint": "nights"}, f)
    except OSError:
        pass


def clear_save():
    try:
        os.remove(SAVE_FILE)
    except OSError:
        pass


def save_checkpoint():
    write_save()
    show_panel([Text("PROGRESS SAVED", "h2")])
    yield 1800
    hide_panel()
    return True


def titled(title, gen_fn):
    def run():
        show_panel([Text(title, "h2")])
        yield 2200
        hide_panel()
        return (yield from gen_fn())
    return run


def build_segments():
    segs = [("QUESTIONS", titled("QUESTIONS", stage_questions)),
            ("ACTIONS", titled("ACTIONS", stage_actions)),
            ("SAVE", save_checkpoint)]
    for n in range(1, NIGHTS + 1):
        segs.append(("CLOWN GAME", stage_clown))
        segs.append((f"NIGHT {n}", (lambda n=n: play_night(n, n == 1))))
    segs.append(("STORY", stage_story))
    return segs


SEGMENTS = build_segments()
CONTINUE_INDEX = 3
DEV_PICKS = ["QUESTIONS", "ACTIONS", "CLOWN GAME", "NIGHT 1", "NIGHT 2", "NIGHT 3", "NIGHT 4", "NIGHT 5", "STORY"]


# ---------------------------------------------------------------- game flow

def set_intro_clown(on):
    S.intro_clown = on
    P.side = on
    if on:
        S.intro_player.play()
    else:
        S.intro_player.pause()


def begin(segments, finish):
    S.menu = False
    set_intro_clown(False)
    hide_panel()
    show_panel([Text("LOADING", "h2")])
    yield (lambda: True if A.ready() else None)
    hide_panel()
    S.game = Game()
    AU.start_drone()
    for i, (_, fn) in enumerate(segments):
        yield from fn()
        if i < len(segments) - 1:
            hide_panel()
            S.blackout = True
            yield 5000
            S.blackout = False
    finish()


def new_game():
    RUN.start(begin(SEGMENTS, game_complete))


def continue_game():
    RUN.start(begin(SEGMENTS[CONTINUE_INDEX:], game_complete))


def test_segment(label):
    seg = next(sg for sg in SEGMENTS if sg[0] == label)
    if label == "NIGHT 1":
        seg = (label, lambda: play_night(1, True))
    RUN.start(begin([seg], show_intro))


def game_complete():
    clear_save()
    show_end()


def show_end():
    show_panel([Text("DARK GLASS", "h1", color=BLOOD, eff=("glow", BLOOD, 0.5, 12)),
                Text("THANK YOU FOR PLAYING", color=DIM),
                Row([Button("PLAY AGAIN", new_game)], glitch=False),
                exit_row()])


DEMO_ROUNDS = 3


def demo_segments():
    return [("CLOWN GAME", (lambda i=i: stage_clown(i == 0))) for i in range(DEMO_ROUNDS)]


def start_demo():
    RUN.start(begin(demo_segments(), show_demo_end))


def show_demo_end():
    show_panel([Text("DARK GLASS", "h1", color=BLOOD, eff=("glow", BLOOD, 0.5, 12)),
                Text("He will be back tomorrow night.", "h2", eff=SHADOW),
                Text("THANK YOU FOR PLAYING THE DEMO. WISHLIST DARK GLASS ON STEAM.", color=DIM),
                Row([Button("PLAY AGAIN", start_demo)], glitch=False),
                exit_row()])


def show_demo_intro():
    set_intro_clown(True)
    show_panel([
        Text("DARK GLASS", "h1"),
        Text("DEMO", "warn"),
        OList(["Sit in a dark room.",
               "Wear headphones and turn the volume up.",
               "Hold SPACE or the mouse button to walk.",
               "When he turns around, do not move."]),
        Text("CONTAINS LOUD SOUNDS AND SUDDEN FRIGHTENING IMAGES", "warn"),
        Row([Button("PLAY DEMO", start_demo)], glitch=False),
        exit_row(),
    ])


def show_intro():
    S.menu = True
    if DEMO:
        show_demo_intro()
        return
    set_intro_clown(True)
    picks = [Text("TEST ONE STAGE", "label", glitch=False)]
    for label in DEV_PICKS:
        picks.append(Button(label, (lambda label=label: test_segment(label)), small=True))
    if has_save():
        main_row = Row([Button("CONTINUE", continue_game), Button("NEW GAME", new_game)], glitch=False)
    else:
        main_row = Row([Button("BEGIN", new_game)], glitch=False)
    show_panel([
        Text("DARK GLASS", "h1"),
        OList(["Sit in a dark room.",
               "Wear headphones and turn the volume up.",
               "Answer honestly. Do what you are asked.",
               "Do not look away from the screen."]),
        Text("CONTAINS LOUD SOUNDS AND SUDDEN FRIGHTENING IMAGES", "warn"),
        main_row,
        exit_row(),
        Row(picks, glitch=False),
    ])


def quit_game():
    pygame.quit()
    sys.exit()


def exit_row():
    return Row([Button("EXIT", quit_game, small=True)], glitch=False)


def show_gate():
    show_panel([
        Text("DARK GLASS", "h1"),
        Text("PUT ON HEADPHONES. THIS PROGRAM USES SOUND.", "warn"),
        Row([Button("CLICK TO ENTER")], glitch=False),
        exit_row(),
    ])


def return_to_menu():
    RUN.gen = None
    RUN.cond = None
    KEYS.clear()
    if S.glass:
        if S.glass.get("music"):
            S.glass["music"].stop()
        glass_teardown()
    S.rlgl = None
    S.holding = False
    if S.walker_player:
        hide_walker()
    if S.scare:
        if S.scare.get("player"):
            S.scare["player"].pause()
        S.scare = None
    S.peek = None
    S.hand = None
    S.blackout = False
    S.flash_until = 0
    S.shake_until = 0
    R.shake_boost = 1.0
    keep = set()
    for d in [AU.drone] + AU.dying:
        if d:
            keep.update(id(v) for v in (d.synth, d.heart, d.amb) if v)
    for v in VOICES[:]:
        if id(v) not in keep:
            v.stop()
    AU.start_drone()
    hide_panel()
    show_intro()


def enter_from_gate():
    S.gate = False
    show_intro()
    AU.start_drone()


# ---------------------------------------------------------------- main loop

def handle_event(ev):
    if ev.type == pygame.QUIT:
        pygame.quit()
        sys.exit()
    if ev.type == pygame.VIDEORESIZE:
        R.screen = pygame.display.set_mode((max(320, ev.w), max(200, ev.h)), pygame.RESIZABLE)
        R.build()
        return
    if S.loading:
        return
    if ev.type == pygame.MOUSEBUTTONDOWN:
        if S.gate:
            pos = R.to_crt(ev.pos)
            for rect, btn in P.hits:
                if btn.label == "EXIT" and rect.collidepoint(pos):
                    quit_game()
            enter_from_gate()
            return
        if S.scare:
            return
        if ev.button == 1 and P.visible and P.click(R.to_crt(ev.pos)):
            return
        on_pointer_down(ev)
        return
    if ev.type == pygame.MOUSEBUTTONUP:
        on_pointer_up(ev)
        return
    if ev.type == pygame.KEYDOWN:
        if ev.key == pygame.K_ESCAPE:
            if not S.gate and not S.menu:
                return_to_menu()
            return
        if S.gate:
            enter_from_gate()
            return
        for k in KEYS[:]:
            k(ev.key)
        on_key_down(ev)
        return
    if ev.type == pygame.KEYUP:
        on_key_up(ev)


def on_pointer_down(ev):
    if S.glass and ev.button == 1:
        glass_flash(R.to_crt(ev.pos))
    if S.rlgl:
        S.holding = True


def on_pointer_up(ev):
    S.holding = False


def on_key_down(ev):
    if ev.key == pygame.K_SPACE and S.glass:
        glass_toggle_tv()
    if ev.key == pygame.K_SPACE and S.rlgl:
        S.holding = True


def on_key_up(ev):
    if ev.key == pygame.K_SPACE:
        S.holding = False


R = None


def main():
    global R
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
    pygame.mixer.pre_init(SR, -16, 2, 512)
    pygame.init()
    pygame.mixer.set_num_channels(64)
    pygame.display.set_caption("Dark Glass Demo" if DEMO else "Dark Glass")
    icon = pygame.Surface((32, 32))
    icon.fill(VOID)
    pygame.display.set_icon(icon)
    R = Renderer()
    R.screen.fill(VOID)
    pygame.display.flip()
    start_loading()
    clock = pygame.time.Clock()
    last = now_ms()
    while True:
        for ev in pygame.event.get():
            handle_event(ev)
        if S.loading:
            if A.error:
                raise A.error
            if A.ready() and S.loading["x"] > 0.97:
                finish_loading()
        t = now_ms()
        dt = min(0.05, (t - last) / 1000)
        last = t
        tick(dt)
        clock.tick(60)


def start_loading():
    S.loading = dict(video=StreamVideo("loading.mp4"), t0=now_ms(), last=now_ms(), x=0.0)
    A.load_drone()
    AU.start_drone()
    A.load_bike()
    S.loading["bike"] = Voice(A.bike, 1.0, loops=-1) if A.bike else None
    A.load()


def finish_loading():
    if S.loading.get("bike"):
        S.loading["bike"].fade_out(0.3, 1.2)
    S.loading = None
    S.intro_player = Player(A.intro, loop=True)
    S.scare_player = Player(A.scare, loop=True)
    S.walker_player = Player(A.walker)
    S.turn_player = Player(A.turn)
    show_gate()


def tick(dt):
    RUN.update()
    for pl in PLAYERS:
        pl.update(dt)
    update_run_steps()
    AU.update()
    P.update()
    R.update()
    R.render()


if __name__ == "__main__":
    main()
