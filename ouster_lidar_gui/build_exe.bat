@echo off
REM ============================================================
REM  Build OusterLidarGUI.exe - a single file that runs on a Windows PC
REM  WITHOUT Python and without internet.
REM
REM  Run this on any Windows PC that has Python + internet, then copy
REM  dist\OusterLidarGUI.exe to the target computer (e.g. by USB stick).
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY=py"
where py >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found. Install Python 3 ^(64-bit^) from
    echo https://www.python.org/downloads/ with "Add Python to PATH" checked.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo ==^> Creating virtual environment...
    %PY% -m venv venv || goto :fail
)

echo ==^> Installing dependencies and PyInstaller...
"venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
"venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :fail

echo ==^> Building dist\OusterLidarGUI.exe (this takes a few minutes)...
"venv\Scripts\python.exe" build_exe.py || goto :fail

echo ==^> Self-testing the EXE...
start "" /wait "dist\OusterLidarGUI.exe" --self-test "%CD%\selftest.txt"
set "RESULT=%errorlevel%"
type selftest.txt
if not "%RESULT%"=="0" goto :fail

echo.
echo ============================================================
echo  Done! Copy this single file to the target computer:
echo      %CD%\dist\OusterLidarGUI.exe
echo ============================================================
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
