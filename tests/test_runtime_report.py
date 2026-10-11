import io
import json
from datetime import UTC, datetime, timedelta
from unittest import TestCase
from unittest.mock import patch

from audiodigest.progress import PREFIX, ProgressReporter
from audiodigest.runtime_report import (
    MAX_LINE_CHARACTERS,
    MAX_SAMPLES,
    main,
    render_text,
    summarize,
    terminal_samples,
)


def sample(*, status="completed", elapsed=100, minute=0):
    reporter = ProgressReporter(interval=0, stream=io.StringIO())
    profile = reporter.finish(status)
    profile.update(
        at=(datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minute)).isoformat(),
        elapsed_seconds=elapsed,
        stage_seconds={"4": elapsed / 4, "7": elapsed * 3 / 4},
        operations={"model": {"count": 2, "seconds": elapsed / 4}},
        counters={"model_retries": 1, "newsletters": 2},
    )
    return profile


class RuntimeReportTests(TestCase):
    def test_github_prefixes_and_duplicate_terminal_events(self):
        value = sample()
        lines = ["raw private output\n", PREFIX + json.dumps(value),
                 "job\tstep\ttimestamp " + PREFIX + json.dumps(value)]
        self.assertEqual(1, len(terminal_samples(lines)))

    def test_nonterminal_and_malformed_records_are_not_inferred(self):
        running = {**sample(), "event": "heartbeat", "status": "running"}
        self.assertEqual([], terminal_samples([
            PREFIX + "not JSON", PREFIX + json.dumps(running),
            PREFIX + json.dumps({"status": "completed"}),
            PREFIX + "x" * MAX_LINE_CHARACTERS,
        ]))

    def test_privacy_allowlist_applies_to_direct_inputs_and_rendering(self):
        value = sample()
        sentinel = "private sentinel"
        value.update(taskName=sentinel, token=sentinel,
                     url="https://example.com/private-sentinel",
                     exception="private sentinel", failure_code="private sentinel")
        value["counters"]["password"] = sentinel
        value["operations"]["private sentinel"] = {"seconds": 30}
        value["stage_seconds"]["private sentinel"] = 30
        value["resources"] = {"token": "private sentinel"}
        report = summarize([value])
        self.assertNotIn("sentinel", json.dumps(report))
        self.assertNotIn("sentinel", render_text(report))
        self.assertNotIn("2026-10-01", json.dumps(report))

    def test_failures_are_separate_and_operations_are_not_added_to_stages(self):
        failed = sample(status="failed", elapsed=400, minute=1)
        failed["failure_code"] = "verification_rejected"
        report = summarize([sample(), failed])
        self.assertEqual(500, report["all_attempts"]["generation"]["total_seconds"])
        self.assertEqual(100, report["completed_only"]["generation"]["total_seconds"])
        self.assertEqual(400, report["unsuccessful_generation"]["total_seconds"])
        self.assertEqual({"verification_rejected": 1}, report["failure_codes"])
        self.assertEqual(7, report["all_attempts"]["stages"][0]["stage"])
        self.assertEqual(75, report["all_attempts"]["stages"][0]
                         ["share_of_measured_stage_percent"])

    def test_missing_stage_observations_not_turned_into_zeros(self):
        missing = sample(minute=1)
        missing["stage_seconds"] = {"4": 25}
        missing["operations"] = {"speech": {"count": 1}}
        report = summarize([sample(), missing])
        stages = {row["stage"]: row for row in report["all_attempts"]["stages"]}
        self.assertEqual(1, stages[7]["samples"])
        self.assertEqual(2, stages[4]["samples"])
        self.assertNotIn("speech", report["all_attempts"]["operations_nested_in_stages"])
        self.assertEqual(0, report["completed_ready_by"]["measured_samples"])

    def test_nonfinite_and_negative_metrics_removed_and_job_time_not_summed(self):
        value = sample()
        value["operations"]["speech"] = {"seconds": float("inf")}
        value["stage_seconds"]["6"] = -20
        value["measurements"] = {"job_observed_seconds": 1000}
        report = summarize([value])
        encoded = json.dumps(report, allow_nan=False)
        self.assertNotIn("job_observed_seconds", encoded)
        self.assertNotIn("speech", encoded)
        self.assertNotIn('"stage": 6', encoded)

    def test_bounds_and_deduplication_apply_to_direct_and_log_inputs(self):
        values = [sample(minute=minute) for minute in range(MAX_SAMPLES + 5)]
        values.append(values[-1])
        self.assertEqual(MAX_SAMPLES, summarize(values)["sample_count"])
        lines = (PREFIX + json.dumps(value) for value in values)
        self.assertEqual(MAX_SAMPLES, len(terminal_samples(lines)))

    def test_no_observations_are_not_zero_allowance_or_zero_jobs(self):
        report = summarize([])
        self.assertEqual({"samples": 0}, report["all_attempts"]["generation"])
        self.assertIn("no measurements", render_text(report))
        self.assertIn("remaining allowance", render_text(report))

    def test_direct_malformed_inputs_are_rejected_without_echoing_them(self):
        values = [None, [], {**sample(), "at": "private sentinel"},
                  {**sample(), "elapsed_seconds": float("nan")},
                  {**sample(), "at": "9999-12-31T23:59:59-23:00"}]
        self.assertEqual(0, summarize(values)["sample_count"])

    def test_only_completed_measured_targets_count(self):
        values = [sample(minute=index) for index in range(3)]
        for value in values:
            value["measurements"] = {"ready_by_late_seconds": 0}
        values[1]["measurements"]["ready_by_late_seconds"] = 10
        values[2]["status"] = "failed"
        self.assertEqual({"measured_samples": 2, "late_samples": 1},
                         summarize(values)["completed_ready_by"])

    def test_cli_stdout_json_is_aggregate_only_without_writing_files(self):
        with patch("sys.argv", ["runtime_report", "--format", "json"]), \
             patch("sys.stdin", io.StringIO(PREFIX + json.dumps(sample()))), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            main()
        self.assertEqual(1, json.loads(output.getvalue())["sample_count"])

    def test_cli_unreadable_input_does_not_echo_private_path(self):
        with patch("sys.argv", ["runtime_report", "--input", "private-sentinel"]), \
             patch("pathlib.Path.open", side_effect=OSError("private-sentinel")), \
             patch("sys.stderr", new_callable=io.StringIO) as output, \
             self.assertRaises(SystemExit) as caught:
            main()
        self.assertEqual(2, caught.exception.code)
        self.assertNotIn("private-sentinel", output.getvalue())

    def test_cli_empty_input_fails_instead_of_implying_successful_profiling(self):
        with patch("sys.argv", ["runtime_report"]), \
             patch("sys.stdin", io.StringIO("private sentinel")), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("sys.stderr", new_callable=io.StringIO) as error, \
             self.assertRaises(SystemExit) as caught:
            main()
        self.assertEqual(2, caught.exception.code)
        self.assertNotIn("private sentinel", error.getvalue())
