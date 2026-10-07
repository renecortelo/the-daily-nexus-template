"""One wall-clock budget for a protected cloud task; no local run limit change."""

from __future__ import annotations

import math
import os
import signal
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
def reserve_time(seconds: float):
    """Bound an optional phase while preserving time for speech and publishing."""
    deadline = _DEADLINE.get()
    if deadline is None:
        yield
        return
    token = _DEADLINE.set(deadline - seconds)
    try:
        check_budget()
        yield
    finally:
        _DEADLINE.reset(token)


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
