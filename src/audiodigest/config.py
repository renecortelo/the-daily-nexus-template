from __future__ import annotations

import os
import re
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from audiodigest.constants import normalize_custom_sections
from audiodigest.preferences import (
    editorial_tone,
    validate_gmail_label,
    voice_profile,
)

_WINDOWS_ENVIRONMENT = re.compile(r"%([^%]+)%")
FIREBASE_FEED_VAULT_SERVICE = "TheDailyNexusFirebase"
FIREBASE_FEED_STORAGE_KIND = "keyring"


def _expand_environment(value: str) -> str:
    portable = _WINDOWS_ENVIRONMENT.sub(
        lambda match: os.environ.get(match.group(1), match.group(0)),
        value,
    )
    return os.path.expandvars(os.path.expanduser(portable))


def _expand_path(value: str, base_dir: Path) -> Path:
    expanded = _expand_environment(value)
    path = Path(expanded)
    return path if path.is_absolute() else (base_dir / path).resolve()


def _expand_executable(value: str, base_dir: Path) -> str:
    expanded = _expand_environment(value)
    path = Path(expanded)
    if path.is_absolute():
        return str(path)
    if "/" in expanded or "\\" in expanded:
        return str((base_dir / path).resolve())
    return expanded


def firebase_secret_username(project_id: str) -> str:
    """Return the non-secret credential-vault key for one Firebase project."""
    return f"{project_id}-private-feed-path"


def read_firebase_secret(project_id: str) -> str:
    """Read the private feed path from the operating system credential vault."""
    if not project_id or "REPLACE_" in project_id:
        return ""
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise ValueError(
            "keyring is required to read the private feed path securely"
        ) from exc
    try:
        return keyring.get_password(
            FIREBASE_FEED_VAULT_SERVICE,
            firebase_secret_username(project_id),
        ) or ""
    except KeyringError as exc:
        raise ValueError(
            "the operating system credential vault could not read the private feed path"
        ) from exc


@dataclass(slots=True)
class AppSettings:
    timezone: str = "UTC"
    gmail_label: str = "AudioDigest/Source"
    retention_days: int = 30
    target_min_words: int = 2850
    target_max_words: int = 3800
    max_newsletters: int = 80
    max_articles_per_newsletter: int = 5
    runtime_dir: Path = field(
        default_factory=lambda: Path(os.getenv("LOCALAPPDATA", ".")) / "AudioDigest"
    )
    backup_dir: Path | None = None


@dataclass(slots=True)
class GmailSettings:
    client_secret_path: Path
    token_service: str = "AudioDigest"  # noqa: S105 - Credential Manager service name.
    token_username: str = "gmail-oauth-token"  # noqa: S105 - Credential record name.
    token_file_path: Path | None = None
    account_email_file_path: Path | None = None


@dataclass(slots=True)
class AntigravitySettings:
    executable: str = "agy"
    model: str = ""
    timeout_seconds: int = 900
    workspace_dir: Path = field(
        default_factory=lambda: (
            Path(os.getenv("LOCALAPPDATA", ".")) / "AudioDigest" / "antigravity-workspace"
        )
    )
    settings_path: Path = field(
        default_factory=lambda: Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    )
    agent_path: Path = Path("config/antigravity-agent.md")
    agent_name: str = "audio-digest"
    use_g1_credits: bool = False
    telemetry: bool = False


@dataclass(slots=True)
class ArticleSettings:
    timeout_seconds: int = 10
    max_bytes: int = 2_097_152
    max_redirects: int = 3
    user_agent: str = "AudioDigest/0.1 personal newsletter reader"


@dataclass(slots=True)
class ResearchSettings:
    enabled: bool = True
    required: bool = True
    timeout_seconds: int = 10
    request_attempts: int = 3
    max_bytes: int = 2_097_152
    history_event_limit: int = 15
    current_events_max_chars: int = 30_000
    user_agent: str = "TheDailyNexus/0.2 private daily briefing"


