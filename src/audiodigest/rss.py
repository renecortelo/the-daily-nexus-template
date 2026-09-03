from __future__ import annotations

import mimetypes
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from email.utils import format_datetime, parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from audiodigest.config import Settings

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
APPLE_AUDIO_TYPES = frozenset(
    {
        "audio/aac",
        "audio/mp4",
        "audio/mpeg",
        "audio/x-m4a",
    }
)
REMOTE_GUID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("content", CONTENT_NS)


@dataclass(frozen=True, slots=True)
class AppleFeedReport:
    episode_count: int
    latest_guid: str
    guids: tuple[str, ...]
    enclosure_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RemoteFeedEpisode:
    episode_date: date
    guid: str
    title: str
    published_at: str
    audio_url: str
    audio_bytes: int
    duration_seconds: float
    show_notes: tuple[str, ...]
    newspaper_url: str = ""


def _sub(parent, tag: str, text: str | None = None, **attributes):
    element = ET.SubElement(parent, tag, attributes)
    if text is not None:
        element.text = text
    return element


def _duration_text(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _duration_seconds(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError("RSS episode duration must use HH:MM:SS")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("RSS episode duration is invalid") from exc
    if hours < 0 or minutes not in range(60) or seconds not in range(60):
        raise ValueError("RSS episode duration is invalid")
    return float(hours * 3600 + minutes * 60 + seconds)


def _published_datetime(settings: Settings, episode: dict) -> datetime:
    episode_day = date.fromisoformat(episode["episode_date"])
    published = episode.get("published_at")
    return (
        datetime.fromisoformat(published).astimezone(UTC)
        if published
        else datetime.combine(
            episode_day,
            time(hour=6),
            tzinfo=settings.timezone,
        ).astimezone(UTC)
    )


def build_feed(settings: Settings, episodes: list[dict], output: Path) -> None:
    base = settings.firebase.base_url.rstrip("/")
    secret = quote(settings.firebase.secret_path, safe="")
    feed_url = f"{base}/p/{secret}/feed.xml"
    cover_url = f"{base}/p/{secret}/{quote(settings.podcast.cover_filename)}"

    rss = ET.Element("rss", {"version": "2.0"})
    channel = _sub(rss, "channel")
    _sub(channel, "title", settings.podcast.title)
    _sub(channel, "link", feed_url)
    _sub(channel, "description", settings.podcast.description)
    _sub(channel, "language", settings.podcast.language)
    _sub(channel, "generator", "The Daily Nexus")
    _sub(channel, f"{{{ITUNES_NS}}}author", settings.podcast.author)
    _sub(channel, f"{{{ITUNES_NS}}}summary", settings.podcast.description)
    _sub(channel, f"{{{ITUNES_NS}}}explicit", "true" if settings.podcast.explicit else "false")
    _sub(channel, f"{{{ITUNES_NS}}}block", "yes")
    _sub(channel, f"{{{ITUNES_NS}}}type", "episodic")
    _sub(channel, f"{{{ITUNES_NS}}}image", href=cover_url)
    _sub(channel, f"{{{ITUNES_NS}}}category", text=settings.podcast.category)
    _sub(channel, f"{{{ATOM_NS}}}link", href=feed_url, rel="self", type="application/rss+xml")
    if episodes:
        _sub(
            channel,
            "lastBuildDate",
            format_datetime(max(_published_datetime(settings, item) for item in episodes)),
        )
    image = _sub(channel, "image")
    _sub(image, "url", cover_url)
    _sub(image, "title", settings.podcast.title)
    _sub(image, "link", feed_url)

    for episode in episodes:
        episode_day = date.fromisoformat(episode["episode_date"])
        published_at = _published_datetime(settings, episode)
        audio_name = f"{episode_day.isoformat()}-{episode['guid']}.mp3"
        audio_url = f"{base}/p/{secret}/audio/{quote(audio_name, safe='')}"
        audio_path_value = str(episode.get("audio_path", ""))
        audio_path = Path(audio_path_value) if audio_path_value else None
        audio_bytes = int(episode.get("audio_bytes", 0))
        if audio_bytes <= 0:
            if audio_path is None or not audio_path.is_file():
                raise ValueError("RSS episode audio is unavailable")
            audio_bytes = audio_path.stat().st_size
        notes = episode.get("show_notes", [])
        newspaper_path = episode.get("newspaper_path")
        newspaper_url = ""
        if (
            newspaper_path
            and Path(newspaper_path).is_file()
        ) or episode.get("remote_newspaper"):
            newspaper_name = f"{episode_day.isoformat()}-{episode['guid']}.pdf"
            newspaper_url = f"{base}/p/{secret}/read/{quote(newspaper_name, safe='')}"
            notes = [*notes, f"Read the Nexus edition - {newspaper_url}"]
        description = "\n".join(notes)
        item = _sub(channel, "item")
        _sub(item, "title", episode["title"])
        _sub(item, "guid", episode["guid"], isPermaLink="false")
        _sub(item, "pubDate", format_datetime(published_at))
        if newspaper_url:
            _sub(item, "link", newspaper_url)
        _sub(item, "description", description)
        _sub(item, f"{{{CONTENT_NS}}}encoded", description.replace("\n", "<br/>"))
        _sub(item, f"{{{ITUNES_NS}}}summary", description)
        _sub(item, f"{{{ITUNES_NS}}}episodeType", "full")
        _sub(
            item,
            "enclosure",
            url=audio_url,
            length=str(audio_bytes),
            type=mimetypes.guess_type(audio_name)[0] or "audio/mpeg",
        )
        _sub(item, f"{{{ITUNES_NS}}}duration", _duration_text(episode["duration_seconds"]))
        _sub(
            item,
            f"{{{ITUNES_NS}}}explicit",
            "true" if settings.podcast.explicit else "false",
        )

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)


def _validate_feed_root(root: ET.Element) -> AppleFeedReport:
    def required_text(parent: ET.Element, tag: str, label: str) -> str:
        value = parent.findtext(tag, "").strip()
        if not value:
            raise ValueError(f"RSS {label} is missing")
        return value

    if root.tag != "rss" or root.attrib.get("version") != "2.0":
        raise ValueError("RSS root must be version 2.0")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel is missing")
    required_text(channel, "title", "channel title")
    required_text(channel, "description", "channel description")
    required_text(channel, "language", "channel language")
    required_text(channel, f"{{{ITUNES_NS}}}author", "iTunes author")
    required_text(channel, f"{{{ITUNES_NS}}}category", "iTunes category")
    if channel.findtext(f"{{{ITUNES_NS}}}block") != "yes":
        raise ValueError("RSS feed must block public Apple directory listing")
    image = channel.find(f"{{{ITUNES_NS}}}image")
    if image is None or not image.attrib.get("href", "").startswith("https://"):
        raise ValueError("RSS iTunes artwork URL is invalid")
    atom_link = channel.find(f"{{{ATOM_NS}}}link")
    if (
        atom_link is None
        or atom_link.attrib.get("rel") != "self"
        or not atom_link.attrib.get("href", "").startswith("https://")
    ):
        raise ValueError("RSS self link is invalid")

    items = channel.findall("item")
    if not items:
        raise ValueError("RSS feed must contain at least one episode")
    guids: set[str] = set()
    enclosure_urls: list[str] = []
    for item in items:
        required_text(item, "title", "episode title")
        required_text(item, "description", "episode description")
        guid = required_text(item, "guid", "episode GUID")
        if guid in guids:
            raise ValueError(f"RSS episode GUID is duplicated: {guid}")
        guids.add(guid)
        published = required_text(item, "pubDate", "episode publication date")
        try:
            parsedate_to_datetime(published)
        except (TypeError, ValueError) as exc:
            raise ValueError("RSS episode publication date is invalid") from exc
        enclosure = item.find("enclosure")
        if enclosure is None:
            raise ValueError("RSS episode enclosure is missing")
        enclosure_url = enclosure.attrib.get("url", "")
        if not enclosure_url.startswith("https://"):
            raise ValueError("episode enclosure URL is invalid")
        enclosure_type = enclosure.attrib.get("type", "")
        if enclosure_type not in APPLE_AUDIO_TYPES:
            raise ValueError(
                f"episode enclosure type is not supported by Apple Podcasts: {enclosure_type}"
            )
        try:
            enclosure_length = int(enclosure.attrib.get("length", "0"))
        except ValueError as exc:
            raise ValueError("episode enclosure length is invalid") from exc
        if enclosure_length <= 0:
            raise ValueError("episode enclosure length must be positive")
        required_text(item, f"{{{ITUNES_NS}}}duration", "iTunes duration")
        enclosure_urls.append(enclosure_url)
    return AppleFeedReport(
        episode_count=len(items),
        latest_guid=required_text(items[0], "guid", "latest episode GUID"),
        guids=tuple(
            required_text(item, "guid", "episode GUID")
            for item in items
        ),
        enclosure_urls=tuple(enclosure_urls),
    )


def validate_feed_bytes(value: bytes) -> AppleFeedReport:
    uppercase_prefix = value[:4096].upper()
    if b"<!DOCTYPE" in uppercase_prefix or b"<!ENTITY" in uppercase_prefix:
        raise ValueError("RSS feed must not contain document types or XML entities")
    try:
        root = ET.parse(BytesIO(value)).getroot()  # noqa: S314
    except ET.ParseError as exc:
        raise ValueError(f"RSS feed is not valid XML: {exc}") from exc
    return _validate_feed_root(root)


def parse_remote_feed_bytes(
    value: bytes,
    *,
    base_url: str,
    secret_path: str,
    maximum_episodes: int,
) -> tuple[RemoteFeedEpisode, ...]:
    validate_feed_bytes(value)
    root = ET.parse(BytesIO(value)).getroot()  # noqa: S314 - validated above.
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel is missing")
    configured = urlsplit(base_url)
    if configured.scheme != "https" or not configured.hostname:
        raise ValueError("configured Firebase URL is invalid")
    private_root = f"/p/{quote(secret_path, safe='')}"
    result: list[RemoteFeedEpisode] = []
    for item in channel.findall("item")[:maximum_episodes]:
        guid = item.findtext("guid", "").strip()
        if not REMOTE_GUID_PATTERN.fullmatch(guid):
            raise ValueError("remote RSS episode GUID is invalid")
        title = item.findtext("title", "").strip()
        published_text = item.findtext("pubDate", "").strip()
        description = item.findtext("description", "")
        duration_text = item.findtext(f"{{{ITUNES_NS}}}duration", "").strip()
        enclosure = item.find("enclosure")
        if enclosure is None:
            raise ValueError("RSS episode enclosure is missing")
        audio_url = enclosure.attrib.get("url", "")
        parsed_audio = urlsplit(audio_url)
        if (
            parsed_audio.scheme != "https"
            or parsed_audio.hostname != configured.hostname
            or parsed_audio.username
            or parsed_audio.password
            or parsed_audio.port not in {None, 443}
            or parsed_audio.query
            or parsed_audio.fragment
            or not parsed_audio.path.startswith(f"{private_root}/audio/")
        ):
            raise ValueError("remote RSS audio escaped the private Firebase path")
        audio_name = unquote(parsed_audio.path.rsplit("/", 1)[-1])
        suffix = f"-{guid}.mp3"
        if not audio_name.endswith(suffix):
            raise ValueError("remote RSS audio filename does not match its GUID")
        try:
            episode_date = date.fromisoformat(audio_name[:10])
            published_at = parsedate_to_datetime(published_text).astimezone(UTC)
            audio_bytes = int(enclosure.attrib.get("length", "0"))
        except (TypeError, ValueError) as exc:
            raise ValueError("remote RSS episode metadata is invalid") from exc
        if audio_bytes <= 0 or audio_bytes > 150 * 1024 * 1024:
            raise ValueError("remote RSS audio size exceeds the safety limit")
        newspaper_url = item.findtext("link", "").strip()
        if newspaper_url:
            parsed_newspaper = urlsplit(newspaper_url)
            expected_pdf_name = quote(
                f"{episode_date.isoformat()}-{guid}.pdf",
                safe="",
            )
            expected_pdf = f"{private_root}/read/{expected_pdf_name}"
            if (
                parsed_newspaper.scheme != "https"
                or parsed_newspaper.hostname != configured.hostname
                or parsed_newspaper.username
                or parsed_newspaper.password
                or parsed_newspaper.port not in {None, 443}
                or parsed_newspaper.path != expected_pdf
                or parsed_newspaper.query
                or parsed_newspaper.fragment
            ):
                raise ValueError("remote RSS newspaper escaped the private Firebase path")
        notes = tuple(
            line.strip()
            for line in description.splitlines()
            if line.strip()
        )
        result.append(
            RemoteFeedEpisode(
                episode_date=episode_date,
                guid=guid,
                title=title,
                published_at=published_at.isoformat(),
                audio_url=audio_url,
                audio_bytes=audio_bytes,
                duration_seconds=_duration_seconds(duration_text),
                show_notes=notes[:100],
                newspaper_url=newspaper_url,
            )
        )
    return tuple(result)


def validate_feed(path: Path) -> AppleFeedReport:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read RSS feed: {exc}") from exc
    return validate_feed_bytes(value)
