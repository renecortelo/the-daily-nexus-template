from __future__ import annotations

import json
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, time
from typing import Any

from audiodigest.config import ResearchSettings
from audiodigest.content import clean_html
from audiodigest.models import SourceItem


class DailyResearchError(RuntimeError):
    pass


class WikimediaDailyResearch:
    def __init__(self, settings: ResearchSettings):
        self.settings = settings

    def _get_json(self, url: str) -> dict[str, Any]:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "en.wikipedia.org":
            raise DailyResearchError("research URL is outside the Wikimedia allowlist")
        request = urllib.request.Request(  # noqa: S310 - HTTPS host checked above
            url,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "application/json",
            },
        )
        body: bytes | None = None
        latest_error: BaseException | None = None
        for attempt in range(1, self.settings.request_attempts + 1):
            try:
                with urllib.request.urlopen(  # noqa: S310 - caller uses fixed Wikimedia endpoints
                    request, timeout=self.settings.timeout_seconds
                ) as response:
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > self.settings.max_bytes:
                        raise DailyResearchError("Wikimedia response exceeds the size limit")
                    body = response.read(self.settings.max_bytes + 1)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429} and exc.code < 500:
                    raise DailyResearchError(f"Wikimedia request failed: HTTP {exc.code}") from exc
                latest_error = exc
            except (OSError, urllib.error.URLError) as exc:
                latest_error = exc
            if attempt < self.settings.request_attempts:
                time_module.sleep(min(0.5 * attempt, 2.0))
        if body is None:
            raise DailyResearchError(f"Wikimedia request failed: {latest_error}") from latest_error
        if len(body) > self.settings.max_bytes:
            raise DailyResearchError("Wikimedia response exceeds the size limit")
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DailyResearchError("Wikimedia returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise DailyResearchError("Wikimedia returned an unexpected response")
        return data

    def history_source(self, episode_date: date) -> SourceItem:
        endpoint = (
            "https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/"
            f"{episode_date.month:02d}/{episode_date.day:02d}"
        )
        data = self._get_json(endpoint)
        events = data.get("events")
        if not isinstance(events, list):
            raise DailyResearchError("Wikimedia On This Day returned no event list")

        lines: list[str] = []
        source_urls: list[str] = []
        for event in events[: self.settings.history_event_limit]:
            if not isinstance(event, dict):
                continue
            year = event.get("year")
            text_value = event.get("text")
            if not isinstance(year, int) or not isinstance(text_value, str):
                continue
            text_value = " ".join(text_value.split())
            if text_value:
                lines.append(f"{year}: {text_value}")
            pages = event.get("pages", [])
            if isinstance(pages, list):
                for page in pages[:1]:
                    if not isinstance(page, dict):
                        continue
                    content_urls = page.get("content_urls")
                    desktop = (
                        content_urls.get("desktop")
                        if isinstance(content_urls, dict)
                        else None
                    )
                    url = desktop.get("page") if isinstance(desktop, dict) else None
                    if isinstance(url, str) and url.startswith("https://"):
                        source_urls.append(url)

        if not lines:
            raise DailyResearchError("Wikimedia On This Day returned no usable events")
        received_at = datetime.combine(episode_date, time.min, tzinfo=UTC)
        return SourceItem(
            message_id=f"wikimedia-history-{episode_date.isoformat()}",
            publication="Wikipedia On This Day",
            sender="Wikimedia",
            subject=f"Historical events for {episode_date:%B} {episode_date.day}",
            received_at=received_at,
            email_text="\n".join(lines),
            source_type="history",
            article_urls=list(dict.fromkeys(source_urls)),
        )

    def current_events_source(self, episode_date: date) -> SourceItem:
        page_title = (
            f"Portal:Current_events/{episode_date.year}_"
            f"{episode_date:%B}_{episode_date.day}"
        )
        query = urllib.parse.urlencode(
            {
                "action": "parse",
                "page": page_title,
                "prop": "text",
                "format": "json",
                "formatversion": "2",
            }
        )
        endpoint = f"https://en.wikipedia.org/w/api.php?{query}"
        data = self._get_json(endpoint)
        parsed = data.get("parse")
        if not isinstance(parsed, dict):
            error = data.get("error", {})
            detail = error.get("info", "page is unavailable") if isinstance(error, dict) else ""
            raise DailyResearchError(f"Wikipedia Current Events {detail}".strip())
        html = parsed.get("text")
        if not isinstance(html, str):
            raise DailyResearchError("Wikipedia Current Events returned no page text")
        text_value = clean_html(html)[: self.settings.current_events_max_chars]
        if not text_value:
            raise DailyResearchError("Wikipedia Current Events returned no usable stories")
        page_url = (
            "https://en.wikipedia.org/wiki/"
            + urllib.parse.quote(page_title.replace(" ", "_"), safe=":_")
        )
        received_at = datetime.combine(episode_date, time.min, tzinfo=UTC)
        return SourceItem(
            message_id=f"wikipedia-current-events-{episode_date.isoformat()}",
            publication="Wikipedia Current Events",
            sender="Wikimedia",
            subject=f"Current world events for {episode_date.isoformat()}",
            received_at=received_at,
            email_text=text_value,
            source_type="current_world",
            article_urls=[page_url],
        )

    def fetch(self, episode_date: date) -> list[SourceItem]:
        return [
            self.history_source(episode_date),
            self.current_events_source(episode_date),
        ]
