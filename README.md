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
  * **Arbe radar** - the 4D imaging radar's detections, taken from its
    ROS 2 `PointCloud2` topic or from a recorded cloud.
  * **Inertial (IMU / INS)** - a serial unit streaming ASCII lines, a
    ROS 2 `sensor_msgs/Imu` topic, or a recorded log.

Everything is stored locally in `~/.ouster_projects.json`, so the tree and
all per-sensor settings survive restarts and can be copied between
machines.

## Install

```bash
pip install ouster-sdk numpy matplotlib
# for camera sensors:
pip install opencv-python
# for serial inertial sensors:
pip install pyserial
# for network-camera settings (IP, MTU, bitrate, GOP):
pip install onvif-zeep
# optional, only for "Export to MCAP":
pip install mcap mcap-protobuf-support foxglove-schemas-protobuf protobuf
```

Arbe radar sensors need a ROS 2 environment on `PATH` (`rclpy`,
`sensor_msgs` and the `ros2` CLI) only for their live topic and their
parameters - recordings replay with no ROS installed at all. Start the app
from a shell where you have already sourced ROS 2:

```bash
source /opt/ros/humble/setup.bash    # or your distro
python3 ouster_gui.py
```

`ouster-sdk`, `opencv-python`, `pyserial`, `onvif-zeep` and ROS 2 are all
optional: without one of them the app still runs, and only the matching
sensor type - or, for `onvif-zeep`, the network-camera panel - reports that
its package is missing.

On Linux, reading a serial IMU usually needs your user in the `dialout`
group:

