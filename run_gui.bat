@echo off
REM Double-click this file to launch the USB camera GUI on Windows.
REM It keeps the window open if an error happens so you can read the message.

cd /d "%~dp0"
python usb_camera.py
if errorlevel 1 (
    echo.
    echo The program exited with an error. See the message above.
    pause
)
