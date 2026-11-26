@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller pygame opencv-python numpy
if errorlevel 1 goto fail
python -m PyInstaller --noconfirm --clean --windowed --name DarkGlassDemo ^
 --add-data "fonts;fonts" ^
 --add-data "img\loading.mp4;img" ^
 --add-data "img\intro.mp4;img" ^
 --add-data "img\turn.mp4;img" ^
 --add-data "img\walker.mp4;img" ^
 --add-data "img\scare.mp4;img" ^
 --add-data "audio\heartbeat.wav;audio" ^
 --add-data "audio\ambient.wav;audio" ^
 --add-data "audio\runstep.mp3;audio" ^
 --add-data "audio\scream.mp3;audio" ^
 dark_glass_demo.py
if errorlevel 1 goto fail
echo.
echo Done: dist\DarkGlassDemo\DarkGlassDemo.exe
pause
exit /b 0
:fail
echo.
echo Build failed.
pause
exit /b 1
