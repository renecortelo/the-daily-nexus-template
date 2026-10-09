import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest.config import load_settings
from audiodigest.publisher import PublishError, _remote_media_paths, verify_remote_private_feed
from audiodigest.rss import RemoteFeedEpisode, build_feed, parse_remote_feed_bytes
from audiodigest.web_runner import FirebaseWebRunnerClient, WebRunnerError
from audiodigest.web_scheduler import _reconcile_archive_lifecycle
from tests.test_rss import rss_settings


class ArchiveLifecycleTests(TestCase):
    def settings(self):
        settings = load_settings("config.example.toml")
        settings.firebase.base_url = "https://test.web.app"
        settings.firebase.secret_path = "a" * 32
        return settings

    def record(self, episode_guid, **changes):
        root = f"{self.settings().firebase.base_url}/p/{'a' * 32}"
        return {
            "document_id": f"record-{episode_guid}", "status": "published", "guid": episode_guid,
            "audioUrl": f"{root}/audio/2026-10-08-{episode_guid}.mp3", **changes,
        }

    def test_retention_count_alias_preserves_legacy_settings_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "config.toml"
            for key in ("retention_days", "retention_episodes"):
                path.write_text(f"[app]\n{key}=17\n", encoding="utf-8")
                self.assertEqual(17, load_settings(path).app.retention_episodes)
            path.write_text("[app]\nretention_days=17\nretention_episodes=12\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "disagree"):
                load_settings(path)
            for count in (0, 31):
                path.write_text(f"[app]\nretention_episodes={count}\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "retention_episodes"):
                    load_settings(path)

    def test_retirement_removes_audio_pdf_and_all_three_possible_previews(self):
        root = f"https://test.web.app/p/{'a' * 32}"
        episode = RemoteFeedEpisode(
            date(2026, 10, 8), "old-guid", "An edition", "2026-10-08T06:00:00+00:00",
            f"{root}/audio/2026-10-08-old-guid.mp3", 100, 60, (),
            f"{root}/read/2026-10-08-old-guid.pdf",
        )
        paths = _remote_media_paths(episode)
        self.assertEqual(5, len(paths))
        self.assertTrue(paths[-1].endswith("old-guid-3.png"))
        self.assertTrue(all(path.startswith(f"/p/{'a' * 32}/") for path in paths))

    def test_feed_inventory_cannot_silently_truncate_before_retirement(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            settings = rss_settings(root)
            audio = root / "episode.mp3"
            audio.write_bytes(b"synthetic media")
            episodes = [{
                "guid": f"guid-{number}", "episode_date": "2026-10-08", "title": "An edition",
                "audio_path": str(audio), "duration_seconds": 60, "show_notes": ["Synthetic news"],
            } for number in (1, 2)]
            feed = root / "feed.xml"
            build_feed(settings, episodes, feed)
            with self.assertRaisesRegex(ValueError, "inventory exceeds"):
                parse_remote_feed_bytes(
                    feed.read_bytes(), base_url=settings.firebase.base_url,
                    secret_path=settings.firebase.secret_path, maximum_episodes=1,
                )

    def test_reconciliation_is_complete_idempotent_and_scoped_to_exact_feed(self):
        settings = self.settings()
        records = [
            self.record("keep-guid"), self.record("old-guid"),
            self.record("already-retired", mediaState="retired"),
            self.record("external", audioUrl="https://attacker.example/p/other/audio/test.mp3"),
            self.record("staged", status="staged"),
            self.record("wrong-feed", audioUrl=(
                f"{settings.firebase.base_url}/p/{'b'*32}/audio/2026-10-08-wrong-feed.mp3"
            )),
            self.record("inconsistent", guid="forged"),
        ]
        client = Mock()
        client.list_private_collection.return_value = records
        def patch_record(_collection, document_id, changes):
            next(item for item in records if item["document_id"] == document_id).update(changes)
        client.patch_private_document.side_effect = patch_record
        _reconcile_archive_lifecycle(settings, client, ["keep-guid"])
        self.assertTrue(client.list_private_collection.call_args.kwargs["all_pages"])
        self.assertEqual(2, client.patch_private_document.call_count)
        self.assertEqual("available", records[0]["mediaState"])
        self.assertEqual("retired", records[1]["mediaState"])
        self.assertEqual("published", records[1]["status"])
        self.assertIsInstance(records[1]["retiredAt"], datetime)
        self.assertIsNone(records[0]["retiredAt"])
        _reconcile_archive_lifecycle(settings, client, ["keep-guid"])
        self.assertEqual(2, client.patch_private_document.call_count)

    def test_empty_or_corrupt_inventory_never_retires_any_record(self):
        client = Mock()
        for inventory in ([], ["../forged"], [True], "not-an-array"):
            with self.subTest(inventory=inventory), self.assertRaises(WebRunnerError):
                _reconcile_archive_lifecycle(self.settings(), client, inventory)
        client.list_private_collection.assert_not_called()
        client.patch_private_document.assert_not_called()

    def test_remote_verification_checks_exact_retained_inventory_without_an_extra_fetch(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            settings = rss_settings(root)
            audio = root / "episode.mp3"
            audio.write_bytes(b"synthetic media")
            feed = root / "feed.xml"
            build_feed(settings, [{
                "guid": "current-guid", "episode_date": "2026-10-08", "title": "An edition",
                "audio_path": str(audio), "duration_seconds": 60, "show_notes": ["Synthetic news"],
            }], feed)
            url = f"{settings.firebase.base_url}/p/{settings.firebase.secret_path}/feed.xml"
            with patch("audiodigest.publisher._fetch_remote", return_value=(
                feed.read_bytes(), "application/rss+xml",
            )) as fetch:
                with self.assertRaisesRegex(PublishError, "retention inventory differs"):
                    verify_remote_private_feed(url, expected_guid="current-guid",
                                               expected_guids=("current-guid", "missing-guid"),
                                               retry_delays=(0,))
                self.assertEqual(1, fetch.call_count)

    def test_episode_patch_is_allowlisted_and_never_overwrites_history_or_references(self):
        settings = self.settings()
        settings.web.enabled = True
        settings.web.owner_uid = "synthetic-owner"
        client = FirebaseWebRunnerClient(settings)
        with patch.object(client, "_token", return_value="synthetic-inert-value"), patch(
            "audiodigest.web_runner._json_request",
        ) as request:
            client.patch_private_document("episodes", "synthetic-edition", {
                "mediaState": "retired", "retiredAt": datetime.now(UTC),
                "updatedAt": datetime.now(UTC),
            })
            self.assertIn("updateMask.fieldPaths=mediaState", request.call_args.args[0])
            for changes in ({"status": "failed"}, {"mediaState": "available", "references": []}):
                with self.assertRaises(WebRunnerError):
                    client.patch_private_document("episodes", "synthetic-edition", changes)
            self.assertEqual(1, request.call_count)
