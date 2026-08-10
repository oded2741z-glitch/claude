# Sensor Fleet Manager (Ouster lidars + cameras)

A Tkinter desktop app that organizes sensors into a simple three-level
hierarchy and lets you read data from each one or push settings to it.

```
Project  ->  Equipment  ->  Sensor  ->  Sensor dashboard
```

* **Project** - a site, a customer, a survey campaign, a research program.
* **Equipment** - the platform the sensors are mounted on: a vehicle, a
  drone, a mast, a robot, a fixed installation.
* **Sensor** - one entry per physical device, with its own saved settings.
  Each sensor has a **type**, which decides the dashboard it opens:
  * **Ouster lidar** - reached by hostname or IP through `ouster-sdk`.
  * **Camera** - a USB camera (`0`, `1`, `/dev/video0`) or a network
    camera (`rtsp://...`, `http://.../video.mjpg`) through OpenCV.

Everything is stored locally in `~/.ouster_projects.json`, so the tree and
all per-sensor settings survive restarts and can be copied between
machines.

## Install

```bash
pip install ouster-sdk numpy matplotlib
# for camera sensors:
pip install opencv-python
# optional, only for "Export to MCAP":
pip install mcap mcap-protobuf-support foxglove-schemas-protobuf protobuf
```

Both `ouster-sdk` and `opencv-python` are optional: without one of them
the app still runs, and only the matching sensor type reports that its
package is missing.

On Debian/Ubuntu, Tkinter comes from the system packages:

```bash
sudo apt install python3-tk
```

## Run

```bash
python3 ouster_gui.py
```

The app opens on the **Projects** screen. Create a project, open it, create
equipment, open it, create a sensor (choosing its type), then open the
sensor to reach its dashboard. The breadcrumb at the top
(`Projects › Highway 6 › Van #3 › front-left OS1`) is clickable, and
`←  Back` goes up one level.

You can always build and edit the project tree; only the actions that talk
to hardware need the matching package installed.

## Screens

### Projects / Equipment / Sensors

Each level is a list with the same four actions:

| Action | What it does |
| --- | --- |
| **Open** | Go one level deeper (also: double-click a row, or press Enter) |
| **New ...** | Create an entry at this level |
| **Edit** | Rename / change the details of the selected entry |
| **Delete** | Remove the entry and everything under it (from the app only - the sensors themselves are never touched) |

The projects list shows how much equipment and how many sensors each
project holds; the sensors list shows each sensor's type, address, model,
saved settings and when the app last talked to it.

Changing a sensor's type in **Edit** resets its settings to that type's
defaults, because a lidar and a camera keep different ones.

### Ouster lidar dashboard

**Reading data from the sensor**

* **Pull from sensor** - reads the live configuration (lidar mode,
  timestamp mode, operating mode, signal multiplier, azimuth window, UDP
  profile, UDP ports) into the form and saves it into the project. Values
  the sensor reports that are not in the app's lists are logged and left
  alone rather than silently overwritten.
* **Get Status** - sensor info, telemetry (voltages, currents,
  temperatures), alerts, and the shot-limiting / thermal flags carried on
  the most recent frame.
* **Web dashboard** - opens the sensor's own web page in a browser.
* **Start Stream** - live scans rendered as destaggered 2D images:
  RANGE, SIGNAL, REFLECTIVITY, NEAR_IR. Click an image to enlarge it,
  click again (or use the View buttons) to return to the 2x2 grid.
  Metadata (product line, serial, firmware, mode, resolution) appears
  above the images, and the model / serial are filled into the sensor
  record the first time they are seen.
* **Open 3D Viewer** - launches Ouster's official point-cloud viewer
  (`ouster-cli source <host> viz`) in a separate window. The 2D stream is
  stopped first, because only one process can bind the UDP data port.

**Writing data to the sensor**

* **Push to sensor** - writes the form's configuration to the sensor with
  `set_config(..., udp_dest_auto=True)` after a confirmation dialog that
  lists exactly what will be applied. Tick **Persist** to keep the settings
  across sensor reboots. The sensor reinitializes and stops sending data
  for a few seconds.
* **Save to project** - stores the form in the project without contacting
  the sensor, so you can prepare a configuration offline and push it later.
* **Network / IP...** - shows the sensor's live network configuration,
  keeps a static IP / gateway in the project, and can **push the static
  IP** to the sensor or **revert it to DHCP / link-local**. Both change
  the sensor's address, so you have to reconnect afterwards.
* **Reinitialize** - restarts the sensor's data path.

**Recording and playback**

* **Start Recording** - `ouster-cli source <host> save <file>.pcap`, with a
  file name pre-filled from the sensor's name and the current time.
* **Play Recording (PCAP / OSF)** - replays a recording through the same
  2D viewer, so the app is fully usable without a physical sensor.
  Tick **Loop playback** to repeat.
