#!/usr/bin/env python3
"""CPU Load Generator with a web GUI.

Generates a controllable CPU load (0-100% duty cycle per worker process)
and serves a live dashboard at http://localhost:8321 — no dependencies,
Python standard library only.

Usage:
    python3 cpu_load.py [--port 8321] [--no-browser]
"""

import argparse
import json
import multiprocessing as mp
import os
import signal
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ---------------------------------------------------------------- load engine

def _worker(target, stop_flag):
    """Busy-loop for target% of each 100ms cycle, sleep the rest."""
    period = 0.1
    while not stop_flag.is_set():
        t = min(100.0, max(0.0, target.value))
        if t <= 0:
            time.sleep(period)
            continue
        busy_until = time.perf_counter() + period * t / 100.0
        while time.perf_counter() < busy_until:
            pass
        rest = period * (100.0 - t) / 100.0
        if rest > 0:
            time.sleep(rest)


class LoadEngine:
    def __init__(self):
        self.cpu_count = os.cpu_count() or 1
        self._target = mp.Value("d", 50.0)
        self._stop = mp.Event()
        self._procs = []
        self._lock = threading.Lock()

    @property
    def running(self):
        return bool(self._procs)

    @property
    def workers(self):
        return len(self._procs)

    @property
    def target(self):
        return self._target.value

    def set_target(self, pct):
        self._target.value = min(100.0, max(0.0, float(pct)))

    def start(self, workers):
        with self._lock:
            self._stop_locked()
            workers = min(self.cpu_count * 2, max(1, int(workers)))
            self._stop.clear()
            for _ in range(workers):
                p = mp.Process(target=_worker, args=(self._target, self._stop), daemon=True)
                p.start()
                self._procs.append(p)

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if not self._procs:
            return
        self._stop.set()
        for p in self._procs:
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()
        self._procs = []


# ---------------------------------------------------------------- cpu sampler

