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
- **Record & playback** — save the stream to a PCAP file and replay
  PCAP / OSF recordings with no physical sensor attached
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
5. **Start Recording** captures to a PCAP file; **Open PCAP / OSF File**
   replays a recording — works with no sensor connected.

> Note: **Apply Configuration** with **Persist** off changes the sensor only
> until its next power cycle; with **Persist** on, the settings are saved on
> the sensor and survive a reboot.

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
