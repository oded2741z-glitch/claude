# Changelog

## v1.0.0

First stable version of the Ouster Digital Lidar GUI (Python / Tkinter).

### Connection & control
- Connect to a sensor by hostname or IP (remembers the last address).
- **Get Sensor Info** opens the sensor's web dashboard in the browser.
- **Get Status** shows telemetry (voltage/temperature), alerts, run status
  and live shot-limiting / thermal state.
- **Reinitialize** restarts the sensor's data path.
- **Network / IP** dialog: view the network config, set a static IP (with
  optional gateway), or revert to DHCP / link-local.

### Configuration
- Lidar mode, timestamp mode, operating mode (NORMAL/STANDBY), signal
  multiplier, UDP data profile, azimuth window (horizontal FOV) and UDP ports.
- **Persist** toggle (off by default) to keep settings across reboots.

### Visualization
- Live 2D field images (RANGE / SIGNAL / REFLECTIVITY / NEAR_IR), destaggered.
- Click an image (or use the View buttons) to enlarge one field; click again
  to return to the 4-up grid.
- **Open 3D Viewer** launches Ouster's point-cloud viewer.

### Recording, playback & export
- Record to PCAP; play back PCAP / OSF recordings, with optional looping.
- **Export to MCAP (Foxglove)**: point clouds on `/ouster/points`
  (`foxglove.PointCloud`) plus IMU (accel + gyro) on `/ouster/imu` for PCAP
  inputs.

### App
- Python-branded dark theme, scrollable control panel.
- Settings saved to `~/.ouster_lidar_gui.json`.
- In-app **Help** (README) viewer.
- Runs on Ubuntu 24.04 and Windows.
