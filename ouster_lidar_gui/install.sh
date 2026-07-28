#!/usr/bin/env bash
# Installation script for Ubuntu 24.04
# Creates a virtual environment (required on Ubuntu 24.04 / PEP 668)
# and installs the Ouster SDK + GUI dependencies.
set -e

cd "$(dirname "$0")"

echo "==> Installing system packages (python3-venv, python3-tk)..."
sudo apt-get update
sudo apt-get install -y python3-venv python3-tk

echo "==> Creating virtual environment..."
python3 -m venv venv

echo "==> Installing Python dependencies..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo ""
echo "Done! Run the GUI with:"
echo "    ./venv/bin/python ouster_gui.py"
