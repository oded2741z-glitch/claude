import datetime
import math
import os
import random
import sys
import threading

import cv2
import numpy as np
import pygame

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
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
LEVELS = 10


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
                f = cv2.convertScaleAbs(f, alpha=self.contrast, beta=127.5 * (1 - self.contrast))
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
        self.ch = sound.play(loops=loops) if sound else None
        self.apply()
        VOICES.append(self)

    def alive(self):
        return self.ch is not None and self.ch.get_sound() is self.sound

    def apply(self):
        if self.alive():
            self.ch.set_volume(clamp(self.gain.value() * MASTER, 0, 1))

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
        self.synth.gain.set(self.out.value() * A.drone_scale)
        if self.heart:
            self.heart.gain.set(self.hg.value())
        if self.amb:
            self.amb.gain.set(self.hg.value() * 0.6)
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


# ---------------------------------------------------------------- assets

class Assets:
    def load(self):
        self.drone_synth, self.drone_scale = make_drone_synth()
        self.heart = load_sound("heartbeat.wav")
        self.ambient = load_sound("ambient.wav")
        self.scream = load_sound("scream.mp3", 1.8)
        self.eye = css_filter(read_rgb("eye.jpg"), ("gray", 1), ("contrast", 1.2), ("bright", 0.9))
        self.faces = []
        for i in (6, 7, 8, 9, 10):
            try:
                self.faces.append(rgb_surface(read_rgb(f"s{i}.jpg")))
            except cv2.error:
                pass
        self.intro = Clip("intro.mp4", 1.1)
        self.scare = Clip("scare.mp4")
        self.clips = [self.intro, self.scare]
        threading.Thread(target=self._load_clips, daemon=True).start()

    def _load_clips(self):
        for c in self.clips:
            c.load()
        self.clips_done = True

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


class Stats:
    def __init__(self, pairs):
        self.pairs = pairs

    def blocks(self, maxw):
        px = clamp(3 * vmin(), 16, 28)
        f = font("display", px)
        lw = max(f.size(a)[0] for a, _ in self.pairs)
        rw = max(f.size(b)[0] for _, b in self.pairs)
        lh = f.get_height()
        bw = lw + 24 + rw
        bh = lh * len(self.pairs) + 4 * (len(self.pairs) - 1)

        def draw(surf, x, y, bright):
            for i, (a, b) in enumerate(self.pairs):
                yy = y + i * (lh + 4)
                ia = f.render(a, True, brighten(DIM) if bright else DIM)
                ib = f.render(b, True, brighten(PHOSPHOR) if bright else PHOSPHOR)
                surf.blit(ia, (int(x + lw - ia.get_width()), int(yy)))
                surf.blit(ib, (int(x + lw + 24), int(yy)))
        return [Block(bw, bh, draw, True)]


DART = [(0.0, 0, 0), (0.18, 0, 0), (0.21, -0.025, 0.01), (0.40, -0.025, 0.01), (0.43, 0.02, -0.01), (0.62, 0.02, -0.01),
        (0.65, -0.01, -0.015), (0.82, -0.01, -0.015), (0.85, 0, 0), (1.0, 0, 0)]


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


class Panel:
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
        w = min(1312, int(sw * 0.9))
        h = min(752, int(sh * 0.85))
        w = min(w, int((h - 32) * 16 / 9) + 32)
        h = min(h, int((w - 32) * 9 / 16) + 32)
        self.screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
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
        if S.intro_clown:
            ch = H * 0.82
            cw = ch * 460 / 720
            draw_screen_video(c, S.intro_player, (W - W * 0.03 - cw, H - H * 0.02 - ch, cw, ch), 0.75, 0.85)
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
                rgb = cv2.convertScaleAbs(rgb, alpha=1.8 * 1.3, beta=127.5 * (1 - 1.8) * 1.3)
            else:
                rgb = cv2.convertScaleAbs(rgb, alpha=1.25, beta=127.5 * (1 - 1.25))
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

    def start(self, gen):
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


S = State()


class Game:
    def __init__(self, start_stage):
        self.start_stage = start_stage
        self.stage = start_stage + 1
        self.scare_level = 3 + random.randint(0, 5)
        self.hits = 0
        self.misses = 0
        self.falses = 0
        self.rt_sum = 0.0
        self.lv = None
        self.paused = False
        self.level = 1
        self.chain = 0
        self.caught = False
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


