from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from audiodigest.config import Settings
from audiodigest.database import StateDatabase
from audiodigest.private_store import (
    PrivateStoreError,
    delete_private_value,
    read_private_value,
    write_private_value,
)
from audiodigest.rss import (
    APPLE_AUDIO_TYPES,
    RemoteFeedEpisode,
    build_feed,
    parse_remote_feed_bytes,
    validate_feed,
    validate_feed_bytes,
)
from audiodigest.runtime_environment import local_tool_environment


class PublishError(RuntimeError):
    pass


MAX_SPARK_PUBLIC_TREE_BYTES = 1024 * 1024 * 1024
MAX_REMOTE_FEED_BYTES = 2 * 1024 * 1024
REMOTE_VERIFY_TIMEOUT_SECONDS = 30
REMOTE_VERIFY_RETRY_DELAYS_SECONDS = (0.0, 1.0, 2.0, 4.0)
WEB_APP_FILES = (
    "index.html",
    "styles.css",
    "app.js",
    "manifest.webmanifest",
    "service-worker.js",
)
WEB_APP_ASSETS = (
    "tdn-icon.png",
    "tdn-icon-transparent.png",
    "google-g.png",
)


def _assert_managed_directory(path: Path, runtime_dir: Path, project_dir: Path) -> None:
    resolved = path.resolve()
    runtime = runtime_dir.resolve()
    project = project_dir.resolve()
    allowed = (
        resolved != runtime
        and resolved != project
        and (runtime in resolved.parents or project in resolved.parents)
    )
    if not allowed:
        raise PublishError(f"refusing to rebuild unmanaged directory: {resolved}")


def _validate_apple_artwork(path: Path) -> None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
            image_format = image.format
            image_mode = image.mode
    except (OSError, ValueError) as exc:
        raise PublishError(f"podcast artwork could not be inspected: {exc}") from exc
    if width != height or not 1400 <= width <= 3000:
        raise PublishError(
            "Apple podcast artwork must be square and between 1400 and 3000 pixels"
        )
    if image_format not in {"JPEG", "PNG"}:
        raise PublishError("Apple podcast artwork must be JPEG or PNG")
    if image_mode != "RGB":
        raise PublishError("Apple podcast artwork must use RGB color without transparency")


def _validate_apple_audio_probe(path: Path, payload: dict) -> None:
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise PublishError(f"audio probe did not find a stream: {path.name}")
    stream = streams[0]
    codec = str(stream.get("codec_name", "")).lower()
    if codec not in {"aac", "mp3"}:
        raise PublishError(f"Apple private RSS requires MP3 or AAC audio: {path.name}")
    try:
        sample_rate = int(stream.get("sample_rate", 0))
        channels = int(stream.get("channels", 0))
        bit_rate = int(stream.get("bit_rate", 0))
    except (TypeError, ValueError) as exc:
        raise PublishError(f"audio probe returned invalid values: {path.name}") from exc
    if sample_rate not in {44100, 48000}:
        raise PublishError(
            f"Apple-ready audio must use a 44.1 or 48 kHz sample rate: {path.name}"
        )
    if channels not in {1, 2}:
        raise PublishError(f"Apple-ready audio must be mono or stereo: {path.name}")
    recommended_minimum = 64_000 if channels == 1 else 128_000
    if bit_rate < recommended_minimum:
        raise PublishError(
            "Audio bitrate is below Apple's recommended minimum "
            f"for {channels}-channel audio: {path.name}"
        )


