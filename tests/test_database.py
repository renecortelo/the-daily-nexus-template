import tempfile
from datetime import date
from pathlib import Path
from unittest import TestCase

from audiodigest.database import StateDatabase


class DatabaseTests(TestCase):
    def test_message_idempotency(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            database = StateDatabase(Path(name) / "state.sqlite3")
            self.assertFalse(database.is_processed("message-1"))
            database.mark_messages_processed(["message-1"], date(2026, 7, 25))
            self.assertTrue(database.is_processed("message-1"))

    def test_run_state_can_be_refreshed_after_app_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            database = StateDatabase(Path(name) / "state.sqlite3")
            day = date(2026, 7, 27)
            database.begin_run(day)
            self.assertEqual(database.run_for_date(day)["status"], "running")
            database.finish_run(day, "failed", "interrupted")
            self.assertEqual(database.run_for_date(day)["status"], "failed")

    def test_published_day_cannot_begin_again(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            audio = root / "episode.mp3"
            manifest = root / "manifest.json"
            audio.write_bytes(b"audio")
            manifest.write_text("{}", encoding="utf-8")
            database = StateDatabase(root / "state.sqlite3")
            day = date(2026, 7, 25)
            database.begin_run(day)
            database.stage_episode(
                episode_date=day,
                guid="guid-1",
                title="Title",
                audio_path=audio,
                manifest_path=manifest,
                checksum="abc",
                duration_seconds=120,
                show_notes=[],
            )
            database.mark_published(day)
            database.finish_run(day, "published")
            with self.assertRaises(RuntimeError):
                database.begin_run(day)

    def test_scheduled_execution_can_only_be_claimed_once(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            database = StateDatabase(Path(name) / "state.sqlite3")
            day = date(2026, 7, 29)
            self.assertTrue(database.claim_scheduled_execution("morning", day))
            self.assertFalse(database.claim_scheduled_execution("morning", day))
            database.finish_scheduled_execution("morning", day, "completed")
            execution = database.scheduled_execution("morning", day)
            self.assertIsNotNone(execution)
            self.assertEqual("completed", execution["status"])

    def test_episode_library_includes_newspaper_paths(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            audio = root / "episode.mp3"
            manifest = root / "manifest.json"
            newspaper = root / "edition.pdf"
            preview = root / "edition.png"
            for path in (audio, manifest, newspaper, preview):
                path.write_bytes(b"content")
            database = StateDatabase(root / "state.sqlite3")
            day = date(2026, 7, 27)
            database.begin_run(day)
            database.stage_episode(
                episode_date=day,
                guid="guid-newspaper",
                title="The Daily Nexus",
                audio_path=audio,
                manifest_path=manifest,
                checksum="abc",
                duration_seconds=120,
                show_notes=["Note"],
                newspaper_path=newspaper,
                preview_path=preview,
            )
            database.finish_run(day, "staged")

            record = database.list_episodes()[0]
            self.assertEqual(str(newspaper), record["newspaper_path"])
            self.assertEqual(str(preview), record["preview_path"])
