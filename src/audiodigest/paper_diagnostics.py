"""Fixed optional-paper outcomes; no source/reviewer text or external calls."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from audiodigest.validation_diagnostics import safe_detail

PHASES = {"admission", "draft", "compact", "review", "rewrite", "recheck", "render", "promote"}
OUTCOMES = {"ready", "failed", "deferred"}
PAPER_CODES = {
    "paper_verification",
    "paper_render",
    "paper_timeout",
    "paper_validation",
    "paper_other",
    "paper_budget_insufficient",
    "paper_budget_exhausted",
}


@dataclass
class PaperTrace:
    phase: str = "draft"
    attempt: int = 1


_TRACE: ContextVar[PaperTrace | None] = ContextVar("paper_trace", default=None)


@contextmanager
def paper_scope(trace):
    token = _TRACE.set(trace)
    try:
        yield
    finally:
        _TRACE.reset(token)


def paper_phase(phase, attempt=1):
    if phase in PHASES and type(attempt) is int and 1 <= attempt <= 10 and (trace := _TRACE.get()):
        trace.phase, trace.attempt = phase, attempt


def safe_paper_result(raw, allowed_codes):
    if not isinstance(raw, dict):
        return None
    if (
        not isinstance(raw.get("outcome"), str)
        or raw["outcome"] not in OUTCOMES
        or not isinstance(raw.get("phase"), str)
        or raw["phase"] not in PHASES
        or type(raw.get("attempt")) is not int
        or not 1 <= raw["attempt"] <= 10
    ):
        return None
    result = {key: raw[key] for key in ("outcome", "phase", "attempt")}
    code = raw.get("code")
    if isinstance(code, str) and code in allowed_codes | PAPER_CODES:
        result["code"] = code
    elapsed = raw.get("elapsed_seconds")
    if type(elapsed) in (int, float) and 0 <= elapsed <= 3600:
        result["elapsed_seconds"] = round(elapsed, 3)
    result.update(safe_detail(raw))
    return result
