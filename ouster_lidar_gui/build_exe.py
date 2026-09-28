"""Build a standalone OusterLidarGUI executable with PyInstaller.

The result bundles Python and every library, so it runs on a computer that
has no Python installed and no internet (copy it over by USB):

    Windows  ->  dist\\OusterLidarGUI.exe

Usage, from this folder, with requirements.txt + pyinstaller installed:

    python build_exe.py

Then verify the build without starting the GUI:

    dist\\OusterLidarGUI.exe --self-test selftest.txt
"""
import os

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))

# Packages that load parts of themselves dynamically, so PyInstaller's
# import scan can't find everything on its own.
COLLECT_ALL = ["ouster.sdk", "ouster.cli", "rosbags", "zeroconf"]
COLLECT_SUBMODULES = ["foxglove_schemas_protobuf", "mcap_protobuf"]

# ouster-cli discovers its plugins at runtime, and the plugins import these;
# without them the 3D viewer and recording fail inside the EXE.
HIDDEN_IMPORTS = ["prettytable", "laspy", "psutil", "more_itertools",
                  "threadpoolctl", "requests", "flask", "click"]


def main():
    build_dir = os.path.join(HERE, "build")
    args = [
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", "OusterLidarGUI",
        "--copy-metadata", "ouster-sdk",
        "--add-data", f"{os.path.join(HERE, 'README.md')}{os.pathsep}.",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", build_dir,
        "--specpath", build_dir,
    ]
    for pkg in COLLECT_ALL:
        args += ["--collect-all", pkg]
    for pkg in COLLECT_SUBMODULES:
        args += ["--collect-submodules", pkg]
    for mod in HIDDEN_IMPORTS:
        args += ["--hidden-import", mod]
    args.append(os.path.join(HERE, "ouster_gui.py"))
    PyInstaller.__main__.run(args)


if __name__ == "__main__":
    main()