def _validate_apple_audio(path: Path, ffprobe: str, environment: dict[str, str]) -> None:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,channels,sample_rate,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise PublishError(
            f"Apple audio validation failed for {path.name}: "
            f"{completed.stderr.strip()[:500]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PublishError(f"audio probe returned invalid JSON: {path.name}") from exc
    _validate_apple_audio_probe(path, payload)


def _public_tree_size(path: Path) -> int:
    return sum(
        item.stat().st_size
        for item in path.rglob("*")
        if item.is_file()
    )


def _copy_static_web_app(project_dir: Path, public_dir: Path) -> None:
    web_source = project_dir / "web"
    asset_source = project_dir / "assets"
    public_assets = public_dir / "assets"
    public_dir.mkdir(parents=True, exist_ok=True)
    public_assets.mkdir(parents=True, exist_ok=True)
    for name in WEB_APP_FILES:
        source = web_source / name
        if not source.is_file() or source.is_symlink():
            raise PublishError(f"missing or unsafe V4 web asset: web/{name}")
        shutil.copy2(source, public_dir / name)
    _write_cloud_clock_config(public_dir / "cloud-clock-config.js")
    for name in WEB_APP_ASSETS:
        source = asset_source / name
        if not source.is_file() or source.is_symlink():
            raise PublishError(f"missing or unsafe V4 image asset: assets/{name}")
        shutil.copy2(source, public_assets / name)


def _write_cloud_clock_config(path: Path) -> None:
    """Write the public Worker endpoint without ever storing it in Git.

    The endpoint is not a credential, but it is deployment-specific. It is
    supplied only through the private runner/local deployment environment and
    is intentionally overwritten with an empty configuration otherwise.
    """

    endpoint = os.environ.get("TDN_CLOUD_CLOCK_URL", "").strip()
    if endpoint:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".workers.dev")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise PublishError("cloud clock endpoint must be a standard HTTPS workers.dev URL")
        endpoint = f"https://{parsed.hostname}"
    payload = (
        "// Generated for this private Hosting release.\n"
        "window.TDN_CLOUD_CLOCK = Object.freeze({ endpoint: "
        f"{json.dumps(endpoint)}"
        " });\n"
    )
    path.write_text(payload, encoding="utf-8")


