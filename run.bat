@echo off
REM Start LAN Phone on Windows. Double-click this file.
setlocal
cd /d "%~dp0"

set PY=py -3
py -3 -V >nul 2>nul
if errorlevel 1 set PY=python

%PY% -V >nul 2>nul
if errorlevel 1 goto nopython

%PY% -c "import numpy, sounddevice" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages ^(numpy, sounddevice^)...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 goto nodeps
)

%PY% -m lanphone
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo Python was not found.
echo Install Python 3.9 or newer from https://www.python.org/downloads/windows/
echo During setup, check "Add python.exe to PATH".
echo.
pause
exit /b 1

:nodeps
echo.
echo Could not install the required packages.
echo Try running this command manually in a Command Prompt:
echo     %PY% -m pip install numpy sounddevice
echo.
pause
exit /b 1