@dataclass(slots=True)
class AudioSettings:
    voice: str = "am_michael"
    language_code: str = "a"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    bitrate: str = "64k"
    sample_rate: int = 44_100
    target_lufs: float = -16.0
    true_peak_db: float = -1.0
    min_duration_seconds: int = 60


@dataclass(slots=True)
class HostSettings:
    count: int = 1
    solo_name: str = "Dalia"
    dialogue_style: str = "broadcast"
    primary_name: str = "Dalia"
    primary_voice: str = "af_heart"
    primary_tone: str = "warm"
    secondary_name: str = "Nox"
    secondary_voice: str = "am_michael"
    secondary_tone: str = "dry_wit"

    @property
    def active_names(self) -> list[str]:
        if self.count == 1:
            return [self.solo_name]
        return [self.primary_name, self.secondary_name]


@dataclass(slots=True)
class FirebaseSettings:
    project_id: str
    executable: str
    public_dir: Path
    base_url: str
    secret_path: str
    require_spark_confirmation: bool = True
    publish_enabled: bool = False
    publish_mode: str = "manual"
    deployment_token_file_path: Path | None = None


@dataclass(slots=True)
class WebSettings:
    enabled: bool = False
    firebase_api_key: str = ""
    owner_uid: str = ""
    oauth_client_secret_path: Path = Path(
        "%LOCALAPPDATA%/AudioDigest/secrets/client_secret_web_runner.json"
    )
    token_service: str = (
        "TheDailyNexusWebRunner"  # noqa: S105 - Credential Manager service name.
    )
    token_username: str = (
        "firebase-refresh-token"  # noqa: S105 - Credential record name.
    )
    token_file_path: Path | None = None
    poll_minutes: int = 5


@dataclass(slots=True)
class PodcastSettings:
    title: str = "The Daily Nexus"
    author: str = "Dario Novelli"
    description: str = (
        "A private AI-generated daily briefing on technology, data, and world events."
    )
    language: str = "en"
    category: str = "News"
    explicit: bool = False
    cover_filename: str = "cover.png"
    closing_quotes_path: Path = Path("config/closing-quotes.json")
    tone: str = "dry_wit"
    max_script_repairs: int = 2
    sections: tuple[str, ...] = ()
    include_today_in_history: bool = True
    newspaper_edition_scale: str = "standard"
    evidence_mode: str = "newsletter_first"


@dataclass(slots=True)
class SafetySettings:
    forbid_paid_credentials: bool = True
    delete_source_payloads_after_success: bool = True
    allow_personal_email: bool = False
    allow_attachments: bool = False


@dataclass(slots=True)
class Settings:
    project_dir: Path
    app: AppSettings
    gmail: GmailSettings
    antigravity: AntigravitySettings
    articles: ArticleSettings
    research: ResearchSettings
    audio: AudioSettings
    firebase: FirebaseSettings
    podcast: PodcastSettings
    safety: SafetySettings
    hosts: HostSettings = field(default_factory=HostSettings)
    web: WebSettings = field(default_factory=WebSettings)
    # Internal-only override used by cloud jobs that intentionally generate
    # more than one edition for a calendar day. It is never read from TOML.
    database_override_path: Path | None = None

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app.timezone)

    @property
    def database_path(self) -> Path:
        return self.database_override_path or self.app.runtime_dir / "state.sqlite3"

    @property
    def episodes_dir(self) -> Path:
        return self.app.runtime_dir / "episodes"

    @property
    def staging_dir(self) -> Path:
        return self.app.runtime_dir / "staging"

    @property
    def safety_confirmation_path(self) -> Path:
        return self.app.runtime_dir / "spark-confirmation.json"


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def _optional_path(raw: dict[str, Any], key: str, base: Path) -> Path | None:
    value = str(raw.get(key, "")).strip()
    return _expand_path(value, base) if value else None


