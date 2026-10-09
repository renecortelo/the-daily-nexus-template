import io
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest.jobs import ScheduledJob
from audiodigest.progress import ProgressReporter
from audiodigest.publisher import _publication_asset_metrics
from audiodigest.resource_metrics import append_sample, measured_profile, safe_sample
from audiodigest.web_scheduler import _save_timing_profile, _schedule_timing
from tests.test_jobs import schedule_payload


def profile():
    reporter = ProgressReporter(interval=0, stream=io.StringIO())
    value = reporter.finish("completed")
    value["at"] = "2026-10-09T05:20:00+00:00"
    return value


class ResourceMetricTests(TestCase):
    def test_setup_queue_generation_and_target_measurements_are_distinct(self):
        start = datetime(2026, 10, 9, 5, 1, tzinfo=UTC)
        result = measured_profile(
            profile(),
            started_at=start,
            expected_start=start - timedelta(minutes=1),
            ready_by=start + timedelta(minutes=9),
            environment={
                "GITHUB_ACTIONS": "true",
                "TDN_RUNNER_STARTED_EPOCH": str((start - timedelta(seconds=40)).timestamp()),
                "TDN_RUNNER_DEADLINE_EPOCH": str((start + timedelta(minutes=50)).timestamp()),
            },
        )
        self.assertEqual("scheduled", result["origin"])
        self.assertEqual(60, result["measurements"]["start_delay_seconds"])
        self.assertEqual(40, result["measurements"]["setup_seconds"])
        self.assertEqual(1180, result["measurements"]["job_observed_seconds"])
        self.assertEqual(600, result["measurements"]["ready_by_late_seconds"])
        self.assertEqual(1860, result["measurements"]["protected_remaining_seconds"])

    def test_missing_or_invalid_measurements_are_not_reported_as_zero(self):
        self.assertIsNone(safe_sample({**profile(), "status": "running"}))
        result = measured_profile(
            profile(),
            started_at=datetime(2026, 10, 9, 5, tzinfo=UTC),
            environment={"GITHUB_ACTIONS": "true", "TDN_RUNNER_STARTED_EPOCH": "nan"},
        )
        self.assertEqual({}, result["measurements"])
        self.assertEqual({}, result["resources"])
        with self.assertRaises(ValueError):
            measured_profile({}, started_at=datetime.now(UTC))

    def test_samples_strip_private_and_arbitrary_fields_and_retain_failures(self):
        value = {
            **profile(),
            "taskName": "private text",
            "url": "https://example.com/secret",
            "measurements": {
                "setup_seconds": 2,
                "password": "sensitive",
                "job_observed_seconds": float("inf"),
            },
            "resources": {"new_audio_bytes": 123, "token": "sensitive"},
            "origin": [],
        }
        result = safe_sample(value)
        self.assertEqual({"setup_seconds": 2}, result["measurements"])
        self.assertNotIn("private", json.dumps(result))
        failed = {**profile(), "status": "failed", "at": "2026-10-08T05:20:00+00:00"}
        result = append_sample({"recent": [failed, {"status": "private"}]}, safe_sample(value))
        self.assertEqual(["failed", "completed"], [row["status"] for row in result["recent"]])

    def test_history_is_bounded_and_idempotent_and_accepts_legacy_profile(self):
        base = profile()
        latest = safe_sample(base)
        result = append_sample(base, latest)
        self.assertEqual(1, len(result["recent"]))
        rows = [{**base, "at": f"2026-10-{day:02d}T05:20:00+00:00"} for day in range(1, 29)]
        result = append_sample({"recent": rows}, latest)
        self.assertEqual(20, len(result["recent"]))
        self.assertEqual(20, len(append_sample(result, latest)["recent"]))
        self.assertEqual(1, len(append_sample({"recent": "bad"}, latest)["recent"]))

    def test_noncritical_profile_failures_do_not_abort_or_retry_generation(self):
        client = Mock()
        client.get_private_runner_document.side_effect = RuntimeError("private details")
        _save_timing_profile(client, profile(), started_at=datetime.now(UTC))
        self.assertEqual(1, client.set_private_document.call_count)
        client.set_private_document.side_effect = RuntimeError("private details")
        _save_timing_profile(client, profile(), started_at=datetime.now(UTC))
        self.assertEqual(2, client.set_private_document.call_count)

    def test_scheduled_targets_use_occurrence_day_and_timezone_not_episode_day(self):
        payload = schedule_payload()
        payload.update(timezone="Europe/Madrid", startTime="04:45", readyBy="06:00")
        job = ScheduledJob.from_dict(payload, schedule_id="synthetic-task")
        context = _schedule_timing(job, datetime(2026, 10, 9, 8, tzinfo=UTC))
        self.assertEqual(
            datetime(2026, 10, 9, 2, 45, tzinfo=UTC), context["expected_start"].astimezone(UTC)
        )
        # A late targeted alarm crossing midnight keeps the original local target.
        context = _schedule_timing(
            job, datetime(2026, 10, 10, 0, tzinfo=UTC), datetime(2026, 10, 9).date()
        )
        self.assertEqual(datetime(2026, 10, 9, 4, tzinfo=UTC), context["ready_by"].astimezone(UTC))

    def test_asset_measurement_excludes_paths_and_missing_bytes_stay_unknown(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            audio = root / "episode.mp3"
            audio.write_bytes(b"synthetic")
            paper = root / "edition.pdf"
            paper.write_bytes(b"paper")
            (root / "edition-1.png").write_bytes(b"image")
            current = {"audio_path": str(audio), "newspaper_path": str(paper)}
            result = _publication_asset_metrics(
                [current, {"remote_only": True, "audio_bytes": 100}], current, 40, 30
            )
            self.assertEqual(109, result["retained_audio_bytes"])
            self.assertEqual(5, result["new_paper_bytes"])
            self.assertEqual(5, result["new_preview_bytes"])
            self.assertNotIn(str(root), json.dumps(result))
            with patch.object(Path, "stat", side_effect=OSError("private path")):
                result = _publication_asset_metrics([current], current, 40, 30)
            self.assertNotIn("new_audio_bytes", result)
            self.assertNotIn("new_paper_bytes", result)
