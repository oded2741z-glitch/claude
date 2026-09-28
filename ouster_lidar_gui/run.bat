@echo off
REM Launch the Ouster Digital Lidar GUI on Windows.
REM Run install.bat once first to create the virtual environment.
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found. Please run install.bat first.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" ouster_gui.py
