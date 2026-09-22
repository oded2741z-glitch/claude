# Backup V2 - Sensor Fleet Manager 2.6.0

A frozen copy of the application as it stood on 2026-09-22, kept so this
version can always be read or restored even after the working files move
on.

| | |
| --- | --- |
| App version | 2.6.0 |
| Taken on | 2026-09-22 |
| Source commit | `bdcb2a4` - *Freeze each sensor's settings as a restorable baseline* |
| Branch | `claude/file-this-nwnu7c` |
| Git tag | `V2` (local only - see below) |
| Previous version | [`V1`](../V1/VERSION.md) - 2.4.0 |

The `V2` tag was created locally, but this environment's GitHub
credentials are not allowed to push tags (the push is refused with
HTTP 403), so the tag is not on the remote. Push it from a checkout with
tag permission:

```bash
git push origin V2
```

Nothing depends on the tag: the folder below is the backup, and the source
commit `bdcb2a4` identifies the same state.

## What is in here

| File | Lines | SHA-256 |
| --- | --- | --- |
| `ouster_gui.py` | 6300 | `1a57d38d14e7e187f8513b38b9e64c255f42c932668c15be3e914293aeffa6f2` |
| `README.md` | 550 | `2c5f6449aabbd4fbca66dc816746e42b5ac01961cc43a41a966ebb56a2ffd117` |
| `requirements.txt` | 24 | `6621f3122f34ea7e60769504a2ff60a0b9d2aa93c868dfef8d7dcb790a9a3381` |

`LICENSE` is not copied - it covers the whole repository and is unchanged
at the root.

## What changed since V1 (2.4.0)

* **Compare a sensor with the project** - every dashboard, and the sensors
  list, can read a sensor and show the settings saved in the project next
  to the live ones, marking each row same / differs / not set / not
  reported, and adopt just the rows that differ. Per kind it reads
  `get_config` (Ouster), the capture and ONVIF settings (camera),
  `ros2 param dump` (Arbe) or the column layout the unit is really sending
  (inertial).
* **Baseline (legacy) settings** - a sensor's settings are frozen when it
  is created, can always be restored, and replacing that baseline is
  guarded by a warning that logs the outgoing values first.

Everything V1 had is still here: the Project → Equipment → Sensor tree and
the four sensor types (Ouster lidar, camera with ONVIF and multi-screen
views, Arbe radar, inertial IMU / INS).

## Restoring this version

Copy the files back over the working ones:

```bash
cp backup/V2/ouster_gui.py backup/V2/README.md backup/V2/requirements.txt .
```

Or read it straight out of git, without touching the working tree:

```bash
git show bdcb2a4:ouster_gui.py | less                  # from the commit
git show HEAD:backup/V2/ouster_gui.py | less           # from the snapshot
git show V2:ouster_gui.py | less                       # once the tag is pushed
```

Check a copy against the table above with:

```bash
sha256sum backup/V2/*
```

## Adding a later version

Snapshot the working files into a new folder beside this one and tag the
commit, so `backup/` keeps one directory per released version:

```bash
mkdir -p backup/V3
cp ouster_gui.py README.md requirements.txt backup/V3/
git add backup/V3 && git commit -m "Back up V3"
git tag -a V3 -m "Sensor Fleet Manager <version>"
git push origin <branch> --tags
```
