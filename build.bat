@echo off
setlocal
cd /d "%~dp0"

set ICON=
if exist app_icon.ico set ICON=--icon app_icon.ico

pyinstaller --noconfirm --onefile --windowed --name display_controller %ICON% ^
  --collect-submodules eventlet ^
  --collect-submodules dns ^
  --hidden-import engineio.async_drivers.eventlet ^
  display_controller.py
if errorlevel 1 goto fail

pyinstaller --noconfirm --onefile --windowed --name viewer %ICON% viewer.py
if errorlevel 1 goto fail

pyinstaller --noconfirm --onefile --windowed --name display_configurator %ICON% display_configurator.py
if errorlevel 1 goto fail

echo.
echo Build finished. Executables are in the dist folder.
echo Copy config.txt, targets.txt, displays_map.txt, scenes.json and loading.json next to them.
goto end

:fail
echo.
echo Build failed.
exit /b 1

:end
endlocal
