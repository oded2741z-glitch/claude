@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  P2P Intercom - Windows installer
REM
REM    install.bat A                     computer A: signalling server + peer
REM    install.bat B 10.0.0.5            computer B: peer, pointed at A
REM    install.bat A "" D:\intercom      third argument overrides the folder
REM
REM  Messages here are English on purpose: the console codepage is not UTF-8,
REM  and Hebrew text would come out as garbage. The user guide is in Hebrew.
REM ===========================================================================

set "ROLE=%~1"
set "SERVER_IP=%~2"
set "DEST=%~3"
if "%DEST%"=="" set "DEST=C:\intercom"
set "SRC=%~dp0..\.."

if /I "%ROLE%"=="A" goto role_ok
if /I "%ROLE%"=="B" goto role_ok
echo.
echo   Usage: install.bat A ^| B [server-ip] [install-folder]
echo.
echo   A = this computer runs the signalling server and takes part in the call
echo   B = this computer is a client only; pass the address of computer A
echo.
exit /b 1
:role_ok
call :upper ROLE

echo.
echo === P2P Intercom installer - role %ROLE% ===
echo.

REM A prebuilt .exe (see build_exe.bat) needs no Python on this machine.
set "EXE="
if exist "%SRC%\dist\intercom_%ROLE%.exe" set "EXE=%SRC%\dist\intercom_%ROLE%.exe"
if exist "%SRC%\intercom_%ROLE%.exe" set "EXE=%SRC%\intercom_%ROLE%.exe"
if defined EXE (
    echo [1/6] Found a prebuilt executable - skipping the Python setup.
    echo [2/6] No dependencies needed.
    goto files
)

REM --- 1. Python -------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python was not found on PATH.
    echo     Install Python 3 from python.org and tick "Add Python to PATH".
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [1/6] Found %%v

REM --- 2. Dependencies -------------------------------------------------------
echo [2/6] Installing sounddevice and numpy...
python -m pip install --quiet --upgrade sounddevice numpy
if errorlevel 1 (
    echo [X] pip failed. Check the internet connection or a proxy.
    exit /b 1
)
python -c "import sounddevice, numpy" 2>nul
if errorlevel 1 (
    echo [X] sounddevice still does not import. PortAudio may be missing.
    exit /b 1
)

REM --- 3. Files --------------------------------------------------------------
:files
echo [3/6] Copying to %DEST% ...
if not exist "%DEST%" mkdir "%DEST%"
if defined EXE (
    copy /Y "%EXE%" "%DEST%\" >nul
) else (
    if not exist "%SRC%\intercom_%ROLE%.py" (
        echo [X] Cannot find "%SRC%\intercom_%ROLE%.py".
        echo     Run this from the deploy\windows folder of the project.
        exit /b 1
    )
    copy /Y "%SRC%\intercom_%ROLE%.py" "%DEST%\" >nul
)

REM --- 4. Launcher and shortcuts --------------------------------------------
echo [4/6] Writing launcher and on/off shortcuts...
set "ARGS=--log-file %DEST%\node.log"
if /I "%ROLE%"=="B" if not "%SERVER_IP%"=="" set "ARGS=--server-ip %SERVER_IP% %ARGS%"

REM pythonw runs without a console window; the log file is where output goes
set "LAUNCH=pythonw intercom_%ROLE%.py"
if defined EXE set "LAUNCH=intercom_%ROLE%.exe"

> "%DEST%\run.bat" echo @echo off
>>"%DEST%\run.bat" echo cd /d "%DEST%"
>>"%DEST%\run.bat" echo %LAUNCH% %ARGS%

REM No space before the > : "echo on > file" would write "on " with a trailing space
> "%DEST%\on.bat"  echo @echo on^> "%DEST%\switch_%ROLE%.txt"
> "%DEST%\off.bat" echo @echo off^> "%DEST%\switch_%ROLE%.txt"
> "%DEST%\status.bat" echo @type "%DEST%\status_%ROLE%.txt" ^& pause

REM --- 5. Scheduled task -----------------------------------------------------
echo [5/6] Registering the logon task...
schtasks /Create /TN "Intercom %ROLE%" /TR "\"%DEST%\run.bat\"" /SC ONLOGON /F >nul
if errorlevel 1 (
    echo [!] Could not register the task. Create it by hand in taskschd.msc:
    echo     trigger At log on, action "%DEST%\run.bat",
    echo     and tick "Run only when user is logged on".
) else (
    echo     Task "Intercom %ROLE%" runs at logon, inside your session.
)

REM --- 6. Firewall (role A hosts the server) --------------------------------
if /I "%ROLE%"=="A" (
    echo [6/6] Opening UDP 9999 for the signalling server...
    netsh advfirewall firewall add rule name="Intercom UDP 9999" dir=in action=allow protocol=UDP localport=9999 >nul 2>&1
    if errorlevel 1 (
        echo [!] Firewall rule not added - run this installer as Administrator,
        echo     or add the rule by hand. Without it computer B cannot register.
    )
) else (
    echo [6/6] No firewall rule needed on a client.
)

echo.
echo === Done ===
echo   Start now:      "%DEST%\run.bat"
echo   Turn call on:   "%DEST%\on.bat"
echo   Turn call off:  "%DEST%\off.bat"
echo   See state:      "%DEST%\status.bat"
echo   Log file:       "%DEST%\node.log"
echo.
if /I "%ROLE%"=="A" echo   Edit "%DEST%\control_A.txt" after the first run to set ext_ip / local_mode.
if /I "%ROLE%"=="B" echo   Check "server_ip" in "%DEST%\control_B.txt" points at computer A.
echo.
exit /b 0

:upper
call set "%~1=%%%~1:a=A%%"
call set "%~1=%%%~1:b=B%%"
exit /b 0
