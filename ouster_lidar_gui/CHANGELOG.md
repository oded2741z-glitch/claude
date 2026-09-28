# Changelog

## v1.2.0

- **Standalone Windows EXE** — `OusterLidarGUI.exe` bundles Python and all
  libraries, so it runs on a PC with no Python and no internet. Built with
  `build_exe.py` / `build_exe.bat`, and automatically on every push by the
  "Build Windows EXE" GitHub Actions workflow.
- `--self-test` flag checks every bundled dependency (SDK native code, OSF
  playback, ouster-cli plugins, 3D viewer, Tk/matplotlib, MCAP, README).
- Inside the EXE, the 3D viewer and recording re-launch the EXE itself in
  ouster-cli mode, since there is no separate Python interpreter.
- A windowed EXE's stray console output goes to `~/.ouster_lidar_gui.log`.
- README: Windows firewall and no-network static-IP troubleshooting.

## v1.1.0

- **Sensor profiles** — save named profiles (address + full configuration)
  for each Ouster sensor, then switch between sensors by picking a profile
  from the dropdown. Save / Load / Delete buttons; profiles are stored in
  `~/.ouster_lidar_gui.json` and the last-used profile is remembered.
- Windows: one-click `install.bat` installer and `run.bat` launcher.
- Quieter startup: harmless native-SDK "Duplicate metadata type" console
  errors are suppressed.

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
