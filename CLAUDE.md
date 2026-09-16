# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-only "display room" system: one **controller** PC drives several **viewer** windows (each a
fullscreen PyQt5/QtWebEngine grid of 1, 2 or 4 web streams) over Socket.IO. A third tool, the
**configurator**, edits the text config files the other two read. There is no build step, no test
suite and no linter; each script is a standalone entry point run from the repo root.

## Running

All scripts must be run from the directory that holds the config files (they use relative paths).

```
python display_controller.py            # host: embedded Socket.IO server on 0.0.0.0:5000 + control GUI
python viewer.py "Screen 2" 4           # argv[1] = screen label, argv[2] = layout (1 | 2 | 4)
python display_configurator.py          # edits displays_map.txt / targets.txt / config.txt
```

The controller normally launches viewers itself as subprocesses (`viewer.py`, or `viewer.exe` next to the
executable when frozen). Dependencies: `customtkinter`, `PyQt5`, `PyQtWebEngine`, `python-socketio`,
`eventlet`, `keyboard`. `winsound`, `ctypes.windll`, `os.startfile` and `taskkill` make the code Windows-only.

`build.bat` builds the three `--onefile` executables with PyInstaller into `dist/`. eventlet picks its hub
and socketio its async driver via `importlib` at runtime, which PyInstaller cannot see, so
`display_controller.py` imports `eventlet.hubs.{epolls,kqueue,poll,selects}` and
`engineio.async_drivers.eventlet` explicitly (all four hubs import safely on every OS; only
`is_available()` differs) and the batch file adds `--collect-submodules eventlet dns` as a belt-and-braces.
`shared.py` is a module, not an entry point; do not build it.

Hotkey: F8 hides/shows the configurator. There is no viewer/controller hotkey; the `oT` watermark in the
viewer's bottom-right corner closes it on click, and the controller has a Quit button. The controller
appends every log line to `controller.log` next to the scripts.

## Architecture

### Three processes, one message bus

`display_controller.py` starts a `socketio.Server` (eventlet, port 5000) in a daemon thread and then
connects to it as a *client* like any viewer. All state flows through the server; the controller never
talks to a viewer directly. `shared.py` holds the loaders for `config.txt` and `targets.txt` and derives
`SERVER_URL` from the `server_url` key in `config.txt` (default 127.0.0.1:5000); a viewer on another
machine needs its own `config.txt` pointing at the controller's IP.

Server-side dicts: `room_state` (per-screen payload), `networked_viewers` (label -> layout),
`sid_to_target`. Events:

| Client emits            | Server rebroadcasts   | Meaning                                   |
|-------------------------|-----------------------|-------------------------------------------|
| `announce_viewer`       | `viewer_online`       | viewer joined with `{target, layout}`     |
| (disconnect)            | `viewer_offline`      | last socket for that label dropped        |
| `update_screen_state`   | `screen_update`       | merge payload into `room_state[target]`   |
| `request_snapshot`      | `execute_snapshot`    | viewer grabs one quad to `snapshots/`     |
| `kill_viewer`           | `kill_command`        | viewer with that label closes             |
| (connect)               | `init_sync`           | full `room_state` + `networked_viewers`   |

### The per-screen payload

`room_state["Screen 1"]` is a flat dict of **strings only** (tkinter/JSON round-trips depend on it):
`"0".."3"` = stream URL per quad, `"0_name".."3_name"` = target name shown in the quad header,
`"fullscreen"` = quad index or `"-1"`, `"blackout"` = `"True"`/`"False"`. Updates are merges, so a
partial payload (e.g. only `blackout`) is valid. The viewer resolves a URL without `://` by prefixing
`http://`; an empty URL shows the placeholder HTML (the loader picked by `loading_animation`, or plain
text when that key is `none`), and blackout shows a plain black page. Every state update is
applied to both the grid frames and the fullscreen frame (`ContentFrame.show_content` only reloads when
the URL actually changed).

### Identity is the screen label string

