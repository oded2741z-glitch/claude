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

`MSAcpi_ThermalZoneTemperature` usually needs administrator rights and many
boards never expose it, so LibreHardwareMonitor's sensors are tried first —
they also give the GPU reading on non-NVIDIA cards. Run `--temps` to see
which source answers on a given machine.
