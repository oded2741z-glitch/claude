# Backup V4 (record) - Sensor Fleet Manager 2.15.0

A frozen copy of the application as it stood on 2026-09-26, kept so this
version can always be read or restored even after the working files move
on. Labelled **record**.

| | |
| --- | --- |
| App version | 2.15.0 |
| Label | record |
| Taken on | 2026-09-26 |
| Source commit | `1aa2425` - *Draw each sensor as a small 3D model of its kind* |
| Branch | `claude/file-this-nwnu7c` |
| Git tag | `V4-record` (local only - see below) |
| Previous version | [`V3`](../V3/VERSION.md) - 2.13.0 |

The `V4-record` tag was created locally, but this environment's GitHub
credentials are not allowed to push tags, so the tag is not on the remote.
Push it from a checkout with tag permission:

```bash
git push origin V4-record
```

Nothing depends on the tag: the folder below is the backup, and the source
commit `1aa2425` identifies the same state.

## What is in here

| File | Lines | SHA-256 |
| --- | --- | --- |
| `ouster_gui.py` | 8349 | `efe6707a5132566bea9c6aaa136f13b445402dde35a3faed99365b0718b29d25` |
| `README.md` | 738 | `d5670e1c53fe301410bb02bec77383b42ac4f223959c8b2eeab19b34aeea1b6b` |
| `requirements.txt` | 24 | `6621f3122f34ea7e60769504a2ff60a0b9d2aa93c868dfef8d7dcb790a9a3381` |

`LICENSE` is not copied - it covers the whole repository and is unchanged
at the root.

## What changed since V3 (2.13.0)

* **Simpler forms** - a project is a name and notes (no Site / customer),
  equipment a name, a type and notes (no Serial / asset ID). Values an
  older file holds stay in it, just no longer shown.
* **Editable equipment types** - **✎ Edit list** beside the Type box adds,
  renames (the equipment follows), reorders and removes types (refused
  while in use), resets to the built-in list, and picks the built-in shape
  each type is drawn as in the 3D layout.
* **Sensor models in the layout** - each sensor is a small 3D model of its
  kind (lidar drum, camera with lens, radar panel, IMU block) turned by its
  roll / pitch / yaw, in 3D and flattened in 2D drag mode, with a
  remembered **Sensor size** (real size / small / medium / large).

Everything V3 had is still here: the Project → Equipment → Sensor tree,
the four sensor types, compare and baseline, the 3D mount layout with STL
models, the real (calibration) pose and the 2D drag mode.

## Restoring this version

Copy the files back over the working ones:

```bash
cp backup/V4-record/ouster_gui.py backup/V4-record/README.md \
   backup/V4-record/requirements.txt .
```

Or read it straight out of git, without touching the working tree:

```bash
git show 1aa2425:ouster_gui.py | less                     # from the commit
git show HEAD:backup/V4-record/ouster_gui.py | less       # from the snapshot
git show V4-record:ouster_gui.py | less                   # once the tag is pushed
```

Check a copy against the table above with:

```bash
sha256sum backup/V4-record/*
```
