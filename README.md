# Ouster Lidar Fleet Manager

A Tkinter desktop app that organizes Ouster digital lidars into a simple
three-level hierarchy and lets you read data from each sensor or push
settings to it.

```
Project  ->  Equipment  ->  Ouster sensor  ->  Sensor dashboard
```

* **Project** - a site, a customer, a survey campaign, a research program.
* **Equipment** - the platform the lidars are mounted on: a vehicle, a
  drone, a mast, a robot, a fixed installation.
* **Sensor** - one entry per physical Ouster lidar (hostname or IP), with
  its own saved configuration and network settings.

Everything is stored locally in `~/.ouster_projects.json`, so the tree and
all per-sensor settings survive restarts and can be copied between
machines.

## Install

```bash
pip install ouster-sdk numpy matplotlib
# optional, only for "Export to MCAP":
pip install mcap mcap-protobuf-support foxglove-schemas-protobuf protobuf
```

On Debian/Ubuntu, Tkinter comes from the system packages:

```bash
sudo apt install python3-tk
```

## Run

```bash
python3 ouster_gui.py
```

The app opens on the **Projects** screen. Create a project, open it, create
equipment, open it, create a sensor, then open the sensor to reach its
dashboard. The breadcrumb at the top (`Projects › Highway 6 › Van #3 ›
front-left OS1`) is clickable, and `←  Back` goes up one level.

Without `ouster-sdk` installed the app still runs - you can build and edit
the project tree, but anything that talks to a sensor is disabled.

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
project holds; the sensors list shows each sensor's host, model, saved
lidar mode and when the app last talked to it.

### Sensor dashboard

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
            }
          ]
        }
      ]
    }
  ]
}
```

The file is written atomically (via a `.tmp` file and `os.replace`), so an
interrupted save cannot corrupt the tree. Back it up or copy it to another
machine to move a fleet definition around.

Sensor profiles saved by version 1.x of this app
(`~/.ouster_lidar_gui.json`) are imported once, on first run, into a
project called **Imported**.

## Compatibility

* `ouster-sdk` >= 1.0 (`ouster.sdk.core`) and < 1.0 (`ouster.sdk.client`).
* Linux (developed on Ubuntu 24.04), macOS and Windows. On Windows the
  title bar is switched to dark mode where the OS supports it.
