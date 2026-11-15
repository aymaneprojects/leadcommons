"""LeadCommons as a desktop app: the web app runs on this computer, shown in its own window.

Used by the installable builds (Windows, macOS, Linux; made with PyInstaller by
.github/workflows/desktop.yml) and by `leadcommons desktop`. The server listens on 127.0.0.1
only, on a free port, so nothing is reachable from the network. Data lives in the usual home
(~/.leadcommons, or LEADCOMMONS_HOME), shared with the command-line install.

Window: pywebview (WebView2 on Windows, WebKit on macOS). Without it (or when it fails, for
example on a Linux desktop without WebKitGTK) the app opens the default browser and shows a
small window with Open and Quit.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

APP_NAME = "LeadCommons"
STATE_FILE = "desktop.json"
LOG_FILE = "desktop.log"
PACK_JOB_LABEL = "Download the latest company data"

log = logging.getLogger("leadcommons.desktop")


# -- small helpers (tested) -----------------------------------------------------------------


def free_port() -> int:
    """A TCP port on 127.0.0.1 that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def is_alive(url: str, timeout: float = 1.0) -> bool:
    """True when a LeadCommons server answers /healthz at url."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=timeout) as resp:  # noqa: S310
            return resp.status == 200 and b'"ok"' in resp.read(200)
    except (OSError, ValueError):
        return False


def write_state(home: Path, port: int) -> None:
    (home / STATE_FILE).write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")


def clear_state(home: Path, port: int) -> None:
    path = home / STATE_FILE
    with contextlib.suppress(OSError, ValueError):
        if json.loads(path.read_text(encoding="utf-8")).get("port") == port:
            path.unlink()


def running_instance(home: Path) -> str | None:
    """URL of a LeadCommons window already running for this home, if any."""
    try:
        port = int(json.loads((home / STATE_FILE).read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    url = f"http://127.0.0.1:{port}/"
    return url if is_alive(url) else None


def setup_logging(home: Path) -> Path:
    """Log to home/desktop.log. Windowed builds have no console, so stdout/stderr go there too."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / LOG_FILE
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, "baseFilename", None) == str(path)]:
        root.removeHandler(old)  # main() may run more than once in a process (tests)
        old.close()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    if sys.stdout is None or sys.stderr is None:
        stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 (lives with the app)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    return path


# -- the local server ---------------------------------------------------------------------


class LocalServer:
    """The FastAPI app under uvicorn, in a background thread, on 127.0.0.1 only."""

    def __init__(self, settings: Any, port: int) -> None:
        import uvicorn

        from leadcommons.web.app import create_app

        self.port = port
        self.url = f"http://127.0.0.1:{port}/"
        self.app = create_app(settings=settings)
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=port,
            log_config=None,  # keep our file logging; uvicorn's default writes to the console
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="lc-server", daemon=True)

    def start(self, timeout: float = 60.0) -> str:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError("the local server stopped while starting; see desktop.log")
            if time.monotonic() > deadline:
                raise RuntimeError("the local server did not start in time; see desktop.log")
            time.sleep(0.05)
        return self.url

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def start_first_pack_download(app: Any) -> bool:
    """A fresh install only has the small sample pack: fetch the full one in the background."""
    from leadcommons.packs import has_own_pack

    settings = app.state.settings
    if has_own_pack(settings.data_dir) or os.environ.get("LEADCOMMONS_DESKTOP_NO_DOWNLOAD"):
        return False
    try:
        app.state.jobs.submit("download_pack", {}, PACK_JOB_LABEL)
    except Exception:  # never block the window on this
        log.exception("could not start the first pack download")
        return False
    return True


# -- windows --------------------------------------------------------------------------------


def open_window(url: str, home: Path) -> bool:
    """Show url in a native window until it is closed. False when no window toolkit works."""
    try:
        import webview
    except Exception:
        log.info("pywebview is not available; using the browser")
        return False
    try:
        with contextlib.suppress(Exception):
            webview.settings["ALLOW_DOWNLOADS"] = True  # CSV exports
            webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.create_window(
            APP_NAME, url, width=1440, height=900, min_size=(1024, 680), text_select=True
        )
        # A real profile, so preferences kept in the page (columns, sidebar) survive a restart.
        webview.start(private_mode=False, storage_path=str(home / "webview"))
    except Exception:
        log.exception("the native window failed; using the browser")
        return False
    return True


def run_in_browser(url: str) -> None:
    """Open the default browser and keep a small Open / Quit window until the user quits."""
    webbrowser.open(url)
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
    except Exception:
        log.info("no window toolkit; LeadCommons runs until this process is stopped")
        _wait_forever()
        return
    root.title(APP_NAME)
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid()
    ttk.Label(frame, text="LeadCommons is running on this computer.").grid(columnspan=2, pady=4)
    ttk.Label(frame, text=url).grid(columnspan=2, pady=(0, 10))
    ttk.Button(frame, text="Open", command=lambda: webbrowser.open(url)).grid(row=2, column=0)
    ttk.Button(frame, text="Quit", command=root.destroy).grid(row=2, column=1)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


def _wait_forever(seconds: float | None = None) -> None:
    deadline = None if seconds is None else time.monotonic() + seconds
    with contextlib.suppress(KeyboardInterrupt):
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.5)


# -- entry point ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadcommons-desktop", description=__doc__.split("\n")[0])
    parser.add_argument("--headless", action="store_true", help="Serve only, no window (tests).")
    parser.add_argument("--port", type=int, default=0, help="Port to use (default: a free one).")
    parser.add_argument("--exit-after", type=float, default=None, help="Stop after N seconds.")
    parser.add_argument("--browser", action="store_true", help="Use the browser, not a window.")
    args = parser.parse_args(argv)

    os.environ.setdefault("LEADCOMMONS_MODE", "local")
    from leadcommons.config import Settings

    settings = Settings()
    settings.ensure_dirs()
    log_path = setup_logging(settings.home)

    if not args.headless and (url := running_instance(settings.home)):
        log.info("already running at %s; opening another window", url)
        if args.browser or not open_window(url, settings.home):
            webbrowser.open(url)
        return 0

    port = args.port or free_port()
    try:
        server = LocalServer(settings, port)
        url = server.start()
    except Exception as exc:
        log.exception("could not start LeadCommons")
        print(f"LeadCommons could not start: {exc}. Details: {log_path}", file=sys.stderr)
        return 1
    write_state(settings.home, port)
    log.info("LeadCommons %s at %s (home %s)", _version(), url, settings.home)
    try:
        start_first_pack_download(server.app)
        if args.headless:
            print(f"LeadCommons running at {url}", flush=True)
            _wait_forever(args.exit_after)
        elif args.browser or not open_window(url, settings.home):
            run_in_browser(url)
    finally:
        clear_state(settings.home, port)
        server.stop()
    return 0


def _version() -> str:
    from leadcommons import __version__

    return __version__


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