* **Export to MCAP (Foxglove)** - converts a PCAP/OSF recording to an MCAP
  file containing `foxglove.PointCloud` messages on `/ouster/points`, plus
  IMU samples on `/ouster/imu` when the source is a PCAP.

### Camera dashboard

The camera source is either a device index (`0`, `1`), a device path
(`/dev/video0`) or a URL (`rtsp://user:pass@host/stream`,
`http://host/video.mjpg`). A capture backend can be forced (`v4l2`,
`ffmpeg`, `gstreamer`, `dshow`, `avfoundation`) when `auto` picks the
wrong one.

**Reading from the camera**

* **Probe camera** - opens the camera briefly and reports the backend,
  resolution, frame rate and pixel format without starting a preview.
* **Pull from camera** - reads the current settings (resolution, FPS,
  FOURCC, brightness, contrast, saturation, gain, exposure) into the form
  and the project. Properties the backend does not expose report `-1` in
  OpenCV and are skipped rather than saved.
* **Start Preview** - live image in the right-hand panel. The frame number
  and size are shown above it, plus `● REC` while recording.
* **Snapshot** - writes the current frame to PNG/JPEG. It comes from the
  running preview when there is one, otherwise the camera is opened just
  for the grab.
* **Play video file** - replays an MP4/AVI/MKV/MOV/WEBM file through the
  same viewer, so the dashboard is usable without a camera attached.
  Tick **Loop playback** to repeat.

**Writing to the camera**

* **Push to camera** - applies the form to the camera. While a preview is
  running the settings are applied to that live capture, so you see the
  result immediately; otherwise the camera is opened for the write.
* **Save to project** - stores the form without opening the camera.

Cameras are free to ignore a setting - a webcam that has no `1920x1080`
mode will quietly stay at `1280x720`. After a push the app reads the
settings back, writes what the camera actually kept into the form and the
project, and logs every value that did not stick. Leave a box empty to
keep whatever the camera is already using.

**Recording**

* **Start Recording** - writes the live frames to MP4 (`mp4v`) or AVI
  (`MJPG`), chosen from the file extension. Recording happens inside the
  capture thread that already owns the device, so it does not open the
  camera a second time; if no preview is running, one is started first.

## Data file

`~/.ouster_projects.json`:

```json
{
  "version": 2,
  "projects": [
    {
      "id": "3f2a1c9b8d4e",
      "name": "Highway 6 survey",
      "site": "Netivei Israel",
      "notes": "mobile mapping",
      "created": "2026-08-08 18:09",
      "equipment": [
        {
          "id": "7c1d0e5a2b93",
          "name": "Van #3",
          "type": "Vehicle",
          "serial": "VAN-003",
          "sensors": [
            {
              "id": "a4b8c2d16e07",
              "name": "front-left OS1",
              "kind": "ouster",
              "host": "os-992001.local",
              "model": "OS1-128",
              "last_seen": "2026-08-08 18:12",
              "config": {
                "lidar_mode": "1024x10",
                "timestamp_mode": "TIME_FROM_INTERNAL_OSC",
                "operating_mode": "NORMAL",
                "signal_multiplier": "1",
                "udp_profile": "(leave unchanged)",
                "az_start": "0",
                "az_end": "360",
                "lidar_port": "7502",
                "imu_port": "7503",
                "persist": false
              },
              "network": {"static_ip": "", "gateway": ""}
            },
            {
              "id": "b71e3c8a04d2",
              "name": "cabin camera",
              "kind": "camera",
              "host": "0",
              "model": "Logitech C920",
              "last_seen": "",
              "config": {
                "backend": "auto",
                "fourcc": "MJPG",
                "width": "1280",
                "height": "720",
                "fps": "30",
                "brightness": "",
                "contrast": "",
                "saturation": "",
                "gain": "",
                "exposure": ""
              },
              "network": {"static_ip": "", "gateway": ""}
            }
          ]
        }
      ]
    }
  ]
}
```

A sensor without a `kind` is read as an Ouster lidar, so files written by
earlier versions load unchanged.

The file is written atomically (via a `.tmp` file and `os.replace`), so an
interrupted save cannot corrupt the tree. Back it up or copy it to another
machine to move a fleet definition around.

Sensor profiles saved by version 1.x of this app
(`~/.ouster_lidar_gui.json`) are imported once, on first run, into a
project called **Imported**.

## Compatibility

* `ouster-sdk` >= 1.0 (`ouster.sdk.core`) and < 1.0 (`ouster.sdk.client`).
* OpenCV 4.x / 5.x (`opencv-python`, or `opencv-python-headless`).
* Linux (developed on Ubuntu 24.04), macOS and Windows. On Windows the
  title bar is switched to dark mode where the OS supports it.
