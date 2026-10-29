"""Background work: daemon threads and debounced timers. Errors are logged, never raised, and the
work runs at low CPU priority unless asked otherwise (scans keep normal priority)."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


def _run(fn: Callable, args: tuple, name: str, low_priority: bool) -> None:
    if low_priority:
        try:
            os.nice(10)  # Linux: lowers this thread only
        except OSError:
            pass
    try:
        fn(*args)
    except Exception:
        log.exception("background task %s failed", name)


def run_in_background(fn: Callable, *args, name: str = "classifiles-task",
                      low_priority: bool = True) -> threading.Thread:
    """Start fn(*args) in a daemon thread and return the thread."""
    t = threading.Thread(target=lambda: _run(fn, args, name, low_priority), daemon=True, name=name)
    t.start()
    return t


class Debounced:
    """Run fn after a delay; scheduling again before it ran replaces the pending run."""

    def __init__(self, fn: Callable[[], object], name: str, low_priority: bool = True):
        self.fn, self.name, self.low_priority = fn, name, low_priority
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def schedule(self, delay: float) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(delay, _run, (self.fn, (), self.name, self.low_priority))
            self._timer.daemon = True
            self._timer.name = self.name
            self._timer.start()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the pending run (tests)."""
        if self._timer is not None:
            self._timer.join(timeout)
