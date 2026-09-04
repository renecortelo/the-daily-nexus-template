from __future__ import annotations

import base64
import binascii
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

BLOCKED_LINK_TERMS = (
    "unsubscribe",
    "subscribe now",
    "sign up",
    "sign in",
    "log in",
    "manage preferences",
    "email preferences",
    "privacy",
    "terms",
    "view in browser",
    "view online",
    "facebook",
    "instagram",
    "linkedin",
    "twitter",
    "x.com",
    "youtube",
    "mailto:",
)

TRACKING_DOMAIN_SUFFIXES = (
    "list-manage.com",
    "maillist-manage.eu",
    "convertkit-mail4.com",
    "convertk.it",
    "rs6.net",
    "acemlnb.com",
)

TRACKING_HOSTS = {
    "open.substack.com",
}

TRACKING_HOST_LABELS = {
    "click",
    "clicks",
    "e",
    "email",
    "link",
    "links",
    "nl",
    "track",
    "tracking",
}

UTILITY_HOST_SUFFIXES = (
    "apps.apple.com",
    "constantcontact.com",
    "play.google.com",
    "youtube.com",
    "youtu.be",
)

UTILITY_PATH_SEGMENTS = {
    "account",
    "advertise",
    "app",
    "apps",
    "auth",
    "careers",
    "jobs",
    "login",
    "preferences",
    "register",
    "sign-up",
    "signin",
    "signup",
    "subscribe",
    "subscription",
}

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "referrer",
}


@dataclass(slots=True)
class EditorialLinkResult:
    urls: list[str]
    tracking_skipped: int = 0
    utility_skipped: int = 0
    invalid_skipped: int = 0
    unwrapped: int = 0
    not_selected: int = 0

    def stats(self) -> dict[str, int]:
        return {
            "tracking_skipped": self.tracking_skipped,
            "utility_skipped": self.utility_skipped,
            "invalid_skipped": self.invalid_skipped,
            "unwrapped": self.unwrapped,
            "not_selected": self.not_selected,
        }


class _FallbackHTMLExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._anchor_text: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "a":
            self._href = dict(attrs).get("href", "")
            self._anchor_text = []
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor_text)))
            self._href = ""

    def handle_data(self, data: str):
        if self._ignored_depth:
            return
        self.text.append(data)
        if self._href:
            self._anchor_text.append(data)


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if any(term in lowered for term in ("unsubscribe", "manage preferences")):
            continue
        if stripped:
            lines.append(stripped)
    return "\n".join(lines).strip()


def clean_html(value: str) -> str:
    if not value:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        parser = _FallbackHTMLExtractor()
        parser.feed(value)
        return _normalize_whitespace(html.unescape("".join(parser.text)))
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "form", "nav"]):
        tag.decompose()
    for tag in soup.find_all(
        attrs={"class": re.compile(r"unsubscribe|footer|social|advert", re.I)}
    ):
        tag.decompose()
    return _normalize_whitespace(soup.get_text("\n"))


def normalize_url(value: str) -> str:
    parts = urlsplit(html.unescape(value.strip()))
    if parts.scheme.lower() != "https" or not parts.hostname:
        return ""
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    try:
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parts.port and parts.port != 443:
        netloc = f"{netloc}:{parts.port}"
    path = quote(
        re.sub(r"/{2,}", "/", parts.path or "/"),
        safe="/%:@!$&'()*+,;=-._~",
    )
    return urlunsplit(("https", netloc, path, urlencode(query, doseq=True), ""))


def _host_matches_suffix(hostname: str, suffix: str) -> bool:
    return hostname == suffix or hostname.endswith(f".{suffix}")


def is_tracking_url(value: str) -> bool:
    parts = urlsplit(html.unescape(value.strip()))
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return False
    path = unquote(parts.path).lower()
    if hostname == "substack.com" and path.startswith(("/redirect/", "/app-link/")):
        return True
    if hostname in TRACKING_HOSTS:
        return True
    if any(_host_matches_suffix(hostname, suffix) for suffix in TRACKING_DOMAIN_SUFFIXES):
        return True
    labels = hostname.split(".")[:-2]
    return any(
        label in TRACKING_HOST_LABELS or re.fullmatch(r"(?:click|elink|link|track|url)\d*", label)
        for label in labels
    )


def _decode_repeatedly(value: str) -> str:
    decoded = html.unescape(value.strip())
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _urls_from_text_payload(value: str) -> list[str]:
    decoded = _decode_repeatedly(value)
    if decoded.startswith("https://"):
        return [decoded]
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    return [
        _decode_repeatedly(candidate)
        for candidate in payload.values()
        if isinstance(candidate, str) and _decode_repeatedly(candidate).startswith("https://")
    ]