class CpuSampler:
    """Samples real CPU usage. Uses /proc/stat on Linux, psutil elsewhere
    if installed; otherwise reports no measurements (GUI shows target only)."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.total = None          # overall usage %, or None if unavailable
        self.cores = []            # per-core usage %
        self._prev = None
        self._psutil = None
        if not os.path.exists("/proc/stat"):
            try:
                import psutil
                self._psutil = psutil
            except ImportError:
                pass
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        while True:
            try:
                self._sample()
            except Exception:
                self.total, self.cores = None, []
            time.sleep(self.interval)

    def _sample(self):
        if self._psutil:
            self.cores = self._psutil.cpu_percent(percpu=True)
            self.total = sum(self.cores) / max(1, len(self.cores))
            return
        if not os.path.exists("/proc/stat"):
            return
        with open("/proc/stat") as f:
            lines = [l.split() for l in f if l.startswith("cpu")]
        now = {}
        for parts in lines:
            vals = [int(v) for v in parts[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            now[parts[0]] = (sum(vals), idle)
        if self._prev:
            usages = {}
            for key, (tot, idle) in now.items():
                ptot, pidle = self._prev.get(key, (tot, idle))
                dt, di = tot - ptot, idle - pidle
                usages[key] = 100.0 * (dt - di) / dt if dt > 0 else 0.0
            self.total = usages.get("cpu")
            self.cores = [usages[k] for k in sorted(
                (k for k in usages if k != "cpu"), key=lambda k: int(k[3:]))]
        self._prev = now


# ---------------------------------------------------------------- http server

def make_handler(engine, sampler):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/stats":
                self._json({
                    "cpu": sampler.total,
                    "cores": sampler.cores,
                    "running": engine.running,
                    "target": engine.target,
                    "workers": engine.workers,
                    "cpuCount": engine.cpu_count,
                })
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/api/control":
                self._json({"error": "not found"}, 404)
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            if "target" in req:
                engine.set_target(req["target"])
            action = req.get("action")
            if action == "start":
                engine.start(req.get("workers", engine.cpu_count))
            elif action == "stop":
                engine.stop()
            self._json({"ok": True, "running": engine.running,
                        "workers": engine.workers, "target": engine.target})

    return Handler


# ---------------------------------------------------------------------- page

PAGE = r"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>מחולל עומס CPU</title>
<style>
  :root {
    --page:      #0d0d0d;
    --surface:   #1a1a19;
    --surface-2: #242423;
    --ink:       #ffffff;
    --ink-2:     #c3c2b7;
    --muted:     #898781;
    --grid:      #2c2c2a;
    --baseline:  #383835;
    --border:    rgba(255,255,255,0.10);
    --series:    #3987e5;
    --good:      #0ca30c;
    --critical:  #d03b3b;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--page); color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    min-height: 100vh; padding: 28px clamp(16px, 4vw, 48px);
  }
  header { display: flex; align-items: baseline; gap: 14px; margin-bottom: 24px; flex-wrap: wrap; }
  h1 { font-size: 22px; font-weight: 650; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13px; }
  .pill {
    margin-inline-start: auto; font-size: 13px; font-weight: 600;
    padding: 5px 14px; border-radius: 999px; border: 1px solid var(--border);
    color: var(--ink-2); display: flex; align-items: center; gap: 8px;
  }
  .pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .pill.on .dot { background: var(--good); box-shadow: 0 0 8px rgba(12,163,12,.6); }
  .pill.on { color: var(--ink); }

  .layout { display: grid; grid-template-columns: 320px 1fr; gap: 18px; align-items: start; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }

  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 14px; padding: 20px;
  }
  .card h2 { font-size: 13px; font-weight: 600; color: var(--muted); margin-bottom: 14px; }

  /* controls */
  .bignum { font-size: 46px; font-weight: 700; line-height: 1; margin-bottom: 4px; }
  .bignum small { font-size: 22px; color: var(--ink-2); font-weight: 500; }
  .ctl-label { display: flex; justify-content: space-between; color: var(--ink-2);
               font-size: 13px; margin: 18px 0 8px; }
  input[type=range] {
    width: 100%; appearance: none; height: 6px; border-radius: 3px;
    background: linear-gradient(to left, var(--series) var(--fill,50%), var(--surface-2) var(--fill,50%));
    outline: none; cursor: pointer;
  }
  input[type=range]::-webkit-slider-thumb {
    appearance: none; width: 20px; height: 20px; border-radius: 50%;
    background: var(--ink); border: 3px solid var(--series); cursor: grab;
  }
  input[type=range]::-moz-range-thumb {
    width: 14px; height: 14px; border-radius: 50%;
    background: var(--ink); border: 3px solid var(--series); cursor: grab;
  }
  .stepper { display: flex; align-items: center; gap: 12px; }
  .stepper button {
    width: 36px; height: 36px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--surface-2); color: var(--ink); font-size: 18px; cursor: pointer;
  }
  .stepper button:hover { background: #2e2e2c; }
  .stepper .val { font-size: 20px; font-weight: 650; min-width: 2.2ch; text-align: center;
                  font-variant-numeric: tabular-nums; }
  .stepper .of { color: var(--muted); font-size: 13px; }
  .go {
    width: 100%; margin-top: 22px; padding: 13px; font-size: 16px; font-weight: 700;
    border: none; border-radius: 12px; cursor: pointer;
    background: var(--series); color: #fff; transition: filter .15s;
  }
  .go:hover { filter: brightness(1.12); }
  .go.stop { background: var(--critical); }
  .hint { color: var(--muted); font-size: 12px; margin-top: 12px; line-height: 1.5; }

  /* stat tiles */
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
           gap: 18px; margin-bottom: 18px; }
  .tile { background: var(--surface); border: 1px solid var(--border);
          border-radius: 14px; padding: 16px 20px; }
  .tile .k { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .tile .v { font-size: 28px; font-weight: 700; }
  .tile .v small { font-size: 15px; color: var(--ink-2); font-weight: 500; }

  /* chart */
  .chart-wrap { position: relative; direction: ltr; }
  canvas { width: 100%; height: 260px; display: block; }
  #tip {
    position: absolute; pointer-events: none; display: none; direction: ltr;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px;
    padding: 6px 10px; font-size: 12px; color: var(--ink);
    font-variant-numeric: tabular-nums; white-space: nowrap; z-index: 2;
    transform: translate(-50%, -130%);
  }
  #tip .t { color: var(--muted); }

  /* per-core bars */
  .cores { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
           gap: 10px 24px; margin-top: 14px; }
  .core { display: flex; align-items: center; gap: 10px; font-size: 12px; }
  .core .name { color: var(--muted); min-width: 52px; }
  .core .track { flex: 1; display: block; height: 8px; background: var(--surface-2);
                 border-radius: 4px; overflow: hidden; direction: ltr; }
  .core .fill { display: block; height: 100%; background: var(--series);
                border-radius: 0 4px 4px 0; width: 0%; transition: width .4s ease; }
  .core .pct { color: var(--ink-2); min-width: 4ch; text-align: left; direction: ltr;
               font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<header>
  <h1>מחולל עומס CPU</h1>
  <span class="sub">כלי בדיקת עומס למעבד</span>
  <span class="pill" id="pill"><span class="dot"></span><span id="pillText">מנוחה</span></span>
</header>

<div class="layout">
  <div class="card">
    <h2>בקרת עומס</h2>
    <div class="bignum"><span id="targetNum">50</span><small>% יעד</small></div>
    <div class="ctl-label"><span>עוצמת עומס</span><span id="targetEcho">50%</span></div>
    <input type="range" id="target" min="0" max="100" value="50">
    <div class="ctl-label"><span>תהליכי עומס (ליבות)</span></div>
    <div class="stepper">
      <button id="wMinus" aria-label="פחות">−</button>
      <span class="val" id="wVal">1</span>
      <span class="of" id="wOf">מתוך 1 ליבות</span>
      <button id="wPlus" aria-label="יותר">+</button>
    </div>
    <button class="go" id="go">▶ התחל עומס</button>
    <p class="hint">כל תהליך עובד במחזוריות של 100 מילישניות — עסוק לפי אחוז היעד וישן בשאר הזמן. אפשר לשנות את העוצמה תוך כדי ריצה.</p>
  </div>

  <div>
    <div class="tiles">
      <div class="tile"><div class="k">שימוש כולל ב-CPU</div><div class="v" id="tCpu">—</div></div>
      <div class="tile"><div class="k">יעד עומס</div><div class="v" id="tTarget">50<small>%</small></div></div>
      <div class="tile"><div class="k">תהליכים פעילים</div><div class="v" id="tWorkers">0</div></div>
      <div class="tile"><div class="k">ליבות מעבד</div><div class="v" id="tCores">—</div></div>
    </div>

    <div class="card">
      <h2>שימוש ב-CPU — 60 השניות האחרונות</h2>
      <div class="chart-wrap">
        <canvas id="chart"></canvas>
        <div id="tip"></div>
      </div>
      <div class="cores" id="cores"></div>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const POLL_MS = 500, WINDOW_S = 60;
const MAXPTS = Math.round(WINDOW_S * 1000 / POLL_MS);
let history = [];            // {t: ms, v: pct}
let state = { running:false, cpuCount:1, workers:0 };
let wantWorkers = null;

// ---- controls
const slider = $("target");
function paintSlider() {
  slider.style.setProperty("--fill", slider.value + "%");
  $("targetNum").textContent = slider.value;
  $("targetEcho").textContent = slider.value + "%";
  $("tTarget").innerHTML = slider.value + "<small>%</small>";
}
let dragging = false;
slider.addEventListener("pointerdown", () => dragging = true);
slider.addEventListener("pointerup", () => dragging = false);
slider.addEventListener("input", paintSlider);
slider.addEventListener("change", () => {
  dragging = false;
  post({ target: +slider.value });
});
paintSlider();

$("wMinus").onclick = () => bumpWorkers(-1);
$("wPlus").onclick  = () => bumpWorkers(+1);
function bumpWorkers(d) {
  wantWorkers = Math.min(state.cpuCount * 2, Math.max(1, (wantWorkers ?? state.cpuCount) + d));
  $("wVal").textContent = wantWorkers;
  if (state.running) post({ action: "start", workers: wantWorkers, target: +slider.value });
}

$("go").onclick = () => {
  if (state.running) post({ action: "stop" });
  else post({ action: "start", workers: wantWorkers ?? state.cpuCount, target: +slider.value });
};

async function post(body) {
  try {
    const r = await fetch("/api/control", { method: "POST", body: JSON.stringify(body) });
    render(await r.json(), true);
  } catch (e) {}
}

// ---- polling
async function poll() {
  try {
    const r = await fetch("/api/stats");
    const s = await r.json();
    if (s.cpu != null) {
      history.push({ t: Date.now(), v: s.cpu });
      if (history.length > MAXPTS) history.shift();
    }
    render(s);
    drawChart();
    drawCores(s.cores || []);
  } catch (e) {}
  setTimeout(poll, POLL_MS);
}

function render(s, fromPost) {
  if (s.running !== undefined) state.running = s.running;
  if (s.cpuCount) state.cpuCount = s.cpuCount;
  if (s.workers !== undefined) state.workers = s.workers;
  if (wantWorkers === null && state.cpuCount) wantWorkers = state.cpuCount;
  $("wVal").textContent = state.running ? state.workers : (wantWorkers ?? 1);
  $("wOf").textContent = "מתוך " + state.cpuCount + " ליבות";
  $("tWorkers").textContent = state.workers;
  $("tCores").textContent = state.cpuCount;
  if (s.cpu !== undefined)
    $("tCpu").innerHTML = s.cpu == null ? "—" : s.cpu.toFixed(0) + "<small>%</small>";
  const pill = $("pill");
  pill.classList.toggle("on", state.running);
  $("pillText").textContent = state.running ? "עומס פעיל" : "מנוחה";
  const go = $("go");
  go.classList.toggle("stop", state.running);
  go.textContent = state.running ? "■ עצור עומס" : "▶ התחל עומס";
  if (!fromPost && !dragging && s.target !== undefined && +slider.value !== Math.round(s.target)) {
    slider.value = Math.round(s.target); paintSlider();
  }
}

// ---- line chart (canvas, LTR)
const cv = $("chart"), ctx = cv.getContext("2d");
const PAD = { l: 34, r: 10, t: 10, b: 22 };
let hoverX = null;

function cssVar(n) { return getComputedStyle(document.body).getPropertyValue(n).trim(); }

function drawChart() {
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const pw = W - PAD.l - PAD.r, ph = H - PAD.t - PAD.b;
  const now = Date.now(), t0 = now - WINDOW_S * 1000;
  const x = t => PAD.l + pw * (t - t0) / (WINDOW_S * 1000);
  const y = v => PAD.t + ph * (1 - v / 100);

  // grid + y ticks
  ctx.strokeStyle = cssVar("--grid"); ctx.lineWidth = 1;
  ctx.fillStyle = cssVar("--muted");
  ctx.font = "11px system-ui, sans-serif";
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const v of [0, 25, 50, 75, 100]) {
    ctx.beginPath(); ctx.moveTo(PAD.l, y(v)); ctx.lineTo(W - PAD.r, y(v)); ctx.stroke();
    ctx.fillText(v, PAD.l - 8, y(v));
  }
  // x ticks (seconds ago)
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const s of [60, 45, 30, 15, 0]) {
    const px = x(now - s * 1000);
    ctx.fillText(s === 0 ? "עכשיו" : "-" + s + 'ש', px, H - PAD.b + 6);
  }
  // baseline
  ctx.strokeStyle = cssVar("--baseline");
  ctx.beginPath(); ctx.moveTo(PAD.l, y(0)); ctx.lineTo(W - PAD.r, y(0)); ctx.stroke();

  const pts = history.filter(p => p.t >= t0);
  if (pts.length > 1) {
    // area fill
    const grad = ctx.createLinearGradient(0, PAD.t, 0, PAD.t + ph);
    grad.addColorStop(0, "rgba(57,135,229,0.22)");
    grad.addColorStop(1, "rgba(57,135,229,0)");
    ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(x(p.t), y(p.v)) : ctx.moveTo(x(p.t), y(p.v)));
    ctx.lineTo(x(pts[pts.length - 1].t), y(0));
    ctx.lineTo(x(pts[0].t), y(0));
    ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
    // line
    ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(x(p.t), y(p.v)) : ctx.moveTo(x(p.t), y(p.v)));
    ctx.strokeStyle = cssVar("--series"); ctx.lineWidth = 2;
    ctx.lineJoin = "round"; ctx.stroke();
    // last point marker
    const last = pts[pts.length - 1];
    ctx.beginPath(); ctx.arc(x(last.t), y(last.v), 4, 0, Math.PI * 2);
    ctx.fillStyle = cssVar("--series"); ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
  }

  // hover crosshair + tooltip
  const tip = $("tip");
  if (hoverX != null && pts.length > 1) {
    let best = pts[0];
    for (const p of pts) if (Math.abs(x(p.t) - hoverX) < Math.abs(x(best.t) - hoverX)) best = p;
    const bx = x(best.t), by = y(best.v);
    ctx.strokeStyle = cssVar("--baseline");
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(bx, PAD.t); ctx.lineTo(bx, PAD.t + ph); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(bx, by, 5, 0, Math.PI * 2);
    ctx.fillStyle = cssVar("--series"); ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
    const ago = Math.round((now - best.t) / 1000);
    tip.innerHTML = "<b>" + best.v.toFixed(1) + "%</b> <span class='t'>· " +
                    (ago === 0 ? "now" : "-" + ago + "s") + "</span>";
    tip.style.display = "block";
    tip.style.left = bx + "px"; tip.style.top = by + "px";
  } else {
    tip.style.display = "none";
  }
}

cv.addEventListener("mousemove", e => {
  hoverX = e.offsetX; drawChart();
});
cv.addEventListener("mouseleave", () => { hoverX = null; drawChart(); });
window.addEventListener("resize", drawChart);

// ---- per-core bars
function drawCores(cores) {
  const box = $("cores");
  if (box.children.length !== cores.length) {
    box.innerHTML = cores.map((_, i) =>
      '<div class="core"><span class="name">ליבה ' + i + '</span>' +
      '<span class="track"><span class="fill"></span></span>' +
      '<span class="pct">0%</span></div>').join("");
  }
  cores.forEach((v, i) => {
    const el = box.children[i];
    el.querySelector(".fill").style.width = Math.min(100, v).toFixed(0) + "%";
    el.querySelector(".pct").textContent = v.toFixed(0) + "%";
  });
}

poll();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="CPU load generator with a web GUI")
    ap.add_argument("--port", type=int, default=8321, help="HTTP port (default 8321)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser")
    args = ap.parse_args()

    engine = LoadEngine()
    sampler = CpuSampler()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(engine, sampler))
    url = f"http://127.0.0.1:{args.port}"
    print(f"CPU Load Generator ({engine.cpu_count} cores) — GUI at {url}")
    print("Press Ctrl+C to quit.")

    def shutdown(*_):
        print("\nStopping workers…")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        engine.stop()


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
