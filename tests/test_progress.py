import io
import json
import tempfile
import threading
from pathlib import Path
from unittest import TestCase

from audiodigest.progress import (
    FAILURE_CODES,
    PREFIX,
    ProgressReporter,
    clear_failure_code,
    counts,
    filter_stream,
    record_failure_code,
    safe_event,
    stage,
    timed_operation,
)


class ProgressTests(TestCase):
    def test_recovered_attempt_clears_code_but_retains_measurements(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            record_failure_code("script_coverage")
            counts(model_validation_failures=2, model_retries=1)
            clear_failure_code()
            self.assertNotIn("failure_code", reporter.snapshot())
            self.assertEqual(2, reporter.snapshot()["counters"]["model_validation_failures"])
            self.assertEqual(1, reporter.snapshot()["counters"]["model_retries"])
            terminal = reporter.finish("failed", failure_code="verification_rejected")
            record_failure_code("json_invalid")
            clear_failure_code()
            self.assertEqual(terminal, reporter.snapshot("finish"))
        finally:
            reporter.close()

    def test_terminal_code_is_explicit_not_a_recovered_or_unrelated_attempt(self):
        for status, reason, code, expected in (
            ("completed", "", "script_coverage", None),
            ("completed", "sync_pending", "script_coverage", None),
            ("interrupted", "interrupted", "script_coverage", None),
            ("failed", "verification_failure", "", None),
            ("failed", "verification_failure", "verification_rejected", "verification_rejected"),
            ("failed", "model_failure", "script_hosts", "script_hosts"),
            ("failed", "other", "sensitive arbitrary text", None),
            ("failed", "other", [], None),
        ):
            with self.subTest(status=status, reason=reason, code=code):
                reporter = ProgressReporter(interval=0, stream=io.StringIO())
                reporter.failure_code = "script_coverage"
                event = reporter.finish(status, reason=reason, failure_code=code)
                self.assertEqual(expected, event.get("failure_code"))
                # Finishing again cannot rewrite a terminal profile.
                self.assertEqual(event, reporter.finish("failed", failure_code="json_invalid"))

    def test_detailed_closing_codes_survive_without_exception_content(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        for code in ("script_editor_credit", "script_quote_text", "script_quote_author",
                     "script_quote_source", "script_closing_comment"):
            self.assertIn(code, FAILURE_CODES)
            event = reporter.snapshot()
            event.update(failure_code=code, exception="private detail")
            self.assertEqual(code, safe_event(event)["failure_code"])
            self.assertNotIn("exception", safe_event(event))

    def test_only_fixed_diagnostic_codes_survive_the_privacy_filter(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        reporter.start()
        try:
            record_failure_code("json_invalid")
            record_failure_code("sensitive arbitrary text")
            record_failure_code([])
            self.assertEqual("json_invalid", reporter.snapshot()["failure_code"])
            for value in ("sensitive arbitrary text", {}, []):
                event = reporter.snapshot()
                event["failure_code"] = value
                self.assertNotIn("failure_code", safe_event(event))
        finally:
            reporter.close()

    def test_stage_totals_include_repairs_and_stop_at_finish(self):
        now = [0.0]
        reporter = ProgressReporter(clock=lambda: now[0], interval=0, stream=io.StringIO())
        reporter.start()
        try:
            now[0] = 2
            stage(5)
            now[0] = 5
            stage(5)  # A verifier repair remains part of stage five.
            counts(stories=26)
            with timed_operation("model"):
                pass
            now[0] = 9
            profile = reporter.finish("failed")
            now[0] = 100
            self.assertEqual(9, reporter.snapshot()["elapsed_seconds"])
            self.assertEqual({"0": 2, "5": 7}, profile["stage_seconds"])
            self.assertEqual(26, profile["counters"]["stories"])
            self.assertEqual(1, profile["operations"]["model"]["count"])
            self.assertEqual("failed", profile["status"])
        finally:
            reporter.close()
        stage(7)  # Closed reporters must not capture another run's progress.
        self.assertEqual(5, reporter.snapshot()["stage"])

    def test_filter_streams_only_safe_data_before_process_completion(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        event = reporter.snapshot("stage")
        event.update({"label": "private text", "token": "private-token", "url": "private-url"})
        event["counters"]["private-account"] = 1
        destination = io.StringIO()

        def child_output():
            yield PREFIX + json.dumps(event) + "\n"
            self.assertIn(PREFIX, destination.getvalue())
            yield "Private newsletter body, credentials and error details\n"
            yield '{"status": "published", "execution_id": "private-task"}\n'

        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result.json"
            filter_stream(child_output(), destination, result)
            self.assertEqual({"status": "published"}, json.loads(result.read_text()))
        self.assertNotIn("private", destination.getvalue().lower())
        self.assertEqual(1, len(destination.getvalue().splitlines()))

    def test_malformed_or_unbounded_events_cannot_break_or_leak_through_filter(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        event = reporter.snapshot()
        for key, invalid in (
            ("event", {}),
            ("status", []),
            ("stage", True),
            ("elapsed_seconds", float("nan")),
        ):
            self.assertIsNone(safe_event({**event, key: invalid}))
        event["counters"] = {"links": 10**400, "stories": "private-text"}
        self.assertEqual({}, safe_event(event)["counters"])

    def test_final_log_summary_is_readable_and_uses_only_sanitized_fields(self):
        reporter = ProgressReporter(interval=0, stream=io.StringIO())
        event = reporter.snapshot("finish")
        event.update(status="failed", label="private content", stage_seconds={"7": 125.5})
        event["operations"] = {"speech": {"count": 3, "seconds": 124}}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            filter_stream([PREFIX + json.dumps(event)], output, Path(directory) / "result")
        self.assertIn("Generation timing // failed", output.getvalue())
        self.assertIn("Stage 7/8 (Rendering audio): 125.5s", output.getvalue())
        self.assertIn("Operation speech: 124.0s // 3 calls", output.getvalue())
        self.assertNotIn("private content", output.getvalue())

    def test_heartbeat_keeps_reporting_when_generation_produces_no_output(self):
        received = threading.Event()
        snapshots = []

        def callback(profile):
            snapshots.append(profile)
            received.set()

        reporter = ProgressReporter(callback=callback, interval=0.02, stream=io.StringIO())
        reporter.start()
        try:
            stage(4)
            self.assertTrue(received.wait(2))
            self.assertEqual(4, snapshots[-1]["stage"])
        finally:
            reporter.finish("interrupted")
            reporter.close()

    def test_monitor_outage_does_not_interrupt_generation_or_retry_it(self):
        def unavailable(_profile):
            raise RuntimeError("sensitive connection details")

        reporter = ProgressReporter(callback=unavailable, interval=0.01, stream=io.StringIO())
        reporter.start()
        try:
            # Wake and wait for the first heartbeat without doing any real work.
            reporter.changed.set()
            for _ in range(100):
                if reporter.snapshot()["counters"].get("progress_updates_failed"):
                    break
                threading.Event().wait(0.01)
            self.assertGreater(reporter.snapshot()["counters"]["progress_updates_failed"], 0)
            self.assertNotIn("sensitive", reporter.stream.getvalue())
            self.assertEqual("completed", reporter.finish("completed")["status"])
        finally:
            reporter.close()