def _fetch_remote(
    url: str,
    *,
    expected_host: str,
    maximum_bytes: int,
    accept: str,
    byte_range: bool = False,
    allow_not_found: bool = False,
) -> tuple[bytes, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != expected_host:
        raise PublishError("remote verification URL escaped the configured Firebase host")
    headers = {
        "Accept": accept,
        "Cache-Control": "no-cache, max-age=0",
        "Pragma": "no-cache",
        "User-Agent": "TheDailyNexus/0.7.0 ApplePrivateFeedVerifier",
    }
    if byte_range:
        headers["Range"] = "bytes=0-0"
    request = Request(  # noqa: S310 - HTTPS and the exact Firebase host are enforced.
        url,
        headers=headers,
    )
    try:
        with urlopen(  # noqa: S310 - host and scheme are restricted above.
            request,
            timeout=REMOTE_VERIFY_TIMEOUT_SECONDS,
        ) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname != expected_host:
                raise PublishError("Firebase verification redirected outside the expected host")
            content_type = response.headers.get_content_type()
            value = response.read(
                maximum_bytes if byte_range else maximum_bytes + 1
            )
    except HTTPError as exc:
        if exc.code == 404 and allow_not_found:
            return b"", ""
        raise PublishError(
            f"remote Firebase verification returned HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PublishError(
            f"remote Firebase verification failed ({type(exc).__name__})"
        ) from exc
    if not value:
        raise PublishError("remote Firebase response was empty")
    if not byte_range and len(value) > maximum_bytes:
        raise PublishError("remote Firebase response exceeded the verification size limit")
    return value, content_type


def load_remote_publication(
    settings: Settings,
    *,
    maximum_episodes: int,
) -> tuple[RemoteFeedEpisode, ...]:
    feed_url = (
        f"{settings.firebase.base_url}/p/{settings.firebase.secret_path}/feed.xml"
    )
    expected_host = urlsplit(settings.firebase.base_url).hostname or ""
    feed_bytes, content_type = _fetch_remote(
        feed_url,
        expected_host=expected_host,
        maximum_bytes=MAX_REMOTE_FEED_BYTES,
        accept="application/rss+xml, application/xml;q=0.9",
        allow_not_found=True,
    )
    if not feed_bytes:
        return ()
    if content_type not in {"application/rss+xml", "application/xml", "text/xml"}:
        raise PublishError("existing private feed has an unexpected content type")
    try:
        remote_episodes = parse_remote_feed_bytes(
            feed_bytes,
            base_url=settings.firebase.base_url,
            secret_path=settings.firebase.secret_path,
            maximum_episodes=maximum_episodes,
        )
    except ValueError as exc:
        raise PublishError(f"existing private feed could not be preserved: {exc}") from exc
    return remote_episodes


def _remote_episode_record(remote: RemoteFeedEpisode) -> dict:
    generated_read_prefix = "Read the Nexus edition - "
    return {
        "episode_date": remote.episode_date.isoformat(),
        "guid": remote.guid,
        "title": remote.title,
        "audio_path": "",
        "audio_bytes": remote.audio_bytes,
        "duration_seconds": remote.duration_seconds,
        "published_at": remote.published_at,
        "status": "published",
        "show_notes": [
            note
            for note in remote.show_notes
            if not note.startswith(generated_read_prefix)
        ],
        "newspaper_path": None,
        "remote_newspaper": bool(remote.newspaper_url),
        "remote_only": True,
    }


def _remote_media_paths(remote: RemoteFeedEpisode) -> tuple[str, ...]:
    result = [urlsplit(remote.audio_url).path]
    if remote.newspaper_url:
        result.append(urlsplit(remote.newspaper_url).path)
    return tuple(result)


def _cache_busted_url(url: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_tdn_verify", secrets.token_urlsafe(12)))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def _verify_remote_private_feed_once(
    feed_url: str,
    *,
    expected_host: str,
    expected_guid: str,
) -> int:
    feed_bytes, content_type = _fetch_remote(
        _cache_busted_url(feed_url),
        expected_host=expected_host,
        maximum_bytes=MAX_REMOTE_FEED_BYTES,
        accept="application/rss+xml, application/xml;q=0.9",
    )
    if content_type not in {"application/rss+xml", "application/xml", "text/xml"}:
        raise PublishError(
            f"remote feed has an unexpected content type: {content_type or 'missing'}"
        )
    try:
        report = validate_feed_bytes(feed_bytes)
    except ValueError as exc:
        raise PublishError(f"remote Apple RSS validation failed: {exc}") from exc
    if expected_guid not in report.guids:
        raise PublishError("the newly published episode is missing from the remote feed")
    audio_url = report.enclosure_urls[report.guids.index(expected_guid)]
    _sample, audio_content_type = _fetch_remote(
        audio_url,
        expected_host=expected_host,
        maximum_bytes=1,
        accept="audio/mpeg, audio/mp4, audio/aac",
        byte_range=True,
    )
    if audio_content_type not in APPLE_AUDIO_TYPES:
        raise PublishError(
            "remote episode has an Apple-incompatible content type: "
            f"{audio_content_type or 'missing'}"
        )
    return report.episode_count


def verify_remote_private_feed(
    feed_url: str,
    *,
    expected_guid: str,
    retry_delays: tuple[float, ...] = REMOTE_VERIFY_RETRY_DELAYS_SECONDS,
) -> int:
    parsed_feed_url = urlsplit(feed_url)
    expected_host = parsed_feed_url.hostname or ""
    if not (
        expected_host.endswith(".web.app")
        or expected_host.endswith(".firebaseapp.com")
    ):
        raise PublishError("remote verification requires a standard Firebase Hosting URL")
    if not parsed_feed_url.path.startswith("/p/"):
        raise PublishError("remote verification requires the private feed path")
    if not retry_delays:
        raise ValueError("remote verification requires at least one attempt")

    last_error: PublishError | None = None
    for delay_seconds in retry_delays:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            return _verify_remote_private_feed_once(
                feed_url,
                expected_host=expected_host,
                expected_guid=expected_guid,
            )
        except PublishError as exc:
            last_error = exc

    if last_error is None:
        raise PublishError("remote Firebase verification did not run")
    raise PublishError(
        "remote Firebase propagation did not verify after "
        f"{len(retry_delays)} attempts: {last_error}"
    ) from last_error


@dataclass(frozen=True, slots=True)
class PublishResult:
    feed_url: str
    episode_count: int
    hosted_bytes: int
    remote_verified: bool = True


class FirebasePublisher:
    def __init__(self, settings: Settings, database: StateDatabase):
        self.settings = settings
        self.database = database

    def _build_tree(
        self,
        episode_date: date,
        *,
        remote_episodes: tuple[RemoteFeedEpisode, ...] = (),
    ) -> tuple[list[dict], int, tuple[str, ...]]:
        public = self.settings.firebase.public_dir
        _assert_managed_directory(public, self.settings.app.runtime_dir, self.settings.project_dir)
        if public.exists():
            shutil.rmtree(public)
        _copy_static_web_app(self.settings.project_dir, public)
        secret_root = public / "p" / self.settings.firebase.secret_path
        audio_root = secret_root / "audio"
        read_root = secret_root / "read"
        audio_root.mkdir(parents=True, exist_ok=True)
        read_root.mkdir(parents=True, exist_ok=True)

        local_episodes = self.database.feed_episodes(
            include_staged_date=episode_date,
            limit=self.settings.app.retention_days,
        )
        local_guids = {str(episode["guid"]) for episode in local_episodes}
        combined = {
            remote.guid: _remote_episode_record(remote)
            for remote in remote_episodes
            if remote.guid not in local_guids
        }
        combined.update(
            {
                str(episode["guid"]): episode
                for episode in local_episodes
            }
        )
        episodes = sorted(
            combined.values(),
            key=lambda episode: str(episode["episode_date"]),
            reverse=True,
        )[: self.settings.app.retention_days]
        retained_guids = {
            str(episode["guid"])
            for episode in episodes
        }
        removed_paths = tuple(
            path
            for remote in remote_episodes
            if (
                remote.guid not in retained_guids
            )
            for path in _remote_media_paths(remote)
        )
        environment = local_tool_environment(
            dict(os.environ),
            self.settings.app.runtime_dir,
        )
        for episode in episodes:
            if episode.get("remote_only"):
                continue
            source = Path(episode["audio_path"])
            if not source.is_file():
                raise PublishError(f"missing episode audio: {source}")
            _validate_apple_audio(
                source,
                self.settings.audio.ffprobe,
                environment,
            )
            target_name = f"{episode['episode_date']}-{episode['guid']}.mp3"
            shutil.copy2(source, audio_root / target_name)
            newspaper_value = episode.get("newspaper_path")
            if newspaper_value:
                newspaper = Path(newspaper_value)
                if newspaper.is_file():
                    newspaper_stem = f"{episode['episode_date']}-{episode['guid']}"
                    newspaper_name = f"{newspaper_stem}.pdf"
                    shutil.copy2(newspaper, read_root / newspaper_name)
                    for preview in sorted(newspaper.parent.glob("edition-[0-9]*.png")):
                        suffix = preview.stem.removeprefix("edition")
                        if suffix and preview.is_file():
                            shutil.copy2(
                                preview,
                                read_root / f"{newspaper_stem}{suffix}.png",
                            )

        cover_source = self.settings.project_dir / "assets" / self.settings.podcast.cover_filename
        if not cover_source.is_file():
            raise PublishError(f"missing podcast cover: {cover_source}")
        _validate_apple_artwork(cover_source)
        shutil.copy2(cover_source, secret_root / self.settings.podcast.cover_filename)

        feed_path = secret_root / "feed.xml"
        build_feed(self.settings, episodes, feed_path)
        validate_feed(feed_path)
        hosted_bytes = _public_tree_size(public)
        retained_remote_bytes = sum(
            int(episode.get("audio_bytes", 0))
            for episode in episodes
            if episode.get("remote_only")
        )
        if hosted_bytes + retained_remote_bytes > MAX_SPARK_PUBLIC_TREE_BYTES:
            raise PublishError(
                "Private feed exceeds the conservative 1 GB Spark publishing ceiling. "
                "Reduce retention rather than enabling billing."
            )
        return episodes, hosted_bytes, removed_paths

    def publish(self, episode_date: date) -> PublishResult:
        removal_manifest: Path | None = None
        remote_episodes: tuple[RemoteFeedEpisode, ...] = ()
        if (
            self.settings.web.enabled
            and self.settings.firebase.deployment_token_file_path is None
        ):
            raise PublishError(
                "web-managed private publishing must run through the cloud runner; "
                "a desktop deploy could replace the hosted archive"
            )
        if self.settings.firebase.deployment_token_file_path is not None:
            remote_episodes = load_remote_publication(
                self.settings,
                maximum_episodes=self.settings.app.retention_days + 1,
            )
        episodes, hosted_bytes, removed_paths = self._build_tree(
            episode_date,
            remote_episodes=remote_episodes,
        )
        token_path = self.settings.firebase.deployment_token_file_path
        if token_path is None:
            command = [
                self.settings.firebase.executable,
                "deploy",
                "--only",
                "hosting",
                "--project",
                self.settings.firebase.project_id,
                "--non-interactive",
            ]
        else:
            removal_manifest = (
                self.settings.app.runtime_dir
                / "secrets"
                / "firebase-remove-paths.json"
            )
            try:
                write_private_value(
                    removal_manifest,
                    json.dumps(list(removed_paths), separators=(",", ":")),
                )
            except PrivateStoreError as exc:
                raise PublishError(str(exc)) from exc
            command = [
                "node",
                str(self.settings.project_dir / "scripts" / "firebase-clone-deploy.cjs"),
                "--project",
                self.settings.firebase.project_id,
                "--public",
                str(self.settings.firebase.public_dir),
                "--remove-manifest",
                str(removal_manifest),
            ]
        environment = local_tool_environment(
            dict(os.environ),
            self.settings.app.runtime_dir,
        )
        if token_path is not None:
            try:
                deployment_token = read_private_value(token_path)
            except PrivateStoreError as exc:
                raise PublishError(str(exc)) from exc
            if not deployment_token:
                raise PublishError("Firebase deployment token file is empty")
            environment["FIREBASE_TOKEN"] = deployment_token.strip()
        try:
            completed = subprocess.run(
                command,
                cwd=self.settings.project_dir,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            if removal_manifest is not None:
                try:
                    delete_private_value(removal_manifest)
                except PrivateStoreError:
                    pass
        if completed.returncode != 0:
            raise PublishError(f"Firebase deploy failed: {completed.stderr.strip()[:2000]}")
        feed_url = (
            f"{self.settings.firebase.base_url}/p/{self.settings.firebase.secret_path}/feed.xml"
        )
        current_episode = self.database.episode_for_date(episode_date)
        if current_episode is None:
            raise PublishError("published episode was not included in the local episode store")
        expected_episode = next(
            (
                episode
                for episode in episodes
                if episode["guid"] == current_episode["guid"]
            ),
            None,
        )
        if expected_episode is None:
            raise PublishError("published episode was not included in the local feed tree")
        remote_episode_count = verify_remote_private_feed(
            feed_url,
            expected_guid=str(expected_episode["guid"]),
        )
        self.database.mark_published(episode_date)
        return PublishResult(
            feed_url=feed_url,
            episode_count=remote_episode_count,
            hosted_bytes=hosted_bytes,
        )
