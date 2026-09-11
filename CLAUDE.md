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

Hotkeys: F4 hides/shows the controller and every viewer; F8 hides/shows the configurator. The `oT`
watermark in the viewer's bottom-right corner closes it on click.

## Architecture

### Three processes, one message bus

`display_controller.py` starts a `socketio.Server` (eventlet, port 5000) in a daemon thread and then
connects to it as a *client* like any viewer. All state flows through the server; the controller never
talks to a viewer directly. `shared.py` holds `SERVER_URL` (127.0.0.1) plus the loaders for `config.txt`
and `targets.txt`; to run viewers on other machines, change `SERVER_URL` in the viewer's copy.

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
`http://`; an empty URL shows the placeholder HTML (Lottie from `loading.json` if present, else a CSS spinner,
or plain text when `show_animation=False`).

### Identity is the screen label string

Everything is keyed by the free-text label from `displays_map.txt` (default `"Screen N"`). The viewer
derives which physical monitor to open on from the digits in its label (`"Screen 2"` -> monitor index 1).
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

- `displays_map.txt`: header lines `CONFIG: SIZE, w, h`, `CONFIG: GRID, rows, cols`, `CONFIG: LINK, a, b`;
  then one CSV row per node with 10 fields: `label, type(Screen|GPU), info1, info2, res, offset, primary,
  row, col, span`. `offset` is `X:0 Y:0` and `res` is `1920x1080`; the controller parses both.
- `targets.txt`: `name|url` per line.
- `config.txt`: `key=value`; booleans are the strings `True`/`False`.
- `viewer_layouts.json`: viewer-side per-layout window size, saved by the resize grip.

## Gotchas

- `display_configurator.py`'s `__main__` block and `on_close` reference `DisplayController` / `self.destroy`,
  which do not exist there; the working entry point is `DisplayConfigurator(ctk.CTk())` + `root.mainloop()`.
- `COLORS`, the frameless drag-header code and the help popup are duplicated in the controller and the
  configurator; keep them in sync when changing the look. The accent is `#389379` in both.
- All windows use `overrideredirect(True)`; there is no native title bar, so every window needs its own
  Quit button and drag handle.
- The Lottie placeholder loads `lottie.min.js` from cdnjs, so viewers without internet fall back to the
  static spinner only if `loading.json` is absent.
