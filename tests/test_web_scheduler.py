import json
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from audiodigest.closing_quotes import load_closing_quotes
from audiodigest.config import load_settings
from audiodigest.jobs import GenerationParameters
from audiodigest.progress import stage
from audiodigest.web_runner import WebRunnerError
from audiodigest.web_scheduler import (
    GenerationInterrupted,
    TerminalTaskFailure,
    _execute_generation,
    _next_publication_sequence,
    _published_metadata,
    run_web_runner_tick,
)
from tests.test_jobs import schedule_payload


class _FakeWebClient:
    def __init__(self, schedules, requests=None, episodes=None):
        self.schedules = schedules
        self.requests = requests or []
        self.episodes = episodes or []
        self.writes = []
        self.execution_statuses = {}

    def authenticate(self):
        return "test-token"

    def list_private_collection(self, name, **kwargs):
        if name == "schedules":
            return [dict(item) for item in self.schedules]
        if name == "runRequests":
            return [dict(item) for item in self.requests]
        if name == "episodes":
            return [dict(item) for item in self.episodes]
        return []

    def set_private_document(self, collection, document_id, data):
        self.writes.append((collection, document_id, data))

    def patch_private_document(self, collection, document_id, data):
        self.writes.append((collection, document_id, data))

    def claim_private_execution(self, execution_id, episode_date):
        key = (execution_id, episode_date)
        if self.execution_statuses.get(key) in {"running", "completed"}:
            return False
        self.execution_statuses[key] = "running"
        return True

    def private_execution_status(self, execution_id, episode_date):
        return self.execution_statuses.get((execution_id, episode_date), "")

    def finish_private_execution(self, execution_id, episode_date, *, status):
        self.execution_statuses[(execution_id, episode_date)] = status
        self.writes.append(
            (
                "executions",
                f"{execution_id}:{episode_date}",
                {"status": status},
            )
        )


