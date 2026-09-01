# CPU / GPU Load Generator

`cpu_load_gui.py` — a tkinter GUI that puts a controllable load on the CPU
(and optionally the GPU, via OpenCL) and charts live usage and temperature.
Standard library only; `psutil` is optional and only adds CPU usage on
Windows.

```
python3 cpu_load_gui.py           # run the GUI
python3 cpu_load_gui.py --temps   # print which temperature sources work here
```

## Where the temperatures come from

| | CPU | GPU |
|---|---|---|
| Linux | `/sys` thermal zones + hwmon | AMD `gpu_busy_percent` / hwmon, `nvidia-smi` |
| Windows | LibreHardwareMonitor web API → PowerShell WMI (LHM/OHM sensors, then `MSAcpi_ThermalZoneTemperature`) | `nvidia-smi`, else LibreHardwareMonitor |

The PowerShell fallback runs one query per poll:

```powershell
Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature |
    Sort-Object CurrentTemperature -Descending | Select-Object -First 1
# ($z.CurrentTemperature / 10) - 273.15  ->  °C
```

`MSAcpi_ThermalZoneTemperature` needs administrator rights *and* firmware
support. Many desktop boards answer `Not supported` (`0x8004100c`) even to an
elevated query — and when the class returns nothing, the arithmetic above
yields `-273.15`, which is absolute zero, not a reading. A second provider
(`Win32_PerfFormattedData_Counters_ThermalZoneInformation`) is tried after
it, but it reads the same ACPI zones and fails on the same boards.

So LibreHardwareMonitor's sensors are queried first: they read the CPU's own
on-die sensors, work without ACPI, and also cover the GPU on non-NVIDIA
cards. Run `--temps` to see which source answers on a given machine.
