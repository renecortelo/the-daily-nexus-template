import gzip
import http.client
import socket
import ssl
import urllib.error
from unittest import TestCase
from unittest.mock import MagicMock, patch

from audiodigest.config import ArticleSettings
from audiodigest.web_fetcher import (
    ArticleFetchError,
    SafeArticleFetcher,
    UnsafeURLError,
    _connect_public,
    _PublicHTTPSConnection,
    _PublicHTTPSHandler,
    assert_public_https_url,
)


class WebFetcherTests(TestCase):
    def test_robots_http_statuses_distinguish_missing_rules_from_uncertainty(self):
        for code in (401, 403, 404, 410, 408, 429, 500, 503):
            with self.subTest(code=code):
                fetcher = SafeArticleFetcher(ArticleSettings())
                fetcher.opener = MagicMock()
                error = urllib.error.HTTPError('https://example.test/robots.txt', code,
                                               'synthetic status', {}, None)
                fetcher.opener.open.side_effect = error
                if code in (401, 403, 404, 410):
                    self.assertEqual(fetcher._allowed_by_robots('https://example.test/story'),
                                     code in (404, 410))
                else:
                    with self.assertRaisesRegex(ArticleFetchError, 'check unavailable'):
                        fetcher._allowed_by_robots('https://example.test/story')

    @patch('audiodigest.web_fetcher.assert_public_https_url')
    def test_uncertain_robots_never_fetches_an_article_and_is_not_retried_per_link(self, _safe):
        for failure in (TimeoutError('synthetic'), urllib.error.URLError('synthetic'),
                        http.client.RemoteDisconnected('synthetic')):
            with self.subTest(failure=type(failure).__name__):
                fetcher = SafeArticleFetcher(ArticleSettings())
                fetcher.opener = MagicMock()
                fetcher.opener.open.side_effect = failure
                for path in ('first', 'second'):
                    with self.assertRaisesRegex(ArticleFetchError, 'robots.txt check unavailable'):
                        fetcher.fetch('https://example.test/' + path)
                fetcher.opener.open.assert_called_once()
                self.assertTrue(fetcher.opener.open.call_args.args[0].full_url.endswith('/robots.txt'))

    def test_cached_rules_apply_to_each_article_path_and_stay_origin_scoped(self):
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.return_value = self._robots_response(
            b'User-agent: *\nDisallow: /private\n')
        self.assertTrue(fetcher._allowed_by_robots('https://example.test/public'))
        self.assertFalse(fetcher._allowed_by_robots('https://example.test/private/story'))
        fetcher.opener.open.assert_called_once()
        self.assertTrue(fetcher._allowed_by_robots('https://another.test/public'))
        self.assertEqual(fetcher.opener.open.call_count, 2)

    def test_html_robots_and_partial_downloads_fail_closed(self):
        for response in (self._robots_response(b'<html>Access blocked</html>'),
                         self._robots_response(b'User-agent: *\n')):
            fetcher = SafeArticleFetcher(ArticleSettings())
            fetcher.opener = MagicMock()
            fetcher.opener.open.return_value = response
            if not response.read.return_value.startswith(b'<'):
                response.read.side_effect = http.client.IncompleteRead(b'partial')
            with self.assertRaises(ArticleFetchError):
                fetcher._allowed_by_robots('https://example.test/story')

    def test_short_or_partial_response_cannot_be_parsed_as_permission(self):
        for partial_status in (False, True):
            response = self._robots_response(b'User-agent: *\nDisallow:')
            if partial_status:
                response.status = 206
            else:
                response.headers.get.side_effect = lambda name, default=None: (
                    '100' if name == 'Content-Length' else default)
            fetcher = SafeArticleFetcher(ArticleSettings())
            fetcher.opener = MagicMock()
            fetcher.opener.open.return_value = response
            with self.assertRaises(ArticleFetchError):
                fetcher._allowed_by_robots('https://example.test/story')

    def test_utf8_bom_does_not_hide_disallow_rules(self):
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.return_value = self._robots_response(
            b'\xef\xbb\xbfUser-agent: *\nDisallow: /private\n')
        self.assertFalse(fetcher._allowed_by_robots('https://example.test/private/story'))

    @patch('audiodigest.web_fetcher.assert_public_https_url')
    def test_robots_redirects_are_bounded_and_rules_apply_to_original_article(self, safe):
        redirect = urllib.error.HTTPError('https://example.test/robots.txt', 301,
            'redirect', {'Location': 'https://rules.test/robots.txt'}, None)
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.side_effect = [redirect, self._robots_response(
            b'User-agent: *\nDisallow: /private\n')]
        self.assertFalse(fetcher._allowed_by_robots('https://example.test/private/story'))
        safe.assert_called_once_with('https://rules.test/robots.txt')
        bounded = SafeArticleFetcher(ArticleSettings(max_redirects=1))
        bounded.opener = MagicMock()
        bounded.opener.open.side_effect = redirect
        with self.assertRaisesRegex(ArticleFetchError, 'redirect limit'):
            bounded._allowed_by_robots('https://example.test/story')
        self.assertEqual(bounded.opener.open.call_count, 2)

    @patch('audiodigest.web_fetcher.socket.getaddrinfo')
    def test_robots_redirect_to_private_address_is_rejected_before_destination_fetch(self, dns):
        dns.return_value = [(socket.AF_INET, 1, 6, '', ('127.0.0.1', 443))]
        fetcher = SafeArticleFetcher(ArticleSettings())
        fetcher.opener = MagicMock()
        fetcher.opener.open.side_effect = urllib.error.HTTPError(
            'https://example.test/robots.txt', 302, 'redirect',
            {'Location': 'https://private.test/robots.txt'}, None)
        with self.assertRaises(UnsafeURLError):
            fetcher._allowed_by_robots('https://example.test/story')
        fetcher.opener.open.assert_called_once()

    @patch('audiodigest.web_fetcher.socket.socket')
    @patch('audiodigest.web_fetcher.socket.getaddrinfo')
    def test_connection_pins_checked_address_and_preserves_tls_hostname(self, dns, sockets):
        dns.side_effect = [[(socket.AF_INET, 1, 6, '', ('93.184.216.34', 443))],
                           [(socket.AF_INET, 1, 6, '', ('127.0.0.1', 443))]]
        context = MagicMock()
        connection = _PublicHTTPSConnection('example.test', timeout=5, context=context)
        connection.connect()
        dns.assert_called_once()
        sockets.return_value.connect.assert_called_once_with(('93.184.216.34', 443))
        context.wrap_socket.assert_called_once_with(
            sockets.return_value, server_hostname='example.test')
        self.assertIs(connection.sock, context.wrap_socket.return_value)

    @patch('audiodigest.web_fetcher.socket.socket')
    @patch('audiodigest.web_fetcher.socket.getaddrinfo')
    def test_rebound_mixed_or_empty_addresses_cannot_open_a_socket(self, dns, sockets):
        for addresses in ([], [(2, 1, 6, '', ('127.0.0.1', 443))],
                          [(2, 1, 6, '', ('93.184.216.34', 443)),
                           (2, 1, 6, '', ('10.0.0.1', 443))],
                          [(10, 1, 6, '', ('::1', 443, 0, 0))]):
            dns.return_value = addresses
            with self.assertRaises(UnsafeURLError):
                _connect_public(('example.test', 443), 5)
        sockets.assert_not_called()

    @patch('audiodigest.web_fetcher._connect_public')
    def test_tls_rejection_closes_socket_and_no_proxy_can_bypass_validation(self, connect):
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError('synthetic mismatch')
        connection = _PublicHTTPSConnection('example.test', timeout=5, context=context)
        with self.assertRaises(ssl.SSLCertVerificationError):
            connection.connect()
        connect.return_value.close.assert_called_once()
        connection.set_tunnel('another.test')
        connect.reset_mock()
        with self.assertRaises(UnsafeURLError):
            connection.connect()
        connect.assert_not_called()
        handler = _PublicHTTPSHandler()
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)

    @patch('audiodigest.web_fetcher.socket.socket')
    @patch('audiodigest.web_fetcher.socket.getaddrinfo')
    def test_failed_connection_closes_socket_before_validated_fallback(self, dns, sockets):
        dns.return_value = [(socket.AF_INET, 1, 6, '', ('93.184.216.34', 443)),
                            (socket.AF_INET, 1, 6, '', ('1.1.1.1', 443))]
        first, second = MagicMock(), MagicMock()
        first.connect.side_effect = OSError('synthetic refused')
        sockets.side_effect = [first, second]
        self.assertIs(_connect_public(('example.test', 443), 5), second)
        first.close.assert_called_once()
        second.connect.assert_called_once_with(('1.1.1.1', 443))
        dns.assert_called_once()

    @patch('audiodigest.web_fetcher.socket.getaddrinfo')
    def test_credential_bearing_urls_are_refused_before_dns(self, dns):
        with self.assertRaises(UnsafeURLError):
            assert_public_https_url('https://synthetic-user:synthetic-pass@example.com/story')
        dns.assert_not_called()

    def test_robots_cache_has_a_fixed_origin_limit(self):
        fetcher = SafeArticleFetcher(ArticleSettings())
        with patch.object(fetcher, '_read_robots', return_value=True):
            for index in range(140):
                fetcher._allowed_by_robots(f'https://origin-{index}.test/story')
        self.assertEqual(len(fetcher._robots_cache), 128)


    @staticmethod
    def _robots_response(body: bytes, *, content_encoding: str = ""):
        response = MagicMock()
        response.status = 200
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
