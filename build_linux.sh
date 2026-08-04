#!/usr/bin/env bash
# Build a standalone Linux executable of the CPU Load Generator with
# PyInstaller. Run this ON the oldest Linux you want to support (e.g.
# Ubuntu 22.04) — a binary built on an older glibc runs on newer distros,
# but not the other way around.
#
#   chmod +x build_linux.sh
#   ./build_linux.sh
#
# Output: dist/cpu_load_gui   (a single self-contained file)
set -euo pipefail

cd "$(dirname "$0")"

# 1. tkinter must be present (it is bundled into the build)
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "tkinter is missing. Install it, then re-run:"
    echo "    sudo apt install python3-tk"
    exit 1
fi

# 2. PyInstaller (installed into a throwaway venv so the system stays clean)
if [ ! -d .buildenv ]; then
    python3 -m venv .buildenv
fi
# shellcheck disable=SC1091
source .buildenv/bin/activate
pip install --quiet --upgrade pip pyinstaller

# 3. build
rm -rf build dist cpu_load_gui.spec
pyinstaller --onefile --windowed --name cpu_load_gui cpu_load_gui.py

echo
echo "Done. Run it with:  ./dist/cpu_load_gui"
echo "For GPU load + temperature, the target machine also needs:"
echo "    sudo apt install ocl-icd-libopencl1     # OpenCL loader"
echo "    (plus your GPU's OpenCL driver: nvidia-driver / mesa-opencl-icd / intel-opencl-icd)"
