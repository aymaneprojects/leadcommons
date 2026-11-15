# PyInstaller recipe for the desktop app's backend (Windows, macOS, Linux).
# Built by .github/workflows/desktop.yml:  pyinstaller packaging/desktop/leadcommons.spec
# Output: dist/leadcommons-backend/ (leadcommons-backend[.exe] and its files). The Electron
# shell in desktop/electron starts it with --headless --port N --parent-pid PID and shows the
# app in its own window.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

HERE = Path(SPECPATH)  # noqa: F821 (set by PyInstaller)
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src"))

datas, binaries, hiddenimports = [], [], []
# Templates, static files and the sample pack ship inside the leadcommons package. The others
# import modules by name at run time, which PyInstaller cannot see on its own.
for pkg in ("leadcommons", "uvicorn", "mcp", "dns", "email_validator", "selectolax", "duckdb"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(  # noqa: F821
    [str(HERE / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    # The window comes from Electron: no GUI toolkits in the backend.
    excludes=["pyarrow", "pytest", "ruff", "IPython", "tkinter", "webview"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="leadcommons-backend",
    console=False,  # no console window on Windows; logs go to ~/.leadcommons/desktop.log
)
coll = COLLECT(exe, a.binaries, a.datas, name="leadcommons-backend")  # noqa: F821
