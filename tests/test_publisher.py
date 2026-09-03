import shutil
import tempfile
from datetime import date
from email.message import Message
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from audiodigest.database import StateDatabase
from audiodigest.publisher import (
    FirebasePublisher,
    PublishError,
    _fetch_remote,
    _public_tree_size,
    _validate_apple_artwork,
    _validate_apple_audio_probe,
    load_remote_publication,
    verify_remote_private_feed,
)
from audiodigest.rss import RemoteFeedEpisode, build_feed
from tests.test_rss import rss_settings


class _RemoteResponse:
    def __init__(self, url: str, value: bytes, content_type: str):
        self._url = url
        self._value = value
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self) -> str:
        return self._url

    def read(self, amount: int) -> bytes:
        return self._value[:amount]


class PublisherTests(TestCase):
    def test_current_artwork_is_apple_ready(self):
        _validate_apple_artwork(Path("assets/cover-retrofuture.jpg"))

    def test_current_audio_profile_is_apple_ready(self):
        _validate_apple_audio_probe(
            Path("episode.mp3"),
            {
                "streams": [
                    {
                        "codec_name": "mp3",
                        "sample_rate": "44100",
                        "channels": 1,
                        "bit_rate": "64000",
                    }
                ]
            },
        )

    def test_web_managed_publish_refuses_non_incremental_desktop_deploy(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            settings = rss_settings(Path(name))
            settings.web.enabled = True
            publisher = FirebasePublisher(settings, StateDatabase(Path(name) / "state.sqlite3"))
            with patch.object(publisher, "_build_tree") as build_tree:
                with self.assertRaisesRegex(PublishError, "cloud runner"):
                    publisher.publish(date(2026, 7, 20))
            build_tree.assert_not_called()

    def test_low_stereo_bitrate_is_rejected(self):
        with self.assertRaisesRegex(PublishError, "recommended minimum"):
            _validate_apple_audio_probe(
                Path("episode.mp3"),
                {
                    "streams": [
                        {
                            "codec_name": "mp3",
                            "sample_rate": "44100",
                            "channels": 2,
                            "bit_rate": "64000",
                        }
                    ]
                },
            )

    def test_private_tree_size_counts_only_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            (root / "nested").mkdir()
            (root / "one.bin").write_bytes(b"123")
            (root / "nested" / "two.bin").write_bytes(b"4567")
            self.assertEqual(7, _public_tree_size(root))

    def test_cloud_publication_inventory_downloads_only_the_feed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = rss_settings(root)
            audio = root / "source.mp3"
            audio.write_bytes(b"existing remote audio")
            feed = root / "feed.xml"
            build_feed(
                settings,
                [
                    {
                        "episode_date": "2026-07-27",
                        "guid": "existing-guid",
                        "title": "The Daily Nexus",
                        "audio_path": str(audio),
                        "duration_seconds": 1200,
                        "show_notes": ["A verified story"],
                    }
                ],
                feed,
            )
            with patch(
                "audiodigest.publisher._fetch_remote",
                return_value=(feed.read_bytes(), "application/rss+xml"),
            ) as remote_fetch:
                episodes = load_remote_publication(
                    settings,
                    maximum_episodes=30,
                )
            self.assertEqual(1, len(episodes))
            remote_fetch.assert_called_once()

    def test_incremental_tree_reuses_remote_media_without_local_copy(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = rss_settings(root)
            settings.app.retention_days = 1
            (root / "web").mkdir()
            (root / "assets").mkdir()
            for filename in (
                "index.html",
                "styles.css",
                "app.js",
                "manifest.webmanifest",
                "service-worker.js",
            ):
                shutil.copy2(Path("web") / filename, root / "web" / filename)
            for filename in ("tdn-icon.png", "tdn-icon-transparent.png", "google-g.png"):
                shutil.copy2(Path("assets") / filename, root / "assets" / filename)
            shutil.copy2(
                Path("assets") / settings.podcast.cover_filename,
                root / "assets" / settings.podcast.cover_filename,
            )
            remote = RemoteFeedEpisode(
                episode_date=date(2026, 7, 27),
                guid="existing-guid",
                title="The Daily Nexus",
                published_at="2026-07-27T06:00:00+00:00",
                audio_url=(
                    "https://test.web.app/p/"
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/audio/"
                    "2026-07-27-existing-guid.mp3"
                ),
                audio_bytes=123456,
                duration_seconds=1200,
                show_notes=("A verified story",),
                newspaper_url=(
                    "https://test.web.app/p/"
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/read/"
                    "2026-07-27-existing-guid.pdf"
                ),
            )
            database = StateDatabase(root / "runtime" / "state.sqlite3")
            publisher = FirebasePublisher(settings, database)
            episodes, _hosted_bytes, removed = publisher._build_tree(
                date(2026, 7, 28),
                remote_episodes=(remote,),
            )
            self.assertEqual(1, len(episodes))
            self.assertTrue(episodes[0]["remote_only"])
            self.assertEqual(123456, episodes[0]["audio_bytes"])
            self.assertEqual((), removed)
            self.assertFalse(
                (
                    settings.firebase.public_dir
                    / "p"
                    / settings.firebase.secret_path
                    / "audio"
                    / "2026-07-27-existing-guid.mp3"
                ).exists()
            )

    def test_new_guid_retains_a_remote_episode_from_the_same_date(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = rss_settings(root)
            settings.project_dir = Path.cwd()
            settings.app.retention_days = 2
            local_audio = root / "new.mp3"
            local_audio.write_bytes(b"new audio")
            database = StateDatabase(root / "runtime" / "state.sqlite3")
            database.stage_episode(
                episode_date=date(2026, 8, 2),
                guid="new-guid",
                title="The Daily Nexus - August 2 // 002",
                audio_path=local_audio,
                manifest_path=root / "manifest.json",
                checksum="checksum",
                duration_seconds=1200,
                show_notes=["New run note"],
            )
            remote = RemoteFeedEpisode(
                episode_date=date(2026, 8, 2),
                guid="old-guid",
                title="The Daily Nexus - August 2 // 001",
                published_at="2026-08-02T04:00:00+00:00",
                audio_url=(
                    "https://test.web.app/p/"
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/audio/old-guid.mp3"
                ),
                audio_bytes=123456,
                duration_seconds=1200,
                show_notes=("Prior run note",),
            )
            publisher = FirebasePublisher(settings, database)
            with patch("audiodigest.publisher._validate_apple_audio"):
                episodes, _hosted_bytes, removed = publisher._build_tree(
                    date(2026, 8, 2),
                    remote_episodes=(remote,),
                )
            self.assertEqual({"old-guid", "new-guid"}, {item["guid"] for item in episodes})
            self.assertEqual((), removed)

    def test_remote_feed_and_new_audio_are_verified(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = rss_settings(root)
            audio = root / "episode.mp3"
            audio.write_bytes(b"remote-audio")
            feed = root / "feed.xml"
            build_feed(
                settings,
                [
                    {
                        "episode_date": "2026-07-20",
                        "guid": "episode-guid",
                        "title": "The Daily Nexus - Monday, July 20, 2026",
                        "audio_path": str(audio),
                        "duration_seconds": 811,
                        "show_notes": ["A verified story"],
                    }
                ],
                feed,
            )
            feed_url = (
                "https://test.web.app/p/"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/feed.xml"
            )
            audio_url = (
                "https://test.web.app/p/"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/audio/"
                "2026-07-20-episode-guid.mp3"
            )
            responses = [
                _RemoteResponse(
                    feed_url,
                    feed.read_bytes(),
                    "application/rss+xml",
                ),
                _RemoteResponse(audio_url, b"x", "audio/mpeg"),
            ]
            with patch(
                "audiodigest.publisher.urlopen",
                side_effect=responses,
            ) as remote_open:
                self.assertEqual(
                    1,
                    verify_remote_private_feed(
                        feed_url,
                        expected_guid="episode-guid",
                    ),
                )
            request_url = remote_open.call_args_list[0].args[0].full_url
            parsed_request = urlsplit(request_url)
            self.assertEqual(urlsplit(feed_url).path, parsed_request.path)
            self.assertIn("_tdn_verify", parse_qs(parsed_request.query))

    def test_remote_feed_must_include_the_new_episode(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = rss_settings(root)
            audio = root / "episode.mp3"
            audio.write_bytes(b"remote-audio")
            feed = root / "feed.xml"
            build_feed(
                settings,
                [
                    {
                        "episode_date": date(2026, 7, 20).isoformat(),
                        "guid": "different-guid",
                        "title": "The Daily Nexus",
                        "audio_path": str(audio),
                        "duration_seconds": 811,
                        "show_notes": ["A verified story"],
                    }
                ],
                feed,
            )
            feed_url = (
                "https://test.web.app/p/"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/feed.xml"
            )
            with patch(
                "audiodigest.publisher.urlopen",
                return_value=_RemoteResponse(
                    feed_url,
                    feed.read_bytes(),
                    "application/rss+xml",
                ),
            ):
                with self.assertRaisesRegex(PublishError, "missing"):
                    verify_remote_private_feed(
                        feed_url,
                        expected_guid="expected-guid",
                        retry_delays=(0.0,),
                    )

    def test_stale_remote_feed_is_retried_with_a_fresh_cache_buster(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            settings = rss_settings(root)
            audio = root / "episode.mp3"
            audio.write_bytes(b"remote-audio")
            stale_feed = root / "stale.xml"
            current_feed = root / "current.xml"
            common_episode = {
                "episode_date": "2026-07-29",
                "title": "The Daily Nexus",
                "audio_path": str(audio),
                "duration_seconds": 811,
                "show_notes": ["A verified story"],
            }
            build_feed(
                settings,
                [{**common_episode, "guid": "previous-guid"}],
                stale_feed,
            )
            build_feed(
                settings,
                [{**common_episode, "guid": "expected-guid"}],
                current_feed,
            )
            feed_url = (
                "https://test.web.app/p/"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/feed.xml"
            )
            audio_url = (
                "https://test.web.app/p/"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/audio/"
                "2026-07-29-expected-guid.mp3"
            )
            responses = [
                _RemoteResponse(feed_url, stale_feed.read_bytes(), "application/rss+xml"),
                _RemoteResponse(feed_url, current_feed.read_bytes(), "application/rss+xml"),
                _RemoteResponse(audio_url, b"x", "audio/mpeg"),
            ]
            with patch(
                "audiodigest.publisher.urlopen",
                side_effect=responses,
            ) as remote_open:
                self.assertEqual(
                    1,
                    verify_remote_private_feed(
                        feed_url,
                        expected_guid="expected-guid",
                        retry_delays=(0.0, 0.0),
                    ),
                )
            first_query = urlsplit(
                remote_open.call_args_list[0].args[0].full_url
            ).query
            second_query = urlsplit(
                remote_open.call_args_list[1].args[0].full_url
            ).query
            self.assertNotEqual(first_query, second_query)

    def test_remote_http_error_does_not_expose_private_feed_url(self):
        private_url = (
            "https://daily-nexus-private.web.app/p/"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/feed.xml"
        )
        error = HTTPError(private_url, 404, "Not Found", {}, None)
        with patch("audiodigest.publisher.urlopen", side_effect=error):
            with self.assertRaises(PublishError) as caught:
                _fetch_remote(
                    private_url,
                    expected_host="daily-nexus-private.web.app",
                    maximum_bytes=1024,
                    accept="application/rss+xml",
                )
        self.assertNotIn("0123456789abcdef", str(caught.exception))
        self.assertIn("HTTP 404", str(caught.exception))
