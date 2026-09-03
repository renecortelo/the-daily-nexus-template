from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import socket
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
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArticleFetchError("robots.txt could not be decoded safely") from exc


def assert_public_https_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise UnsafeURLError("article URL must use HTTPS and include a hostname")
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve {parts.hostname}") from exc
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise UnsafeURLError(f"{parts.hostname} resolves to a non-public address")


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
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        robots_url = f"https://{parts.netloc}/robots.txt"
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
            if exc.code in {401, 403}:
                return False
            return True
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            return True
        try:
            with response:
                declared = response.headers.get("Content-Length")
                try:
                    declared_size = int(declared) if declared else None
                except ValueError as exc:
                    raise ArticleFetchError(
                        "robots.txt has an invalid Content-Length"
                    ) from exc
                if declared_size and declared_size > ROBOTS_MAX_BYTES:
                    raise ArticleFetchError(
                        "robots.txt exceeds the safety size limit"
                    )
                body = response.read(ROBOTS_MAX_BYTES + 1)
                content_encoding = response.headers.get(
                    "Content-Encoding",
                    "",
                )
        except ArticleFetchError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise ArticleFetchError(
                f"robots.txt request failed safely: {_network_error_message(exc)}"
            ) from exc
        parser.parse(_decode_robots(body, content_encoding).splitlines())
        return parser.can_fetch(self.settings.user_agent, url)

    def fetch(self, url: str) -> FetchResult:
        current = normalize_url(url)
        if not current:
            raise UnsafeURLError("article URL is invalid")
        original = current
        for redirect_count in range(self.settings.max_redirects + 1):
            if is_tracking_url(current):
                raise ArticleFetchError("tracking link was not fetched")
            assert_public_https_url(current)
            if redirect_count == 0 and not self._allowed_by_robots(current):
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
