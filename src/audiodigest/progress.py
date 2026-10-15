"""Owner-only timing profiles and an allowlisted cloud log stream.

No source text, host names, task identities, URLs or exception messages enter
this protocol. Fixed diagnostic codes and bounded counters are permitted.
Raw subprocess output is consumed, never forwarded or saved.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from audiodigest.cli_protocol import CLI_FAILURE_CODES
from audiodigest.newsletter_accounting import ACCOUNTING_COUNTERS
from audiodigest.validation_diagnostics import DIAGNOSTIC_LIMIT, safe_detail

PREFIX = "TDN_PROGRESS "
STAGES = {
    0: "Preparing",
    1: "Loading newsletters and research",
    2: "Retrieving articles",
    3: "Extracting stories",
    4: "Writing script",
    5: "Checking script",
    6: "Writing and rendering newspaper",
    7: "Rendering audio",
    8: "Publishing",
}
COUNTERS = {
    "cli_stdout_bytes", "cli_stderr_bytes", "cli_failures", "cli_failed_ms",
    "cli_retries", "cli_recoveries", "cli_usage_discarded",
    *ACCOUNTING_COUNTERS,
    "newsletters",
    "links",
    "articles",
    "stories",
    "selected_stories",
    "extracted_records",
    "merged_duplicates",
    "omitted_stories",
    "newsletters_with_extracted_news",
    "newsletters_without_extracted_news",
    "newsletters_in_audio",
    "newsletters_outside_audio",
    "unique_facts",
    "word_count",
    "voice_blocks",
    "voice_total",
    "speech_reused_blocks",
    "model_retries",
    "model_validation_failures",
    "model_validation_recoveries",
    "script_patch_repairs",
    "script_full_repairs",
    "extraction_metadata_repairs",
    "model_request_bytes", "model_response_bytes", "model_accepted_ms", "model_rejected_ms",
    "script_patch_edits", "script_patch_response_bytes", "script_full_response_bytes",
    "speech_characters", "speech_samples",
    "evidence_passages_input", "evidence_passages_sent", "evidence_context_chars_saved",
    "progress_updates_failed",
    "paper_completion_reserve_seconds", "paper_allowance_seconds",
    "paper_budget_skipped", "paper_budget_exhausted",
}
OPERATIONS = {"model", "voice_model", "speech", "speech_prepare", "speech_pipeline",
              "speech_write", "speech_edges", "audio_encode", "paper_render"}
EVENTS = {"start", "stage", "counts", "operation", "heartbeat", "finish"}
STATES = {"running", "completed", "failed", "interrupted"}
REASONS = {
    "sync_pending",
    "time_budget",
    "interrupted",
    "subprocess_timeout",
    "model_failure",
    "verification_failure",
    "audio_failure",
    "publish_failure",
    "no_content",
    "cost_guard",
    "other",
}
RESULTS = {"idle", "already-claimed", "published", "completed", "failed"}
FAILURE_CODES = {
    *CLI_FAILURE_CODES,
    "extraction_section", "extraction_section_limit", "extraction_confidence", "extraction_rank",
    "extraction_duplicate_id", "extraction_evidence_shape", "extraction_consolidation",
    "source_alias_unknown", "source_alias_unused", "source_url_unowned",
    "source_fact_alignment", "source_fact_support_shape", "source_passage_unknown",
    "model_object_shape",
    "model_required_field",
    "model_list_shape",
    "model_dialogue_shape",
    "script_shape",
    "script_section_name",
    "script_duplicate_section",
    "script_section_coverage",
    "script_turn_length",
    "script_reference_url",
    "source_evidence",
    "json_invalid",
    "model_incomplete",
    "model_timeout",
    "model_exit",
    "script_coverage",
    "script_unsupported_reference",
    "script_hosts",
    "script_closing",
    "script_editor_credit",
    "script_quote_text",
    "script_quote_author",
    "script_quote_source",
    "script_closing_comment",
    "script_length",
    "model_structure",
    "verification_rejected",
}
_CURRENT: ContextVar[ProgressReporter | None] = ContextVar("generation_progress", default=None)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 3) if 0 <= value <= 10**9 and math.isfinite(value) else None


def safe_event(value: Any) -> dict[str, Any] | None:
    """Rebuild the protocol from fixed strings and bounded numbers only."""
    if not isinstance(value, dict):
        return None
    stage = value.get("stage")
    if (
        not isinstance(value.get("event"), str)
        or value["event"] not in EVENTS
        or not isinstance(value.get("status"), str)
        or value["status"] not in STATES
        or isinstance(stage, bool)
        or not isinstance(stage, int)
        or stage not in STAGES
    ):
        return None
    result = {"event": value["event"], "status": value["status"], "stage": stage}
    result["label"] = STAGES[stage]
    if isinstance(value.get("reason"), str) and value["reason"] in REASONS:
        result["reason"] = value["reason"]
    if isinstance(value.get("failure_code"), str) and value["failure_code"] in FAILURE_CODES:
        result["failure_code"] = value["failure_code"]
    for key in ("elapsed_seconds", "stage_elapsed_seconds"):
        number = _number(value.get(key))
        if number is None:
            return None
        result[key] = number
    try:
        result["at"] = datetime.fromisoformat(value["at"]).astimezone(UTC).isoformat()
    except (KeyError, TypeError, ValueError):
        return None
    for key, allowed in (("counters", COUNTERS), ("stage_seconds", {str(s) for s in STAGES})):
        raw = value.get(key, {})
        if isinstance(raw, dict):
            result[key] = {
                k: number
                for k, v in raw.items()
                if k in allowed and (number := _number(v)) is not None
            }
    raw_operations = value.get("operations", {})
    history = value.get("validation_history")
    if isinstance(history, list):
        result["validation_history"] = []
        for raw in history[-DIAGNOSTIC_LIMIT:]:
            if not isinstance(raw, dict):
                continue
            code, stage = raw.get("code"), raw.get("stage")
            attempt, elapsed = raw.get("attempt"), _number(raw.get("elapsed_seconds"))
            if (
                not isinstance(code, str) or code not in FAILURE_CODES
                or isinstance(stage, bool) or not isinstance(stage, int) or stage not in STAGES
                or isinstance(attempt, bool) or not isinstance(attempt, int)
                or not 1 <= attempt <= 10
                or elapsed is None
            ):
                continue
            result["validation_history"].append({
                "code": code, "stage": stage, "attempt": attempt,
                "elapsed_seconds": elapsed, **safe_detail(raw),
            })
    result["operations"] = {}
    if isinstance(raw_operations, dict):
        for name, raw in raw_operations.items():
            if name in OPERATIONS and isinstance(raw, dict):
                result["operations"][name] = {
                    k: number
                    for k, v in raw.items()
                    if k in {"count", "seconds"} and (number := _number(v)) is not None
                }
    return result


class ProgressReporter:
    def __init__(self, *, callback=None, interval=60, clock=time.monotonic, stream=None):
        self.callback = callback
        self.interval = interval
        self.clock = clock
        self.stream = stream if stream is not None else sys.stdout
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.changed = threading.Event()
        self.thread: threading.Thread | None = None
        self.token: Token | None = None
        self.status = "running"
        self.reason = ""
        self.failure_code = ""
        self.validation_history: list[dict[str, Any]] = []
        self.stage = 0
        self.counters: dict[str, float] = {}
        self.operations: dict[str, dict[str, float]] = {}
        self.stage_seconds: dict[str, float] = {}
        self.started = self.clock()
        self.stage_started = self.started
        self.finished: float | None = None

    def start(self) -> None:
        self.token = _CURRENT.set(self)
        self.emit("start")
        if self.interval > 0:
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()

    def snapshot(self, event="heartbeat") -> dict[str, Any]:
        with self.lock:
            current = self.finished if self.finished is not None else self.clock()
            totals = dict(self.stage_seconds)
            if self.finished is None:
                key = str(self.stage)
                totals[key] = totals.get(key, 0) + current - self.stage_started
            return (
                safe_event(
                    {
                        "event": event,
                        "status": self.status,
                        "stage": self.stage,
                        "reason": self.reason,
                        "failure_code": self.failure_code,
                        "validation_history": list(self.validation_history),
                        "elapsed_seconds": current - self.started,
                        "stage_elapsed_seconds": current - self.stage_started,
                        "at": datetime.now(UTC).isoformat(),
                        "stage_seconds": totals,
                        "counters": dict(self.counters),
                        "operations": {
                            name: dict(values) for name, values in self.operations.items()
                        },
                    }
                )
                or {}
            )

    def emit(self, event) -> None:
        with self.lock:
            print(
                PREFIX + json.dumps(self.snapshot(event), separators=(",", ":")),
                file=self.stream,
                flush=True,
            )

    def _heartbeat(self) -> None:
        while not self.stop.is_set():
            self.changed.wait(self.interval)
            self.changed.clear()
            if self.stop.is_set():
                return
            self.emit("heartbeat")
            if self.callback:
                try:
                    self.callback(self.snapshot())
                except Exception:
                    # Observability must never abort or retry a generation.
                    with self.lock:
                        self.counters["progress_updates_failed"] = (
                            self.counters.get("progress_updates_failed", 0) + 1
                        )

    def set_stage(self, stage: int) -> None:
        if stage not in STAGES:
            return
        with self.lock:
            current = self.clock()
            key = str(self.stage)
            self.stage_seconds[key] = self.stage_seconds.get(key, 0) + current - self.stage_started
            self.stage, self.stage_started = stage, current
            self.emit("stage")
            self.changed.set()

    def count(self, **values: int) -> None:
        with self.lock:
            for key, value in values.items():
                if key in COUNTERS and (number := _number(value)) is not None:
                    self.counters[key] = number
            self.emit("counts")

    def operation(self, name: str, seconds: float) -> None:
        if name not in OPERATIONS:
            return
        with self.lock:
            entry = self.operations.setdefault(name, {"count": 0, "seconds": 0})
            entry["count"] += 1
            entry["seconds"] += seconds
            self.emit("operation")

    def finish(
        self, status: str, *, reason: str = "", failure_code: str = "",
    ) -> dict[str, Any]:
        self.stop.set()
        self.changed.set()
        with self.lock:
            if self.finished is None:
                self.finished = self.clock()
                key = str(self.stage)
                self.stage_seconds[key] = (
                    self.stage_seconds.get(key, 0) + self.finished - self.stage_started
                )
                self.status = status if status in STATES else "failed"
                self.reason = reason if reason in REASONS else ""
                # A rejected attempt is not the terminal cause. Callers must
                # supply the actual final exception's fixed code explicitly.
                self.failure_code = (
                    failure_code if self.status == "failed"
                    and isinstance(failure_code, str) and failure_code in FAILURE_CODES else ""
                )
                self.emit("finish")
            return self.snapshot("finish")

    def close(self) -> None:
        self.stop.set()
        self.changed.set()
        if self.thread:
            self.thread.join(timeout=1)
        if self.token is not None:
            _CURRENT.reset(self.token)
            self.token = None


def stage(number: int) -> None:
    if reporter := _CURRENT.get():
        reporter.set_stage(number)


def counts(**values: int) -> None:
    if reporter := _CURRENT.get():
        reporter.count(**values)


def record_failure_code(code: str) -> None:
    if isinstance(code, str) and code in FAILURE_CODES and (reporter := _CURRENT.get()):
        with reporter.lock:
            if reporter.finished is not None:
                return
            reporter.failure_code = code
            reporter.emit("counts")


def clear_failure_code() -> None:
    """Clear a recovered attempt without erasing retry/failure measurements."""
    if reporter := _CURRENT.get():
        with reporter.lock:
            if reporter.finished is None and reporter.failure_code:
                reporter.failure_code = ""
                reporter.emit("counts")


def increment(name: str) -> None:
    if reporter := _CURRENT.get():
        with reporter.lock:
            if reporter.finished is None:
                reporter.count(**{name: int(reporter.counters.get(name, 0)) + 1})


def add_metric(name: str, amount: int) -> None:
    """Sum fixed numeric measurements, never text or identities; no new cloud write."""
    if (name not in COUNTERS or isinstance(amount, bool) or not isinstance(amount, int)
            or not 0 <= amount <= 10**9):
        return
    if reporter := _CURRENT.get():
        with reporter.lock:
            if reporter.finished is None:
                # The next ordinary event/heartbeat carries this; no extra per-metric log.
                reporter.counters[name] = min(10**9, int(reporter.counters.get(name, 0)) + amount)


def record_validation(code: str, attempt: int, detail: dict | None = None) -> None:
    """Retain each rejection's safe constraint even if later stages clear its code."""
    if not isinstance(code, str) or code not in FAILURE_CODES:
        return
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10:
        return
    if reporter := _CURRENT.get():
        with reporter.lock:
            if reporter.finished is not None:
                return
            reporter.validation_history.append({
                "code": code, "stage": reporter.stage, "attempt": attempt,
                "elapsed_seconds": reporter.clock() - reporter.started, **safe_detail(detail),
            })
            reporter.validation_history = reporter.validation_history[-DIAGNOSTIC_LIMIT:]
            reporter.emit("counts")


