from datetime import UTC, date, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.cloud_probe import cloud_work_is_due
from tests.test_jobs import schedule_payload


class _ProbeClient:
    def __init__(self, schedules, requests=None):
        self.schedules = schedules
        self.requests = requests or []
        self.collection_reads: list[str] = []
        self.writes = []
        self.execution_statuses = {}

    def authenticate(self):
        return "test-token"

    def list_private_collection(self, name, **kwargs):
        self.collection_reads.append(name)
        if name == "schedules":
            return [dict(item) for item in self.schedules]
        if name == "runRequests":
            return [dict(item) for item in self.requests]
        return []

    def private_execution_status(self, execution_id, episode_date):
        return self.execution_statuses.get((execution_id, episode_date), "")

    def set_private_document(self, collection, document_id, data):
        self.writes.append((collection, document_id, data))

    def patch_private_document(self, collection, document_id, data):
        self.writes.append((collection, document_id, data))


class CloudProbeTests(TestCase):
    def test_exact_clock_occurrence_survives_a_midnight_delay(self):
        schedule = schedule_payload()
        schedule["document_id"] = "weekday-morning"
        schedule.pop("scheduleId")
        client = _ProbeClient([schedule])
        with patch("audiodigest.cloud_probe.FirebaseWebRunnerClient", return_value=client):
            due = cloud_work_is_due(
                Path("config.example.toml"),
                now=datetime(2026, 7, 28, 0, 10, tzinfo=UTC),
                schedule_id="weekday-morning",
                schedule_date=date(2026, 7, 27),
            )
        self.assertTrue(due)
        self.assertEqual("queued", client.writes[-1][2]["state"])

    def test_exact_clock_occurrence_never_checks_manual_queue(self):
        schedule = schedule_payload()
        schedule["document_id"] = "weekday-morning"
        schedule.pop("scheduleId")
        request = {
            "document_id": "manual-request",
            "requestedDate": "2026-07-27",
            "requestedAt": "2026-07-27T02:00:00Z",
            "status": "queued",
        }
        client = _ProbeClient([schedule], [request])
        with patch("audiodigest.cloud_probe.FirebaseWebRunnerClient", return_value=client):
            due = cloud_work_is_due(
                Path("config.example.toml"),
                now=datetime(2026, 7, 28, 0, 10, tzinfo=UTC),
                schedule_id="weekday-morning",
                # Sunday is deliberately not in this schedule's weekdays.
                schedule_date=date(2026, 7, 26),
            )
        self.assertFalse(due)
        self.assertNotIn("runRequests", client.collection_reads)

    def test_failed_schedule_is_not_reawakened(self):
        schedule = schedule_payload()
        schedule["document_id"] = "weekday-morning"
        schedule.pop("scheduleId")
        client = _ProbeClient([schedule])
        client.execution_statuses[("weekday-morning", "2026-07-26")] = "failed"
        with patch("audiodigest.cloud_probe.FirebaseWebRunnerClient", return_value=client):
            due = cloud_work_is_due(
                Path("config.example.toml"),
                now=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
                schedule_id="weekday-morning",
                schedule_date=date(2026, 7, 27),
            )
        self.assertFalse(due)

    def test_failed_manual_request_is_not_reawakened(self):
        request = {
            "document_id": "manual-request",
            "requestedDate": "2026-07-27",
            "requestedAt": "2026-07-27T02:00:00Z",
            "status": "queued",
        }
        client = _ProbeClient([], [request])
        client.execution_statuses[("request-manual-request", "2026-07-27")] = "failed"
        with patch("audiodigest.cloud_probe.FirebaseWebRunnerClient", return_value=client):
            due = cloud_work_is_due(
                Path("config.example.toml"),
                now=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
            )
        self.assertFalse(due)
        failure = [
            item for item in client.writes
            if item[0] == "runRequests" and item[1] == "manual-request"
        ]
        self.assertEqual("failed", failure[0][2]["status"])
