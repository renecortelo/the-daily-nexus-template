import gzip
import urllib.error
from unittest import TestCase
from unittest.mock import MagicMock, patch

from audiodigest.config import ArticleSettings
from audiodigest.web_fetcher import (
    ArticleFetchError,
    SafeArticleFetcher,
    UnsafeURLError,
    assert_public_https_url,
)


class WebFetcherTests(TestCase):
    @staticmethod
    def _robots_response(body: bytes, *, content_encoding: str = ""):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get.side_effect = lambda name, default=None: {
            "Content-Length": str(len(body)),
            "Content-Encoding": content_encoding,
        }.get(name, default)
        response.read.return_value = body
        return response

    def test_gzip_robots_response_is_decoded_before_parsing(self):
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.return_value = self._robots_response(
            gzip.compress(b"User-agent: *\nDisallow: /private\n"),
            content_encoding="gzip",
        )

        self.assertFalse(
            fetcher._allowed_by_robots("https://example.test/private/story")
        )

    def test_malformed_robots_response_fails_closed_for_only_that_article(self):
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.return_value = self._robots_response(b"\x1f\x8bnot-gzip")

        with self.assertRaisesRegex(
            ArticleFetchError,
            "robots.txt could not be decoded safely",
        ):
            fetcher._allowed_by_robots("https://example.test/story")

    @patch("audiodigest.web_fetcher.socket.getaddrinfo")
    def test_private_ip_is_rejected(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaises(UnsafeURLError):
            assert_public_https_url("https://example.test/story")

    @patch("audiodigest.web_fetcher.socket.getaddrinfo")
    def test_public_ip_is_allowed(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        assert_public_https_url("https://example.test/story")

    @patch("audiodigest.web_fetcher.assert_public_https_url")
    def test_connection_timeout_is_reported_as_article_fetch_error(self, _assert_safe):
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.side_effect = TimeoutError("The read operation timed out")

        with (
            patch.object(fetcher, "_allowed_by_robots", return_value=True),
            self.assertRaisesRegex(ArticleFetchError, "request timed out"),
        ):
            fetcher.fetch("https://example.test/story")

    @patch("audiodigest.web_fetcher.assert_public_https_url")
    def test_accented_article_path_is_ascii_encoded_before_open(self, _assert_safe):
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.side_effect = UnicodeEncodeError(
            "ascii", "cafè", 3, 4, "ordinal not in range"
        )

        with (
            patch.object(fetcher, "_allowed_by_robots", return_value=True),
            self.assertRaisesRegex(ArticleFetchError, "could not be encoded safely"),
        ):
            fetcher.fetch("https://example.test/cafè")

        request = fetcher.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/caf%C3%A8")

    @patch("audiodigest.web_fetcher.assert_public_https_url")
    def test_mid_download_timeout_is_reported_as_article_fetch_error(self, _assert_safe):
        response = MagicMock()
        response.headers.get_content_type.return_value = "text/html"
        response.headers.get.return_value = None
        response.read.side_effect = TimeoutError("The read operation timed out")

        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.return_value = response

        with (
            patch.object(fetcher, "_allowed_by_robots", return_value=True),
            self.assertRaisesRegex(ArticleFetchError, "request timed out"),
        ):
            fetcher.fetch("https://example.test/story")

    def test_tracking_link_is_rejected_before_any_network_request(self):
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()

        with self.assertRaisesRegex(ArticleFetchError, "tracking link was not fetched"):
            fetcher.fetch("https://tracking.tldrnewsletter.com/opaque-token")

        fetcher.opener.open.assert_not_called()

    @patch("audiodigest.web_fetcher.assert_public_https_url")
    def test_redirect_destination_is_checked_against_its_robots_rules(self, _assert_safe):
        headers = MagicMock()
        headers.get.return_value = "https://destination.test/private/story"
        redirect = urllib.error.HTTPError(
            "https://origin.test/story",
            302,
            "Found",
            headers,
            None,
        )
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.side_effect = [redirect]

        with (
            patch.object(
                fetcher,
                "_allowed_by_robots",
                side_effect=[True, False],
            ) as allowed,
            self.assertRaisesRegex(ArticleFetchError, "robots.txt disallows"),
        ):
            fetcher.fetch("https://origin.test/story")

        self.assertEqual(
            [call.args[0] for call in allowed.call_args_list],
            [
                "https://origin.test/story",
                "https://destination.test/private/story",
            ],
        )
        fetcher.opener.open.assert_called_once()
