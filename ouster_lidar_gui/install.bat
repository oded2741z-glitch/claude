@echo off
REM ============================================================
REM  Ouster Digital Lidar GUI - Windows installer
REM  Creates a virtual environment and installs all dependencies.
REM  Just double-click this file, or run it from CMD / PowerShell.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ==^> Checking for Python...

REM Prefer the "py" launcher; fall back to "python"
set "PY=py"
where py >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: Python was not found.
    echo Install Python 3 ^(64-bit^) from https://www.python.org/downloads/
    echo and make sure "Add Python to PATH" is checked during install.
    echo.
    pause
    exit /b 1
)
%PY% --version

echo.
echo ==^> Creating virtual environment ^(venv^)...
%PY% -m venv venv
if errorlevel 1 (
    echo ERROR: could not create the virtual environment.
    pause
    exit /b 1
)

echo.
echo ==^> Installing Python dependencies...
call "venv\Scripts\python.exe" -m pip install --upgrade pip
call "venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: dependency installation failed. See the messages above.
    echo If you see a DLL error later, install the Microsoft Visual C++
    echo Redistributable ^(x64^): https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done! To run the app:
echo      run.bat
echo  or manually:
echo      venv\Scripts\python.exe ouster_gui.py
echo ============================================================
echo.
pause
