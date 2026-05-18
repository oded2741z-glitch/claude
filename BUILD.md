# Building Pulse.exe

## Requirements
```
pip install pyinstaller pywebview flask
```

Place `icon.ico` in this folder (PNG -> ICO converter: https://convertio.co/png-ico/).

## Build the EXE
```
pyinstaller Pulse.spec
```
Output: `dist/Pulse.exe` (no CMD window, custom icon, no Python needed).

## Build the installer (optional)
1. Install [Inno Setup](https://jrsoftware.org/isdl.php).
2. Open `installer.iss` in Inno Setup -> Build.
3. Output: `installer/Pulse-Setup.exe`.
