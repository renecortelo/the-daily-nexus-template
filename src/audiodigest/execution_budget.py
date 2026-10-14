"""One wall-clock budget for a protected cloud task; no local run limit change."""

from __future__ import annotations

import math
import os
import signal
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

_DEADLINE: ContextVar[float | None] = ContextVar("generation_deadline", default=None)


class RunBudgetExceeded(RuntimeError):
    pass


def check_budget() -> None:
    deadline = _DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise RunBudgetExceeded("The protected generation time budget is exhausted")


def operation_timeout(default: float) -> float:
    check_budget()
    deadline = _DEADLINE.get()
    return default if deadline is None else min(default, max(0.05, deadline - time.monotonic()))


@contextmanager
def reserve_time(seconds: float, *, maximum_seconds: float | None = None):
    """Bound an optional phase while preserving time for speech and publishing."""
    for value in (seconds, maximum_seconds):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise ValueError("Invalid optional phase budget")
    deadline = _DEADLINE.get()
    if deadline is None:
        yield
        return
    started = time.monotonic()
    phase_deadline = deadline - seconds
    if maximum_seconds is not None:
        phase_deadline = min(phase_deadline, started + maximum_seconds)
    token = _DEADLINE.set(phase_deadline)
    previous_timer = None
    try:
        check_budget()
        if (hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
                and signal.getsignal(signal.SIGALRM) is RunBudget._expired):
            old_delay, _interval = signal.getitimer(signal.ITIMER_REAL)
            delay = phase_deadline - started
            if old_delay > 0:
                delay = min(delay, old_delay)
            previous_timer = signal.setitimer(signal.ITIMER_REAL, delay)
        yield
        check_budget()
    finally:
        _DEADLINE.reset(token)
        if previous_timer is not None:
            # Never restart the outer clock at its original full duration.
            remaining = previous_timer[0] - (time.monotonic() - started)
            delay = max(0.000001, remaining) if previous_timer[0] > 0 else 0.0
            signal.setitimer(signal.ITIMER_REAL, delay, previous_timer[1])


def remaining_time() -> float | None:
    """None means a local run without a protected cloud deadline."""
    check_budget()
    deadline = _DEADLINE.get()
    return None if deadline is None else max(0.0, deadline - time.monotonic())


class RunBudget:
    def __init__(self):
        self.token = None
        self.alarm_handler = None
        self.previous_alarm = None

    def start_from_environment(self) -> None:
        raw = os.environ.get("TDN_RUNNER_DEADLINE_EPOCH", "")
        if os.environ.get("GITHUB_ACTIONS") != "true" or not raw:
            return
        try:
            seconds = float(raw) - time.time()
        except ValueError as exc:
            raise RunBudgetExceeded("Invalid protected run deadline") from exc
        if not math.isfinite(seconds) or seconds <= 0 or seconds > 3600:
            raise RunBudgetExceeded("Invalid or exhausted protected run deadline")
        self.token = _DEADLINE.set(time.monotonic() + seconds)
        if hasattr(signal, "SIGALRM"):
            try:
                self.alarm_handler = signal.signal(signal.SIGALRM, self._expired)
                self.previous_alarm = signal.setitimer(signal.ITIMER_REAL, seconds)
            except ValueError:  # Non-main-thread callers retain cooperative checks.
                self.alarm_handler = None

    @staticmethod
    def _expired(_signum, _frame):
        raise RunBudgetExceeded("The protected generation time budget is exhausted")

    def close(self) -> None:
        if self.alarm_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.alarm_handler)
            if self.previous_alarm and self.previous_alarm[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *self.previous_alarm)
            self.alarm_handler = None
            self.previous_alarm = None
        if self.token is not None:
            _DEADLINE.reset(self.token)
            self.token = None
