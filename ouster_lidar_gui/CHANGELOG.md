# Changelog

## v1.2.0

### Cameras (new)
- New **CAMERAS** panel: open USB / built-in cameras (device index) or IP
  cameras (RTSP / HTTP URL) via OpenCV — any number of cameras, each in
  its own floating window.
- Live view with resolution + measured FPS, aspect-ratio-preserving
  scaling, and a **Snapshot...** button (PNG / JPEG).
- Per-camera **Publish to ROS 2** toggle: frames go out as
  `sensor_msgs/Image` (`bgr8` / `mono8`) on `/cameraN/image_raw`, sharing
  the ROS node started from the ROS 2 TOPICS panel.
- Camera support is optional (`pip install opencv-python`); the last used
  camera source is remembered in `~/.ouster_lidar_gui.json`.
- Rendering uses Tk's native PPM support — no extra imaging dependency.

## v1.1.0

### ROS 2 topics (new)
- New **ROS 2 TOPICS** panel: publish the live point cloud as
  `sensor_msgs/PointCloud2` on a configurable topic (default
  `/ouster/points`) with a configurable TF frame ID (default `ouster`),
  using the sensor-data QoS profile.
- Works while streaming from a real sensor **and** while playing back a
  PCAP / OSF recording (loop playback included) — usable as a simple
  "replay to ROS" tool with no sensor attached.
- Message stamps use the sensor's packet timestamps when available, with a
  fallback to ROS clock time.
- Publishing can be toggled at any time, even mid-stream; the topic and
  frame ID are remembered in `~/.ouster_lidar_gui.json`.
- ROS support is optional: without ROS 2 / `rclpy` installed the rest of
  the app is unaffected, and the button explains how to set ROS up
  (install ROS 2, source it, venv with `--system-site-packages`).

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
