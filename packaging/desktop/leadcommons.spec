# PyInstaller recipe for the LeadCommons desktop app (Windows, macOS, Linux).
# Built by .github/workflows/desktop.yml:  pyinstaller packaging/desktop/leadcommons.spec
# Output: dist/LeadCommons/ (a folder with LeadCommons[.exe]) and, on macOS, dist/LeadCommons.app
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

HERE = Path(SPECPATH)  # noqa: F821 (set by PyInstaller)
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from leadcommons import __version__  # noqa: E402

datas, binaries, hiddenimports = [], [], []
# Templates, static files and the sample pack ship inside the leadcommons package. The others
# import modules by name at run time, which PyInstaller cannot see on its own.
for pkg in ("leadcommons", "uvicorn", "mcp", "dns", "email_validator", "selectolax", "duckdb"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
try:
    hiddenimports += collect_submodules("webview")
except Exception:  # Linux builds use the browser instead of a native window
    pass

if sys.platform == "win32":
    icon = HERE / "icons" / "leadcommons.ico"
elif sys.platform == "darwin":
    icon = HERE / "icons" / "leadcommons.icns"
else:
    icon = HERE / "icons" / "leadcommons.png"

a = Analysis(  # noqa: F821
    [str(HERE / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["pyarrow", "pytest", "ruff", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LeadCommons",
    console=False,  # a window app; logs go to ~/.leadcommons/desktop.log
    icon=str(icon),
)
coll = COLLECT(exe, a.binaries, a.datas, name="LeadCommons")  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="LeadCommons.app",
        icon=str(icon),
        bundle_identifier="org.leadcommons.desktop",
        version=__version__,
        info_plist={
            "CFBundleName": "LeadCommons",
            "CFBundleDisplayName": "LeadCommons",
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.business",
        },
    )