Everything is keyed by the free-text label from `displays_map.txt` (default `"Screen N"`). The viewer
picks its physical monitor in `resolve_target_screen`: it reads its own row from `displays_map.txt` (via
`shared.load_display_nodes`) and matches, in order, the Windows device name in `info2` against
`QScreen.name()`, then the `offset` point against screen geometries, then falls back to the digits in the
label (`"Screen 2"` -> monitor index 1). It prints which rule won at startup.
The controller refuses to launch a viewer on the monitor its own window currently sits on
(`is_controller_on_screen`, computed from the node's `offset`/`res` fields). Renaming a screen in the
configurator rewrites the `CONFIG: LINK` entries but not saved scenes.

### Controller UI flow

The canvas draws nodes from `displays_map.txt`; each screen node has three tiny `4/2/1` boxes that
launch, relaunch-with-new-layout, or kill the viewer subprocess (`toggle_viewer_checkbox`). Clicking the
node body selects it, which enables the "Stream Controls" panel; that panel edits **only**
`current_stream_target`. `sync_stream_ui_from_state` temporarily sets `current_stream_target = None`
while it writes the option menus so that `on_stream_change` does not echo state back mid-refresh.

Scenes (`scenes.json`) store `{"state": room_state, "layouts": screen_layouts}`; older files hold the bare
state and are handled. `load_scene` blanks screens missing from the scene, kills/relaunches viewers whose
layout differs, and re-emits the state at 0/2/4/6 s to catch viewers that are still starting. A
`default_scene` key in `config.txt` auto-loads 2.5 s after startup.

### Config files (all plain text, in the working directory)

- `displays_map.txt`: header lines `CONFIG: GRID, rows, cols` and `CONFIG: LINK, a, b` (an old `CONFIG: SIZE` line is ignored);
  then one CSV row per node with 10 fields: `label, type(Screen|GPU), info1, info2, res, offset, primary,
  row, col, span`. `offset` is `X:0 Y:0` and `res` is `1920x1080`; the controller parses both.
- `targets.txt`: `name|url` per line.
- `config.txt`: `key=value`; booleans are the strings `True`/`False`. Keys in use: `show_header`,
  `show_animation`, `loading_animation`, `server_url`, `default_scene`. Always write it through `shared.update_config` so
  keys set by another tool (the controller's `default_scene`) survive.
- `viewer_layouts.json`: viewer-side per-layout window size, saved by the resize grip.

## Gotchas

- `COLORS`, the frameless drag-header code and the help popup are duplicated in the controller and the
  configurator; keep them in sync when changing the look. The accent is `#389379` in both.
- All windows use `overrideredirect(True)`; there is no native title bar, so every window needs its own
  Quit button and drag handle.
- The waiting page is a self-contained page with **no JavaScript and no external files**: every loader is
  an inline SVG animated purely with CSS keyframes. JavaScript inside a page handed to `setHtml` does
  **not** run in this viewer: Qt percent-encodes the whole string into a `data:` URL, and the page renders
  its markup but never executes its scripts. That is why the earlier Lottie versions (inline player, then a
  local `placeholder.html` with `lottie.min.js` beside it) both failed while the CSS spinner always worked.
  Keep the page JS-free.
- `viewer.py` holds ten loaders as `CSS_*` / `SVG_*` string pairs in the `ANIMATIONS` dict, keyed by the
  names in `shared.ANIMATION_CHOICES`: `elta`, `elta_wave`, `elta_wave_ltr`, `dots`, `rings`, `radar`,
  `waves`, `scan`, `spinner`, `none`. `build_animation_page(name)` pastes a pair into `ANIM_SHELL` with `.replace()` on the
  `__CSS__` / `__SVG__` markers — never an f-string or `%`, because the CSS is full of braces and percent
  signs. `shared.load_animation_name()` reads `loading_animation` from `config.txt`, falling back to `none`
  when the legacy `show_animation=False` is set and to `elta` otherwise; the configurator's Settings tab
  writes both keys. `elta` is hand-translated from the original Lottie (4 rounded squares that flip outward
  and squash on landing, the wordmark bouncing between them; 140 frames at 60 fps, so a 2.333 s loop, and
  every keyframe percentage is `frame / 140`); `elta_wave` is the same artwork with each square and letter
  sharing one `hop` keyframe on a staggered `animation-delay`, right to left; `elta_wave_ltr` reuses
  `SVG_ELTA_WAVE` unchanged and only reverses those delays, so the two CSS blocks must stay in step. The wordmark is an SVG
  `<text>` in Segoe UI Bold, not the original's stroked letter paths, which looked blobby at this size.
  Every loader uses the same three blues: `#2766BE`, `#246CD0` and `#3787F6`.
- Snapshots grab the quad's rectangle from the screen compositor (`QScreen.grabWindow`) because
  `QWebEngineView.grab()` returns black for GPU-rendered video; the viewer must be visible for this to work.
- Hotkeys from the `keyboard` library fire on a background thread; always hop to the tkinter thread with
  `root.after(0, ...)` as the configurator's F8 handler does.