```bash
sudo usermod -aG dialout "$USER"   # log out and back in
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
| **Edit** | Rename / change the details of the selected entry - for a sensor: type, name, address, model, serial and notes |
| **Delete** | Remove the entry and everything under it (from the app only - the sensors themselves are never touched) |

The projects list shows how much equipment and how many sensors each
project holds; the sensors list shows each sensor's type, address, model,
saved settings and when the app last talked to it.

Changing a sensor's type in **Edit** resets its settings to that type's
defaults, because a lidar and a camera keep different ones.

**⇄ Compare** puts the settings saved for the selected sensor next to the
ones it is actually running - the same table the **Compare with ...**
button on every dashboard opens. See below.

### 3D mount layout

Sensors are not just attached to a piece of equipment, they sit somewhere
on it. Each sensor carries a **mount** - `x`, `y`, `z` in metres and
`roll`, `pitch`, `yaw` in degrees - and each equipment item carries a body
size, so the whole rig can be drawn to scale.

**⬔ 3D layout**, on the equipment list and on the sensors list, opens the
layout window for that equipment:

* The equipment is drawn from its type: a vehicle gets a body, a cabin and
  four wheels, a drone gets arms and rotor discs, a mast gets its legs, a
  gantry its posts and beam, a bench its top and legs.
* Each sensor is a coloured marker - blue lidar, green camera, orange
  radar, purple inertial - with an arrow showing where it looks, and its
  name beside it.
* Drag to turn the view, or pick one of **Isometric / Top / Side / Front /
  Rear**. Click a sensor to select it.
* Selecting a sensor - in the list or in the 3D view - fills the
  **SELECTED SENSOR** panel with its real data: name, type, address,
  model, serial, when the app last reached it, its settings in its own
  terms (`1024x10, ports 7502/7503`, `1920x1080 @ 30 fps`, the radar's
  topic, the unit's port and baud) and its **real, measured pose** with
  the date it was measured.
* Each sensor has **two poses**, kept apart on purpose:

  | | Where it is edited | What it is for |
  | --- | --- | --- |
  | **Display position** | **⚙ Config** (and the **Place at** presets) | where the sensor is *drawn* in the 3D view |
  | **Real position** | **✎ Edit** in the SELECTED SENSOR panel | where the sensor *actually is*, as measured - the numbers used for real calibration |

  The real pose never moves the drawing, and Config never touches the
  measurement. **Edit** opens the six real values (x / y / z in metres,
  roll / pitch / yaw in degrees); **Save** stores them with the date they
  were measured, **Cancel** (or Esc) puts them back. A box left empty
  means *not measured* - a partial survey is fine, and the panel shows
  `-` for the missing values or `not measured` when there are none. Values
  must be numbers; anything else is refused with nothing written. Saving
  without a change keeps the original measurement date.

  Name, address, model and serial stay read-only in the panel - they are
  changed from the sensors list (**Edit** there) - as do type and
  settings (the sensor's dashboard) and last contact (the sensor itself).
* Moving to another sensor with unsaved edits - in the list or by
  clicking in 3D - asks first: **Yes** saves and moves on, **No**
  discards and moves on, **Cancel** stays on the sensor being edited.
  With nothing changed, it just moves.

Everything that changes the *drawing* lives behind **⚙ Config...**, which
opens a separate window with the equipment's size, the **3D model**
button, and the display position fields and **Place at** presets for
whichever sensor is selected. It is not modal, so the 3D view updates as
values are applied, it retargets itself when the selection changes, and
asking again focuses the one that is open rather than stacking another.

The frame is **x forward, y left, z up**, with the origin on the ground at
the centre of the equipment's footprint - the convention the sensor
extrinsics of a vehicle rig are usually written in.

To place a sensor, select it, press **Config...**, and either type `x`,
`y`, `z`, `roll`, `pitch`, `yaw` and press **Apply position**, or pick a
spot from **Place at**: front bumper, roof front / centre / rear, rear, left side, right
side or underside. The presets are worked out from the body's size, so
they follow it when the size changes. Positions are saved to the project
as soon as they are applied.

In the same window, **Length / width / height** set the body size in
metres; **Apply size** redraws the equipment and moves the presets with it. Equipment saved
before this existed gets a default size from its type, and sensors get a
mount at the origin.

#### Using your own model (STL)

**3D model...** replaces the built-in shape with an **STL** file - binary
or ascii, both read by a parser built into the app, with no extra package
to install. STL is the format every CAD tool exports and the only one
supported; OBJ, STEP, glTF and the rest are not read.

| Field | What it does |
| --- | --- |
| **STL file** | the path, or **Browse...** |
| **Scale to the equipment size** | on: the model is scaled uniformly to fit the length / width / height and stood on the ground - the usual case, since STL carries no units. Off: its own coordinates are used, times **scale** |
| **Orientation** | `as exported`, `Y up -> Z up`, `Z forward -> X forward` or `turn 180°`, which just fill in roll / pitch / yaw |
| **roll / pitch / yaw** | degrees, applied before scaling |
| **offset x / y / z** | metres, applied last |

Scaling is always uniform, so a model never comes out stretched: the
tightest of the three body dimensions decides the factor. A 2 x 1 x 0.5 m
model in a 4.8 x 1.9 x 1.6 m body ends up 3.8 x 1.9 x 0.95 m - the width
binds.

Models heavier than 20 000 triangles are thinned by taking every nth
triangle, which keeps the view interactive; the panel reports both counts
(`van.stl - 84 120 triangles, showing 20 030`). A parsed file is cached
until it changes on disk or the model is re-applied.

If the file is missing or unreadable, the layout falls back to the
built-in shape and says so in the panel and the log, so a moved file
never breaks a project. **Remove model** goes back to the built-in shape
deliberately.

Sensor markers, arrows and labels are drawn on top of the model exactly as
they are on the built-in shape.

### Baseline (legacy) settings

When a sensor is created, its settings are frozen as a **baseline** - the
values it can always be taken back to. The baseline is a copy, so tuning
the sensor afterwards never touches it. Sensors saved before this existed
adopt their current settings as their baseline the first time the project
is opened.

Every dashboard has a **BASELINE (LEGACY) SETTINGS** panel showing when
the baseline was frozen, with three actions:

| Action | What it does |
| --- | --- |
| **⇄ Compare with baseline** | the same table as below, with the baseline in the right column |
| **↺ Restore baseline settings** | takes the differing settings back to the baseline, after listing them |
| **✎ Update baseline to current...** | replaces the baseline - **behind a warning** |

Restoring changes the project only; nothing reaches the sensor until you
push. It lists exactly what will change and does nothing if the settings
already match.

Replacing the baseline is the one destructive action here, so it asks
first, with a warning icon, **No** preselected, and the count of settings
that would change. The previous baseline is written to the log before it
is replaced, so the old values are still on record even after the swap.

Changing a sensor's **type** resets its settings and takes a fresh
baseline, since the old one describes a different kind of device. A
baseline left over from another type cannot be restored - the panel says
so and disables restoring, while still letting you replace it.

### Comparing a sensor with the project

Every dashboard has a **⇄ Compare with ...** button, and the sensors list
has **⇄ Compare** for the selected row. Both read the sensor and open one
table:

| Setting | Saved in project | On the sensor | |
| --- | --- | --- | --- |
| Lidar mode | 1024x10 | 2048x10 | differs |
| Lidar port | 7502 | 7502 | same |
| UDP profile | RNG19_RFL8_SIG16_NIR16 | RNG19_RFL8_SIG16_NIR16_DUAL | differs |

Rows that differ are orange, and the header counts them ("5 of 9 settings
differ"). `not set` means the project has no value for that setting,
`not reported` means the sensor does not expose it; settings that are
blank on both sides are left out. Values are compared the way a person
would - `1` and `1.0` match, and so do `H264` and `h264`.

**Copy the sensor's values into the project** adopts just the differing
rows, so it is a targeted pull rather than an all-or-nothing one.
**Refresh** reads the sensor again. The differences also go to the log, so
there is a record of what was found.

What gets read depends on the type:

* **Ouster lidar** - `get_config`: mode, timestamp and operating mode,
  signal multiplier, UDP profile, azimuth window and ports.
* **Camera** - the capture's resolution, frame rate, pixel format and
  image controls, plus the device's own IP, MTU, bitrate, GOP and encoding
  when ONVIF is available and a password is already held for this session.
  A network camera whose video stream cannot be opened still gets compared
  over ONVIF, with a note in the log.
* **Arbe radar** - the driver's ROS 2 parameters, from `ros2 param dump`.
* **Inertial (IMU / INS)** - the column layout the unit is really sending,
  derived from two seconds of its output, or the driver's ROS 2
  parameters. Adopting the layout overwrites the saved one.

A sensor set to replay a recording has nothing live to compare against, so
the app says so instead of opening an empty table. The same goes for a
busy serial port: stop the stream first.

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

**Network camera settings (ONVIF)**

The boxes above change the *local capture*. A network camera's own
settings - address, MTU, bitrate and GOP - live on the device, and the
**NETWORK CAMERA (ONVIF)** panel edits them over ONVIF, the standard that
covers exactly those:

| Setting | What it does | ONVIF operation |
| --- | --- | --- |
| **IP / CIDR** | `192.168.1.64/24`, or `dhcp` to go back to automatic | `SetNetworkInterfaces` |
| **Gateway** | default IPv4 gateway | `SetNetworkDefaultGateway` |
| **MTU** | 576-9216; raise it for jumbo frames on a dedicated link | `SetNetworkInterfaces` |
| **Bitrate** | encoder ceiling in kbit/s | `SetVideoEncoderConfiguration` |
| **GOP** | frames between key frames (`GovLength`) | `SetVideoEncoderConfiguration` |
| **Encoding** | H264 / H265 / JPEG | `SetVideoEncoderConfiguration` |

* **Pull from camera (ONVIF)** reads all of them - plus the encoder's
  resolution and frame rate - into the form and the project.
* **Push to camera (ONVIF)** writes only the boxes you filled in, after a
  confirmation listing them, and logs one line per operation. Encoder
  settings are sent with `ForcePersistence`, so they survive a reboot.
  Changing the IP drops the connection; the confirmation says so.

The **password is never written to the project file**. It is asked for the
first time it is needed and kept in memory for that session only;
**Forget stored password** clears it. The username, port and the settings
themselves are stored.

Set the camera source to the device's address or RTSP URL for this panel
to work - a USB index like `0` has no ONVIF endpoint, and the app says so
rather than trying.

**Recording**

* **Start Recording** - writes the live frames to MP4 (`mp4v`) or AVI
  (`MJPG`), chosen from the file extension. Recording happens inside the
  capture thread that already owns the device, so it does not open the
  camera a second time; if no preview is running, one is started first.

**One camera on several screens**

**Open on another screen** mirrors the live image into its own window.
Drag it to a second monitor and press **F11** (or double-click) for full
screen, **Esc** to leave it. Open it as many times as you have screens -
every window draws from the same capture thread, so the camera is never
opened twice, and each shows the frame number, size and a `● REC` marker
while recording. The windows close when you leave the dashboard.

### Arbe radar dashboard

Arbe's 4D imaging radar is integrated through the interfaces its driver
exposes on the host, not through a proprietary library:

* **ROS 2 topic** - the app subscribes to the driver's `PointCloud2`
  (default `/arbe/rviz/pointcloud`) with `rclpy`, using best-effort or
  reliable QoS and the `ROS_DOMAIN_ID` you set. `List ROS 2 topics` runs
  `ros2 topic list` so you can find the right one.
* **Recording file** - replays a recorded cloud: `.npz`, structured
  `.npy`, `.csv` with a header row, or ascii `.pcd`. A `frame` column
  splits the file into frames and they play back in order (with optional
  looping), so the dashboard is fully usable with no radar and no ROS.

The cloud is read field by field, so whatever the driver names things,
`x`/`y`/`z`, `doppler` (also `velocity`, `radial_velocity`, `range_rate`),
`snr` and `power` (also `intensity`, `rcs`) are recognized, and `range` is
computed when it is not published.

**Display** - a bird's-eye view with forward `x` up the screen and lateral
`y` across it (positive to the left, as in the vehicle frame). Points are
coloured by doppler on a diverging map - approaching and receding are
immediately distinguishable - or by SNR, power, range or height. **Max
range** and **Min SNR** filter the cloud, and the panel above the plot
reports the detection count, maximum range, and the doppler and SNR spans
of the current frame. **Save current frame** writes the visible frame to
`.csv` or `.npz`.

**Parameters** - the radar's own settings live in the driver's ROS 2
parameters, so the app edits them as `name: value` lines:

* **Pull from radar** runs `ros2 param dump <node>` and fills the box.
* **Push to radar** runs `ros2 param set <node> <name> <value>` for every
  line, after a confirmation listing them, and logs each result
  individually so a rejected parameter is visible.

Both accept `name: value` and `name=value`, and skip comments and the YAML
header lines that `ros2 param dump` emits.

> The ROS 2 topic and parameter paths were written against the standard
> ROS 2 interfaces and are exercised here with synthetic `PointCloud2`
> messages and a stubbed `ros2` CLI; they have not been run against a
> physical Arbe unit. If your driver publishes on a different topic or
> names its fields differently, only the topic string and the field
> aliases need adjusting.

### Inertial (IMU / INS) dashboard

Three sources, all producing the same sample:

* **Serial port** - `/dev/ttyUSB0`, `COM4` and friends, at any of the
  usual baud rates. Units stream ASCII lines, so a **column layout** says
  what each column is: `t,ax,ay,az,gx,gy,gz`, using any of `t`, `ax`,
  `ay`, `az`, `gx`, `gy`, `gz`, `mx`, `my`, `mz`, `roll`, `pitch`, `yaw`,
  and `-` to skip a column. Plain CSV, whitespace-separated output and
  NMEA-style sentences (`$VNYMR,...*6A` - talker word and checksum
  dropped) all parse; lines that are not numeric data are skipped and
  reported in the log.
* **ROS 2 topic** - a `sensor_msgs/Imu` topic. Angular rates are converted
  from rad/s to deg/s and the orientation quaternion to roll/pitch/yaw, so
  it plots on the same axes as a serial unit.
* **Recording file** - a `.csv` with a header row or a `.npz`, replayed at
  the rate its `t` column implies, with optional looping.

**Display** - stacked traces for acceleration, angular rate, orientation
and magnetometer. Only the groups the device actually sends are drawn, and
a group appears as soon as its first sample arrives. **Samples shown** sets
the window length. The panel above lists the sample count, the measured
rate in Hz and the latest value of every field.

**Reading**

* **Pull from device** - on a serial port, listens for two seconds, prints
  the first raw lines to the log and *suggests a column layout* from the
  number of numeric columns, filling the layout box. On ROS 2, it runs
  `ros2 param dump` like the radar dashboard. Check a guessed layout
  against the device's manual before trusting the plots.
* **List serial ports** - enumerates the ports pyserial can see, with
  their descriptions.
* **Start Recording** - appends samples to a CSV as they arrive, with a
  header naming the columns the device sends.

**Writing**

* **Push to device** - on a serial port, writes each command line to the
  port with the configured line ending (CRLF/LF/CR/none) and logs the
  device's reply, so a rejected command is visible - e.g. VectorNav's
  `$VNWRG,07,100`. On ROS 2, each `name: value` line goes through
  `ros2 param set`.

The port can only be open once, so pull and push ask you to stop the
stream first rather than fighting the reader for the device.

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
                "exposure": "",
                "onvif_port": "80",
                "onvif_user": "admin",
                "net_ip": "192.168.1.64/24",
                "net_gateway": "192.168.1.1",
                "mtu": "9000",
                "bitrate": "8192",
                "gop": "25",
                "encoding": "H265"
              },
              "network": {"static_ip": "", "gateway": ""}
            },
            {
              "id": "c93f5a2b7e18",
              "name": "front radar",
              "kind": "arbe",
              "host": "/data/arbe/drive-07.npz",
              "model": "Arbe Phoenix",
              "last_seen": "",
              "config": {
                "source_type": "ROS 2 topic",
                "topic": "/arbe/rviz/pointcloud",
                "domain_id": "0",
                "qos": "best_effort",
                "node": "/arbe_driver",
                "color_by": "doppler",
                "max_range": "150",
                "min_snr": "",
                "parameters": "framerate: 10\ntx_power: 3"
              },
              "network": {"static_ip": "", "gateway": ""}
            },
            {
              "id": "d05c8e1f6a34",
              "name": "nav IMU",
              "kind": "imu",
              "host": "/dev/ttyUSB0",
              "model": "VectorNav VN-100",
              "last_seen": "",
              "config": {
                "source_type": "Serial port",
                "port": "/dev/ttyUSB0",
                "baud": "115200",
                "line_ending": "CRLF",
                "layout": "t,ax,ay,az,gx,gy,gz",
                "topic": "/imu/data",
                "domain_id": "0",
                "qos": "best_effort",
                "node": "/imu_driver",
                "commands": "$VNWRG,07,100",
                "window": "600"
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

Each sensor also carries a `calibration` block - `{"x", "y", "z", "roll",
"pitch", "yaw"}`, empty strings where nothing was measured - and a
`calibrated` date: the real, surveyed pose, separate from the `mount`
that only positions it in the 3D view. Records written before this get an
empty calibration on load.

Each equipment item carries a `body` block - `{"length", "width",
"height"}` in metres - and a `model` block - `{"path", "fit", "scale",
"roll", "pitch", "yaw", "dx", "dy", "dz"}` for an optional STL - and each
sensor a `mount` block - `{"x", "y", "z",
"roll", "pitch", "yaw"}` - giving its place on that equipment.

Each sensor also carries a `legacy` block - `{"kind", "created", "note",
"config"}` - holding the settings frozen when it was created.

A sensor without a `kind` is read as an Ouster lidar, and one without a
`legacy` block adopts its current settings as its baseline, so files
written by earlier versions load unchanged.

The file is written atomically (via a `.tmp` file and `os.replace`), so an
interrupted save cannot corrupt the tree. Back it up or copy it to another
machine to move a fleet definition around.

Sensor profiles saved by version 1.x of this app
(`~/.ouster_lidar_gui.json`) are imported once, on first run, into a
project called **Imported**.

## Compatibility

* `ouster-sdk` >= 1.0 (`ouster.sdk.core`) and < 1.0 (`ouster.sdk.client`).
* OpenCV 4.x / 5.x (`opencv-python`, or `opencv-python-headless`).
* ONVIF Profile S cameras, through `onvif-zeep`.
* Linux (developed on Ubuntu 24.04), macOS and Windows. On Windows the
  title bar is switched to dark mode where the OS supports it.
