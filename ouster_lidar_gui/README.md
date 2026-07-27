# Ouster Digital Lidar — GUI Control & Visualization

A Python GUI application for controlling and visualizing an **Ouster Digital
Lidar** sensor, built on the official [ouster-sdk](https://pypi.org/project/ouster-sdk/) —
as demonstrated in Ouster's
[Digital Lidar SDK: Setup and Visualization](https://www.youtube.com/watch?v=m0ANVFunObU)
video. Designed for Ubuntu 24.04; also runs on Windows.

## Features

- **Sensor connection** by hostname or IP address (e.g. `os-122xxxxxxxxxx.local`)
- **Get Sensor Info** — opens the sensor's built-in web dashboard in your browser
- **Get Status** — temperature/voltage telemetry, alerts, run status and live
  shot-limiting / thermal state, shown in a pop-up window
- **Reinitialize** — restart the sensor's data path from the app
- **Network / IP** — view the sensor's network config, set a static IP
  (with optional gateway), or revert it to DHCP / link-local
- **Sensor configuration** — lidar mode (512x10 up to 2048x10), timestamp mode,
  operating mode (NORMAL/STANDBY), signal multiplier, UDP data profile,
  azimuth window (horizontal field of view), UDP ports, and a **Persist**
  option to keep settings after reboot
- **Live streaming** — destaggered 2D images of the four sensor fields:
  RANGE, SIGNAL, REFLECTIVITY and NEAR_IR (ambient light), updated in real time.
  Click any image (or use the **View** buttons) to enlarge a single field;
  click again to return to the 4-up grid
- **3D viewer** — launches Ouster's official point-cloud viewer with one click
- **Record & playback** — save the stream to a PCAP file and play back
  PCAP / OSF recordings (**Play Recording** button) with no physical sensor
  attached; tick **Loop playback** to repeat the recording continuously
- **Export to MCAP (Foxglove)** — convert a PCAP / OSF recording into an
  `.mcap` file that opens directly in Foxglove: point clouds on
  `/ouster/points` (`foxglove.PointCloud`, colored by signal intensity) and,
  for PCAP inputs, the sensor's IMU (accel + gyro) on `/ouster/imu`
- **ROS 2 topics** — publish the live point cloud as
  `sensor_msgs/PointCloud2` on a configurable topic (default
  `/ouster/points`) while streaming from the sensor **or** while playing
  back a recording, so RViz2 / any ROS 2 node can consume it in real time
- **Remembers your settings** — the hostname/IP and all configuration fields
  are saved to `~/.ouster_lidar_gui.json` and restored on the next launch, so
  you never have to retype the sensor address
- **Help** — this README opens inside the app via the Help button

## Installation

### Ubuntu 24.04

The quick way — the install script:

```bash
cd ouster_lidar_gui
chmod +x install.sh
./install.sh
```

Or manually:

```bash
# system packages (Ubuntu 24.04 requires a virtual environment - PEP 668)
sudo apt-get update
sudo apt-get install -y python3-venv python3-tk

# virtual environment + dependencies
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Windows

```bat
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Connecting the sensor

1. Connect the sensor to its interface box, plug the network cable into the
   computer, and power it up.
2. The sensor gets an address automatically (DHCP or link-local). It is
   reachable by name: `os-<serial-number>.local`
   (e.g. `os-122204001234.local`).
3. Quick check that the sensor is reachable:

```bash
ping os-122xxxxxxxxxx.local
# or open the sensor's home page in a browser:
# http://os-122xxxxxxxxxx.local
```

> Tip: if the name does not resolve on Ubuntu, make sure avahi is installed
> (`sudo apt-get install avahi-daemon`), or use the sensor's IP address
> directly.

## Running

```bash
source venv/bin/activate      # Windows: venv\Scripts\activate
python ouster_gui.py
```

### Basic usage

1. Enter the sensor's hostname / IP. **Get Sensor Info** opens the sensor's
   web dashboard; **Get Status** shows telemetry and health.
2. Pick a lidar mode (e.g. `1024x10`) and any other settings, then click
   **Apply Configuration** — the sensor reinitializes itself (a few seconds).
   Leave **Persist** unchecked to have the change reset on the next reboot,
   or check it to store the setting on the sensor.
3. Click **Start Stream** for the live 2D view inside the window. Click an
   image to enlarge just that field; click again to go back to all four.
4. Click **Open 3D Viewer** for the 3D point cloud (this stops the 2D stream
   first, then opens Ouster's viewer in a separate window).
5. **Start Recording** captures to a PCAP file; **Play Recording (PCAP / OSF)**
   plays a recording back — works with no sensor connected. Tick **Loop
   playback** to have it repeat until you press **Stop Stream**.
6. **Export to MCAP (Foxglove)** converts a PCAP / OSF recording to an
   `.mcap` file. Open that file in [Foxglove](https://foxglove.dev/): add a
   **3D** panel for the `/ouster/points` point cloud (color it by the
   `intensity` field), and — when the input is a PCAP — add a **Plot** panel
   for `/ouster/imu` to graph the sensor's acceleration and angular velocity.
   (IMU is only present in PCAP recordings, not OSF.)

> Note: **Apply Configuration** with **Persist** off changes the sensor only
> until its next power cycle; with **Persist** on, the settings are saved on
> the sensor and survive a reboot.

### ROS 2 topics

The **ROS 2 TOPICS** panel publishes the live point cloud as a standard
`sensor_msgs/PointCloud2` (fields `x`, `y`, `z`, `intensity`, sensor-data
QoS) so RViz2, `ros2 topic echo`, rosbag recording or any ROS 2 node can
consume it. It works both while streaming from a real sensor and while
playing back a PCAP / OSF recording — so you can "replay into ROS" with no
sensor attached.

Setup (Ubuntu — rclpy comes from the ROS 2 installation, not from pip):

```bash
# 1. install ROS 2 (e.g. Jazzy on Ubuntu 24.04), then in every terminal:
source /opt/ros/jazzy/setup.bash

# 2. create the app's venv WITH access to the ROS python packages:
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt

# 3. run the app from that ROS-sourced terminal:
python ouster_gui.py
```

Usage:

1. (Optional) change the **Point-cloud topic** (default `/ouster/points`)
   and the **Frame ID** (default `ouster`).
2. Click **Start ROS Publishing**, then start a live stream or play a
   recording — every frame is published as one `PointCloud2` message.
3. In RViz2: set **Fixed Frame** to the frame ID (e.g. `ouster`), add a
   **PointCloud2** display on the topic, and color it by `intensity`.
   Quick check from a terminal: `ros2 topic hz /ouster/points`.

Message timestamps use the sensor's own packet timestamps when available
(falling back to ROS clock time). On Windows, live ROS publishing requires
a Windows ROS 2 installation; the more common setup is Ubuntu.

> Note: only the point cloud is published live. The sensor's IMU stream is
> available offline via **Export to MCAP** (from PCAP recordings); for a
> full live ROS driver (IMU, TF, multiple topics) see Ouster's official
> [ouster-ros](https://github.com/ouster-lidar/ouster-ros) package.

### No sensor? Try a sample recording

Ouster publishes sample recordings on its website
([Sample Data](https://ouster.com/resources/lidar-sample-data)).
Download a PCAP + JSON pair and open the PCAP via **Open PCAP / OSF File**
(the metadata JSON must sit next to the PCAP with the same base name).

## Project layout

```
ouster_lidar_gui/
├── ouster_gui.py      # main application (Tkinter + ouster-sdk + matplotlib)
├── requirements.txt   # Python dependencies
├── install.sh         # automated install for Ubuntu 24.04
└── README.md
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ouster-sdk missing` | Activate the virtual environment: `source venv/bin/activate` |
| No data while streaming | Make sure the configuration was applied with `udp_dest_auto` (done automatically by Apply) and that the firewall is not blocking ports 7502/7503 (`sudo ufw allow 7502/udp && sudo ufw allow 7503/udp`) |
| `no module named tkinter` | `sudo apt-get install python3-tk` |
| `.local` name does not resolve | `sudo apt-get install avahi-daemon` or use the IP address directly |
| `rclpy (ROS 2) is not available` | Install ROS 2, `source /opt/ros/<distro>/setup.bash`, and recreate the venv with `--system-site-packages` (see "ROS 2 topics") |
| ROS topic exists but RViz2 shows nothing | Set RViz2's **Fixed Frame** to the app's Frame ID (default `ouster`) and check both ends use the same `ROS_DOMAIN_ID` |