def _urls_from_base64(value: str) -> list[str]:
    token = _decode_repeatedly(value).strip()
    if "." in token:
        token = token.rsplit(".", 1)[-1]
    if not 16 <= len(token) <= 16_384 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", token):
        return []
    padded = token.rstrip("=") + "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return []
    return _urls_from_text_payload(decoded)


def _tracking_destinations(value: str) -> list[str]:
    parts = urlsplit(html.unescape(value.strip()))
    destinations: list[str] = []

    path = _decode_repeatedly(parts.path)
    tldr_match = re.match(
        r"^/CL0/(https://.+?)/\d+/[A-Za-z0-9_-]{16,}(?:/.*)?$",
        path,
        flags=re.IGNORECASE,
    )
    if tldr_match:
        destinations.append(tldr_match.group(1))

    segments = [segment for segment in parts.path.split("/") if segment]
    if (
        (parts.hostname or "").lower() == "open.substack.com"
        and len(segments) >= 4
        and segments[0] == "pub"
        and segments[2] == "p"
        and re.fullmatch(r"[A-Za-z0-9-]+", segments[1])
        and re.fullmatch(r"[A-Za-z0-9-]+", segments[3])
    ):
        destinations.append(f"https://{segments[1]}.substack.com/p/{segments[3]}")

    values = [item for _, item in parse_qsl(parts.query, keep_blank_values=False)]
    values.extend(segments)
    for item in values:
        destinations.extend(_urls_from_text_payload(item))
        destinations.extend(_urls_from_base64(item))
    return destinations


def unwrap_tracking_url(value: str, *, max_depth: int = 3) -> str:
    current = html.unescape(value.strip())
    if not is_tracking_url(current):
        return normalize_url(current)
    if max_depth <= 0:
        return ""

    current_normalized = normalize_url(current)
    utility_fallback = ""
    for candidate in _tracking_destinations(current):
        normalized = normalize_url(candidate)
        if not normalized or normalized == current_normalized:
            continue
        resolved = (
            unwrap_tracking_url(normalized, max_depth=max_depth - 1)
            if is_tracking_url(normalized)
            else normalized
        )
        if not resolved:
            continue
        if not _is_utility_url(resolved):
            return resolved
        utility_fallback = utility_fallback or resolved
    return utility_fallback


def _is_utility_url(value: str) -> bool:
    parts = urlsplit(value)
    hostname = (parts.hostname or "").lower()
    if any(_host_matches_suffix(hostname, suffix) for suffix in UTILITY_HOST_SUFFIXES):
        return True
    if hostname.split(".", 1)[0] in {"account", "advertise", "ads"}:
        return True
    segments = {segment.lower() for segment in parts.path.split("/") if segment}
    if not segments:
        return True
    return bool(segments & UTILITY_PATH_SEGMENTS)


def extract_editorial_links_with_stats(value: str, *, limit: int = 3) -> EditorialLinkResult:
    if not value or limit <= 0:
        return EditorialLinkResult(urls=[])
    candidates: list[tuple[str, str]] = []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        parser = _FallbackHTMLExtractor()
        parser.feed(value)
        candidates = parser.links
    else:
        soup = BeautifulSoup(value, "html.parser")
        candidates = [
            (str(tag.get("href", "")), tag.get_text(" ", strip=True))
            for tag in soup.find_all("a", href=True)
        ]
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    result = EditorialLinkResult(urls=[])
    for position, (href, anchor) in enumerate(candidates):
        combined = f"{href} {anchor}".lower()
        if any(term in combined for term in BLOCKED_LINK_TERMS):
            result.utility_skipped += 1
            continue
        if is_tracking_url(href):
            unwrapped = unwrap_tracking_url(href)
            if not unwrapped:
                result.tracking_skipped += 1
                continue
            href = unwrapped
            result.unwrapped += 1
        normalized = normalize_url(href)
        if not normalized:
            result.invalid_skipped += 1
            continue
        if is_tracking_url(normalized):
            result.tracking_skipped += 1
            continue
        if _is_utility_url(normalized):
            result.utility_skipped += 1
            continue
        if normalized and normalized not in seen:
            seen.add(normalized)
            anchor_words = len(anchor.split())
            score = min(len(anchor.strip()), 120) + (30 if anchor_words >= 3 else 0)
            if re.search(r"/(?:article|story|news|post|20\d{2})/", normalized):
                score += 20
            scored.append((score, -position, normalized))
    scored.sort(reverse=True)
    result.not_selected = max(0, len(scored) - limit)
    result.urls = [item[2] for item in scored[:limit]]
    return result


def extract_editorial_links(value: str, *, limit: int = 3) -> list[str]:
    return extract_editorial_links_with_stats(value, limit=limit).urls
