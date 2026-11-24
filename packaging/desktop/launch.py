"""Entry point of the installable desktop app (frozen by PyInstaller)."""

import multiprocessing

from pepite.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
