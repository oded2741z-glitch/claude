# PyInstaller build recipe: one self-contained executable, no console window.
#   pyinstaller --noconfirm --clean LANPhone.spec
# or just run build.bat on Windows.
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# sounddevice ships the PortAudio library as package data; without collecting it
# the frozen app starts but finds no audio devices.
for package in ("sounddevice",):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here needs SciPy or Matplotlib; leaving them out keeps the
    # executable small when they happen to be installed.
    excludes=["scipy", "matplotlib", "PIL", "pytest", "setuptools", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LANPhone",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is what makes antivirus tools complain
    runtime_tmpdir=None,
    console=False,  # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
