from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from audiodigest.config import ArticleSettings
from audiodigest.content import clean_html, is_tracking_url, normalize_url
from audiodigest.models import ArticleReference


class UnsafeURLError(ValueError):
    pass


class ArticleFetchError(RuntimeError):
    pass


ROBOTS_MAX_BYTES = 256_000
ROBOTS_CACHE_ORIGINS = 128


def _network_error_message(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "request timed out"
    return str(exc) or exc.__class__.__name__


def _decode_robots(body: bytes, content_encoding: str) -> str:
    is_gzip = content_encoding.casefold() in {"gzip", "x-gzip"} or body.startswith(
        b"\x1f\x8b"
    )
    if is_gzip:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                body = compressed.read(ROBOTS_MAX_BYTES + 1)
        except (EOFError, OSError) as exc:
            raise ArticleFetchError(
                "robots.txt could not be decoded safely"
            ) from exc
    if len(body) > ROBOTS_MAX_BYTES:
        raise ArticleFetchError("robots.txt exceeds the safety size limit")
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArticleFetchError("robots.txt could not be decoded safely") from exc


def _public_addresses(host: str, port: int) -> list[tuple]:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError("destination could not be resolved safely") from exc
    if not addresses:
        raise UnsafeURLError("destination has no usable public address")
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise UnsafeURLError("destination resolves to a non-public address")
    return addresses[:16]


def assert_public_https_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise UnsafeURLError("article URL must use HTTPS and include a hostname")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("credential-bearing article URLs are not allowed")
    _public_addresses(parts.hostname, parts.port or 443)


def _connect_public(address: tuple[str, int], timeout, source_address=None) -> socket.socket:
    """Connect to the exact validated address, without a second DNS lookup."""
    if source_address is not None:
        raise UnsafeURLError("custom source addresses are not supported")
    addresses = _public_addresses(*address)
    bounded_timeout = (
        socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    )
    deadline = time.monotonic() + bounded_timeout if bounded_timeout is not None else None
    last_error = None
    for family, kind, protocol, _, destination in addresses:
        remaining = max(0.0, deadline - time.monotonic()) if deadline is not None else None
        if remaining == 0:
            raise TimeoutError("request timed out")
        connection = socket.socket(family, kind, protocol)
        try:
            connection.settimeout(remaining)
            connection.connect(destination)
            return connection
        except OSError as exc:
            connection.close()
            last_error = exc
    raise OSError("public destination connection failed") from last_error


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self._tunnel_host:
            raise UnsafeURLError("article proxy tunnels are not supported")
        connection = _connect_public((self.host, self.port), self.timeout, self.source_address)
        try:
            # Keep the original hostname for SNI and normal CA/hostname validation.
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except BaseException:
            connection.close()
            raise


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_PublicHTTPSConnection, request, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    content_type: str
    body: bytes


class SafeArticleFetcher:
    def __init__(self, settings: ArticleSettings):
        self.settings = settings
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect, _PublicHTTPSHandler(),
        )
        # One fetcher belongs to one enrichment run; never persist domain policies.
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | bool | str] = {}

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        robots_url = f"https://{parts.netloc}/robots.txt"
        if robots_url not in self._robots_cache:
            try:
                policy = self._read_robots(robots_url)
            except ArticleFetchError as exc:
                policy = str(exc)
            if len(self._robots_cache) >= ROBOTS_CACHE_ORIGINS:
                del self._robots_cache[next(iter(self._robots_cache))]
            self._robots_cache[robots_url] = policy
        policy = self._robots_cache[robots_url]
        if isinstance(policy, str):
            raise ArticleFetchError(policy)
        if isinstance(policy, bool):
            return policy
        return policy.can_fetch(self.settings.user_agent, url)

    def _read_robots(
        self, robots_url: str, redirects: int = 0,
    ) -> urllib.robotparser.RobotFileParser | bool:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        request = urllib.request.Request(  # noqa: S310 - same public HTTPS origin.
            robots_url,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "text/plain,*/*;q=0.1",
                "Accept-Encoding": "identity",
            },
        )
        try:
            response = self.opener.open(
                request,
                timeout=self.settings.timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location")
                redirected = normalize_url(urljoin(robots_url, location)) if location else ""
                if not redirected or redirects >= min(5, self.settings.max_redirects):
                    raise ArticleFetchError(
                        "robots.txt redirect limit exceeded; article skipped"
                    ) from exc
                if is_tracking_url(redirected):
                    raise ArticleFetchError(
                        "robots.txt tracking destination refused; article skipped"
                    ) from exc
                assert_public_https_url(redirected)
                return self._read_robots(redirected, redirects + 1)
            if exc.code in {401, 403}:
                return False
            if exc.code in {404, 410}:
                return True
            raise ArticleFetchError(
                f"robots.txt check unavailable (HTTP {exc.code}); article skipped"
            ) from exc
        except (OSError, urllib.error.URLError, http.client.HTTPException, UnicodeError) as exc:
            raise ArticleFetchError("robots.txt check unavailable; article skipped") from exc
        try:
            with response:
                if response.status not in {200, 204}:
                    raise ArticleFetchError("robots.txt returned incomplete rules; article skipped")
                declared = response.headers.get("Content-Length")
                try:
                    declared_size = int(declared) if declared else None
                except ValueError as exc:
                    raise ArticleFetchError(
                        "robots.txt has an invalid Content-Length"
                    ) from exc
                if declared_size is not None and (
                    declared_size < 0 or declared_size > ROBOTS_MAX_BYTES
                ):
                    raise ArticleFetchError(
                        "robots.txt exceeds the safety size limit"
                    )
                body = response.read(ROBOTS_MAX_BYTES + 1)
                if declared_size is not None and len(body) != declared_size:
                    raise ArticleFetchError("robots.txt download is truncated; article skipped")
                content_encoding = response.headers.get(
                    "Content-Encoding",
                    "",
                )
        except ArticleFetchError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise ArticleFetchError(
                "robots.txt download unavailable; article skipped"
            ) from exc
        decoded = _decode_robots(body, content_encoding)
        if decoded.lstrip().startswith("<"):
            raise ArticleFetchError("robots.txt returned HTML instead of rules; article skipped")
        parser.parse(decoded.splitlines())
        return parser

    def fetch(self, url: str) -> FetchResult:
        current = normalize_url(url)
        if not current:
            raise UnsafeURLError("article URL is invalid")
        original = current
        for redirect_count in range(self.settings.max_redirects + 1):
            if is_tracking_url(current):
                raise ArticleFetchError("tracking link was not fetched")
            assert_public_https_url(current)
            # A redirect can cross origins or move to a path with different
            # robots rules. Re-evaluate every destination before requesting it.
            if not self._allowed_by_robots(current):
                raise ArticleFetchError("robots.txt disallows this article")
            request = urllib.request.Request(  # noqa: S310 - URL is HTTPS and public-checked above.
                current,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            try:
                response = self.opener.open(request, timeout=self.settings.timeout_seconds)
            except urllib.error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location or redirect_count >= self.settings.max_redirects:
                        raise ArticleFetchError("redirect limit exceeded") from exc
                    current = normalize_url(urljoin(current, location))
                    if not current:
                        raise UnsafeURLError("redirect target is not safe") from exc
                    continue
                raise ArticleFetchError(f"HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise ArticleFetchError(str(exc.reason)) from exc
            except UnicodeError as exc:
                raise ArticleFetchError("article URL could not be encoded safely") from exc
            except (OSError, http.client.HTTPException) as exc:
                raise ArticleFetchError(_network_error_message(exc)) from exc
            try:
                with response:
                    content_type = response.headers.get_content_type().lower()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise ArticleFetchError(f"unsupported content type: {content_type}")
                    declared = response.headers.get("Content-Length")
                    try:
                        declared_size = int(declared) if declared else None
                    except ValueError as exc:
                        raise ArticleFetchError("invalid Content-Length header") from exc
                    if declared_size and declared_size > self.settings.max_bytes:
                        raise ArticleFetchError("article exceeds size limit")
                    body = response.read(self.settings.max_bytes + 1)
                    if len(body) > self.settings.max_bytes:
                        raise ArticleFetchError("article exceeds size limit")
                    final_url = normalize_url(response.geturl()) or current
                    assert_public_https_url(final_url)
                    return FetchResult(original, final_url, content_type, body)
            except (ArticleFetchError, UnsafeURLError):
                raise
            except (OSError, http.client.HTTPException) as exc:
                raise ArticleFetchError(_network_error_message(exc)) from exc
        raise ArticleFetchError("redirect limit exceeded")

    def extract(self, url: str) -> ArticleReference:
        result = self.fetch(url)
        html_text = result.body.decode("utf-8", errors="replace")
        title = ""
        text = ""
        try:
            import trafilatura
        except ImportError:
            text = clean_html(html_text)
        else:
            text = trafilatura.extract(
                html_text,
                url=result.final_url,
                include_links=False,
                include_images=False,
                include_comments=False,
                favor_precision=True,
            ) or clean_html(html_text)
            metadata = trafilatura.extract_metadata(html_text, default_url=result.final_url)
            title = metadata.title if metadata and metadata.title else ""
        if len(text) < 100:
            raise ArticleFetchError("article did not contain enough readable text")
        return ArticleReference(
            url=result.url,
            canonical_url=result.final_url,
            title=title,
            text=text,
        )
