# Backup V3 - Sensor Fleet Manager 2.13.0

A frozen copy of the application as it stood on 2026-09-23, kept so this
version can always be read or restored even after the working files move
on.

| | |
| --- | --- |
| App version | 2.13.0 |
| Taken on | 2026-09-23 |
| Source commit | `453e8ee` - *Add a 2D drag mode to the mount layout* |
| Branch | `claude/file-this-nwnu7c` |
| Git tag | `V3` (local only - see below) |
| Previous version | [`V2`](../V2/VERSION.md) - 2.6.0 |

The `V3` tag was created locally, but this environment's GitHub
credentials are not allowed to push tags (the push is refused with
HTTP 403), so the tag is not on the remote. Push it from a checkout with
tag permission:

```bash
git push origin V3
```

Nothing depends on the tag: the folder below is the backup, and the source
commit `453e8ee` identifies the same state.

## What is in here

| File | Lines | SHA-256 |
| --- | --- | --- |
| `ouster_gui.py` | 7888 | `45c6b9d992319abe2b32ecff9cef38b47cc67eb7810e5af9f3b23bbad45327a5` |
| `README.md` | 697 | `be58d23b0cd25c1b513e3e742294c0c45c88f244dfc5372ab14d24a0daa06d1d` |
| `requirements.txt` | 24 | `6621f3122f34ea7e60769504a2ff60a0b9d2aa93c868dfef8d7dcb790a9a3381` |

`LICENSE` is not copied - it covers the whole repository and is unchanged
at the root.

## What changed since V2 (2.6.0)

* **3D mount layout** - each equipment item has a body size and each
  sensor a mount (x / y / z, roll / pitch / yaw) in an x-forward, y-left,
  z-up frame. **⬔ 3D layout** draws the equipment by type (vehicle, drone,
  mast, gantry, bench) with every sensor as a coloured marker and a
  viewing arrow, from Isometric / Top / Side / Front / Rear.
* **STL models** - the built-in shape can be replaced by a binary or
  ascii STL file, scaled uniformly to the body size or placed by hand,
  with a fall-back to the built-in shape if the file goes missing.
* **Selected sensor panel** - the sensor's real data (identity, last
  contact, settings) next to the drawing. Name, address, model and serial
  are read-only there and changed from the sensors list.
* **Display pose vs real pose** - **⚙ Config** holds the display position
  (what the drawing shows) plus the body size and the model; the panel's
  **✎ Edit** holds the real, measured pose used for calibration, stored
  with its measurement date and never moving the drawing. Unsaved edits
  are guarded when moving to another sensor.
* **2D drag mode** - on a Top / Side / Front / Rear plane, a sensor is
  dragged with the mouse to set its display position, snapped to a
  free / 1 / 5 / 10 cm grid; Esc cancels, release saves.

Everything V2 had is still here: the Project → Equipment → Sensor tree,
the four sensor types (Ouster lidar, camera with ONVIF and multi-screen
views, Arbe radar, inertial IMU / INS), comparing a sensor with the
project, and the baseline (legacy) settings.

## Restoring this version

Copy the files back over the working ones:

```bash
cp backup/V3/ouster_gui.py backup/V3/README.md backup/V3/requirements.txt .
```

Or read it straight out of git, without touching the working tree:

```bash
git show 453e8ee:ouster_gui.py | less                  # from the commit
git show HEAD:backup/V3/ouster_gui.py | less           # from the snapshot
git show V3:ouster_gui.py | less                       # once the tag is pushed
```

Check a copy against the table above with:

```bash
sha256sum backup/V3/*
```

## Adding a later version

Snapshot the working files into a new folder beside this one and tag the
commit, so `backup/` keeps one directory per released version:

```bash
mkdir -p backup/V4
cp ouster_gui.py README.md requirements.txt backup/V4/
git add backup/V4 && git commit -m "Back up V4"
git tag -a V4 -m "Sensor Fleet Manager <version>"
git push origin <branch> --tags
```