class WebSchedulerTests(TestCase):
    def test_quote_identity_reaches_owner_metadata_even_without_optional_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            quote = load_closing_quotes(settings.podcast.closing_quotes_path)[0]
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"closing_quote_id": quote.quote_id}), encoding="utf-8")

            class StubDatabase:
                def episode_for_date(self, _day):
                    return {
                        "status": "staged", "title": "An edition", "guid": "an-edition",
                        "duration_seconds": 1200, "show_notes": [], "manifest_path": str(manifest),
                    }

            _, metadata = _published_metadata(
                settings, StubDatabase(), episode_date=date(2026, 10, 5),
                execution_id="execution", publication_label="An edition", publication_sequence=1,
            )
            self.assertEqual(quote.quote_id, metadata["closingQuoteId"])
            self.assertEqual([], metadata["transcript"])

    def _settings(self, root: Path):
        settings = load_settings("config.example.toml")
        settings.app.runtime_dir = root / "runtime"
        settings.web.enabled = True
        settings.web.firebase_api_key = "AIza" + ("x" * 35)
        settings.web.owner_uid = "owner-uid"
        settings.firebase.project_id = "example-private-project"
        settings.firebase.base_url = "https://example-private-project.web.app"
        return settings

    def test_idle_tick_updates_runner_without_generating(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            client = _FakeWebClient([])
            result = run_web_runner_tick(
                self._settings(Path(name)),
                now=datetime(2026, 7, 27, 10, 0, tzinfo=UTC),
                client=client,
            )
            self.assertEqual("idle", result["status"])
            self.assertEqual("idle", client.writes[-1][2]["state"])

    def test_same_named_publication_gets_next_readable_sequence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            client = _FakeWebClient(
                [],
                episodes=[
                    {
                        "episodeDate": "2026-12-31",
                        "publicationLabel": "TDN All",
                        "publicationSequence": 1,
                        "title": "12/31/2026 The Daily Nexus - TDN All - 001",
                    },
                    {
                        "episodeDate": "2026-12-31",
                        "title": "12/31/2026 The Daily Nexus - TDN All - 002",
                    },
                ],
            )
            self.assertEqual(
                3,
                _next_publication_sequence(
                    settings,
                    client,
                    episode_date=date(2026, 12, 31),
                    label="TDN All",
                ),
            )

    def test_same_day_cloud_runs_use_isolated_temporary_databases(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            parameters = GenerationParameters.from_dict(schedule_payload()["parameters"])
            client = _FakeWebClient([])
            received_settings = []

            class RecordingPipeline:
                def __init__(self, configured):
                    received_settings.append(configured)

                def run(self, **_kwargs):
                    return {"status": "published"}

            with patch(
                "audiodigest.web_scheduler._published_metadata",
                return_value=("episode", {"status": "published"}),
            ):
                for index in ("one", "two"):
                    _execute_generation(
                        settings,
                        client,
                        execution_id=f"same-day-{index}",
                        display_name="TDN All",
                        parameters=parameters,
                        episode_date=date(2026, 12, 31),
                        pipeline_factory=RecordingPipeline,
                        request_id=f"request-{index}",
                    )

            self.assertEqual(2, len(received_settings))
            self.assertNotEqual(
                received_settings[0].database_path,
                received_settings[1].database_path,
            )
            self.assertTrue(all(item.firebase.publish_enabled for item in received_settings))
            self.assertTrue(
                all(item.firebase.publish_mode == "automatic" for item in received_settings)
            )

    def test_cloud_quote_history_is_shared_across_schedules_not_from_ephemeral_database(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self._settings(Path(directory))
            catalog = load_closing_quotes(settings.podcast.closing_quotes_path)
            client = _FakeWebClient([], episodes=[
                {"status": "published", "closingQuoteId": catalog[0].quote_id},
                {"status": "published", "references": [catalog[1].source_url]},
            ])
            received = []

            class RecordingPipeline:
                def __init__(self, _configured):
                    pass

                def run(self, **kwargs):
                    received.append(kwargs["recent_quote_ids"])
                    return {"status": "published"}

            with patch("audiodigest.web_scheduler._published_metadata", return_value=(
                "episode", {"status": "published"},
            )):
                _execute_generation(
                    settings, client, execution_id="another-schedule",
                    display_name="Another edition",
                    parameters=GenerationParameters.from_dict(schedule_payload()["parameters"]),
                    episode_date=date(2026, 10, 5), pipeline_factory=RecordingPipeline,
                )
            self.assertEqual([[catalog[0].quote_id, catalog[1].quote_id]], received)

    def test_invalid_legacy_schedule_does_not_block_runner(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            invalid = schedule_payload()
            invalid["document_id"] = "legacy-invalid"
            invalid.pop("scheduleId")
            invalid["parameters"]["hostCount"] = 1
            invalid["parameters"]["soloName"] = "Nox"
            invalid["parameters"]["primaryVoice"] = "invalid-voice"
            client = _FakeWebClient([invalid])
            result = run_web_runner_tick(
                self._settings(Path(name)),
                now=datetime(2026, 7, 27, 10, 0, tzinfo=UTC),
                client=client,
            )
            self.assertEqual("idle", result["status"])
            self.assertIn("invalid saved schedule was skipped", client.writes[-1][2]["detail"])

    def test_same_day_schedules_are_allowed_as_distinct_episodes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            first = schedule_payload()
            first["document_id"] = "first"
            first.pop("scheduleId")
            second = schedule_payload()
            second["document_id"] = "second"
            second.pop("scheduleId")
            client = _FakeWebClient([first, second])
            with patch(
                "audiodigest.web_scheduler._execute_generation",
                return_value={"status": "started"},
            ) as execute:
                result = run_web_runner_tick(
                    self._settings(Path(name)),
                    now=datetime(2026, 7, 27, 4, 47, tzinfo=UTC),
                    client=client,
                )
            self.assertEqual("started", result["status"])
            self.assertEqual("first", execute.call_args.kwargs["execution_id"])

    def test_completed_schedule_is_skipped_instead_of_blocking_the_queue(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            item = schedule_payload()
            item["document_id"] = "weekday-morning"
            item.pop("scheduleId")
            client = _FakeWebClient([item])
            client.execution_statuses[("weekday-morning", "2026-07-26")] = "completed"
            result = run_web_runner_tick(
                settings,
                now=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
                client=client,
            )
            self.assertEqual("idle", result["status"])

    def test_failed_schedule_is_terminal_until_the_next_occurrence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = self._settings(Path(name))
            item = schedule_payload()
            item["document_id"] = "weekday-morning"
            item.pop("scheduleId")
            client = _FakeWebClient([item])
            client.execution_statuses[("weekday-morning", "2026-07-26")] = "failed"
            result = run_web_runner_tick(
                settings,
                now=datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
                client=client,
            )
            self.assertEqual("idle", result["status"])

    def test_targeted_clock_run_uses_exact_schedule_and_local_occurrence_date(self):
        first = schedule_payload()
        first["document_id"] = "first-schedule"
        first.pop("scheduleId")
        target = schedule_payload()
        target["document_id"] = "target-schedule"
        target.pop("scheduleId")
        client = _FakeWebClient([first, target])
        with patch(
            "audiodigest.web_scheduler._execute_generation",
            return_value={"status": "started"},
        ) as execute:
            result = run_web_runner_tick(
                self._settings(Path(__file__).parent),
                # The alarm crossed midnight, but it must retain Monday's
                # occurrence rather than becoming Tuesday's task.
                now=datetime(2026, 7, 28, 0, 10, tzinfo=UTC),
                client=client,
                schedule_id="target-schedule",
                schedule_date=date(2026, 7, 27),
            )
        self.assertEqual("started", result["status"])
        self.assertEqual("target-schedule", execute.call_args.kwargs["execution_id"])
        self.assertEqual(
            date(2026, 7, 26),
            execute.call_args.kwargs["episode_date"],
        )

    def test_targeted_clock_run_never_falls_back_to_a_manual_request(self):
        schedule = schedule_payload()
        schedule["document_id"] = "weekday-morning"
        schedule.pop("scheduleId")
        request = {
            "document_id": "manual-request",
            "requestedDate": "2026-07-27",
            "requestedAt": "2026-07-27T02:00:00Z",
            "status": "queued",
            "parameters": schedule_payload()["parameters"],
        }
        client = _FakeWebClient([schedule], [request])
        with patch("audiodigest.web_scheduler._execute_generation") as execute:
            result = run_web_runner_tick(
                self._settings(Path(__file__).parent),
                now=datetime(2026, 7, 28, 0, 10, tzinfo=UTC),
                client=client,
                schedule_id="weekday-morning",
                # Sunday is not an enabled weekday for this schedule.
                schedule_date=date(2026, 7, 26),
            )
        self.assertEqual("idle", result["status"])
        execute.assert_not_called()

    def test_targeted_clock_date_requires_schedule_id(self):
        with self.assertRaisesRegex(WebRunnerError, "schedule date requires"):
            run_web_runner_tick(
                self._settings(Path(__file__).parent),
                now=datetime(2026, 7, 27, 3, 0, tzinfo=UTC),
                client=_FakeWebClient([]),
                schedule_date=date(2026, 7, 27),
            )

    def test_old_queued_request_is_expired_without_generating(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            request = {
                "document_id": "old-request",
                "requestedDate": "2026-07-20",
                "requestedAt": "2026-07-20T02:00:00Z",
                "status": "queued",
                "parameters": schedule_payload()["parameters"],
            }
            client = _FakeWebClient([], [request])
            result = run_web_runner_tick(
                self._settings(Path(name)),
                now=datetime(2026, 7, 27, 3, 0, tzinfo=UTC),
                client=client,
            )
            self.assertEqual("idle", result["status"])
            expiry = [
                item[2]
                for item in client.writes
                if item[0] == "runRequests" and item[1] == "old-request"
            ]
            self.assertEqual("expired", expiry[0]["status"])

    def test_cloud_failure_detail_does_not_include_local_exception_text(self):
        class FailingPipeline:
            def __init__(self, _settings):
                pass

            def run(self, **_kwargs):
                raise RuntimeError("private C:\\secret\\newsletter path")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            request = {
                "document_id": "request-one",
                "requestedDate": "2026-07-27",
                "requestedAt": "2026-07-27T02:00:00Z",
                "status": "queued",
                "parameters": schedule_payload()["parameters"],
            }
            client = _FakeWebClient([], [request])
            with self.assertRaises(TerminalTaskFailure) as failure:
                run_web_runner_tick(
                    self._settings(Path(name)),
                    now=datetime(2026, 7, 27, 3, 0, tzinfo=UTC),
                    client=client,
                    pipeline_factory=FailingPipeline,
                )
            self.assertEqual("failed", failure.exception.result["status"])
            self.assertEqual("RuntimeError", failure.exception.result["error_type"])
            self.assertNotIn("secret", str(failure.exception.result))
            details = [
                item[2].get("detail", "")
                for item in client.writes
                if item[0] in {"runRequests", "runner"}
            ]
            self.assertTrue(any("inspect the private local runner log" in item for item in details))
            self.assertTrue(all("C:\\secret" not in item for item in details))

    def test_interrupted_generation_is_recorded_as_terminal_failure(self):
        class InterruptedPipeline:
            def __init__(self, _settings):
                pass

            def run(self, **_kwargs):
                stage(7)
                raise GenerationInterrupted("generation interrupted by signal 15")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            request = {
                "document_id": "request-interrupted",
                "requestedDate": "2026-07-27",
                "requestedAt": "2026-07-27T02:00:00Z",
                "status": "queued",
                "parameters": schedule_payload()["parameters"],
            }
            client = _FakeWebClient([], [request])
            with self.assertRaises(TerminalTaskFailure) as failure:
                run_web_runner_tick(
                    self._settings(Path(name)),
                    now=datetime(2026, 7, 27, 3, 0, tzinfo=UTC),
                    client=client,
                    pipeline_factory=InterruptedPipeline,
                )
            self.assertEqual(
                "GenerationInterrupted", failure.exception.result["error_type"]
            )
            self.assertEqual(
                "failed",
                client.execution_statuses[("request-request-interrupted", "2026-07-27")],
            )
            request_writes = [
                item[2]
                for item in client.writes
                if item[0] == "runRequests" and item[1] == "request-interrupted"
            ]
            self.assertEqual("failed", request_writes[-1]["status"])
            profile = next(
                item[2] for item in client.writes
                if item[0] == "runner" and item[1] == "lastProfile"
            )
            self.assertEqual(7, profile["stage"])
            self.assertEqual("interrupted", profile["status"])
            self.assertNotIn("execution_id", profile)
            self.assertIn("stage 7/8", request_writes[-1]["detail"])