def load_settings(path: str | Path = "config.toml") -> Settings:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    base = config_path.parent

    app_raw = _section(raw, "app")
    gmail_raw = _section(raw, "gmail")
    antigravity_raw = _section(raw, "antigravity")
    article_raw = _section(raw, "articles")
    research_raw = _section(raw, "research")
    audio_raw = _section(raw, "audio")
    hosts_raw = _section(raw, "hosts")
    firebase_raw = _section(raw, "firebase")
    web_raw = _section(raw, "web")
    podcast_raw = _section(raw, "podcast")
    safety_raw = _section(raw, "safety")

    runtime_dir = _expand_path(str(app_raw.get("runtime_dir", "%LOCALAPPDATA%/AudioDigest")), base)
    backup_value = str(app_raw.get("backup_dir", "")).strip()
    app = AppSettings(
        timezone=str(app_raw.get("timezone", "UTC")),
        gmail_label=str(app_raw.get("gmail_label", "AudioDigest/Source")),
        retention_days=int(app_raw.get("retention_days", 30)),
        target_min_words=int(app_raw.get("target_min_words", 2850)),
        target_max_words=int(app_raw.get("target_max_words", 3800)),
        max_newsletters=int(app_raw.get("max_newsletters", 80)),
        max_articles_per_newsletter=int(app_raw.get("max_articles_per_newsletter", 5)),
        runtime_dir=runtime_dir,
        backup_dir=_expand_path(backup_value, base) if backup_value else None,
    )
    gmail = GmailSettings(
        client_secret_path=_expand_path(
            str(gmail_raw.get("client_secret_path", "client_secret.json")), base
        ),
        token_service=str(gmail_raw.get("token_service", "AudioDigest")),
        token_username=str(gmail_raw.get("token_username", "gmail-oauth-token")),
        token_file_path=_optional_path(gmail_raw, "token_file_path", base),
        account_email_file_path=_optional_path(
            gmail_raw,
            "account_email_file_path",
            base,
        ),
    )
    antigravity = AntigravitySettings(
        executable=_expand_executable(
            str(antigravity_raw.get("executable", "agy")),
            base,
        ),
        model=str(antigravity_raw.get("model", "")),
        timeout_seconds=int(antigravity_raw.get("timeout_seconds", 900)),
        workspace_dir=_expand_path(
            str(
                antigravity_raw.get(
                    "workspace_dir",
                    "%LOCALAPPDATA%/AudioDigest/antigravity-workspace",
                )
            ),
            base,
        ),
        settings_path=_expand_path(
            str(
                antigravity_raw.get(
                    "settings_path",
                    "%USERPROFILE%/.gemini/antigravity-cli/settings.json",
                )
            ),
            base,
        ),
        agent_path=_expand_path(
            str(antigravity_raw.get("agent_path", "config/antigravity-agent.md")),
            base,
        ),
        agent_name=str(antigravity_raw.get("agent_name", "audio-digest")),
        use_g1_credits=bool(antigravity_raw.get("use_g1_credits", False)),
        telemetry=bool(antigravity_raw.get("telemetry", False)),
    )
    articles = ArticleSettings(
        timeout_seconds=int(article_raw.get("timeout_seconds", 10)),
        max_bytes=int(article_raw.get("max_bytes", 2_097_152)),
        max_redirects=int(article_raw.get("max_redirects", 3)),
        user_agent=str(article_raw.get("user_agent", "AudioDigest/0.1 personal newsletter reader")),
    )
    research = ResearchSettings(
        enabled=bool(research_raw.get("enabled", True)),
        required=bool(research_raw.get("required", True)),
        timeout_seconds=int(research_raw.get("timeout_seconds", 10)),
        request_attempts=int(research_raw.get("request_attempts", 3)),
        max_bytes=int(research_raw.get("max_bytes", 2_097_152)),
        history_event_limit=int(research_raw.get("history_event_limit", 15)),
        current_events_max_chars=int(research_raw.get("current_events_max_chars", 30_000)),
        user_agent=str(research_raw.get("user_agent", "TheDailyNexus/0.2 private daily briefing")),
    )
    audio = AudioSettings(
        voice=str(audio_raw.get("voice", "am_michael")),
        language_code=str(audio_raw.get("language_code", "a")),
        ffmpeg=_expand_executable(
            str(audio_raw.get("ffmpeg", "ffmpeg")),
            base,
        ),
        ffprobe=_expand_executable(
            str(audio_raw.get("ffprobe", "ffprobe")),
            base,
        ),
        bitrate=str(audio_raw.get("bitrate", "64k")),
        sample_rate=int(audio_raw.get("sample_rate", 44_100)),
        target_lufs=float(audio_raw.get("target_lufs", -16.0)),
        true_peak_db=float(audio_raw.get("true_peak_db", -1.0)),
        min_duration_seconds=int(audio_raw.get("min_duration_seconds", 60)),
    )
    hosts = HostSettings(
        count=int(hosts_raw.get("count", 1)),
        solo_name=str(hosts_raw.get("solo_name", "Dalia")),
        dialogue_style=str(hosts_raw.get("dialogue_style", "broadcast")),
        primary_name=str(hosts_raw.get("primary_name", "Dalia")),
        primary_voice=str(hosts_raw.get("primary_voice", "af_heart")),
        primary_tone=str(hosts_raw.get("primary_tone", "warm")),
        secondary_name=str(hosts_raw.get("secondary_name", "Nox")),
        secondary_voice=str(hosts_raw.get("secondary_voice", "am_michael")),
        secondary_tone=str(hosts_raw.get("secondary_tone", "dry_wit")),
    )
    firebase_project_id = str(firebase_raw.get("project_id", ""))
    firebase_secret_storage = str(
        firebase_raw.get("secret_storage", "")
    ).strip().casefold()
    if firebase_secret_storage not in {"", FIREBASE_FEED_STORAGE_KIND}:
        raise ValueError("firebase.secret_storage must be empty or 'keyring'")
    configured_secret_path = str(firebase_raw.get("secret_path", "")).strip().strip("/")
    if firebase_secret_storage == FIREBASE_FEED_STORAGE_KIND:
        if configured_secret_path:
            raise ValueError(
                "firebase.secret_path must be empty when secret_storage is 'keyring'"
            )
        configured_secret_path = read_firebase_secret(firebase_project_id)
    firebase = FirebaseSettings(
        project_id=firebase_project_id,
        executable=_expand_executable(
            str(firebase_raw.get("executable", "firebase")),
            base,
        ),
        public_dir=_expand_path(str(firebase_raw.get("public_dir", "hosting")), base),
        base_url=str(firebase_raw.get("base_url", "")).rstrip("/"),
        secret_path=configured_secret_path,
        require_spark_confirmation=bool(firebase_raw.get("require_spark_confirmation", True)),
        publish_enabled=bool(firebase_raw.get("publish_enabled", False)),
        publish_mode=str(firebase_raw.get("publish_mode", "manual")),
        deployment_token_file_path=_optional_path(
            firebase_raw,
            "deployment_token_file_path",
            base,
        ),
    )
    web = WebSettings(
        enabled=bool(web_raw.get("enabled", False)),
        firebase_api_key=str(web_raw.get("firebase_api_key", "")).strip(),
        owner_uid=str(web_raw.get("owner_uid", "")).strip(),
        oauth_client_secret_path=_expand_path(
            str(
                web_raw.get(
                    "oauth_client_secret_path",
                    "%LOCALAPPDATA%/AudioDigest/secrets/client_secret_web_runner.json",
                )
            ),
            base,
        ),
        token_service=str(
            web_raw.get("token_service", "TheDailyNexusWebRunner")
        ).strip(),
        token_username=str(
            web_raw.get("token_username", "firebase-refresh-token")
        ).strip(),
        token_file_path=_optional_path(web_raw, "token_file_path", base),
        poll_minutes=int(web_raw.get("poll_minutes", 5)),
    )
    section_values = podcast_raw.get("sections", [])
    if not isinstance(section_values, list) or any(
        not isinstance(item, str) for item in section_values
    ):
        raise ValueError("[podcast].sections must be a list of strings")
    newspaper_edition_scale = str(
        podcast_raw.get("newspaper_edition_scale", "standard")
    ).strip().casefold()
    if newspaper_edition_scale not in {"focused", "standard", "comprehensive"}:
        raise ValueError(
            "[podcast].newspaper_edition_scale must be focused, standard, or comprehensive"
        )
    evidence_mode = str(
        podcast_raw.get("evidence_mode", "newsletter_first")
    ).strip().casefold()
    if evidence_mode not in {"newsletter_first", "newsletter_only"}:
        raise ValueError(
            "[podcast].evidence_mode must be newsletter_first or newsletter_only"
        )
    podcast = PodcastSettings(
        title=str(podcast_raw.get("title", "The Daily Nexus")),
        author=str(podcast_raw.get("author", "Dario Novelli")),
        description=str(
            podcast_raw.get(
                "description",
                ("A private AI-generated daily briefing on technology, data, and world events."),
            )
        ),
        language=str(podcast_raw.get("language", "en")),
        category=str(podcast_raw.get("category", "News")),
        explicit=bool(podcast_raw.get("explicit", False)),
        cover_filename=str(podcast_raw.get("cover_filename", "cover.png")),
        closing_quotes_path=_expand_path(
            str(podcast_raw.get("closing_quotes_path", "config/closing-quotes.json")),
            base,
        ),
        tone=str(podcast_raw.get("tone", "dry_wit")),
        max_script_repairs=int(podcast_raw.get("max_script_repairs", 2)),
        include_today_in_history=bool(
            podcast_raw.get("include_today_in_history", True)
        ),
        newspaper_edition_scale=newspaper_edition_scale,
        evidence_mode=evidence_mode,
        sections=normalize_custom_sections(
            tuple(section_values),
            include_today_in_history=bool(
                podcast_raw.get("include_today_in_history", True)
            ),
        ),
    )
    safety = SafetySettings(
        forbid_paid_credentials=bool(safety_raw.get("forbid_paid_credentials", True)),
        delete_source_payloads_after_success=bool(
            safety_raw.get("delete_source_payloads_after_success", True)
        ),
        allow_personal_email=bool(safety_raw.get("allow_personal_email", False)),
        allow_attachments=bool(safety_raw.get("allow_attachments", False)),
    )
    settings = Settings(
        project_dir=base,
        app=app,
        gmail=gmail,
        antigravity=antigravity,
        articles=articles,
        research=research,
        audio=audio,
        firebase=firebase,
        podcast=podcast,
        safety=safety,
        hosts=hosts,
        web=web,
    )
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    validate_gmail_label(settings.app.gmail_label)
    selected_voice = voice_profile(settings.audio.voice)
    if settings.audio.language_code != selected_voice.language_code:
        raise ValueError(
            "audio.language_code does not match the selected voice; "
            f"{selected_voice.voice_id} requires {selected_voice.language_code!r}"
        )
    editorial_tone(settings.podcast.tone)
    if settings.hosts.count not in {1, 2}:
        raise ValueError("hosts.count must be 1 or 2")
    if settings.hosts.dialogue_style not in {"broadcast", "conversation"}:
        raise ValueError("hosts.dialogue_style must be broadcast or conversation")
    if settings.web.poll_minutes not in range(1, 61):
        raise ValueError("web.poll_minutes must be from 1 to 60")
    private_file_paths = (
        settings.gmail.token_file_path,
        settings.gmail.account_email_file_path,
        settings.web.token_file_path,
        settings.firebase.deployment_token_file_path,
    )
    runtime_root = settings.app.runtime_dir.resolve()
    for private_path in private_file_paths:
        if private_path is None:
            continue
        resolved_private_path = private_path.resolve()
        if (
            resolved_private_path == runtime_root
            or runtime_root not in resolved_private_path.parents
        ):
            raise ValueError(
                "file-backed credentials must stay inside app.runtime_dir"
            )
    if settings.web.enabled:
        if not settings.web.firebase_api_key.startswith("AIza"):
            raise ValueError("web.firebase_api_key is not a valid Firebase Web API key")
        if (
            not settings.firebase.project_id
            or "REPLACE_" in settings.firebase.project_id
        ):
            raise ValueError("web runner requires the dedicated Firebase project ID")
        expected_web_url = f"https://{settings.firebase.project_id}.web.app"
        if settings.firebase.base_url != expected_web_url:
            raise ValueError(
                "web runner requires the exact dedicated Firebase Hosting URL"
            )
        if (
            not settings.web.owner_uid
            or len(settings.web.owner_uid) > 128
            or "/" in settings.web.owner_uid
            or any(character.isspace() for character in settings.web.owner_uid)
        ):
            raise ValueError("web.owner_uid must be the authorized Firebase user ID")
        if (
            settings.web.oauth_client_secret_path.resolve()
            == settings.gmail.client_secret_path.resolve()
        ):
            raise ValueError(
                "web runner must use its own Firebase-project OAuth client"
            )
    host_names = [
        settings.hosts.primary_name.strip(),
        settings.hosts.secondary_name.strip(),
    ]
    if any(not name for name in host_names):
        raise ValueError("host names must not be empty")
    if host_names[0].casefold() == host_names[1].casefold():
        raise ValueError("host names must be different")
    if settings.hosts.solo_name.casefold() not in {
        host_names[0].casefold(),
        host_names[1].casefold(),
    }:
        raise ValueError("hosts.solo_name must match primary_name or secondary_name")
    voice_profile(settings.hosts.primary_voice)
    voice_profile(settings.hosts.secondary_voice)
    editorial_tone(settings.hosts.primary_tone)
    editorial_tone(settings.hosts.secondary_tone)
    if settings.firebase.publish_mode not in {"manual", "automatic"}:
        raise ValueError("firebase.publish_mode must be 'manual' or 'automatic'")
    if not 1 <= settings.podcast.max_script_repairs <= 3:
        raise ValueError("podcast.max_script_repairs must be between 1 and 3")
    if settings.app.retention_days < 1 or settings.app.retention_days > 30:
        raise ValueError("retention_days must be between 1 and 30 for the Spark pilot")
    if settings.app.target_min_words < 100:
        raise ValueError("target_min_words is unexpectedly small")
    if settings.app.target_max_words < settings.app.target_min_words:
        raise ValueError("target_max_words must be >= target_min_words")
    if settings.app.max_articles_per_newsletter < 0:
        raise ValueError("max_articles_per_newsletter must not be negative")
    if settings.research.history_event_limit < 1:
        raise ValueError("research.history_event_limit must be positive")
    if settings.research.request_attempts not in range(1, 6):
        raise ValueError("research.request_attempts must be from 1 to 5")
    if settings.research.current_events_max_chars < 1_000:
        raise ValueError("research.current_events_max_chars is unexpectedly small")
    if settings.safety.allow_personal_email:
        raise ValueError("The privacy boundary forbids personal email")
    if settings.safety.allow_attachments:
        raise ValueError("The privacy boundary forbids attachments")
    if settings.antigravity.use_g1_credits:
        raise ValueError("antigravity.use_g1_credits must remain false")
    if settings.antigravity.telemetry:
        raise ValueError("antigravity.telemetry must remain false")
    if not settings.antigravity.agent_name.strip():
        raise ValueError("antigravity.agent_name must not be empty")
    if settings.firebase.publish_enabled:
        if not settings.firebase.project_id or "REPLACE_" in settings.firebase.project_id:
            raise ValueError("firebase.project_id must be configured before publishing")
        parsed_firebase_url = urlsplit(settings.firebase.base_url)
        allowed_firebase_hosts = {
            f"{settings.firebase.project_id}.web.app",
            f"{settings.firebase.project_id}.firebaseapp.com",
        }
        if (
            parsed_firebase_url.scheme != "https"
            or parsed_firebase_url.hostname not in allowed_firebase_hosts
            or parsed_firebase_url.username
            or parsed_firebase_url.password
            or parsed_firebase_url.port not in {None, 443}
            or parsed_firebase_url.path not in {"", "/"}
            or parsed_firebase_url.query
            or parsed_firebase_url.fragment
        ):
            raise ValueError(
                "firebase.base_url must be this project's standard HTTPS "
                "web.app or firebaseapp.com URL"
            )
        if len(settings.firebase.secret_path) < 32:
            raise ValueError("firebase.secret_path must contain at least 128 bits of entropy")


def generate_secret_path() -> str:
    return secrets.token_hex(16)
