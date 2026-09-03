from datetime import date
from unittest import TestCase
from unittest.mock import MagicMock, patch

from audiodigest.config import ResearchSettings
from audiodigest.daily_research import WikimediaDailyResearch


class WikimediaDailyResearchTests(TestCase):
    def setUp(self):
        self.research = WikimediaDailyResearch(
            ResearchSettings(history_event_limit=2)
        )
        self.day = date(2026, 7, 27)

    def test_history_source_is_limited_and_carries_public_sources(self):
        payload = {
            "events": [
                {
                    "year": 1969,
                    "text": "A first documented event.",
                    "pages": [
                        {
                            "content_urls": {
                                "desktop": {
                                    "page": "https://en.wikipedia.org/wiki/First"
                                }
                            }
                        }
                    ],
                },
                {
                    "year": 1981,
                    "text": "A second documented event.",
                    "pages": [],
                },
                {"year": 2001, "text": "This event is beyond the limit.", "pages": []},
            ]
        }
        with patch.object(self.research, "_get_json", return_value=payload):
            source = self.research.history_source(self.day)

        self.assertEqual("history", source.source_type)
        self.assertIn("1969:", source.email_text)
        self.assertIn("1981:", source.email_text)
        self.assertNotIn("2001:", source.email_text)
        self.assertEqual(
            ["https://en.wikipedia.org/wiki/First"], source.article_urls
        )

    def test_current_events_source_cleans_html(self):
        payload = {
            "parse": {
                "text": (
                    "<div><h2>World</h2><p>A documented current event.</p>"
                    "<script>ignore me</script></div>"
                )
            }
        }
        with patch.object(self.research, "_get_json", return_value=payload):
            source = self.research.current_events_source(self.day)

        self.assertEqual("current_world", source.source_type)
        self.assertIn("A documented current event.", source.email_text)
        self.assertNotIn("ignore me", source.email_text)
        self.assertTrue(source.article_urls[0].startswith("https://en.wikipedia.org/"))

    def test_transient_timeout_is_retried_without_expanding_the_allowlist(self):
        response = MagicMock()
        response.headers.get.return_value = None
        response.read.return_value = b'{"events": []}'
        response.__enter__.return_value = response
        with (
            patch(
                "audiodigest.daily_research.urllib.request.urlopen",
                side_effect=[TimeoutError("temporary delay"), response],
            ) as urlopen,
            patch("audiodigest.daily_research.time_module.sleep") as sleep,
        ):
            result = self.research._get_json(
                "https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/07/30"
            )

        self.assertEqual({"events": []}, result)
        self.assertEqual(2, urlopen.call_count)
        sleep.assert_called_once_with(0.5)
