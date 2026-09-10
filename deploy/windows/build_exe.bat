@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  Build standalone Windows executables - no Python needed on the target
REM
REM    build_exe.bat            build both intercom_A.exe and intercom_B.exe
REM    build_exe.bat A          build only computer A's executable
REM    build_exe.bat A console  build with a visible console window (debugging)
REM
REM  Output goes to the dist\ folder at the top of the project.
REM  Run this once on a Windows machine that has Python; the resulting .exe
REM  runs anywhere, and install.bat picks it up automatically.
REM ===========================================================================

set "WHICH=%~1"
set "MODE=%~2"
set "SRC=%~dp0..\.."
if "%WHICH%"=="" set "WHICH=AB"

REM --noconsole keeps a logon-started node from flashing a black window.
REM Logging then has nowhere to go but the file, which is why run.bat and the
REM installer always pass --log-file.
set "WINDOW=--noconsole"
if /I "%MODE%"=="console" set "WINDOW="

where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python was not found on PATH. Install it to build; the .exe it
    echo     produces will not need Python at all.
    exit /b 1
)

echo [1/2] Installing build dependencies...
python -m pip install --quiet --upgrade pyinstaller sounddevice numpy
if errorlevel 1 (
    echo [X] pip failed.
    exit /b 1
)

echo [2/2] Building...
echo %WHICH% | find /I "A" >nul && call :build A
echo %WHICH% | find /I "B" >nul && call :build B

echo.
echo === Done ===
echo   Executables are in "%SRC%\dist"
echo   Copy the one for that machine and run install.bat there - it will use
echo   the .exe and skip the Python setup entirely.
echo.
exit /b 0

:build
set "ROLE=%~1"
echo   - intercom_%ROLE%.exe
REM --collect-all sounddevice bundles _sounddevice_data, which holds the
REM PortAudio DLL. Without it the .exe starts and then cannot open any device.
python -m PyInstaller --noconfirm --clean --onefile %WINDOW% ^
    --collect-all sounddevice ^
    --name intercom_%ROLE% ^
    --distpath "%SRC%\dist" ^
    --workpath "%SRC%\dist\build" ^
    --specpath "%SRC%\dist" ^
    "%SRC%\intercom_%ROLE%.py"
if errorlevel 1 echo   [X] build of intercom_%ROLE%.exe failed.
exit /b 0
