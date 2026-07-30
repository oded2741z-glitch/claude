"""Start LAN Phone: ``python main.py``.

This is also the entry point PyInstaller builds ``LANPhone.exe`` from, because
it needs a plain script rather than a package.  ``python -m lanphone`` does the
same thing.
"""

from lanphone.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
