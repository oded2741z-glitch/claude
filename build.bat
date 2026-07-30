@echo off
REM Build a standalone LANPhone.exe that runs without Python installed.
REM Double-click this file on the PC you build on, then copy dist\LANPhone.exe
REM to the other computer.
setlocal
cd /d "%~dp0"

set PY=py -3
py -3 -V >nul 2>nul
if errorlevel 1 set PY=python

%PY% -V >nul 2>nul
if errorlevel 1 goto nopython

echo Installing build requirements...
%PY% -m pip install --upgrade pyinstaller -r requirements.txt
if errorlevel 1 goto nodeps

if exist icon.ico (
    echo Using icon.ico for the executable.
) else (
    echo No icon.ico here - the executable gets the default icon.
    echo Put an icon.ico next to this file to use your own.
)

echo.
echo Building. This takes a minute or two...
%PY% -m PyInstaller --noconfirm --clean LANPhone.spec
if errorlevel 1 goto failed

echo.
echo ============================================================
echo  Done:  dist\LANPhone.exe
echo.
echo  Copy that single file to the other computer - nothing else
echo  is needed there, not even Python. The first start takes a
echo  few seconds because the file unpacks itself.
echo ============================================================
echo.
pause
exit /b 0

:nopython
echo.
echo Python was not found. Install it from
echo   https://www.python.org/downloads/windows/
echo and check "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:nodeps
echo.
echo Could not install the build requirements. Check the internet connection,
echo then try again, or run this manually:
echo     %PY% -m pip install pyinstaller numpy sounddevice
echo.
pause
exit /b 1

:failed
echo.
echo The build failed. The output above says why; the usual causes are
echo antivirus software blocking PyInstaller, or a half-installed package
echo ^(fix with: %PY% -m pip install --force-reinstall pyinstaller^).
echo.
pause
exit /b 1
