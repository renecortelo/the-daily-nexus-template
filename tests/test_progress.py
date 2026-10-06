import io
import json
import tempfile
import threading
from pathlib import Path
from unittest import TestCase

from audiodigest.progress import (
    PREFIX,
    ProgressReporter,
    counts,
    filter_stream,
    safe_event,
    stage,
    timed_operation,
)


class ProgressTests(TestCase):
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