STAGES = [
    ("QUESTIONS", stage_questions),
]


# ---------------------------------------------------------------- game flow

def set_intro_clown(on):
    S.intro_clown = on
    P.side = on
    if on:
        S.intro_player.play()
    else:
        S.intro_player.pause()


def run_game(start_stage=0):
    set_intro_clown(False)
    hide_panel()
    show_panel([Text("LOADING", "h2")])
    yield (lambda: True if A.ready() else None)
    g = Game(start_stage)
    S.game = g
    AU.start_drone()
    for i in range(start_stage, len(STAGES)):
        g.stage = i + 1
        show_panel([Text(f"STAGE {i + 1}", "h2"), Text(STAGES[i][0], color=DIM)])
        yield 2200
        hide_panel()
        ok = yield from STAGES[i][1]()
        if not ok:
            AU.stop_drone()
            yield 250
            yield from jumpscare()
            show_end(False)
            return
    AU.stop_drone()
    show_end(True)


def show_end(won):
    g = S.game
    avg = f"{g.rt_sum / g.hits / 1000:.3f} S" if g.hits else "-"
    if won:
        head = [Text("ASSESSMENT COMPLETE", "h1"),
                Text(f"You responded to every stimulus through all {LEVELS} levels. Your results have been recorded.")]
    else:
        msg = g.fail_msg or (f"It reached you at level {g.level}." if g.caught else f"Too many errors at level {g.level}.")
        head = [Text("TERMINATED", "h1"), Text(msg, "warn")]
    stats = Stats([("STIMULI CAUGHT", str(g.hits)), ("MISSED", str(g.misses)),
                   ("FALSE RESPONSES", str(g.falses)), ("AVG RESPONSE", avg)])
    again = Button("RUN AGAIN", lambda: RUN.start(run_game(g.start_stage)))
    show_panel(head + [stats, Row([again], glitch=False)])


def show_intro():
    set_intro_clown(True)
    picks = [Text("TEST ONE STAGE", "label", glitch=False)]
    for i, (name, _) in enumerate(STAGES):
        picks.append(Button(f"{i + 1}. {name}", (lambda i=i: RUN.start(run_game(i))), small=True))
    show_panel([
        Text("STATIC RESPONSE", "h1"),
        Text("This program measures how fast you react to things you do not expect. Each level lasts over a minute and moves faster than the one before."),
        OList(["Sit in a dark room.",
               "Wear headphones and turn the volume up.",
               "Press SPACE, or tap the screen, the moment you hear a sound or see an image.",
               "Do not press when nothing is there.",
               "Three errors in one level end the session.",
               "Do not look away from the screen."]),
        Text("CONTAINS LOUD SOUNDS AND SUDDEN FRIGHTENING IMAGES", "warn"),
        Row([Button("BEGIN", lambda: RUN.start(run_game(0)))], glitch=False),
        Row(picks, glitch=False),
    ])


def show_gate():
    show_panel([
        Text("STATIC RESPONSE", "h1"),
        Text("PUT ON HEADPHONES. THIS PROGRAM USES SOUND.", "warn"),
        Row([Button("CLICK TO ENTER")], glitch=False),
    ])


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
    if ev.type == pygame.MOUSEBUTTONDOWN:
        if S.gate:
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
    pass


def on_pointer_up(ev):
    pass


def on_key_down(ev):
    pass


def on_key_up(ev):
    pass


R = None


def main():
    global R
    pygame.mixer.pre_init(SR, -16, 2, 512)
    pygame.init()
    pygame.mixer.set_num_channels(64)
    pygame.display.set_caption("Static Response")
    icon = pygame.Surface((32, 32))
    icon.fill(VOID)
    pygame.display.set_icon(icon)
    R = Renderer()
    R.screen.fill(VOID)
    pygame.display.flip()
    A.load()
    S.intro_player = Player(A.intro, loop=True)
    S.scare_player = Player(A.scare, loop=True)
    show_gate()
    clock = pygame.time.Clock()
    last = now_ms()
    while True:
        for ev in pygame.event.get():
            handle_event(ev)
        t = now_ms()
        dt = min(0.05, (t - last) / 1000)
        last = t
        RUN.update()
        for pl in PLAYERS:
            pl.update(dt)
        AU.update()
        P.update()
        R.update()
        R.render()
        clock.tick(60)


if __name__ == "__main__":
    main()