@contextmanager
def timed_operation(name: str):
    reporter = _CURRENT.get()
    started = time.monotonic()
    try:
        yield
    finally:
        if reporter:
            reporter.operation(name, time.monotonic() - started)


def filter_stream(source, destination, result_path: Path) -> None:
    """Consume raw child output, forwarding allowlisted progress immediately."""
    result = ""
    for line in source:
        if line.startswith(PREFIX):
            try:
                event = safe_event(json.loads(line[len(PREFIX) :]))
            except (TypeError, ValueError):
                event = None
            if event:
                print(
                    PREFIX + json.dumps(event, separators=(",", ":")), file=destination, flush=True
                )
                if event["event"] == "finish":
                    print(
                        f"Generation timing // {event['status']} // "
                        f"{event['elapsed_seconds']:.1f}s total",
                        file=destination,
                        flush=True,
                    )
                    for number, seconds in event.get("stage_seconds", {}).items():
                        print(
                            f"  Stage {number}/8 ({STAGES[int(number)]}): {seconds:.1f}s",
                            file=destination,
                            flush=True,
                        )
                    for name, operation in event["operations"].items():
                        print(
                            f"  Operation {name}: {operation.get('seconds', 0):.1f}s // "
                            f"{operation.get('count', 0):.0f} calls (included in stage totals)",
                            file=destination,
                            flush=True,
                        )
        # Only the fixed terminal state is retained; raw CLI output is discarded.
        matches = re.findall(r'"status"\s*:\s*"([a-z-]+)"', line)
        for match in matches:
            if match in RESULTS:
                result = match
    result_path.write_text(json.dumps({"status": result}), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", required=True, type=Path)
    args = parser.parse_args()
    filter_stream(sys.stdin, sys.stdout, args.result_file)


if __name__ == "__main__":
    main()
