import tempfile
from pathlib import Path
from unittest import TestCase

from audiodigest.config import (
    AntigravitySettings,
    AppSettings,
    ArticleSettings,
    AudioSettings,
    FirebaseSettings,
    GmailSettings,
    PodcastSettings,
    ResearchSettings,
    SafetySettings,
    Settings,
)
from audiodigest.rss import build_feed, parse_remote_feed_bytes, validate_feed


def rss_settings(root: Path) -> Settings:
    return Settings(
        project_dir=root,
        app=AppSettings(runtime_dir=root / "runtime"),
        gmail=GmailSettings(client_secret_path=root / "client.json"),
        antigravity=AntigravitySettings(
            workspace_dir=root / "antigravity-workspace",
            settings_path=root / "antigravity-settings.json",
            agent_path=root / "agent.md",
        ),
        articles=ArticleSettings(),
        research=ResearchSettings(),
        audio=AudioSettings(),
        firebase=FirebaseSettings(
            project_id="test",
            executable="firebase",
            public_dir=root / "hosting",
            base_url="https://test.web.app",
            secret_path="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # noqa: S106
        ),
        podcast=PodcastSettings(),
        safety=SafetySettings(),
    )


class RssTests(TestCase):
    def test_private_feed_is_valid(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            audio = root / "episode.mp3"
            audio.write_bytes(b"fake mp3 bytes")
            feed = root / "feed.xml"
            build_feed(
                rss_settings(root),
                [
                    {
                        "episode_date": "2026-07-25",
                        "guid": "guid-1",
                        "title": "The Daily Nexus — July 25, 2026",
                        "audio_path": str(audio),
                        "duration_seconds": 900,
                        "show_notes": ["Story — Source — https://example.com/story"],
                    }
                ],
                feed,
            )
            report = validate_feed(feed)
            value = feed.read_text(encoding="utf-8")
            self.assertEqual(1, report.episode_count)
            self.assertEqual(("guid-1",), report.guids)
            self.assertIn("<itunes:block>yes</itunes:block>", value)
            self.assertIn("<itunes:type>episodic</itunes:type>", value)
            self.assertIn("<itunes:episodeType>full</itunes:episodeType>", value)
            self.assertIn("/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/", value)
            self.assertIn("audio/mpeg", value)
            self.assertNotIn("&amp;amp;", value)

    def test_duplicate_episode_guid_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            audio = root / "episode.mp3"
            audio.write_bytes(b"fake mp3 bytes")
            episode = {
                "episode_date": "2026-07-25",
                "guid": "duplicate-guid",
                "title": "The Daily Nexus",
                "audio_path": str(audio),
                "duration_seconds": 900,
                "show_notes": ["Story"],
            }
            feed = root / "feed.xml"
            build_feed(rss_settings(root), [episode, dict(episode)], feed)

            with self.assertRaisesRegex(ValueError, "duplicated"):
                validate_feed(feed)

    def test_remote_feed_parser_confines_media_to_private_path(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            settings = rss_settings(root)
            audio = root / "episode.mp3"
            audio.write_bytes(b"fake mp3 bytes")
            feed = root / "feed.xml"
            episodes = [
                {
                    "episode_date": "2026-07-27",
                    "guid": "episode-guid",
                    "title": "The Daily Nexus",
                    "audio_path": str(audio),
                    "duration_seconds": 1200,
                    "show_notes": ["Verified summary"],
                    "status": "published",
                    "published_at": "2026-07-27T06:00:00+00:00",
                }
            ]
            build_feed(settings, episodes, feed)
            result = parse_remote_feed_bytes(
                feed.read_bytes(),
                base_url=settings.firebase.base_url,
                secret_path=settings.firebase.secret_path,
                maximum_episodes=30,
            )
            self.assertEqual(1, len(result))
            self.assertEqual("episode-guid", result[0].guid)
            tampered = feed.read_text(encoding="utf-8").replace(
                "https://test.web.app/p/",
                "https://attacker.example/p/",
            )
            with self.assertRaisesRegex(ValueError, "escaped"):
                parse_remote_feed_bytes(
                    tampered.encode(),
                    base_url=settings.firebase.base_url,
                    secret_path=settings.firebase.secret_path,
                    maximum_episodes=30,
                )
