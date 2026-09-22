# Backup V1 - Sensor Fleet Manager 2.4.0

A frozen copy of the application as it stood on 2026-09-22, kept so this
version can always be read or restored even after the working files move
on.

| | |
| --- | --- |
| App version | 2.4.0 |
| Taken on | 2026-09-22 |
| Source commit | `bce5eb7` - *Add network-camera settings (ONVIF) and extra live views* |
| Branch | `claude/file-this-nwnu7c` |
| Git tag | `V1` |

## What is in here

| File | Lines | SHA-256 |
| --- | --- | --- |
| `ouster_gui.py` | 5697 | `b0919eaa13b5f01155f397ca3039e987bf0e978dd6cd8a20c98d37f3911b09f8` |
| `README.md` | 470 | `bb122c1479a42fb4ee509efa2771502d19f2329602dc9205797a3b68d90f2dd9` |
| `requirements.txt` | 24 | `6621f3122f34ea7e60769504a2ff60a0b9d2aa93c868dfef8d7dcb790a9a3381` |

`LICENSE` is not copied - it covers the whole repository and is unchanged
at the root.

## What this version contains

Four sensor types under a Project → Equipment → Sensor hierarchy, stored
in `~/.ouster_projects.json`:

* **Ouster lidar** - config pull/push, status and telemetry, static IP,
  live 2D field images, 3D viewer, PCAP recording and playback, MCAP
  export.
* **Camera** - USB / RTSP / HTTP through OpenCV, with capture settings,
  preview, snapshots, recording, the device's own IP / MTU / bitrate / GOP
  over ONVIF, and extra live views for other monitors.
* **Arbe radar** - ROS 2 `PointCloud2` or recorded clouds, bird's-eye view
  coloured by doppler, and driver parameters through `ros2 param`.
* **Inertial (IMU / INS)** - serial ASCII, ROS 2 `sensor_msgs/Imu` or a
  recorded log, with live traces and serial commands.

## Restoring this version

Copy the files back over the working ones:

```bash
cp backup/V1/ouster_gui.py backup/V1/README.md backup/V1/requirements.txt .
```

Or read it straight out of git, without touching the working tree:

```bash
git show V1:ouster_gui.py > /tmp/ouster_gui_v1.py   # from the tag
git show bce5eb7:ouster_gui.py | less               # from the commit
```

Check a copy against the table above with:

```bash
sha256sum backup/V1/*
```

## Adding a later version

Snapshot the working files into a new folder beside this one and tag the
commit, so `backup/` keeps one directory per released version:

```bash
mkdir -p backup/V2
cp ouster_gui.py README.md requirements.txt backup/V2/
git add backup/V2 && git commit -m "Back up V2"
git tag -a V2 -m "Sensor Fleet Manager <version>"
git push origin <branch> --tags
```
