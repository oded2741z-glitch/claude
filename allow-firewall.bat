@echo off
REM Opens the ports LAN Phone needs on the Windows firewall, for private
REM (home/office) networks only. Right-click this file -> "Run as administrator".
REM You only need this if the Windows firewall prompt was dismissed with "Cancel".
setlocal

net session >nul 2>nul
if errorlevel 1 (
    echo This script must be run as administrator.
    echo Right-click allow-firewall.bat and choose "Run as administrator".
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="LAN Phone (TCP)" >nul 2>nul
netsh advfirewall firewall delete rule name="LAN Phone (UDP)" >nul 2>nul

netsh advfirewall firewall add rule name="LAN Phone (TCP)" dir=in action=allow ^
    protocol=TCP localport=50505-50530 profile=private,domain
netsh advfirewall firewall add rule name="LAN Phone (UDP)" dir=in action=allow ^
    protocol=UDP localport=50505-50530 profile=private,domain

echo.
echo Done. TCP and UDP ports 50505-50530 are now allowed on private networks.
pause
