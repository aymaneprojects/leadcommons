"""Background thread that runs scheduled workflows. Owner: tools agent.

Started by the app's startup hook (see web/auth.py install_auth); not started in demo mode or
when settings.sync_jobs is set (serverless hosts freeze threads).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from pepite.workflows import engine

log = logging.getLogger(__name__)

INTERVAL = 30.0


class Scheduler:
    def __init__(self, app: Any, interval: float = INTERVAL) -> None:
        self.app = app
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def allowed(settings: Any) -> bool:
        return getattr(settings, "mode", "") != "demo" and not getattr(settings, "sync_jobs", False)

    def start(self) -> bool:
        if self._thread is not None or not self.allowed(self.app.state.settings):
            return False
        self._thread = threading.Thread(target=self._loop, name="lc-workflows", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            engine.run_due(self.app)


def install(app: Any) -> None:
    """Startup/shutdown hooks on the app (cheap: nothing starts until the server runs)."""

    def _start() -> None:
        sched = Scheduler(app)
        app.state.workflow_scheduler = sched
        sched.start()

    def _stop() -> None:
        sched = getattr(app.state, "workflow_scheduler", None)
        if sched is not None:
            sched.stop()

    app.on_event("startup")(_start)
    app.on_event("shutdown")(_stop)
