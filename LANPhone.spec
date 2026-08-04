# PyInstaller build recipe: one self-contained executable, no console window.
#   pyinstaller --noconfirm --clean LANPhone.spec
# or just run build.bat on Windows.
import os
import sys

from PyInstaller.utils.hooks import collect_all

# PyInstaller defines SPECPATH when it runs this file.
HERE = globals().get("SPECPATH") or os.getcwd()
sys.path.insert(0, HERE)

from lanphone import appicon  # noqa: E402  (needs HERE on the path first)


def resolve_icon():
    """The icon to build with.

    A file that is not really a .ico - a renamed .png, most often - makes
    PyInstaller stop with an error instead of just skipping the icon, so it is
    checked here and reported.  With no usable file, the app's own icon is
    generated, which is better than PyInstaller's default anyway.
    """
    for name in appicon.ICON_FILENAMES:
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        if appicon.is_ico(path):
            print(f"[LANPhone] using icon {name}")
            return path
        print(
            f"[LANPhone] {name} is not a real Windows .ico file - its contents do not\n"
            f"           match the extension, which usually means an image was renamed.\n"
            f"           PyInstaller stops the whole build over this unless Pillow is\n"
            f"           installed to convert it, so it is being ignored. Either save a\n"
            f"           true .ico (any online converter does it) or run:\n"
            f"               pip install pillow\n"
            f"           Building with the app's own icon in the meantime."
        )
    generated = os.path.join(HERE, "build", "lanphone.ico")
    os.makedirs(os.path.dirname(generated), exist_ok=True)
    appicon.write_ico(generated)
    print(f"[LANPhone] no icon file found; generated {generated}")
    return generated


icon = resolve_icon()

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

# Carry a hand-made icon into the bundle itself: appicon.find_icon_file() looks
# for it there (via sys._MEIPASS) at runtime, so the running app - not just the
# .exe's own file icon - uses it too.  A generated fallback icon needs no such
# copy: appicon regenerates the identical tile at runtime on its own.
if os.path.dirname(icon) == HERE:
    datas.append((icon, "."))

a = Analysis(
    [os.path.join(HERE, "main.py")],
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
    icon=icon,
)
